"""Administrator-only bounded read tools for the planning loop."""
from pydantic import Field, ValidationError
from sqlalchemy import select
from typing import Literal

from .db import Entity, Event, FlowRun, Participant,Channel,Checkpoint,RetiredEntity,Blueprint,Submission,PlayerTurn
from .definitions import DomainError
from .schemas import Input
from .service import packed


class RecordQuery(Input):
    type_key: str | None = Field(default=None,max_length=80)
    ids: list[str] = Field(default_factory=list,max_length=20)
    limit: int = Field(default=10,ge=1,le=20)
    after: str | None = Field(default=None,max_length=36)


class EventQuery(Input):
    kind: str | None = Field(default=None,max_length=80)
    before: int | None = Field(default=None,ge=1)
    limit: int = Field(default=10,ge=1,le=20)


class FlowQuery(Input):
    id: str = Field(min_length=1,max_length=36)


class CapabilitiesQuery(Input):
    kinds: list[str] = Field(default_factory=list,max_length=8)


class RuntimeQuery(Input):
    function_keys: list[str] = Field(default_factory=list,max_length=8)
    module_keys: list[str] = Field(default_factory=list,max_length=8)
    sections: list[Literal['types','interactions','workflows']]=Field(default_factory=list,max_length=3)


class ControlQuery(Input):
    section: Literal['policy','participants','channels','checkpoints','retired','blueprints','submissions','player_turns']
    limit: int = Field(default=10,ge=1,le=20)
    after: str | None = Field(default=None,max_length=36)
    id: str | None = Field(default=None,max_length=36)
    instruction_offset:int=Field(default=0,ge=0)
    instruction_limit:int=Field(default=8000,ge=1,le=10000)


class ModuleValidation(Input):
    key:str=Field(pattern=r'^[a-z][a-z0-9_]{0,79}$')
    manifest:dict


MODELS={'query_records':RecordQuery,'query_events':EventQuery,'inspect_flow':FlowQuery}
MODELS.update(inspect_capabilities=CapabilitiesQuery,list_controls=ControlQuery,inspect_runtime=RuntimeQuery)
MODELS['validate_module']=ModuleValidation
DESCRIPTIONS={'query_records':'查询当前游戏记录，可按类型、完整ID或游标读取，管理员上下文。','query_events':'查询当前游戏审计历史，返回最近事件，可按类型和游标筛选。','inspect_flow':'读取当前游戏某个流程实例的状态、上下文和历史。'}
DESCRIPTIONS.update(inspect_capabilities='查询全部已实现游戏命令，传入 kinds 获取至多8个命令的完整参数schema。',
                    list_controls='查询游戏设置/版本、参与者、群绑定、检查点、退役记录、参与事项、玩家对话(player_turns)或模板；可指定ID或分页。',
                    inspect_runtime='读取动态模块/函数语言的真实语法及限制，可按标识读取当前游戏至多8个模块及函数完整源码。')
DESCRIPTIONS['validate_module']='在当前定义上检验新增/替换模块的完整manifest，返回合法性与版本；不写入、不安装、不执行代码。module.put前先调用。'


def tools():
    from .command_catalog import COMMANDS
    result=[{'type':'function','function':{'name':name,'description':DESCRIPTIONS[name],'parameters':model.model_json_schema()}} for name,model in MODELS.items()]
    for tool in result:
        if tool['function']['name']=='inspect_capabilities':
            tool['function']['parameters']['properties']['kinds']['items']['enum']=list(COMMANDS)
    return result


def read(service,game_id,name,arguments):
    if name not in MODELS:
        raise DomainError('FORBIDDEN_TOOL','未授权读取工具',403)
    try:
        query=MODELS[name].model_validate(arguments)
    except ValidationError:
        raise DomainError('INVALID_TOOL_INPUT','读取参数未通过校验') from None
    with service.store.session() as db:
        game=service.game(db,game_id)
        if name=='validate_module':
            from copy import deepcopy
            from .definitions import validate_definition
            definition=deepcopy(game.definition)
            previous=definition.setdefault('modules',{}).get(query.key,{})
            for section in ('types','interactions','workflows','functions'):
                for key in previous.get(section,{}):definition.get(section,{}).pop(key,None)
                members=query.manifest.get(section,{})
                if not isinstance(members,dict):raise DomainError('INVALID_MODULE','模块各类定义必须为对象')
                if set(members)&set(definition.get(section,{})):raise DomainError('INVALID_MODULE','模块成员名称已被其他定义使用')
            definition['modules'][query.key]=query.manifest
            validate_definition(definition)
            return {'valid':True,'definition_version':game.version,'key':query.key,
                    'members':{s:list(query.manifest.get(s,{})) for s in ('types','interactions','workflows','functions')},
                    'notice':'仅检查定义结构；记录、权限和状态冲突在实际提交时继续校验。'}
        if name=='inspect_runtime':
            from .functions import capabilities
            from .runtime_contract import authoring_contract
            return {**capabilities(),
                    'authoring_contract':authoring_contract(),
                    'definition_version':game.version,'definition':{k:game.definition.get(k,{}) for k in query.sections},
                    'definition_index':{k:list(game.definition.get(k,{})) for k in ('types','interactions','workflows')},
                    'modules':{k:v if k in query.module_keys else {x:v.get(x) for x in ('label','revision','enabled','requires')} for k,v in game.definition.get('modules',{}).items()},
                    'functions':{k:v if k in query.function_keys else {x:v.get(x) for x in ('label','enabled')} for k,v in game.definition.get('functions',{}).items()}}
        if name=='inspect_capabilities':
            from .command_catalog import catalog,COMMANDS
            if any(k not in COMMANDS for k in query.kinds):
                raise DomainError('FORBIDDEN_TOOL','查询包含不存在的游戏命令。请传空kinds查询实际目录；插件编写、安装和试玩属于执行任务工具，不是游戏命令',403)
            return [c if query.kinds else {k:c[k] for k in ('kind','description')} for c in catalog() if not query.kinds or c['kind'] in query.kinds]
        if name=='list_controls':
            if query.section=='policy':
                from .policy import policy_for
                from .instructions import page
                policy,version=policy_for(db,game_id)
                return {**page(policy,query.instruction_offset,query.instruction_limit),'version':version}
            model={'participants':Participant,'channels':Channel,'checkpoints':Checkpoint,'retired':RetiredEntity,'blueprints':Blueprint,'submissions':Submission,'player_turns':PlayerTurn}[query.section]
            statement=select(model)
            if model!=Blueprint:
                statement=statement.where(model.game_id==game_id)
            field=model.group_id if model==Channel else model.id
            if query.after:
                statement=statement.where(field>query.after)
            if query.id:
                statement=statement.where(field==query.id)
            result=[]
            for row in db.scalars(statement.order_by(field).limit(query.limit)):
                item=packed(row)
                if model==Checkpoint:
                    snapshot=item.pop('snapshot')
                    item.update(entities=len(snapshot['entities']),submissions=len(snapshot['submissions']),definition=snapshot['definition'])
                result.append(item)
            return result
        if name=='query_records':
            statement=select(Entity).where(Entity.game_id==game_id)
            if query.type_key:
                statement=statement.where(Entity.type_key==query.type_key)
            if query.ids:
                statement=statement.where(Entity.id.in_(query.ids))
            if query.after:
                statement=statement.where(Entity.id>query.after)
            return [packed(e) for e in db.scalars(statement.order_by(Entity.id).limit(query.limit))]
        if name=='query_events':
            statement=select(Event).where(Event.game_id==game_id)
            if query.kind:
                statement=statement.where(Event.kind==query.kind)
            if query.before:
                statement=statement.where(Event.seq<query.before)
            return [packed(e) for e in db.scalars(statement.order_by(Event.seq.desc()).limit(query.limit))]
        run=db.get(FlowRun,query.id)
        if not run or run.game_id!=game_id:
            raise DomainError('NOT_FOUND','流程实例不存在',404)
        return packed(run)
