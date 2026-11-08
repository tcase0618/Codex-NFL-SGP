"""Build a new immutable-vintage player database from archived public sources."""
import argparse
import csv
import gzip
import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from football_engine.player_metrics import CATALOG, VIEWS
from .complete_parlays import BASE, cached_json, load_games
from .source_based_parlays import instant

ALIASES = {"LA": "LAR", "WAS": "WSH", "JAC": "JAX", "SD": "LAC", "OAK": "LV", "STL": "LAR"}
POSITIONS = {"HB":"RB", "SAF":"S", "T": "OT", "LT": "OT", "RT": "OT", "G": "OG", "LG": "OG", "RG": "OG",
             "DE": "EDGE", "DT": "DI", "NT": "DI", "DL": "DI", "OLB": "LB", "ILB": "LB", "MLB": "LB",
             "FS": "S", "SS": "S", "DB": "CB", "OL": "OT"}


def position(value):
    return POSITIONS.get(value, value) if value in CATALOG or value in POSITIONS else "UNKNOWN"


def rows(path):
    handle = gzip.open(path, "rt", encoding="utf-8-sig", newline="") if path.name.endswith(".gz") else path.open(encoding="utf-8-sig", newline="")
    with handle: yield from csv.DictReader(handle)


def build(root, archive, output):
    output.mkdir(parents=True, exist_ok=False)
    observed = datetime.now(timezone.utc).isoformat()
    db_path = output / "players.sqlite"
    db = sqlite3.connect(db_path)
    db.executescript("""
        CREATE TABLE players(id TEXT PRIMARY KEY,espn_id TEXT,name TEXT,position TEXT,raw_position TEXT,team TEXT,is_current INTEGER,roster_status TEXT,identity_note TEXT);
        CREATE TABLE sources(id INTEGER PRIMARY KEY,reference TEXT,sha256 TEXT,observed_at TEXT,kind TEXT,path TEXT);
        CREATE TABLE observations(player_id TEXT,season INTEGER,week INTEGER,game_type TEXT,kind TEXT,data TEXT,source_id INTEGER,
          PRIMARY KEY(player_id,season,week,game_type,kind),FOREIGN KEY(source_id) REFERENCES sources(id));
        CREATE INDEX observations_player_kind ON observations(player_id,kind,season);
        CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT);
        CREATE TRIGGER observation_no_update BEFORE UPDATE ON observations BEGIN SELECT RAISE(ABORT,'append-only observations'); END;
        CREATE TRIGGER observation_no_delete BEFORE DELETE ON observations BEGIN SELECT RAISE(ABORT,'append-only observations'); END;
        CREATE TRIGGER source_no_update BEFORE UPDATE ON sources BEGIN SELECT RAISE(ABORT,'immutable source'); END;
        CREATE TRIGGER source_no_delete BEFORE DELETE ON sources BEGIN SELECT RAISE(ABORT,'immutable source'); END;
    """)
    manifest = json.loads((archive / "manifest.json").read_text(encoding="utf-8"))
    identities = {}
    player_file = archive / "players.csv"
    if player_file.exists():
        for row in rows(player_file):
            if row.get("gsis_id"): identities[row["gsis_id"]] = row
    identity_conflicts, duplicates = [], []
    last_seen={}
    def player(pid, name, raw_position, team, espn="", current=0, status="", note="", order=None):
        if not pid: return
        old = db.execute("SELECT * FROM players WHERE id=?", (pid,)).fetchone()
        if old and order and order < last_seen.get(pid,(0,0)): return
        if order: last_seen[pid]=order
        identity = identities.get(pid, {})
        raw_position = raw_position or identity.get("position", "") or (old[4] if old else "")
        espn = espn or identity.get("espn_id", "")
        if old and old[1] and espn and old[1] != espn:
            identity_conflicts.append({"gsis_id": pid, "existing_espn": old[1], "incoming_espn": espn})
            db.execute("UPDATE players SET espn_id='',team=?,is_current=?,roster_status=?,identity_note='CONFLICT: ESPN aliases require source reconciliation; ESPN observations quarantined' WHERE id=?",(ALIASES.get(team,team),current,status,pid))
            return
        if old and old[6] and not current: return
        db.execute("INSERT OR REPLACE INTO players VALUES(?,?,?,?,?,?,?,?,?)", (
            pid, espn, name or identity.get("display_name", pid), position(raw_position), raw_position,
            ALIASES.get(team, team), current, status, note or ("Role requires alignment review" if raw_position in ("DL","OL","DB","OLB") else "")))
    def source(record):
        path = Path(record["path"])
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != record["sha256"]: raise ValueError(f"source hash mismatch: {path}")
        return db.execute("INSERT INTO sources(reference,sha256,observed_at,kind,path) VALUES(?,?,?,?,?)", (
            record["reference"], actual, record.get("observed_at", observed), record["kind"], str(path))).lastrowid
    counts = Counter()
    for record in manifest["sources"]:
        if record["status"] != "ARCHIVED": continue
        sid = source(record)
        if record["kind"] == "players": continue
        kind = "stats" if record["kind"] == "player_stats" else record["name"].split("_")[-1].split(".")[0]
        for row in rows(Path(record["path"])):
            pid = row.get("player_id") or row.get("player_gsis_id")
            week = int(row.get("week") or 0)
            if not pid or week <= 0 or row.get("season_type") != "REG": continue
            year = int(row["season"])
            player(pid, row.get("player_display_name", pid), row.get("position", row.get("player_position", "")), row.get("team", row.get("team_abbr", "")),order=(year,week))
            try:
                db.execute("INSERT INTO observations VALUES(?,?,?,?,?,?,?)", (pid, year, week, "REG", kind, json.dumps(row, allow_nan=False), sid))
                counts[kind] += 1
            except sqlite3.IntegrityError:
                duplicates.append({"player_id": pid, "season": year, "week": week, "kind": kind})
    # Supplement identities/current membership using stable cross-provider IDs.
    # No name-only matches; a roster status is not game-day confirmation.
    roster_files = sorted((root / "work/historical-nflverse").glob("*-roster_weekly_2026.csv"))
    roster_sid = None
    current_rows = {}
    if roster_files:
        path = roster_files[-1]
        roster_sid = source({"path": str(path), "reference": "https://github.com/nflverse/nflverse-data/releases/download/weekly_rosters/roster_weekly_2026.csv",
                             "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "kind": "identity_roster", "observed_at": observed})
        for row in rows(path):
            pid = row.get("gsis_id")
            if not pid or row.get("game_type") != "REG": continue
            old = current_rows.get(pid)
            # Multiple historical clubs may be present in the same weekly file;
            # non-CUT entries can describe latest membership but never activity.
            rank = (int(row["week"]), row["status"] not in ("CUT","TRD","RET"))
            if old is None or rank > old[0]: current_rows[pid] = (rank, row)
        for _, row in current_rows.values():
            player(row["gsis_id"], row["full_name"], row["position"], row["team"], row["espn_id"],
                   int(row["status"] not in ("CUT","TRD","RET")), row["status"], "Weekly roster membership; game-day status unconfirmed")
    crosswalk = defaultdict(set)
    for row in db.execute("SELECT id,espn_id FROM players WHERE espn_id<>''"):
        crosswalk[row[1]].add(row[0])
    history_cache = root / "outputs/reconstructed-replay-20261103-v4/final-boxes"
    for game in load_games(root / "work/espn-cache"):
        if game["season"] not in (2025,2026): continue
        url = f"{BASE}/summary?event={game['id']}"
        file_name = hashlib.sha256(url.encode()).hexdigest()+".json"
        cache = history_cache if (history_cache / file_name).exists() else root / "work/espn-cache"
        path = cache / file_name
        if not path.exists(): continue
        raw = cached_json(url, cache)
        if raw.get("header", {}).get("competitions", [{}])[0].get("status", {}).get("type", {}).get("name") != "STATUS_FINAL": continue
        sid = source({"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                      "reference": url, "kind": "espn_final", "observed_at": observed})
        stats = defaultdict(dict)
        for team in raw.get("boxscore", {}).get("players", []):
            abbrev = team["team"]["abbreviation"]
            for section in team.get("statistics", []):
                kind = section.get("name")
                if kind not in ("passing", "rushing", "receiving"): continue
                for item in section.get("athletes", []):
                    aliases = crosswalk.get(str(item["athlete"].get("id")), set())
                    if len(aliases) != 1: continue
                    pid = next(iter(aliases))
                    values = dict(zip(section["labels"], item.get("stats", [])))
                    row = stats[pid]
                    row.update(team=abbrev, game_id=game["id"], kickoff=game["kickoff"])
                    home = next((x.get("homeAway") for x in raw["header"]["competitions"][0]["competitors"] if x["team"]["abbreviation"]==abbrev),None)
                    row["home_away"] = home
                    mapping = {"passing": {"YDS":"passing_yards","TD":"passing_tds","INT":"passing_interceptions"},
                               "rushing": {"CAR":"carries","YDS":"rushing_yards","TD":"rushing_tds"},
                               "receiving": {"REC":"receptions","TGTS":"targets","YDS":"receiving_yards","TD":"receiving_tds"}}[kind]
                    for label, field in mapping.items():
                        if label in values:
                            try: row[field] = float(values[label].replace(",", ""))
                            except ValueError: pass
                    if kind == "passing" and "C/ATT" in values:
                        try: row["completions"],row["attempts"] = map(float, values["C/ATT"].split("/"))
                        except ValueError: pass
        for pid, row in stats.items():
            db.execute("INSERT INTO observations VALUES(?,?,?,?,?,?,?)", (pid,game["season"],game["week"],"REG","stats",json.dumps(row),sid))
            counts["espn_supplement"] += 1
    players = db.execute("SELECT COUNT(*) FROM players").fetchone()[0]
    current = db.execute("SELECT COUNT(*) FROM players WHERE is_current=1").fetchone()[0]
    metadata = {"created_at": observed, "profile_count": players, "current_roster_profiles": current,
                "metric_definitions": sum(map(len,CATALOG.values())), "position_count": len(CATALOG),
                "requested_history": [2006,2026], "weekly_missing_seasons": manifest["missing_weekly_seasons"],
                "observations": dict(counts), "identity_conflicts": identity_conflicts, "duplicate_observations": duplicates,
                "historically_pregame_certified": False, "status": "PARTIAL_SOURCE_COVERAGE",
                "limits": ["2019 and full 2025–26 public weekly files unavailable in checked release; ESPN supplements are partial",
                           "No proprietary grades or assignments populated", "Current-vintage data cannot certify historical pregame knowledge",
                           "Public tracking has provider qualification thresholds", "No calibrated matchup projection supplied"]}
    db.execute("INSERT INTO metadata VALUES('summary',?)",(json.dumps(metadata),))
    db.commit(); db.close()
    (output / "catalog.json").write_text(json.dumps({"positions": CATALOG, "views": VIEWS},indent=2),encoding="utf-8")
    (output / "coverage.json").write_text(json.dumps(metadata,indent=2),encoding="utf-8")
    (output / "sha256.json").write_text(json.dumps({name:hashlib.sha256((output/name).read_bytes()).hexdigest() for name in ("players.sqlite","catalog.json","coverage.json")},indent=2),encoding="utf-8")
    return metadata


if __name__ == "__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--root",type=Path,required=True)
    parser.add_argument("--archive",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(build(args.root,args.archive,args.output),indent=2))
