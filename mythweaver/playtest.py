"""Run configured player actions in a rollback-only database transaction."""
import json
from typing import Literal
from uuid import uuid4

from pydantic import Field
from sqlalchemy import select

from .db import Entity,Outbox
from .definitions import DomainError
from .schemas import Input
from .service import packed


class Action(Input):
    interaction:str=Field(min_length=1,max_length=80)
    data:dict


class Simulation(Input):
    actor:str=Field(default='admin',pattern=r'^(admin|qq:[0-9]{1,25})$')
    audience:Literal['private','group']='private'
    actions:list[Action]=Field(min_length=1)


def simulate(service,game_id,actor,actions,audience='private'):
    body=Simulation.model_validate({'actor':actor,'actions':actions,'audience':audience})
    from .platform_rules import read as read_platform
    with service.store.session() as db:limit=read_platform(db)['rules']['simulation_action_limit']
    if limit and len(body.actions)>limit:raise DomainError('SIMULATION_LIMIT',f'结构化试玩超过当前{limit}步预算，可由管理员修改simulation_action_limit')
    if len(json.dumps(body.model_dump(),ensure_ascii=False))>60000:
        raise DomainError('SIMULATION_LIMIT','试玩输入超过60000字符')
    from .player import AUDIENCE,validate_action,view
    token=AUDIENCE.set(body.audience)
    with service.store.session() as db:
        transaction=db.begin()
        try:
            game=service.game(db,game_id,lock=True)
            existing_deliveries=set(db.scalars(select(Outbox.id).where(Outbox.game_id==game_id)))
            steps=[]
            for index,action in enumerate(body.actions):
                # One rejected action must not discard earlier actions in the same trial.
                try:
                    with db.begin_nested():
                        payload=action.model_dump()
                        if actor!='admin':validate_action(db,game,actor,payload)
                        result=service.execute(db,game_id,actor,'playtest:'+uuid4().hex,'submit',payload)
                        db.flush()
                        visible=view(service,db,game,actor) if actor!='admin' else None
                        if visible is not None:
                            from .instructions import page
                            visible=page(visible,limit=2000)
                        step={'index':index+1,'ok':True,'action':payload,'result':result,'observation':visible}
                except DomainError as exc:
                    step={'index':index+1,'ok':False,'code':exc.code,'message':exc.message}
                steps.append(step)
            records=list(db.scalars(select(Entity).where(Entity.game_id==game_id).order_by(Entity.id).limit(201)))
            if len(records)>200:raise DomainError('SIMULATION_LIMIT','试玩最多检查200条记录，请缩小游戏范围')
            queued=list(db.scalars(select(Outbox).where(Outbox.game_id==game_id,Outbox.status=='queued').order_by(Outbox.created_at)))
            deliveries=[{'recipient':row.group_id,'text':row.text} for row in queued if row.id not in existing_deliveries]
            result={'actor':actor,'audience':body.audience,'test_layer':'structured_actions_only','steps':steps,'final_records':[packed(r) for r in records],
                    'deliveries':deliveries,
                    'notice':'仅验证结构化行动，不经过玩家模型，不能据此声明自然语言聊天通过。游戏数据、事项、审计和待发消息全部回滚；未发送QQ消息。可信插件外部副作用无法回滚。',
                    'rolled_back':True}
            if len(json.dumps(result,ensure_ascii=False))>28000:
                raise DomainError('SIMULATION_LIMIT','试玩结果过大，请减少步骤或记录')
            return result
        finally:
            transaction.rollback()
            AUDIENCE.reset(token)


class ChatStep(Input):
    message:str=Field(min_length=1,max_length=2000)
    expect_interaction:str|None=Field(default=None,max_length=80)
    expect_reply_contains:str|None=Field(default=None,max_length=500)


class ConversationSimulation(Input):
    actor:str=Field(pattern=r'^qq:[0-9]{1,25}$')
    audience:Literal['private','group']
    messages:list[ChatStep]=Field(min_length=1)


async def simulate_conversation(service,settings,game_id,arguments):
    """Real player model and actual transactions inside an always-rolled-back connection."""
    from types import SimpleNamespace
    from sqlalchemy.orm import sessionmaker
    from .service import Service
    from .player import PlayerAgent
    from .plugins import Plugins
    args=ConversationSimulation.model_validate(arguments)
    from .platform_rules import read as read_platform
    with service.store.session() as db:limit=read_platform(db)['rules']['simulation_chat_limit']
    if limit and len(args.messages)>limit:raise DomainError('SIMULATION_LIMIT',f'聊天试玩超过当前{limit}轮预算，可由管理员修改simulation_chat_limit')
    with service.store.engine.connect() as connection:
        outer=connection.begin()
        scoped=SimpleNamespace(engine=service.store.engine,session=sessionmaker(bind=connection,expire_on_commit=False,join_transaction_mode='create_savepoint'))
        trial=Service(scoped);trial._plugins=Plugins(trial,service.plugins.root)
        player=PlayerAgent(trial,settings);steps=[]
        try:
            with scoped.session.begin() as db:trial.game(db,game_id,lock=True)
            conversation='simulation-'+uuid4().hex
            for index,step in enumerate(args.messages):
                try:
                    result=await player.turn(game_id,args.actor,step.message,'chat-test-'+uuid4().hex,
                                             audience=args.audience,conversation_id=conversation)
                    action=result.get('action')
                    checks={}
                    if step.expect_interaction is not None:checks['interaction']=bool(action) and action['interaction']==step.expect_interaction
                    if step.expect_reply_contains is not None:checks['reply_contains']=step.expect_reply_contains in result['answer']
                    steps.append({'index':index+1,'message':step.message,'ok':all(checks.values()),'checks':checks,
                                  'answer':result['answer'],'action':action,'visible_records':result['records']})
                except DomainError as exc:
                    steps.append({'index':index+1,'message':step.message,'ok':False,'code':exc.code,'message_detail':exc.message})
            return {'test_layer':'player_model_conversation','actor':args.actor,'audience':args.audience,
                    'steps':steps,'passed':all(step['ok'] for step in steps),'rolled_back':True,
                    'notice':'实际调用玩家模型、受众权限、聊天历史和交互结算；数据库最终回滚，无QQ发送器。不验证NapCat传输。插件文件/网络外部副作用不能回滚；工具使用期间会锁定该游戏。'}
        finally:outer.rollback()
