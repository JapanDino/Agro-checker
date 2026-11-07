import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tmp/georef-deps')]
from agro_checker.triage_retention import protocol,run,verify
if __name__=='__main__':
    commands=dict(protocol=protocol,run=run,verify=verify)
    if len(sys.argv)!=2 or sys.argv[1] not in commands:raise SystemExit('Usage: train_triage_retention.py protocol|run|verify')
    commands[sys.argv[1]]()
