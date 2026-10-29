"""Trusted Python plugin ABI. Runs in the plugin's own interpreter, never imported by API."""
import contextlib
import importlib.util
import inspect
import asyncio
import json
from pathlib import Path
import sys


def main():
    root=Path(sys.argv[1])
    relative,function=sys.argv[2].split(':',1)
    sys.path.insert(0,str(root))
    payload=json.load(sys.stdin)
    # stdout is the protocol channel; plugin diagnostics go to stderr.
    with contextlib.redirect_stdout(sys.stderr):
        spec=importlib.util.spec_from_file_location('_mythweaver_plugin',root/relative)
        module=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        result=getattr(module,function)(payload)
        if inspect.isawaitable(result):
            result=asyncio.run(result)
    sys.stdout.write(json.dumps(result,ensure_ascii=False,allow_nan=False))


if __name__=='__main__':
    main()
