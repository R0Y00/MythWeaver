import asyncio
from copy import deepcopy
import json
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func,select

from mythweaver.agent import Agent
from mythweaver.agent_reads import read
from mythweaver.checkpoints import restore_checkpoint
from mythweaver.config import Settings
from mythweaver.db import Checkpoint
from mythweaver.definitions import DomainError,validate_definition
from mythweaver.functions import Runtime,check_expression
from mythweaver.player import PlayerAgent


def literal(value):return {'literal':value}
def path(value):return {'path':value}
def op(name,*args):return {'op':name,'args':list(args)}


def cmd(service,game,kind,payload,actor='admin',key=None):
    return service.command(game['id'],kind,payload,key or uuid4().hex,actor)


def manifest():
    accepted=op('filter',path('input.entries'),path('item.accepted'))
    score=op('sum',op('map',accepted,op('multiply',path('item.value'),path('item.value'))))
    return {'label':'临时创建的协作评估','description':'不是内置模块：有效记录的数值平方求和',
        'types':{'evaluation_record':{'schema':{'type':'object','properties':{
            'owner':{'type':'string'},'score':{'type':'number'},'entries':{'type':'array','items':{'type':'object','properties':{'accepted':{'type':'boolean'},'value':{'type':'number'}},'required':['accepted','value']}}},'required':['owner','score','entries'],'additionalProperties':False},
            'player_access':{'owner_field':'owner','read_fields':['entries','score']}}},
        'functions':{'evaluate_entries':{'schema':{'type':'object','properties':{'entries':{'type':'array'}},'required':['entries']},'result':score}},
        'interactions':{'evaluate_record':{'mode':'rule','player_enabled':True,'player_description':'重新计算自己记录的评估值',
            'schema':{'type':'object','properties':{'record':{'type':'string','x-reference':{'type_key':'evaluation_record'}}},'required':['record'],'additionalProperties':False},
            'effects':[{'type_key':'evaluation_record','id_from':'record','field':'score','operation':'compute',
                        'expression':{'call':'evaluate_entries','input':{'object':{'entries':path('current.entries')}}}}]}}}


def install(service,game,value=None,key='evaluation'):
    version=service.snapshot(game['id'])['game']['version']
    return cmd(service,game,'module.put',{'key':key,'version':version,'manifest':value or manifest()})


def record(service,game):
    return cmd(service,game,'entity.create',{'type_key':'evaluation_record','data':{'owner':'qq:1','score':0,'entries':[
        {'accepted':True,'value':2},{'accepted':True,'value':3},{'accepted':False,'value':100}]}})


def run(service,game,row,key=None):
    return cmd(service,game,'submit',{'interaction':'evaluate_record','data':{'record':row['id']}},actor='qq:1',key=key)


def test_runtime_module_creates_previously_unknown_function_and_interaction(service,game):
    result=install(service,game)
    assert result['module']['revision']==1 and result['version']==2
    row=record(service,game)
    run(service,game,row,'once')
    run(service,game,row,'once')
    snapshot=service.snapshot(game['id'])
    assert snapshot['entities'][0]['data']['score']==13
    assert len(snapshot['submissions'])==1
    # Persistence uses the definition, not a process-local registration.
    from mythweaver.service import Service
    restarted=Service(service.store)
    run(restarted,game,row)
    assert restarted.snapshot(game['id'])['entities'][0]['data']['score']==13


def test_module_replacement_changes_algorithm_without_platform_code(service,game):
    install(service,game)
    row=record(service,game)
    run(service,game,row)
    replacement=manifest()
    replacement['functions']['evaluate_entries']['result']=op('count',op('filter',path('input.entries'),path('item.accepted')))
    upgraded=install(service,game,replacement)
    assert upgraded['module']['revision']==2
    run(service,game,row)
    assert service.snapshot(game['id'])['entities'][0]['data']['score']==2
    restore_checkpoint(service,game['id'],upgraded['safety_checkpoint'],3,uuid4().hex)
    run(service,game,row)
    assert service.snapshot(game['id'])['entities'][0]['data']['score']==13


def test_disable_preserves_records_and_reenable_restores_actions(service,game):
    install(service,game)
    row=record(service,game)
    cmd(service,game,'module.state',{'key':'evaluation','version':2,'enabled':False})
    assert service.snapshot(game['id'])['entities'][0]['id']==row['id']
    with pytest.raises(DomainError) as error:run(service,game,row)
    assert error.value.code=='INTERACTION_UNAVAILABLE'
    cmd(service,game,'module.state',{'key':'evaluation','version':3,'enabled':True})
    run(service,game,row)
    assert service.snapshot(game['id'])['entities'][0]['data']['score']==13


def test_module_dependencies_block_disable_and_uninstall(service,game):
    install(service,game,{'label':'自定义函数库','functions':{'constant_result':{'result':literal(7)}}},key='library')
    install(service,game,{'label':'依赖库的功能','requires':['library'],'functions':{'dependent':{'result':{'call':'constant_result','input':{'object':{}}}}}},key='dependent')
    with pytest.raises(DomainError):
        cmd(service,game,'module.state',{'key':'library','version':3,'enabled':False})
    with pytest.raises(DomainError):
        cmd(service,game,'module.remove',{'key':'library','version':3})
    assert service.snapshot(game['id'])['game']['version']==3


def test_uninstall_requires_migrating_or_retiring_existing_data(service,game):
    install(service,game)
    row=record(service,game)
    with pytest.raises(DomainError) as error:
        cmd(service,game,'module.remove',{'key':'evaluation','version':2})
    assert error.value.code=='MIGRATION_REQUIRED'
    cmd(service,game,'entity.retire',{'id':row['id'],'version':1,'reason':'先退役模块数据'})
    cmd(service,game,'module.remove',{'key':'evaluation','version':2})
    assert service.snapshot(game['id'])['game']['definition']['modules']=={}


def test_incompatible_upgrade_rolls_back_definition_and_safety_checkpoint(service,game):
    install(service,game)
    record(service,game)
    with service.store.session() as db:
        before=db.scalar(select(func.count()).select_from(Checkpoint))
    value=manifest()
    value['types']['evaluation_record']['schema']['properties']['score']['type']='string'
    with pytest.raises(DomainError):install(service,game,value)
    assert service.snapshot(game['id'])['game']['version']==2
    with service.store.session() as db:
        assert db.scalar(select(func.count()).select_from(Checkpoint))==before


def test_module_cannot_override_other_module_member(service,game):
    install(service,game)
    with pytest.raises(DomainError) as error:
        install(service,game,manifest(),key='imposter')
    assert error.value.code=='INVALID_MODULE'


def test_expression_error_rolls_back_earlier_effects(service,game):
    value=manifest()
    value['interactions']['evaluate_record']['effects'].insert(0,{'type_key':'evaluation_record','id_from':'record','field':'score','operation':'set','value':99})
    value['functions']['evaluate_entries']['result']=op('divide',literal(1),literal(0))
    install(service,game,value)
    row=record(service,game)
    before=service.snapshot(game['id'])
    with pytest.raises(DomainError):run(service,game,row)
    after=service.snapshot(game['id'])
    assert before['entities']==after['entities'] and before['events']==after['events'] and after['submissions']==[]


@pytest.mark.parametrize('expression',[
    {'op':'eval','args':[literal('host code')]},
    {'path':'__class__.__base__'},
    {'call':'missing','input':{'object':{}}},
    {'op':'multiply','args':[literal(1)]},
])
def test_function_rejects_unknown_or_host_operations(expression):
    with pytest.raises(DomainError):check_expression(expression,{})


def test_functions_reject_recursive_calls_and_enforce_execution_budget():
    with pytest.raises(DomainError):
        validate_definition({'functions':{'recursive':{'result':{'call':'recursive','input':{'object':{}}}}}})
    expression=op('map',path('input.values'),op('map',path('input.values'),literal(1)))
    check_expression(expression,{})
    with pytest.raises(DomainError):
        Runtime({}).evaluate(expression,{'input':{'values':list(range(100))}})


def test_standalone_interaction_dependency_blocks_disabling_module_function(service,game):
    install(service,game)
    definition=deepcopy(service.snapshot(game['id'])['game']['definition'])
    definition['interactions']['base_action']=deepcopy(definition['interactions']['evaluate_record'])
    cmd(service,game,'definition',{'version':2,'definition':definition})
    with pytest.raises(DomainError) as error:
        cmd(service,game,'module.state',{'key':'evaluation','version':3,'enabled':False})
    assert error.value.code=='INVALID_FUNCTION'
    assert service.snapshot(game['id'])['game']['version']==3


def test_dynamic_function_cannot_change_player_record_ownership(service,game):
    value=manifest()
    value['interactions']['evaluate_record']['effects'][0]['field']='owner'
    value['interactions']['evaluate_record']['effects'][0]['expression']=literal('qq:2')
    install(service,game,value)
    row=record(service,game)
    cmd(service,game,'policy.update',{'version':0,'player_agent_enabled':True})
    with pytest.raises(DomainError) as error:run(service,game,row)
    assert error.value.code=='TARGET_DENIED'
    assert service.snapshot(game['id'])['entities'][0]['data']['owner']=='qq:1'


def test_agent_discovers_runtime_then_creates_new_module_and_player_uses_it(service,game,monkeypatch):
    real=httpx.AsyncClient
    requests=[]
    def handler(request):
        body=json.loads(request.content)
        requests.append(body)
        name='inspect_runtime' if len(requests)==1 else 'prepare_plan'
        if len(requests)==1:
            arguments={}
        else:
            assert 'multiply' in body['messages'][-1]['content']
            arguments={'explanation':'按管理员要求创建新的协作评估模块','commands':[{'kind':'module.put','payload':{'key':'evaluation','version':1,'manifest':manifest()}}]}
        return httpx.Response(200,json={'choices':[{'message':{'tool_calls':[{'id':str(len(requests)),'type':'function','function':{'name':name,'arguments':json.dumps(arguments)}}]}}]})
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(handler),**kw))
    settings=Settings(llm_base_url='https://provider.example/v1',llm_api_key='dummy',llm_model='test')
    result=asyncio.run(Agent(service,settings).propose(game['id'],'创建新模块：仅有效提交的数值平方求和；让我用自然语言调用',execute=True))
    assert result['status']=='applied' and len(requests)==2
    row=record(service,game)
    cmd(service,game,'policy.update',{'version':0,'player_agent_enabled':True})
    def player_handler(request):
        body=json.loads(request.content)
        assert any(m['role']=='user' and 'evaluate_record' in m['content'] for m in body['messages'])
        return httpx.Response(200,json={'choices':[{'message':{'tool_calls':[{'function':{'name':'submit_action','arguments':json.dumps({'interaction':'evaluate_record','data':{'record':row['id']}})}}]}}]})
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(player_handler),**kw))
    receipt=asyncio.run(PlayerAgent(service,settings).turn(game['id'],'qq:1','计算我的协作评估','new-module'))
    assert receipt['action']['status']=='applied' and receipt['records'][0]['data']['score']==13
    assert 'evaluate_entries' in read(service,game['id'],'inspect_runtime',{'function_keys':['evaluate_entries']})['functions']
