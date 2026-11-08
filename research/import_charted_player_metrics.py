"""Import explicitly sourced metric values into a NEW snapshot, never overwrite.

This supports any catalog field without approximating licensed charting metrics.
The input JSON is archived and hashed; its values remain provider-supplied,
not independently certified as historical pregame observations.
"""
import argparse
import hashlib
import json
import math
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from football_engine.player_metrics import CATALOG, VIEWS
from football_engine.player_profile_store import timestamp


def validate(record, players):
    definitions={metric['id']:metric for metrics in CATALOG.values() for metric in metrics}
    if record.get('player_id') not in players or record.get('metric_id') not in definitions:
        raise ValueError('unknown stable player ID or metric ID')
    if record.get('view') not in dict(VIEWS) or record['view'] in ('percentile','confidence'):
        raise ValueError('unsupported imported view; percentile and confidence are computed locally')
    value=record.get('value')
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):
        raise ValueError('finite numeric value required')
    if not all(record.get(key) for key in ('unit','definition_reference','method','period_start','period_end')):
        raise ValueError('unit, definition, method and coverage period required')
    start,end=timestamp(record['period_start']),timestamp(record['period_end'])
    if start>end or end>datetime.now(timezone.utc):raise ValueError('invalid or future observation period')
    if not isinstance(record.get('season'),int) or not 2006<=record['season']<=end.year:
        raise ValueError('invalid season')
    if not isinstance(record.get('sample_games'),int) or record['sample_games']<=0:
        raise ValueError('positive sample size required')
    opportunities=record.get('sample_opportunities')
    if opportunities is not None and (not isinstance(opportunities,(int,float)) or isinstance(opportunities,bool) or not math.isfinite(opportunities) or opportunities<=0):
        raise ValueError('invalid denominator sample')
    if record['unit']=='%' and not 0<=value<=100:raise ValueError('percentage outside 0–100')
    return record


def run(base, source, output):
    payload=json.loads(source.read_text(encoding='utf-8'))
    if not payload.get('provider') or not str(payload.get('reference','')).startswith('https://'):
        raise ValueError('provider and HTTPS source reference required')
    with sqlite3.connect(base) as db:players={row[0] for row in db.execute('SELECT id FROM players')}
    records=[validate(record,players) for record in payload.get('records',[])]
    keys=[(r['player_id'],r['metric_id'],r['season'],r['view']) for r in records]
    if not records or len(keys)!=len(set(keys)):raise ValueError('empty import or duplicate metric windows')
    output.mkdir(parents=True,exist_ok=False)
    archived=output/'charted-input.json';shutil.copy2(source,archived)
    destination=output/'players.sqlite';shutil.copy2(base,destination)
    observed=datetime.now(timezone.utc).isoformat();content_hash=hashlib.sha256(archived.read_bytes()).hexdigest()
    with sqlite3.connect(destination) as db:
        db.executescript('''CREATE TABLE IF NOT EXISTS metric_values(player_id TEXT,metric_id TEXT,season INTEGER,view TEXT,value REAL,unit TEXT,sample_games INTEGER,sample_opportunities REAL,period_start TEXT,period_end TEXT,definition_reference TEXT,method TEXT,source_id INTEGER,PRIMARY KEY(player_id,metric_id,season,view,source_id));
            CREATE TRIGGER IF NOT EXISTS metric_values_no_update BEFORE UPDATE ON metric_values BEGIN SELECT RAISE(ABORT,'immutable metric'); END;
            CREATE TRIGGER IF NOT EXISTS metric_values_no_delete BEFORE DELETE ON metric_values BEGIN SELECT RAISE(ABORT,'immutable metric'); END;''')
        sid=db.execute('INSERT INTO sources(reference,sha256,observed_at,kind,path) VALUES(?,?,?,?,?)',
                       (payload['reference'],content_hash,observed,'charted:'+payload['provider'],str(archived))).lastrowid
        for r in records:
            db.execute('INSERT INTO metric_values VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',tuple(r[k] for k in ('player_id','metric_id','season','view','value','unit','sample_games'))+(r.get('sample_opportunities'),)+tuple(r[k] for k in ('period_start','period_end','definition_reference','method'))+(sid,))
    report={'imported':len(records),'source_sha256':content_hash,'observed_at':observed,'historically_pregame_certified':False}
    (output/'import-report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--base',type=Path,required=True);parser.add_argument('--source',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();print(json.dumps(run(args.base,args.source,args.output),indent=2))
