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
