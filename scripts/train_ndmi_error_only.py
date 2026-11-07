"""Isolated NDMI bounds: 2024-only fit, then a single frozen evaluation."""
from pathlib import Path
import argparse
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from agro_checker.ndmi_error_experiment import protocol,fit,evaluate

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('stage',choices=['protocol','fit','evaluate'])
    {'protocol':protocol,'fit':fit,'evaluate':evaluate}[p.parse_args().stage]()
