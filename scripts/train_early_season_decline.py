import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tmp/georef-deps')]
from agro_checker.early_season_decline import protocol,fit,evaluate
if __name__=='__main__':
    actions=dict(protocol=protocol,fit=fit,evaluate=evaluate)
    if len(sys.argv)!=2 or sys.argv[1] not in actions:
        raise SystemExit('Usage: train_early_season_decline.py protocol|fit|evaluate')
    actions[sys.argv[1]]()
