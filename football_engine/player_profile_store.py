"""Source-versioned player observations and denominator-safe metric views."""
import json
import math
import sqlite3
from functools import lru_cache
from datetime import datetime
from pathlib import Path

from .player_metrics import CATALOG, PLAY_METRICS, play_definition


def timestamp(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None: raise ValueError("timezone required")
    return parsed


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError): return None


# kind, numerator field(s), denominator field(s), scale; missing fields stay null.
RULES = {
    "completion": ("ratio", "completions", "attempts", 100),
    "interception_rate": ("ratio", "passing_interceptions", "attempts", 100),
    "yards_attempt": ("ratio", "passing_yards", "attempts", 1),
    "air_yards_attempt": ("ratio", "passing_air_yards", "attempts", 1),
    "passing_td_rate": ("ratio", "passing_tds", "attempts", 100),
    "sack_rate": ("ratio", "sacks_suffered", ("attempts", "sacks_suffered"), 100),
    "carries": ("sum", "carries", None, 1), "rush_yards": ("sum", "rushing_yards", None, 1),
    "yards_carry": ("ratio", "rushing_yards", "carries", 1),
    "rush_td_rate": ("ratio", "rushing_tds", "carries", 100),
    "rush_epa": ("ratio", "rushing_epa", "carries", 1),
    "first_down_run": ("ratio", "rushing_first_downs", "carries", 100),
    "targets": ("sum", "targets", None, 1), "receptions": ("sum", "receptions", None, 1),
    "receiving_yards": ("sum", "receiving_yards", None, 1),
    "catch": ("ratio", "receptions", "targets", 100),
    "yards_target": ("ratio", "receiving_yards", "targets", 1),
    "receiving_epa_target": ("ratio", "receiving_epa", "targets", 1),
    "receiving_epa": ("sum", "receiving_epa", None, 1),
    "yac_reception": ("ratio", "receiving_yards_after_catch", "receptions", 1),
    "adot": ("ratio", "receiving_air_yards", "targets", 1),
    "receiving_td_target": ("ratio", "receiving_tds", "targets", 100),
    "first_down_catch": ("ratio", "receiving_first_downs", "receptions", 100),
    "solo_tackles": ("sum", "def_tackles_solo", None, 1),
    "assisted_tackles": ("sum", "def_tackle_assists", None, 1),
    "sacks": ("sum", "def_sacks", None, 1), "qb_hits": ("sum", "def_qb_hits", None, 1),
    "interceptions": ("sum", "def_interceptions", None, 1),
    "pass_breakups": ("sum", "def_pass_defended", None, 1),
    "field_goal_attempts": ("sum", "fg_att", None, 1), "field_goals_made": ("sum", "fg_made", None, 1),
    "field_goal": ("ratio", "fg_made", "fg_att", 100),
    "fg_0_29": ("sum", ("fg_made_0_19", "fg_made_20_29"), None, 1),
    "fg_30_39": ("sum", "fg_made_30_39", None, 1), "fg_40_49": ("sum", "fg_made_40_49", None, 1),
    "fg_50_59": ("sum", "fg_made_50_59", None, 1), "fg_60": ("sum", "fg_made_60_", None, 1),
    "longest_fg": ("max", "fg_long", None, 1),
    "pat_attempts": ("sum", "pat_att", None, 1), "pat_made": ("sum", "pat_made", None, 1),
    "pat": ("ratio", "pat_made", "pat_att", 100),
    "kick_returns": ("sum", "kickoff_returns", None, 1), "kick_return_yards": ("sum", "kickoff_return_yards", None, 1),
    "yards_kick_return": ("ratio", "kickoff_return_yards", "kickoff_returns", 1),
    "punt_returns": ("sum", "punt_returns", None, 1), "punt_return_yards": ("sum", "punt_return_yards", None, 1),
    "yards_punt_return": ("ratio", "punt_return_yards", "punt_returns", 1),
    "time_throw": ("weighted", "avg_time_to_throw", "attempts", 1),
    "throw_depth": ("weighted", "avg_intended_air_yards", "attempts", 1),
    "aggressiveness": ("weighted", "aggressiveness", "attempts", 1),
    "cpoe": ("weighted", "completion_percentage_above_expectation", "attempts", 1),
    "separation": ("weighted", "avg_separation", "targets", 1),
    "yac_expected": ("weighted", "avg_yac_above_expectation", "receptions", 1),
    "time_los": ("weighted", "avg_time_to_los", "rush_attempts", 1),
    "ryoe_attempt": ("weighted", "rush_yards_over_expected_per_att", "rush_attempts", 1),
    "ryoe": ("sum", "rush_yards_over_expected", None, 1),
}

COMMON = {"Carries": "carries", "Rush yards": "rush_yards", "Yards / carry": "yards_carry",
          "Receptions": "receptions", "Targets": "targets", "Catch %": "catch", "Receiving yards": "receiving_yards",
          "Yards / target": "yards_target", "Receiving EPA / target": "receiving_epa_target", "EPA / target": "receiving_epa_target",
          "Receiving EPA": "receiving_epa", "YAC / reception": "yac_reception", "Average depth of target": "adot",
          "Touchdown / target": "receiving_td_target", "First-down conversion %": "first_down_catch",
          "Rushing EPA / play": "rush_epa", "Rushing EPA / attempt": "rush_epa", "First-down run %": "first_down_run",
          "Solo tackles": "solo_tackles", "Assisted tackles": "assisted_tackles", "Sacks": "sacks", "QB hits": "qb_hits",
          "Hits": "qb_hits", "Interceptions": "interceptions",
          "Average separation": "separation", "YAC above expected": "yac_expected", "YAC over expected": "yac_expected",
          "Time to line of scrimmage": "time_los", "RYOE / attempt": "ryoe_attempt", "Rush yards over expected": "ryoe"}
POSITION_RULES = {
    "QB": {"Completion %": "completion", "Interception rate": "interception_rate", "Yards / attempt": "yards_attempt",
           "Air yards / attempt": "air_yards_attempt", "Touchdown rate": "passing_td_rate", "Sack rate": "sack_rate",
           "QB rushing TD rate": "rush_td_rate", "Average throw depth": "throw_depth", "Time to throw": "time_throw",
           "Aggressiveness %": "aggressiveness", "CPOE": "cpoe"},
    "RB": {"Touchdown rate": "rush_td_rate"},
    "K": {"Field-goal attempts": "field_goal_attempts", "Field goals made": "field_goals_made", "Field-goal %": "field_goal",
          "Field goals 0–29 yards": "fg_0_29", "Field goals 30–39 yards": "fg_30_39", "Field goals 40–49 yards": "fg_40_49",
          "Field goals 50–59 yards": "fg_50_59", "Field goals 60+ yards": "fg_60", "Longest field goal": "longest_fg",
          "Extra-point attempts": "pat_attempts", "Extra points made": "pat_made", "Extra-point %": "pat"},
    "KR": {"Kick returns": "kick_returns", "Kick-return yards": "kick_return_yards", "Yards / kick return": "yards_kick_return"},
    "PR": {"Punt returns": "punt_returns", "Punt-return yards": "punt_return_yards", "Yards / punt return": "yards_punt_return"},
}
TRACKING = {"time_throw": "passing", "throw_depth": "passing", "aggressiveness": "passing", "cpoe": "passing",
            "separation": "receiving", "yac_expected": "receiving", "time_los": "rushing", "ryoe_attempt": "rushing", "ryoe": "rushing"}


def rule_for(metric):
    return POSITION_RULES.get(metric["position"], {}).get(metric["label"], COMMON.get(metric["label"]))


def fields_value(row, fields):
    fields = (fields,) if isinstance(fields, str) else fields
    values = [number(row.get(field)) for field in fields]
    return sum(values) if all(value is not None for value in values) else None


def aggregate(rows, rule):
    operation, numerator, denominator, scale = RULES[rule]
    pairs = []
    for row in rows:
        n = fields_value(row, numerator)
        d = fields_value(row, denominator) if denominator else None
        if n is not None and (not denominator or (d is not None and d > 0)):
            pairs.append((n, d))
    if not pairs: return None, 0, None
    if operation == "sum": return sum(n for n, _ in pairs), len(pairs), None
    if operation == "max": return max(n for n, _ in pairs), len(pairs), None
    total = sum(d for _, d in pairs)
    value = sum(n*d if operation == "weighted" else n for n, d in pairs) / total * scale
    return value, len(pairs), total


def definition(metric):
    rule = rule_for(metric)
    if not rule: return metric["definition"], "No approved calculation or licensed feed configured."
    operation, numerator, denominator, scale = RULES[rule]
    fields = lambda value: " + ".join(value) if isinstance(value, tuple) else value
    formula = f"{operation}({fields(numerator)})"
    if operation == "ratio": formula = f"sum({fields(numerator)}) / sum({fields(denominator)}) × {scale}"
    if operation == "weighted": formula = f"weighted mean of {fields(numerator)}, weighted by {fields(denominator)}"
    note = "Only rows with observed fields and positive denominators contribute. No missing field is treated as zero."
    if rule == "pass_breakups": note += " Provider def_pass_defended; includes all defended passes per provider definition and is not isolated non-interception breakups."
    return formula, note


class PlayerProfileStore:
    def __init__(self, path, charting_path=None):
        self.path = Path(path)
        self.charting_path = Path(charting_path).resolve() if charting_path else None
        with self.connect() as db:
            self.has_charted=bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='metric_values'").fetchone())

    def connect(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        return db

    def players(self, query="", position="", team="", limit=80):
        returners=self.returner_ids(position) if position in ("KR","PR") else None
        lookup_position="" if returners is not None else position
        with self.connect() as db:
            rows = db.execute("SELECT * FROM players WHERE name LIKE ? AND (?='' OR position=?) AND (?='' OR team=?) ORDER BY is_current DESC,name LIMIT ?",
                              (f"%{query}%", lookup_position, lookup_position, team, team, 100000 if returners is not None else min(200,max(1,limit)))).fetchall()
            return [dict(row) for row in rows if returners is None or row['id'] in returners][:min(200,max(1,limit))]

    @lru_cache(maxsize=2)
    def returner_ids(self, role):
        field={"KR":"kickoff_returns","PR":"punt_returns"}[role]
        with self.connect() as db:
            return frozenset(row[0] for row in db.execute("SELECT DISTINCT player_id FROM observations WHERE kind='stats' AND CAST(json_extract(data,?) AS REAL)>0",('$.'+field,)))

    @lru_cache(maxsize=4096)
    def observations(self, player_id, season, kind="stats", as_of=None, career=False):
        with self.connect() as db:
            rows = db.execute("SELECT o.*,s.reference,s.observed_at,s.sha256 FROM observations o JOIN sources s ON s.id=o.source_id WHERE o.player_id=? AND o.kind=? AND o.season<=? AND (? OR o.season=?) ORDER BY o.season,o.week,o.game_type",
                              (player_id, kind, season, int(career), season)).fetchall()
        # Current-vintage historical data must never be made available before
        # its actual collection time in an as-of query.
        if as_of:
            cutoff = timestamp(as_of)
            rows = [row for row in rows if timestamp(row["observed_at"]) <= cutoff]
        return [{**dict(row), "data": json.loads(row["data"])} for row in rows if row["game_type"] == "REG"]

    def metric(self, player_id, metric, season, view="season", as_of=None):
        rule = rule_for(metric)
        formula, note = definition(metric)
        result = {**metric, "value": None, "status": "UNAVAILABLE", "sample_games": 0, "sample_opportunities": None,
                  "formula": formula, "method_note": note, "source_ids": [], "coverage": None,
                  "confidence": "Not statistically calibrated", "reason": "Required charting, tracking or model input is not configured."}
        if self.has_charted:
            with self.connect() as db:
                imported=db.execute("SELECT v.*,s.observed_at FROM metric_values v JOIN sources s ON s.id=v.source_id WHERE v.player_id=? AND v.metric_id=? AND v.season=? AND v.view=? ORDER BY s.observed_at DESC",(player_id,metric['id'],season,'season' if view=='confidence' else view)).fetchall()
                cutoff=timestamp(as_of) if as_of else None
                imported=[row for row in imported if cutoff is None or timestamp(row['observed_at'])<=cutoff]
                if imported:
                    row=imported[0]
                    return {**result,'value':row['value'],'unit':row['unit'],'status':'PROVIDER_DERIVED',
                            'sample_games':row['sample_games'],'sample_opportunities':row['sample_opportunities'],
                            'source_ids':[row['source_id']],'formula':row['definition_reference'],'method_note':row['method'],
                            'coverage':{'period_start':row['period_start'],'period_end':row['period_end']},
                            'confidence':'Provider supplied; not independently calibrated','reason':None}
        if view == "percentile":
            base = self.metric(player_id, metric, season, "season", as_of)
            if base["value"] is None: return base
            with self.connect() as db:
                peers = [row[0] for row in db.execute("SELECT DISTINCT o.player_id FROM observations o LEFT JOIN players p ON p.id=o.player_id WHERE o.season=? AND o.kind IN ('stats','pbp_season') AND o.game_type='REG' AND COALESCE(json_extract(o.data,'$.position'),p.position)=?", (season, metric["position"]))]
            values = [self.metric(pid,metric,season,"season",as_of)["value"] for pid in peers]
            values = [value for value in values if value is not None]
            if len(values) < 2:
                result["reason"] = "Fewer than two same-position observed peers; percentile unavailable."
                return result
            rank = 100 * (sum(v < base["value"] for v in values) + .5*sum(v == base["value"] for v in values)) / len(values)
            return {**base,"value":round(rank,2),"unit":"percentile","status":"DESCRIPTIVE_PERCENTILE",
                    "peer_count":len(values),"raw_value":base["value"],
                    "method_note":"Midrank of raw values among same-season, same-position observed peers, with no opportunity floor. Higher rank is not always better; this is not a predictive grade."}
        if not rule:
            return self.play_metric(player_id, metric, season, view, as_of) or result
        operation, numerator, denominator, scale = RULES[rule]
        fields = (numerator,) if isinstance(numerator,str) else numerator
        if operation == "sum" and not any("yards" in field or "epa" in field for field in fields):
            result["unit"]="count"
        if rule == "longest_fg": result["unit"]="yd"
        if view not in {"season", "last3", "last5", "career", "yoy", "confidence", "home", "away"}:
            result["reason"] = "This split requires play-level context or an approved adjustment model; aggregate stats cannot establish it."
            return self.play_metric(player_id, metric, season, view, as_of) or result
        kind = TRACKING.get(rule, "stats")
        rows = self.observations(player_id, season, kind, as_of, view == "career")
        if view in ("last3", "last5"):
            # Use actual last N observed game rows, not last N rows with a
            # non-null metric; otherwise an injured/missing week is hidden.
            anchors = self.observations(player_id, season, "stats", as_of)[-int(view[-1]):]
            weeks = {row["week"] for row in anchors}
            rows = [row for row in rows if row["week"] in weeks]
        if view in ("home", "away"):
            anchors = self.observations(player_id, season, "stats", as_of)
            weeks = {row["week"] for row in anchors if row["data"].get("home_away") == view}
            rows = [row for row in rows if row["week"] in weeks]
        if view == "yoy":
            prior = self.observations(player_id, season-1, kind, as_of)
            weeks = {row["week"] for row in rows} & {row["week"] for row in prior}
            rows = [row for row in rows if row["week"] in weeks]
            prior = [row for row in prior if row["week"] in weeks]
        value, count, denominator = aggregate([row["data"] for row in rows], rule)
        if view == "yoy":
            prior_value, _, _ = aggregate([row["data"] for row in prior], rule)
            if value is None or prior_value is None:
                result["reason"] = "Both adjacent seasons require observed data."
                return result
            value -= prior_value
        if value is None:
            result["reason"] = "No usable observations for this player, season and source vintage."
            return self.play_metric(player_id, metric, season, view, as_of) or result
        result.update(value=round(value, 4), status="PROVIDER_DERIVED" if rule in TRACKING else "CALCULATED",
                      sample_games=count, sample_opportunities=denominator,
                      source_ids=sorted({row["source_id"] for row in rows}),
                      coverage={"start_season": min(row["season"] for row in rows), "end_season": max(row["season"] for row in rows),
                                "observed_weeks": sorted({row["week"] for row in rows})}, reason=None)
        if view == "yoy":
            result["method_note"] += " Year-over-year difference uses only week numbers observed in both adjacent seasons; not opponent adjusted."
            result["source_ids"] = sorted(set(result["source_ids"]) | {row["source_id"] for row in prior})
        if rule in TRACKING: result["confidence"] = "Provider-qualified sample; no independent interval computed"
        return result

    def play_metric(self, player_id, metric, season, view="season", as_of=None):
        """Read independently denominated play observations; never splice sources."""
        spec = PLAY_METRICS.get(metric["id"])
        supported = {"season", "career", "last3", "last5", "confidence", "home", "away",
                     "first_half", "second_half", "third_down"}
        if not spec or view not in supported:
            return None
        split = view if view in {"home", "away", "first_half", "second_half", "third_down"} else "season"
        rows = self.observations(player_id, season, "pbp_" + split, as_of, view == "career")
        if view in {"last3", "last5"}:
            # Anchor on the union of actual box-score and play-observed games,
            # not the most recent games that happen to qualify for this metric.
            anchors = self.observations(player_id, season, "stats", as_of) + self.observations(player_id, season, "pbp_season", as_of)
            weeks = sorted({row["week"] for row in anchors})[-int(view[-1]):]
            rows = [row for row in rows if row["week"] in weeks]
        key = metric["id"]
        measured = [(row, number(row["data"].get(key + ":n")), number(row["data"].get(key + ":d"))) for row in rows]
        measured = [(row, n, d) for row, n, d in measured if n is not None and d is not None and d > 0]
        if not measured:
            return None
        numerator = sum(n for _, n, _ in measured)
        denominator = sum(d for _, _, d in measured)
        value = numerator if spec["operation"] in {"sum", "count"} else numerator / denominator * spec["scale"]
        definition = play_definition(key)
        return {**metric, "value": round(value, 4), "status": "CALCULATED", "unit": "count" if spec["operation"] == "count" else metric["unit"],
                "sample_games": len({row["data"]["game_id"] for row, _, _ in measured}),
                "sample_opportunities": denominator, "source_ids": sorted({row["source_id"] for row, _, _ in measured}),
                "formula": definition["formula"], "definition": definition["formula"],
                "definition_status": "validated_public_event_adapter_v1",
                "denominator_contract": "Observed qualifying events with finite required measurements; metric-specific denominator",
                "method_note": json.dumps(definition, sort_keys=True),
                "coverage": {"observed_weeks": sorted({row["week"] for row, _, _ in measured}),
                             "start_season": min(row["season"] for row, _, _ in measured),
                             "end_season": max(row["season"] for row, _, _ in measured)},
                "confidence": "Observed attributed events only; not statistically calibrated", "reason": None}

    def supplemental_context(self, player_id, season=2026, as_of=None, week_max=None):
        """Independent public snap/NGS source rows, never advanced catalog proxies.

        Exact season only; NGS week 0 aggregates remain separate from game rows.
        Both snap and identity-crosswalk vintages must be visible.
        """
        result = {"schema": "PLAYER_SUPPLEMENTAL_CONTEXT_V1", "player_id": player_id, "season": season,
                  "status": "UNAVAILABLE", "snaps": [], "snap_totals": {}, "ngs": [], "sources": [],
                  "scope": "Exact-season REG source rows; independent of advanced catalog and profile view",
                  "limits": ["Offense/defense/ST snaps are participation counts, not routes, pass-block or coverage snaps.",
                             "NGS week 0 is a provider season aggregate; it is never added to weekly rows.",
                             "Missing observations do not establish zero participation or availability."]}
        if self.charting_path is None:
            result["reason"] = "No independent charting snapshot configured."
            return result
        cutoff = timestamp(as_of) if as_of else None
        with sqlite3.connect(self.charting_path.as_uri() + "?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            sources = {row['id']: dict(row) for row in db.execute('SELECT * FROM sources')}
            def visible(ids):
                return all(sid in sources and (cutoff is None or timestamp(sources[sid]['observed_at']) <= cutoff) for sid in ids)
            used = set()
            rows = db.execute("SELECT s.*,c.gsis_id AS mapped_gsis,c.source_id AS mapped_source FROM player_snaps s JOIN player_crosswalk c ON c.pfr_id=s.pfr_id WHERE s.gsis_id=? AND s.season=? ORDER BY s.week,s.game_id,s.team", (player_id, season))
            for source_row in rows:
                row = dict(source_row)
                ids = [row['source_id'], row['crosswalk_source_id']]
                if row['mapped_gsis'] != player_id or row['mapped_source'] != row['crosswalk_source_id'] or not visible(ids): continue
                data = json.loads(row['data'])
                if data.get('game_type') != 'REG' or (week_max is not None and row['week'] > week_max): continue
                counts = {}
                for field in ('offense_snaps', 'defense_snaps', 'st_snaps'):
                    value = number(row[field])
                    counts[field] = value if value is not None and value >= 0 and value.is_integer() else None
                counts['total_snaps'] = sum(counts.values()) if all(v is not None for v in counts.values()) else None
                result['snaps'].append({k: row[k] for k in ('game_id', 'pfr_id', 'season', 'week', 'team')} | counts | {'source_ids': ids})
                used.update(ids)
            # Return measurements as source rows rather than assuming common
            # qualification denominators across different NGS measurements.
            tracking_fields = {'passing': ('attempts', 'avg_time_to_throw', 'avg_intended_air_yards', 'aggressiveness', 'completion_percentage_above_expectation'),
                               'receiving': ('targets', 'receptions', 'avg_separation', 'avg_cushion', 'avg_yac', 'avg_yac_above_expectation', 'avg_intended_air_yards'),
                               'rushing': ('rush_attempts', 'avg_time_to_los', 'rush_yards_over_expected', 'rush_yards_over_expected_per_att', 'percent_attempts_gte_eight_defenders')}
            for source_row in db.execute("SELECT * FROM ngs_observations WHERE gsis_id=? AND season=? AND season_type='REG' ORDER BY kind,week,team", (player_id, season)):
                row = dict(source_row)
                if not visible([row['source_id']]): continue
                # A season aggregate cannot satisfy a partial-season week cutoff.
                if week_max is not None and (row['week'] == 0 or row['week'] > week_max): continue
                data = json.loads(row['data'])
                if data.get('player_gsis_id') != player_id: continue
                fields = {key: number(data.get(key)) for key in tracking_fields.get(row['kind'], ())}
                if not any(value is not None for value in fields.values()): continue
                result['ngs'].append({k: row[k] for k in ('kind', 'season', 'season_type', 'week', 'team')} |
                                     {'grain': 'provider_season_aggregate' if row['week'] == 0 else 'player_week_team',
                                      'fields': fields, 'raw_fields': data, 'source_ids': [row['source_id']]})
                used.add(row['source_id'])
        for field in ('offense_snaps', 'defense_snaps', 'st_snaps', 'total_snaps'):
            values = [row[field] for row in result['snaps'] if row[field] is not None]
            result['snap_totals'][field] = {'value': sum(values) if values else None,
                'observed_rows': len(values), 'missing_rows': len(result['snaps']) - len(values)}
        result['sources'] = [sources[sid] for sid in sorted(used)]
        if result['snaps'] or result['ngs']:
            result['status'] = 'OBSERVED_PUBLIC_CONTEXT'
            result['reason'] = None
        else:
            result['reason'] = 'No visible, identity-mapped source observations for this season.'
        return result

    def profile(self, player_id, season=2026, view="season", role=None, as_of=None):
        with self.connect() as db:
            row = db.execute("SELECT * FROM players WHERE id=?", (player_id,)).fetchone()
            if not row: raise KeyError(player_id)
            sources = [dict(source) for source in db.execute("SELECT id,reference,sha256,observed_at,kind FROM sources")]
            seasons = [row[0] for row in db.execute("SELECT DISTINCT season FROM observations WHERE player_id=? ORDER BY season DESC", (player_id,))]
        player = dict(row)
        role = role or player["position"]
        if role not in CATALOG and role != "UNKNOWN": raise ValueError("unknown role")
        metrics = [self.metric(player_id, metric, season, view, as_of) for metric in CATALOG.get(role,[])]
        history = []
        for year in seasons:
            point = {"season": year, "metrics": {}}
            for metric in CATALOG.get(role,[]):
                if rule_for(metric) or metric["id"] in PLAY_METRICS:
                    value = self.metric(player_id, metric, year, "season", as_of)
                    if value["value"] is not None: point["metrics"][metric["id"]] = value["value"]
            if point["metrics"]: history.append(point)
        return {"player": player, "role": role, "season": season, "view": view, "seasons": seasons,
                "observed_teams": sorted({row["data"].get("team") for row in self.observations(player_id,season,"stats",as_of) if row["data"].get("team")}),
                "metrics": metrics, "history": history, "sources": sources,
                "coverage": {"defined": len(metrics), "populated": sum(metric["value"] is not None for metric in metrics)},
                "game_day_active": "UNCONFIRMED", "historically_pregame_certified": False,
                "matchup_projection": {"status": "UNAVAILABLE", "reason": "Requires calibrated opportunity and shared scenario inputs; metric profiles alone do not produce probabilities."}}
