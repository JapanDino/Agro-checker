"""Register and run a separate 2024 radar ablation; leave frozen future study alone."""
import argparse
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agro_checker import radar_experiment as study

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=('protocol','run'))
    print(dict(protocol=study.protocol,run=study.run)[p.parse_args().action]().get('selected_development','Protocol saved'))
