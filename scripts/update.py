"""Fast-forward a tracked Git branch, preserving local state and returning to server console."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from scripts import local as runtime


def git(*args,check=True):
    return subprocess.run(['git',*args],cwd=ROOT,text=True,encoding='utf-8',errors='replace',capture_output=True,check=check)


def plan():
    if Path(git('rev-parse','--show-toplevel').stdout.strip()).resolve()!=ROOT.resolve():
        raise SystemExit('Run updates from a Git clone of this project, not inside another repository.')
    if git('status','--porcelain','--untracked-files=normal').stdout.strip():
        raise SystemExit('Local code changes detected. Commit or preserve them before updating; no files were reset.')
    branch=git('symbolic-ref','--short','HEAD').stdout.strip()
    remote=git('config','--get','branch.'+branch+'.remote').stdout.strip()
    if not remote or remote=='.':raise SystemExit('Configure a remote tracking branch first (git push -u / git clone).')
    git('fetch','--prune',remote)
    old=git('rev-parse','HEAD').stdout.strip();target=git('rev-parse','@{upstream}').stdout.strip()
    if git('merge-base','--is-ancestor',old,target,check=False).returncode:
        raise SystemExit('Local and remote branches diverged. Resolve the Git history manually; no force reset was performed.')
    changed=git('diff','--name-only',old,target).stdout.splitlines()
    return old,target,changed


def console_running():
    import msvcrt
    path=runtime.RUNTIME/'console.lock'
    if not path.exists():return False
    with path.open('r+b') as stream:
        try:msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
        except OSError:return True
        msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1);return False


def update(no_start=False):
    old,target,changed=plan()
    if old==target:
        print('Already up to date. Start the server with start-platform.bat.');return
    rebuild=any(p.startswith('apps/web/') for p in changed) or not (ROOT/'apps/web/dist/index.html').exists()
    if rebuild and (not shutil.which('node') or not shutil.which('npm.cmd')):
        raise SystemExit('Frontend changed: install Node.js 20.19+ or 22.12+ before updating. Current server was not stopped.')
    folder=runtime.RUNTIME/'updates'/str(time.time_ns());folder.mkdir(parents=True)
    report={'from':old,'to':target,'status':'preparing','backup':None}
    def save():(folder/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    save()
    try:
        print('Stopping the current server console for update...',flush=True)
        from scripts.console import request_stop
        request_stop()
        deadline=time.monotonic()+20
        while console_running():
            if time.monotonic()>deadline:raise RuntimeError('Server console has not exited; close it before retrying.')
            time.sleep(.25)
        runtime.start_database();report['backup']=str(runtime.backup());save()
        git('merge','--ff-only',target)
        report['status']='code_updated';save()
        if 'requirements.lock' in changed:
            from mythweaver.package_sources import pip_arguments
            runtime.run([sys.executable,'-m','ensurepip'])
            runtime.run([sys.executable,'-m','pip','--isolated','install','--disable-pip-version-check',*pip_arguments(),'-r',ROOT/'requirements.lock'])
        if rebuild:
            npm=shutil.which('npm.cmd')
            cli=Path(npm).parent/'node_modules/npm/bin/npm-cli.js'
            if not cli.is_file():raise RuntimeError('Cannot locate npm CLI; repair the Node.js installation.')
            node=shutil.which('node')
            subprocess.run([node,str(cli),'ci'],cwd=ROOT/'apps/web',check=True)
            subprocess.run([node,str(cli),'run','build'],cwd=ROOT/'apps/web',check=True)
        report['status']='updated';save()
        print('Update completed: '+target[:12]+'. Backup/report: '+str(folder),flush=True)
    except BaseException:
        report['status']='failed';save()
        print('Update failed. No automatic database downgrade was attempted. Backup/report: '+str(folder),file=sys.stderr)
        raise
    finally:
        # No detached database is left behind while handing over to the console.
        runtime.stop()
    if not no_start:
        subprocess.run([sys.executable,str(ROOT/'scripts/console.py')],cwd=ROOT,check=True)


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--no-start',action='store_true')
    update(parser.parse_args().no_start)
