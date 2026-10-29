from uuid import uuid4
import pytest

from mythweaver.definitions import DomainError,validate_definition


def setup(service,game):
    d={'types':{'person':{'schema':{'type':'object','properties':{'name':{'type':'string'}}}},
                'lead':{'schema':{'type':'object','properties':{'witness':{'type':'string','x-reference':{'type_key':'person'}}}}}},'interactions':{}}
    service.command(game['id'],'definition',{'version':1,'definition':d},uuid4().hex)
    person=service.command(game['id'],'entity.create',{'type_key':'person','data':{'name':'线人'}},uuid4().hex)
    return d,person


def create(service,game,target):
    return service.command(game['id'],'entity.create',{'type_key':'lead','data':{'witness':target}},uuid4().hex)


def test_typed_reference_accepts_current_game_target(service,game):
    _,person=setup(service,game)
    assert create(service,game,person['id'])['data']['witness']==person['id']


def test_reference_rejects_cross_game_wrong_type_missing_and_archived(service,game):
    _,person=setup(service,game)
    other=service.create_game('外部','')
    _,other_person=setup(service,other)
    lead=create(service,game,person['id'])
    for target in (other_person['id'],lead['id'],uuid4().hex):
        with pytest.raises(DomainError) as error:create(service,game,target)
        assert error.value.code=='INVALID_REFERENCE'
    with pytest.raises(DomainError) as error:
        service.command(game['id'],'entity.update',{'id':person['id'],'type_key':'person','data':person['data'],'version':1,'archived':True},uuid4().hex)
    assert error.value.code=='REFERENCE_IN_USE'
    service.command(game['id'],'entity.update',{'id':lead['id'],'type_key':'lead','data':lead['data'],'version':1,'archived':True},uuid4().hex)
    service.command(game['id'],'entity.update',{'id':person['id'],'type_key':'person','data':person['data'],'version':1,'archived':True},uuid4().hex)
    with pytest.raises(DomainError):create(service,game,person['id'])


def test_reference_type_cannot_be_deleted_from_definition(service,game):
    d,_=setup(service,game)
    del d['types']['person']
    with pytest.raises(DomainError):validate_definition(d)


def test_rule_cannot_write_cross_game_reference(service,game):
    d,person=setup(service,game)
    lead=create(service,game,person['id'])
    other=service.create_game('外部','')
    _,foreign=setup(service,other)
    d['interactions']['assign']={'mode':'rule','effects':[{'type_key':'lead','id_from':'target','field':'witness','operation':'set','value_from':'witness'}]}
    service.command(game['id'],'definition',{'version':2,'definition':d},uuid4().hex)
    with pytest.raises(DomainError):
        service.command(game['id'],'submit',{'interaction':'assign','data':{'target':lead['id'],'witness':foreign['id']}},uuid4().hex,actor='qq:7')
    assert next(e for e in service.snapshot(game['id'])['entities'] if e['id']==lead['id'])['data']['witness']==person['id']
