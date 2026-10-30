"""Fast-forward a tracked Git branch, preserving local state and returning to server console."""
import argparse
import json
import re
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from scripts import local as runtime


class GitUpdateError(RuntimeError):pass


def safe_git_text(value):
    value=re.sub(r'(https?://)[^/\s@]+@',r'\1[credentials hidden]@',value)
    return re.sub(r'(?i)([?&](?:token|access_token|api_key|password)=)[^&\s]+',r'\1[hidden]',value)


def git_error(args,result):
    detail=safe_git_text((result.stderr or result.stdout or 'Git did not return an error description.').strip())
    lower=detail.lower()
    if any(s in lower for s in ('certificate','ssl')):
        hint='请检查系统时间、证书或代理证书配置；不要关闭 SSL 验证。'
    elif any(s in lower for s in ('could not resolve','failed to connect','timed out','connection reset','unable to access','proxy')):
        hint='请检查这台电脑访问 GitHub 的网络及 Git 代理设置。不要为了重试而关闭 SSL 验证，也不要清除未知的代理配置。'
    elif 'dubious ownership' in lower:
        hint='Git 不信任当前目录的所有者。确认这是自己的部署目录后，再按 Git 上面的提示仅信任该目录。'
    elif any(s in lower for s in ('authentication','repository not found','permission denied (publickey)','could not read username')):
        hint='请检查 origin 仓库地址及访问权限。公开仓库地址应为 https://github.com/R0Y00/MythWeaver.git 。'
    elif any(s in lower for s in ('index.lock','cannot lock','permission denied','access is denied')):
        hint='请检查目录写入权限及是否有其他 Git 操作正在进行；不要直接删除锁文件。'
    else:hint='请根据上面的 Git 原始错误处理。需要协助时发送这段错误，不要发送 .env、密码或 API key。'
    stage='拉取更新失败，尚未停止平台或替换代码。' if args and args[0]=='fetch' else 'Git 操作失败。'
    return f'{stage}\nGit 退出码：{result.returncode}\n\n{detail}\n\n{hint}'


def git(*args,check=True):
    try:result=subprocess.run(['git',*args],cwd=ROOT,text=True,encoding='utf-8',errors='replace',capture_output=True,check=False)
    except FileNotFoundError:raise GitUpdateError('找不到 Git。请安装 Git for Windows 后重新打开更新脚本。') from None
    if check and result.returncode:raise GitUpdateError(git_error(args,result))
    return result


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
    # Legacy adoption preserves additional local files outside Git. A future
    # upstream addition must not overwrite those files merely because ignored.
    for name in git('diff','--no-renames','--diff-filter=A','--name-only','-z',old,target).stdout.split('\0'):
        if not name:continue
        path=ROOT/name
        if not path.resolve().is_relative_to(ROOT.resolve()):
            raise SystemExit('Update contains a path outside this installation; no files were changed.')
        parents=[]
        current=path.parent
        while current!=ROOT:
            parents.append(current);current=current.parent
        if path.exists() or any(p.exists() and not p.is_dir() for p in parents):
            raise SystemExit('新增上游文件与本机保留文件冲突：'+name+'。请先备份并处理该文件；平台未停止，文件未覆盖。')
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


def main(arguments=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--no-start',action='store_true')
    options=parser.parse_args(arguments)
    try:update(options.no_start)
    except GitUpdateError as exc:
        message=str(exc)
        print(message,file=sys.stderr)
        try:
            folder=ROOT/'.runtime';folder.mkdir(exist_ok=True)
            log=folder/'update-error.log';log.write_text(message+'\n',encoding='utf-8')
            print('错误记录：'+str(log),file=sys.stderr)
        except OSError:pass
        return 1
    return 0


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    raise SystemExit(main())
