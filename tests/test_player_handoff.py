import asyncio,json
from copy import deepcopy
from uuid import uuid4
import httpx,pytest
from sqlalchemy import select
from mythweaver.config import Settings
from mythweaver.db import PlayerTurn,ToolExecution,Entity,HarnessTask,PlatformConfig,SchemaMigration
from mythweaver.definitions import DomainError,validate_definition
from mythweaver.player import PlayerAgent
from mythweaver.player_intent import action_tool_name,numbers,matches,Option
from mythweaver.player_handoff import inspect
from mythweaver.playtest import simulate_conversation
from mythweaver.task_checks import verify
from mythweaver.harness import Harness
from mythweaver.platform_rules import Rules,update


def cmd(service,game,kind,payload):return service.command(game['id'],kind,payload,uuid4().hex)
def settings():return Settings(llm_base_url='https://example.test',llm_api_key='dummy',llm_model='test')
def message(data,quote='蓝色两次',turn_id=None):
    evidence=[{'path':'/'+field,'quote':quote,**({'turn_id':turn_id} if turn_id else {})} for field in ('option','count')]
    return {'tool_calls':[{'function':{'name':'submit_action','arguments':json.dumps({'interaction':'mark','data':data,'evidence':evidence},ensure_ascii=False)}}]}


@pytest.fixture
def configured(service,game):
    definition={'types':{'score':{'label':'选择记录','schema':{'type':'object','properties':{
        'owner':{'type':'string'},'total':{'type':'integer'},'credits':{'type':'integer','minimum':0},'last':{'type':'string'}},
        'required':['owner','total','credits','last'],'additionalProperties':False},'player_access':{'owner_field':'owner','read_fields':['total','credits','last']}}},
        'interactions':{'mark':{'label':'选择并累计','mode':'rule','player_enabled':True,'group_enabled':True,'player_batch':True,
            'player_description':'选择红色或蓝色并累计指定次数；缺少选项或次数时询问，普通问候不操作。',
            'schema':{'type':'object','properties':{'account':{'type':'string','x-reference':{'type_key':'score'}},'option':{'type':'string','enum':['red','blue'],'title':'颜色'},'count':{'type':'integer','minimum':1,'title':'次数'}},'required':['account','option','count'],'additionalProperties':False},
            'player_contract':{'instructions':'按玩家实际选择和次数执行，不能默认成1；一组为3次。','examples':[{'message':'蓝色两次','meaning':'blue、2次'},{'context':['蓝色两次'],'message':'再来三次','meaning':'沿用blue；次数改为当前3次，不要求重说颜色'}],
                'fields':[{'path':'/option','kind':'choice','label':'颜色','options':[{'value':'red','label':'红色','aliases':['红']},{'value':'blue','label':'蓝色','aliases':['蓝']}]},
                          {'path':'/count','kind':'number','label':'次数','units':{'组':3}}]},
            'effects':[{'type_key':'score','id_from':'account','field':'total','operation':'compute','expression':{'op':'add','args':[{'path':'current.total'},{'path':'input.count'}]}},
                {'type_key':'score','id_from':'account','field':'credits','operation':'compute','expression':{'op':'subtract','args':[{'path':'current.credits'},{'path':'input.count'}]}},
                {'type_key':'score','id_from':'account','field':'last','operation':'set','value_from':'option'}]}}}
    cmd(service,game,'definition',{'version':1,'definition':definition})
    cmd(service,game,'policy.update',{'version':0,'player_agent_enabled':True,'player_instructions':'处理颜色选择和次数累计，缺少信息只问缺项。'})
    record=cmd(service,game,'entity.create',{'type_key':'score','data':{'owner':'qq:1','total':0,'credits':10,'last':''}})
    return definition,record


@pytest.mark.parametrize('option,count,quote',[('blue',1,'蓝色两次'),('red',2,'蓝色两次'),('blue',2,'蓝色三次'),('blue',2,'红色或蓝色两次')])
def test_wrong_choice_count_or_invented_quote_never_executes(service,game,configured,monkeypatch,option,count,quote):
    _,row=configured
    async def request(*args):return message({'account':row['id'],'option':option,'count':count},quote)
    monkeypatch.setattr(PlayerAgent,'request',request)
    with pytest.raises(DomainError) as exc:asyncio.run(PlayerAgent(service,settings()).turn(game['id'],'qq:1','蓝色两次','bad'))
    assert exc.value.code=='INTENT_MISMATCH'
    assert service.snapshot(game['id'])['entities'][0]['data']['total']==0 and not service.snapshot(game['id'])['submissions']
    report=inspect(service,game['id'],{},settings())
    assert report['recent_turns'][0]['error_code']=='INTENT_MISMATCH'


def test_semantic_repair_can_ask_for_missing_information_without_writes(service,game,configured,monkeypatch):
    _,row=configured;seen=[]
    async def request(self,context,history,prompt):
        seen.append(context)
        return message({'account':row['id'],'option':'blue','count':1},'蓝色') if len(seen)==1 else {'content':'你想累计几次？'}
    monkeypatch.setattr(PlayerAgent,'request',request)
    result=asyncio.run(PlayerAgent(service,settings()).turn(game['id'],'qq:1','蓝色','ask'))
    assert result['action'] is None and 'validation_feedback' in seen[1]
    assert not service.snapshot(game['id'])['submissions']


def test_same_chat_followup_uses_real_history_but_current_correction_wins(service,game,configured,monkeypatch):
    _,row=configured;last=[]
    async def request(self,context,history,prompt):
        if not history:return message({'account':row['id'],'option':'blue','count':2})
        last.append(history[-1].id)
        return message({'account':row['id'],'option':'blue','count':2},turn_id=history[-1].id)
    monkeypatch.setattr(PlayerAgent,'request',request);player=PlayerAgent(service,settings())
    asyncio.run(player.turn(game['id'],'qq:1','蓝色两次','first',conversation_id='a'))
    result=asyncio.run(player.turn(game['id'],'qq:1','照刚才','second',conversation_id='a'))
    assert result['records'][0]['data']['total']==4
    with pytest.raises(DomainError):asyncio.run(player.turn(game['id'],'qq:1','改成红色三次','third',conversation_id='a'))
    assert service.snapshot(game['id'])['entities'][0]['data']['total']==4
    async def forged(*args):return message({'account':row['id'],'option':'blue','count':2},turn_id=last[0])
    monkeypatch.setattr(PlayerAgent,'request',forged)
    with pytest.raises(DomainError):asyncio.run(player.turn(game['id'],'qq:1','照刚才','other',conversation_id='b'))


@pytest.mark.parametrize('text,count',[('蓝色两组',6),('蓝色二十次',20),('蓝色十二次',12),('蓝色2次',2)])
def test_generic_number_and_unit_grounding(text,count):assert numbers(text,{'组':3})==[count]


def test_long_option_alias_does_not_resolve_to_shorter_other_option():
    small=Option(value='low',label='蓝色');large=Option(value='high',label='高级蓝色')
    assert all(o.value=='high' for o in matches('高级蓝色',[small,large]))


def test_batch_is_atomic_and_idempotent(service,game,configured,monkeypatch):
    _,row=configured
    def batch(count):
        a=json.loads(message({'account':row['id'],'option':'blue','count':2})['tool_calls'][0]['function']['arguments'])
        b=json.loads(message({'account':row['id'],'option':'red','count':count},f'红色{count}次')['tool_calls'][0]['function']['arguments'])
        return {'tool_calls':[{'function':{'name':'submit_actions','arguments':json.dumps({'actions':[a,b]},ensure_ascii=False)}}]}
    async def request(*args):return batch(3)
    monkeypatch.setattr(PlayerAgent,'request',request);player=PlayerAgent(service,settings())
    result=asyncio.run(player.turn(game['id'],'qq:1','蓝色两次，再红色3次','a'))
    assert result['action']['interaction']=='_batch' and result['records'][0]['data']['total']==5
    assert len(result['intent']['actions'])==2
    assert asyncio.run(player.turn(game['id'],'qq:1','蓝色两次，再红色3次','a'))==result
    before=service.snapshot(game['id'])
    async def failed(*args):return batch(8)
    monkeypatch.setattr(PlayerAgent,'request',failed)
    with pytest.raises(DomainError):asyncio.run(player.turn(game['id'],'qq:1','蓝色两次，再红色8次','b'))
    after=service.snapshot(game['id'])
    assert after['entities']==before['entities'] and after['submissions']==before['submissions']


def test_player_receives_specific_schema_and_parallel_reads_then_specific_action(service,game,configured,monkeypatch):
    _,row=configured;calls=[]
    def handler(request):
        body=json.loads(request.content);calls.append(body)
        tool=next(t['function'] for t in body['tools'] if t['function']['name']==action_tool_name('mark'))
        assert tool['parameters']['properties']['data']['required']==['account','option','count']
        assert '缺少选项或次数' in tool['description']
        if len(calls)==1:
            return httpx.Response(200,json={'choices':[{'message':{'tool_calls':[{'id':str(i),'type':'function','function':{'name':'read_player_records','arguments':json.dumps({'id':row['id'],'fields':[field]})}} for i,field in enumerate(('credits','total'))]}}]})
        assert sum(m['role']=='tool' for m in body['messages'])==2
        args=json.loads(message({'account':row['id'],'option':'blue','count':2})['tool_calls'][0]['function']['arguments']);args.pop('interaction')
        return httpx.Response(200,json={'choices':[{'message':{'tool_calls':[{'id':'perform','type':'function','function':{'name':action_tool_name('mark'),'arguments':json.dumps(args)}}]}}]})
    real=httpx.AsyncClient;monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(handler),**kw))
    result=asyncio.run(PlayerAgent(service,settings()).turn(game['id'],'qq:1','蓝色两次','actual'))
    assert result['records'][0]['data']['total']==2 and len(result['reads'])==2


def test_handoff_reports_real_effective_configuration_and_privacy(service,game,configured):
    _,row=configured
    with service.store.session.begin() as db:update(db,0,{'player_instructions':'全局职责：先理解再操作'})
    result=inspect(service,game['id'],{'actor':'qq:1'},settings())
    assert result['enabled'] and not result['blocking_issues'] and result['available_count']==1
    assert '全局职责' in result['player_instructions'] and result['actions'][0]['contract']['fields']
    group=inspect(service,game['id'],{'actor':'qq:1','audience':'group'},settings())
    assert group['player_context']['records']==[]


def test_real_conversation_asserts_extracted_input_and_finish_requires_current_handoff(service,game,configured,monkeypatch):
    _,row=configured
    async def request(self,context,history,prompt):
        if prompt=='你好':return {'content':'你好'}
        if prompt=='再来三次':
            result=message({'account':row['id'],'option':'blue','count':3},'三次')
            args=json.loads(result['tool_calls'][0]['function']['arguments'])
            args['evidence'][0]={'path':'/option','quote':'蓝色两次','turn_id':history[0].id}
            result['tool_calls'][0]['function']['arguments']=json.dumps(args)
            return result
        return message({'account':row['id'],'option':'blue','count':2})
    monkeypatch.setattr(PlayerAgent,'request',request)
    cases={'actor':'qq:1','audience':'private','messages':[{'message':'你好','expect_action':'none'},
        {'message':'蓝色两次','expect_inputs':[{'path':'/count','expected':2}],'expect_action':'applied','expect_records':[{'id':row['id'],'path':'/data/total','expected':2}]},
        {'message':'再来三次','expect_history_used':True,'expect_inputs':[{'path':'/option','expected':'blue'},{'path':'/count','expected':3}],'expect_action':'applied','expect_records':[{'id':row['id'],'path':'/data/total','expected':5}]}]}
    result=asyncio.run(simulate_conversation(service,settings(),game['id'],cases));assert result['passed'],{k:result[k] for k in ('configuration_current','steps')}
    handoff=inspect(service,game['id'],{},settings())
    harness=Harness(service,settings());rid=harness.create(game['id'],'配置玩家助手')
    with service.store.session.begin() as db:
        db.add(ToolExecution(run_id=rid,call_id='config',name='execute_commands',arguments={'commands':[{'kind':'policy.update','payload':{'player_instructions':'职责'}}]},status='completed',result={'ok':True,'value':{'saved':True}}))
        db.add(ToolExecution(run_id=rid,call_id='handoff',name='inspect_player_agent',arguments={},status='completed',result={'ok':True,'value':handoff}))
        db.add(ToolExecution(run_id=rid,call_id='conversation',name='simulate_conversation',arguments=cases,status='completed',result={'ok':True,'value':result}))
    checks=[{'label':'保存','call_id':'config','path':'/value/saved','expected':True}]
    with service.store.session() as db:
        with pytest.raises(DomainError) as error:verify(db,rid,checks)
        assert error.value.code=='PLAYER_HANDOFF_REQUIRED'
    checks+=[{'label':'交接','call_id':'handoff','path':'/value/enabled','expected':True},{'label':'对话','call_id':'conversation','path':'/value/passed','expected':True}]
    with service.store.session() as db:assert verify(db,rid,checks)
    document={'key':'pending','revision':0,'title':'尚未完成的原文','source':'累计正确','clauses':[{'key':'count','text':'累计正确','status':'planned'}],'scenarios':[]}
    cmd(service,game,'requirements.put',document)
    with service.store.session.begin() as db:
        db.add(ToolExecution(run_id=rid,call_id='document',name='execute_commands',arguments={'commands':[{'kind':'requirements.put','payload':document}]},status='completed',result={'ok':True,'value':{}}))
    with service.store.session() as db:
        with pytest.raises(DomainError) as error:verify(db,rid,checks)
        assert error.value.code=='BUSINESS_EVIDENCE_REQUIRED'
    cmd(service,game,'policy.update',{'version':1,'player_instructions':'后续变化'})
    with service.store.session() as db:
        with pytest.raises(DomainError):verify(db,rid,checks)


def test_default_budgets_unlimited_and_old_task_cap_no_longer_interrupts(service,game,monkeypatch):
    assert Rules().agent_max_steps==0 and Rules().agent_timeout_seconds==0
    harness=Harness(service,settings());rid=harness.create(game['id'],'讨论')
    with service.store.session.begin() as db:
        task=db.get(HarnessTask,rid);task.step=24;task.max_steps=24
    async def request(*args):return {'content':'已解释当前配置。'}
    monkeypatch.setattr(harness,'request',request)
    result=asyncio.run(harness.run(game['id'],rid))
    assert result['status']=='answered' and result['step']==25 and result['max_steps']==0


@pytest.mark.parametrize('steps,timeout,expected',[(24,900,(0,0)),(30,3600,(30,3600))])
def test_migration_removes_legacy_default_caps_but_preserves_custom_budget(store,steps,timeout,expected):
    with store.session.begin() as db:
        db.delete(db.get(SchemaMigration,13));db.add(PlatformConfig(key='runtime_rules',value={'agent_max_steps':steps,'agent_timeout_seconds':timeout}))
    store.initialize()
    with store.session() as db:
        value=db.get(PlatformConfig,'runtime_rules').value
        assert (value['agent_max_steps'],value['agent_timeout_seconds'])==expected


def test_dynamic_choices_respect_private_and_group_visibility(service,game,configured,monkeypatch):
    definition,row=configured;definition=deepcopy(definition)
    definition['types']['option']={'schema':{'type':'object','properties':{'owner':{'type':'string'},'name':{'type':'string'},'aliases':{'type':'array','items':{'type':'string'}}},'required':['owner','name','aliases']},'player_access':{'owner_field':'owner','read_fields':['name','aliases']}}
    spec=definition['interactions']['mark'];spec['schema']['properties']['option'].pop('enum')
    binding=spec['player_contract']['fields'][0];binding.pop('options');binding['options_from']={'type_key':'option','label_field':'name','aliases_field':'aliases'}
    cmd(service,game,'definition',{'version':2,'definition':definition})
    allowed=cmd(service,game,'entity.create',{'type_key':'option','visibility':'public','data':{'owner':'qq:2','name':'蓝色','aliases':['蓝']}})
    hidden=cmd(service,game,'entity.create',{'type_key':'option','data':{'owner':'qq:2','name':'红色','aliases':['红']}})
    async def request(*args):return message({'account':row['id'],'option':allowed['id'],'count':2})
    monkeypatch.setattr(PlayerAgent,'request',request)
    result=asyncio.run(PlayerAgent(service,settings()).turn(game['id'],'qq:1','蓝色两次','allowed'))
    assert result['action']['status']=='applied'
    async def forged(*args):return message({'account':row['id'],'option':hidden['id'],'count':2},'红色两次')
    monkeypatch.setattr(PlayerAgent,'request',forged)
    with pytest.raises(DomainError):asyncio.run(PlayerAgent(service,settings()).turn(game['id'],'qq:1','红色两次','hidden'))
    assert len(service.snapshot(game['id'])['submissions'])==1


def test_array_intent_cannot_borrow_quantity_from_a_different_item(service,game,configured):
    from mythweaver.player_intent import verify as verify_intent
    definition,row=configured;definition=deepcopy(definition)
    item=definition['interactions']['mark'];item['schema']={'type':'object','properties':{'lines':{'type':'array','items':{'type':'object','properties':{'option':{'type':'string'},'count':{'type':'integer'}}}}}}
    item['effects']=[]
    for binding in item['player_contract']['fields']:binding['path']='/lines/*'+binding['path']
    cmd(service,game,'definition',{'version':2,'definition':definition})
    payload={'interaction':'mark','data':{'lines':[{'option':'blue','count':2},{'option':'red','count':3}]}}
    proof=[{'path':f'/lines/{i}/{field}','quote':quote} for i,quote in enumerate(('蓝色两次','红色三次')) for field in ('option','count')]
    with service.store.session() as db:
        live=service.game(db,game['id']);verify_intent(db,live,'qq:1',payload,proof,'蓝色两次，红色三次',[])
        payload['data']['lines'][1]['count']=2;proof[-1]['quote']='蓝色两次'
        with pytest.raises(DomainError):verify_intent(db,live,'qq:1',payload,proof,'蓝色两次，红色三次',[])


def test_handoff_api_is_admin_only_and_turn_detail_is_game_scoped(store,service,game,configured,monkeypatch):
    from fastapi.testclient import TestClient
    from mythweaver.api import create_app
    async def request(*args):return {'content':'你好'}
    monkeypatch.setattr(PlayerAgent,'request',request)
    turn=asyncio.run(PlayerAgent(service,settings()).turn(game['id'],'qq:1','你好','hello'))
    client=TestClient(create_app(settings(),store));client.headers['X-MythWeaver']='1'
    base='/api/games/'+game['id']+'/player'
    assert client.get(base+'/handoff').status_code==401
    assert client.post('/api/login',json={'password':settings().admin_password}).status_code==200
    assert client.get(base+'/handoff?actor=invalid').status_code==422
    assert client.get(base+'/handoff').json()['enabled']
    assert client.get(base+'/turns/'+turn['id']).json()['prompt']=='你好'
    other=service.create_game('其他空间','')
    assert client.get('/api/games/'+other['id']+'/player/turns/'+turn['id']).status_code==404


def test_default_task_completes_more_than_24_model_rounds_without_manual_continue(service,game,monkeypatch):
    harness=Harness(service,settings());rid=harness.create(game['id'],'持续整理');calls=[]
    async def request(messages,tools):
        calls.append(True)
        if len(calls)<=30:return {'tool_calls':[{'id':'progress_'+str(len(calls)),'function':{'name':'record_progress','arguments':json.dumps({'completed':'处理中','next_step':'继续必要查询'})}}]}
        return {'content':'已完成说明。'}
    monkeypatch.setattr(harness,'request',request)
    result=asyncio.run(harness.run(game['id'],rid))
    assert result['status']=='answered' and result['step']==31 and result['max_steps']==0 and len(calls)==31


def test_ask_admin_waits_and_resumes_same_task_without_replaying_effects(service,game,monkeypatch):
    harness=Harness(service,settings());rid=harness.create(game['id'],'有一条未定规则');calls=[]
    async def request(*args):
        calls.append(True)
        return {'tool_calls':[{'id':'question','function':{'name':'ask_admin','arguments':json.dumps({'question':'获胜条件是什么？','reason':'现有说明未定义胜负，不能自行创造。','completed':['已整理已知规则']})}}]} if len(calls)==1 else {'content':'已记录新的胜负条件。'}
    monkeypatch.setattr(harness,'request',request)
    first=asyncio.run(harness.run(game['id'],rid))
    assert first['status']=='awaiting_input' and '获胜条件' in first['answer']
    harness.resume(game['id'],rid,'达到十次就获胜')
    second=asyncio.run(harness.run(game['id'],rid))
    assert second['status']=='answered' and len(second['executions'])==1 and len(calls)==2


def test_premature_player_delivery_continues_automatically_until_business_question(service,game,configured,monkeypatch):
    harness=Harness(service,settings());rid=harness.create(game['id'],'配置聊天助手');calls=[]
    with service.store.session.begin() as db:
        db.add(ToolExecution(run_id=rid,call_id='saved',name='execute_commands',arguments={'commands':[{'kind':'policy.update','payload':{'player_instructions':'已更新说明'}}]},status='completed',result={'ok':True,'value':{}}))
    async def request(messages,tools):
        calls.append(messages)
        if len(calls)==1:return {'content':'配置已保存，下次可以让我继续测试。'}
        assert '不要把未完成任务交回管理员' in messages[-1]['content']
        return {'tool_calls':[{'id':'business_question','function':{'name':'ask_admin','arguments':json.dumps({'question':'是否允许红色？','reason':'两份业务说明对此矛盾，需要GM裁决。','completed':['已保存说明，尚未验收']})}}]}
    monkeypatch.setattr(harness,'request',request)
    result=asyncio.run(harness.run(game['id'],rid))
    assert len(calls)==2 and result['status']=='awaiting_input'


def test_continuous_invalid_protocol_stops_as_real_error_not_round_budget(service,game,monkeypatch):
    harness=Harness(service,settings());rid=harness.create(game['id'],'测试响应错误');calls=[]
    async def request(*args):calls.append(True);return {'content':''}
    monkeypatch.setattr(harness,'request',request)
    result=asyncio.run(harness.run(game['id'],rid))
    assert result['status']=='failed' and result['error_code']=='MODEL_PROTOCOL_STALLED' and len(calls)==3


def test_three_greetings_cannot_verify_a_configured_game(service,game,configured):
    from mythweaver.task_checks import qualifies_player_dialogue
    with service.store.session() as db:
        assert not qualifies_player_dialogue(service.game(db,game['id']),{'passed':True,'steps':[{'ok':True,'checks':{'action':True},'action':None}]*3})


def test_unused_history_marker_cannot_forge_a_followup_test(service,game,configured,monkeypatch):
    _,row=configured
    async def request(*args):
        result=message({'account':row['id'],'option':'blue','count':2})
        args=json.loads(result['tool_calls'][0]['function']['arguments'])
        args['evidence'].append({'path':'/$interaction','quote':'蓝色两次','turn_id':'invented-history'})
        result['tool_calls'][0]['function']['arguments']=json.dumps(args)
        return result
    monkeypatch.setattr(PlayerAgent,'request',request)
    with pytest.raises(DomainError):asyncio.run(PlayerAgent(service,settings()).turn(game['id'],'qq:1','蓝色两次','forged'))
    assert not service.snapshot(game['id'])['submissions']


def test_semantic_repair_sees_its_rejected_arguments_and_fixes_local_evidence(service,game,configured,monkeypatch):
    _,row=configured;calls=[]
    async def request(self,context,history,prompt):
        calls.append(context)
        if len(calls)==1:return message({'account':row['id'],'option':'blue','count':2},'一笔蓝色两次')
        feedback=context['validation_feedback']
        assert feedback['rejected_actions'][0]['evidence'][0]['quote']=='一笔蓝色两次'
        assert '多个数值' in feedback['message']
        return message({'account':row['id'],'option':'blue','count':2},'蓝色两次')
    monkeypatch.setattr(PlayerAgent,'request',request)
    result=asyncio.run(PlayerAgent(service,settings()).turn(game['id'],'qq:1','新增一笔蓝色两次','fixed'))
    assert result['action']['status']=='applied' and len(calls)==2
    assert service.snapshot(game['id'])['entities'][0]['data']['total']==2
