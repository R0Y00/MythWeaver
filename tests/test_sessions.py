import asyncio
import json

import httpx
import pytest
from sqlalchemy import select,text

from mythweaver.agent import Agent
from mythweaver.config import Settings
from mythweaver.db import AgentRun,AgentSession,HarnessTask
from mythweaver.definitions import DomainError
from mythweaver.harness import Harness
from mythweaver.sessions import attach,context


@pytest.fixture
def client(store):
    from fastapi.testclient import TestClient
    from mythweaver.api import create_app
    settings=Settings(llm_api_key='',llm_base_url='',llm_model='')
    with TestClient(create_app(settings,store)) as value:
        value.headers.update({'x-mythweaver':'1'})
        assert value.post('/api/login',json={'password':settings.admin_password}).status_code==200
        yield value


def test_session_management_retains_history_and_is_scoped(client,service,game):
    base=f"/api/games/{game['id']}/agent/sessions"
    row=client.post(base,json={}).json();sid=row['id']
    assert row['title']=='新会话'
    assert client.patch(base+'/'+sid,json={'title':'猜数 [1]','pinned':True}).status_code==200
    assert client.get(base,params={'q':'[1]'}).json()['total']==1
    other=service.create_game('另一个空间','')
    assert client.get(f"/api/games/{other['id']}/agent/sessions/{sid}").status_code==404
    with service.store.session.begin() as db:
        db.add(AgentRun(game_id=game['id'],session_id=sid,prompt='创建游戏',answer='已创建',status='answered'))
    assert len(client.get(base+'/'+sid).json()['runs'])==1
    assert client.patch(base+'/'+sid,json={'archived':True}).status_code==200
    assert client.get(base).json()['total']==0
    assert client.get(base,params={'view':'archived'}).json()['total']==1
    with pytest.raises(DomainError) as error:
        Harness(service,Settings()).create(game['id'],'继续',sid)
    assert error.value.code=='SESSION_CLOSED'
    assert client.patch(base+'/'+sid,json={'deleted':True}).status_code==200
    assert client.get(base,params={'view':'trash'}).json()['total']==1
    assert client.patch(base+'/'+sid,json={'deleted':False,'archived':False}).status_code==200
    assert len(client.get(base+'/'+sid).json()['runs'])==1
    assert client.patch(base+'/'+sid,json={'title':'   '}).status_code==400


def test_one_running_turn_per_session_and_resume_history(client,service,game):
    harness=Harness(service,Settings());first=harness.create(game['id'],'第一轮')
    with service.store.session() as db:sid=db.get(AgentRun,first).session_id
    with pytest.raises(DomainError) as error:harness.create(game['id'],'第二轮',sid)
    assert error.value.code=='SESSION_BUSY'
    base=f"/api/games/{game['id']}/agent/sessions/{sid}"
    assert client.patch(base,json={'deleted':True}).status_code==409
    assert client.patch(base,json={'archived':True}).status_code==409
    with service.store.session.begin() as db:
        row=db.get(AgentRun,first);row.status='failed';row.answer='已安装插件，剩余配置未完成'
    harness.resume(game['id'],first,'继续配置')
    with service.store.session() as db:
        run=db.get(AgentRun,first)
        assert run.answer=='' and run.status=='running'
        assert run.followups[0]['previous_answer']=='已安装插件，剩余配置未完成'
        assert run.followups[0]['prompt']=='继续配置'


def test_harness_conversation_context_excludes_other_sessions(service,game):
    harness=Harness(service,Settings());first=harness.create(game['id'],'本会话的独有目标')
    unrelated=harness.create(game['id'],'其他会话的秘密')
    with service.store.session.begin() as db:
        old=db.get(AgentRun,first);sid=old.session_id;old.status='answered';old.answer='第一轮的结果'
        db.get(AgentRun,unrelated).status='answered'
    followup=harness.create(game['id'],'把它完善',sid)
    with service.store.session() as db:
        content=db.get(HarnessTask,followup).messages[-1]['content']
        assert '第一轮的结果' in content and '本会话的独有目标' in content
        assert '其他会话的秘密' not in content


def test_empty_planner_response_retries_then_fails_instead_of_answered(service,game,monkeypatch):
    real=httpx.AsyncClient;requests=[]
    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200,json={'choices':[{'message':{'content':None}}]})
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(handler),**kw))
    agent=Agent(service,Settings(llm_base_url='https://provider.example/v1',llm_api_key='test-key',llm_model='test'))
    with pytest.raises(DomainError) as error:asyncio.run(agent.propose(game['id'],'创建游戏'))
    assert error.value.code=='EMPTY_MODEL_RESPONSE' and len(requests)==4
    with service.store.session() as db:
        row=db.scalar(select(AgentRun));assert row.status=='failed' and row.error_code=='EMPTY_MODEL_RESPONSE'


def test_discussion_context_is_per_session_and_empty_replies_fail(service,game,monkeypatch):
    with service.store.session.begin() as db:
        sid=attach(db,game['id'],'目标');other=attach(db,game['id'],'另一个目标')
        db.add_all([AgentRun(game_id=game['id'],session_id=sid,prompt='同会话背景',answer='同会话答复',status='answered'),AgentRun(game_id=game['id'],session_id=other,prompt='不可混入的背景',answer='其他会话答复',status='answered')])
    real=httpx.AsyncClient
    def handler(request):
        body=json.loads(request.content);content=json.dumps(body['messages'],ensure_ascii=False)
        assert '同会话背景' in content and '不可混入的背景' not in content
        return httpx.Response(200,json={'choices':[{'message':{'content':'   '}}]})
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(handler),**kw))
    agent=Agent(service,Settings(llm_base_url='https://provider.example/v1',llm_api_key='test-key',llm_model='test'))
    with pytest.raises(DomainError) as error:asyncio.run(agent.chat(game['id'],'继续',sid))
    assert error.value.code=='EMPTY_MODEL_RESPONSE'


def test_migration_backfills_legacy_runs(store,service,game):
    with store.session.begin() as db:
        run=AgentRun(game_id=game['id'],prompt='历史任务',answer='',status='answered');db.add(run);db.flush();rid=run.id
        db.execute(text('DELETE FROM schema_migrations WHERE version=10'))
        db.execute(text('ALTER TABLE agent_runs DROP COLUMN session_id'))
        db.execute(text('ALTER TABLE agent_runs DROP COLUMN followups'))
    store.initialize()
    with store.session() as db:
        assert db.get(AgentRun,rid).session_id==rid
        assert db.get(AgentSession,rid).title=='历史任务'


def test_module_contract_and_preflight_prevent_writes_on_invalid_spec(service,game):
    from mythweaver.agent_reads import read
    runtime=read(service,game['id'],'inspect_runtime',{})
    contract=runtime['authoring_contract']
    manifest=contract['minimal_module_example']
    before=service.snapshot(game['id'])
    result=read(service,game['id'],'validate_module',{'key':'counter_demo','manifest':manifest})
    assert result['valid'] and result['definition_version']==before['game']['version']
    assert service.snapshot(game['id'])==before
    broken={**manifest,'interactions':{'bad':{'mode':'rule','response':{'script':'not supported'}}}}
    with pytest.raises(DomainError):read(service,game['id'],'validate_module',{'key':'counter_demo','manifest':broken})
    assert service.snapshot(game['id'])==before
