"""Archive available play-level sources without inventing historical vintages."""
import argparse
import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
from pathlib import Path

import requests


def collect(output,start=2006,end=2026):
    output.mkdir(parents=True,exist_ok=False)
    reference='https://api.github.com/repos/nflverse/nflverse-data/releases/tags/pbp'
    response=requests.get(reference,timeout=30);response.raise_for_status()
    (output/'release-pbp.json').write_bytes(response.content)
    assets=[]
    for asset in response.json().get('assets',[]):
        match=re.fullmatch(r'play_by_play_(\d{4})\.csv\.gz',asset['name'])
        if match and start<=int(match[1])<=end:
            assets.append({'name':asset['name'],'reference':asset['browser_download_url'],'season':int(match[1]),'kind':'pbp','provider_updated_at':asset['updated_at']})
    assets.append({'name':'games.csv','reference':'https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv','kind':'games'})
    def fetch(item):
        try:
            response=requests.get(item['reference'],timeout=90);response.raise_for_status()
            path=output/item['name']
            with path.open('xb') as handle:handle.write(response.content)
            return {**item,'path':str(path),'sha256':hashlib.sha256(response.content).hexdigest(),'observed_at':datetime.now(timezone.utc).isoformat(),'status':'ARCHIVED','historically_pregame_certified':False}
        except Exception as exc:return {**item,'status':'UNAVAILABLE','error':str(exc)}
    with ThreadPoolExecutor(max_workers=4) as executor:sources=list(executor.map(fetch,assets))
    manifest={'sources':sources,'requested_seasons':[start,end],'historically_pregame_certified':False,'missing_pbp_seasons':[y for y in range(start,end+1) if not any(s.get('season')==y and s['status']=='ARCHIVED' for s in sources)]}
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    return {'archived':sum(s['status']=='ARCHIVED' for s in sources),'missing':manifest['missing_pbp_seasons'],'errors':[s for s in sources if s['status']!='ARCHIVED']}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    print(json.dumps(collect(args.output),indent=2))
