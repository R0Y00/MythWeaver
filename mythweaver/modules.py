"""Runtime-authored capability packages, materialized into the game definition."""
from copy import deepcopy
import re

from .definitions import DomainError

SECTIONS=('types','interactions','workflows','functions')


def error(message):
    raise DomainError('INVALID_MODULE',message)


def materialize(value):
    result=deepcopy(value)
    modules=result.get('modules',{})
    if not isinstance(modules,dict) or len(modules)>30:
        error('模块必须为对象，最多30个')
    owned={section:{} for section in SECTIONS}
    for key,manifest in modules.items():
        if (not isinstance(key,str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,79}',key)
            or not isinstance(manifest,dict) or set(manifest)-{'label','description','revision','enabled','requires',*SECTIONS}
            or not isinstance(manifest.get('label',key),str) or len(manifest.get('label',key))>120
            or not isinstance(manifest.get('description',''),str) or len(manifest.get('description',''))>4000
            or not isinstance(manifest.get('enabled',True),bool)
            or type(manifest.get('revision',1)) is not int or manifest.get('revision',1)<1):
            error('模块名称、版本或配置无效')
        requires=manifest.get('requires',[])
        if not isinstance(requires,list) or len(requires)>30 or any(not isinstance(r,str) or r not in modules or not isinstance(modules[r],dict) or r==key for r in requires):
            error('模块依赖不存在或包含自身')
        if manifest.get('enabled',True) and any(not modules[r].get('enabled',True) for r in requires):
            error('启用模块依赖的其他模块不能停用')
        for section in SECTIONS:
            members=manifest.get(section,{})
            if not isinstance(members,dict) or len(members)>50:
                error('每个模块的各类定义最多50项')
            target=result.setdefault(section,{})
            if not isinstance(target,dict):
                error('模块目标定义必须为对象')
            for member,spec in members.items():
                if member in owned[section] or not isinstance(spec,dict):
                    error('模块成员重复或定义无效')
                effective=deepcopy(spec)
                if not manifest.get('enabled',True):
                    effective['enabled']=False
                # Allow round-tripping a materialized definition, never silently override it.
                if member in target and target[member]!=effective:
                    error('模块成员与其他定义冲突，请通过module.put修改模块')
                target[member]=effective
                owned[section][member]=key
    visiting,visited=set(),set()
    def visit(key):
        if key in visiting:
            error('模块依赖不能形成循环')
        if key in visited:
            return
        visiting.add(key)
        for dependency in modules[key].get('requires',[]):
            visit(dependency)
        visiting.remove(key)
        visited.add(key)
    for key in modules:
        visit(key)
    return result


def change(service,db,game,kind,p):
    if p['version']!=game.version:
        raise DomainError('STATE_STALE','模块定义已变化，请重新读取',409)
    definition=deepcopy(game.definition)
    modules=definition.setdefault('modules',{})
    previous=modules.get(p['key'])
    if kind!='module.put' and not previous:
        raise DomainError('NOT_FOUND','模块不存在',404)
    if previous:
        for section in SECTIONS:
            for member in previous.get(section,{}):
                definition.get(section,{}).pop(member,None)
    if kind=='module.remove':
        del modules[p['key']]
    else:
        manifest=deepcopy(p['manifest'] if kind=='module.put' else previous)
        if kind=='module.state':
            manifest['enabled']=p['enabled']
        manifest['revision']=(previous.get('revision',1) if previous else 0)+1
        # New members may not claim base or other modules' existing definitions.
        for section in SECTIONS:
            members=manifest.get(section,{})
            if not isinstance(members,dict):
                error('模块定义必须为对象')
            if set(members)&set(definition.get(section,{})):
                error('模块成员名称已被其他定义使用')
        modules[p['key']]=manifest
    from .definitions import validate_definition
    definition=validate_definition(definition)
    from .checkpoints import checkpoint_create_in
    safety=checkpoint_create_in(service,db,game,'模块变更前 · '+p['key'][:60])
    # Reuse record/schema validation, stale-flow handling and event storage.
    result=service.apply(db,game,'admin','definition',{'version':game.version,'definition':definition})
    service.event(db,game.id,'admin',kind,{'key':p['key'],'before':previous,'after':modules.get(p['key']),
                                         'safety_checkpoint':safety['id']})
    return {**result,'module':modules.get(p['key']),'key':p['key'],'safety_checkpoint':safety['id']}
