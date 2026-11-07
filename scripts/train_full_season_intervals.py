"""Run the predeclared 2024-only full-season experiment, then frozen evaluation."""
from pathlib import Path
import sys
import argparse
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from agro_checker.full_season_intervals import protocol,fit,evaluate

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage',choices=['protocol','fit','evaluate'])
    stage=parser.parse_args().stage
    {'protocol':protocol,'fit':fit,'evaluate':evaluate}[stage]()
