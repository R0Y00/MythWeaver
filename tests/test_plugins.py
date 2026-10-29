import asyncio
from copy import deepcopy
import io
import json
from pathlib import Path
import tarfile
import time
from uuid import uuid4
import zipfile

import httpx
import pytest
from sqlalchemy import select

from mythweaver.config import Settings
from mythweaver.db import AgentRun,HarnessTask,ToolExecution
from mythweaver.definitions import DomainError
from mythweaver.harness import Harness,context_window
from mythweaver.plugins import Plugins,PluginFailure,validate_plugin


@pytest.fixture
def manager(service,tmp_path):
    service._plugins=Plugins(service,tmp_path/'plugins')
    return service.plugins


def plugin(runtime='python',source=None,requirements=None):
    file='tools.py' if runtime=='python' else 'tools.mjs'
    source=source or ('def calculate(data):\n    return sum(i*i for i in range(data["n"]))\n' if runtime=='python' else 'export async function calculate(data) { let value=0; for(let i=0;i<data.n;i++) value+=i*i; return value; }')
    manifest={'runtime':runtime,'requirements':requirements or [],'tools':{'calculate':{'entry':file+':calculate',
        'description':'由代码实现的工具','input_schema':{'type':'object','properties':{'n':{'type':'integer','minimum':0}},'required':['n'],'additionalProperties':False},
        'output_schema':{'type':'integer'},'player_enabled':True}},'tests':[{'tool':'calculate','input':{'n':5},'expected':30}]}
    return manifest,{file:source}


def installed(manager,game,runtime='python',**kwargs):
    manifest,files=plugin(runtime,**kwargs)
    draft=manager.draft(game['id'],'calculator',manifest,files)
    result=manager.install(game['id'],draft['id'])
    assert result['status']=='ready' and result['enabled']
    return draft


@pytest.mark.parametrize('timeout',[0,600])
def test_plugin_timeout_can_be_unlimited_or_longer_than_sixty_seconds(timeout):
    manifest,files=plugin();manifest['timeout']=timeout
    assert validate_plugin('calculator',manifest,files)[0]['timeout']==timeout


def test_unlimited_plugin_timeout_still_executes_and_returns_output(tmp_path):
    import sys
    from mythweaver.plugins import process
    stdout,_=process([sys.executable,'-c','print("completed")'],tmp_path,timeout=0)
    assert stdout.strip()=='completed'


@pytest.mark.parametrize('runtime',['python','node'])
def test_arbitrary_code_install_test_and_dynamic_tool_registration(manager,service,game,runtime):
    draft=installed(manager,game,runtime)
    assert manager.invoke(game['id'],'calculator','calculate',{'n':4})==14
    assert manager.tool_specs(game['id'])[0]['function']['name']=='ext_calculator__calculate'
    assert service.snapshot(game['id'])['game']['version']==2
    assert manager.inspect(game['id'],draft['id'])['report']['tests'][0]['passed']


def test_python_real_offline_dependency_installation(manager,game,tmp_path):
    wheel=tmp_path/'mw_plugin_fixture-1.0.0-py3-none-any.whl'
    with zipfile.ZipFile(wheel,'w') as archive:
        archive.writestr('mw_plugin_fixture.py','def calculate(n):\n    return n * 6\n')
        archive.writestr('mw_plugin_fixture-1.0.0.dist-info/METADATA','Metadata-Version: 2.1\nName: mw-plugin-fixture\nVersion: 1.0.0\n')
        archive.writestr('mw_plugin_fixture-1.0.0.dist-info/WHEEL','Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n')
        archive.writestr('mw_plugin_fixture-1.0.0.dist-info/RECORD','')
    installed(manager,game,source='from mw_plugin_fixture import calculate as calc\ndef calculate(data):\n    return calc(data["n"])\n',requirements=[str(wheel)])
    assert manager.invoke(game['id'],'calculator','calculate',{'n':7})==42


def test_node_real_offline_dependency_installation(manager,game,tmp_path):
    package=tmp_path/'mw-plugin-fixture.tgz'
    with tarfile.open(package,'w:gz') as archive:
        files={'package/package.json':json.dumps({'name':'mw-plugin-fixture','version':'1.0.0','type':'module','exports':'./index.js'}),
               'package/index.js':'export function calculate(n) { return n * 6; }'}
        for name,source in files.items():
            data=source.encode();info=tarfile.TarInfo(name);info.size=len(data);archive.addfile(info,io.BytesIO(data))
    installed(manager,game,'node',source='import {calculate as calc} from "mw-plugin-fixture"; export function calculate(data) {return calc(data.n);}',requirements=[str(package)])
    assert manager.invoke(game['id'],'calculator','calculate',{'n':7})==42


def test_failed_upgrade_keeps_previous_release_and_can_switch_back(manager,game):
    old=installed(manager,game)
    manifest,files=plugin(source='def calculate(data):\n    return -1\n')
    failed=manager.draft(game['id'],'calculator',manifest,files)
    with pytest.raises(PluginFailure):manager.install(game['id'],failed['id'])
    assert manager.inspect(game['id'],failed['id'])['status']=='failed'
    assert manager.inspect(game['id'],old['id'])['enabled']
    assert manager.invoke(game['id'],'calculator','calculate',{'n':4})==14
    manifest['tests'][0]['expected']=-1
    new=manager.draft(game['id'],'calculator',manifest,files)
    manager.install(game['id'],new['id'])
    assert manager.invoke(game['id'],'calculator','calculate',{'n':4})==-1
    manager.state(game['id'],old['id'],True)
    assert manager.invoke(game['id'],'calculator','calculate',{'n':4})==14
    manager.state(game['id'],old['id'],False)
    with pytest.raises(DomainError):manager.invoke(game['id'],'calculator','calculate',{'n':4})


def test_plugin_does_not_inherit_platform_secrets_and_source_is_pinned(manager,game):
    manifest,files=plugin(source='import os\ndef calculate(data):\n    return 30 if not any(k in os.environ for k in ("DATABASE_URL","SESSION_SECRET","ONEBOT_TOKEN")) else 0\n')
    draft=manager.draft(game['id'],'calculator',manifest,files)
    manager.install(game['id'],draft['id'])
    with manager.store.session() as db:
        row=manager.release(db,game['id'],draft['id'])
        (manager.folder(row)/'src'/'tools.py').write_text('def calculate(data): return 999',encoding='utf-8')
    with pytest.raises(PluginFailure) as error:manager.invoke(game['id'],'calculator','calculate',{'n':5})
    assert error.value.code=='PLUGIN_MODIFIED'


@pytest.mark.parametrize('file',['../escape.py','/escape.py','C:/escape.py','a\\escape.py','con.py','a/../escape.py'])
def test_plugin_files_cannot_escape_artifact_directory(file):
    manifest,files=plugin()
    files={file:next(iter(files.values()))}
    manifest['tools']['calculate']['entry']=file+':calculate'
    with pytest.raises(DomainError):validate_plugin('calculator',manifest,files)


def test_timeout_terminates_plugin_and_does_not_enable_failed_release(manager,game):
    manifest,files=plugin(source='import time\ndef calculate(data):\n    while True: time.sleep(.1)\n')
    manifest['timeout']=1
    draft=manager.draft(game['id'],'calculator',manifest,files)
    started=time.monotonic()
    with pytest.raises(PluginFailure) as error:manager.install(game['id'],draft['id'])
    assert error.value.code=='PLUGIN_TIMEOUT' and time.monotonic()-started<10
    assert not manager.inspect(game['id'],draft['id'])['enabled']


def test_plugin_result_can_update_only_authorized_records_through_rules(manager,service,game):
    installed(manager,game)
    effect={'type_key':'custom','id_from':'target','field':'value','operation':'plugin','plugin_key':'calculator','tool':'calculate',
            'expression':{'object':{'n':{'path':'current.n'}}}}
    definition={'types':{'custom':{'schema':{'type':'object','properties':{'owner':{'type':'string'},'n':{'type':'integer'},'value':{'type':'integer'}},'required':['owner','n','value']},
        'player_access':{'owner_field':'owner','read_fields':['n','value']}}},
        'interactions':{'calculate':{'mode':'rule','player_enabled':True,'schema':{'type':'object','properties':{'target':{'type':'string'}},'required':['target']},'effects':[effect]}}}
    service.command(game['id'],'definition',{'version':2,'definition':definition},uuid4().hex)
    own=service.command(game['id'],'entity.create',{'type_key':'custom','data':{'owner':'qq:1','n':4,'value':0}},uuid4().hex)
    other=service.command(game['id'],'entity.create',{'type_key':'custom','data':{'owner':'qq:2','n':5,'value':0}},uuid4().hex)
    service.command(game['id'],'policy.update',{'version':0,'player_agent_enabled':True},uuid4().hex)
    service.command(game['id'],'submit',{'interaction':'calculate','data':{'target':own['id']}},uuid4().hex,actor='qq:1')
    with pytest.raises(DomainError):
        service.command(game['id'],'submit',{'interaction':'calculate','data':{'target':other['id']}},uuid4().hex,actor='qq:1')
    assert next(e for e in service.snapshot(game['id'])['entities'] if e['id']==own['id'])['data']['value']==14
    other_game=service.create_game('另一个空间','')
    with pytest.raises(DomainError):manager.invoke(other_game['id'],'calculator','calculate',{'n':4})


def test_harness_repairs_failed_code_installs_and_uses_new_tool(manager,service,game,monkeypatch):
    harness=Harness(service,Settings())
    manifest,files=plugin(source='def calculate(data):\n    return -1\n')
    calls=[]
    async def request(messages,tools):
        index=len(calls);calls.append(messages)
        def call(name,args):return {'tool_calls':[{'id':f'call-{index}','type':'function','function':{'name':name,'arguments':json.dumps(args)}}]}
        previous=json.loads(messages[-1]['content']) if messages[-1]['role']=='tool' else None
        if index==0:return call('plugin_draft',{'key':'calculator','manifest':manifest,'files':files})
        if index==1:return call('plugin_install',{'id':previous['value']['id']})
        if index==2:
            assert not previous['ok'] and previous['code']=='PLUGIN_TEST_FAILED'
            fixed=plugin()[1]
            return call('plugin_draft',{'key':'calculator','manifest':manifest,'files':fixed})
        if index==3:return call('plugin_install',{'id':previous['value']['id']})
        if index==4:
            assert any(t['function']['name']=='ext_calculator__calculate' for t in tools)
            return call('ext_calculator__calculate',{'n':4})
        assert previous['ok'] is True and previous['value']==14
        assert previous['receipt']['call_id']=='call-4'
        return {'content':'代码已修复，安装和验证通过，实际调用返回14。'}
    monkeypatch.setattr(harness,'request',request)
    run_id=harness.create(game['id'],'创建并测试一个代码工具，失败时修复后继续执行',approval_mode='auto')
    result=asyncio.run(harness.run(game['id'],run_id))
    assert result['status']=='answered' and len(calls)==6
    assert len(result['executions'])==5 and result['executions'][1]['result']['ok'] is False
    assert result['executions'][-1]['result']['value']==14


def test_harness_resume_preserves_completed_work_and_blocks_unknown_side_effects(manager,service,game,monkeypatch):
    harness=Harness(service,Settings())
    run_id=harness.create(game['id'],'记录任务进度')
    async def request(messages,tools):return {'content':'本轮完成'}
    monkeypatch.setattr(harness,'request',request)
    asyncio.run(harness.run(game['id'],run_id))
    harness.resume(game['id'],run_id,'继续下一步')
    assert asyncio.run(harness.run(game['id'],run_id))['step']==2
    with service.store.session.begin() as db:
        db.add(ToolExecution(run_id=run_id,call_id='unknown-call',name='external',arguments={},status='unknown'))
    with pytest.raises(DomainError) as error:harness.resume(game['id'],run_id)
    assert error.value.code=='RECONCILIATION_REQUIRED'


def test_harness_cancellation_terminates_active_plugin_process(manager,service,game,monkeypatch):
    manifest,files=plugin(source='import time\ndef calculate(data):\n    if data["n"]==99: time.sleep(60)\n    return 30\n')
    manifest['timeout']=60
    draft=manager.draft(game['id'],'calculator',manifest,files);manager.install(game['id'],draft['id'])
    harness=Harness(service,Settings())
    async def request(messages,tools):
        return {'tool_calls':[{'id':'slow','type':'function','function':{'name':'ext_calculator__calculate','arguments':'{"n":99}'}}]}
    monkeypatch.setattr(harness,'request',request)
    async def scenario():
        run_id=harness.create(game['id'],'执行一个可停止的工具',approval_mode='auto')
        task=asyncio.create_task(harness.run(game['id'],run_id))
        for _ in range(100):
            await asyncio.sleep(.02)
            if harness.inspect(game['id'],run_id)['executions']:break
        harness.cancel(game['id'],run_id)
        result=await asyncio.wait_for(task,5)
        assert result['status']=='cancelled' and result['executions'][0]['status']=='unknown'
    asyncio.run(scenario())


def test_context_compaction_keeps_complete_tool_groups():
    messages=[{'role':'system','content':'system'},{'role':'user','content':'metadata'},{'role':'user','content':'objective'}]
    for i in range(20):
        messages.extend([{'role':'assistant','tool_calls':[{'id':str(i)}],'content':None},
                         {'role':'tool','tool_call_id':str(i),'content':json.dumps({'ok':True,'value':{'id':str(i),'blob':'x'*10000}})}])
    compact=context_window(messages)
    assert len(json.dumps(compact))<120000 and compact[:3]==messages[:3]
    calls={c['id'] for m in compact if m['role']=='assistant' for c in m.get('tool_calls',[])}
    assert all(m['tool_call_id'] in calls for m in compact if m['role']=='tool')


def test_plugin_environment_can_be_rebuilt_from_database_source(manager,game):
    draft=installed(manager,game)
    with manager.store.session() as db:
        row=manager.release(db,game['id'],draft['id'])
        path=manager.folder(row)/'src'/'tools.py'
        path.unlink()
    manager.reconcile()
    assert manager.inspect(game['id'],draft['id'])['status']=='environment_missing'
    manager.install(game['id'],draft['id'])
    assert manager.invoke(game['id'],'calculator','calculate',{'n':4})==14


def test_plugin_and_harness_http_are_admin_only_and_scoped(manager,service,store,game,monkeypatch):
    from fastapi.testclient import TestClient
    from mythweaver.api import create_app
    settings=Settings()
    app=create_app(settings,store)
    app.state.service._plugins=manager
    async def request(self,messages,tools):return {'content':'已检查授权范围，本轮没有安装或执行插件。'}
    monkeypatch.setattr(Harness,'request',request)
    with TestClient(app) as client:
        client.headers['x-mythweaver']='1'
        base=f"/api/games/{game['id']}"
        manifest,files=plugin()
        assert client.post(base+'/plugins',json={'key':'calculator','manifest':manifest,'files':files}).status_code==401
        client.post('/api/login',json={'password':settings.admin_password}).raise_for_status()
        draft=client.post(base+'/plugins',json={'key':'calculator','manifest':manifest,'files':files})
        assert draft.status_code==200
        result=client.post(base+'/plugins/install',json={'id':draft.json()['id']})
        assert result.status_code==200 and result.json()['enabled']
        other=service.create_game('其他空间','')
        assert client.get(f"/api/games/{other['id']}/plugins/{draft.json()['id']}").status_code==404
        started=client.post(base+'/agent/tasks',json={'prompt':'只检查当前工具，不执行变更'})
        assert started.status_code==200 and started.json()['kind']=='harness'
        task_id=started.json()['id']
        for _ in range(50):
            detail=client.get(base+'/agent/tasks/'+task_id).json()
            if detail['status']!='running':break
            time.sleep(.02)
        assert detail['status']=='answered'
        assert client.get(f"/api/games/{other['id']}/agent/tasks/{task_id}").status_code==404


def test_registered_dispatch_handles_underscores_without_parsing_alias(manager,game):
    manifest,files=plugin()
    draft=manager.draft(game['id'],'custom__calculator',manifest,files)
    manager.install(game['id'],draft['id'])
    assert manager.invoke_registered(game['id'],'ext_custom__calculator__calculate',{'n':4})==14
    manager.state(game['id'],draft['id'],False)
    with pytest.raises(DomainError):manager.invoke_registered(game['id'],'ext_custom__calculator__calculate',{'n':4})
