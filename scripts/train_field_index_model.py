"""Train an isolated case-aligned research model, never the operational detector."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tmp/georef-deps')]
from agro_checker.field_index_experiment import run
run()
