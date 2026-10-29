"""Windows release packaging and explicit, staged data migration. No Docker/services."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time
import zipfile

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from scripts import local as runtime


def package(output,include_postgres=True):
    output=Path(output).resolve()
    if output.exists():raise SystemExit('Output already exists; choose a new filename.')
    if not (ROOT/'apps/web/dist/index.html').is_file():raise SystemExit('Build apps/web first: npm ci && npm run build.')
    files=[]
    for name in ('mythweaver','scripts','docs','examples','apps/web/src','apps/web/dist'):
        files.extend(p for p in (ROOT/name).rglob('*') if p.is_file() and not p.is_symlink() and '__pycache__' not in p.parts)
    files.extend(p for p in ROOT.iterdir() if p.is_file() and (p.suffix=='.bat' or p.name in ('README.md','requirements.lock','pyproject.toml','.gitignore')))
    files.extend(p for p in (ROOT/'apps/web').iterdir() if p.is_file() and
                 (p.name in ('package.json','package-lock.json','index.html') or p.name.startswith(('tsconfig','vite.config'))))
    if include_postgres:
        pg_root=runtime.pg_bin().parent
        if not (pg_root/'bin/initdb.exe').is_file():raise SystemExit('PostgreSQL binary package not found. Use --without-postgres or configure POSTGRES_BIN.')
        for name in ('bin','lib','share'):
            files.extend(p for p in (pg_root/name).rglob('*') if p.is_file() and not p.is_symlink())
        files.extend(p for p in pg_root.iterdir() if p.is_file() and
                     (p.name.upper().startswith(('LICENSE','COPYING','COPYRIGHT','NOTICE','README')) or 'license' in p.name.lower()))
    output.parent.mkdir(parents=True,exist_ok=True)
    index={}
    with zipfile.ZipFile(output,'x',zipfile.ZIP_DEFLATED,compresslevel=1) as archive:
        for path in sorted(set(files)):
            if include_postgres and path.is_relative_to(pg_root):
                name='.runtime/postgresql/pgsql/'+path.relative_to(pg_root).as_posix()
            else:name=path.relative_to(ROOT).as_posix()
            archive.write(path,name)
            with path.open('rb') as stream:index[name]=hashlib.file_digest(stream,'sha256').hexdigest()
        archive.writestr('release-manifest.json',json.dumps({'format':'mythweaver.windows.v1','postgres':include_postgres,'sha256':index},indent=2))
    with output.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
    output.with_suffix(output.suffix+'.sha256').write_text(digest+'  '+output.name+'\n',encoding='ascii')
    print('Windows release saved:',output)
    print('Contains code, prebuilt frontend and optional PostgreSQL programs. No .env, database, models, QQ login, or plugin environments.')


def export_data(output):
    output=Path(output).resolve()
    if output.exists():raise SystemExit('Output already exists; choose a new filename.')
    config=runtime.load_config();connection,pg_env,args=runtime.database_connection(config)
    folder=runtime.RUNTIME/'migration';folder.mkdir(exist_ok=True)
    dump=folder/('export-'+str(time.time_ns())+'.dump')
    runtime.run([runtime.pg_bin()/'pg_dump.exe',*args,'-Fc','--schema=public','--no-owner','--no-acl','-f',dump,connection.path.lstrip('/')],env={**os.environ,**pg_env})
    output.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(output,'x',zipfile.ZIP_DEFLATED,compresslevel=1) as archive:
        archive.write(dump,'database.dump')
        archive.writestr('migration.json',json.dumps({'format':'mythweaver.private-migration.v1','session_secret':config['SESSION_SECRET']}))
    dump.unlink()
    print('Private migration archive saved:',output)
    print('Contains game data and the key needed to decrypt model credentials. Keep private; this is not a public release package.')


def import_data(source):
    import urllib.request
    import psycopg
    from psycopg import sql
    from urllib.parse import urlsplit
    config=runtime.load_config()
    try:
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(runtime.api_url()+'/api/health',timeout=1):
            raise SystemExit('Stop the target platform before importing data.')
    except OSError:pass
    folder=runtime.RUNTIME/'migration'/('import-'+str(time.time_ns()));folder.mkdir(parents=True)
    with zipfile.ZipFile(source) as archive:
        if set(archive.namelist())!={'database.dump','migration.json'} or archive.getinfo('migration.json').file_size>8192:
            raise SystemExit('Invalid migration archive.')
        metadata=json.loads(archive.read('migration.json'))
        if metadata.get('format')!='mythweaver.private-migration.v1' or not isinstance(metadata.get('session_secret'),str) or len(metadata['session_secret'])<32:
            raise SystemExit('Migration metadata is invalid.')
        dump=folder/'database.dump'
        with archive.open('database.dump') as stream,dump.open('wb') as target:shutil.copyfileobj(stream,target)
    runtime.start_database()
    connection,pg_env,args=runtime.database_connection(config)
    admin_url=config['DATABASE_URL'].replace('postgresql+psycopg','postgresql').rsplit('/',1)[0]+'/postgres'
    name='mythweaver_import_'+str(time.time_ns())
    with psycopg.connect(admin_url,autocommit=True) as db:
        db.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    # --clean applies only to this newly created staging database, never the live one.
    runtime.run([runtime.pg_bin()/'pg_restore.exe',*args,'--clean','--if-exists','--single-transaction','--exit-on-error','--no-owner','--no-acl','-d',name,dump],env={**os.environ,**pg_env})
    new_url=urlsplit(config['DATABASE_URL'])._replace(path='/'+name).geturl()
    from mythweaver.db import Store,Channel,Outbox,PluginRelease
    from sqlalchemy import delete,update,select
    store=Store(new_url);store.initialize()
    with store.session.begin() as db:
        rebuild=[{'game_id':p.game_id,'id':p.id,'key':p.key} for p in db.scalars(select(PluginRelease).where(PluginRelease.enabled==True))]
        db.execute(delete(Channel))
        db.execute(update(Outbox).where(Outbox.status.in_(['queued','sending'])).values(status='cancelled'))
        db.execute(update(PluginRelease).values(enabled=False))
        db.execute(update(PluginRelease).where(PluginRelease.status=='ready').values(status='environment_missing'))
    report={'database':name,'source':str(Path(source).resolve()),'rebuild_plugins':rebuild,'qq_bindings_cleared':True}
    (runtime.RUNTIME/'import-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    # Preserve local login/database/OneBot passwords; bring only the model encryption key.
    shutil.copy2(ROOT/'.env',folder/'previous.env')
    config.update(DATABASE_URL=new_url,SESSION_SECRET=metadata['session_secret'])
    temporary=folder/'next.env'
    temporary.write_text('\n'.join(f'{k}={json.dumps(v,ensure_ascii=False)}' for k,v in config.items() if v is not None)+'\n',encoding='utf-8')
    temporary.replace(ROOT/'.env')
    print('Imported into NEW database:',name)
    print('Original database retained. Local login preserved; model credentials preserved. QQ must be rebound, plugins reinstalled.')


def rebuild_plugins():
    from mythweaver.db import Store
    from mythweaver.service import Service
    report=json.loads((runtime.RUNTIME/'import-report.json').read_text(encoding='utf-8'))
    service=Service(Store(runtime.load_config()['DATABASE_URL']))
    if runtime.database_connection()[0].path.lstrip('/')!=report['database']:
        raise SystemExit('Import report belongs to another database; refusing plugin installation.')
    for item in report['rebuild_plugins']:
        service.plugins.install(item['game_id'],item['id'],activate=True)
        print('Rebuilt trusted plugin:',item['key'])


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='action',required=True)
    release=commands.add_parser('package');release.add_argument('--output',required=True);release.add_argument('--without-postgres',action='store_true')
    export=commands.add_parser('export-data');export.add_argument('--output',required=True)
    restore=commands.add_parser('import-data');restore.add_argument('source')
    commands.add_parser('rebuild-plugins')
    args=parser.parse_args()
    if args.action=='package':package(args.output,not args.without_postgres)
    elif args.action=='export-data':export_data(args.output)
    elif args.action=='import-data':import_data(args.source)
    else:rebuild_plugins()
