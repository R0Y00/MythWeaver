"""Typed references are validated server-side, including imported data and rule writes."""
from .db import Entity
from .definitions import DomainError


def check_reference_schema(schema,definition):
    if not isinstance(schema,dict):
        return
    if 'x-reference' in schema:
        ref=schema['x-reference']
        if (schema.get('type')!='string' or not isinstance(ref,dict) or set(ref)-{'type_key','allow_archived'}
            or not isinstance(ref.get('type_key'),str) or ref['type_key'] not in definition.get('types',{})
            or not isinstance(ref.get('allow_archived',False),bool)):
            raise DomainError('INVALID_REFERENCE','对象引用需指定已定义类型，且字段类型为文本')
    for field in schema.get('properties',{}).values():
        check_reference_schema(field,definition)
    if isinstance(schema.get('items'),dict):
        check_reference_schema(schema['items'],definition)
    for keyword in ('anyOf','oneOf','allOf'):
        for alternative in schema.get(keyword,[]):
            check_reference_schema(alternative,definition)


def validate_references(db,game,schema,data,historical=False):
    if not isinstance(schema,dict):
        return
    if 'x-reference' in schema:
        ref=schema['x-reference']
        row=db.get(Entity,data) if isinstance(data,str) else None
        if not row or row.game_id!=game.id or row.type_key!=ref['type_key'] or row.archived and not historical and not ref.get('allow_archived',False):
            raise DomainError('INVALID_REFERENCE','引用对象不存在、类型不匹配、跨游戏或已归档')
    if isinstance(data,dict):
        for key,field in schema.get('properties',{}).items():
            if key in data:
                validate_references(db,game,field,data[key],historical)
    if isinstance(data,list) and isinstance(schema.get('items'),dict):
        for value in data:
            validate_references(db,game,schema['items'],value,historical)
    # For alternatives apply reference checks only to schemas that match this value.
    from jsonschema import Draft202012Validator
    for keyword in ('anyOf','oneOf','allOf'):
        for alternative in schema.get(keyword,[]):
            if Draft202012Validator(alternative).is_valid(data):
                validate_references(db,game,alternative,data,historical)


def ensure_archivable(db,game,target):
    from sqlalchemy import select
    def uses(schema,value):
        if not isinstance(schema,dict):
            return False
        if schema.get('x-reference') and value==target.id and not schema['x-reference'].get('allow_archived',False):
            return True
        if isinstance(value,dict) and any(k in value and uses(s,value[k]) for k,s in schema.get('properties',{}).items()):
            return True
        if isinstance(value,list) and isinstance(schema.get('items'),dict) and any(uses(schema['items'],v) for v in value):
            return True
        from jsonschema import Draft202012Validator
        return any(Draft202012Validator(s).is_valid(value) and uses(s,value) for keyword in ('anyOf','oneOf','allOf') for s in schema.get(keyword,[]))
    for entity in db.scalars(select(Entity).where(Entity.game_id==game.id,Entity.archived==False,Entity.id!=target.id)):
        schema=game.definition['types'].get(entity.type_key,{}).get('schema',{})
        if uses(schema,entity.data):
            raise DomainError('REFERENCE_IN_USE','仍有有效记录引用此对象，请先调整引用或归档关联记录',409)
