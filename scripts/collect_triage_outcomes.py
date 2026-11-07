import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tmp/georef-deps')]
from agro_checker.triage_outcomes import protocol,catalogue,collect,evaluate
if __name__=='__main__':
    commands=dict(protocol=protocol,catalogue=catalogue,collect=collect,evaluate=evaluate)
    if len(sys.argv)!=2 or sys.argv[1] not in commands:raise SystemExit('Usage: collect_triage_outcomes.py protocol|catalogue|collect|evaluate')
    commands[sys.argv[1]]()
