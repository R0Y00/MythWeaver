"""Small declarative state operations, independent of any game genre."""
from copy import deepcopy
import math
import re

from .db import Entity, EntityRevision
from .definitions import DomainError,schema_validate
from .conditions import required_path


def finite_number(value):
    if type(value) not in (int,float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def check_effects(definition,spec):
    effects=spec.get("effects",[])
    if not isinstance(effects,list) or len(effects)>10:
        raise DomainError("INVALID_RULE","每次交互最多 10 个数据变更")
    for index,effect in enumerate(effects):
        if not isinstance(effect,dict) or set(effect)-{"type_key","id_from","field","operation","value","value_from","expression","plugin_key","tool"}:
            unknown=','.join(sorted(str(k) for k in set(effect)-{"type_key","id_from","field","operation","value","value_from","expression","plugin_key","tool"})) if isinstance(effect,dict) else '非对象'
            raise DomainError("INVALID_RULE",f"effects[{index}]包含不支持字段：{unknown}。条件放交互condition；按输入分支计算用compute.expression，不能给单项effect添加condition。")
        if effect.get("operation") not in ("set","increment","append","compute","plugin"):
            raise DomainError("INVALID_RULE","变更操作支持 set、increment、append、compute、plugin")
        if any(not isinstance(effect.get(k),str) or not effect[k] for k in ("type_key","id_from","field")):
            raise DomainError("INVALID_RULE","变更需要目标类型、对象 ID 输入字段和数据字段")
        if effect['operation']=='plugin':
            if any(not isinstance(effect.get(k),str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,23}',effect[k]) for k in ('plugin_key','tool')):
                raise DomainError('INVALID_RULE','插件效果需要plugin_key和tool标识')
        elif 'plugin_key' in effect or 'tool' in effect:
            raise DomainError('INVALID_RULE','仅插件效果可指定插件工具')
        if effect['operation'] in ('compute','plugin'):
            if 'expression' not in effect or 'value' in effect or 'value_from' in effect:
                raise DomainError('INVALID_RULE','compute必须仅指定expression作为值来源')
            from .functions import check_expression
            check_expression(effect['expression'],definition.get('functions',{}))
        elif 'expression' in effect or ("value" in effect)==("value_from" in effect):
            raise DomainError("INVALID_RULE","请选择常量值或输入字段值之一")
        if "value_from" in effect and not isinstance(effect["value_from"],str):
            raise DomainError("INVALID_RULE","输入字段标识无效")
        if effect["type_key"] not in definition.get("types",{}):
            raise DomainError("INVALID_RULE","变更目标类型尚未定义")


def targets(db,game,spec,data,actor='admin'):
    result={}
    for effect in spec.get("effects",[]):
        entity_id=required_path(data,effect["id_from"])
        if not isinstance(entity_id,str):
            raise DomainError("INVALID_TARGET","交互输入必须包含目标记录的完整 ID")
        entity=db.get(Entity,entity_id)
        if not entity or entity.game_id!=game.id or entity.type_key!=effect["type_key"] or entity.archived:
            raise DomainError("INVALID_TARGET","目标记录不存在、类型不匹配或已归档")
        type_spec=game.definition.get("types",{}).get(entity.type_key)
        if not type_spec or not type_spec.get("enabled",True):
            raise DomainError("TYPE_UNAVAILABLE","目标类型已停用")
        result[entity.id]=entity.version
    from .extensions import dependencies
    result.update(dependencies(db,game,spec,actor))
    return result


def apply_effects(service,db,game,actor,spec,data,dependencies=None,run_id=None,extension_actor=None):
    from .workflows import ensure_unlocked,invalidate_dependents
    extension_actor=extension_actor or actor
    current=targets(db,game,spec,data,extension_actor)
    from .functions import Runtime
    runtime=Runtime(game.definition)
    if dependencies is not None and current!=dependencies:
        raise DomainError("STATE_STALE","目标数据已变化，请按当前状态重新提交",409)
    for effect in spec.get("effects",[]):
        entity=db.get(Entity,required_path(data,effect["id_from"]))
        from .player import authorize_target
        authorize_target(db,game,entity,actor)
        access=game.definition['types'][entity.type_key].get('player_access',{})
        if actor!='admin' and access.get('owner_field')==effect['field']:
            raise DomainError('TARGET_DENIED','玩家行动不能修改记录归属',403)
        ensure_unlocked(db,game.id,entity.id,actor,run_id)
        before={"data":deepcopy(entity.data),"version":entity.version}
        updated=deepcopy(entity.data)
        if effect['operation'] in ('compute','plugin'):
            value=runtime.evaluate(effect['expression'],{'input':data,'current':updated,'actor':actor})
            if effect['operation']=='plugin':
                if not isinstance(value,dict):raise DomainError('INVALID_RULE_INPUT','插件输入必须为对象')
                value=service.plugins.invoke(game.id,effect['plugin_key'],effect['tool'],value,actor=actor,db=db)
        elif "value_from" in effect:
            value=required_path(data,effect["value_from"])
        else:
            value=deepcopy(effect["value"])
        field=effect["field"]
        if effect["operation"] in ('set','compute','plugin'):
            updated[field]=value
        elif effect["operation"]=="increment":
            old=updated.get(field,0)
            if not finite_number(old) or not finite_number(value):
                raise DomainError("INVALID_RULE_INPUT","数值增加只接受有限数值")
            updated[field]=old+value
            if not finite_number(updated[field]):
                raise DomainError("INVALID_RULE_INPUT","计算结果超出范围")
        else:
            if not isinstance(updated.get(field,[]),list):
                raise DomainError("INVALID_RULE_INPUT","追加操作的目标字段必须是列表")
            updated[field]=[*updated.get(field,[]),value]
        schema_validate(game.definition["types"][entity.type_key].get("schema",{"type":"object"}),updated)
        from .references import validate_references
        validate_references(db,game,game.definition['types'][entity.type_key].get('schema',{}),updated)
        entity.data=updated
        entity.version+=1
        invalidate_dependents(service,db,game.id,entity.id,run_id,actor)
        revision=db.get(EntityRevision,entity.id)
        if revision:
            revision.last_version=entity.version
        else:
            db.add(EntityRevision(id=entity.id,last_version=entity.version))
        service.event(db,game.id,actor,"rule.effect",{"id":entity.id,"operation":effect["operation"],"field":field,"before":before,"after":{"data":updated,"version":entity.version}})
        db.flush()
    from .extensions import apply
    return apply(service,db,game,spec,data,extension_actor,run_id)
