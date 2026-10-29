import asyncio
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from mythweaver.agent import Agent
from mythweaver.api import create_app
from mythweaver.checkpoints import create_checkpoint, restore_checkpoint
from mythweaver.config import Settings
from mythweaver.db import AgentPlan, AgentRun, Channel, Checkpoint, GamePolicy, Inbox, ModelProvider, Outbox, Participant
from mythweaver.definitions import DomainError
from mythweaver.models import Models, validate_url


@pytest.fixture
def client(store):
    settings=Settings(llm_api_key='',llm_base_url='',llm_model='')
    with TestClient(create_app(settings,store)) as value:
        value.headers.update({'x-mythweaver':'1','idempotency-key':uuid4().hex})
        assert value.post('/api/login',json={'password':settings.admin_password}).status_code==200
        yield value


def provider_body(**overrides):
    return {'name':'测试服务','base_url':'https://provider.example/v1','api_key':'dummy-test-key','models':['test-model'],**overrides}


def test_provider_keys_encrypted_not_returned_and_blank_preserves(client,store):
    saved=client.post('/api/models/providers',json=provider_body()).json()
    assert saved['has_key'] and 'dummy-test-key' not in str(saved) and 'encrypted_key' not in saved
    view=client.get('/api/models').json()
    assert 'dummy-test-key' not in str(view)
    with store.session() as db:
        row=db.get(ModelProvider,saved['id'])
        assert row.encrypted_key and row.encrypted_key!='dummy-test-key'
        assert Models(store,Settings()).cipher.decrypt(row.encrypted_key.encode()).decode()=='dummy-test-key'
    updated=client.put('/api/models/providers/'+saved['id'],json=provider_body(api_key='',version=saved['version'])).json()
    assert updated['has_key']
    assert client.put('/api/models/providers/'+saved['id'],json=provider_body(api_key='',version=saved['version'])).status_code==409
    cleared=client.put('/api/models/providers/'+saved['id'],json=provider_body(api_key='',clear_key=True,version=updated['version'])).json()
    assert not cleared['has_key']


@pytest.mark.parametrize('url',['http://remote.example/v1','https://secret:password@provider.example/v1','https://provider.example/v1?key=secret','file:///private','https://provider.example/v1#fragment'])
def test_provider_url_rejects_insecure_or_embedded_credentials(url):
    with pytest.raises(DomainError):
        validate_url(url)


def test_task_routing_uses_live_config_and_disabled_provider_blocks(client,store):
    provider=client.post('/api/models/providers',json=provider_body()).json()
    route={'planner':{'provider_id':provider['id'],'model':'test-model','max_tokens':2048,'timeout':30}}
    saved=client.put('/api/models/routing',json={'version':0,'routing':route})
    assert saved.status_code==200
    selected=Models(store,Settings()).resolve()
    assert selected.llm_model=='test-model' and selected.max_tokens==2048
    assert client.get('/api/status').json()['agent']['configured']
    client.put('/api/models/providers/'+provider['id'],json=provider_body(version=provider['version'],enabled=False))
    assert not client.get('/api/status').json()['agent']['configured']


def test_policy_whitelist_pause_and_interaction_permissions(client,store,service,game):
    path='/api/games/'+game['id']
    policy=client.get(path+'/policy').json()
    response=client.put(path+'/policy',json={**policy,'allow_users':['qq:1'],'command_prefix':'/game'})
    assert response.status_code==200
    payload={'interaction':'_free','data':{'text':'用户输入'}}
    with pytest.raises(DomainError) as error:
        service.command(game['id'],'submit',payload,uuid4().hex,actor='qq:2')
    assert error.value.code=='PARTICIPATION_DENIED'
    service.command(game['id'],'submit',payload,uuid4().hex,actor='qq:1')
    assert client.get(path+'/participants').json()[0]['actor']=='qq:1'
    assert client.put(path+'/participants',json={'actor':'qq:1','blocked':True}).status_code==200
    with pytest.raises(DomainError):
        service.command(game['id'],'submit',payload,uuid4().hex,actor='qq:1')
    current=client.get(path+'/policy').json()
    assert client.put(path+'/policy',json={**current,'allow_users':[],'paused':True},headers={'idempotency-key':uuid4().hex}).status_code==200
    with pytest.raises(DomainError) as error:
        service.command(game['id'],'submit',payload,uuid4().hex,actor='qq:3')
    assert error.value.code=='GAME_PAUSED'
    # A paused game still permits administrator maintenance.
    assert service.command(game['id'],'submit',payload,uuid4().hex)['status']=='pending'


def test_interaction_allowlist_enforced_in_service(service,game):
    service.command(game['id'],'definition',{'version':1,'definition':{'interactions':{'special':{'mode':'record','allow_users':['qq:7']}}}},uuid4().hex)
    with pytest.raises(DomainError) as error:
        service.command(game['id'],'submit',{'interaction':'special','data':{}},uuid4().hex,actor='qq:8')
    assert error.value.code=='INTERACTION_DENIED'
    assert service.command(game['id'],'submit',{'interaction':'special','data':{}},uuid4().hex,actor='qq:7')['status']=='applied'


def test_checkpoint_restore_safe_snapshot_monotonic_versions_idempotent(service,store,game):
    service.command(game['id'],'definition',{'version':1,'definition':{'types':{'note':{'schema':{'type':'object'}}}}},uuid4().hex)
    record=service.command(game['id'],'entity.create',{'type_key':'note','data':{'text':'之前'}},uuid4().hex)
    checkpoint=create_checkpoint(service,game['id'],'之前')
    service.command(game['id'],'entity.update',{'id':record['id'],'type_key':'note','data':{'text':'之后'},'version':record['version']},uuid4().hex)
    command_key=uuid4().hex
    result=restore_checkpoint(service,game['id'],checkpoint['id'],2,command_key)
    assert restore_checkpoint(service,game['id'],checkpoint['id'],2,command_key)==result
    restored=service.snapshot(game['id'])['entities'][0]
    assert restored['data']=={'text':'之前'} and restored['version']>2
    with pytest.raises(DomainError):
        service.command(game['id'],'entity.update',{'id':record['id'],'type_key':'note','data':{'text':'过期编辑'},'version':2},uuid4().hex)
    with store.session() as db:
        rows=list(db.scalars(select(Checkpoint).where(Checkpoint.game_id==game['id'])))
        assert len(rows)==2 and any('恢复前' in r.label for r in rows)


def test_checkpoint_scope_and_cancellation_of_old_queues(service,store,game):
    checkpoint=create_checkpoint(service,game['id'],'空白')
    other=service.create_game('其他游戏','')
    with pytest.raises(DomainError):
        restore_checkpoint(service,other['id'],checkpoint['id'],1,uuid4().hex)
    with store.session.begin() as db:
        db.add(Inbox(key='1:1:1',game_id=game['id'],actor='qq:1',content='提交 old'))
        db.add(Outbox(group_id='1',game_id=game['id'],text='old'))
    restore_checkpoint(service,game['id'],checkpoint['id'],1,uuid4().hex)
    with store.session() as db:
        assert db.scalar(select(Inbox)).status=='cancelled'
        assert db.scalar(select(Outbox)).status=='cancelled'


def test_blueprint_creates_independent_empty_game(client,service,game):
    definition={'types':{'custom':{'schema':{'type':'object'}}},'interactions':{}}
    blueprint=client.post('/api/blueprints',json={'name':'自定义模板','definition':definition}).json()
    created=client.post('/api/blueprints/'+blueprint['id']+'/instantiate',json={'label':'新空间'}).json()
    snapshot=service.snapshot(created['id'])
    assert snapshot['game']['definition']==definition
    assert snapshot['entities']==[] and snapshot['submissions']==[] and snapshot['channels']==[]


def test_batch_all_or_nothing_and_envelope_idempotency(client,service,game):
    path='/api/games/'+game['id']+'/batch'
    first={'kind':'submit','payload':{'interaction':'_free','data':{'text':'one'}}}
    invalid={'kind':'entity.create','payload':{'type_key':'absent','data':{}}}
    assert client.post(path,json={'commands':[first,invalid]}).status_code==400
    assert service.snapshot(game['id'])['submissions']==[]
    response=client.post(path,json={'commands':[first]})
    assert response.status_code==200
    assert client.post(path,json={'commands':[first]}).json()==response.json()
    assert client.post(path,json={'commands':[first,first]}).status_code==409
    assert len(service.snapshot(game['id'])['submissions'])==1


def test_cancelling_plan_prevents_execution(client,service,game):
    agent=Agent(service,Settings())
    plan=agent.prepare(game['id'],'记录','仅记录',[{'kind':'submit','payload':{'interaction':'_free','data':{'text':'不应执行'}}}])
    assert client.post('/api/games/'+game['id']+'/agent/plans/'+plan['id']+'/cancel',json={}).status_code==200
    with pytest.raises(DomainError) as error:
        agent.apply(game['id'],plan['id'])
    assert error.value.code=='PLAN_CANCELLED'
    assert service.snapshot(game['id'])['submissions']==[]


def test_model_failure_is_persisted_without_secret(service,store,game):
    agent=Agent(service,Settings(llm_base_url='',llm_api_key='',llm_model=''))
    with pytest.raises(DomainError):
        asyncio.run(agent.propose(game['id'],'测试未配置'))
    with store.session() as db:
        row=db.scalar(select(AgentRun))
        assert row.status=='failed' and row.error_code=='MODEL_NOT_CONFIGURED'


def test_unknown_delivery_requires_explicit_duplicate_ack(client,store,game):
    with store.session.begin() as db:
        row=Outbox(game_id=game['id'],group_id='1',text='测试',status='unknown')
        db.add(row)
        db.flush()
        row_id=row.id
    path=f"/api/games/{game['id']}/deliveries/{row_id}/retry"
    assert client.post(path,json={}).status_code==409
    assert client.post(path,json={'acknowledge_duplicate_risk':True}).status_code==200

