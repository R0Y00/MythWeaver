import asyncio
import json
from copy import deepcopy
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select

from mythweaver.config import Settings
from mythweaver.db import Inbox,Outbox,PlayerTurn
from mythweaver.definitions import DomainError,validate_definition
from mythweaver.onebot import OneBot
from mythweaver.player import PlayerAgent,view


def command(service,game,kind,payload):
    return service.command(game['id'],kind,payload,uuid4().hex)


@pytest.fixture
def playground(service,game):
    definition={'types':{'account':{'schema':{'type':'object','properties':{
        'owner':{'type':'string'},'credits':{'type':'integer','minimum':0},
        'items':{'type':'array','items':{'type':'string'}},'secret':{'type':'string'}},
        'required':['owner','credits','items','secret'],'additionalProperties':False},
        'player_access':{'owner_field':'owner','read_fields':['credits','items']}}},
        'interactions':{'collect':{'player_enabled':True,'player_description':'消耗3积分领取一份资料',
            'mode':'rule','schema':{'type':'object','properties':{'account':{'type':'string','x-reference':{'type_key':'account'}}},'required':['account'],'additionalProperties':False},
            'condition':{'left':{'entity':{'id_from':'account','type_key':'account','field':'credits'}},'op':'gte','right':{'value':3}},
            'effects':[{'type_key':'account','id_from':'account','field':'credits','operation':'increment','value':-3},
                       {'type_key':'account','id_from':'account','field':'items','operation':'append','value':'资料'}]},
            'special':{'player_enabled':True,'mode':'manual','schema':{'type':'object','properties':{'text':{'type':'string'}},'required':['text']}}}}
    command(service,game,'definition',{'version':1,'definition':definition})
    command(service,game,'policy.update',{'version':0,'player_agent_enabled':True,'player_instructions':'普通领取自动执行，特殊行动由管理员决定','instructions':'管理端绝密剧情'})
    a=command(service,game,'entity.create',{'type_key':'account','data':{'owner':'qq:1','credits':5,'items':[],'secret':'A隐藏字段'}})
    b=command(service,game,'entity.create',{'type_key':'account','data':{'owner':'qq:2','credits':9,'items':['B秘密背包'],'secret':'B隐藏字段'}})
    return definition,a,b


def player(service):
    return PlayerAgent(service,Settings(llm_base_url='https://provider.example/v1',llm_api_key='dummy',llm_model='test'))


def mock_model(monkeypatch,fn):
    real=httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(fn),**kw))


def action(interaction,data):
    return httpx.Response(200,json={'choices':[{'message':{'tool_calls':[{'function':{'name':'submit_action','arguments':json.dumps({'interaction':interaction,'data':data})}}]}}]})


def test_player_model_only_receives_scoped_fields_and_atomic_action_receipt(service,game,playground,monkeypatch):
    _,a,b=playground
    calls=[]
    def handler(request):
        body=request.content.decode()
        assert b['id'] not in body and 'B秘密背包' not in body
        assert 'A隐藏字段' not in body and '管理端绝密剧情' not in body
        assert 'prepare_plan' not in body and 'entity.update' not in body
        calls.append(body)
        return action('collect',{'account':a['id']})
    mock_model(monkeypatch,handler)
    agent=player(service)
    result=asyncio.run(agent.turn(game['id'],'qq:1','领取资料','once'))
    assert result['action']['status']=='applied'
    assert result['records'][0]['data']=={'credits':2,'items':['资料']}
    again=asyncio.run(agent.turn(game['id'],'qq:1','领取资料','once'))
    assert result==again and len(calls)==1
    snapshot=service.snapshot(game['id'])
    assert next(e for e in snapshot['entities'] if e['id']==b['id'])['data']['credits']==9
    with pytest.raises(DomainError) as error:
        asyncio.run(agent.turn(game['id'],'qq:1','另一条输入','once'))
    assert error.value.code=='IDEMPOTENCY_CONFLICT'


def test_forged_other_player_target_rejected_without_changes(service,game,playground,monkeypatch):
    _,a,b=playground
    mock_model(monkeypatch,lambda request:action('collect',{'account':b['id']}))
    with pytest.raises(DomainError) as error:
        asyncio.run(player(service).turn(game['id'],'qq:1','偷用另一个账号','x'))
    assert error.value.code=='TARGET_DENIED'
    assert not service.snapshot(game['id'])['submissions']
    assert {e['id']:e['data']['credits'] for e in service.snapshot(game['id'])['entities']}=={a['id']:5,b['id']:9}


def test_player_repairs_schema_before_any_action_is_executed(service,game,playground,monkeypatch):
    _,a,_=playground;calls=[]
    def handler(request):
        body=json.loads(request.content);calls.append(body)
        variants=body['tools'][0]['function']['parameters']['oneOf']
        assert any(v['properties']['interaction']['enum']==['collect'] for v in variants)
        if len(calls)==1:return action('collect',{'account':a['id'],'extra_invented_field':True})
        assert not service.snapshot(game['id'])['submissions']
        assert 'validation_feedback' in json.dumps(body)
        return action('collect',{'account':a['id']})
    mock_model(monkeypatch,handler)
    result=asyncio.run(player(service).turn(game['id'],'qq:1','领取资料','repair'))
    assert len(calls)==2 and result['records'][0]['data']['credits']==2
    assert len(service.snapshot(game['id'])['submissions'])==1


def test_player_repair_budget_exhausted_never_executes(service,game,playground,monkeypatch):
    _,a,_=playground;calls=[]
    def handler(request):calls.append(request);return action('collect',{'account':a['id'],'extra':1})
    mock_model(monkeypatch,handler)
    with pytest.raises(DomainError) as error:asyncio.run(player(service).turn(game['id'],'qq:1','领取','invalid-forever'))
    assert error.value.code=='VALIDATION_FAILED' and len(calls)==3
    assert not service.snapshot(game['id'])['submissions']
    assert next(e for e in service.snapshot(game['id'])['entities'] if e['id']==a['id'])['data']['credits']==5


def test_public_read_does_not_grant_write_and_direct_command_cannot_bypass(service,game,playground):
    _,a,b=playground
    command(service,game,'entity.update',{**{k:b[k] for k in ('id','type_key','data','version')},'visibility':'public'})
    with service.store.session() as db:
        context=view(service,db,service.game(db,game['id']),'qq:1')
        assert len(context['records'])==2
    with pytest.raises(DomainError) as error:
        service.command(game['id'],'submit',{'interaction':'collect','data':{'account':b['id']}},'direct',actor='qq:1')
    assert error.value.code=='TARGET_DENIED'
    assert not service.snapshot(game['id'])['submissions']


def test_insufficient_resource_rolls_back_whole_action(service,game,playground,monkeypatch):
    _,a,_=playground
    mock_model(monkeypatch,lambda request:action('collect',{'account':a['id']}))
    agent=player(service)
    asyncio.run(agent.turn(game['id'],'qq:1','领取','first'))
    with pytest.raises(DomainError) as error:
        asyncio.run(agent.turn(game['id'],'qq:1','再领取','second'))
    assert error.value.code=='PRECONDITION_FAILED'
    assert len(service.snapshot(game['id'])['submissions'])==1


def test_manual_action_stays_pending_and_admin_decision_visible_only_to_actor(service,game,playground,monkeypatch):
    mock_model(monkeypatch,lambda request:action('special',{'text':'尝试一个特殊行动'}))
    result=asyncio.run(player(service).turn(game['id'],'qq:1','特殊行动','pending'))
    assert result['action']['status']=='pending' and '等待管理员' in result['answer']
    command(service,game,'decide',{'id':result['action']['id'],'decision':'applied','result':'管理员指定的私有结果'})
    with service.store.session() as db:
        own=view(service,db,service.game(db,game['id']),'qq:1')
        other=view(service,db,service.game(db,game['id']),'qq:2')
    assert own['decisions'][0]['result']=='管理员指定的私有结果' and other['decisions']==[]


def test_permissions_change_during_model_request_prevents_execution(service,game,playground,monkeypatch):
    _,a,_=playground
    def handler(request):
        command(service,game,'participant.update',{'actor':'qq:1','blocked':True})
        return action('collect',{'account':a['id']})
    mock_model(monkeypatch,handler)
    with pytest.raises(DomainError) as error:
        asyncio.run(player(service).turn(game['id'],'qq:1','领取','race'))
    assert error.value.code=='STATE_STALE'
    assert not service.snapshot(game['id'])['submissions']


def test_history_is_per_actor_and_old_answers_not_replayed(service,game,playground,monkeypatch):
    with service.store.session.begin() as db:
        db.add(PlayerTurn(game_id=game['id'],actor='qq:2',key='b',prompt='B的私有请求',answer='B私密回复',status='answered'))
        db.add(PlayerTurn(game_id=game['id'],actor='qq:1',key='a',prompt='我想领取资料',answer='旧隐藏字段内容',status='answered'))
    def handler(request):
        body=request.content.decode()
        assert 'B的私有请求' not in body and 'B私密回复' not in body and '旧隐藏字段内容' not in body
        assert '我想领取资料' in body
        return httpx.Response(200,json={'choices':[{'message':{'content':'请确认要领取哪份资料？'}}]})
    mock_model(monkeypatch,handler)
    result=asyncio.run(player(service).turn(game['id'],'qq:1','先问一下','question'))
    assert result['action'] is None and '未执行行动' in result['answer']
    assert not service.snapshot(game['id'])['submissions']


def test_private_binding_prefix_routing_receipt_and_unbind(service,store,game,playground,monkeypatch):
    _,a,_=playground
    command(service,game,'channel.bind',{'group_id':'private-1'})
    bot=OneBot(store,service,Settings(llm_base_url='https://provider.example/v1',llm_api_key='dummy',llm_model='test'))
    frame={'message_type':'private','user_id':1,'message_id':1,'message':[{'type':'text','data':{'text':'普通聊天'}}]}
    bot.accept_event(frame)
    with store.session() as db:
        assert list(db.scalars(select(Inbox)))==[]
    frame['message']=[{'type':'text','data':{'text':'/mw 领取资料'}}]
    bot.accept_event(frame)
    bot.accept_event(frame)
    bot.process_inbox()  # Group processor must not take private requests.
    item=bot.claim_private()
    assert item and item[2]=='qq:1' and item[4]=='private-1'
    mock_model(monkeypatch,lambda request:action('collect',{'account':a['id']}))
    asyncio.run(bot.process_private(item))
    with store.session() as db:
        out=db.scalar(select(Outbox))
        assert out.group_id=='private-1' and '当前可见状态' in out.text and 'B秘密背包' not in out.text
    command(service,game,'channel.unbind',{'group_id':'private-1'})
    assert bot.next_delivery() is None


def test_player_access_validation_and_default_off(service,game):
    assert not service.snapshot(game['id'])['policy']['player_agent_enabled']
    with pytest.raises(DomainError):
        validate_definition({'types':{'a':{'player_access':{'owner_field':'missing','read_fields':[]}}}})
    with pytest.raises(DomainError) as error:
        asyncio.run(player(service).turn(game['id'],'qq:1','你好','off'))
    assert error.value.code=='PLAYER_AGENT_DISABLED'


def test_old_receipt_not_returned_after_visibility_change(service,game,playground,monkeypatch):
    definition,a,_=playground
    mock_model(monkeypatch,lambda request:action('collect',{'account':a['id']}))
    agent=player(service)
    asyncio.run(agent.turn(game['id'],'qq:1','领取','old'))
    new=deepcopy(definition)
    new['types']['account']['player_access']['read_fields']=[]
    command(service,game,'definition',{'version':2,'definition':new})
    with pytest.raises(DomainError) as error:
        asyncio.run(agent.turn(game['id'],'qq:1','领取','old'))
    assert error.value.code=='STATE_STALE'
    assert len(service.snapshot(game['id'])['submissions'])==1


def test_admin_natural_language_configures_player_agent_using_shared_commands(service,game,monkeypatch):
    from mythweaver.agent import Agent
    def handler(request):
        body=request.content.decode()
        assert 'player_agent_enabled' in body and 'player_access' in body
        return httpx.Response(200,json={'choices':[{'message':{'tool_calls':[{'function':{'name':'prepare_plan','arguments':json.dumps({
            'explanation':'启用玩家助手，特殊请求交给管理员',
            'commands':[{'kind':'policy.update','payload':{'version':0,'player_agent_enabled':True,'player_instructions':'只解释当前资料，特殊请求由管理员决定'}}]})}}]}}]})
    mock_model(monkeypatch,handler)
    result=asyncio.run(Agent(service,Settings(llm_base_url='https://provider.example/v1',llm_api_key='dummy',llm_model='test')).propose(game['id'],'让助手参与玩家流程，特殊请求由我决定',execute=True))
    assert result['status']=='applied'
    assert service.snapshot(game['id'])['policy']['player_agent_enabled']


def test_private_delivery_uses_private_onebot_action_only(service,store,game,playground):
    command(service,game,'channel.bind',{'group_id':'private-1'})
    with store.session.begin() as db:
        db.add(Outbox(group_id='private-1',game_id=game['id'],text='玩家1私有回复'))
    bot=OneBot(store,service,Settings())
    async def scenario():
        delivered=asyncio.Event()
        calls=[]
        async def fake_call(action,params,timeout=15):
            calls.append((action,params))
            delivered.set()
        bot.call=fake_call
        bot.socket=object()
        task=asyncio.create_task(bot.pump())
        await asyncio.wait_for(delivered.wait(),3)
        task.cancel()
        await asyncio.gather(task,return_exceptions=True)
        assert calls[0][0]=='send_private_msg' and calls[0][1]['user_id']==1
        assert 'group_id' not in calls[0][1]
    asyncio.run(scenario())


def test_private_player_example_is_opt_in_and_has_no_real_binding(service):
    from mythweaver.examples import instantiate
    game=instantiate(service,'private-player-agent')
    snapshot=service.snapshot(game['id'])
    assert snapshot['policy']['player_agent_enabled'] and snapshot['channels']==[]
    with service.store.session() as db:
        context=view(service,db,service.game(db,game['id']),'qq:1')
        assert len(context['records'])==1 and 'gm_note' not in context['records'][0]['data']


def test_player_rehearsal_http_requires_admin_and_uses_player_scope(store,service,game,playground):
    from fastapi.testclient import TestClient
    from mythweaver.api import create_app
    from mythweaver.agent_reads import read
    settings=Settings(llm_api_key='',llm_base_url='',llm_model='')
    with TestClient(create_app(settings,store)) as client:
        path=f"/api/games/{game['id']}/player"
        assert client.get(path+'/records/qq:1').status_code==401
        client.headers.update({'x-mythweaver':'1'})
        assert client.post('/api/login',json={'password':settings.admin_password}).status_code==200
        data=client.get(path+'/records/qq:1').json()
        assert len(data['records'])==1 and 'secret' not in data['records'][0]['data']
        assert client.get(path+'/records/admin').status_code==400
        response=client.post(path+'/turn',json={'actor':'qq:1','prompt':'领取','key':'http'})
        assert response.status_code==409 and response.json()['code']=='MODEL_NOT_CONFIGURED'
        turns=client.get(path+'/turns').json()
        assert len(turns)==1 and turns[0]['status']=='failed'
        assert read(service,game['id'],'list_controls',{'section':'player_turns'})[0]['actor']=='qq:1'
        other=service.create_game('另一个空间','')
        assert read(service,other['id'],'list_controls',{'section':'player_turns'})==[]
        assert service.snapshot(game['id'])['submissions']==[]
