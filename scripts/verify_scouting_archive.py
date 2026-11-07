"""Read-only integrity check for a fresh clone. No download, fit, or journal write."""
import hashlib
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agro_checker import farm_scouting, farm_workspace


def main():
    scenes = farm_scouting.archive()[3]
    checked = 0
    for scene_id, scene in scenes.items():
        if scene['status'] != 'ready':
            continue
        path = ROOT / 'data/farm-research/area-context/sentinel' / scene_id / 'indices.npz'
        if not path.is_file():
            raise RuntimeError(f'Missing archive: {path.relative_to(ROOT)}')
        if hashlib.sha256(path.read_bytes()).hexdigest() != scene['crop_sha256']['indices.npz']:
            raise RuntimeError(f'Archive checksum mismatch: {scene_id}')
        checked += 1
    if checked != 92:
        raise RuntimeError(f'Expected 92 archives, found {checked}')
    for period in ('recent2026', 'spring2024'):
        comparison = farm_workspace.comparison(period)
        print(f"{period}: {comparison['available_areas']}/{comparison['total_areas']} usable areas")
    print(f'{checked} satellite archives verified. No observations created.')


if __name__ == '__main__':
    main()
