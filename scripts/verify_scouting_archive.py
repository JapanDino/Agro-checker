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
    extra = 0
    for scene_id, scene in scenes.items():
        if scene['status'] != 'ready':
            continue
        directory = scene.get('observation_directory')
        path = (Path(directory) if directory else ROOT / 'data/farm-research/area-context') / 'sentinel' / scene_id / 'indices.npz'
        if not path.is_file():
            raise RuntimeError(f'Missing archive: {path.relative_to(ROOT)}')
        if hashlib.sha256(path.read_bytes()).hexdigest() != scene['crop_sha256']['indices.npz']:
            raise RuntimeError(f'Archive checksum mismatch: {scene_id}')
        if directory:
            extra += 1
        else:
            checked += 1
    if checked != 92:
        raise RuntimeError(f'Expected 92 archives, found {checked}')
    for period in ('recent2026', 'spring2024', *[p['id'] for p in farm_scouting.archive()[0]['periods'] if p['id'] not in ('recent2026','spring2024')]):
        comparison = farm_workspace.comparison(period)
        if comparison['pair']:
            print(f"{period}: {comparison['available_areas']}/{comparison['total_areas']} usable areas")
        else:
            print(f'{period}: no automatic common pair under the unchanged policy; select dates explicitly')
    print(f'{checked} satellite archives verified. No observations created.')
    print(f'{extra} additional published archives verified. Frozen base retained.')


if __name__ == '__main__':
    main()
