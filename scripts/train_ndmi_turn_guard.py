import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tmp/georef-deps')]
from agro_checker.ndmi_turn_guard import protocol,run
if __name__=='__main__':
    commands=dict(protocol=protocol,run=run)
    if len(sys.argv)!=2 or sys.argv[1] not in commands:raise SystemExit('Usage: train_ndmi_turn_guard.py protocol|run')
    commands[sys.argv[1]]()
