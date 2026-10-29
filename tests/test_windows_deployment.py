import json
from pathlib import Path
import zipfile

import pytest

from scripts import local,windows


@pytest.mark.parametrize('config,host',[({},'127.0.0.1'),({'API_HOST':'0.0.0.0'},'0.0.0.0'),({'API_HOST':'127.0.0.1'},'127.0.0.1')])
def test_api_network_binding_keeps_health_and_napcat_loopback(config,host,monkeypatch):
    monkeypatch.setattr(local,'load_config',lambda:{**config,'API_PORT':'18080'})
    assert local.api_host()==host
    assert local.api_url()=='http://127.0.0.1:18080'
    assert local.onebot_url()=='http://127.0.0.1:8080'


def test_invalid_api_host_is_rejected_before_start(monkeypatch):
    monkeypatch.setattr(local,'load_config',lambda:{'API_HOST':'http://example.com'})
    monkeypatch.setattr(local,'start_database',lambda:pytest.fail('Invalid binding must not start database'))
    with pytest.raises(SystemExit,match='API_HOST'):local.start()


def test_start_checks_both_listeners_and_launches_single_shared_process(tmp_path,monkeypatch):
    from types import SimpleNamespace
    import socket
    import urllib.request
    config={'API_HOST':'0.0.0.0','API_PORT':'18001','ONEBOT_PORT':'18080'}
    monkeypatch.setattr(local,'load_config',lambda:config)
    monkeypatch.setattr(local,'RUNTIME',tmp_path)
    monkeypatch.setattr(local,'start_database',lambda:None)
    monkeypatch.setattr(socket,'create_connection',lambda *args,**kw:(_ for _ in ()).throw(OSError()))
    processes=[];probes=[]
    def launch(args,**kwargs):
        processes.append(args);return SimpleNamespace(pid=12345,poll=lambda:None)
    monkeypatch.setattr(local.subprocess,'Popen',launch)
    class Reply:
        status=200
        def __init__(self,url):self.url=url
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self):return json.dumps({'ok':True,'service':'onebot'} if self.url.endswith('/health') and ':18080/' in self.url else {'ok':True}).encode()
    def open_url(url,**kwargs):probes.append(url);return Reply(url)
    monkeypatch.setattr(urllib.request,'build_opener',lambda *args:SimpleNamespace(open=open_url))
    local.start()
    assert len(processes)==1 and processes[0][-2:]==['-m','mythweaver.server']
    assert probes==['http://127.0.0.1:18001/api/health','http://127.0.0.1:18080/health']
    assert (tmp_path/'api.pid').read_text()=='12345'


def test_stop_recognizes_both_legacy_and_split_process_owners(tmp_path,monkeypatch):
    monkeypatch.setattr(local,'RUNTIME',tmp_path);(tmp_path/'api.pid').write_text('12345')
    commands=[];monkeypatch.setattr(local,'run',lambda args:commands.append(args))
    local.stop_api()
    assert 'mythweaver.api:create_app' in commands[0][-1] and '-m mythweaver.server' in commands[0][-1]
    assert not (tmp_path/'api.pid').exists()


def test_release_whitelist_excludes_machine_credentials_and_runtime(tmp_path,monkeypatch):
    root=tmp_path/'source';root.mkdir()
    for name,body in {'apps/web/dist/index.html':'built','apps/web/package.json':'{}','mythweaver/main.py':'code',
                      'scripts/setup.ps1':'setup','requirements.lock':'locked','.env':'secret',
                      'apps/web/.env.local':'frontend-secret','.runtime/pgdata/data':'private','.venv/pyvenv.cfg':'machine','NapCat.Shell.Windows.OneKey/login':'qq'}.items():
        path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(body)
    monkeypatch.setattr(windows,'ROOT',root)
    output=tmp_path/'release.zip';windows.package(output,False)
    with zipfile.ZipFile(output) as archive:
        names=archive.namelist()
        assert 'apps/web/dist/index.html' in names and 'scripts/setup.ps1' in names
        assert all(not n.startswith(('.env','.venv/','.runtime/','NapCat.')) for n in names)
        assert 'apps/web/.env.local' not in names
        assert json.loads(archive.read('release-manifest.json'))['sha256']['mythweaver/main.py']


def test_connection_config_uses_target_port_and_decodes_credentials():
    url,env,args=local.database_connection({'DATABASE_URL':'postgresql+psycopg://mythweaver:pass%40word@127.0.0.1:15543/other'})
    assert url.path=='/other' and env=={'PGPASSWORD':'pass@word'}
    assert args==['-h','127.0.0.1','-p','15543','-U','mythweaver']


def test_napcat_explicit_config_preserves_other_connections(tmp_path,monkeypatch):
    runtime=tmp_path/'runtime';runtime.mkdir()
    path=tmp_path/'onebot11.json'
    original={'network':{'websocketClients':[{'name':'another-service','token':'unrelated'}]},'other':42}
    path.write_text(json.dumps(original))
    monkeypatch.setattr(local,'RUNTIME',runtime)
    monkeypatch.setattr(local,'load_config',lambda:{'ONEBOT_BOT_ID':'123','ONEBOT_TOKEN':'new-token','API_PORT':'18080'})
    local.configure_napcat(path)
    result=json.loads(path.read_text())
    assert result['other']==42 and result['network']['websocketClients'][0]==original['network']['websocketClients'][0]
    assert result['network']['websocketClients'][1]['url']=='ws://127.0.0.1:8080/channels/onebot/v11/ws'
    local.configure_napcat(path)
    assert len(json.loads(path.read_text())['network']['websocketClients'])==2
    assert json.loads((runtime/'napcat-onebot.before-mythweaver.json').read_text())==original


def test_export_data_is_explicit_private_archive_without_old_local_passwords(tmp_path,monkeypatch):
    monkeypatch.setattr(local,'RUNTIME',tmp_path)
    monkeypatch.setattr(local,'load_config',lambda:{'DATABASE_URL':'postgresql+psycopg://mythweaver:database-pass@localhost:15543/game','SESSION_SECRET':'a'*64,'ADMIN_PASSWORD':'local-admin'})
    monkeypatch.setattr(local,'pg_bin',lambda:tmp_path)
    calls=[]
    def run(args,**kw):
        calls.append(args);Path(args[args.index('-f')+1]).write_bytes(b'dump')
    monkeypatch.setattr(local,'run',run)
    output=tmp_path/'private.zip';windows.export_data(output)
    assert '--schema=public' in calls[0]
    with zipfile.ZipFile(output) as archive:
        metadata=archive.read('migration.json').decode()
        assert 'a'*64 in metadata and 'local-admin' not in metadata and 'database-pass' not in metadata


def test_setup_defaults_have_no_original_account_or_path(tmp_path,monkeypatch):
    monkeypatch.setattr(local,'ROOT',tmp_path);monkeypatch.setattr(local,'RUNTIME',tmp_path/'.runtime')
    monkeypatch.setattr(local,'load_config',lambda:{})
    monkeypatch.setattr(local,'pg_bin',lambda:tmp_path/'missing')
    with pytest.raises(SystemExit,match='PostgreSQL'):local.setup()
    config=(tmp_path/'.env').read_text()
    assert 'ONEBOT_BOT_ID=0' in config and '1764692580' not in config
    assert 'API_PORT=8001' in config and 'ONEBOT_PORT=8080' in config


def test_setup_preserves_existing_configuration(tmp_path,monkeypatch):
    (tmp_path/'.env').write_text('original-config')
    monkeypatch.setattr(local,'ROOT',tmp_path);monkeypatch.setattr(local,'RUNTIME',tmp_path/'.runtime')
    monkeypatch.setattr(local,'load_config',lambda:{})
    monkeypatch.setattr(local,'pg_bin',lambda:tmp_path/'missing')
    with pytest.raises(SystemExit):local.setup(bot_id='555',api_port=18080)
    assert (tmp_path/'.env').read_text()=='original-config'


def test_import_invalid_archive_never_starts_or_changes_database(tmp_path,monkeypatch):
    import urllib.request
    class Offline:
        def open(self,*args,**kwargs):raise OSError('not running')
    monkeypatch.setattr(urllib.request,'build_opener',lambda *args:Offline())
    monkeypatch.setattr(local,'load_config',lambda:{})
    monkeypatch.setattr(local,'api_url',lambda:'http://127.0.0.1:18080')
    monkeypatch.setattr(local,'RUNTIME',tmp_path)
    monkeypatch.setattr(local,'start_database',lambda:pytest.fail('must not start database'))
    source=tmp_path/'bad.zip'
    with zipfile.ZipFile(source,'w') as archive:archive.writestr('../.env','invalid')
    with pytest.raises(SystemExit,match='Invalid migration archive'):windows.import_data(source)


def test_import_requires_platform_to_be_stopped_before_reading_backup(monkeypatch):
    from contextlib import nullcontext
    import urllib.request
    class Online:
        def open(self,*args,**kwargs):return nullcontext()
    monkeypatch.setattr(urllib.request,'build_opener',lambda *args:Online())
    monkeypatch.setattr(local,'load_config',lambda:{})
    monkeypatch.setattr(local,'api_url',lambda:'http://127.0.0.1:18080')
    with pytest.raises(SystemExit,match='Stop the target platform'):windows.import_data('not-opened.zip')
