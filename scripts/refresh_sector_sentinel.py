"""Collect/quality-check/publish an additive sector archive; never train on current outcomes."""
import argparse
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from agro_checker import sector_refresh as refresh
from agro_checker.sentinel_updates import update_lock


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('collect','analyse','publish'))
    parser.add_argument('--version',required=True)
    parser.add_argument('--through')
    parser.add_argument('--workers',type=int,default=2,choices=range(1,5))
    args=parser.parse_args()
    if not __import__('re').fullmatch(r'[a-zA-Z0-9_-]+',args.version):
        parser.error('Invalid version name')
    with update_lock(refresh.DIRECTORY):
        execute(args,parser)


def execute(args,parser):
    directory=refresh.DIRECTORY/'versions'/args.version
    if args.action=='collect':
        if not args.through:parser.error('collect requires explicit --through with timezone')
        result=refresh.collect(args.through,directory,args.workers)
        print(result['counts'])
    elif args.action=='analyse':
        result=refresh.analyse(directory)
        print(result['summary'])
    else:
        print(refresh.publish(directory))


if __name__=='__main__':main()
