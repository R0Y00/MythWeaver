from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from mythweaver.checkpoints import create_checkpoint,restore_checkpoint
from mythweaver.definitions import DomainError,validate_definition


def definitions(mode='rule',effects=None):
    return {'types':{'custom':{'schema':{'type':'object','properties':{'value':{'type':'number'},'entries':{'type':'array','items':{'type':'string'}}},'additionalProperties':False}}},
            'interactions':{'change':{'mode':mode,'schema':{'type':'object','properties':{'target':{'type':'string'},'delta':{'type':'number'}},'required':['target','delta']},
                                      'effects':effects or [{'type_key':'custom','id_from':'target','field':'value','operation':'increment','value_from':'delta'}]}}}


def arrange(service,game,mode='rule',effects=None):
    service.command(game['id'],'definition',{'version':1,'definition':definitions(mode,effects)},uuid4().hex)
    return service.command(game['id'],'entity.create',{'type_key':'custom','data':{'value':5,'entries':[]}},uuid4().hex)


def submit(service,game,record,delta=3,key=None):
    return service.command(game['id'],'submit',{'interaction':'change','data':{'target':record['id'],'delta':delta}},key or uuid4().hex,actor='qq:7')


def test_automatic_rule_uses_custom_fields_without_game_assumptions(service,game):
    record=arrange(service,game,effects=[{'type_key':'custom','id_from':'target','field':'value','operation':'increment','value_from':'delta'},
                                       {'type_key':'custom','id_from':'target','field':'entries','operation':'append','value':'完成'}])
    assert submit(service,game,record)['status']=='applied'
    snapshot=service.snapshot(game['id'])
    assert snapshot['entities'][0]['data']=={'value':8,'entries':['完成']}
    assert len([e for e in snapshot['events'] if e['kind']=='rule.effect'])==2


def test_manual_rule_has_no_effect_until_decided(service,game):
    record=arrange(service,game,'manual')
    pending=submit(service,game,record)
    assert service.snapshot(game['id'])['entities'][0]['data']['value']==5
    service.command(game['id'],'decide',{'id':pending['id'],'decision':'applied','result':'批准自定义变更'},uuid4().hex)
    assert service.snapshot(game['id'])['entities'][0]['data']['value']==8


def test_rejected_rule_never_applies(service,game):
    record=arrange(service,game,'manual')
    pending=submit(service,game,record)
    service.command(game['id'],'decide',{'id':pending['id'],'decision':'rejected','result':'本次不改变数据'},uuid4().hex)
    assert service.snapshot(game['id'])['entities'][0]['data']['value']==5


def test_pending_rule_requires_unchanged_target_version(service,game):
    record=arrange(service,game,'manual')
    pending=submit(service,game,record)
    service.command(game['id'],'entity.update',{**{k:record[k] for k in ('id','type_key','visibility','version')},'data':{'value':9,'entries':[]}},uuid4().hex)
    with pytest.raises(DomainError) as error:
        service.command(game['id'],'decide',{'id':pending['id'],'decision':'applied','result':'旧方案'},uuid4().hex)
    assert error.value.code=='STATE_STALE'
    assert service.snapshot(game['id'])['entities'][0]['data']['value']==9


def test_effect_failure_rolls_back_all_state_and_submission(service,game):
    record=arrange(service,game,effects=[{'type_key':'custom','id_from':'target','field':'value','operation':'increment','value':10},
                                       {'type_key':'custom','id_from':'target','field':'value','operation':'set','value':'invalid string'}])
    before=service.snapshot(game['id'])
    with pytest.raises(DomainError):
        submit(service,game,record)
    after=service.snapshot(game['id'])
    assert after['entities']==before['entities'] and after['submissions']==[]
    assert len(after['events'])==len(before['events'])


def test_concurrent_repeated_rule_changes_record_only_once(service,game):
    record=arrange(service,game)
    key=uuid4().hex
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows=list(pool.map(lambda _:submit(service,game,record,key=key),range(10)))
    assert len({r['id'] for r in rows})==1
    assert service.snapshot(game['id'])['entities'][0]['data']['value']==8


def test_rule_cannot_address_another_game(service,game):
    record=arrange(service,game)
    other=service.create_game('其他空间','')
    other_record=arrange(service,other)
    with pytest.raises(DomainError) as error:
        submit(service,game,other_record)
    assert error.value.code=='INVALID_TARGET'
    assert service.snapshot(other['id'])['entities'][0]['data']['value']==5


def test_rule_rejects_unbounded_integer_without_partial_state(service,game):
    record=arrange(service,game)
    with pytest.raises(DomainError) as error:
        submit(service,game,record,delta=10**400)
    assert error.value.code=='INVALID_RULE_INPUT'
    assert service.snapshot(game['id'])['entities'][0]['data']['value']==5


@pytest.mark.parametrize('patch',[{'operation':'execute_python'},{'type_key':'missing'},{'value':1,'value_from':'delta'},{'id_from':1}])
def test_invalid_rule_definitions_are_rejected(patch):
    effect={'type_key':'custom','id_from':'target','field':'value','operation':'set','value':1}
    effect.update(patch)
    with pytest.raises(DomainError):
        validate_definition(definitions(effects=[effect]))


def test_restore_does_not_revalidate_old_dependent_approval_silently(service,game):
    record=arrange(service,game,'manual')
    pending=submit(service,game,record)
    checkpoint=create_checkpoint(service,game['id'],'有待定数据变更')
    restore_checkpoint(service,game['id'],checkpoint['id'],2,uuid4().hex)
    snapshot=service.snapshot(game['id'])
    assert snapshot['submissions'][0]['status']=='stale'
    assert snapshot['entities'][0]['data']['value']==5

