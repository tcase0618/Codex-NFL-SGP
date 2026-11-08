import hashlib
import json
import sqlite3

import pytest

from football_engine.player_metrics import CATALOG
from football_engine.player_profile_store import PlayerProfileStore
from research.import_charted_player_metrics import run,validate


def record():
    return {'player_id':'p','metric_id':'qb.decision_grade','season':2024,'view':'season','value':75,
            'unit':'grade','sample_games':8,'sample_opportunities':200,'definition_reference':'fixture:provider-grade-v1',
            'method':'Fixture provider grade; no conversion to probabilities','period_start':'2024-09-01T00:00:00Z','period_end':'2024-11-01T00:00:00Z'}


@pytest.mark.parametrize('changes',[{'player_id':'wrong'},{'metric_id':'qb.made_up'},{'value':float('nan')},{'sample_games':0},
                                   {'sample_opportunities':0},{'unit':'%','value':101},{'view':'percentile'},{'period_end':'2099-11-01T00:00:00Z'}])
def test_unverifiable_or_invalid_import_records_are_rejected(changes):
    with pytest.raises(ValueError):validate({**record(),**changes},{'p'})


def test_source_bound_import_uses_new_snapshot_and_respects_knowledge_time(tmp_path):
    base=tmp_path/'base.sqlite'
    with sqlite3.connect(base) as db:
        db.executescript('CREATE TABLE players(id TEXT); INSERT INTO players VALUES("p"); CREATE TABLE sources(id INTEGER PRIMARY KEY,reference TEXT,sha256 TEXT,observed_at TEXT,kind TEXT,path TEXT);')
    before=hashlib.sha256(base.read_bytes()).hexdigest()
    source=tmp_path/'provider.json';source.write_text(json.dumps({'provider':'fixture','reference':'https://example.test/fixture','records':[record()]}))
    destination=tmp_path/'new';run(base,source,destination)
    assert hashlib.sha256(base.read_bytes()).hexdigest()==before
    store=PlayerProfileStore(destination/'players.sqlite');metric=next(m for m in CATALOG['QB'] if m['id']=='qb.decision_grade')
    actual=store.metric('p',metric,2024)
    assert actual['value']==75 and actual['source_ids'] and actual['formula']=='fixture:provider-grade-v1'
    assert store.metric('p',metric,2024,as_of='2023-01-01T00:00:00Z')['value'] is None
    assert store.metric('p',metric,2024,'confidence')['sample_games']==8
    with sqlite3.connect(destination/'players.sqlite') as db:
        with pytest.raises(sqlite3.IntegrityError):db.execute('UPDATE metric_values SET value=100')
