"""Windows-native development runtime. Never installs a service or uses Docker."""
import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
import tempfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
RUNTIME = ROOT / ".runtime"
PG_BIN = RUNTIME / "postgresql" / "pgsql" / "bin"
PG_DATA = RUNTIME / "pgdata"
NAPCAT = ROOT / "NapCat.Shell.Windows.OneKey" / "NapCat.52230.Shell"
NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def run(args, **kwargs):
    native=Path(args[0]).stem in ('initdb','pg_ctl','pg_dump','pg_restore')
    values=[str(native_path(a) if native and isinstance(a,Path) else a) for a in args]
    if native and 'stderr' not in kwargs:
        # pg_ctl's long-lived child can inherit stderr: a PIPE would never reach EOF.
        with tempfile.TemporaryFile() as errors:
            try:return subprocess.run(values,cwd=native_path(ROOT),check=True,creationflags=NO_WINDOW,stderr=errors,**kwargs)
            except subprocess.CalledProcessError:
                errors.seek(0)
                print(errors.read().decode('utf-8',errors='replace'),file=sys.stderr)
                raise
    try:return subprocess.run(values, cwd=native_path(ROOT) if native else ROOT, check=True, creationflags=NO_WINDOW, **kwargs)
    except subprocess.CalledProcessError as error:
        if error.stderr:print(error.stderr.decode('utf-8',errors='replace') if isinstance(error.stderr,bytes) else error.stderr,file=sys.stderr)
        raise


def native_path(path):
    """PostgreSQL Windows tools can feed ANSI paths into UTF-8 bootstrap SQL."""
    path=Path(path).resolve()
    if os.name!='nt' or str(path).isascii():return path
    import ctypes
    missing=[];ancestor=path
    if path.name.isascii() and path.is_file():
        missing=[path.name];ancestor=path.parent
    while not ancestor.exists():missing.insert(0,ancestor.name);ancestor=ancestor.parent
    buffer=ctypes.create_unicode_buffer(32768)
    length=ctypes.windll.kernel32.GetShortPathNameW(str(ancestor),buffer,len(buffer))
    result=Path(buffer.value).joinpath(*missing) if length else path
    if not str(result).isascii():
        raise SystemExit('PostgreSQL requires an ASCII path on this Windows installation (8.3 alias unavailable). Move the project to e.g. D:\\MythWeaver.')
    return result


def load_config():
    from dotenv import dotenv_values
    return dotenv_values(ROOT / ".env")


def pg_bin():
    value=load_config().get('POSTGRES_BIN')
    return (ROOT / value).resolve() if value else PG_BIN


def database_connection(config=None):
    from urllib.parse import urlsplit,unquote
    config=config or load_config()
    value=urlsplit(config['DATABASE_URL'].replace('postgresql+psycopg','postgresql'))
    if value.hostname not in ('127.0.0.1','localhost'):
        raise SystemExit('Native runtime requires a local PostgreSQL URL.')
    return value,{'PGPASSWORD':unquote(value.password or '')},['-h',value.hostname,'-p',str(value.port or 5432),'-U',unquote(value.username or 'mythweaver')]


def api_url():
    return 'http://127.0.0.1:'+str(int(load_config().get('API_PORT') or 8001))


def onebot_url():
    return 'http://127.0.0.1:'+str(int(load_config().get('ONEBOT_PORT') or 8080))


def api_host():
    host=load_config().get('API_HOST') or '127.0.0.1'
    if host not in ('127.0.0.1','0.0.0.0'):
        raise SystemExit('API_HOST must be 127.0.0.1 (local only) or 0.0.0.0 (network access).')
    return host


def setup(bot_id='0',api_port=8001,db_port=55432,postgres_bin=None,napcat_script=None):
    RUNTIME.mkdir(exist_ok=True)
    env = ROOT / ".env"
    if not env.exists():
        if len({api_port,db_port,8080})!=3:raise SystemExit('Web, database and OneBot (8080) need three distinct ports.')
        admin = secrets.token_urlsafe(24)
        dbpass = secrets.token_hex(24)
        values = {
            "DATABASE_URL": f"postgresql+psycopg://mythweaver:{dbpass}@127.0.0.1:{db_port}/mythweaver",
            "ADMIN_PASSWORD": admin,
            "SESSION_SECRET": secrets.token_hex(32),
            "ONEBOT_TOKEN": secrets.token_hex(32),
            "ONEBOT_BOT_ID": bot_id,
            "API_PORT":str(api_port),
            "API_HOST":"127.0.0.1",
            "ONEBOT_PORT":"8080",
            "ONEBOT_HOST":"127.0.0.1",
            "PUBLIC_ORIGIN": "auto",
        }
        if postgres_bin:values['POSTGRES_BIN']=str(Path(postgres_bin).resolve())
        if napcat_script:values['NAPCAT_SCRIPT']=str(Path(napcat_script).resolve())
        env.write_text("\n".join(f"{k}={v}" for k, v in values.items()) + "\n", encoding="utf-8")
        (RUNTIME / "credentials.txt").write_text(
            f"MythWeaver 本机管理员\n地址：http://127.0.0.1:{api_port}\n密码：" + admin +
            "\n\n仅保存在本机，请勿把此文件、.env 或数据库备份发到群里。\n", encoding="utf-8")
    config = load_config()
    bin_path=pg_bin()
    if not (bin_path / "initdb.exe").exists():
        raise SystemExit("Extract the PostgreSQL Windows archive into .runtime/postgresql first")
    if (PG_DATA / "PG_VERSION").exists() and not (PG_DATA / "global" / "pg_control").exists():
        raise SystemExit("Incomplete PostgreSQL initialization. Preserve/rename .runtime/pgdata before retrying.")
    if not (PG_DATA / "PG_VERSION").exists():
        from urllib.parse import urlsplit
        password = urlsplit(config["DATABASE_URL"].replace("postgresql+psycopg", "postgresql")).password
        pwfile = RUNTIME / "init-password.tmp"
        try:
            pwfile.write_text(password, encoding="utf-8")
            run([bin_path / "initdb.exe", "-D", PG_DATA, "-U", "mythweaver", "--pwfile", pwfile,
                 "--encoding=UTF8", "--locale=C", "--auth=scram-sha-256"], stdout=subprocess.DEVNULL)
        finally:
            pwfile.unlink(missing_ok=True)
        with (PG_DATA / "postgresql.conf").open("a", encoding="utf-8") as stream:
            stream.write(f"\nlisten_addresses = '127.0.0.1'\nport = {database_connection(config)[0].port or 5432}\n")
    start_database()
    import psycopg
    url = config["DATABASE_URL"].replace("postgresql+psycopg", "postgresql")
    with psycopg.connect(url.rsplit("/", 1)[0] + "/postgres", autocommit=True) as db:
        if not db.execute("SELECT 1 FROM pg_database WHERE datname = %s", ("mythweaver",)).fetchone():
            db.execute('CREATE DATABASE "mythweaver"')
    print(f"Native PostgreSQL is ready on port {database_connection(config)[0].port or 5432}. Credentials: .runtime/credentials.txt")


def start_database():
    status = subprocess.run([str(native_path(pg_bin() / "pg_ctl.exe")), "-D", str(native_path(PG_DATA)), "status"], capture_output=True, creationflags=NO_WINDOW,cwd=native_path(ROOT))
    if status.returncode:
        run([pg_bin() / "pg_ctl.exe", "-D", PG_DATA, "-l", RUNTIME / "postgres.log", "-w", "start"], stdout=subprocess.DEVNULL)


def napcat_path():
    value=load_config().get('NAPCAT_SCRIPT')
    if value:return (ROOT/value).resolve()
    legacy=NAPCAT/'napcat.quick.bat'
    if legacy.exists():return legacy
    raise SystemExit('Set NAPCAT_SCRIPT in .env to the installed NapCat launcher.')


def configure_napcat(config_path=None):
    config = load_config()
    if not config['ONEBOT_BOT_ID'].isdecimal() or config['ONEBOT_BOT_ID']=='0':
        raise SystemExit('Set ONEBOT_BOT_ID in .env to the real bot QQ first.')
    files = [Path(config_path)] if config_path else list(napcat_path().parent.glob(f"versions/*/resources/app/napcat/config/onebot11_{config['ONEBOT_BOT_ID']}.json"))
    if len(files) != 1:
        raise SystemExit("Expected one NapCat account configuration; select the version explicitly")
    path = files[0]
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    backup = RUNTIME / "napcat-onebot.before-mythweaver.json"
    if not backup.exists():
        backup.write_bytes(path.read_bytes())
    network = data.setdefault("network", {})
    clients = network.setdefault("websocketClients", [])
    name = "mythweaver-local"
    connection = {"name": name, "enable": True, "url": onebot_url().replace('http://','ws://')+"/channels/onebot/v11/ws",
                  "messagePostFormat": "array", "reportSelfMessage": False, "token": config["ONEBOT_TOKEN"],
                  "debug": False, "reconnectInterval": 5000, "heartInterval": 30000}
    matches = [i for i, client in enumerate(clients) if client.get("name") == name]
    if matches:
        clients[matches[0]] = connection
    else:
        clients.append(connection)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Configured only the mythweaver-local reverse WebSocket. Original config backed up in .runtime.")


def start():
    host=api_host()
    from mythweaver.server import listeners
    try:ports=listeners(load_config())
    except ValueError as exc:raise SystemExit(str(exc)) from None
    start_database()
    (RUNTIME/'platform.url').write_text(api_url(),encoding='ascii')
    import urllib.request
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def ready():
        try:
            with opener.open(api_url()+"/api/health", timeout=1) as response:
                web_ready=response.status == 200 and json.load(response).get("ok") is True
            with opener.open(onebot_url()+'/health',timeout=1) as response:
                return web_ready and response.status==200 and json.load(response).get('service')=='onebot'
        except (OSError, ValueError, AttributeError):
            return False

    pidfile = RUNTIME / "api.pid"
    if pidfile.exists() and ready():
        print("Platform is already responding at "+api_url())
        return
    import socket
    for name in ('API_PORT','ONEBOT_PORT'):
        try:
            with socket.create_connection(('127.0.0.1',ports[name]),timeout=1):
                raise SystemExit(name+' is already in use. Stop its owner or choose a different port.')
        except OSError:pass
    log = (RUNTIME / "api.log").open("ab")
    process = subprocess.Popen([sys.executable, "-m", "mythweaver.server"],
                               cwd=ROOT, stdout=log, stderr=log, creationflags=NO_WINDOW)
    log.close()
    pidfile.write_text(str(process.pid), encoding="ascii")
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            pidfile.unlink(missing_ok=True)
            raise SystemExit("API process exited during startup. Check .runtime/api.log for details.")
        if ready():
            print("Started MythWeaver at "+api_url()+"; log: .runtime/api.log")
            return
        time.sleep(0.25)
    raise SystemExit("API did not become ready within 30 seconds. Check .runtime/api.log; the process may still be starting.")


def stop_api():
    pidfile = RUNTIME / "api.pid"
    if pidfile.exists():
        pid = pidfile.read_text().strip()
        if os.name == "nt" and pid.isdecimal():
            # Verify the recorded PID before terminating to avoid PID-reuse mistakes.
            executable=str(Path(sys.executable).resolve()).replace("'","''")
            query = f"$p = Get-CimInstance Win32_Process -Filter 'ProcessId = {pid}'; if ($p -and ($p.CommandLine -like '*mythweaver.api:create_app*' -or $p.CommandLine -like '*-m mythweaver.server*') -and $p.CommandLine.Contains('{executable}')) {{ Stop-Process -Id {pid} }}"
            run(["powershell", "-NoProfile", "-Command", query])
        pidfile.unlink(missing_ok=True)


def stop():
    stop_api()
    status=subprocess.run([str(native_path(pg_bin()/'pg_ctl.exe')),'-D',str(native_path(PG_DATA)),'status'],capture_output=True,creationflags=NO_WINDOW,cwd=native_path(ROOT))
    if status.returncode==0:run([pg_bin() / "pg_ctl.exe", "-D", PG_DATA, "-m", "fast", "-w", "stop"], stdout=subprocess.DEVNULL)
    print("Platform and project database stopped. NapCat is managed separately.")


def backup():
    config = load_config()
    connection,pg_env,pg_args=database_connection(config)
    folder = RUNTIME / "backups"
    folder.mkdir(exist_ok=True)
    target = folder / (time.strftime("%Y%m%d-%H%M%S") + ".dump")
    run([pg_bin() / "pg_dump.exe", *pg_args, "-Fc", "-f", target, connection.path.lstrip('/')], env={**os.environ, **pg_env})
    print("Database backup saved:", target)
    return target


def restore_check():
    """Restore the latest backup into a NEW database, never overwrite the live one."""
    import psycopg
    from psycopg import sql
    from urllib.parse import urlsplit
    config = load_config()
    url = config["DATABASE_URL"].replace("postgresql+psycopg", "postgresql")
    password = urlsplit(url).password
    dumps = sorted((RUNTIME / "backups").glob("*.dump"))
    if not dumps:
        raise SystemExit("Create a backup first")
    name = "mythweaver_restore_" + time.strftime("%Y%m%d_%H%M%S") + "_" + secrets.token_hex(3)
    with psycopg.connect(url.rsplit("/", 1)[0] + "/postgres", autocommit=True) as db:
        db.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    _,pg_env,pg_args=database_connection(config)
    run([pg_bin() / "pg_restore.exe", *pg_args, "--exit-on-error",'--no-owner','--no-acl',
         "-d", name, dumps[-1]], env={**os.environ, **pg_env})
    with psycopg.connect(url.rsplit("/", 1)[0] + "/" + name) as db:
        games = db.execute("SELECT count(*) FROM games").fetchone()[0]
        events = db.execute("SELECT count(*) FROM events").fetchone()[0]
        broken = db.execute("SELECT count(*) FROM entities e LEFT JOIN games g ON g.id=e.game_id WHERE g.id IS NULL").fetchone()[0]
        flows=0
        if db.execute("SELECT to_regclass('flow_runs')").fetchone()[0]:
            flows=db.execute("SELECT count(*) FROM flow_runs").fetchone()[0]
            broken+=db.execute("SELECT count(*) FROM flow_runs r LEFT JOIN games g ON g.id=r.game_id WHERE g.id IS NULL").fetchone()[0]
        plugins=tasks=0
        if db.execute("SELECT to_regclass('plugin_releases')").fetchone()[0]:
            plugins=db.execute('SELECT count(*) FROM plugin_releases').fetchone()[0]
            broken+=db.execute('SELECT count(*) FROM plugin_releases p LEFT JOIN games g ON g.id=p.game_id WHERE g.id IS NULL').fetchone()[0]
        if db.execute("SELECT to_regclass('harness_tasks')").fetchone()[0]:
            tasks=db.execute('SELECT count(*) FROM harness_tasks').fetchone()[0]
            broken+=db.execute('SELECT count(*) FROM harness_tasks t LEFT JOIN agent_runs r ON r.id=t.id WHERE r.id IS NULL OR r.game_id<>t.game_id').fetchone()[0]
            broken+=db.execute('SELECT count(*) FROM tool_executions e LEFT JOIN agent_runs r ON r.id=e.run_id WHERE r.id IS NULL').fetchone()[0]
        if broken:
            raise SystemExit("Restore integrity check failed")
    print(f"Restore verified in independent database {name}: games={games}, events={events}, flows={flows}, plugins={plugins}, tasks={tasks}. Live database untouched.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["setup", "start", "stop", "configure-napcat", "backup", "restore-check", "napcat-start"])
    parser.add_argument('--bot-id',default='0');parser.add_argument('--api-port',type=int,default=8001)
    parser.add_argument('--db-port',type=int,default=55432);parser.add_argument('--postgres-bin');parser.add_argument('--napcat-script');parser.add_argument('--napcat-config')
    args=parser.parse_args()
    if args.action=='setup':
        if not args.bot_id.isdecimal() or any(not 1024<=p<=65535 for p in (args.api_port,args.db_port)) or args.api_port==args.db_port:
            parser.error('Use a numeric QQ and two distinct ports between 1024 and 65535.')
        setup(args.bot_id,args.api_port,args.db_port,args.postgres_bin,args.napcat_script)
    elif args.action=='configure-napcat':configure_napcat(args.napcat_config)
    elif args.action=='napcat-start':
        script=napcat_path()
        if not script.is_file():raise SystemExit('NapCat launcher was not found. Set NAPCAT_SCRIPT in .env.')
        os.startfile(str(script),cwd=str(script.parent))
        print('Opened configured NapCat launcher.')
    elif args.action=='start':
        from scripts.console import main
        main()
    elif args.action=='stop':
        from scripts.console import request_stop
        request_stop()
    else:{"backup": backup, "restore-check": restore_check}[args.action]()
