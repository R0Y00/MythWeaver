import asyncio,json
from copy import deepcopy
import pytest
from mythweaver.harness import Harness,Commands,context_window,ContextProjector,protocol_issue
from mythweaver.harness_protocol import normalize,diagnostics,provider_tools,repair_context
from mythweaver.harness_results import read,observation
from mythweaver.module_patch import run as patch,patched,Edit
from mythweaver.agent_contracts import bind_command_schema,validate_advertised_call
from mythweaver.config import Settings
from mythweaver.db import ToolExecution,HarnessTask
from mythweaver.definitions import DomainError


def call(name,args,cid):return {'tool_calls':[{'id':cid,'function':{'name':name,'arguments':json.dumps(args,ensure_ascii=False)}}]}


def test_compact_wire_catalog_keeps_full_dispatch_validation():
    original=[{'type':'function','function':{'name':'execute_commands','description':'commands','parameters':bind_command_schema(Commands.model_json_schema())}}]
    wire=provider_tools(original)
    before=len(json.dumps(original));after=len(json.dumps(wire))
    assert after<before*.2 and 'oneOf' in original[0]['function']['parameters']['properties']['commands']['items']
    with pytest.raises(DomainError):validate_advertised_call(original,'execute_commands',{'commands':[{'kind':'game.create','payload':{}}],'explanation':'test'})
    assert wire[0]['function']['parameters']['properties']['commands']['items']['properties']['kind']==original[0]['function']['parameters']['properties']['commands']['items']['properties']['kind']


@pytest.mark.parametrize('value',[{'source':'line1\nline2'},'{"source":"line1\nline2"}'])
def test_narrow_provider_normalization_preserves_literal_text(value):
    original={'tool_calls':[{'id':'a','function':{'name':'plugin_draft','arguments':value}}]}
    fixed,events=normalize(original)
    assert json.loads(fixed['tool_calls'][0]['function']['arguments'])=={'source':'line1\nline2'}
    assert events and protocol_issue(fixed) is None and original['tool_calls'][0]['function']['arguments']==value


@pytest.mark.parametrize('value',['{"x":1,"x":2}','{"source":"unfinished','{"x":NaN}'])
def test_ambiguous_or_incomplete_json_is_never_repaired(value):
    original={'tool_calls':[{'id':'a','function':{'name':'plugin_draft','arguments':value}}]}
    fixed,events=normalize(original)
    assert not events and protocol_issue(fixed)=='INVALID_TOOL_INPUT'
    assert diagnostics(fixed)[0]['tool_name']=='plugin_draft'
    assert value not in json.dumps(diagnostics(fixed))


def test_truncated_response_is_not_normalized_or_executed():
    msg=call('execute_commands',{},'a');msg['_response_meta']={'finish_reason':'length'}
    assert normalize(msg)==(msg,[]) and protocol_issue(msg)=='MODEL_OUTPUT_TRUNCATED'


def test_syntax_repair_feedback_is_small_and_explicitly_partial():
    text='{"source":"'+('草稿'*10000)
    message={'tool_calls':[{'function':{'name':'plugin_draft','arguments':text}}]}
    parts=repair_context(message)
    assert parts[0]['partial'] and len(json.dumps(parts,ensure_ascii=False))<900
    assert 'excerpt' not in json.dumps(diagnostics(message))


def test_saved_result_directory_is_scoped_and_paginated(service,game):
    h=Harness(service,Settings());rid=h.create(game['id'],'回执目录');other=h.create(game['id'],'其他任务')
    with service.store.session.begin() as db:
        for i in range(3):db.add(ToolExecution(run_id=rid,call_id=f'entry-{i}',name='query_events',arguments={},status='completed',result={'ok':True,'value':[]}))
        db.add(ToolExecution(run_id=other,call_id='private-other-task',name='query_events',arguments={},status='completed',result={'ok':True,'value':[]}))
    first=read(service.store,rid,{'limit':2});second=read(service.store,rid,{'limit':2,'offset':first['next_offset']})
    ids={item['call_id'] for item in first['items']+second['items']}
    assert ids=={'entry-0','entry-1','entry-2'} and second['next_offset'] is None


def test_large_result_saved_and_paged_without_reexecution(service,game,monkeypatch):
    h=Harness(service,Settings());calls=[];large='原文完整保留。'*9000
    async def request(messages,tools):
        calls.append(messages)
        if len(calls)==1:return call('inspect_runtime',{},'large')
        if len(calls)==2:
            result=json.loads(messages[-1]['content'])
            assert result['ok'] and result['partial'] and result['stored_result']['call_id']=='large'
            return call('read_tool_result',{'call_id':'large','path':'/value/source','offset':18000,'limit':100},'page')
        result=json.loads(messages[-1]['content'])
        assert result['value']['content']==large[18000:18100] and result['value']['next_offset']==18100
        return {'content':'读取完成'}
    original=h.execute
    async def execute(g,r,name,args,e):
        if name=='inspect_runtime':return {'source':large,'status':'available'}
        return await original(g,r,name,args,e)
    monkeypatch.setattr(h,'execute',execute);monkeypatch.setattr(h,'request',request)
    rid=h.create(game['id'],'大结果续读');result=asyncio.run(h.run(game['id'],rid))
    assert result['status']=='answered' and result['executions'][0]['result']['value']['source']==large
    other=h.create(game['id'],'隔离任务')
    with pytest.raises(DomainError):read(service.store,other,{'call_id':'large'})


def test_large_write_receipt_retains_success_and_evidence(service,game,monkeypatch):
    h=Harness(service,Settings());calls=[];actual=[]
    original=h.execute
    async def execute(g,r,name,args,e):
        if name=='plugin_state':actual.append(True);return {'status':'ready','details':'大回执'*20000}
        return await original(g,r,name,args,e)
    async def request(messages,tools):
        calls.append(True)
        if len(calls)==1:return call('plugin_state',{'id':'test-release','enabled':True},'saved')
        assert json.loads(messages[-1]['content'])['ok']
        return call('finish_task',{'answer':'大回执原样保存','checks':[{'label':'状态回执','call_id':'saved','path':'/value/status','expected':'ready'}]},'finish')
    monkeypatch.setattr(h,'execute',execute);monkeypatch.setattr(h,'request',request)
    result=asyncio.run(h.run(game['id'],h.create(game['id'],'大回执不等于失败',approval_mode='auto')))
    assert result['status']=='completed' and len(actual)==1 and result['executions'][0]['result']['ok']
    assert len(result['executions'][0]['result']['value']['details'])>30000


@pytest.fixture
def module(service,game):
    manifest={'label':'测试模块','description':'必须保留的说明','interactions':{'hello':{'mode':'fixed','response':'你好','schema':{'type':'object'},'player_enabled':False}}}
    service.command(game['id'],'module.put',{'key':'demo','version':1,'manifest':manifest},'install')
    return service.snapshot(game['id'])['game']['version'],manifest


def test_patch_preview_and_apply_preserves_unmentioned_fields(service,game,module):
    version,manifest=module
    args={'key':'demo','version':version,'edits':[{'op':'test','path':'/interactions/hello/response','value':'你好'},{'op':'replace','path':'/interactions/hello/response','value':'欢迎'}],'explanation':'只修改招呼语'}
    preview=patch(service,game['id'],args)
    assert preview['valid'] and service.snapshot(game['id'])['game']['version']==version
    applied=patch(service,game['id'],args,'patch-once')
    assert applied['status']=='applied'
    current=service.snapshot(game['id'])['game']['definition']['modules']['demo']
    assert current['description']==manifest['description'] and current['interactions']['hello']['response']=='欢迎'
    with pytest.raises(DomainError):patch(service,game['id'],args,'patch-twice')


def test_patch_requires_approval_and_is_not_disguised_as_read(service,game,module,monkeypatch):
    version,_=module;h=Harness(service,Settings())
    async def request(*args):return call('patch_module',{'key':'demo','version':version,'edits':[{'op':'replace','path':'/label','value':'新名字'}],'explanation':'更名'},'modify')
    monkeypatch.setattr(h,'request',request)
    result=asyncio.run(h.run(game['id'],h.create(game['id'],'更名')))
    assert result['status']=='awaiting_approval' and service.snapshot(game['id'])['game']['version']==version


def test_patch_player_fields_triggers_handoff_but_unrelated_gm_module_does_not(service,game,module):
    from mythweaver.task_checks import player_targets
    service.command(game['id'],'policy.update',{'version':0,'player_agent_enabled':True},'enable-player')
    h=Harness(service,Settings());rid=h.create(game['id'],'核对局部修改交接')
    with service.store.session.begin() as db:
        row=ToolExecution(run_id=rid,call_id='patch',name='patch_module',arguments={'key':'demo','edits':[{'op':'replace','path':'/label','value':'新名字'}]},status='completed',result={'ok':True})
        db.add(row);db.flush()
        assert not player_targets(db,rid)
        row.arguments={**row.arguments,'edits':[{'op':'add','path':'/interactions/hello/player_enabled','value':True}]}
        assert player_targets(db,rid)=={game['id']}


def test_patch_conflict_and_invalid_definition_make_no_changes(service,game,module):
    version,_=module;before=deepcopy(service.snapshot(game['id']))
    for edits in ([{'op':'remove','path':'/missing'}],[{'op':'replace','path':'/interactions/hello/mode','value':'invented'}],[{'op':'test','path':'/label','value':'wrong'},{'op':'replace','path':'/label','value':'new'}]):
        with pytest.raises(DomainError):patch(service,game['id'],{'key':'demo','version':version,'edits':edits,'explanation':'冲突测试'},'invalid')
        assert service.snapshot(game['id'])==before


def test_context_compaction_keeps_latest_admin_decision_and_prefix_stable():
    initial=[{'role':'system','content':'stable system'},{'role':'user','content':'game'},{'role':'user','content':'原始目标'}]
    messages=[*initial,{'role':'user','content':'方案二可以，但是风险不能太大；保留我的账号。'}]
    for i in range(18):
        messages.extend([{'role':'assistant','tool_calls':[{'id':str(i),'function':{'name':'inspect_runtime','arguments':'{}'}}]},
                         {'role':'tool','tool_call_id':str(i),'content':json.dumps({'ok':True,'value':{'source':'x'*10000}})}])
    projector=ContextProjector();first,stats=projector.prepare(messages)
    assert stats['compacted'] and stats['projected_chars']<stats['source_chars']
    assert '保留我的账号' in json.dumps(first,ensure_ascii=False)
    extra={'role':'user','content':'继续保留超级稀有品种'}
    second,next_stats=projector.prepare([*messages,extra])
    assert second[:-1]==first and second[-1]==extra and not next_stats['compacted']
    # No orphaned tool result after compaction.
    ids={c['id'] for m in second for c in m.get('tool_calls',[])}
    assert all(m['tool_call_id'] in ids for m in second if m['role']=='tool')
