"""Trusted code plugins: immutable releases, private dependency environments and JSON tools.

Process/venv isolation is NOT an OS security sandbox. Administrators install trusted code.
"""
import asyncio
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path,PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from contextvars import ContextVar

from jsonschema import Draft202012Validator
from sqlalchemy import select,func

from .config import ROOT
from .db import PluginRelease
from .definitions import DomainError,check_schema,schema_validate
from .service import packed

STOP=ContextVar('plugin_stop',default=None)


class PluginFailure(DomainError):
    def __init__(self,code='PLUGIN_FAILED',detail=''):
        super().__init__(code,'代码插件执行未成功，请管理员查看插件报告',409)
        self.detail=detail[:16000]


def relative_path(value):
    if not isinstance(value,str) or not value or len(value)>180 or '\\' in value or ':' in value:
        raise DomainError('INVALID_PLUGIN','插件文件必须使用相对路径')
    path=PurePosixPath(value)
    if path.is_absolute() or any(p in ('','..','.') or p.endswith((' ','.')) or re.fullmatch(r'(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?',p) for p in value.split('/')):
        raise DomainError('INVALID_PLUGIN','插件路径不能越界或使用保留名称')
    return value


def validate_plugin(key,manifest,files):
    if not re.fullmatch(r'[a-z][a-z0-9_]{0,23}',key):
        raise DomainError('INVALID_PLUGIN','插件标识最多24位英文小写、数字和下划线')
    if (not isinstance(manifest,dict) or set(manifest)-{'label','description','runtime','requirements','tools','tests','timeout'}
        or manifest.get('runtime') not in ('python','node') or not isinstance(files,dict) or not 1<=len(files)<=64):
        raise DomainError('INVALID_PLUGIN','插件需要python/node运行时、源码文件和工具定义')
    for field,limit in (('label',120),('description',4000)):
        if not isinstance(manifest.get(field,''),str) or len(manifest.get(field,''))>limit:
            raise DomainError('INVALID_PLUGIN','插件说明无效')
    total=0
    for file,source in files.items():
        relative_path(file)
        if not isinstance(source,str):
            raise DomainError('INVALID_PLUGIN','源码文件必须是文本')
        total+=len(source.encode('utf-8'))
    if total>262144:
        raise DomainError('INVALID_PLUGIN','插件源码合计最多256KB')
    requirements=manifest.get('requirements',[])
    if not isinstance(requirements,list) or len(requirements)>50 or any(not isinstance(r,str) or not r.strip() or len(r)>1000 or r.strip().startswith('-') or '\n' in r or '\r' in r or '\x00' in r for r in requirements):
        raise DomainError('INVALID_PLUGIN','依赖必须为包安装器参数列表，不接受命令行选项或多行指令')
    if type(manifest.get('timeout',10)) is not int or manifest.get('timeout',10)<0:
        raise DomainError('INVALID_PLUGIN','工具超时须为非负整数秒，0表示不限')
    tools=manifest.get('tools')
    if not isinstance(tools,dict) or not 1<=len(tools)<=20:
        raise DomainError('INVALID_PLUGIN','插件需要1–20个工具')
    for name,spec in tools.items():
        if (not isinstance(name,str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,23}',name) or not isinstance(spec,dict)
            or set(spec)-{'description','entry','input_schema','output_schema','player_enabled'}
            or not isinstance(spec.get('description',''),str) or len(spec.get('description',''))>2000
            or not isinstance(spec.get('entry'),str) or not isinstance(spec.get('player_enabled',False),bool)):
            raise DomainError('INVALID_PLUGIN','工具定义无效')
        parts=spec['entry'].split(':')
        if len(parts)!=2 or not re.fullmatch(r'[a-zA-Z_][a-zA-Z0-9_]*',parts[1]) or parts[0] not in files:
            raise DomainError('INVALID_PLUGIN','工具入口应为已有文件:函数名')
        suffix=Path(parts[0]).suffix
        if suffix not in (('.py',) if manifest['runtime']=='python' else ('.mjs','.js','.cjs')):
            raise DomainError('INVALID_PLUGIN','入口文件与运行时不匹配')
        for field in ('input_schema','output_schema'):
            schema=spec.get(field,{'type':'object'} if field=='input_schema' else {})
            if not isinstance(schema,dict) or field=='input_schema' and schema.get('type')!='object':
                raise DomainError('INVALID_PLUGIN','工具输入必须为对象schema')
            check_schema(schema)
            try:Draft202012Validator.check_schema(schema)
            except Exception:raise DomainError('INVALID_PLUGIN','工具schema无效') from None
    tests=manifest.get('tests',[])
    if not isinstance(tests,list) or not 1<=len(tests)<=20:
        raise DomainError('INVALID_PLUGIN','插件需要1–20个验证用例')
    for case in tests:
        if not isinstance(case,dict) or set(case)!={'tool','input','expected'} or not isinstance(case['tool'],str) or case['tool'] not in tools or not isinstance(case['input'],dict):
            raise DomainError('INVALID_PLUGIN','测试需要tool/input/expected')
        schema_validate(tools[case['tool']].get('input_schema',{'type':'object'}),case['input'])
    return deepcopy(manifest),deepcopy(files)


def clean_environment(folder):
    env={k:os.environ[k] for k in ('SystemRoot','WINDIR','PATH','PATHEXT','COMSPEC') if k in os.environ}
    home=folder/'home'
    home.mkdir(exist_ok=True)
    env.update(HOME=str(home),USERPROFILE=str(home),TEMP=str(home),TMP=str(home),PYTHONIOENCODING='utf-8',
               PYTHONUTF8='1',PIP_CONFIG_FILE=os.devnull,PIP_CACHE_DIR=str(folder/'cache'),
               npm_config_cache=str(folder/'cache'),npm_config_userconfig=os.devnull)
    return env


def terminate(process):
    if process.poll() is not None:return
    if os.name=='nt':
        subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        import signal
        try:os.killpg(process.pid,signal.SIGKILL)
        except ProcessLookupError:pass
    if process.poll() is None:process.kill()
    process.wait(timeout=10)


def process(args,folder,payload=None,timeout=60,stop=None):
    stop=stop or STOP.get() or threading.Event()
    if stop.is_set():raise PluginFailure('PLUGIN_CANCELLED','任务已取消')
    with tempfile.TemporaryFile() as out,tempfile.TemporaryFile() as err,tempfile.TemporaryFile() as stdin:
        if payload is not None:
            stdin.write(json.dumps(payload,ensure_ascii=False,allow_nan=False).encode('utf-8'))
        stdin.seek(0)
        child=subprocess.Popen([str(v) for v in args],cwd=folder,env=clean_environment(folder),stdin=stdin,stdout=out,stderr=err,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0,start_new_session=os.name!='nt')
        started=time.monotonic()
        try:
            while child.poll() is None:
                if stop.is_set():raise PluginFailure('PLUGIN_CANCELLED','任务已取消')
                if timeout and time.monotonic()-started>timeout:raise PluginFailure('PLUGIN_TIMEOUT','进程超过时间上限')
                if os.fstat(out.fileno()).st_size+os.fstat(err.fileno()).st_size>262144:raise PluginFailure('PLUGIN_OUTPUT_LIMIT','进程输出过大')
                time.sleep(.03)
            out.seek(0);err.seek(0)
            stdout=out.read(262145);stderr=err.read(262145)
            if len(stdout)+len(stderr)>262144:raise PluginFailure('PLUGIN_OUTPUT_LIMIT','进程输出过大')
            if child.returncode!=0:raise PluginFailure(detail=stderr.decode('utf-8',errors='replace')[-16000:])
            return stdout.decode('utf-8'),stderr.decode('utf-8')[-16000:]
        finally:
            terminate(child)


async def cancellable(fn,*args,**kwargs):
    stop=threading.Event()
    def invoke():
        token=STOP.set(stop)
        try:return fn(*args,**kwargs,stop=stop)
        finally:STOP.reset(token)
    task=asyncio.create_task(asyncio.to_thread(invoke))
    try:return await asyncio.shield(task)
    except asyncio.CancelledError:
        stop.set()
        try:await asyncio.shield(task)
        except Exception:pass
        raise


class Plugins:
    def __init__(self,service,root=None):
        self.service,self.store=service,service.store
        namespace=hashlib.sha256(str(self.store.engine.url).encode()).hexdigest()[:12]
        self.root=Path(root) if root else ROOT/'.runtime'/'plugins'/namespace

    def release(self,db,game_id,release_id):
        row=db.get(PluginRelease,release_id)
        if not row or row.game_id!=game_id:raise DomainError('NOT_FOUND','插件版本不存在',404)
        return row

    def folder(self,row):
        base=self.root.resolve()
        path=(base/row.game_id/row.id).resolve()
        if not path.is_relative_to(base):raise DomainError('INVALID_PLUGIN','插件目录越界')
        return path

    def list(self,game_id):
        with self.store.session() as db:
            self.service.game(db,game_id)
            return [{k:v for k,v in packed(r).items() if k!='files'} for r in db.scalars(select(PluginRelease).where(PluginRelease.game_id==game_id).order_by(PluginRelease.created_at.desc()).limit(100))]

    def draft(self,game_id,key,manifest,files):
        manifest,files=validate_plugin(key,manifest,files)
        digest=hashlib.sha256(json.dumps([manifest,files],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        with self.store.session.begin() as db:
            self.service.game(db,game_id,lock=True)
            existing=db.scalar(select(PluginRelease).where(PluginRelease.game_id==game_id,PluginRelease.key==key,PluginRelease.digest==digest))
            if existing:return packed(existing)
            revision=(db.scalar(select(func.max(PluginRelease.revision)).where(PluginRelease.game_id==game_id,PluginRelease.key==key)) or 0)+1
            row=PluginRelease(game_id=game_id,key=key,revision=revision,manifest=manifest,files=files,digest=digest)
            db.add(row);db.flush()
            self.service.event(db,game_id,'admin','plugin.drafted',{'id':row.id,'key':key,'revision':revision,'digest':digest})
            return packed(row)

    def inspect(self,game_id,release_id):
        with self.store.session() as db:return packed(self.release(db,game_id,release_id))

    def executable(self,row):
        folder=self.folder(row)
        if row.manifest['runtime']=='python':
            return folder/'env'/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
        node=shutil.which('node')
        if not node:raise PluginFailure('RUNTIME_MISSING','未安装Node.js')
        return Path(node)

    def verify_source(self,row):
        folder=self.folder(row)/'src'
        for file,source in row.files.items():
            path=(folder/file).resolve()
            if not path.is_relative_to(folder.resolve()) or not path.is_file() or path.read_text(encoding='utf-8')!=source:
                raise PluginFailure('PLUGIN_MODIFIED','源码与已记录版本不一致，需创建新版本')

    def environment_ready(self,row):
        try:
            self.verify_source(row)
            return self.executable(row).exists()
        except (DomainError,OSError):return False

    def reconcile(self):
        with self.store.session() as db:
            ids=[(r.game_id,r.id) for r in db.scalars(select(PluginRelease).where(PluginRelease.status=='ready',PluginRelease.enabled==True))]
        for game_id,release_id in ids:
            with self.store.session.begin() as db:
                self.service.game(db,game_id,lock=True)
                row=self.release(db,game_id,release_id)
                if not self.environment_ready(row):
                    self._activate(db,row,False)
                    row.status='environment_missing'
                    row.report={**row.report,'code':'PLUGIN_ENVIRONMENT_MISSING','detail':'源码或依赖环境缺失，请重新安装此版本'}

    def execute(self,row,name,data,stop=None):
        from .agent_contracts import require_code_execution
        require_code_execution()
        spec=row.manifest['tools'].get(name)
        if not spec:raise DomainError('NOT_FOUND','插件工具不存在',404)
        schema_validate(spec.get('input_schema',{'type':'object'}),data)
        self.verify_source(row)
        runtime=row.manifest['runtime']
        args=[self.executable(row)]
        if runtime=='python':args+=['-I','-X','utf8',Path(__file__).with_name('plugin_worker.py')]
        else:args+=[Path(__file__).with_name('plugin_worker.mjs')]
        args+=[self.folder(row)/'src',spec['entry']]
        stdout,_=process(args,self.folder(row),data,row.manifest.get('timeout',10),stop)
        try:
            result=json.loads(stdout,parse_constant=lambda x:(_ for _ in ()).throw(ValueError(x)))
            if len(stdout)>64000:raise ValueError()
        except (ValueError,TypeError):raise PluginFailure('PLUGIN_PROTOCOL','工具必须返回不超过64000字符的JSON') from None
        schema_validate(spec.get('output_schema',{}),result)
        return result

    def install(self,game_id,release_id,activate=True,stop=None):
        with self.store.session.begin() as db:
            self.service.game(db,game_id,lock=True)
            row=self.release(db,game_id,release_id)
            if row.status in ('installing','testing'):raise DomainError('PLUGIN_BUSY','插件正在安装或测试',409)
            if row.status=='ready' and self.environment_ready(row):
                if activate:self._activate(db,row,True)
                return {k:v for k,v in packed(row).items() if k!='files'}
            row.status='installing'
            row.enabled=False
        try:
            folder=self.folder(row)
            folder.mkdir(parents=True,exist_ok=True)
            src=folder/'src';src.mkdir(exist_ok=True)
            for file,source in row.files.items():
                path=(src/file).resolve()
                if not path.is_relative_to(src.resolve()):raise PluginFailure(detail='源码目录越界')
                path.parent.mkdir(parents=True,exist_ok=True)
                path.write_text(source,encoding='utf-8')
            requirements=row.manifest.get('requirements',[])
            if row.manifest['runtime']=='python':
                if not self.executable(row).exists():
                    args=[sys.executable,'-I','-m','venv',str(folder/'env')]
                    if not requirements:args+=['--without-pip']
                    process(args,folder,timeout=90,stop=stop)
                if requirements:
                    requirements_file=folder/'requirements.txt'
                    requirements_file.write_text('\n'.join(requirements)+'\n',encoding='utf-8')
                    process([self.executable(row),'-I','-m','ensurepip'],folder,timeout=90,stop=stop)
                    from .package_sources import pip_arguments
                    process([self.executable(row),'-I','-m','pip','install','--disable-pip-version-check',*pip_arguments(),'-r',requirements_file],folder,timeout=180,stop=stop)
            else:
                self.executable(row)
                (folder/'package.json').write_text(json.dumps({'private':True,'type':'module'}),encoding='utf-8')
                if requirements:
                    npm=shutil.which('npm.cmd' if os.name=='nt' else 'npm')
                    # Invoke npm's JS entry directly; do not pass package text through cmd.exe.
                    if not npm:raise PluginFailure('RUNTIME_MISSING','未安装npm')
                    npm_cli=Path(npm).parent/'node_modules'/'npm'/'bin'/'npm-cli.js'
                    if not npm_cli.exists():raise PluginFailure('RUNTIME_MISSING','未找到npm CLI入口')
                    process([self.executable(row),npm_cli,'install','--prefix',folder,'--no-audit','--no-fund','--',*requirements],folder,timeout=180,stop=stop)
            with self.store.session.begin() as db:self.release(db,game_id,release_id).status='testing'
            checks=[]
            for case in row.manifest['tests']:
                actual=self.execute(row,case['tool'],case['input'],stop)
                if actual!=case['expected']:raise PluginFailure('PLUGIN_TEST_FAILED',f"测试工具 {case['tool']} 的结果与expected不一致")
                checks.append({'tool':case['tool'],'passed':True})
            with self.store.session.begin() as db:
                self.service.game(db,game_id,lock=True)
                saved=self.release(db,game_id,release_id)
                saved.status,saved.report='ready',{'tests':checks,'runtime':'trusted-process','digest':saved.digest}
                if activate:self._activate(db,saved,True)
                self.service.event(db,game_id,'admin','plugin.installed',{'id':saved.id,'key':saved.key,'revision':saved.revision,'enabled':saved.enabled})
                return {k:v for k,v in packed(saved).items() if k!='files'}
        except Exception as exc:
            with self.store.session.begin() as db:
                saved=self.release(db,game_id,release_id)
                saved.status,saved.enabled='failed',False
                saved.report={'code':getattr(exc,'code','PLUGIN_FAILED'),'detail':getattr(exc,'detail',type(exc).__name__)}
            if isinstance(exc,DomainError):raise
            raise PluginFailure(detail=type(exc).__name__) from None

    def _activate(self,db,row,enabled):
        if enabled and row.status!='ready':raise DomainError('PLUGIN_NOT_READY','插件必须安装并通过测试',409)
        if enabled and not self.environment_ready(row):raise DomainError('PLUGIN_ENVIRONMENT_MISSING','插件环境缺失或源码变化，请重新安装',409)
        if enabled:
            names={f'ext_{row.key}__{name}' for name in row.manifest['tools']}
            for other in db.scalars(select(PluginRelease).where(PluginRelease.game_id==row.game_id,PluginRelease.key!=row.key,PluginRelease.enabled==True)):
                if names & {f'ext_{other.key}__{name}' for name in other.manifest['tools']}:
                    raise DomainError('PLUGIN_TOOL_COLLISION','插件工具注册名称与其他活动插件冲突',409)
        changed=row.enabled!=enabled
        if enabled:
            for other in db.scalars(select(PluginRelease).where(PluginRelease.game_id==row.game_id,PluginRelease.key==row.key)):
                other.enabled=False
        row.enabled=enabled
        if changed:
            game=self.service.game(db,row.game_id)
            self.service.apply(db,game,'admin','definition',{'version':game.version,'definition':game.definition})
            from .db import Outbox
            from sqlalchemy import update
            db.execute(update(Outbox).where(Outbox.game_id==row.game_id,Outbox.group_id.like('private-%'),Outbox.status=='queued').values(status='cancelled'))

    def state(self,game_id,release_id,enabled):
        with self.store.session.begin() as db:
            self.service.game(db,game_id,lock=True)
            row=self.release(db,game_id,release_id)
            if row.status in ('installing','testing'):raise DomainError('PLUGIN_BUSY','请先停止正在执行的安装任务',409)
            self._activate(db,row,enabled)
            self.service.event(db,game_id,'admin','plugin.state',{'id':row.id,'enabled':enabled})
            return {k:v for k,v in packed(row).items() if k!='files'}

    def active(self,db,game_id,key):
        row=db.scalar(select(PluginRelease).where(PluginRelease.game_id==game_id,PluginRelease.key==key,PluginRelease.enabled==True,PluginRelease.status=='ready'))
        if not row:raise DomainError('PLUGIN_NOT_READY','插件未启用或没有通过测试的版本',409)
        return row

    def invoke(self,game_id,key,tool,data,actor='admin',stop=None,db=None):
        if db is None:
            with self.store.session() as session:return self.invoke(game_id,key,tool,data,actor,stop,session)
        row=self.active(db,game_id,key)
        if actor!='admin' and not row.manifest['tools'].get(tool,{}).get('player_enabled',False):
            raise DomainError('FORBIDDEN','插件工具未开放给玩家',403)
        return self.execute(row,tool,data,stop)

    def tool_specs(self,game_id):
        result=[]
        with self.store.session() as db:
            for row in db.scalars(select(PluginRelease).where(PluginRelease.game_id==game_id,PluginRelease.enabled==True,PluginRelease.status=='ready')):
                for name,spec in row.manifest['tools'].items():
                    result.append({'type':'function','function':{'name':f'ext_{row.key}__{name}','description':spec.get('description','插件工具'),
                                   'parameters':spec.get('input_schema',{'type':'object'})}})
        return result

    def invoke_registered(self,game_id,name,data,stop=None):
        with self.store.session() as db:
            for row in db.scalars(select(PluginRelease).where(PluginRelease.game_id==game_id,PluginRelease.enabled==True,PluginRelease.status=='ready')):
                for tool in row.manifest['tools']:
                    if name==f'ext_{row.key}__{tool}':
                        return self.execute(row,tool,data,stop)
        raise DomainError('FORBIDDEN_TOOL','插件工具未注册或已停用',403)
