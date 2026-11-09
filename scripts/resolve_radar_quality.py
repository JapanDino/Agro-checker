"""Retrieve canonical official RFI reports after a redirect-only first attempt."""
from concurrent.futures import ThreadPoolExecutor
from html import unescape
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import urljoin
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agro_checker import radar_data as d

Q=d.D/'quality'


def fetch(row):
    ident=row['id'].lstrip('#');url=row['href'].rstrip('/')+'/'
    raw=d.net.curl_bytes(url)
    if b'<html' not in raw.lower() or b'anomaly' not in raw.lower():raise ValueError('Empty/noncanonical disclaimer response')
    path=Q/('canonical-'+ident+'.html');path.write_bytes(raw)
    links=[urljoin(url,unescape(v)) for v in re.findall(r'href="([^"]+)"',raw.decode()) if '/download/' in v]
    pdfs=[]
    for i,link in enumerate(links):
        if not link.startswith('https://sar-mpc.eu/download/'):raise ValueError('Unregistered report host')
        request=subprocess.run(['curl.exe','--location','--max-redirs','5','--proto-redir','=https',
            '--fail','--silent','--show-error','--connect-timeout','10','--max-time','120','--max-filesize','20000000',link],
            capture_output=True,timeout=130)
        if request.returncode:raise ValueError('Official PDF download failed')
        b=request.stdout
        if not b.startswith(b'%PDF-'):raise ValueError('Expected official PDF')
        target=Q/(f'RFI-{ident}-{i+1}.pdf');target.write_bytes(b)
        pdfs.append(dict(path=target.relative_to(d.ROOT).as_posix(),sha256=d.sha(target),href=link))
    return dict(id=row['id'],source=url,description=row['description'],html=path.relative_to(d.ROOT).as_posix(),
        html_sha256=d.sha(path),pdfs=pdfs)


def run():
    path=Q/'rfi-reports.json'
    if path.exists():return d.read(path)
    rows=[v for v in d.read(Q/'listing.json')['relevant2024'] if 'residual RFI' in v['description'] and '2024' in v['description']]
    with ThreadPoolExecutor(3) as pool:reports=list(pool.map(fetch,rows))
    result=dict(created_at_utc=d.stamp(),reports=reports,post_fit_source_audit=True,scores_labels_thresholds_changed=False,
        first_attempt_note='Initial unsuffixed detail URLs returned empty HTTP redirects; those bytes retained. Canonical trailing-slash pages and validated PDF downloads used here.')
    d.save_once(path,result);print('Official monthly RFI reports',len(reports));return result


if __name__=='__main__':run()
