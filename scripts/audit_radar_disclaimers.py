"""Post-fit source-quality audit only. Never alter scores, labels or source crops."""
from html import unescape
from pathlib import Path
import re
import sys
from urllib.parse import urljoin

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agro_checker import radar_data as d

Q=d.D/'quality'
ROOT='https://sar-mpc.eu/disclaimers/'


def text(value):return unescape(re.sub(r'<[^>]+>','',value)).strip()


def listing(raw):
    html=raw.decode('utf-8');body=re.search(r'<tbody>(.*?)</tbody>',html,re.S).group(1);rows=[]
    for tr in re.findall(r'<tr>(.*?)</tr>',body,re.S):
        td=re.findall(r'<td[^>]*>(.*?)</td>',tr,re.S)
        if len(td)!=7:raise ValueError('Changed official listing schema')
        values=[text(v) for v in td];href=re.search(r'href="([^"]+)"',td[0]).group(1)
        rows.append(dict(id=values[0],mission=values[1],status=values[2],modified=values[3],start=values[4],stop=values[5],
            description=values[6],href=urljoin(ROOT,href)))
    links=re.findall(r'<a\b([^>]*aria-label="Next"[^>]*)>',html)
    if len(links)>1:raise ValueError('Ambiguous official pagination')
    match=re.search(r'href="([^"]+)"',links[0]) if links else None
    return rows,urljoin(ROOT,unescape(match.group(1))) if match else None


def run():
    if (Q/'listing.json').exists():return d.read(Q/'listing.json')
    Q.mkdir(parents=True,exist_ok=True);url=ROOT;seen=set();rows=[];pages=[]
    while url:
        if url in seen or len(seen)>=80 or not url.startswith(ROOT):raise ValueError('Unsafe/incomplete official pagination')
        seen.add(url);raw=d.net.curl_bytes(url);path=Q/f'list-{len(seen):02d}.html';path.write_bytes(raw)
        items,url=listing(raw);rows.extend(items);pages.append(dict(path=path.relative_to(d.ROOT).as_posix(),sha256=d.sha(path)))
    if len({v['id'] for v in rows})!=len(rows):raise ValueError('Duplicate changing quality catalogue')
    relevant=[v for v in rows if v['mission']=='S1A' and v['start'][:4]<='2024' and v['stop'][:4]>='2024']
    for row in relevant:
        raw=d.net.curl_bytes(row['href']);path=Q/('detail-'+row['id'].lstrip('#')+'.html');path.write_bytes(raw)
        row['detail_path']=path.relative_to(d.ROOT).as_posix();row['detail_sha256']=d.sha(path)
        row['links']=[urljoin(row['href']+'/',unescape(v)) for v in re.findall(r'href="([^"]+)"',raw.decode('utf-8'))
                      if any(k in v.lower() for k in ('csv','json','product','download','attachment'))]
        print(row['id'],row['description'],row['links'],flush=True)
    result=dict(created_at_utc=d.stamp(),source=ROOT,pagination_complete=True,records=len(rows),pages=pages,
        relevant2024=relevant,post_fit_source_audit=True,scores_and_labels_changed=False)
    d.save_once(Q/'listing.json',result);return result


if __name__=='__main__':print('Official quality records:',run()['records'])
