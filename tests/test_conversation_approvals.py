import asyncio
import json

import pytest
from sqlalchemy import select

from mythweaver.approvals import decide
from mythweaver.config import Settings
from mythweaver.db import AgentRun,AgentSession,HarnessTask,ToolExecution
from mythweaver.definitions import DomainError
from mythweaver.harness import Harness
from mythweaver.platform_rules import read,update


def call(name,args,cid='change'):
    return {'tool_calls':[{'id':cid,'type':'function','function':{'name':name,'arguments':json.dumps(args)}}]}


def install_model(monkeypatch,harness):
    async def request(messages,tools):
        if messages[-1]['role']=='tool':
            outcome=json.loads(messages[-1]['content'])
            return {'content':'操作已处理。' if outcome['ok'] else '未执行，继续讨论。'}
        return call('execute_commands',{'explanation':'记录约定的内容','commands':[{'kind':'submit','payload':{'interaction':'_free','data':{'text':'agreed'}}}]})
    monkeypatch.setattr(harness,'request',request)


def test_manual_approval_never_changes_state_before_permission(service,game,monkeypatch):
    harness=Harness(service,Settings());install_model(monkeypatch,harness)
    rid=harness.create(game['id'],'按刚才讨论的记录一条内容')
    result=asyncio.run(harness.run(game['id'],rid))
    assert result['status']=='awaiting_approval' and result['executions'][0]['status']=='awaiting_approval'
    assert not service.snapshot(game['id'])['submissions']
    eid=result['executions'][0]['id']
    decide(harness,game['id'],rid,eid,'approve')
    result=asyncio.run(harness.run(game['id'],rid))
    assert result['status']=='answered' and len(service.snapshot(game['id'])['submissions'])==1
    assert len(result['executions'])==1 and result['executions'][0]['approval']['decision']=='approve'
    with pytest.raises(DomainError):decide(harness,game['id'],rid,eid,'approve')


def test_rejection_is_observation_and_no_effect(service,game,monkeypatch):
    harness=Harness(service,Settings());install_model(monkeypatch,harness)
    rid=harness.create(game['id'],'记录一条内容')
    result=asyncio.run(harness.run(game['id'],rid))
    decide(harness,game['id'],rid,result['executions'][0]['id'],'reject','先别改')
    result=asyncio.run(harness.run(game['id'],rid))
    assert result['executions'][0]['result']['code']=='ADMIN_REJECTED'
    assert not service.snapshot(game['id'])['submissions']


def test_new_user_message_invalidates_pending_request_and_keeps_context(service,game,monkeypatch):
    harness=Harness(service,Settings());install_model(monkeypatch,harness)
    rid=harness.create(game['id'],'记录一条内容');result=asyncio.run(harness.run(game['id'],rid));eid=result['executions'][0]['id']
    harness.resume(game['id'],rid,'不要写入了，只解释一下')
    async def discussion(messages,tools):
        assert messages[-1]['content']=='不要写入了，只解释一下'
        assert any(m['role']=='tool' and 'ADMIN_REVISED' in m['content'] for m in messages)
        return {'content':'我们继续讨论，没有修改。'}
    monkeypatch.setattr(harness,'request',discussion)
    result=asyncio.run(harness.run(game['id'],rid))
    assert result['status']=='answered' and result['executions'][0]['approval']['decision']=='invalidated'
    with pytest.raises(DomainError):decide(harness,game['id'],rid,eid,'approve')
    assert not service.snapshot(game['id'])['submissions']


def test_auto_permission_is_explicit_session_setting(service,game,monkeypatch):
    harness=Harness(service,Settings());install_model(monkeypatch,harness)
    rid=harness.create(game['id'],'记录一条内容',approval_mode='auto')
    result=asyncio.run(harness.run(game['id'],rid))
    assert result['status']=='answered' and result['executions'][0]['approval']['decision']=='auto'
    assert len(service.snapshot(game['id'])['submissions'])==1


def test_changed_state_invalidates_approval_before_effect(service,game,monkeypatch):
    harness=Harness(service,Settings());install_model(monkeypatch,harness)
    rid=harness.create(game['id'],'记录一条内容');result=asyncio.run(harness.run(game['id'],rid))
    with service.store.session.begin() as db:update(db,0,{'command_prefix':'!'})
    decide(harness,game['id'],rid,result['executions'][0]['id'],'approve')
    result=asyncio.run(harness.run(game['id'],rid))
    assert result['executions'][0]['result']['code']=='APPROVAL_STALE' and not service.snapshot(game['id'])['submissions']


def test_platform_change_waits_for_approval_and_preserves_unspecified_settings(service,game,monkeypatch):
    harness=Harness(service,Settings())
    async def request(messages,tools):
        if messages[-1]['role']=='tool':return {'content':'平台默认私聊现在无需前缀。'}
        return call('platform_rules_update',{'version':0,'changes':{'private_trigger':'all'},'apply_scope':'all_games'})
    monkeypatch.setattr(harness,'request',request)
    rid=harness.create(game['id'],'把全平台的私聊都改为不用指令')
    result=asyncio.run(harness.run(game['id'],rid))
    with service.store.session() as db:assert read(db)['rules']['private_trigger']=='prefix'
    decide(harness,game['id'],rid,result['executions'][0]['id'],'approve')
    result=asyncio.run(harness.run(game['id'],rid))
    with service.store.session() as db:
        rules=read(db);assert rules['rules']['private_trigger']=='all' and rules['rules']['command_prefix']=='/mw'
    assert result['status']=='answered'


def test_paused_approval_survives_restart_and_cancel_cannot_be_approved(service,game,monkeypatch):
    harness=Harness(service,Settings());install_model(monkeypatch,harness)
    rid=harness.create(game['id'],'记录');result=asyncio.run(harness.run(game['id'],rid));eid=result['executions'][0]['id']
    service.store.initialize()
    other=Harness(service,Settings());assert other.inspect(game['id'],rid)['status']=='awaiting_approval'
    assert other.cancel(game['id'],rid)['status']=='cancelled'
    with pytest.raises(DomainError):decide(other,game['id'],rid,eid,'approve')
    assert not service.snapshot(game['id'])['submissions']


def test_pending_batch_continues_exactly_once_and_asks_for_next_effect(service,game,monkeypatch):
    harness=Harness(service,Settings());requests=[]
    async def request(messages,tools):
        requests.append(messages)
        if len(requests)>1:return {'content':'两项操作处理完成。'}
        return {'tool_calls':[call('execute_commands',{'explanation':str(i),'commands':[{'kind':'submit','payload':{'interaction':'_free','data':{'text':str(i)}}}]},str(i))['tool_calls'][0] for i in range(2)]}
    monkeypatch.setattr(harness,'request',request)
    rid=harness.create(game['id'],'记录两条')
    first=asyncio.run(harness.run(game['id'],rid));decide(harness,game['id'],rid,first['executions'][0]['id'],'approve')
    second=asyncio.run(harness.run(game['id'],rid))
    assert second['status']=='awaiting_approval' and len(service.snapshot(game['id'])['submissions'])==1 and len(requests)==1
    pending=next(e for e in second['executions'] if e['status']=='awaiting_approval')
    decide(harness,game['id'],rid,pending['id'],'approve')
    result=asyncio.run(harness.run(game['id'],rid))
    assert result['status']=='answered' and len(service.snapshot(game['id'])['submissions'])==2 and len(requests)==2


def test_modified_pending_arguments_cannot_reuse_approval(service,game,monkeypatch):
    harness=Harness(service,Settings());install_model(monkeypatch,harness)
    rid=harness.create(game['id'],'记录');result=asyncio.run(harness.run(game['id'],rid))
    decide(harness,game['id'],rid,result['executions'][0]['id'],'approve')
    with service.store.session.begin() as db:
        task=db.get(HarnessTask,rid);pending=json.loads(json.dumps(task.pending_calls))
        arguments=json.loads(pending[0]['function']['arguments']);arguments['commands'][0]['payload']['data']['text']='unapproved'
        pending[0]['function']['arguments']=json.dumps(arguments);task.pending_calls=pending
    result=asyncio.run(harness.run(game['id'],rid))
    assert result['status']=='failed' and result['error_code']=='APPROVAL_STALE'
    assert not service.snapshot(game['id'])['submissions']


def test_http_approvals_are_admin_only_scoped_and_resume_same_task(store,service,game,monkeypatch):
    import time
    from fastapi.testclient import TestClient
    from mythweaver.api import create_app
    async def request(self,messages,tools):
        if messages[-1]['role']=='tool':return {'content':'已处理'}
        return call('execute_commands',{'explanation':'记录','commands':[{'kind':'submit','payload':{'interaction':'_free','data':{'text':'http approval'}}}]})
    monkeypatch.setattr(Harness,'request',request)
    settings=Settings();base=f"/api/games/{game['id']}/agent"
    with TestClient(create_app(settings,store)) as client:
        client.headers['X-MythWeaver']='1'
        assert client.get('/api/platform/rules').status_code==401
        assert client.post(base+'/tasks/x/approvals/y',json={'decision':'approve'}).status_code==401
        client.post('/api/login',json={'password':settings.admin_password}).raise_for_status()
        sid=client.post(base+'/sessions',json={}).json()['id']
        started=client.post(base+'/tasks',json={'prompt':'记录','session_id':sid}).json();rid=started['id']
        def wait_status(status):
            for _ in range(80):
                current=client.get(base+'/tasks/'+rid).json()
                if current['status']==status:return current
                time.sleep(.01)
            pytest.fail('Unexpected task status: '+str(current))
        waiting=wait_status('awaiting_approval');eid=waiting['executions'][0]['id']
        assert not service.snapshot(game['id'])['submissions']
        other=service.create_game('other','')
        assert client.post(f"/api/games/{other['id']}/agent/tasks/{rid}/approvals/{eid}",json={'decision':'approve'}).status_code==404
        assert client.post(base+f'/tasks/{rid}/approvals/{eid}',json={'decision':'approve'}).status_code==200
        wait_status('answered')
        assert len(service.snapshot(game['id'])['submissions'])==1
        assert client.post(base+f'/tasks/{rid}/approvals/{eid}',json={'decision':'approve'}).status_code==409
        assert client.patch(base+'/sessions/'+sid,json={'approval_mode':'auto'}).json()['approval_mode']=='auto'
        assert client.put('/api/platform/rules',json={'version':0,'changes':{'approval_mode':'auto'}}).status_code==422
        rules=client.put('/api/platform/rules',json={'version':0,'changes':{'private_trigger':'all'}})
        assert rules.status_code==200 and rules.json()['rules']['private_trigger']=='all'


def test_schema_11_upgrades_existing_tables_to_manual_approval(store,service,game):
    from sqlalchemy import text
    harness=Harness(service,Settings());rid=harness.create(game['id'],'historical task')
    with store.session.begin() as db:
        sid=db.get(AgentRun,rid).session_id
        db.execute(text('DELETE FROM schema_migrations WHERE version=11'))
        db.execute(text('ALTER TABLE agent_sessions DROP COLUMN approval_mode'))
        db.execute(text('ALTER TABLE harness_tasks DROP COLUMN pending_calls'))
        db.execute(text('ALTER TABLE harness_tasks DROP COLUMN pending_tools'))
        db.execute(text('ALTER TABLE tool_executions DROP COLUMN approval'))
        db.execute(text('ALTER TABLE inbox DROP COLUMN route'))
    store.initialize()
    with store.session() as db:
        assert db.get(AgentSession,sid).approval_mode=='manual'
        assert db.get(HarnessTask,rid).pending_calls==[]
