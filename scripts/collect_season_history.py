"""Extra PAST satellite context; labels/cohorts from the old study stay fixed."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from agro_checker import farm_context_data as source
if len(sys.argv)!=2 or sys.argv[1] not in ['2024','2026']:
    raise SystemExit('Usage: collect_season_history.py 2024|2026')
year=int(sys.argv[1]);base=ROOT/'data/farm-research/crop-season-model'
if year==2026 and not (base/'frozen-selection.json').exists():
    raise SystemExit('Freeze the 2024 model before retrieving 2026 seasonal history')
D=base/f'context-{year}';D.mkdir(parents=True,exist_ok=True)
path=D/'protocol.json'
if not path.exists():
    original=source.read(ROOT/'data/farm-research/area-context/protocol.json')
    start,end=('2024-01-01T00:00:00Z','2024-02-29T23:59:59Z') if year==2024 else ('2026-03-01T00:00:00Z','2026-07-21T17:43:13Z')
    source.dump(path,{**original,'periods':[dict(id=f'past{year}',label=f'Past-only context {year}',start=start,end=end)],
        'used_by_model':'research features only; no new response labels',
        'policy':'Extra earlier observations only; original issue/target scene triples remain fixed. 2026 collection allowed only after model freeze.'})
source.D=D
source.satellite()
