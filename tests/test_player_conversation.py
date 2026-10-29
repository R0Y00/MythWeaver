import asyncio
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import func,select

from mythweaver.config import Settings
from mythweaver.db import Entity,Outbox,PlayerTurn,Submission
from mythweaver.definitions import DomainError
from mythweaver.player import PlayerAgent,conversation_history
from mythweaver.playtest import simulate,simulate_conversation


def setup(service,game,group=True):
    service.command(game['id'],'definition',{'version':1,'definition':{'types':{'secret':{
        'schema':{'type':'object','properties':{'value':{'type':'integer'}}}}},
        'interactions':{'play':{'mode':'fixed','response':'开局或行动成功','player_enabled':True,'group_enabled':group,
            'player_description':'范围开局，数字猜测；隐藏状态由服务端判断',
            'schema':{'type':'object','properties':{'guess':{'type':'integer'}},'additionalProperties':False}}}}},'def')
    service.command(game['id'],'policy.update',{'version':0,'player_agent_enabled':True},'enable')
    service.command(game['id'],'entity.create',{'type_key':'secret','data':{'value':123456789},'visibility':'private'},'secret')


def action():
    return {'tool_calls':[{'function':{'name':'submit_action','arguments':json.dumps({'interaction':'play','data':{}})}}]}


def test_group_history_contains_receipts_but_not_private_or_other_group(service,game,monkeypatch):
    setup(service,game);player=PlayerAgent(service,Settings());seen=[]
    async def request(context,history,prompt):
        history=conversation_history(context,history);seen.append(history)
        assert '123456789' not in json.dumps(context)
        if prompt=='第三句':
            assert len(history)==2
            assert history[0]['actual_receipt']['status']=='applied'
            assert history[0]['reply']=='开局或行动成功'
        if prompt in ('其他群','私聊'):assert history==[]
        return action()
    monkeypatch.setattr(player,'request',request)
    for index,prompt in enumerate(('开局','第二句','第三句')):
        result=asyncio.run(player.turn(game['id'],'qq:1',prompt,str(index),audience='group',conversation_id='group-A'))
        assert result['answer']=='开局或行动成功' and result['records']==[]
    asyncio.run(player.turn(game['id'],'qq:1','其他群','other',audience='group',conversation_id='group-B'))
    asyncio.run(player.turn(game['id'],'qq:1','私聊','private'))
    with pytest.raises(DomainError) as error:
        asyncio.run(player.turn(game['id'],'qq:1','开局','0',audience='group',conversation_id='group-B'))
    assert error.value.code=='IDEMPOTENCY_CONFLICT'


def test_receipt_epoch_change_and_private_history_do_not_replay_old_answers():
    row=SimpleNamespace(prompt='旧请求',answer='OLD_SECRET',result={'action':{'result':'OLD_SECRET','status':'applied'},'context_epoch':[1,1,0]})
    for context in ({'audience':'private','context_epoch':[1,1,0]},{'audience':'group','context_epoch':[2,1,0]}):
        assert conversation_history(context,[row])==[{'request':'旧请求'}]


def test_natural_conversation_runs_player_model_with_history_and_rolls_back(service,game,monkeypatch):
    setup(service,game);calls=[]
    async def request(self,context,history,prompt):
        calls.append(prompt)
        assert context['audience']=='group' and context['records']==[]
        if len(calls)>1:
            assert len(history)==len(calls)-1
            assert conversation_history(context,history)[-1]['actual_receipt']['status']=='applied'
        return action()
    monkeypatch.setattr(PlayerAgent,'request',request)
    before=service.snapshot(game['id'])
    result=asyncio.run(simulate_conversation(service,Settings(),game['id'],{'actor':'qq:1','audience':'group',
        'messages':[{'message':message,'expect_interaction':'play','expect_reply_contains':'成功'} for message in ('100到200','150','125')]}))
    assert result['passed'] and result['test_layer']=='player_model_conversation' and calls==['100到200','150','125']
    assert service.snapshot(game['id'])==before
    with service.store.session() as db:
        assert db.scalar(select(func.count()).select_from(PlayerTurn))==0
        assert db.scalar(select(func.count()).select_from(Submission))==0


def test_conversation_exposes_model_refusal_as_failed_expectation(service,game,monkeypatch):
    setup(service,game)
    async def request(self,context,history,prompt):return {'content':'没有可见记录，无法行动'}
    monkeypatch.setattr(PlayerAgent,'request',request)
    result=asyncio.run(simulate_conversation(service,Settings(),game['id'],{'actor':'qq:1','audience':'group',
        'messages':[{'message':'125','expect_interaction':'play'}]}))
    assert not result['passed'] and not result['steps'][0]['checks']['interaction']


def test_structured_simulation_group_permissions_and_layer_are_explicit(service,game):
    setup(service,game,group=False)
    result=simulate(service,game['id'],'qq:1',[{'interaction':'play','data':{}}],audience='group')
    assert result['test_layer']=='structured_actions_only' and result['audience']=='group'
    assert result['steps'][0]['code']=='INTERACTION_DENIED'
    assert simulate(service,game['id'],'qq:1',[{'interaction':'play','data':{}}])['steps'][0]['ok']


def test_onebot_group_route_retains_same_chat_receipts_without_sending_qq(service,game,monkeypatch):
    from mythweaver.onebot import OneBot
    from mythweaver.platform_rules import update
    setup(service,game)
    service.command(game['id'],'channel.bind',{'group_id':'12345'},'bind')
    with service.store.session.begin() as db:update(db,0,{'group_trigger':'mention','group_input':'agent'})
    settings=Settings();bot=OneBot(service.store,service,settings);calls=[]
    async def request(context,history,prompt):
        calls.append(prompt)
        if len(calls)>1:assert conversation_history(context,history)[-1]['actual_receipt']['status']=='applied'
        return action()
    monkeypatch.setattr(bot.player_agent,'request',request)
    for index,prompt in enumerate(('重新开一把100到200','150','125')):
        bot.accept_event({'message_type':'group','group_id':12345,'user_id':1,'message_id':index,
                          'message':[{'type':'at','data':{'qq':settings.bot_id}},{'type':'text','data':{'text':prompt}}]})
        item=bot.claim_private();assert item
        asyncio.run(bot.process_private(item))
    with service.store.session() as db:
        turns=list(db.scalars(select(PlayerTurn)))
        assert len(turns)==3 and all(t.result['conversation_id']=='12345' and t.result['audience']=='group' for t in turns)
        replies=list(db.scalars(select(Outbox)))
        assert len(replies)==3 and all(r.status=='queued' and r.text=='开局或行动成功' for r in replies)
