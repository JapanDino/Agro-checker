"""Separate commands enforce protocol -> 2024 fit/freeze -> read-only evaluation."""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tmp/georef-deps')]
from agro_checker.conservative_decline import protocol, fit, evaluate

if __name__ == '__main__':
    actions = dict(protocol=protocol, fit=fit, evaluate=evaluate)
    if len(sys.argv) != 2 or sys.argv[1] not in actions:
        raise SystemExit('Usage: train_conservative_decline.py protocol|fit|evaluate')
    actions[sys.argv[1]]()
