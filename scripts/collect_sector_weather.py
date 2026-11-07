"""Collect the 2024 sector archive, or frozen-test 2026 weather after selection."""
from pathlib import Path
import sys,argparse
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from agro_checker.sector_weather_data import collect_2024,collect_year
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('year',type=int,choices=[2024,2026]);year=p.parse_args().year
    collect_2024() if year==2024 else collect_year(year)
