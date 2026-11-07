"""Fit/choose only on 2024; separate explicit evaluation after freezing."""
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tmp/georef-deps')]
from agro_checker.field_decline_experiment import fit_experiment,evaluate
if len(sys.argv)!=2 or sys.argv[1] not in ['fit','fit-gated','evaluate']:
    raise SystemExit('Usage: train_field_decline_model.py fit|fit-gated|evaluate')
fit_experiment(gated=sys.argv[1]=='fit-gated') if sys.argv[1]!='evaluate' else evaluate()
