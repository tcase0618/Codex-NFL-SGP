"""Overlay routing/provenance; fixed fixtures are not real football estimates."""
import hashlib
import json
import sqlite3

import pytest

from football_engine.team_quant import TeamQuantStore
from test_team_quant import quant


@pytest.fixture
def context_overlay(quant,tmp_path):
    path=tmp_path/'context.sqlite'
    source={'id':'context:fixture','reference':'fixture:raw','sha256':'a'*64,
            'observed_at':'2026-11-04T12:00:00Z','path':'fixture','kind':'pbp'}
    value={'id':'matchup.defense_vs_deep_passing','value':.125,'unit':'EPA/play',
           'status':'DESCRIPTIVE_DEEP_PASS_COHORT','sample_plays':120,'sample_games':3,
           'source_ids':['context:fixture'],'qualified':True,'game_ids':[],
           'formula':'synthetic integration value; not empirical evidence'}
    with sqlite3.connect(path) as db:
        db.executescript('CREATE TABLE metadata(key TEXT,value TEXT); CREATE TABLE sources(id TEXT,data TEXT); CREATE TABLE metrics(team TEXT,season INTEGER,view TEXT,metric_id TEXT,data TEXT); CREATE TABLE current_personnel(team TEXT,kind TEXT,source_id TEXT,data TEXT);')
        for key,val in {'as_of':'2026-11-04T12:00:00Z','input_sha256':hashlib.sha256(open(quant.path,'rb').read()).hexdigest()}.items():
            db.execute('INSERT INTO metadata VALUES(?,?)',(key,json.dumps(val)))
        db.execute('INSERT INTO sources VALUES(?,?)',(source['id'],json.dumps(source)))
        db.execute('INSERT INTO metrics VALUES(?,?,?,?,?)',('CHI',2026,'season',value['id'],json.dumps(value)))
    return path


def test_context_overlay_reaches_actual_profile_and_inspectable_sources(quant,context_overlay):
    store=TeamQuantStore(quant.path,context_path=context_overlay)
    result=store.profile('CHI',2026)
    metric=next(m for m in result['metrics'] if m['id']=='matchup.defense_vs_deep_passing')
    assert metric['value']==.125 and metric['sample_plays']==120
    assert metric['source_ids']==['context:fixture']
    assert any(source['id']=='context:fixture' for source in result['sources'])
    assert result['coverage']['defined']==401
    evidence=store.evidence('CHI',2026,'matchup.defense_vs_deep_passing')
    assert evidence['evidence_kind']=='SOURCED_AGGREGATE'
    assert evidence['aggregate']['value']==.125
    assert evidence['sources'][0]['id']=='context:fixture'
    assert evidence['rows']==[] and evidence['total_eligible_rows'] is None
    assert store.value('CHI',2026,'matchup.defense_vs_deep_passing',as_of='2026-11-03T23:00:00Z')['value'] is None
    assert store.value('CHI',2025,'matchup.defense_vs_deep_passing')['value'] is None


def test_overlay_cannot_be_attached_to_another_base_snapshot(quant,context_overlay):
    with sqlite3.connect(quant.path) as db:db.execute("INSERT INTO metadata VALUES('changed','true')")
    with pytest.raises(ValueError,match='hash'):
        TeamQuantStore(quant.path,context_path=context_overlay)


def test_charting_source_identity_survives_profile_and_evidence_routing(quant):
    source={'id':'charting:fixture','reference':'fixture:chart','sha256':'b'*64,
            'observed_at':'2026-11-04T12:00:00Z','kind':'charting'}
    class Reader:
        def sources(self):return [source]
        def value(self,team,season,metric_id,view='season',as_of=None):
            if metric_id!='scheme.play_action_frequency':return None
            return {'value':25,'sample_plays':120,'sample_games':3,'qualified':True,
                    'source_ids':[source['id']],'status':'CALCULATED','numerator':30}
    quant.charting_metrics=Reader()
    result=quant.profile('CHI',2026)
    metric=next(m for m in result['metrics'] if m['id']=='scheme.play_action_frequency')
    assert metric['source_ids']==[source['id']]
    assert source in result['sources']
    evidence=quant.evidence('CHI',2026,metric['id'])
    assert evidence['evidence_kind']=='SOURCED_AGGREGATE'
    assert evidence['aggregate']['numerator']==30
    assert evidence['sources']==[source]
