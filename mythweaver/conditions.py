"""Bounded declarative conditions. Never execute expressions or resolve external data."""
from copy import deepcopy

from .db import Entity
from .definitions import DomainError

MISSING = object()


def path_value(data, path, missing=MISSING):
    # Exact keys keep older interaction fields containing a dot usable.
    if isinstance(data,dict) and path in data:
        return deepcopy(data[path])
    current=data
    for part in path.split('.'):
        if not isinstance(current,dict) or part not in current:
            return missing
        current=current[part]
    return deepcopy(current)


def required_path(data,path):
    value=path_value(data,path)
    if value is MISSING:
        raise DomainError("INVALID_RULE_INPUT","缺少规则或流程需要的字段")
    return value


def validate_operand(value,definition):
    if not isinstance(value,dict) or len(value)!=1:
        raise DomainError("INVALID_CONDITION","条件值必须指定 path、value 或 entity 之一")
    if 'value' in value:
        return
    if 'path' in value and isinstance(value['path'],str) and 0<len(value['path'])<=240:
        return
    target=value.get('entity')
    if (isinstance(target,dict) and set(target)=={'id_from','type_key','field'}
        and all(isinstance(v,str) and v for v in target.values())
        and target['type_key'] in definition.get('types',{})):
        return
    raise DomainError("INVALID_CONDITION","条件字段路径或对象来源无效")


def validate_condition(condition,definition,depth=0):
    if depth>6 or not isinstance(condition,dict):
        raise DomainError("INVALID_CONDITION","条件结构无效或超过 6 层")
    for group in ('all','any'):
        if group in condition:
            children=condition[group]
            if set(condition)!={group} or not isinstance(children,list) or not 1<=len(children)<=10:
                raise DomainError("INVALID_CONDITION","条件组合需要 1–10 项")
            for child in children:
                validate_condition(child,definition,depth+1)
            return
    if 'not' in condition:
        if set(condition)!={'not'}:
            raise DomainError("INVALID_CONDITION","取反条件包含未知字段")
        validate_condition(condition['not'],definition,depth+1)
        return
    op=condition.get('op')
    if op not in ('eq','ne','gt','gte','lt','lte','in','contains','exists') or set(condition)!=({'left','op'} if op=='exists' else {'left','op','right'}):
        raise DomainError("INVALID_CONDITION","条件操作或字段无效")
    validate_operand(condition['left'],definition)
    if op!='exists':
        validate_operand(condition['right'],definition)


def operand(db,game,source,context,dependencies):
    if 'value' in source:
        return source['value']
    if 'path' in source:
        return path_value(context,source['path'])
    target=source['entity']
    entity_id=required_path(context,target['id_from'])
    row=db.get(Entity,entity_id) if isinstance(entity_id,str) else None
    if not row or row.game_id!=game.id or row.type_key!=target['type_key'] or row.archived:
        raise DomainError("INVALID_TARGET","条件目标不存在、类型不匹配或已归档")
    if not game.definition['types'][row.type_key].get('enabled',True):
        raise DomainError("TYPE_UNAVAILABLE","条件目标类型已停用")
    dependencies[row.id]=row.version
    return path_value(row.data,target['field'])


def evaluate(db,game,condition,context,dependencies):
    # Evaluate every member so the full read set is retained before a manual wait.
    if 'all' in condition:
        return all([evaluate(db,game,c,context,dependencies) for c in condition['all']])
    if 'any' in condition:
        return any([evaluate(db,game,c,context,dependencies) for c in condition['any']])
    if 'not' in condition:
        return not evaluate(db,game,condition['not'],context,dependencies)
    left=operand(db,game,condition['left'],context,dependencies)
    op=condition['op']
    if op=='exists':
        return left is not MISSING
    right=operand(db,game,condition['right'],context,dependencies)
    if left is MISSING or right is MISSING:
        raise DomainError("INVALID_RULE_INPUT","条件引用的字段未提供")
    if op in ('eq','ne'):
        equal=left==right and (type(left)==type(right) or type(left) in (int,float) and type(right) in (int,float))
        return equal if op=='eq' else not equal
    if op in ('gt','gte','lt','lte'):
        from .rules import finite_number
        if not finite_number(left) or not finite_number(right):
            raise DomainError("INVALID_RULE_INPUT","大小比较只接受有限数值")
        return {'gt':left>right,'gte':left>=right,'lt':left<right,'lte':left<=right}[op]
    container,item=(right,left) if op=='in' else (left,right)
    if not isinstance(container,(str,list)) or isinstance(container,str) and not isinstance(item,str):
        raise DomainError("INVALID_RULE_INPUT","包含条件需要列表或文本")
    return item in container
