"""Explicit opt-in examples; never installed into blank games."""
import json
from pathlib import Path

from fastapi import APIRouter

from .db import Game
from .definitions import DomainError,validate_definition
from .service import packed

EXAMPLES=('proposal-collaboration','city-investigation','private-player-agent')


def load(key):
    if key not in EXAMPLES:
        raise DomainError('NOT_FOUND','示例不存在',404)
    return json.loads((Path(__file__).resolve().parent.parent/'examples'/(key+'.json')).read_text(encoding='utf-8'))


def instantiate(service,key):
    example=load(key)
    definition=validate_definition(example['definition'])
    with service.store.session.begin() as db:
        game=Game(name=example['name'],description=example['description'])
        db.add(game)
        db.flush()
        service.event(db,game.id,'admin','game.created',{'name':game.name,'example':key})
        service.execute(db,game.id,'admin','example-definition','definition',{'version':1,'definition':definition})
        for index,seed in enumerate(example['seeds']):
            service.execute(db,game.id,'admin',f'example-seed:{index}','entity.create',seed)
        if example.get('policy'):
            service.execute(db,game.id,'admin','example-policy','policy.update',{'version':0,**example['policy']})
        return packed(game)


def router(service):
    routes=APIRouter(prefix='/api/examples')
    @routes.get('')
    def list_examples():
        return [{'id':key,**{k:v for k,v in load(key).items() if k in ('name','description','definition')}} for key in EXAMPLES]
    @routes.post('/{key}/instantiate')
    def create(key:str):
        return instantiate(service,key)
    return routes
