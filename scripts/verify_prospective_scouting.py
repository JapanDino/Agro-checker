"""Read-only replay of sealed predictions; no training, outcome collection or new releases."""
import argparse
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import numpy as np
import joblib
from agro_checker import prospective_scouting as s


def verify(directory=s.DIRECTORY):
    directory=Path(directory);settings,protocol_sha=s.configuration(directory)
    models={name:joblib.load(directory/'frozen'/meta['file']) for name,meta in settings['models'].items()}
    releases=0
    for path in sorted((directory/'releases').glob('*.json')):
        r=s.checked(path)['payload'];rows=r['candidates']
        if r['protocol_sha256']!=protocol_sha or path.stem!=r['anchor_acquisition_date']:
            raise ValueError('Wrong release identity/protocol')
        issued=s.instant(r['issued_at_utc']);cutoff=s.instant(r['feature_cutoff_utc'])
        if not s.instant(settings['registered_at_utc'])<=cutoff<=issued<s.instant(r['target_window']['start_utc']):
            raise ValueError('Nonprospective timing')
        for meta in r['source_scenes']:
            if s.instant(meta['received_at_utc'])>cutoff or s.instant(meta['acquired_at_utc'])>cutoff:
                raise ValueError('Input unavailable at feature cutoff')
            folder=ROOT/meta['directory']
            if s.sha(folder/'indices.npz')!=meta['indices_sha256'] or s.sha(folder/'item.json')!=meta['item_sha256']:
                raise ValueError('Changed release source')
        if any(row['y'] is not None or row['target'] is not None for row in rows):
            raise ValueError('Outcome leaked into sealed issue record')
        scores=dict(recent_drop=s.base.rule_score(rows,'recent_drop'))
        scores.update({name:s.base.score(model,rows) for name,model in models.items()})
        for name,values in scores.items():np.testing.assert_allclose(values,r['scores'][name],rtol=1e-12,atol=1e-12)
        for name,v in [('baseline',settings['baseline']),('candidate',settings['candidate'])]:
            chosen=s.repair.choose(rows,{n:np.array(values) for n,values in r['scores'].items()},v,settings['ranking_parameters'])
            if [row['fid'] for row,c in zip(rows,chosen) if c]!=r['selected'][name]:raise ValueError('Changed saved ranking')
        if len(r['selected']['baseline'])!=len(r['selected']['candidate']) or r['slots_each']!=min(settings['budget_k'],len(rows)):
            raise ValueError('Unequal budget')
        releases+=1
    summary=s.report(directory)  # Checks every finalised result's link to its exact release.
    return dict(releases_verified=releases,finalised=summary['finalised'],pending=summary['pending'],
                model_training_performed=False,records_written=0)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',type=Path,default=s.DIRECTORY)
    print(verify(parser.parse_args().directory))
