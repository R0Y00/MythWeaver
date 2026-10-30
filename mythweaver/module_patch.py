"""Small, version-bound module edits through the existing module.put transaction."""
from copy import deepcopy
from typing import Any,Literal
from pydantic import Field

from .definitions import DomainError
from .schemas import Input
from .harness_results import pointer


class Edit(Input):
    op:Literal['test','add','replace','remove']
    path:str=Field(min_length=1,description='模块manifest内的JSON Pointer；必须从已读取的真实模块定位')
    value:Any=None


class ModulePatch(Input):
    key:str=Field(pattern=r'^[a-z][a-z0-9_]{0,79}$')
    version:int=Field(ge=1,description='刚读取的definition_version，防止覆盖他人改动')
    edits:list[Edit]=Field(min_length=1,max_length=30)
    explanation:str=Field(min_length=1,max_length=8000)


def patched(manifest,edits):
    result=deepcopy(manifest);changes=[]
    for edit in edits:
        path=edit.path
        if not path.startswith('/'):raise DomainError('INVALID_TOOL_INPUT','修改路径必须是模块内JSON Pointer')
        parent_path,_,raw=path.rpartition('/')
        import re
        if re.search(r'~(?![01])',raw):raise DomainError('INVALID_TOOL_INPUT','修改路径转义无效')
        key=raw.replace('~1','/').replace('~0','~');parent=pointer(result,parent_path)
        if not isinstance(parent,(dict,list)):raise DomainError('INVALID_TOOL_INPUT','修改路径的父级必须是对象或数组')
        index=None
        if isinstance(parent,list):
            if key=='-' and edit.op=='add':index=len(parent)
            elif key.isdigit() and (len(key)==1 or not key.startswith('0')):index=int(key)
            else:raise DomainError('INVALID_TOOL_INPUT','数组路径须使用有效下标')
        exists=key in parent if isinstance(parent,dict) else 0<=index<len(parent)
        before=deepcopy(parent[key] if isinstance(parent,dict) else parent[index]) if exists else None
        if edit.op=='test':
            if not exists or before!=edit.value:raise DomainError('PATCH_CONFLICT','原值核对失败，请重新读取后修改',409)
            continue
        if edit.op in ('replace','remove') and not exists:raise DomainError('PATCH_CONFLICT','要替换或移除的路径不存在',409)
        if edit.op=='add' and isinstance(parent,dict) and exists:raise DomainError('PATCH_CONFLICT','新增字段已存在，请明确使用replace',409)
        if edit.op=='remove':
            if isinstance(parent,dict):del parent[key]
            else:parent.pop(index)
        elif isinstance(parent,dict):parent[key]=deepcopy(edit.value)
        elif edit.op=='add':
            if index>len(parent):raise DomainError('PATCH_CONFLICT','数组新增下标越界',409)
            parent.insert(index,deepcopy(edit.value))
        else:parent[index]=deepcopy(edit.value)
        changes.append({'path':path,'op':edit.op,'before':before,'after':None if edit.op=='remove' else edit.value})
    if not changes:raise DomainError('INVALID_TOOL_INPUT','仅有test条件，没有实际修改')
    return result,changes


def run(service,game_id,args,execution_id=None):
    query=ModulePatch.model_validate(args)
    with service.store.session.begin() as db:
        game=service.game(db,game_id,lock=bool(execution_id))
        if game.version!=query.version:raise DomainError('VERSION_CONFLICT','游戏定义已变化，请重新读取最新版本',409)
        manifest=game.definition.get('modules',{}).get(query.key)
        if manifest is None:raise DomainError('NOT_FOUND','局部修改只用于已有模块；新模块使用module.put',404)
        value,changes=patched(manifest,query.edits)
        payload={'key':query.key,'version':query.version,'manifest':value}
        if execution_id:
            applied=service.execute(db,game_id,'admin',f'harness:{execution_id}:patch','module.put',payload)
            return {'status':'applied','key':query.key,'changes':changes,'result':applied,'explanation':query.explanation}
    from .agent_reads import read
    checked=read(service,game_id,'validate_module',{'key':query.key,'manifest':value})
    return {'valid':checked['valid'],'key':query.key,'definition_version':query.version,'changes':changes,'notice':'仅预览和校验，尚未修改；实际执行仍需审批和同一版本。'}
