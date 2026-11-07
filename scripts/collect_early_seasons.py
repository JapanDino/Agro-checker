import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT),str(ROOT/'tmp/georef-deps')]
from agro_checker.early_season_data import archive_protocol,collect,extract
if __name__=='__main__':
    actions=dict(protocol=archive_protocol,collect=collect,extract=extract)
    if len(sys.argv)!=2 or sys.argv[1] not in actions:
        raise SystemExit('Usage: collect_early_seasons.py protocol|collect|extract')
    actions[sys.argv[1]]()
