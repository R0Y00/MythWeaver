from copy import deepcopy
from uuid import uuid4

import pytest
from sqlalchemy import func,select

from mythweaver.agent import Agent,validate_commands
from mythweaver.agent_reads import read
from mythweaver.checkpoints import create_checkpoint,restore_checkpoint
from mythweaver.command_catalog import COMMANDS,catalog
from mythweaver.config import Settings
from mythweaver.db import Blueprint,Channel,Checkpoint,Entity,Game,Participant,RetiredEntity
from mythweaver.definitions import DomainError
from mythweaver.examples import instantiate


@pytest.fixture
def control_client(store):
    from fastapi.testclient import TestClient
    from mythweaver.api import create_app
    settings=Settings(llm_api_key='',llm_base_url='',llm_model='')
    with TestClient(create_app(settings,store)) as client:
        client.headers.update({'x-mythweaver':'1'})
        assert client.post('/api/login',json={'password':settings.admin_password}).status_code==200
        yield client


def cmd(service,game,kind,payload,actor='admin',key=None):
    return service.command(game['id'],kind,payload,key or uuid4().hex,actor)


def setup(service,game):
    definition={'types':{'note':{'label':'便签','schema':{'type':'object','properties':{'text':{'type':'string'}},'required':['text'],'additionalProperties':False}}},'interactions':{}}
    cmd(service,game,'definition',{'version':1,'definition':definition})
    entity=cmd(service,game,'entity.create',{'type_key':'note','data':{'text':'原内容'}})
    return definition,entity


def test_catalog_schemas_are_the_actual_agent_validators():
    assert set(c['kind'] for c in catalog())==set(COMMANDS)
    command=validate_commands([{'kind':'policy.update','payload':{'version':0,'paused':True}}])[0]
    assert command['payload']=={'version':0,'paused':True}  # Unrelated overrides must not be reset.
    with pytest.raises(DomainError):
        validate_commands([{'kind':'participant.update','payload':{'actor':'qq:7','role':'platform_admin'}}])


def test_agent_can_plan_complete_game_admin_batch_and_preview_has_no_side_effects(service,store,game):
    agent=Agent(service,Settings())
    commands=[{'kind':'game.update','payload':{'version':1,'name':'新框架','description':'由管理员修改'}},
              {'kind':'policy.update','payload':{'version':0,'paused':True,'command_prefix':'/custom','instructions':'管理员安排后续'}},
              {'kind':'participant.update','payload':{'actor':'qq:7','label':'参与者','blocked':True,'version':0}},
              {'kind':'channel.bind','payload':{'group_id':'100'}},
              {'kind':'checkpoint.create','payload':{'label':'管理员快照'}},
              {'kind':'blueprint.save','payload':{'name':'当前定义模板'}}]
    plan=agent.prepare(game['id'],'按我的要求管理游戏','具体范围已明确',commands)
    assert plan['impact']['events']
    assert service.snapshot(game['id'])['game']['name']==game['name']
    with store.session() as db:
        assert not db.scalar(select(func.count()).select_from(Participant))
        assert not db.scalar(select(func.count()).select_from(Channel))
        assert not db.scalar(select(func.count()).select_from(Checkpoint))
        assert not db.scalar(select(func.count()).select_from(Blueprint))
    result=agent.apply(game['id'],plan['id'])
    assert result['status']=='applied'
    snapshot=service.snapshot(game['id'])
    assert snapshot['game']['name']=='新框架' and snapshot['policy']['paused']
    assert snapshot['channels'][0]['group_id']=='100'
    assert agent.apply(game['id'],plan['id'])==result


def test_policy_or_participant_or_binding_change_invalidates_old_plan(service,game):
    agent=Agent(service,Settings())
    first=cmd(service,game,'participant.update',{'actor':'qq:7','label':'旧名字'})
    plan=agent.prepare(game['id'],'记录','仅记录',[{'kind':'submit','payload':{'data':{'text':'内容'}}}])
    cmd(service,game,'participant.update',{'actor':'qq:7','label':'新名字','version':first['version']})
    with pytest.raises(DomainError) as error:agent.apply(game['id'],plan['id'])
    assert error.value.code=='STATE_STALE'


def test_participant_versions_and_policy_idempotency(service,game):
    member=cmd(service,game,'participant.update',{'actor':'qq:7','version':0})
    with pytest.raises(DomainError):
        cmd(service,game,'participant.update',{'actor':'qq:7','label':'旧请求','version':0})
    key=uuid4().hex
    row=cmd(service,game,'policy.update',{'version':0,'paused':True},key=key)
    assert cmd(service,game,'policy.update',{'version':0,'paused':True},key=key)==row
    with pytest.raises(DomainError):cmd(service,game,'policy.update',{'version':0,'paused':False},key=key)


def test_migration_can_rename_fields_and_types_with_atomic_data_rewrite(service,game):
    _,entity=setup(service,game)
    definition={'types':{'memo':{'schema':{'type':'object','properties':{'content':{'type':'string'},'priority':{'type':'integer'}},'required':['content','priority'],'additionalProperties':False}}}}
    payload={'version':2,'definition':definition,'changes':[{'id':entity['id'],'version':1,'type_key':'memo','data':{'content':'迁移后','priority':3}}],'reason':'重命名类型和字段'}
    plan=Agent(service,Settings()).prepare(game['id'],'迁移','预演类型变更',[{'kind':'definition.migrate','payload':payload}])
    assert service.snapshot(game['id'])['entities'][0]['type_key']=='note'
    assert any(e['kind']=='entity.migrated' for e in plan['impact']['events'])
    Agent(service,Settings()).apply(game['id'],plan['id'])
    snapshot=service.snapshot(game['id'])
    assert snapshot['entities'][0]['data']=={'content':'迁移后','priority':3}
    assert snapshot['entities'][0]['version']==2 and snapshot['entities'][0]['type_key']=='memo'


def test_invalid_migration_rolls_back_records_definition_and_safety_checkpoint(service,store,game):
    _,entity=setup(service,game)
    before=service.snapshot(game['id'])
    with pytest.raises(DomainError):
        cmd(service,game,'definition.migrate',{'version':2,'definition':{'types':{}},'changes':[],'reason':'没有处理旧记录'})
    assert service.snapshot(game['id'])==before
    with store.session() as db:assert db.scalar(select(func.count()).select_from(Checkpoint))==0


def test_retire_allows_removing_old_concept_and_checkpoint_can_restore(service,game):
    _,entity=setup(service,game)
    result=cmd(service,game,'definition.migrate',{'version':2,'definition':{'types':{},'interactions':{}},'retire':[{'id':entity['id'],'version':1,'reason':'不再使用便签'}],'reason':'删除旧概念并保留副本'})
    assert service.snapshot(game['id'])['entities']==[]
    retired=read(service,game['id'],'list_controls',{'section':'retired'})[0]
    assert retired['snapshot']['data']['text']=='原内容'
    with pytest.raises(DomainError):cmd(service,game,'entity.restore',{'id':entity['id'],'version':retired['version'],'reason':'类型尚未恢复'})
    restore_checkpoint(service,game['id'],result['safety_checkpoint'],3,uuid4().hex)
    assert service.snapshot(game['id'])['entities'][0]['data']['text']=='原内容'
    assert service.snapshot(game['id'])['entities'][0]['version']>retired['version']


def test_retirement_rejects_dangling_refs_and_restore_versions_are_monotonic(service,game):
    d,entity=setup(service,game)
    d['types']['link']={'schema':{'type':'object','properties':{'target':{'type':'string','x-reference':{'type_key':'note','allow_archived':True}}}}}
    cmd(service,game,'definition',{'version':2,'definition':d})
    link=cmd(service,game,'entity.create',{'type_key':'link','data':{'target':entity['id']}})
    with pytest.raises(DomainError):cmd(service,game,'entity.retire',{'id':entity['id'],'version':1,'reason':'仍有引用'})
    cmd(service,game,'entity.retire',{'id':link['id'],'version':1,'reason':'先退役引用'})
    retired=cmd(service,game,'entity.retire',{'id':entity['id'],'version':1,'reason':'现在可退役'})
    restored=cmd(service,game,'entity.restore',{'id':entity['id'],'version':retired['version'],'reason':'恢复原记录'})
    assert restored['version']>retired['version']>entity['version']
    with pytest.raises(DomainError):cmd(service,game,'entity.update',{'id':entity['id'],'type_key':'note','version':1,'data':{'text':'陈旧更新'}})


def test_submission_revision_does_not_replay_side_effects_and_invalidates_old_plan(service,game):
    item=cmd(service,game,'submit',{'data':{'text':'原始内容'}})
    plan=Agent(service,Settings()).prepare(game['id'],'记录','记录',[{'kind':'submit','payload':{'data':{'text':'其他内容'}}}])
    revised=cmd(service,game,'submission.revise',{'id':item['id'],'version':1,'interaction':'_free','data':{'text':'修订内容'},'status':'applied','result':'管理员指定结果','reason':'修订记载'})
    assert revised['version']==2 and revised['status']=='applied'
    with pytest.raises(DomainError):Agent(service,Settings()).apply(game['id'],plan['id'])
    event=service.snapshot(game['id'])['events'][0]
    assert event['kind']=='submission.revise' and not event['payload']['side_effects_replayed']


def test_admin_can_skip_or_rewrite_flow_without_replaying_previous_steps(service):
    game=instantiate(service,'proposal-collaboration')
    target=service.snapshot(game['id'])['entities'][0]
    cmd(service,game,'submit',{'interaction':'request','data':{'target':target['id'],'requested':50}})
    run=service.snapshot(game['id'])['flows'][0]
    result=cmd(service,game,'flow.redirect',{'id':run['id'],'version':1,'step':'finish','reason':'管理员决定跳过剩余安排'})
    assert result['status']=='completed'
    assert service.snapshot(game['id'])['entities'][0]['data']==target['data']
    result=cmd(service,game,'flow.redirect',{'id':run['id'],'version':2,'step':'check','input':{'target':target['id'],'requested':5},'reason':'按新输入重新安排'})
    assert result['status']=='completed' and result['version']==3
    assert next(e for e in service.snapshot(game['id'])['entities'] if e['id']==target['id'])['data']['allocation']==5


@pytest.mark.parametrize('kind,payload',[('policy.update',{'version':0}),('participant.update',{'actor':'qq:7'}),('channel.bind',{'group_id':'100'}),('checkpoint.create',{'label':'不能保存'}),('game.create',{'name':'不能创建'})])
def test_player_cannot_use_new_game_admin_tools(service,game,kind,payload):
    with pytest.raises(DomainError) as error:cmd(service,game,kind,payload,actor='qq:7')
    assert error.value.code=='FORBIDDEN'


def test_new_read_tools_are_scoped_and_do_not_include_platform_secrets(service,game):
    other=service.create_game('另一游戏','')
    foreign=cmd(service,other,'checkpoint.create',{'label':'秘密备份'})
    assert read(service,game['id'],'list_controls',{'section':'checkpoints','id':foreign['id']})==[]
    schemas=read(service,game['id'],'inspect_capabilities',{'kinds':['definition.migrate','policy.update']})
    assert len(schemas)==2 and 'schema' in schemas[0]
    assert 'llm_api_key' not in str(schemas)


def test_new_game_creation_and_clone_do_not_change_source(service,game):
    setup(service,game)
    created=cmd(service,game,'game.create',{'name':'空白游戏'})
    cloned=cmd(service,game,'game.clone',{'name':'框架副本'})
    assert created['definition']=={'types':{},'interactions':{}}
    assert 'note' in cloned['definition']['types']
    assert service.snapshot(cloned['id'])['entities']==[]
    assert len(service.snapshot(game['id'])['entities'])==1


def test_retired_record_can_be_restored_directly_into_new_schema(service,game):
    _,entity=setup(service,game)
    result=cmd(service,game,'definition.migrate',{'version':2,'definition':{'types':{'memo':{'schema':{'type':'object','properties':{'content':{'type':'string'}},'required':['content']}}}},
                                               'retire':[{'id':entity['id'],'version':1,'reason':'改造旧类型'}],'reason':'重定义框架'})
    row=read(service,game['id'],'list_controls',{'section':'retired'})[0]
    restored=cmd(service,game,'entity.restore',{'id':row['id'],'version':row['version'],'type_key':'memo','data':{'content':'迁移后的内容'},'reason':'按新框架恢复'})
    assert restored['type_key']=='memo' and restored['data']=={'content':'迁移后的内容'}


def test_submission_version_never_repeats_across_disappearance_and_restore(service,game):
    empty=create_checkpoint(service,game['id'],'空白')
    item=cmd(service,game,'submit',{'data':{'text':'参与记录'}})
    saved=create_checkpoint(service,game['id'],'有参与记录')
    revised=cmd(service,game,'submission.revise',{'id':item['id'],'version':1,'interaction':'_free','data':{'text':'已修订'},'status':'applied','reason':'明确修订'})
    restore_checkpoint(service,game['id'],empty['id'],1,uuid4().hex)
    restore_checkpoint(service,game['id'],saved['id'],2,uuid4().hex)
    restored=service.snapshot(game['id'])['submissions'][0]
    assert restored['version']>revised['version']
    with pytest.raises(DomainError):
        cmd(service,game,'submission.revise',{'id':item['id'],'version':revised['version'],'interaction':'_free','data':{'text':'旧请求'},'status':'applied','reason':'旧版本'})


def test_http_control_and_specific_ui_endpoints_share_the_agent_execution_layer(control_client,service,game):
    path='/api/games/'+game['id']
    advertised=control_client.get(path+'/capabilities').json()
    assert {r['kind'] for r in advertised['commands']}==set(COMMANDS)
    response=control_client.post(path+'/commands/preview',json={'commands':[
        {'kind':'policy.update','payload':{'version':0,'command_prefix':'/shared','paused':True}},
        {'kind':'participant.update','payload':{'actor':'qq:7','label':'同一管理服务','version':0}},
        {'kind':'checkpoint.create','payload':{'label':'共享检查点'}}]})
    assert response.status_code==200
    preview=response.json()
    assert service.snapshot(game['id'])['policy']['command_prefix']=='/mw'
    assert control_client.get(path+'/participants').json()==[]
    assert control_client.get(path+'/checkpoints').json()==[]
    assert control_client.post(path+'/agent/plans/'+preview['id']+'/apply',json={}).status_code==200
    assert control_client.get(path+'/policy').json()['command_prefix']=='/shared'
    assert control_client.get(path+'/participants').json()[0]['label']=='同一管理服务'
    assert len(control_client.get(path+'/checkpoints').json())==1
    assert control_client.put(path+'/policy',json={'version':0},headers={'idempotency-key':uuid4().hex}).status_code==409


def test_http_game_controls_reject_unauthenticated_and_cross_game_retirement(control_client,service,game):
    _,record=setup(service,game)
    retired=cmd(service,game,'entity.retire',{'id':record['id'],'version':1,'reason':'测试退役恢复'})
    other=service.create_game('其他空间','')
    response=control_client.post('/api/games/'+other['id']+'/commands',json={'kind':'entity.restore','payload':{'id':retired['id'],'version':retired['version'],'reason':'不允许跨游戏'}},headers={'idempotency-key':uuid4().hex})
    assert response.status_code==404
    control_client.cookies.clear()
    assert control_client.get('/api/games/'+game['id']+'/capabilities').status_code==401
    assert control_client.post('/api/games/'+game['id']+'/commands',json={'kind':'policy.update','payload':{'version':0}},headers={'idempotency-key':uuid4().hex}).status_code==401


def test_agent_can_create_independent_blueprint_and_instantiate_it(service,game):
    result=cmd(service,game,'blueprint.create',{'name':'独立玩法','definition':{'types':{'memo':{'schema':{'type':'object'}}}}})
    cloned=cmd(service,game,'blueprint.instantiate',{'id':result['id'],'label':'从模板创建'})
    assert 'memo' in cloned['definition']['types']
    assert service.snapshot(game['id'])['game']['definition']=={'types':{},'interactions':{}}
    assert not service.snapshot(cloned['id'])['channels']
