"""Real Git operations against legacy-style installs; no production services involved."""
import json
import os
from pathlib import Path
import subprocess

import pytest

from scripts import adopt_git,update


def git(root,*args):
    return subprocess.run(['git','-C',str(root),*args],check=True,capture_output=True,text=True,encoding='utf-8').stdout.strip()


@pytest.fixture
def legacy(tmp_path,monkeypatch):
    # Test runners may place tmp_path inside the real project repository.
    monkeypatch.setenv('GIT_CEILING_DIRECTORIES',str(tmp_path.parent))
    publisher=tmp_path/'publisher';publisher.mkdir()
    git(publisher,'init','-b','main');git(publisher,'config','user.name','Adoption test');git(publisher,'config','user.email','test@example.invalid')
    sources={'.gitignore':'.runtime/\n.venv/\n.env\nNapCat*/\napps/web/dist/\n',
        'code.txt':'new code','scripts/local.py':'# new runtime','start-platform.bat':'new startup',
        'apps/web/package.json':'{}','requirements.lock':'# dependencies'}
    for name,value in sources.items():
        path=publisher/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(value,encoding='utf-8')
    git(publisher,'add','.');git(publisher,'commit','-m','new version')
    remote=tmp_path/'origin.git'
    subprocess.run(['git','clone','--bare',str(publisher),str(remote)],check=True,capture_output=True)
    git(publisher,'remote','add','origin',str(remote))
    root=tmp_path/'旧部署 with spaces';root.mkdir()
    old={'.env':b'PRIVATE_CONFIG=unchanged\r\nAPI_PORT=8001\r\n',
        '.venv/Scripts/python.exe':b'old environment',
        '.runtime/pgdata/keep.bin':b'old database files',
        '.runtime/plugins/example/venv/keep.bin':b'installed plugin environment',
        'NapCat.Shell.Windows.OneKey/account/login.json':b'QQ LOGIN STAYS LOCAL',
        'apps/web/dist/index.html':b'old built frontend',
        'code.txt':b'old customized code','scripts/local.py':b'# old runtime','start-platform.bat':b'old startup',
        'release-manifest.json':b'{"legacy":true}',
        '我的笔记 [a] #1!.txt':b'private local note'}
    for name,value in old.items():
        path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(value)
    events=[]
    monkeypatch.setattr(adopt_git,'prerequisites',lambda root:None)
    def backup(root,folder):
        events.append('backup');path=folder/'database.dump';path.write_bytes(b'logical backup');return str(path)
    monkeypatch.setattr(adopt_git,'backup_database',backup)
    monkeypatch.setattr(adopt_git,'install',lambda root,folder:events.append('install'))
    monkeypatch.setattr(adopt_git,'start',lambda root:events.append('start'))
    return root,publisher,remote,old,events


def test_connect_preserves_data_paths_and_local_extras_and_supports_next_git_update(legacy,monkeypatch):
    root,publisher,remote,old,events=legacy
    report=adopt_git.adopt(root,remote=str(remote))
    assert events==['backup','install','start'] and report['status']=='ready'
    assert (root/'code.txt').read_text()=='new code'
    for name in old:
        if name not in ('code.txt','scripts/local.py','start-platform.bat'):assert (root/name).read_bytes()==old[name]
    assert git(root,'status','--porcelain')==''
    assert git(root,'rev-parse','--abbrev-ref','--symbolic-full-name','@{upstream}')=='origin/main'
    assert '.env' not in git(root,'ls-files').splitlines()
    assert '我的笔记 [a] #1!.txt' in report['preserved_local_files']
    folder=Path(report['database_backup']).parent
    assert (folder/'previous.env').read_bytes()==old['.env']
    assert (folder/'code/code.txt').read_bytes()==old['code.txt']
    assert not (root/'.runtime/git-adoption/pending.json').exists()
    (publisher/'code.txt').write_text('next update',encoding='utf-8')
    git(publisher,'commit','-am','next');git(publisher,'push','origin','main')
    monkeypatch.setattr(update,'ROOT',root)
    before,target,changed=update.plan()
    assert before!=target and changed==['code.txt']
    git(root,'merge','--ff-only',target)
    assert (root/'code.txt').read_text()=='next update' and (root/'.env').read_bytes()==old['.env']


def test_failed_download_leaves_old_install_untouched(legacy):
    root,_,remote,old,events=legacy
    with pytest.raises(subprocess.CalledProcessError):adopt_git.adopt(root,remote=str(remote.parent/'missing.git'),no_start=True)
    assert not (root/'.git').exists() and events==[]
    for name,value in old.items():assert (root/name).read_bytes()==value


def test_failed_database_backup_never_replaces_code(legacy,monkeypatch):
    root,_,remote,old,events=legacy
    def fail(*args):raise adopt_git.AdoptionError('backup failed')
    monkeypatch.setattr(adopt_git,'backup_database',fail)
    with pytest.raises(adopt_git.AdoptionError,match='backup failed'):adopt_git.adopt(root,remote=str(remote),no_start=True)
    assert not (root/'.git').exists() and events==[]
    for name,value in old.items():assert (root/name).read_bytes()==value


def test_copy_failure_restores_old_files_and_removes_only_new_files(legacy,monkeypatch):
    root,_,remote,old,events=legacy
    original=adopt_git.shutil.copy2;failed=[]
    def copy(source,target,*args,**kwargs):
        if Path(source).name=='code.txt' and 'repository' in Path(source).parts and not failed:
            failed.append(True);raise OSError('simulated disk write error')
        return original(source,target,*args,**kwargs)
    monkeypatch.setattr(adopt_git.shutil,'copy2',copy)
    with pytest.raises(OSError,match='simulated'):adopt_git.adopt(root,remote=str(remote),no_start=True)
    assert not (root/'.git').exists() and events==['backup']
    for name,value in old.items():assert (root/name).read_bytes()==value
    assert not (root/'requirements.lock').exists()
    assert not (root/'.runtime/git-adoption/pending.json').exists()


def test_metadata_failure_also_restores_original_files(legacy,monkeypatch):
    root,_,remote,old,_=legacy
    monkeypatch.setattr(adopt_git,'exclude_local_files',lambda root:(_ for _ in ()).throw(OSError('metadata failure')))
    with pytest.raises(OSError):adopt_git.adopt(root,remote=str(remote),no_start=True)
    assert not (root/'.git').exists()
    for name,value in old.items():assert (root/name).read_bytes()==value


def test_build_failure_is_resumable_without_replacing_configuration(legacy,monkeypatch):
    root,_,remote,old,events=legacy
    def fail(*args):events.append('failed build');raise OSError('npm unavailable')
    monkeypatch.setattr(adopt_git,'install',fail)
    with pytest.raises(OSError):adopt_git.adopt(root,remote=str(remote),no_start=True)
    assert (root/'.git').is_dir() and (root/'.env').read_bytes()==old['.env']
    pending=root/'.runtime/git-adoption/pending.json'
    folder=root/json.loads(pending.read_text(encoding='utf-8'))['folder']
    assert json.loads((folder/'report.json').read_text(encoding='utf-8'))['status']=='setup_failed'
    revision=git(root,'rev-parse','HEAD')
    monkeypatch.setattr(adopt_git,'install',lambda root,folder:events.append('installed on retry'))
    report=adopt_git.adopt(root,remote=str(remote),no_start=True)
    assert report['status']=='ready' and not pending.exists() and git(root,'rev-parse','HEAD')==revision
    assert events==['backup','failed build','backup','installed on retry']
    assert (root/'.env').read_bytes()==old['.env']


def test_retry_does_not_overwrite_code_edited_after_failure(legacy,monkeypatch):
    root,_,remote,_,_=legacy
    def fail(*args):raise OSError('build failed')
    monkeypatch.setattr(adopt_git,'install',fail)
    with pytest.raises(OSError):adopt_git.adopt(root,remote=str(remote),no_start=True)
    (root/'code.txt').write_text('user edit after failure')
    with pytest.raises(adopt_git.AdoptionError,match='本地修改'):adopt_git.adopt(root,remote=str(remote),no_start=True)
    assert (root/'code.txt').read_text()=='user edit after failure'


def test_existing_git_install_is_not_reinitialized(legacy):
    root,_,remote,old,events=legacy
    (root/'.git').mkdir()
    with pytest.raises(adopt_git.AdoptionError,match='已经接入'):adopt_git.adopt(root,remote=str(remote),no_start=True)
    assert events==[] and (root/'code.txt').read_bytes()==old['code.txt']


def test_upstream_data_file_is_rejected_before_backup_or_copy(legacy):
    root,publisher,remote,old,events=legacy
    (publisher/'.env').write_text('unwanted source config')
    git(publisher,'add','-f','.env');git(publisher,'commit','-m','bad source');git(publisher,'push','origin','main')
    with pytest.raises(adopt_git.AdoptionError,match='数据目录'):adopt_git.adopt(root,remote=str(remote),no_start=True)
    assert events==[] and (root/'.env').read_bytes()==old['.env'] and not (root/'.git').exists()


def test_directory_collision_is_rejected_before_stopping_old_server(legacy):
    root,_,remote,_,events=legacy
    (root/'requirements.lock').mkdir()
    with pytest.raises(adopt_git.AdoptionError,match='目录冲突'):adopt_git.adopt(root,remote=str(remote),no_start=True)
    assert events==[] and not (root/'.git').exists()


def test_parent_repository_is_not_taken_over(legacy):
    root,_,remote,_,events=legacy
    git(root.parent,'init')
    with pytest.raises(adopt_git.AdoptionError,match='另一个 Git'):adopt_git.adopt(root,remote=str(remote),no_start=True)
    assert events==[] and not (root/'.git').exists()


def test_path_boundary_rejects_traversal_and_windows_junction(tmp_path):
    root=(tmp_path/'install');root.mkdir()
    outside=tmp_path/'outside';outside.mkdir();(outside/'keep.txt').write_text('unchanged')
    with pytest.raises(adopt_git.AdoptionError):adopt_git.inside(root,'../outside/keep.txt')
    if os.name=='nt':
        result=subprocess.run(['cmd','/c','mklink','/J',str(root/'linked'),str(outside)],capture_output=True)
        assert result.returncode==0
        with pytest.raises(adopt_git.AdoptionError):adopt_git.inside(root,'linked/keep.txt')
        assert (outside/'keep.txt').read_text()=='unchanged'


def test_database_backup_helper_uses_existing_config_and_always_stops(tmp_path):
    root=tmp_path/'legacy';folder=root/'.runtime/git-adoption/test';folder.mkdir(parents=True)
    scripts=root/'scripts';scripts.mkdir();(scripts/'__init__.py').write_text('')
    venv=root/'.venv/Scripts';venv.mkdir(parents=True)
    # Run the actual helper in a fresh Python process; the fake runtime records effects.
    (scripts/'local.py').write_text('''from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
def log(text):
    with (ROOT/'calls.txt').open('a') as f:f.write(text+'\\n')
def stop():log('stop')
def start_database():log('start database')
def backup():
    log('backup');target=ROOT/'.runtime/database.dump';target.write_bytes(b'backup');return target
''',encoding='utf-8')
    import sys
    original=adopt_git.python
    adopt_git.python=lambda root:Path(sys.executable)
    try:path=adopt_git.backup_database(root,folder)
    finally:adopt_git.python=original
    assert Path(path).read_bytes()==b'backup'
    assert (root/'calls.txt').read_text().splitlines()==['stop','start database','backup','stop']


@pytest.mark.parametrize('config,valid',[
    ({'API_PORT':'8001','ONEBOT_PORT':'8080'},True),
    ({'API_PORT':'8080','ONEBOT_PORT':None},False),
    ({'API_PORT':'8001','ONEBOT_PORT':'8001'},False),
    ({'API_PORT':'not-a-port'},False),
    ({'API_PORT':'70000'},False),
    ({'API_PORT':None,'ONEBOT_PORT':None},True),
])
def test_port_compatibility_is_checked_without_rewriting_config(tmp_path,monkeypatch,config,valid):
    from types import SimpleNamespace
    env=tmp_path/'.env';env.write_bytes(b'original config bytes')
    monkeypatch.setattr(adopt_git,'run',lambda *a,**k:SimpleNamespace(stdout=json.dumps(config).encode()))
    if valid:adopt_git.check_ports(tmp_path)
    else:
        with pytest.raises(adopt_git.AdoptionError):adopt_git.check_ports(tmp_path)
    assert env.read_bytes()==b'original config bytes'
