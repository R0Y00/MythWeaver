"""Server console: closing this process ends its API and PostgreSQL process tree."""
import argparse
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import sys
import threading
import time

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from scripts import local as runtime


def request_stop():
    import msvcrt
    path=runtime.RUNTIME/'console.lock'
    def active():
        if not path.exists():return False
        with path.open('r+b') as stream:
            try:msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
            except OSError:return True
            msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1);return False
    if not active():runtime.stop();return
    (runtime.RUNTIME/'console.stop').write_text('stop',encoding='ascii')
    deadline=time.monotonic()+20
    while active():
        if time.monotonic()>deadline:raise SystemExit('Server console did not stop. Inspect the server window.')
        time.sleep(.2)
    print('Server console and project database stopped.')


def terminal_job():
    """All subsequently created children die when the console owner exits."""
    class Basic(ctypes.Structure):
        _fields_=[('process_time',ctypes.c_int64),('job_time',ctypes.c_int64),('flags',wintypes.DWORD),
                  ('min_working',ctypes.c_size_t),('max_working',ctypes.c_size_t),('active_limit',wintypes.DWORD),
                  ('affinity',ctypes.c_size_t),('priority',wintypes.DWORD),('scheduling',wintypes.DWORD)]
    class IO(ctypes.Structure):
        _fields_=[(name,ctypes.c_uint64) for name in ('read_ops','write_ops','other_ops','read_bytes','write_bytes','other_bytes')]
    class Extended(ctypes.Structure):
        _fields_=[('basic',Basic),('io',IO),('process_memory',ctypes.c_size_t),('job_memory',ctypes.c_size_t),
                  ('peak_process_memory',ctypes.c_size_t),('peak_job_memory',ctypes.c_size_t)]
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateJobObjectW.argtypes=[ctypes.c_void_p,wintypes.LPCWSTR];kernel.CreateJobObjectW.restype=wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes=[wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD]
    kernel.AssignProcessToJobObject.argtypes=[wintypes.HANDLE,wintypes.HANDLE]
    kernel.GetCurrentProcess.restype=wintypes.HANDLE
    handle=kernel.CreateJobObjectW(None,None)
    if not handle:raise ctypes.WinError(ctypes.get_last_error())
    limits=Extended();limits.basic.flags=0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not kernel.SetInformationJobObject(handle,9,ctypes.byref(limits),ctypes.sizeof(limits)):
        raise ctypes.WinError(ctypes.get_last_error())
    if not kernel.AssignProcessToJobObject(handle,kernel.GetCurrentProcess()):
        raise ctypes.WinError(ctypes.get_last_error())
    # The handle is deliberately retained until process exit, including abrupt exits.
    return handle


def main(no_ui=False):
    if os.name!='nt':raise SystemExit('This server console currently supports Windows only.')
    import msvcrt
    runtime.RUNTIME.mkdir(exist_ok=True)
    held=(runtime.RUNTIME/'console.lock').open('a+b')
    if held.seek(0,2)==0:held.write(b'0');held.flush()
    held.seek(0)
    try:msvcrt.locking(held.fileno(),msvcrt.LK_NBLCK,1)
    except OSError:
        held.close();raise SystemExit('Another server console is already open. Use that window or stop-platform.bat.')
    stop_file=runtime.RUNTIME/'console.stop';stop_file.unlink(missing_ok=True)
    job=terminal_job();closing=threading.Event()
    handler_type=ctypes.WINFUNCTYPE(wintypes.BOOL,wintypes.DWORD)
    @handler_type
    def on_close(event):
        if event in (2,5,6):
            closing.set();time.sleep(3);return True
        return False
    ctypes.windll.kernel32.SetConsoleCtrlHandler(on_close,True)
    log_path=runtime.RUNTIME/'api.log'
    offset=log_path.stat().st_size if log_path.exists() else 0
    try:
        print('Starting server console. Keep this window open.',flush=True)
        # Take over this installation's legacy standalone processes before owning the tree.
        runtime.stop();runtime.start()
        print('Server ready: '+runtime.api_url(),flush=True)
        print('Ctrl+C stops the server. Closing this window also stops the API and project database.',flush=True)
        if not no_ui:
            import webbrowser
            webbrowser.open(runtime.api_url())
        failures=0;next_probe=0
        while not closing.wait(.2):
            if stop_file.exists():break
            if log_path.exists():
                with log_path.open('rb') as stream:
                    if stream.seek(0,2)<offset:offset=0
                    stream.seek(offset);chunk=stream.read(65536);offset=stream.tell()
                if chunk:sys.stdout.write(chunk.decode('utf-8',errors='replace'));sys.stdout.flush()
            if time.monotonic()>=next_probe:
                import urllib.request
                next_probe=time.monotonic()+2
                try:
                    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
                    with opener.open(runtime.api_url()+'/api/health',timeout=2) as response:
                        healthy=response.status==200
                    with opener.open(runtime.onebot_url()+'/health',timeout=2) as response:
                        healthy=healthy and response.status==200
                    failures=0 if healthy else failures+1
                except OSError:failures+=1
                if failures>=3:raise SystemExit('Server stopped or became unhealthy. See .runtime/api.log.')
    except KeyboardInterrupt:
        print('\nStopping server...',flush=True)
    finally:
        try:runtime.stop()
        finally:stop_file.unlink(missing_ok=True);held.close()
    return job  # Keep the OS handle alive until the Python process exits.


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--no-ui',action='store_true')
    main(parser.parse_args().no_ui)
