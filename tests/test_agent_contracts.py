import asyncio
import json

import pytest
from sqlalchemy import select

from mythweaver.agent import Agent
from mythweaver.agent_contracts import bind_command_schema,execution_mode,validate_advertised_call
from mythweaver.agent_reads import tools as read_tools
from mythweaver.config import Settings
from mythweaver.db import AgentRun,ToolExecution
from mythweaver.definitions import DomainError
from mythweaver.harness import Commands,Harness,protocol_issue
from mythweaver.plugins import Plugins
from mythweaver.task_checks import verify


def test_advertised_commands_reject_unknown_names_and_wrong_payload_before_dispatch():
    specification={'type':'function','function':{'name':'execute_commands','parameters':bind_command_schema(Commands.model_json_schema())}}
    for command in ({'kind':'plugin.create','payload':{}},{'kind':'game.create','payload':{}},{'kind':'entity.create','payload':{'type_key':'note','data':{},'made_up':True}}):
        with pytest.raises(DomainError) as error:
            validate_advertised_call([specification],'execute_commands',{'commands':[command],'explanation':'test'})
        assert error.value.code=='INVALID_TOOL_INPUT'
    validate_advertised_call([specification],'execute_commands',{'commands':[{'kind':'game.create','payload':{'name':'new'}}],'explanation':'test'})
    with pytest.raises(DomainError) as error:validate_advertised_call(read_tools(),'plugin_install',{'id':'x'})
    assert error.value.code=='FORBIDDEN_TOOL'
    with pytest.raises(DomainError):validate_advertised_call(read_tools(),'inspect_capabilities',{'kinds':['plugin.create']})


def test_plan_mode_never_enters_plugin_worker(service,game,monkeypatch):
    from types import SimpleNamespace
    plugins=Plugins(service)
    def forbidden(*args):pytest.fail('Preview reached plugin source/worker')
    monkeypatch.setattr(plugins,'verify_source',forbidden)
    row=SimpleNamespace(manifest={'tools':{'run':{'input_schema':{'type':'object'}}}})
    with execution_mode('plan'):
        with pytest.raises(DomainError) as error:plugins.execute(row,'run',{})
    assert error.value.code=='EXECUTION_MODE_REQUIRED'


def test_preview_propagates_execution_guard_and_rolls_back(service,game,monkeypatch):
    from mythweaver.agent_contracts import require_code_execution
    service.command(game['id'],'definition',{'version':1,'definition':{'types':{'note':{'schema':{'type':'object','properties':{'value':{'type':'integer'}}}}},
        'interactions':{'run':{'mode':'rule','schema':{'type':'object','properties':{'id':{'type':'string'}},'required':['id']},
         'effects':[{'type_key':'note','id_from':'id','field':'value','operation':'plugin','plugin_key':'custom','tool':'calculate','expression':{'object':{}}}]}}}},'def')
    row=service.command(game['id'],'entity.create',{'type_key':'note','data':{'value':1}},'row')
    calls=[]
    def invoke(*args,**kwargs):require_code_execution();calls.append('executed');return 3
    monkeypatch.setattr(service.plugins,'invoke',invoke)
    before=service.snapshot(game['id'])
    with pytest.raises(DomainError) as error:
        Agent(service,Settings()).prepare(game['id'],'preview','preview',[{'kind':'submit','payload':{'interaction':'run','data':{'id':row['id']}}}])
    assert error.value.code=='EXECUTION_MODE_REQUIRED' and calls==[]
    assert service.snapshot(game['id'])==before
    assert service.command(game['id'],'submit',{'interaction':'run','data':{'id':row['id']}},'actual')['status']=='applied'
    assert calls==['executed']


def test_finish_requires_matching_success_receipts_from_same_task(service,game):
    harness=Harness(service,Settings());rid=harness.create(game['id'],'task');other=harness.create(game['id'],'other')
    with service.store.session.begin() as db:
        db.add_all([ToolExecution(run_id=rid,call_id='real',name='simulate_actions',arguments={},status='completed',result={'ok':True,'value':{'steps':[{'ok':True}]}}),
                    ToolExecution(run_id=other,call_id='other-task',name='execute_commands',arguments={},status='completed',result={'ok':True,'value':{'status':'applied'}}),
                    ToolExecution(run_id=rid,call_id='progress',name='record_progress',arguments={},status='completed',result={'ok':True,'value':{'completed':'done'}})])
    with service.store.session() as db:
        check={'label':'trial passed','call_id':'real','path':'/value/steps/0/ok','expected':True}
        assert verify(db,rid,[check])[0]['passed']
        for changed in ({'call_id':'fabricated'},{'call_id':'other-task'},{'call_id':'progress'},{'expected':False},{'expected':1},{'path':'/value/missing'}):
            with pytest.raises(DomainError):verify(db,rid,[{**check,**changed}])


def test_chat_verification_requires_actual_three_turn_model_evidence(service,game):
    harness=Harness(service,Settings());rid=harness.create(game['id'],'验证聊天')
    values=[('structured','simulate_actions',{'test_layer':'structured_actions_only','passed':True,'steps':[{'ok':True}]*3}),
            ('one','simulate_conversation',{'test_layer':'player_model_conversation','passed':True,'steps':[{'ok':True,'checks':{'interaction':True}}]}),
            ('unchecked','simulate_conversation',{'test_layer':'player_model_conversation','passed':True,'steps':[{'ok':True}]*3}),
            ('failed','simulate_conversation',{'test_layer':'player_model_conversation','passed':False,'steps':[{'ok':False,'checks':{'interaction':False}}]*3}),
            ('fake','ext_fake__test',{'test_layer':'player_model_conversation','passed':True,'steps':[{'ok':True,'checks':{'interaction':True}}]*3}),
            ('actual','simulate_conversation',{'test_layer':'player_model_conversation','passed':True,'steps':[{'ok':True,'checks':{'interaction':True}}]*3})]
    with service.store.session.begin() as db:
        for cid,name,value in values:db.add(ToolExecution(run_id=rid,call_id=cid,name=name,arguments={},status='completed',result={'ok':True,'value':value}))
    with service.store.session() as db:
        for cid,_,value in values:
            check={'label':cid,'call_id':cid,'path':'/value/test_layer','expected':value['test_layer']}
            if cid=='actual':assert verify(db,rid,[check],'player_conversation')[0]['passed']
            else:
                with pytest.raises(DomainError) as error:verify(db,rid,[check],'player_conversation')
                assert error.value.code=='CHAT_EVIDENCE_REQUIRED'


def test_harness_blocks_invented_calls_then_verifies_actual_outcome(service,game,monkeypatch):
    harness=Harness(service,Settings());calls=[]
    def tool(name,args,cid):return {'tool_calls':[{'id':cid,'type':'function','function':{'name':name,'arguments':json.dumps(args)}}]}
    async def request(messages,tools):
        index=len(calls);calls.append(messages)
        if index==0:return tool('invented_admin',{},'bad')
        if index==1:
            assert json.loads(messages[-1]['content'])['code']=='FORBIDDEN_TOOL'
            return tool('inspect_runtime',{},'read')
        if index==2:
            receipt=json.loads(messages[-1]['content'])['receipt']
            assert receipt['call_id']=='read' and receipt['execution_id']
            return tool('finish_task',{'answer':'checked','checks':[{'label':'actual version','call_id':receipt['call_id'],'path':'/value/definition_version','expected':999}]},'wrong-proof')
        assert json.loads(messages[-1]['content'])['code']=='EVIDENCE_MISMATCH'
        return tool('finish_task',{'answer':'实际检查完成','checks':[{'label':'actual version','call_id':'read','path':'/value/definition_version','expected':1}]},'finish')
    monkeypatch.setattr(harness,'request',request)
    rid=harness.create(game['id'],'检查当前游戏定义版本')
    result=asyncio.run(harness.run(game['id'],rid))
    assert result['status']=='completed' and result['answer']=='实际检查完成',(result['error_code'],result['tool_trace'],[e['result'] for e in result['executions']])
    assert [e['result']['ok'] for e in result['executions']]==[False,True,False,True]
    assert protocol_issue({'tool_calls':[{'function':{'name':'finish_task'}},{'function':{'name':'execute_commands'}}]})=='FINISH_MUST_BE_SINGLE_CALL'
