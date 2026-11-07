"""Bootstrap the checked-in archive product; never fit a model or seed visits."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8770)
    parser.add_argument('--directory', type=Path, default=ROOT / 'data/commission-journal')
    parser.add_argument('--check-only', action='store_true')
    parser.add_argument('--no-install', action='store_true')
    args = parser.parse_args()
    if sys.version_info[:2] != (3, 13):
        raise RuntimeError('Use Python 3.13. On Windows: py -3.13 scripts/start_scouting.py')
    if not 1 <= args.port <= 65535:
        raise RuntimeError('Port must be between 1 and 65535')
    env = ROOT / '.venv'
    python = env / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if not python.is_file():
        if args.no_install:
            raise RuntimeError('No .venv. Run once without --no-install.')
        print('Creating project environment...', flush=True)
        venv.EnvBuilder(with_pip=True).create(env)
    requirements = ROOT / 'requirements-scouting-lock.txt'
    expected = dict(line.split('==', 1) for line in requirements.read_text().splitlines()
                    if line and not line.startswith('#'))
    probe = subprocess.run([str(python), '-c',
        'import importlib.metadata as m,json,sys;'
        'print(json.dumps({k:m.version(k) for k in json.loads(sys.argv[1])}))',
        json.dumps(list(expected))], capture_output=True, text=True)
    installed = json.loads(probe.stdout) if probe.returncode == 0 else {}
    if any(installed.get(k) != v for k, v in expected.items()):
        if args.no_install:
            raise RuntimeError('Dependencies differ from requirements-scouting-lock.txt')
        print('Installing pinned dependencies (internet needed on first run)...', flush=True)
        subprocess.run([str(python), '-m', 'pip', 'install', '-r', str(requirements)], check=True)
    print('Checking the 92 frozen satellite index archives...', flush=True)
    subprocess.run([str(python), '-X', 'utf8', str(ROOT / 'scripts/verify_scouting_archive.py')],
                   cwd=ROOT, check=True)
    if args.check_only:
        return
    print(f'Open http://127.0.0.1:{args.port}/scouting', flush=True)
    print(f'Local journal: {args.directory.resolve()}', flush=True)
    subprocess.run([str(python), '-X', 'utf8', '-m', 'agro_checker.scouting_server',
                    '--port', str(args.port), '--directory', str(args.directory.resolve())],
                   cwd=ROOT, check=True)


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f'AgroPulse: {error}', file=sys.stderr)
        sys.exit(1)
