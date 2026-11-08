"""New derived snapshot from immutable observations; never rewrite old exports."""
import argparse
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path

from football_engine.team_profiles import attach_team_quant,content_hash
from football_engine.team_quant import TeamQuantStore,CALCULATION_REVISION,DEFINITION_REVISION


def file_hash(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(8*1024*1024),b''):h.update(block)
    return h.hexdigest()


def rebuild(profile_path,database,output):
    profile_path=Path(profile_path);database=Path(database);output=Path(output)
    base=json.loads(profile_path.read_text());store=TeamQuantStore(database);now=datetime.now(timezone.utc).isoformat()
    profiles={}
    for team,p in base['profiles'].items():
        p={**p,'as_of':now};p['version']=content_hash({k:v for k,v in p.items() if k!='version'})
        quant=store.profile(team,p['season_year'],'season',now)
        profiles[team]=attach_team_quant(p,quant)
    artifact={'kind':'REBUILT_DESCRIPTIVE_TEAM_METRICS','generated_at':now,'calculation_revision':CALCULATION_REVISION,
        'definition_revision':DEFINITION_REVISION,'base_profiles_path':str(profile_path),'base_profiles_sha256':file_hash(profile_path),
        'source_database_path':str(database),'source_database_sha256':file_hash(database),'profiles':profiles,
        'historically_pregame_certified':False,'predictive_accuracy_verified':False}
    with output.open('x',encoding='utf-8') as f:json.dump(artifact,f,indent=2,allow_nan=False)
    return {'path':str(output),'sha256':file_hash(output),'teams':len(profiles),'calculation_revision':CALCULATION_REVISION,'definition_revision':DEFINITION_REVISION}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--profiles',type=Path,required=True);p.add_argument('--database',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    print(json.dumps(rebuild(a.profiles,a.database,a.output)))
