"""Run configured player actions in a rollback-only database transaction."""
import json
from typing import Any,Literal
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


class RecordCheck(Input):
    id:str=Field(min_length=1,max_length=36,description='管理员读取的实际记录ID，仅供测试断言，不交给玩家')
    path:str=Field(pattern=r'^/(data(?:/.*)?|version|visibility|archived)$',description='记录JSON Pointer，例如/data/coins')
    expected:Any


class ChatStep(Input):
    message:str=Field(min_length=1,max_length=2000)
    expect_interaction:str|None=Field(default=None,max_length=80)
    expect_reply_contains:str|None=Field(default=None,max_length=500)
    expect_action:Literal['none','applied','pending','confirmation_required','cancelled','expired','stale']|None=None
    expect_state_changed:bool|None=None
    expect_records:list[RecordCheck]=Field(default_factory=list)
    expect_reply_not_contains:list[str]=Field(default_factory=list)
    expect_reply_max_chars:int|None=Field(default=None,ge=1)
    expect_error_code:str|None=Field(default=None,min_length=1,max_length=80)


def record_matches(db,game_id,check):
    row=db.get(Entity,check.id)
    if not row or row.game_id!=game_id:return False
    value=packed(row)
    try:
        for key in check.path[1:].split('/'):
            key=key.replace('~1','/').replace('~0','~')
            value=value[int(key)] if isinstance(value,list) and key.isdecimal() else value[key]
    except (TypeError,KeyError,IndexError):return False
    return json.dumps(value,sort_keys=True,ensure_ascii=False)==json.dumps(check.expected,sort_keys=True,ensure_ascii=False)


class ConversationSimulation(Input):
    allow_external:bool=False
    actor:str=Field(pattern=r'^qq:[0-9]{1,25}$')
    audience:Literal['private','group']
    messages:list[ChatStep]=Field(min_length=1)


async def simulate_conversation(service,settings,game_id,arguments):
    """Real player model and actual transactions inside an always-rolled-back connection."""
    from types import SimpleNamespace
    from sqlalchemy.orm import sessionmaker
    from .service import Service
    from .player import PlayerAgent,private_reply
    from .agent import fingerprint
    from .plugins import Plugins
    args=ConversationSimulation.model_validate(arguments)
    from .platform_rules import read as read_platform
    with service.store.session() as db:limit=read_platform(db)['rules']['simulation_chat_limit']
    if limit and len(args.messages)>limit:raise DomainError('SIMULATION_LIMIT',f'聊天试玩超过当前{limit}轮预算，可由管理员修改simulation_chat_limit')
    with service.store.engine.connect() as connection:
        outer=connection.begin()
        scoped=SimpleNamespace(engine=service.store.engine,session=sessionmaker(bind=connection,expire_on_commit=False,join_transaction_mode='create_savepoint'))
        trial=Service(scoped);trial._plugins=Plugins(trial,service.plugins.root)
        if not args.allow_external:
            def blocked(*a,**kw):raise DomainError('TRIAL_EXTERNAL_BLOCKED','隔离测试默认不运行宿主插件。可信插件需管理员明确允许外部副作用',409)
            trial._plugins.invoke=blocked
        player=PlayerAgent(trial,settings);steps=[]
        try:
            with scoped.session.begin() as db:trial.game(db,game_id,lock=True)
            conversation='simulation-'+uuid4().hex
            for index,step in enumerate(args.messages):
                try:
                    with scoped.session() as db:before=fingerprint(db,trial.game(db,game_id))
                    result=await player.turn(game_id,args.actor,step.message,'chat-test-'+uuid4().hex,
                                             audience=args.audience,conversation_id=conversation)
                    action=result.get('action')
                    reply=private_reply(result)
                    checks={}
                    if step.expect_interaction is not None:checks['interaction']=bool(action) and action['interaction']==step.expect_interaction
                    if step.expect_reply_contains is not None:checks['reply_contains']=step.expect_reply_contains in reply
                    if step.expect_action is not None:checks['action']=not action if step.expect_action=='none' else bool(action) and action['status']==step.expect_action
                    with scoped.session() as db:
                        changed=before!=fingerprint(db,trial.game(db,game_id))
                        if step.expect_state_changed is not None:checks['state_changed']=changed==step.expect_state_changed
                        for check_index,check in enumerate(step.expect_records):checks['record_'+str(check_index+1)]=record_matches(db,game_id,check)
                    for check_index,forbidden in enumerate(step.expect_reply_not_contains):checks['reply_excludes_'+str(check_index+1)]=forbidden not in reply
                    if step.expect_reply_max_chars is not None:checks['reply_length']=len(reply)<=step.expect_reply_max_chars
                    if step.expect_error_code is not None:checks['error_code']=False
                    steps.append({'index':index+1,'message':step.message,'ok':all(checks.values()),'checks':checks,
                                  'answer':reply,'action':action,'state_changed':changed,'visible_records':result['records']})
                except DomainError as exc:
                    # A rejected business action can itself be the expected outcome.
                    checks={'error_code':exc.code==step.expect_error_code} if step.expect_error_code is not None else {}
                    with scoped.session() as db:
                        changed=before!=fingerprint(db,trial.game(db,game_id))
                        if step.expect_state_changed is not None:checks['state_changed']=changed==step.expect_state_changed
                        for check_index,check in enumerate(step.expect_records):checks['record_'+str(check_index+1)]=record_matches(db,game_id,check)
                    if step.expect_action is not None:checks['action']=step.expect_action=='none'
                    if step.expect_interaction is not None:checks['interaction']=False
                    if step.expect_reply_contains is not None:checks['reply_contains']=step.expect_reply_contains in exc.message
                    for check_index,forbidden in enumerate(step.expect_reply_not_contains):checks['reply_excludes_'+str(check_index+1)]=forbidden not in exc.message
                    if step.expect_reply_max_chars is not None:checks['reply_length']=len(exc.message)<=step.expect_reply_max_chars
                    steps.append({'index':index+1,'message':step.message,'ok':step.expect_error_code is not None and all(checks.values()),
                                  'checks':checks,'code':exc.code,'message_detail':exc.message,'state_changed':changed})
            asserted=all(bool(step.get('checks')) for step in steps)
            return {'test_layer':'player_model_conversation','actor':args.actor,'audience':args.audience,
                    'steps':steps,'passed':asserted and all(step['ok'] for step in steps),'asserted':asserted,'rolled_back':True,
                    'notice':'实际调用玩家模型、受众权限、聊天历史和交互结算；数据库最终回滚，无QQ发送器。不验证NapCat传输。插件文件/网络外部副作用不能回滚；工具使用期间会锁定该游戏。'}
        finally:outer.rollback()
