"""Scoped, paginated player data reads; never expose raw server records."""
import json
from pydantic import Field,ValidationError
from sqlalchemy import select
from .schemas import Input
from .db import Entity
from .definitions import DomainError


class PlayerRead(Input):
    type_key:str|None=None
    id:str|None=None
    fields:list[str]=Field(default_factory=list,max_length=20)
    after:str|None=None
    limit:int=Field(default=5,ge=1,le=20)
    offset:int=Field(default=0,ge=0)
    page_size:int=Field(default=10,ge=1,le=50)


def read_records(service,context,arguments):
    try:args=PlayerRead.model_validate(arguments)
    except ValidationError:raise DomainError('INVALID_READ','读取参数不符合工具格式') from None
    from .player import readable
    from .agent import fingerprint
    scope=context['_scope']
    with service.store.session() as db:
        game=service.game(db,scope['game_id'])
        if fingerprint(db,game)!=context['state_stamp']:raise DomainError('STATE_STALE','资料或授权已变化，请重新查询',409)
        from .policy import check_participant
        check_participant(db,game.id,scope['actor'])
        query=select(Entity).where(Entity.game_id==game.id).order_by(Entity.id)
        if args.type_key:query=query.where(Entity.type_key==args.type_key)
        if args.id:query=query.where(Entity.id==args.id)
        if args.after:query=query.where(Entity.id>args.after)
        # Authorization is applied before pagination; forbidden IDs reveal no data.
        rows=[]
        for row in db.scalars(query):
            if readable(game,row,scope['actor']):rows.append(row)
            if len(rows)>args.limit:break
        result=[]
        for row in rows[:args.limit]:
            spec=game.definition['types'][row.type_key];allowed=spec['player_access']['read_fields']
            data={};pages={}
            for key in args.fields or allowed:
                if key not in allowed or key not in row.data:continue
                value=row.data[key]
                if isinstance(value,(str,list)):
                    size=args.page_size if isinstance(value,list) else args.page_size*100
                    data[key]=value[args.offset:args.offset+size]
                    pages[key]={'total':len(value),'offset':args.offset,'next_offset':args.offset+size if args.offset+size<len(value) else None}
                elif isinstance(value,dict):
                    keys=list(value);data[key]={k:value[k] for k in keys[args.offset:args.offset+args.page_size]}
                    pages[key]={'total':len(keys),'offset':args.offset,'next_offset':args.offset+args.page_size if args.offset+args.page_size<len(keys) else None}
                else:data[key]=value
            result.append({'id':row.id,'type_key':row.type_key,'label':spec.get('label',row.type_key),'data':data,'field_pages':pages})
        value={'records':result,'next_after':rows[args.limit-1].id if len(rows)>args.limit else None}
        from .platform_rules import read
        budget=read(db)['rules']['player_data_context_chars']
        if budget and len(json.dumps(value,ensure_ascii=False))>budget:
            raise DomainError('READ_TOO_LARGE','本页资料较多，请指定字段并减小page_size或limit；规则说明没有被截断',409)
        return value
