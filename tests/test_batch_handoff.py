"""Exercise cmd.exe when an update replaces its currently executing launcher."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT=Path(__file__).resolve().parent.parent
pytestmark=pytest.mark.skipif(os.name!='nt',reason='Windows cmd.exe behavior')


@pytest.mark.parametrize('launcher',['connect-git.bat','update-platform.bat'])
@pytest.mark.parametrize('newlines',['\n','\r\n'])
@pytest.mark.parametrize('result',[0,1])
def test_batch_exits_after_child_replaces_launcher(tmp_path,launcher,newlines,result):
    source=(ROOT/launcher).read_text(encoding='utf-8')
    assert source.isascii()
    handoff=next(line for line in source.splitlines() if line.startswith('".venv\\Scripts\\python.exe"'))
    runner=tmp_path/'replace-launcher.py'
    batch=tmp_path/launcher
    output=tmp_path/'arguments.json'
    accidental=tmp_path/'must-not-run.txt'
    runner.write_text('''import json,sys
from pathlib import Path
batch=Path(sys.argv[1]);Path(sys.argv[2]).write_text(json.dumps(sys.argv[3:]),encoding='utf-8')
# Longer replacement changes the byte offsets from which cmd would next read.
batch.write_text('@echo off\\n'+('rem shifted contents\\n'*80)+'echo BAD > must-not-run.txt\\n',encoding='ascii')
raise SystemExit('''+str(result)+')\n',encoding='utf-8')
    # Keep the exact launcher tail; only substitute the child executable/arguments.
    tail=handoff[handoff.index(' & '):]
    line=f'"{sys.executable}" "{runner}" "{batch}" "{output}" --no-start'+tail
    batch.write_bytes(('@echo off'+newlines+line+newlines+'echo BAD > must-not-run.txt'+newlines).encode('ascii'))
    completed=subprocess.run(['cmd.exe','/d','/c',str(batch)],cwd=tmp_path,input=b'\r\n',capture_output=True,timeout=15)
    assert completed.returncode==result,completed.stdout.decode(errors='replace')
    assert json.loads(output.read_text(encoding='utf-8'))==['--no-start']
    assert not accidental.exists()
