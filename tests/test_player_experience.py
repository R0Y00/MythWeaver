"""Behavior boundaries at the actual player, transaction and presentation layers."""
import asyncio
import json
from uuid import uuid4

import pytest
from sqlalchemy import select

from mythweaver.config import Settings
from mythweaver.db import PlayerTurn,Submission,HarnessTask
from mythweaver.player import PlayerAgent,conversation_history,private_reply
from mythweaver.playtest import simulate_conversation


def setup(service,game):
    definition={'types':{'account':{'label':'账户','schema':{'type':'object','properties':{
        'owner':{'type':'string'},'coins':{'type':'integer','minimum':0,'title':'余额'},'secret':{'type':'string'}},
        'required':['owner','coins','secret'],'additionalProperties':False},
        'player_access':{'owner_field':'owner','read_fields':['coins']}}},
        'interactions':{'spend':{'label':'购买','mode':'rule','player_enabled':True,
            'schema':{'type':'object','properties':{'account':{'type':'string','x-reference':{'type_key':'account'}}},'required':['account'],'additionalProperties':False},
            'effects':[{'type_key':'account','id_from':'account','field':'coins','operation':'increment','value':-3}]}}}
    service.command(game['id'],'definition',{'version':1,'definition':definition},uuid4().hex)
    service.command(game['id'],'policy.update',{'version':0,'player_agent_enabled':True,'free_mode':'record',
        'player_instructions':'先问用途，选择后再处理。'},uuid4().hex)
    record=service.command(game['id'],'entity.create',{'type_key':'account','data':{'owner':'qq:1','coins':5,'secret':'HIDDEN'}},uuid4().hex)
    return record


def tool(name,args):return {'tool_calls':[{'function':{'name':name,'arguments':json.dumps(args)}}]}


def test_player_text_and_action_feedback_do_not_append_internal_records(service,game,monkeypatch):
    record=setup(service,game)
    async def request(self,context,history,prompt):
        if prompt=='你好':return {'content':'你好，想做什么？'}
        return tool('submit_action',{'interaction':'spend','data':{'account':record['id']}})
    monkeypatch.setattr(PlayerAgent,'request',request)
    player=PlayerAgent(service,Settings())
    hello=asyncio.run(player.turn(game['id'],'qq:1','你好','hello'))
    assert private_reply(hello)=='你好，想做什么？' and hello['action'] is None
    result=asyncio.run(player.turn(game['id'],'qq:1','购买','purchase'))
    assert result['records'][0]['data']=={'coins':2}
    assert '余额：5 → 2' in result['answer']
    assert private_reply(result)==result['answer']
    assert record['id'] not in private_reply(result) and '当前可见状态' not in private_reply(result)


def test_current_private_question_is_available_for_next_answer_but_not_after_state_change(service,game,monkeypatch):
    record=setup(service,game);seen=[]
    async def request(self,context,history,prompt):
        seen.append(conversation_history(context,history))
        if prompt=='用途A':assert seen[-1][-1]['reply']=='你要用途A还是用途B？'
        if prompt=='状态更新后':assert all('reply' not in item for item in seen[-1])
        if prompt=='其他会话':assert seen[-1]==[]
        return {'content':'你要用途A还是用途B？'}
    monkeypatch.setattr(PlayerAgent,'request',request);player=PlayerAgent(service,Settings())
    asyncio.run(player.turn(game['id'],'qq:1','开始','question',conversation_id='A'))
    asyncio.run(player.turn(game['id'],'qq:1','用途A','reply',conversation_id='A'))
    service.command(game['id'],'entity.update',{**{k:record[k] for k in ('id','type_key','version','data')},'visibility':'public'},'change')
    asyncio.run(player.turn(game['id'],'qq:1','状态更新后','changed',conversation_id='A'))
    asyncio.run(player.turn(game['id'],'qq:1','其他会话','other',conversation_id='B'))


def test_gm_handoff_is_pending_even_in_record_mode_and_has_no_resource_effect(service,game,monkeypatch):
    record=setup(service,game)
    async def request(self,context,history,prompt):return tool('request_gm',{'question':'这条规则与旧公告冲突，请GM裁决。'})
    monkeypatch.setattr(PlayerAgent,'request',request);player=PlayerAgent(service,Settings())
    first=asyncio.run(player.turn(game['id'],'qq:1','我想使用没有定义的能力','review'))
    assert first['action']['interaction']=='_review' and first['action']['status']=='pending'
    assert '等待管理员' in first['answer'] and first['records'][0]['data']['coins']==5
    assert asyncio.run(player.turn(game['id'],'qq:1','我想使用没有定义的能力','review'))==first
    with service.store.session() as db:
        issues=list(db.scalars(select(Submission)))
        assert len(issues)==1 and '我想使用没有定义的能力' in issues[0].data['text']
    service.command(game['id'],'decide',{'id':first['action']['id'],'decision':'applied','result':'本次不消耗资源，规则稍后补齐。'},'decide')
    assert service.snapshot(game['id'])['entities'][0]['data']['coins']==5


def test_player_business_simulation_checks_no_action_exact_resources_and_expected_rejection(service,game,monkeypatch):
    record=setup(service,game)
    async def request(self,context,history,prompt):
        if prompt=='你好':return {'content':'你好！'}
        return tool('submit_action',{'interaction':'spend','data':{'account':record['id']}})
    monkeypatch.setattr(PlayerAgent,'request',request)
    args={'actor':'qq:1','audience':'private','messages':[
        {'message':'你好','expect_action':'none','expect_state_changed':False,'expect_reply_max_chars':40,
         'expect_reply_not_contains':['type_key','HIDDEN'],'expect_records':[{'id':record['id'],'path':'/data/coins','expected':5}]},
        {'message':'买一次','expect_action':'applied','expect_state_changed':True,'expect_records':[{'id':record['id'],'path':'/data/coins','expected':2}]},
        {'message':'余额不足再买','expect_error_code':'VALIDATION_FAILED','expect_action':'none','expect_state_changed':False,
         'expect_records':[{'id':record['id'],'path':'/data/coins','expected':2}]}]}
    before=service.snapshot(game['id'])
    result=asyncio.run(simulate_conversation(service,Settings(),game['id'],args))
    assert result['passed'] and result['asserted'] and all(s['ok'] for s in result['steps'])
    assert service.snapshot(game['id'])==before
    with service.store.session() as db:assert not list(db.scalars(select(PlayerTurn)))


def test_simulation_rejects_wrong_business_expectations_not_just_correct_keywords(service,game,monkeypatch):
    record=setup(service,game)
    async def request(self,context,history,prompt):return tool('submit_action',{'interaction':'spend','data':{'account':record['id']}})
    monkeypatch.setattr(PlayerAgent,'request',request)
    result=asyncio.run(simulate_conversation(service,Settings(),game['id'],{'actor':'qq:1','audience':'private','messages':[
        {'message':'不要买','expect_action':'none','expect_state_changed':False,'expect_records':[{'id':record['id'],'path':'/data/coins','expected':5}]}]}))
    assert not result['passed'] and result['steps'][0]['checks']=={'action':False,'state_changed':False,'record_1':False}


def test_player_model_receives_admin_guidance_separately_and_gm_tool(service,game,monkeypatch):
    import httpx
    setup(service,game);seen=[]
    def handler(request):
        body=json.loads(request.content);seen.append(body)
        assert body['messages'][1]['role']=='system'
        assert '先问用途，选择后再处理。' in body['messages'][1]['content']
        assert '先问用途，选择后再处理。' not in body['messages'][2]['content']
        assert {t['function']['name'] for t in body['tools']}=={'submit_action','request_gm'}
        return httpx.Response(200,json={'choices':[{'message':{'content':'你好！'}}]})
    real=httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(handler),**kw))
    player=PlayerAgent(service,Settings(llm_base_url='https://example.test',llm_api_key='dummy',llm_model='test'))
    asyncio.run(player.turn(game['id'],'qq:1','你好','test'))
    assert len(seen)==1


def test_unasserted_conversation_is_observation_not_acceptance(service,game,monkeypatch):
    setup(service,game)
    async def request(self,context,history,prompt):return {'content':'你好！'}
    monkeypatch.setattr(PlayerAgent,'request',request)
    result=asyncio.run(simulate_conversation(service,Settings(),game['id'],{'actor':'qq:1','audience':'private','messages':[{'message':'你好'}]}))
    assert not result['passed'] and not result['asserted'] and result['steps'][0]['ok']


def test_manager_uses_current_game_instructions_without_legacy_plan_prompt(service,game):
    from mythweaver.harness import Harness
    from mythweaver.platform_rules import read
    service.command(game['id'],'policy.update',{'version':0,'instructions':'INITIAL_GAME_GUIDANCE'},'guide')
    harness=Harness(service,Settings());rid=harness.create(game['id'],'讨论玩法')
    with service.store.session() as db:
        prompt=db.get(HarnessTask,rid).messages[0]['content']
        assert 'INITIAL_GAME_GUIDANCE' in prompt and '通过 prepare_plan 提交' not in prompt
    service.command(game['id'],'policy.update',{'version':1,'instructions':'UPDATED_GAME_GUIDANCE'},'guide-update')
    with service.store.session() as db:
        prompt=harness.system_prompt(db,game['id'],read(db)['rules'])
        assert 'UPDATED_GAME_GUIDANCE' in prompt and 'INITIAL_GAME_GUIDANCE' not in prompt
