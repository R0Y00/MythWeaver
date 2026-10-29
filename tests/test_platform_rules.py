import asyncio
import json

import pytest
from sqlalchemy import select

from mythweaver.config import Settings
from mythweaver.db import Inbox,Outbox,PlayerTurn
from mythweaver.definitions import DomainError
from mythweaver.onebot import OneBot
from mythweaver.platform_rules import Rules,effective,extract,read,update
from mythweaver.player import PlayerAgent


def test_global_defaults_game_overrides_and_explicit_all_games_scope(service,game):
    service.command(game['id'],'policy.update',{'version':0,'command_prefix':'#','player_agent_enabled':True,'player_instructions':'保留此游戏说明'},'override')
    with service.store.session.begin() as db:
        update(db,0,{'command_prefix':'!','private_trigger':'all'})
        assert effective(db,game['id'])['command_prefix']=='#'
        assert effective(db,game['id'])['private_trigger']=='all'
    with service.store.session.begin() as db:
        result=update(db,1,{'command_prefix':'你好','prefix_requires_space':False},'all_games')
        assert result['overrides_reset']==[game['id']]
        assert result['rules']['private_trigger']=='all'
        assert effective(db,game['id'])['command_prefix']=='你好'
    from mythweaver.policy import policy_for
    with service.store.session() as db:
        policy,_=policy_for(db,game['id']);assert policy['player_agent_enabled'] and policy['player_instructions']=='保留此游戏说明'
    with service.store.session.begin() as db:
        with pytest.raises(DomainError):update(db,0,{'private_trigger':'disabled'})


@pytest.mark.parametrize('mode,text,mentioned,expected',[
    ('prefix','/mw 玩',False,'玩'),('prefix','玩',False,None),('all','玩',False,'玩'),('all','/mw 玩',False,'玩'),
    ('disabled','/mw 玩',True,None),('mention','玩',False,None),('mention','玩',True,'玩')])
def test_trigger_contract(mode,text,mentioned,expected):
    rules=Rules(group_trigger=mode).model_dump()
    assert extract(text,rules,False,mentioned)==expected


def test_custom_prefix_without_space():
    rules=Rules(command_prefix='!',prefix_requires_space=False).model_dump()
    assert extract('!猜5',rules,True)=='猜5'
    assert extract('猜5',rules,True) is None


def test_private_all_only_captures_explicitly_bound_conversations(service,game):
    service.command(game['id'],'policy.update',{'version':0,'player_agent_enabled':True},'enable')
    service.command(game['id'],'channel.bind',{'group_id':'private-1'},'bind')
    with service.store.session.begin() as db:update(db,0,{'private_trigger':'all'})
    bot=OneBot(service.store,service,Settings())
    for user in (1,2):bot.accept_event({'message_type':'private','user_id':user,'message_id':1,'message':[{'type':'text','data':{'text':'直接开始玩'}}]})
    with service.store.session() as db:
        rows=list(db.scalars(select(Inbox)));assert len(rows)==1 and rows[0].actor=='qq:1' and rows[0].content=='直接开始玩' and rows[0].route=='agent'


def test_group_agent_keeps_private_state_and_history_out_of_prompt(service,game,monkeypatch):
    definition={'types':{'note':{'schema':{'type':'object','properties':{'owner':{'type':'string'},'text':{'type':'string'},'secret':{'type':'string'}}},
        'player_access':{'owner_field':'owner','read_fields':['text']}}},'interactions':{
        'private_only':{'mode':'record','player_enabled':True},'public_action':{'mode':'record','player_enabled':True,'group_enabled':True}}}
    service.command(game['id'],'definition',{'version':1,'definition':definition},'def')
    service.command(game['id'],'policy.update',{'version':0,'player_agent_enabled':True},'policy')
    service.command(game['id'],'entity.create',{'type_key':'note','visibility':'private','data':{'owner':'qq:1','text':'PRIVATE_NOTE','secret':'GM_SECRET'}},'private')
    service.command(game['id'],'entity.create',{'type_key':'note','visibility':'public','data':{'owner':'qq:1','text':'PUBLIC_NOTE','secret':'GM_SECRET'}},'public')
    with service.store.session.begin() as db:db.add(PlayerTurn(game_id=game['id'],actor='qq:1',key='old',prompt='PRIVATE_HISTORY',answer='PRIVATE_ANSWER',status='answered',result={'audience':'private'}))
    player=PlayerAgent(service,Settings())
    async def request(context,history,prompt):
        encoded=json.dumps(context)+str([r.prompt for r in history])
        assert 'PRIVATE_NOTE' not in encoded and 'GM_SECRET' not in encoded and 'PRIVATE_HISTORY' not in encoded
        assert 'private_only' not in context['interactions'] and 'public_action' in context['interactions']
        assert context['records'][0]['data']=={'text':'PUBLIC_NOTE'}
        return {'content':'公开的讨论答复'}
    monkeypatch.setattr(player,'request',request)
    result=asyncio.run(player.turn(game['id'],'qq:1','在群里讨论','public-turn',audience='group'))
    assert result['audience']=='group' and '公开的讨论答复' in result['answer']


def test_group_mention_route_is_agent_only_when_configured(service,game):
    service.command(game['id'],'channel.bind',{'group_id':'12345'},'bind')
    with service.store.session.begin() as db:update(db,0,{'group_trigger':'mention','group_input':'agent'})
    settings=Settings();bot=OneBot(service.store,service,settings)
    frame={'message_type':'group','group_id':12345,'user_id':1,'message_id':1,'message':[{'type':'text','data':{'text':'普通群聊'}}]}
    bot.accept_event(frame)
    frame['message_id']=2;frame['message'].insert(0,{'type':'at','data':{'qq':settings.bot_id}});bot.accept_event(frame)
    with service.store.session() as db:
        rows=list(db.scalars(select(Inbox)));assert len(rows)==1 and rows[0].route=='agent'
