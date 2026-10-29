from copy import deepcopy

import pytest
from sqlalchemy import select

from mythweaver.db import Entity, Outbox, Submission
from mythweaver.definitions import DomainError,validate_definition
from mythweaver.player import view
from mythweaver.plugins import Plugins


def setup(service,game,mode='rule'):
    definition={'types':{'account':{'schema':{'type':'object','properties':{
        'owner':{'type':'string'},'alias':{'type':'string'},'value':{'type':'integer','minimum':0},'secret':{'type':'string'}},
        'required':['owner','alias','value','secret'],'additionalProperties':False},
        'player_access':{'owner_field':'owner','read_fields':['alias','value'],'shared_write':False}}},
        'interactions':{'transfer':{'mode':mode,'player_enabled':True,'schema':{'type':'object','properties':{
            'alias':{'type':'string'},'amount':{'type':'integer','minimum':1}},'required':['alias','amount'],'additionalProperties':False},
            'extension':{'plugin_key':'transfers','tool':'handle','reads':[{'type_key':'account','fields':['owner','alias','value'],'scope':'game'}],
                         'writes':[{'type_key':'account','fields':['value'],'scope':'game'}],'messages':True}}}}
    service.command(game['id'],'definition',{'version':1,'definition':definition},'definition')
    snap=service.snapshot(game['id'])
    service.command(game['id'],'policy.update',{'version':snap['policy_version'],'player_agent_enabled':True},'policy')
    rows=[]
    for i in (1,2):
        rows.append(service.command(game['id'],'entity.create',{'type_key':'account','data':{
            'owner':f'qq:{i}','alias':f'role-{i}','value':10,'secret':f'GM_SECRET_{i}'},'visibility':'private'},f'entity-{i}'))
        service.command(game['id'],'channel.bind',{'group_id':f'private-{i}'},f'bind-{i}')
    return definition,rows


def handler(payload):
    own=next(r for r in payload['records'] if r['data']['owner']==payload['actor'])
    other=next(r for r in payload['records'] if r['data']['alias']==payload['input']['alias'])
    amount=payload['input']['amount']
    return {'updates':[{'id':own['id'],'changes':{'value':own['data']['value']-amount}},
                       {'id':other['id'],'changes':{'value':other['data']['value']+amount}}],
            'messages':[{'recipient':payload['actor'],'text':f"已向{other['data']['alias']}转移{amount}"},
                        {'recipient':other['data']['owner'],'text':f'收到匿名转移{amount}'}]}


def mock_handler(monkeypatch,service,fn=handler):
    def invoke(game_id,key,tool,data,**kwargs):return fn(deepcopy(data))
    monkeypatch.setattr(service.plugins,'invoke',invoke)


def submit(service,game,key='submit'):
    return service.command(game['id'],'submit',{'interaction':'transfer','data':{'alias':'role-2','amount':3}},key,actor='qq:1')


def test_extension_multiplayer_state_and_separate_deliveries_are_atomic_idempotent(service,game,monkeypatch):
    _,rows=setup(service,game);seen=[]
    def execute(p):seen.append(p);return handler(p)
    mock_handler(monkeypatch,service,execute)
    result=submit(service,game);assert submit(service,game)==result and len(seen)==1
    assert 'secret' not in seen[0]['records'][0]['data']
    with service.store.session() as db:
        assert [db.get(Entity,r['id']).data['value'] for r in rows]==[7,13]
        deliveries=list(db.scalars(select(Outbox).where(Outbox.status=='queued').order_by(Outbox.group_id)))
        assert [(d.group_id,d.text) for d in deliveries]==[('private-1','已向role-2转移3'),('private-2','收到匿名转移3')]
        visible=view(service,db,service.game(db,game['id']),'qq:1')
        assert len(visible['records'])==1 and visible['records'][0]['data']=={'alias':'role-1','value':7}
        assert 'extension' not in visible['interactions']['transfer']


def test_extension_feedback_returns_real_result_without_qq_binding(service,game,monkeypatch):
    definition,rows=setup(service,game)
    definition['interactions']['transfer']['extension']['messages']=False
    service.command(game['id'],'definition',{'version':2,'definition':definition},'feedback-definition')
    for actor in ('1','2'):service.command(game['id'],'channel.unbind',{'group_id':'private-'+actor},'unbind-'+actor)
    def execute(p):
        result=handler(p);result['messages']=[];result['feedback']='已完成转移3点';return result
    mock_handler(monkeypatch,service,execute)
    result=submit(service,game)
    assert result['result']=='已完成转移3点'
    assert submit(service,game)==result
    with service.store.session() as db:
        assert [db.get(Entity,r['id']).data['value'] for r in rows]==[7,13]
        assert not list(db.scalars(select(Outbox)))
        assert db.get(Submission,result['id']).result==result['result']


@pytest.mark.parametrize('feedback',[None,42,'','x'*8001,{'secret':'private'}])
def test_invalid_extension_feedback_rejects_entire_action(service,game,monkeypatch,feedback):
    _,rows=setup(service,game)
    mock_handler(monkeypatch,service,lambda p:{**handler(p),'feedback':feedback})
    with pytest.raises(DomainError,match='feedback'):submit(service,game)
    with service.store.session() as db:
        assert [db.get(Entity,r['id']).data['value'] for r in rows]==[10,10]
        assert not list(db.scalars(select(Submission)))
        assert not list(db.scalars(select(Outbox)))


def test_playtest_validates_player_scope_and_rolls_back_all_steps(service,game,monkeypatch):
    from mythweaver.playtest import simulate
    from mythweaver.db import Command,Event,EntityRevision
    _,rows=setup(service,game)
    def execute(p):return {**handler(p),'feedback':'转移成功'}
    mock_handler(monkeypatch,service,execute)
    before=service.snapshot(game['id'])
    with service.store.session() as db:
        counts_before={model:len(list(db.scalars(select(model)))) for model in (Command,Event,EntityRevision,Outbox)}
    result=simulate(service,game['id'],'qq:1',[
        {'interaction':'transfer','data':{'alias':'role-2','amount':3}},
        {'interaction':'transfer','data':{'alias':'role-2','amount':30}},
        {'interaction':'transfer','data':{'alias':'role-2','amount':2}}])
    assert [s['ok'] for s in result['steps']]==[True,False,True]
    assert result['steps'][0]['result']['result']=='转移成功'
    assert result['steps'][0]['observation']['records'][0]['data']=={'alias':'role-1','value':7}
    assert 'secret' not in result['steps'][0]['observation']['records'][0]['data']
    assert [r['data']['value'] for r in sorted(result['final_records'],key=lambda r:r['data']['owner'])]==[5,15]
    assert len(result['deliveries'])==4 and result['rolled_back'] is True
    assert service.snapshot(game['id'])==before
    with service.store.session() as db:
        assert all(len(list(db.scalars(select(model))))==count for model,count in counts_before.items())


@pytest.mark.parametrize('case',['field','owner','outside','scope','message','unbound','negative','duplicate'])
def test_extension_grant_failure_rolls_back_all_state_and_messages(service,game,monkeypatch,case):
    _,rows=setup(service,game)
    if case=='unbound':service.command(game['id'],'channel.unbind',{'group_id':'private-2'},'unbind')
    if case=='scope':
        snap=service.snapshot(game['id']);definition=snap['game']['definition']
        definition['interactions']['transfer']['extension']['writes'][0]['scope']='own'
        service.command(game['id'],'definition',{'version':snap['game']['version'],'definition':definition},'restrict')
    other=service.create_game('其他游戏','')
    def execute(p):
        result=handler(p)
        if case=='field':result['updates'][1]['changes']={'secret':'tampered'}
        if case=='owner':result['updates'][1]['changes']={'owner':'qq:1'}
        if case=='outside':result['updates'][1]['id']=other['id']
        if case=='message':result['messages'][1]['recipient']='qq:999'
        if case=='negative':result['updates'][1]['changes']={'value':-1}
        if case=='duplicate':result['updates'].append(deepcopy(result['updates'][0]))
        return result
    mock_handler(monkeypatch,service,execute)
    with pytest.raises(DomainError):submit(service,game)
    with service.store.session() as db:
        assert [db.get(Entity,r['id']).data['value'] for r in rows]==[10,10]
        assert not list(db.scalars(select(Outbox)))
        assert not list(db.scalars(select(Submission)))


def test_manual_extension_preserves_original_actor_and_does_not_cancel_new_deliveries(service,game,monkeypatch):
    _,rows=setup(service,game,'manual');actors=[]
    def execute(p):actors.append(p['actor']);return handler(p)
    mock_handler(monkeypatch,service,execute)
    result=submit(service,game);assert actors==[] and result['status']=='pending'
    service.command(game['id'],'decide',{'id':result['id'],'decision':'applied','result':'允许转移'},'decide')
    assert actors==['qq:1']
    with service.store.session() as db:
        assert [db.get(Entity,r['id']).data['value'] for r in rows]==[7,13]
        assert len(list(db.scalars(select(Outbox).where(Outbox.status=='queued'))))==2


def test_manual_extension_rejects_new_records_added_after_submission(service,game,monkeypatch):
    setup(service,game,'manual');mock_handler(monkeypatch,service)
    result=submit(service,game)
    service.command(game['id'],'entity.create',{'type_key':'account','data':{'owner':'qq:3','alias':'role-3','value':0,'secret':'x'}},'third')
    with pytest.raises(DomainError,match='扩展读取范围已变化'):
        service.command(game['id'],'decide',{'id':result['id'],'decision':'applied','result':'允许'},'decide')


def test_extension_errors_do_not_leak_private_plugin_inputs(service,game,monkeypatch):
    setup(service,game)
    def execute(p):raise DomainError('PLUGIN_FAILED','GM_SECRET_1 traceback')
    mock_handler(monkeypatch,service,execute)
    with pytest.raises(DomainError) as error:submit(service,game)
    assert error.value.code=='EXTENSION_FAILED' and 'GM_SECRET' not in str(error.value)


def test_workflow_manual_extension_reads_admin_answers_and_notifies_after_decision(service,game,monkeypatch):
    definition,rows=setup(service,game)
    extension=definition['interactions']['transfer'].pop('extension')
    definition['interactions']['transfer'].update(mode='flow',workflow='review')
    definition['workflows']={'review':{'start':'approval','steps':{
        'approval':{'kind':'manual','schema':{'type':'object','properties':{'amount':{'type':'integer','minimum':1}},'required':['amount']},
                    'extension':extension,'next':'done'},'done':{'kind':'end','result':'已处理'}}}}
    snap=service.snapshot(game['id'])
    service.command(game['id'],'definition',{'version':snap['game']['version'],'definition':definition},'workflow')
    seen=[]
    def execute(p):
        seen.append(p)
        return handler({**p,'input':{'alias':p['input']['input']['alias'],'amount':p['input']['answers']['approval']['amount']}})
    mock_handler(monkeypatch,service,execute)
    result=submit(service,game)
    assert result['flow_status']=='waiting' and not seen
    run=service.snapshot(game['id'])['flows'][0]
    service.command(game['id'],'flow.resume',{'id':run['id'],'version':run['version'],'data':{'amount':4},'reason':'管理员指定实际数量'},'resume')
    assert seen[0]['actor']=='qq:1'
    with service.store.session() as db:
        assert [db.get(Entity,r['id']).data['value'] for r in rows]==[6,14]
        assert len(list(db.scalars(select(Outbox).where(Outbox.status=='queued'))))==2


def test_messages_need_explicit_grant(service,game,monkeypatch):
    definition,rows=setup(service,game)
    definition['interactions']['transfer']['extension']['messages']=False
    service.command(game['id'],'definition',{'version':2,'definition':definition},'no-messages')
    mock_handler(monkeypatch,service)
    with pytest.raises(DomainError,match='消息投递授权'):submit(service,game)
    with service.store.session() as db:
        assert [db.get(Entity,r['id']).data['value'] for r in rows]==[10,10]
        assert not list(db.scalars(select(Outbox)))


def test_extension_real_plugin_can_resolve_hidden_alias_and_update_two_players(service,game,tmp_path):
    setup(service,game)
    service._plugins=Plugins(service,tmp_path/'plugins')
    import inspect
    source=inspect.getsource(handler)
    data={'actor':'qq:1','input':{'alias':'role-2','amount':3},'records':[
        {'id':'a','data':{'owner':'qq:1','alias':'role-1','value':10}},
        {'id':'b','data':{'owner':'qq:2','alias':'role-2','value':10}}]}
    manifest={'runtime':'python','requirements':[],'tools':{'handle':{'entry':'main.py:handler','player_enabled':True,
        'input_schema':{'type':'object'},'output_schema':{'type':'object'}}},
        'tests':[{'tool':'handle','input':data,'expected':handler(data)}]}
    draft=service.plugins.draft(game['id'],'transfers',manifest,{'main.py':source})
    try:service.plugins.install(game['id'],draft['id'])
    except DomainError:
        pytest.fail(str(service.plugins.inspect(game['id'],draft['id'])['report']))
    assert submit(service,game)['status']=='applied'
    assert sorted(e['data']['value'] for e in service.snapshot(game['id'])['entities'])==[7,13]


@pytest.mark.parametrize('change',[{'fields':['owner']},{'scope':'arbitrary'},{'type_key':'missing'}])
def test_definition_rejects_invalid_extension_grants(service,game,change):
    definition,_=setup(service,game)
    definition['interactions']['transfer']['extension']['writes'][0].update(change)
    with pytest.raises(DomainError):validate_definition(definition)
