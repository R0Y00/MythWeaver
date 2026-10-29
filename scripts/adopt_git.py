"""Connect an existing Windows installation to Git without moving its data or paths.

This file deliberately uses only the standard library so old deployments can run it.
It can be downloaded and executed outside the repository; --root is always explicit.
"""
import argparse
import json
import os
from pathlib import Path,PurePosixPath
import shutil
import subprocess
import sys
import time
from uuid import uuid4

REMOTE='https://github.com/R0Y00/MythWeaver.git'
PROTECTED=['/.runtime/','/.venv/','/.env','/.env.*','/NapCat*/']


class AdoptionError(Exception):pass


def run(args,check=True,**kwargs):
    env=dict(os.environ)
    for key in ('GIT_DIR','GIT_WORK_TREE','GIT_INDEX_FILE','GIT_OBJECT_DIRECTORY','GIT_COMMON_DIR','GIT_ALTERNATE_OBJECT_DIRECTORIES'):
        env.pop(key,None)
    return subprocess.run([str(a) for a in args],env=env,check=check,**kwargs)


def git(root,*args):
    return run(['git','-C',root,*args],capture_output=True).stdout.decode('utf-8').strip('\r\n')


def inside(root,name):
    """Reject traversal, symlinks/junctions and directory/file collisions up front."""
    relative=PurePosixPath(name)
    if not name or relative.is_absolute() or any(part in ('','..','.') or ':' in part or '\\' in part for part in relative.parts):
        raise AdoptionError('仓库包含不支持的路径；没有替换本地文件。')
    path=root.joinpath(*relative.parts)
    if not path.resolve().is_relative_to(root):raise AdoptionError('路径超出安装目录；操作停止。')
    for item in [path,*path.parents]:
        if item==root:break
        if item.is_symlink() or (hasattr(item,'is_junction') and item.is_junction()):
            raise AdoptionError('目标路径含符号链接或目录联接；操作停止：'+name)
    return path


def protected(name):
    first=PurePosixPath(name).parts[0].casefold()
    return first in ('.git','.runtime','.venv') or first.startswith('napcat') or (first.startswith('.env') and first!='.env.example')


def save(path,value):
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    temporary.replace(path)


def python(root):return inside(root,'.venv/Scripts/python.exe')


def check_ports(root):
    code="import json;from dotenv import dotenv_values;c=dotenv_values('.env');print(json.dumps({k:c.get(k) for k in ('API_PORT','ONEBOT_PORT')}))"
    values=json.loads(run([python(root),'-c',code],cwd=root,capture_output=True).stdout)
    try:web=int(values.get('API_PORT') or 8001);onebot=int(values.get('ONEBOT_PORT') or 8080)
    except (ValueError,TypeError):raise AdoptionError('原 .env 中的端口不是有效数字，请先检查 API_PORT 和 ONEBOT_PORT。') from None
    if not all(1<=port<=65535 for port in (web,onebot)):raise AdoptionError('端口必须位于 1 到 65535。')
    if web==onebot:
        raise AdoptionError('此旧版本的网页与 NapCat 共用同一端口。请先在 .env 中分开设置，例如 API_PORT=8001、ONEBOT_PORT=8080，然后重试；尚未替换代码，也未修改配置。')


def prerequisites(root):
    if sys.version_info<(3,12) or sys.maxsize<=2**32:raise AdoptionError('需要 Python 3.12 或以上的 64 位环境；尚未更改现有平台。')
    for name in ('.env','scripts/local.py','start-platform.bat','.venv/Scripts/python.exe'):
        if not inside(root,name).is_file():raise AdoptionError('没有找到旧安装所需文件：'+name+'。请把接入脚本放在原平台根目录。')
    for name in ('git','node','npm.cmd'):
        if not shutil.which(name):raise AdoptionError('请先安装 '+name.replace('.cmd','')+' 并重新打开此窗口；尚未更换代码。')
    version=run(['node','--version'],capture_output=True).stdout.decode().strip().lstrip('v')
    try:major,minor,*_=map(int,version.split('.'))
    except ValueError:raise AdoptionError('无法识别 Node.js 版本。') from None
    if not (major==20 and minor>=19 or major>=22 and (major>22 or minor>=12)):
        raise AdoptionError('网页构建需要 Node.js 20.19+ 或 22.12+；请升级后重试。')
    npm_cli=Path(shutil.which('npm.cmd')).parent/'node_modules/npm/bin/npm-cli.js'
    if not npm_cli.is_file():raise AdoptionError('没有找到 npm 程序，请修复 Node.js 安装。')
    check_ports(root)


def backup_database(root,folder):
    """Use the old installation's own configuration and runtime before replacing code."""
    helper=folder/'backup-old-install.py'
    helper.write_text('''import importlib,json,sys
from pathlib import Path
root=Path(sys.argv[1]);sys.path.insert(0,str(root))
from scripts import local as runtime
try:
    if (root/'scripts/console.py').exists():
        module=importlib.import_module('scripts.console')
        if hasattr(module,'request_stop'):module.request_stop()
        else:runtime.stop()
    else:runtime.stop()
    runtime.start_database()
    path=runtime.backup()
    Path(sys.argv[2]).write_text(json.dumps({'backup':str(path)}),encoding='utf-8')
finally:runtime.stop()
''',encoding='utf-8')
    output=folder/'database-backup.json'
    run([python(root),helper,root,output],cwd=root)
    if not output.is_file():raise AdoptionError('数据库备份未返回结果，旧代码没有替换。')
    target=Path(json.loads(output.read_text(encoding='utf-8'))['backup']).resolve()
    if not target.is_relative_to(root) or not target.is_file():raise AdoptionError('数据库备份校验失败，旧代码没有替换。')
    return str(target)


def install(root,folder):
    # Run in new processes: never reuse imports loaded from the old source tree.
    run([python(root),'-m','ensurepip'],cwd=root)
    helper=folder/'install-dependencies.py'
    helper.write_text('''import sys
from pathlib import Path
root=Path(sys.argv[1]);sys.path.insert(0,str(root))
from scripts import local as runtime
from mythweaver.package_sources import pip_arguments
runtime.run([sys.executable,'-m','pip','--isolated','install','--disable-pip-version-check',*pip_arguments(),'-r',root/'requirements.lock'])
''',encoding='utf-8')
    run([python(root),helper,root],cwd=root)
    cli=Path(shutil.which('npm.cmd')).parent/'node_modules/npm/bin/npm-cli.js'
    for args in (['ci'],['run','build']):run([shutil.which('node'),cli,*args],cwd=root/'apps/web')


def start(root):run([python(root),root/'scripts/console.py'],cwd=root)


def tracked_files(repository):
    files=[];folded=set()
    for item in git(repository,'ls-files','--stage','-z').split('\0'):
        if not item:continue
        metadata,name=item.split('\t',1)
        if metadata.split()[0] not in ('100644','100755') or protected(name) or name.casefold() in folded:
            raise AdoptionError('上游包含数据目录、链接、子模块或重名路径，无法安全接入。')
        inside(repository.resolve(),name)
        folded.add(name.casefold());files.append(name)
    return files


def ignore_line(name):
    return '/'+''.join('\\'+c if c in '\\*?[]!# ' else c for c in name)


def exclude_local_files(root):
    exclude=root/'.git/info/exclude'
    with exclude.open('a',encoding='utf-8') as stream:stream.write('\n# Local deployment data; never upload\n'+'\n'.join(PROTECTED)+'\n')
    extra=[entry[3:] for entry in git(root,'status','--porcelain','-z','--untracked-files=all').split('\0') if entry.startswith('?? ')]
    with exclude.open('a',encoding='utf-8') as stream:
        stream.write('# Preserved files from the pre-Git installation\n'+'\n'.join(ignore_line(name) for name in extra)+'\n')
    return extra


def restore_files(root,folder,entries):
    for entry in reversed(entries):
        target=inside(root,entry['path'])
        if entry['existed']:shutil.copy2(inside(folder/'code',entry['path']),target)
        elif target.is_file():target.unlink()


def finish(root,folder,report,pending,no_start):
    try:
        report['status']='installing';save(folder/'report.json',report)
        install(root,folder)
        report['status']='ready';save(folder/'report.json',report);pending.unlink()
    except BaseException:
        report['status']='setup_failed';save(folder/'report.json',report)
        print('代码已接入 Git，但依赖安装或网页构建未完成。修复错误后再次运行接入脚本即可继续；不要重装或删除数据。',flush=True)
        raise
    print('接入完成！以后双击 update-platform.bat 更新。配置、数据库、插件环境和 NapCat 路径保留。',flush=True)
    print('本次备份与报告：'+str(folder),flush=True)
    if not no_start:
        try:start(root)
        except (OSError,subprocess.CalledProcessError):
            raise AdoptionError('Git 接入及依赖安装已完成，但平台启动失败。请查看服务器错误后运行 start-platform.bat；不需要再次迁移。') from None
    return report


def adopt(root,*,remote=REMOTE,no_start=False):
    root=Path(root).resolve()
    if not root.is_dir():raise AdoptionError('安装目录不存在。')
    prerequisites(root)
    adoption=inside(root,'.runtime/git-adoption');adoption.mkdir(parents=True,exist_ok=True)
    pending=adoption/'pending.json'
    if (root/'.git').exists():
        if not pending.exists():
            raise AdoptionError('此目录已经接入 Git，请直接运行 update-platform.bat。')
        folder=inside(root,json.loads(pending.read_text(encoding='utf-8'))['folder'])
        report=json.loads((folder/'report.json').read_text(encoding='utf-8'))
        if report['status'] not in ('code_connected','installing','setup_failed') or git(root,'rev-parse','HEAD')!=report['revision'] or git(root,'remote','get-url','origin')!=report['remote']:
            raise AdoptionError('上次接入后的代码或仓库已变化，请查看接入报告后处理。')
        if git(root,'status','--porcelain','--untracked-files=normal'):
            raise AdoptionError('接入后代码有本地修改，请先保留这些修改；没有强制覆盖。')
        print('继续上次接入：先停止平台并重新备份，再安装依赖。',flush=True)
        report.setdefault('retry_backups',[]).append(backup_database(root,folder))
        return finish(root,folder,report,pending,no_start)
    if pending.exists():raise AdoptionError('发现未完成的接入，请先查看 .runtime/git-adoption 中的备份和报告；没有继续覆盖。')
    # Do not accidentally adopt a directory inside another repository.
    check=run(['git','-C',str(root),'rev-parse','--show-toplevel'],check=False,capture_output=True)
    if check.returncode==0:raise AdoptionError('安装目录位于另一个 Git 仓库中，不能直接接入。')
    folder=adoption/(time.strftime('%Y%m%d-%H%M%S')+'-'+uuid4().hex[:8]);folder.mkdir()
    repository=folder/'repository'
    print('正在获取官方仓库；下载失败时不会替换旧代码。',flush=True)
    run(['git','clone','--branch','main','--single-branch',remote,repository])
    files=tracked_files(repository)
    entries=[]
    for name in files:
        target=inside(root,name)
        if target.exists() and not target.is_file():raise AdoptionError('代码文件与本地目录冲突：'+name)
        for parent in target.parents:
            if parent==root:break
            if parent.exists() and not parent.is_dir():raise AdoptionError('代码路径与本地文件冲突：'+name)
        entries.append({'path':name,'existed':target.exists()})
    report={'status':'backing_up','root':str(root),'remote':remote,'revision':git(repository,'rev-parse','HEAD'),
            'files':entries,'database_backup':None,'preserved_local_files':[]}
    save(folder/'report.json',report)
    for entry in entries:
        if entry['existed']:
            target=inside(folder/'code',entry['path']);target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(inside(root,entry['path']),target)
    # Private configuration is backed up locally and never enters the Git index.
    shutil.copy2(root/'.env',folder/'previous.env')
    report['database_backup']=backup_database(root,folder);save(folder/'report.json',report)
    save(pending,{'folder':folder.relative_to(root).as_posix()})
    moved=False
    try:
        report['status']='applying';save(folder/'report.json',report)
        for name in files:
            target=inside(root,name);target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(inside(repository,name),target)
        # Both absolute directory targets are checked within this installation.
        source_git=inside(root,(repository/'.git').relative_to(root).as_posix())
        target_git=inside(root,'.git')
        if target_git.exists():raise AdoptionError('目标 Git 元数据已存在，操作停止。')
        source_git.rename(target_git);moved=True
        report['preserved_local_files']=exclude_local_files(root)
        if git(root,'status','--porcelain','--untracked-files=normal'):
            raise AdoptionError('接入后代码核对未通过。')
        report['status']='code_connected';save(folder/'report.json',report)
    except BaseException:
        if moved:
            inside(root,'.git').rename(inside(root,(repository/'.git').relative_to(root).as_posix()))
        restore_files(root,folder,entries)
        report['status']='code_restored';save(folder/'report.json',report);pending.unlink()
        print('代码接入失败，已按备份恢复原文件；没有回退或替换数据库。',flush=True)
        raise
    return finish(root,folder,report,pending,no_start)


def main():
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',required=True);parser.add_argument('--no-start',action='store_true')
    args=parser.parse_args()
    try:adopt(args.root,no_start=args.no_start)
    except (AdoptionError,subprocess.CalledProcessError,OSError) as exc:
        print(str(exc) if isinstance(exc,AdoptionError) else '接入没有完成，请根据上方错误重试。备份保存在原目录 .runtime/git-adoption。',file=sys.stderr)
        return 1
    return 0


if __name__=='__main__':raise SystemExit(main())
