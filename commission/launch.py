"""Offline launcher for the Windows x64 / Python 3.13 review bundle."""
import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import struct
import subprocess
import sys
import venv

ROOT=Path(__file__).resolve().parent


def verify():
    manifest=json.loads((ROOT/'manifest.json').read_text(encoding='utf-8'))
    for name,spec in manifest['files'].items():
        p=ROOT/name
        if not p.is_relative_to(ROOT) or not p.is_file():raise RuntimeError('Missing file: '+name)
        h=hashlib.sha256()
        with p.open('rb') as f:
            for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
        if p.stat().st_size!=spec['bytes'] or h.hexdigest()!=spec['sha256']:raise RuntimeError('Checksum mismatch: '+name)
    return manifest


def main():
    parser=argparse.ArgumentParser(description='AgroPulse offline review bundle')
    parser.add_argument('--port',type=int,default=8771)
    parser.add_argument('--directory',type=Path,default=ROOT/'review-journal')
    parser.add_argument('--verify-only',action='store_true')
    args=parser.parse_args()
    if sys.version_info[:2]!=(3,13) or sys.platform!='win32' or struct.calcsize('P')!=8:
        raise RuntimeError('This offline bundle needs Windows x64 and Python 3.13 x64. See README.txt. No Python runtime is bundled.')
    print('Verifying application and archive...',flush=True);manifest=verify()
    if args.verify_only:
        print('All file checksums match.');return
    env=ROOT/'.review-venv';python=env/'Scripts/python.exe'
    if not python.exists():
        print('Creating local environment; installing bundled wheels without internet...',flush=True)
        venv.EnvBuilder(with_pip=True).create(env)
    expected=manifest['dependencies']
    probe=subprocess.run([str(python),'-E','-c',"import importlib.metadata as m,json,sys;print(json.dumps({k:m.version(k) for k in json.loads(sys.argv[1])}))",json.dumps(list(expected))],capture_output=True,text=True)
    installed=json.loads(probe.stdout) if probe.returncode==0 else {}
    if any(installed.get(k)!=v for k,v in expected.items()):
        subprocess.run([str(python),'-m','pip','install','--disable-pip-version-check','--no-index','--find-links',str(ROOT/'wheels'),'-r',str(ROOT/'requirements.txt')],check=True)
    sys.path.insert(0,str(ROOT))
    print('Open http://127.0.0.1:'+str(args.port)+'/scouting',flush=True)
    print('Separate journal: '+str(args.directory.resolve()),flush=True)
    subprocess.run([str(python),'-E','-X','utf8','-m','agro_checker.scouting_server','--port',str(args.port),'--directory',str(args.directory.resolve())],cwd=ROOT,check=True)


if __name__=='__main__':
    try:main()
    except (RuntimeError,OSError,subprocess.CalledProcessError,ValueError) as error:
        print('AgroPulse could not start: '+str(error),file=sys.stderr)
        sys.exit(1)
