"""Administrator cross-workspace requests; each call names exactly one target."""
from pydantic import Field
from sqlalchemy import select

from .db import Game
from .definitions import DomainError
from .schemas import Input


class WorkspaceList(Input):
    query:str=Field(default='',max_length=120)
    after:str|None=Field(default=None,max_length=36)
    limit:int=Field(default=30,ge=1,le=100)


class WorkspaceInspect(Input):
    game_id:str=Field(min_length=1,max_length=36)


class WorkspaceCall(WorkspaceInspect):
    tool:str=Field(min_length=1,max_length=100)
    arguments:dict


class WorkspaceCreate(Input):
    name:str=Field(min_length=1,max_length=120,pattern=r'.*\S.*')
    description:str=Field(default='',max_length=4000)


class WorkspaceDelete(WorkspaceInspect):
    version:int=Field(ge=1)
    confirmation:str=Field(min_length=1,max_length=120)


FORBIDDEN={'workspace_list','workspace_inspect','workspace_call','workspace_create','workspace_delete','finish_task','record_progress','ask_admin','platform_rules_read','platform_rules_update'}


def validate(harness,arguments):
    args=WorkspaceCall.model_validate(arguments)
    if args.tool in FORBIDDEN:raise DomainError('FORBIDDEN_TOOL','跨空间调用仅用于目标游戏的查询、修改和插件，不允许嵌套或修改任务执行器',403)
    with harness.store.session() as db:harness.service.game(db,args.game_id)
    from .agent_contracts import validate_advertised_call
    validate_advertised_call(harness.tools(args.game_id),args.tool,args.arguments)
    return args


def listing(harness,arguments):
    args=WorkspaceList.model_validate(arguments)
    with harness.store.session() as db:
        statement=select(Game).order_by(Game.id).limit(args.limit)
        if args.query:statement=statement.where(Game.name.icontains(args.query,autoescape=True))
        if args.after:statement=statement.where(Game.id>args.after)
        rows=list(db.scalars(statement))
        return {'games':[{'id':g.id,'name':g.name,'description':g.description,'version':g.version} for g in rows],
                'next_after':rows[-1].id if len(rows)==args.limit else None}


def inspect(harness,game_id):
    with harness.store.session() as db:
        game=harness.service.game(db,game_id)
        info={'id':game.id,'name':game.name,'description':game.description,'version':game.version}
    tools=harness.tools(game_id)
    return {'game':info,'tools':[{'name':t['function']['name'],'description':t['function']['description']} for t in tools if t['function']['name'] not in FORBIDDEN],
            'plugin_tools':[t for t in tools if t['function']['name'].startswith('ext_')],
            'usage':'通过workspace_call指定此game_id与目标tool/arguments。先按需调用inspect_runtime、inspect_capabilities读取真实配置和命令schema。'}
