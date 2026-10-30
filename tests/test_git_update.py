from pathlib import Path
import subprocess

import pytest

from scripts import update


@pytest.fixture
def repositories(tmp_path,monkeypatch):
    def git(folder,*args):
        return subprocess.run(['git','-C',str(folder),*args],check=True,capture_output=True,text=True).stdout.strip()
    publisher=tmp_path/'publisher';publisher.mkdir()
    git(publisher,'init','-b','main');git(publisher,'config','user.name','Update test');git(publisher,'config','user.email','test@example.invalid')
    (publisher/'code.txt').write_text('v1');git(publisher,'add','.');git(publisher,'commit','-m','initial')
    remote=tmp_path/'remote.git'
    subprocess.run(['git','clone','--bare',str(publisher),str(remote)],check=True,capture_output=True)
    deployed=tmp_path/'deployed'
    subprocess.run(['git','clone',str(remote),str(deployed)],check=True,capture_output=True)
    git(publisher,'remote','add','origin',str(remote))
    monkeypatch.setattr(update,'ROOT',deployed)
    return publisher,deployed,git


def test_update_fetches_tracking_branch_without_touching_worktree(repositories):
    publisher,deployed,git=repositories
    (publisher/'code.txt').write_text('v2');git(publisher,'commit','-am','next');git(publisher,'push','origin','main')
    old,target,changed=update.plan()
    assert old!=target and changed==['code.txt']
    assert (deployed/'code.txt').read_text()=='v1'


def test_update_refuses_local_edits(repositories):
    _,deployed,_=repositories
    (deployed/'code.txt').write_text('local change')
    with pytest.raises(SystemExit,match='Local code changes'):update.plan()
    assert (deployed/'code.txt').read_text()=='local change'


def test_update_refuses_diverged_history(repositories):
    publisher,deployed,git=repositories
    git(deployed,'config','user.name','Update test');git(deployed,'config','user.email','test@example.invalid')
    (deployed/'local.txt').write_text('local');git(deployed,'add','.');git(deployed,'commit','-m','local')
    (publisher/'remote.txt').write_text('remote');git(publisher,'add','.');git(publisher,'commit','-m','remote');git(publisher,'push','origin','main')
    with pytest.raises(SystemExit,match='diverged'):update.plan()


def test_update_same_revision_is_noop(repositories):
    old,target,changed=update.plan()
    assert old==target and not changed


def test_update_refuses_new_upstream_file_over_preserved_ignored_local_file(repositories):
    publisher,deployed,git=repositories
    (deployed/'.git/info/exclude').write_text('/notes.txt\n')
    (deployed/'notes.txt').write_text('private local notes')
    (publisher/'notes.txt').write_text('new upstream file')
    git(publisher,'add','notes.txt');git(publisher,'commit','-m','new file');git(publisher,'push','origin','main')
    with pytest.raises(SystemExit,match='冲突'):update.plan()
    assert (deployed/'notes.txt').read_text()=='private local notes'


def test_update_refuses_new_directory_over_ignored_local_file(repositories):
    publisher,deployed,git=repositories
    (deployed/'.git/info/exclude').write_text('/assets\n')
    (deployed/'assets').write_text('local file')
    (publisher/'assets').mkdir();(publisher/'assets/new.txt').write_text('upstream')
    git(publisher,'add','.');git(publisher,'commit','-m','new directory');git(publisher,'push','origin','main')
    with pytest.raises(SystemExit,match='冲突'):update.plan()
    assert (deployed/'assets').read_text()=='local file'


def test_fetch_failure_displays_original_error_and_does_not_stop_platform(repositories,monkeypatch,capsys):
    _,deployed,git=repositories
    git(deployed,'remote','set-url','origin',str(deployed/'missing-remote.git'))
    monkeypatch.setattr(update.runtime,'stop',lambda:pytest.fail('Fetch failure must not stop the platform'))
    monkeypatch.setattr(update.runtime,'start_database',lambda:pytest.fail('Fetch failure must not start a database'))
    assert update.main(['--no-start'])==1
    output=capsys.readouterr().err
    assert '尚未停止平台或替换代码' in output and 'does not appear to be a git repository' in output
    assert 'Traceback' not in output and 'does not appear to be a git repository' in (deployed/'.runtime/update-error.log').read_text(encoding='utf-8')
    assert (deployed/'code.txt').read_text()=='v1'


def test_git_error_masks_credentials_and_explains_network_failure():
    result=subprocess.CompletedProcess([],128,stdout='',stderr='fatal: unable to access https://user:secret@example.test/repo?token=private: Could not resolve host')
    message=update.git_error(('fetch','origin'),result)
    assert 'secret' not in message and 'private' not in message
    assert 'Could not resolve host' in message and '网络' in message


def test_unchecked_git_exit_status_is_preserved(monkeypatch):
    result=subprocess.CompletedProcess([],1,stdout='',stderr='not an ancestor')
    monkeypatch.setattr(update.subprocess,'run',lambda *args,**kwargs:result)
    assert update.git('merge-base','--is-ancestor','a','b',check=False).returncode==1
