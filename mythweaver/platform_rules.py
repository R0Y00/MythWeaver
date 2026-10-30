"""Versioned platform behavior, independent of any one game's mechanics."""
from typing import Literal
from pydantic import Field
from sqlalchemy import select

from .db import PlatformConfig,SystemLog
from .definitions import DomainError
from .schemas import Input

class Rules(Input):
    command_prefix:str=Field(default='/mw',min_length=1,max_length=30,pattern=r'^\S+$')
    prefix_requires_space:bool=True
    private_trigger:Literal['prefix','all','disabled']='prefix'
    group_trigger:Literal['prefix','mention','all','disabled']='prefix'
    group_input:Literal['commands','agent']='commands'
    agent_instructions:str=''
    player_instructions:str=''
    agent_max_steps:int=Field(default=0,ge=0,le=2147483647,description='任务模型请求轮数；默认0不限，管理员可主动设置预算')
    agent_timeout_seconds:int=Field(default=0,ge=0,description='任务总超时秒数；默认0不限，服务商请求和插件超时独立')
    player_timeout_seconds:int=Field(default=90,ge=0,description='玩家模型决策总超时秒数；0不限。服务商单次请求超时独立')
    player_read_steps:int=Field(default=6,ge=0,description='玩家单次决策的模型读取轮数；0不限，总超时独立')
    player_action_limit:int=Field(default=8,ge=0,description='玩家单条消息的原子行动数；0不限，仍须每个交互允许多行动')
    agent_stall_limit:int=Field(default=3,ge=0,description='连续无有效协议或跳过必要交接的响应容忍数；0不限，与任务总轮数不同')
    agent_tool_calls_per_turn:int=Field(default=4,ge=0,description='单轮模型工具调用数；0不限')
    player_data_context_chars:int=Field(default=40000,ge=0,description='玩家可见资料字符预算，不含说明；0不限')
    simulation_action_limit:int=Field(default=20,ge=0,description='一次结构化试玩行动数；0不限')
    simulation_chat_limit:int=Field(default=8,ge=0,description='一次自然语言试玩消息数；0不限')

class RulesPatch(Input):
    command_prefix:str|None=Field(default=None,min_length=1,max_length=30,pattern=r'^\S+$')
    prefix_requires_space:bool|None=None
    private_trigger:Literal['prefix','all','disabled']|None=None
    group_trigger:Literal['prefix','mention','all','disabled']|None=None
    group_input:Literal['commands','agent']|None=None
    agent_instructions:str|None=None
    player_instructions:str|None=None
    agent_max_steps:int|None=Field(default=None,ge=0,le=2147483647)
    agent_timeout_seconds:int|None=Field(default=None,ge=0)
    player_timeout_seconds:int|None=Field(default=None,ge=0)
    player_read_steps:int|None=Field(default=None,ge=0)
    player_action_limit:int|None=Field(default=None,ge=0)
    agent_stall_limit:int|None=Field(default=None,ge=0)
    agent_tool_calls_per_turn:int|None=Field(default=None,ge=0)
    player_data_context_chars:int|None=Field(default=None,ge=0)
    simulation_action_limit:int|None=Field(default=None,ge=0)
    simulation_chat_limit:int|None=Field(default=None,ge=0)

class UpdateRules(Input):
    version:int=Field(ge=0)
    changes:RulesPatch
    apply_scope:Literal['defaults','all_games']='defaults'

def read(db,lock=False):
    query=select(PlatformConfig).where(PlatformConfig.key=='runtime_rules')
    row=db.scalar(query.with_for_update() if lock else query)
    return {'version':row.version if row else 0,'rules':Rules.model_validate(row.value if row else {}).model_dump(),
            'notes':['这是平台默认值。游戏可单独覆盖触发规则；apply_scope=all_games仅清除本次所改字段的游戏覆盖。',
                     '管理员/玩家说明不设固定字符上限，不会截断保存；执行预算和试玩数量可修改，0表示不限。模型上下文容量、请求包体和代码运行资源限制独立。',
                     '私聊无需指令：private_trigger=all；仍只接收绑定会话，且游戏须启用player_agent_enabled。',
                     '群聊自然语言：group_input=agent，group_trigger可选mention或all；游戏须启用玩家Agent，交互须单独group_enabled=true。群里不读取私聊记录、历史和裁决。',
                     '审批策略只由管理员在会话界面设置，不能通过平台规则工具或说明文字自行开启。']}

def update(db,version,rules,apply_scope='defaults'):
    # Serialize initial creation too, when there is no config row to lock yet.
    from sqlalchemy import text
    db.execute(text("SELECT pg_advisory_xact_lock(128066,1123)"))
    before=read(db,True)
    if version!=before['version']:raise DomainError('STATE_STALE','平台规则已经变化，请重新读取后提交',409)
    changes=RulesPatch.model_validate(rules).model_dump(exclude_none=True)
    if not changes:raise DomainError('INVALID_CONFIG','请指定需要修改的平台规则')
    value=Rules.model_validate({**before['rules'],**changes}).model_dump()
    row=db.get(PlatformConfig,'runtime_rules')
    if row:row.value=value;row.version+=1
    else:row=PlatformConfig(key='runtime_rules',value=value,version=1);db.add(row)
    affected=[]
    if apply_scope=='all_games':
        from .db import Game,GamePolicy
        fields=set(changes)&{'command_prefix','prefix_requires_space','private_trigger','group_trigger','group_input'}
        for game in db.scalars(select(Game).order_by(Game.id).with_for_update()):
            policy=db.get(GamePolicy,game.id)
            if policy and fields&set(policy.config):
                policy.config={k:v for k,v in policy.config.items() if k not in fields};policy.version+=1;affected.append(game.id)
    db.add(SystemLog(kind='platform.rules.updated',detail={'before':before,'after':{'version':version+1,'rules':value},'apply_scope':apply_scope,'overrides_reset':affected}))
    return {'version':version+1,'rules':value,'overrides_reset':affected}

def effective(db,game_id):
    from .db import GamePolicy
    defaults=read(db)['rules'];row=db.get(GamePolicy,game_id);overrides=row.config if row else {}
    for key in ('command_prefix','prefix_requires_space','private_trigger','group_trigger','group_input'):
        if key in overrides and overrides[key] is not None:defaults[key]=overrides[key]
    return defaults

def extract(text,rules,private,mentioned=False):
    mode=rules['private_trigger' if private else 'group_trigger']
    if mode=='disabled':return None
    prefix=rules['command_prefix'];separator=' ' if rules['prefix_requires_space'] else ''
    marker=prefix+separator
    if mode=='prefix':return text[len(marker):].strip() if text.startswith(marker) and text[len(marker):].strip() else None
    if mode=='mention' and not mentioned:return None
    if text.startswith(marker):text=text[len(marker):].strip()
    return text.strip() or None
