from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tmp/georef-deps')]
from agro_checker.crop_season_experiment import fit,evaluate
if len(sys.argv)!=2 or sys.argv[1] not in ['fit','evaluate']:
    raise SystemExit('Usage: train_crop_season_model.py fit|evaluate')
fit() if sys.argv[1]=='fit' else evaluate()
