"""Collect public 2024 radar evidence separately from the frozen optical experiment."""
import argparse
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agro_checker import radar_data as r

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=('protocol','catalogue','collect'))
    a=p.parse_args();result=dict(protocol=r.protocol,catalogue=r.catalogues,collect=r.collect)[a.action]()
    print('Saved',a.action,'at',r.D)
