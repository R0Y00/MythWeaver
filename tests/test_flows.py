from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlalchemy import select

from mythweaver.agent import Agent
from mythweaver.checkpoints import create_checkpoint,restore_checkpoint
from mythweaver.config import Settings
from mythweaver.db import FlowRun,Channel,Outbox
from mythweaver.definitions import DomainError,validate_definition


def command(service,game,kind,payload,actor='admin',key=None):
    return service.command(game['id'],kind,payload,key or uuid4().hex,actor)


def definition():
    effect=lambda field,**value: {'type_key':'custom','id_from':'input.target','field':field,'operation':'set',**value}
    return {'types':{'custom':{'schema':{'type':'object','properties':{'state':{'type':'string'},'value':{'type':'number'}}}}},
            'interactions':{'begin':{'mode':'flow','workflow':'route','schema':{'type':'object','properties':{'target':{'type':'string'},'amount':{'type':'number'}},'required':['target','amount']}},
                            'change':{'mode':'rule','schema':{'type':'object'},'effects':[{'type_key':'custom','id_from':'target','field':'value','operation':'increment','value':1}]}},
            'workflows':{'route':{'label':'自定义流程','schema':{'type':'object','required':['target','amount']},'start':'initial','steps':{
                'initial':{'kind':'effects','effects':[effect('state',value='started')],'next':'choose'},
                'choose':{'kind':'branch','condition':{'left':{'path':'input.amount'},'op':'gt','right':{'value':10}},'yes':'review','no':'direct'},
                'direct':{'kind':'effects','effects':[effect('value',value_from='input.amount')],'next':'finish'},
                'review':{'kind':'manual','schema':{'type':'object','properties':{'value':{'type':'number'}},'required':['value'],'additionalProperties':False},'locks':['input.target'],
                          'effects':[effect('value',value_from='answers.review.value')],'next':'finish'},
                'finish':{'kind':'end','result':'管理员定义的结果'}}}}}


def arrange(service,game):
    command(service,game,'definition',{'version':1,'definition':definition()})
    return command(service,game,'entity.create',{'type_key':'custom','data':{'state':'new','value':0}})


def start(service,game,record,amount=20,actor='admin',key=None):
    return command(service,game,'submit',{'interaction':'begin','data':{'target':record['id'],'amount':amount}},actor,key)


def current(service,game):
    return service.snapshot(game['id'])['flows'][0]


def resume(service,game,run,value=99,key=None):
    return command(service,game,'flow.resume',{'id':run['id'],'version':run['version'],'data':{'value':value},'reason':'明确指定结果'},key=key)


def test_mixed_fixed_branch_and_manual_decision(service,game):
    record=arrange(service,game)
    response=start(service,game,record,20)
    assert response['flow_status']=='waiting' and 'context' not in response and 'history' not in response
    run=current(service,game)
    assert run['status']=='waiting' and run['current_step']=='review'
    assert service.snapshot(game['id'])['entities'][0]['data']=={'state':'started','value':0}
    result=resume(service,game,run,27)
    assert result['status']=='completed'
    assert service.snapshot(game['id'])['entities'][0]['data']['value']==27
    assert result['context']['answers']['review']=={'value':27}


def test_automatic_branch_never_requires_manual_answer(service,game):
    record=arrange(service,game)
    start(service,game,record,4)
    assert current(service,game)['status']=='completed'
    assert service.snapshot(game['id'])['entities'][0]['data']['value']==4


def test_local_wait_only_blocks_selected_record_and_admin_can_override(service,game):
    record=arrange(service,game)
    other=command(service,game,'entity.create',{'type_key':'custom','data':{'value':0}})
    start(service,game,record,actor='qq:7')
    with pytest.raises(DomainError) as error:
        command(service,game,'submit',{'interaction':'change','data':{'target':record['id']}},'qq:7')
    assert error.value.code=='OBJECT_WAITING'
    command(service,game,'submit',{'interaction':'change','data':{'target':other['id']}},'qq:7')
    command(service,game,'entity.update',{'id':record['id'],'type_key':'custom','version':2,'data':{'value':9}})
    assert current(service,game)['status']=='stale' and current(service,game)['locks']==[]


def test_cancel_releases_wait_without_undoing_committed_steps(service,game):
    record=arrange(service,game)
    start(service,game,record,actor='qq:7')
    run=current(service,game)
    command(service,game,'flow.cancel',{'id':run['id'],'version':1,'reason':'调整计划'})
    assert service.snapshot(game['id'])['entities'][0]['data']['state']=='started'
    command(service,game,'submit',{'interaction':'change','data':{'target':record['id']}},'qq:7')
    with pytest.raises(DomainError):
        resume(service,game,run)


def test_resume_schema_failure_rolls_back_everything(service,game):
    record=arrange(service,game)
    start(service,game,record)
    run=current(service,game)
    with pytest.raises(DomainError):
        resume(service,game,run,'not number')
    assert current(service,game)==run


def test_resume_concurrent_and_idempotent(service,game):
    record=arrange(service,game)
    start(service,game,record)
    run=current(service,game)
    key=uuid4().hex
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows=list(pool.map(lambda _:resume(service,game,run,key=key),range(10)))
    assert len({r['version'] for r in rows})==1
    with pytest.raises(DomainError):
        resume(service,game,run,key=uuid4().hex)
    assert len([e for e in service.snapshot(game['id'])['events'] if e['kind']=='flow.decided'])==1


def test_definition_edit_releases_wait_and_preserves_history(service,game):
    record=arrange(service,game)
    start(service,game,record)
    command(service,game,'definition',{'version':2,'definition':definition()})
    assert current(service,game)['status']=='stale' and not current(service,game)['locks']
    assert current(service,game)['history'][-1]['kind']=='waiting'


def test_wait_survives_store_restart(service,store,game):
    record=arrange(service,game)
    start(service,game,record)
    run=current(service,game)
    store.initialize()
    assert current(service,game)==run
    assert resume(service,game,run)['status']=='completed'


def test_player_cannot_resume_or_directly_start_or_cross_game(service,game):
    record=arrange(service,game)
    start(service,game,record)
    run=current(service,game)
    with pytest.raises(DomainError) as error:
        command(service,game,'flow.resume',{'id':run['id'],'version':1,'data':{'value':3},'reason':'伪造权限'},'qq:7')
    assert error.value.code=='FORBIDDEN'
    other=service.create_game('另一个游戏','')
    with pytest.raises(DomainError):
        resume(service,other,run)
    with pytest.raises(DomainError):
        command(service,game,'flow.start',{'workflow':'route','data':{'target':record['id'],'amount':0}},'qq:7')


@pytest.mark.parametrize('mutation',['cycle','missing','expression','unknown','nested'])
def test_invalid_graphs_and_conditions_rejected(mutation):
    d=definition()
    steps=d['workflows']['route']['steps']
    if mutation=='cycle':steps['review']['next']='choose'
    if mutation=='missing':steps['review']['next']='absent'
    if mutation=='expression':steps['choose']['condition']={'python':'__import__("os")'}
    if mutation=='unknown':steps['review']['execute']='something'
    if mutation=='nested':
        c={'left':{'path':'input.amount'},'op':'exists'}
        for _ in range(8):c={'not':c}
        steps['choose']['condition']=c
    with pytest.raises(DomainError):validate_definition(d)


def test_start_rolls_back_submission_and_first_step_if_later_step_fails(service,game):
    d=definition()
    d['workflows']['route']['steps']['choose']['condition']['left']={'path':'input.absent'}
    command(service,game,'definition',{'version':1,'definition':d})
    record=command(service,game,'entity.create',{'type_key':'custom','data':{'state':'new','value':0}})
    before=service.snapshot(game['id'])
    with pytest.raises(DomainError):start(service,game,record)
    assert service.snapshot(game['id'])==before


def test_checkpoint_restores_wait_as_stale_and_keeps_monotonic_run_version(service,game):
    record=arrange(service,game)
    start(service,game,record)
    run=current(service,game)
    checkpoint=create_checkpoint(service,game['id'],'等待中')
    completed=resume(service,game,run)
    restore_checkpoint(service,game['id'],checkpoint['id'],2,uuid4().hex)
    restored=current(service,game)
    assert restored['status']=='stale' and restored['version']>completed['version'] and restored['locks']==[]
    with pytest.raises(DomainError):resume(service,game,run)


def test_agent_preview_of_flow_is_rolled_back_and_fingerprint_tracks_waits(service,game):
    record=arrange(service,game)
    agent=Agent(service,Settings())
    plan=agent.prepare(game['id'],'发起流程','等待管理员',[{'kind':'flow.start','payload':{'workflow':'route','data':{'target':record['id'],'amount':20}}}])
    assert service.snapshot(game['id'])['flows']==[]
    assert agent.apply(game['id'],plan['id'])['status']=='applied'
    assert current(service,game)['status']=='waiting'


def test_precondition_records_entity_read_set_for_manual_approval(service,game):
    record=arrange(service,game)
    d=definition()
    d['interactions']['approve']={'mode':'manual','condition':{'left':{'entity':{'id_from':'target','type_key':'custom','field':'value'}},'op':'eq','right':{'value':0}}}
    command(service,game,'definition',{'version':2,'definition':d})
    pending=command(service,game,'submit',{'interaction':'approve','data':{'target':record['id']}})
    command(service,game,'entity.update',{'id':record['id'],'type_key':'custom','version':1,'data':{'value':1}})
    with pytest.raises(DomainError) as error:
        command(service,game,'decide',{'id':pending['id'],'decision':'applied','result':'旧条件'})
    assert error.value.code=='STATE_STALE'


def test_qq_trigger_reports_waiting_without_exposing_private_flow_data(service,store,game):
    import json
    from mythweaver.onebot import OneBot
    record=arrange(service,game)
    settings=Settings()
    bot=OneBot(store,service,settings)
    with store.session.begin() as db:
        db.add(Channel(group_id='100',game_id=game['id']))
    text='/mw 使用 begin '+json.dumps({'target':record['id'],'amount':20})
    frame={'self_id':settings.bot_id,'post_type':'message','message_type':'group','group_id':100,'user_id':7,'message_id':1,
           'message':[{'type':'text','data':{'text':text}}]}
    for _ in range(10):bot.accept_event(frame)
    bot.process_inbox()
    with store.session() as db:
        reply=db.scalar(select(Outbox)).text
        assert '等待管理员决定' in reply and 'answers' not in reply and 'review' not in reply
    assert len(service.snapshot(game['id'])['flows'])==1


def test_manual_flow_preview_rolls_back_and_guards_later_state(service,game):
    from mythweaver.workflows import preview_resume
    record=arrange(service,game)
    start(service,game,record)
    run=current(service,game)
    before=service.snapshot(game['id'])
    payload={'id':run['id'],'version':1,'data':{'value':27},'reason':'先检查后续影响'}
    preview=preview_resume(service,game['id'],payload)
    assert preview['run']['status']=='completed' and any(e['kind']=='rule.effect' for e in preview['events'])
    assert service.snapshot(game['id'])==before
    command(service,game,'entity.create',{'type_key':'custom','data':{'value':3}})
    with pytest.raises(DomainError) as error:
        command(service,game,'flow.resume',{**payload,'fingerprint':preview['fingerprint']})
    assert error.value.code=='STATE_STALE'


def test_manual_flow_can_apply_the_exact_preview_if_state_unchanged(service,game):
    from mythweaver.workflows import preview_resume
    record=arrange(service,game)
    start(service,game,record)
    run=current(service,game)
    payload={'id':run['id'],'version':1,'data':{'value':12},'reason':'确认预演结果'}
    preview=preview_resume(service,game['id'],payload)
    result=command(service,game,'flow.resume',{**payload,'fingerprint':preview['fingerprint']})
    assert result['status']=='completed'
    assert service.snapshot(game['id'])['entities'][0]['data']['value']==12
