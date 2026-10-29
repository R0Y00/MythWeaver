import asyncio,json
from copy import deepcopy
from datetime import timedelta
from uuid import uuid4
from types import SimpleNamespace
import httpx
import pytest
from sqlalchemy import select
from mythweaver.config import Settings
from mythweaver.db import PlayerChoice,Submission,Outbox,GameDocument,GameTrial,utcnow
from mythweaver.definitions import DomainError
from mythweaver.player import PlayerAgent,AUDIENCE,view
from mythweaver import quality,trials


def command(service,game,kind,payload):return service.command(game['id'],kind,payload,uuid4().hex)


@pytest.fixture
def market(service,game):
    definition={'types':{'account':{'label':'账户','schema':{'type':'object','properties':{
        'owner':{'type':'string'},'coins':{'type':'integer','minimum':0,'title':'余额'},'items':{'type':'array','items':{'type':'string'}},'secret':{'type':'string'}},
        'required':['owner','coins','items','secret'],'additionalProperties':False},'player_access':{'owner_field':'owner','read_fields':['coins','items'],'summary_fields':['coins']}}},
        'interactions':{'buy':{'label':'购买票券','mode':'rule','player_enabled':True,'group_enabled':True,
            'confirmation':{'prompt':'每张票3币，确认后扣除。','ttl_seconds':600},
            'schema':{'type':'object','properties':{'account':{'type':'string','x-reference':{'type_key':'account'}}},'required':['account'],'additionalProperties':False},
            'effects':[{'type_key':'account','id_from':'account','field':'coins','operation':'increment','value':-3}]}}}
    command(service,game,'definition',{'version':1,'definition':definition})
    command(service,game,'policy.update',{'version':0,'player_agent_enabled':True})
    record=command(service,game,'entity.create',{'type_key':'account','data':{'owner':'qq:1','coins':10,'items':['物品'+str(i) for i in range(200)],'secret':'HIDDEN'}})
    return definition,record


def player_model(monkeypatch,record,seen=None):
    async def request(self,context,history,prompt):
        if seen is not None:seen.append(prompt)
        if prompt in ('你好','确认之后再次确认'):return {'content':'你好！'}
        return {'tool_calls':[{'function':{'name':'submit_action','arguments':json.dumps({'interaction':'buy','data':{'account':record['id']}})}}]}
    monkeypatch.setattr(PlayerAgent,'request',request)


def test_confirmation_is_persistent_and_exactly_once_across_restarts(service,game,market,monkeypatch):
    _,record=market;seen=[];player_model(monkeypatch,record,seen)
    first=asyncio.run(PlayerAgent(service,Settings()).turn(game['id'],'qq:1','买票','offer',conversation_id='A'))
    assert first['action']['status']=='confirmation_required' and first['records'][0]['data']['coins']==10
    assert not service.snapshot(game['id'])['submissions']
    assert '余额：10 → 7' in first['answer']
    confirmed=asyncio.run(PlayerAgent(service,Settings()).turn(game['id'],'qq:1','确认','consent',conversation_id='A'))
    assert confirmed['action']['status']=='applied' and confirmed['records'][0]['data']['coins']==7
    repeated=asyncio.run(PlayerAgent(service,Settings()).turn(game['id'],'qq:1','确认','consent',conversation_id='A'))
    assert repeated==confirmed and seen==['买票']
    with service.store.session() as db:assert db.scalar(select(PlayerChoice)).status=='executed'


@pytest.mark.parametrize('reply,status',[('取消','cancelled'),('确认','expired')])
def test_cancel_and_expiry_never_deduct(service,game,market,monkeypatch,reply,status):
    _,record=market;player_model(monkeypatch,record)
    player=PlayerAgent(service,Settings())
    asyncio.run(player.turn(game['id'],'qq:1','买票','offer'))
    if status=='expired':
        with service.store.session.begin() as db:db.scalar(select(PlayerChoice)).expires_at=utcnow()-timedelta(seconds=1)
    result=asyncio.run(player.turn(game['id'],'qq:1',reply,'decision'))
    assert result['action']['status']==status and result['records'][0]['data']['coins']==10
    assert not service.snapshot(game['id'])['submissions']


def test_related_resource_change_invalidates_quote_but_unrelated_change_does_not(service,game,market,monkeypatch):
    _,record=market;player_model(monkeypatch,record);player=PlayerAgent(service,Settings())
    asyncio.run(player.turn(game['id'],'qq:1','买票','offer'))
    unrelated=command(service,game,'entity.create',{'type_key':'account','data':{'owner':'qq:2','coins':20,'items':[],'secret':'OTHER'}})
    result=asyncio.run(player.turn(game['id'],'qq:1','确认','first-confirm'))
    assert result['action']['status']=='applied'
    asyncio.run(player.turn(game['id'],'qq:1','买票','offer2'))
    current=next(e for e in service.snapshot(game['id'])['entities'] if e['id']==record['id'])
    command(service,game,'entity.update',{k:current[k] for k in ('id','type_key','data','version','visibility')})
    result=asyncio.run(player.turn(game['id'],'qq:1','确认','second-confirm'))
    assert result['action']['status']=='stale' and result['records'][0]['data']['coins']==7


def test_direct_submit_cannot_bypass_confirmation(service,game,market):
    _,record=market
    result=service.command(game['id'],'submit',{'interaction':'buy','data':{'account':record['id']}},'direct',actor='qq:1')
    assert result['status']=='confirmation_required' and not service.snapshot(game['id'])['submissions']
    assert service.snapshot(game['id'])['entities'][0]['data']['coins']==10


def test_consent_is_scoped_to_conversation_and_audience(service,game,market,monkeypatch):
    _,record=market;seen=[];player_model(monkeypatch,record,seen);player=PlayerAgent(service,Settings())
    asyncio.run(player.turn(game['id'],'qq:1','买票','offer',conversation_id='A'))
    other=asyncio.run(player.turn(game['id'],'qq:1','确认','other',conversation_id='B'))
    assert other['action']['status']=='confirmation_required'
    assert service.snapshot(game['id'])['entities'][0]['data']['coins']==10 and seen==['买票','确认']


def test_gm_decision_private_delivery_is_opt_in_atomic_and_not_duplicated(service,game,market):
    command(service,game,'policy.update',{'version':1,'gm_delivery':'private'})
    issue=service.command(game['id'],'submit',{'interaction':'_review','data':{'text':'请GM决定'}},'issue',actor='qq:1')
    with pytest.raises(DomainError) as exc:
        service.command(game['id'],'decide',{'id':issue['id'],'decision':'applied','result':'可以继续'},'decision')
    assert exc.value.code=='DELIVERY_UNBOUND' and service.snapshot(game['id'])['submissions'][0]['status']=='pending'
    command(service,game,'channel.bind',{'group_id':'private-1'})
    for _ in range(2):service.command(game['id'],'decide',{'id':issue['id'],'decision':'applied','result':'可以继续'},'decision')
    with service.store.session() as db:
        messages=list(db.scalars(select(Outbox)))
        assert len(messages)==1 and messages[0].group_id=='private-1' and '可以继续' in messages[0].text


def test_gm_review_resumes_same_actor_flow_and_notifies_once(service,game,market):
    definition,record=market
    definition=deepcopy(definition)
    schema={'type':'object','properties':{'account':{'type':'string','x-reference':{'type_key':'account'}}},'required':['account']}
    definition['workflows']={'reviewed':{'schema':schema,'start':'review','steps':{
        'review':{'kind':'manual','schema':{'type':'object','properties':{'amount':{'type':'integer'}},'required':['amount']},
            'locks':['input.account'],'effects':[{'type_key':'account','id_from':'input.account','field':'coins','operation':'increment','value_from':'answers.review.amount'}],'next':'end'},
        'end':{'kind':'end','result':'流程完成'}}}}
    definition['interactions']['start']={'mode':'flow','workflow':'reviewed','player_enabled':True,'schema':schema}
    command(service,game,'definition',{'version':2,'definition':definition})
    command(service,game,'policy.update',{'version':1,'gm_delivery':'private'})
    command(service,game,'channel.bind',{'group_id':'private-1'})
    started=service.command(game['id'],'submit',{'interaction':'start','data':{'account':record['id']}},'start',actor='qq:1')
    flow=service.snapshot(game['id'])['flows'][0]
    issue=service.command(game['id'],'submit',{'interaction':'_review','data':{'text':'请求推进'}},'issue',actor='qq:1')
    command(service,game,'decide',{'id':issue['id'],'decision':'applied','result':'同意增加5币并继续',
        'resume_flow':{'id':flow['id'],'version':flow['version'],'data':{'amount':5}}})
    snap=service.snapshot(game['id'])
    assert snap['flows'][0]['status']=='completed' and snap['entities'][0]['data']['coins']==15
    with service.store.session() as db:assert len(list(db.scalars(select(Outbox))))==1


def document(record):
    return {'key':'rules','revision':0,'title':'业务说明','source':'买票先确认。\n确认后扣3币。',
        'clauses':[{'key':'confirm','text':'买票先确认。','status':'implemented','implementations':[{'section':'interactions','key':'buy'}],'scenarios':['purchase']},
                   {'key':'cost','text':'确认后扣3币。','status':'implemented','implementations':[{'section':'interactions','key':'buy'}],'scenarios':['purchase']}],
        'scenarios':[{'key':'purchase','label':'确认前后余额','actor':'qq:1','audience':'private','messages':[
            {'message':'买票','expect_action':'confirmation_required','expect_records':[{'id':record['id'],'path':'/data/coins','expected':10}]},
            {'message':'确认','expect_action':'applied','expect_records':[{'id':record['id'],'path':'/data/coins','expected':7}]}]}]}


def test_requirements_history_and_current_business_acceptance(service,game,market,monkeypatch):
    _,record=market;player_model(monkeypatch,record);body=document(record)
    saved=command(service,game,'requirements.put',body)
    initial=quality.read(service,game['id'],'rules')
    assert saved['revision']==1 and not initial['acceptance']['ready']
    before=service.snapshot(game['id'])
    report=asyncio.run(quality.run_acceptance(service,Settings(),game['id'],{'key':'rules','revision':1}))
    assert report['acceptance']['ready'] and not report['unmapped_source']
    assert service.snapshot(game['id'])['entities']==before['entities']
    command(service,game,'policy.update',{'version':1,'player_instructions':'新规则说明'})
    assert not quality.read(service,game['id'],'rules')['acceptance']['current']
    body['revision']=1;body['source']+='\n未覆盖的新要求。'
    command(service,game,'requirements.put',body)
    report=quality.read(service,game['id'],'rules')
    assert report['unmapped_source']==['未覆盖的新要求。'] and len(report['revisions'])==2
    assert quality.read(service,game['id'],'rules',1)['content']['source']=='买票先确认。\n确认后扣3币。'


def test_unmapped_source_and_missing_implementation_cannot_be_accepted(service,game,market,monkeypatch):
    _,record=market;player_model(monkeypatch,record);body=document(record)
    body['source']+='遗漏规则。';body['clauses'][1]['implementations']=[{'section':'interactions','key':'missing'}]
    command(service,game,'requirements.put',body)
    report=asyncio.run(quality.run_acceptance(service,Settings(),game['id'],{'key':'rules','revision':1}))
    assert not report['acceptance']['ready'] and 'cost' in report['acceptance']['unverified'] and report['unmapped_source']


def test_business_finish_requires_real_current_acceptance_not_a_model_claim(service,game,market,monkeypatch):
    from mythweaver.harness import Harness
    from mythweaver.db import ToolExecution
    from mythweaver.task_checks import verify
    _,record=market;player_model(monkeypatch,record)
    command(service,game,'requirements.put',document(record))
    result=asyncio.run(quality.run_acceptance(service,Settings(),game['id'],{'key':'rules','revision':1}))
    harness=Harness(service,Settings());rid=harness.create(game['id'],'验收')
    with service.store.session.begin() as db:
        db.add(ToolExecution(run_id=rid,call_id='acceptance',name='acceptance_run',arguments={},status='completed',result={'ok':True,'value':result}))
        db.add(ToolExecution(run_id=rid,call_id='fake',name='record_progress',arguments={},status='completed',result={'ok':True,'value':result}))
    check={'label':'说明验收','call_id':'acceptance','path':'/value/acceptance/ready','expected':True}
    with service.store.session() as db:assert verify(db,rid,[check],'business_acceptance')
    command(service,game,'policy.update',{'version':1,'player_instructions':'changed'})
    with service.store.session() as db:
        with pytest.raises(DomainError) as error:verify(db,rid,[check],'business_acceptance')
        assert error.value.code=='BUSINESS_EVIDENCE_REQUIRED'


def test_persistent_trial_keeps_random_results_and_production_unchanged(service,game,market,monkeypatch):
    _,record=market;calls=[];player_model(monkeypatch,record,calls)
    original=service.snapshot(game['id'])
    lab=trials.create(service,game['id'],{'label':'测试确认'})
    lab=asyncio.run(trials.turn(service,Settings(),game['id'],{'id':lab['id'],'version':lab['version'],'actor':'qq:1','prompt':'买票','key':'a'}))
    assert lab['turns'][0]['result']['action']['status']=='confirmation_required'
    lab=asyncio.run(trials.turn(service,Settings(),game['id'],{'id':lab['id'],'version':lab['version'],'actor':'qq:1','prompt':'确认','key':'b'}))
    assert lab['turns'][1]['result']['records'][0]['data']['coins']==7 and calls==['买票']
    assert lab['changes'][0]['after']['coins']==7
    assert service.snapshot(game['id'])==original
    again=asyncio.run(trials.turn(service,Settings(),game['id'],{'id':lab['id'],'version':1,'actor':'qq:1','prompt':'确认','key':'b'}))
    assert again['version']==lab['version'] and calls==['买票']
    reset=trials.change(service,game['id'],{'id':lab['id'],'version':lab['version'],'action':'reset'})
    assert not reset['changes'] and not reset['turns'] and service.snapshot(game['id'])==original


def test_trial_rejects_stale_rules_without_replaying(service,game,market):
    lab=trials.create(service,game['id'],{})
    command(service,game,'policy.update',{'version':1,'player_instructions':'changed'})
    with pytest.raises(DomainError) as error:
        asyncio.run(trials.turn(service,Settings(),game['id'],{'id':lab['id'],'version':1,'actor':'qq:1','prompt':'你好','key':'a'}))
    assert error.value.code=='TRIAL_CONFIG_STALE'


def test_lazy_player_reads_are_paged_and_never_include_other_private_state(service,game,market):
    from mythweaver.player_reads import read_records
    from mythweaver.agent import fingerprint
    _,record=market
    other=command(service,game,'entity.create',{'type_key':'account','data':{'owner':'qq:2','coins':40,'items':['PRIVATE_OTHER'],'secret':'SECRET_OTHER'}})
    with service.store.session() as db:
        current=service.game(db,game['id']);context=view(service,db,current,'qq:1',compact=True)
        context.update(_scope={'game_id':game['id'],'actor':'qq:1'},state_stamp=fingerprint(db,current))
    assert context['records'][0]['data']=={'coins':10} and '物品' not in json.dumps(context,ensure_ascii=False)
    first=read_records(service,context,{'id':record['id'],'fields':['items','secret'],'page_size':3})
    assert first['records'][0]['data']=={'items':['物品0','物品1','物品2']}
    assert first['records'][0]['field_pages']['items']['next_offset']==3
    assert read_records(service,context,{'id':other['id']})['records']==[]
    token=AUDIENCE.set('group')
    try:assert read_records(service,context,{'id':record['id']})['records']==[]
    finally:AUDIENCE.reset(token)


def test_player_model_can_read_a_page_then_answer_without_executing(service,game,market,monkeypatch):
    _,record=market;requests=[]
    def handler(request):
        body=json.loads(request.content);requests.append(body)
        if len(requests)==1:
            assert '物品0' not in body['messages'][2]['content']
            return httpx.Response(200,json={'choices':[{'message':{'role':'assistant','tool_calls':[{'id':'read1','type':'function','function':{'name':'read_player_records','arguments':json.dumps({'id':record['id'],'fields':['items','secret'],'page_size':2})}}]}}]})
        assert body['messages'][-1]['role']=='tool'
        visible=json.loads(body['messages'][-1]['content'])
        assert visible['records'][0]['data']=={'items':['物品0','物品1']}
        assert 'HIDDEN' not in json.dumps(visible)
        return httpx.Response(200,json={'choices':[{'message':{'content':'前两项是物品0和物品1，后面还有更多。'}}]})
    real=httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(handler),**kw))
    result=asyncio.run(PlayerAgent(service,Settings(llm_base_url='https://example.test',llm_api_key='dummy',llm_model='test')).turn(game['id'],'qq:1','查看背包前两项','read'))
    assert result['action'] is None and result['reads']==[{'tool':'read_player_records','records':1,'ok':True}]
    assert len(requests)==2 and not service.snapshot(game['id'])['submissions']


def test_trial_blocks_plugins_by_default_and_only_runs_them_with_explicit_opt_in(service,game,market,monkeypatch):
    from mythweaver.plugins import Plugins
    definition,record=market;definition=deepcopy(definition)
    spec=definition['interactions']['buy'];spec.pop('confirmation');spec.pop('effects')
    spec['extension']={'plugin_key':'custom','tool':'play','reads':[{'type_key':'account','fields':['coins'],'scope':'own'}],
        'writes':[{'type_key':'account','fields':['coins'],'scope':'own'}]}
    command(service,game,'definition',{'version':2,'definition':definition})
    player_model(monkeypatch,record);calls=[]
    def invoke(self,*args,**kwargs):
        calls.append(True);return {'updates':[{'id':record['id'],'changes':{'coins':11}}],'feedback':'完成'}
    monkeypatch.setattr(Plugins,'invoke',invoke)
    lab=trials.create(service,game['id'],{})
    with pytest.raises(DomainError) as error:asyncio.run(trials.turn(service,Settings(),game['id'],{'id':lab['id'],'version':1,'actor':'qq:1','prompt':'行动','key':'a'}))
    assert error.value.code=='TRIAL_EXTERNAL_BLOCKED' and not calls
    lab=trials.create(service,game['id'],{'allow_external':True})
    lab=asyncio.run(trials.turn(service,Settings(),game['id'],{'id':lab['id'],'version':1,'actor':'qq:1','prompt':'行动','key':'a'}))
    assert calls==[True] and lab['changes'][0]['after']['coins']==11
    assert service.snapshot(game['id'])['entities'][0]['data']['coins']==10


def test_new_quality_routes_are_authenticated_and_trials_can_be_discarded(store,service,game,market,monkeypatch):
    from fastapi.testclient import TestClient
    from mythweaver.api import create_app
    settings=Settings()
    # No lifespan: the application worker/QQ pump must not run in this test.
    client=TestClient(create_app(settings,store));client.headers.update({'X-MythWeaver':'1'})
    base='/api/games/'+game['id']
    assert client.get(base+'/requirements').status_code==401
    assert client.post('/api/login',json={'password':settings.admin_password}).status_code==200
    assert client.get(base+'/requirements').json()=={'documents':[]}
    created=client.post(base+'/trials',json={'label':'网页试玩'}).json()
    assert client.get(base+'/trials').json()['trials'][0]['id']==created['id']
    response=client.post(base+'/trials/change',json={'id':created['id'],'version':1,'action':'discard'})
    assert response.json()=={'discarded':True} and client.get(base+'/trials').json()=={'trials':[]}


def test_missing_resource_invalidates_confirmation_without_execution(service,game,market,monkeypatch):
    _,record=market;player_model(monkeypatch,record);player=PlayerAgent(service,Settings())
    asyncio.run(player.turn(game['id'],'qq:1','买票','offer'))
    from mythweaver.db import Entity
    with service.store.session.begin() as db:db.delete(db.get(Entity,record['id']))
    result=asyncio.run(player.turn(game['id'],'qq:1','确认','confirm'))
    assert result['action']['status']=='stale' and not service.snapshot(game['id'])['submissions']


def test_player_read_round_budget_can_be_disabled(service):
    from mythweaver.platform_rules import read,update
    with service.store.session.begin() as db:
        assert read(db)['rules']['player_read_steps']==6
        assert update(db,0,{'player_read_steps':0})['rules']['player_read_steps']==0


@pytest.mark.parametrize('change',['participant','channel','routing','provider'])
def test_authorization_and_model_config_changes_invalidate_business_evidence(service,game,market,monkeypatch,change):
    from mythweaver.db import Participant,Channel,PlatformConfig,ModelProvider
    _,record=market;player_model(monkeypatch,record)
    command(service,game,'requirements.put',document(record))
    result=asyncio.run(quality.run_acceptance(service,Settings(),game['id'],{'key':'rules','revision':1}))
    assert result['acceptance']['ready']
    with service.store.session.begin() as db:
        if change=='participant':db.add(Participant(game_id=game['id'],actor='qq:1',blocked=True))
        elif change=='channel':db.add(Channel(game_id=game['id'],group_id='private-1'))
        elif change=='routing':db.add(PlatformConfig(key='model_routing',value={'player':{'model':'another-model'}}))
        else:db.add(ModelProvider(name='New provider',base_url='https://example.test',enabled=True))
    report=quality.read(service,game['id'],'rules')
    assert not report['acceptance']['current'] and not report['acceptance']['ready']
