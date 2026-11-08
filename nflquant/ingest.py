"""Idempotent, incremental ingestion of public nflverse release assets.

Every download is recorded in ``data/raw/manifest.json`` with URL, HTTP validators
(ETag / Last-Modified), byte size, SHA-256 and retrieval time. Re-running the
ingester issues conditional requests; unchanged assets are not rewritten and a
changed asset is stored as a new revision (the prior hash is kept in history).

Licensing: nflverse data is distributed under CC-BY 4.0 (FTN charting CC-BY-SA 4.0).
Participation/coverage fields originate from NFL Next Gen Stats via nflverse.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import requests

from .config import settings

BASE = "https://github.com/nflverse/nflverse-data/releases/download"


@dataclass(frozen=True)
class Asset:
    release: str
    name: str
    season: int | None
    description: str
    source_url: str | None = None

    @property
    def url(self) -> str:
        return self.source_url or f"{BASE}/{self.release}/{self.name}"

    @property
    def key(self) -> str:
        return f"{self.release}/{self.name}"


# (release, filename template, first season available, description)
SEASONAL = [
    ("pbp", "play_by_play_{s}.parquet", 2006, "nflfastR play-by-play with EPA/WP/CP models"),
    ("pbp_participation", "pbp_participation_{s}.parquet", 2016, "NGS participation: on-field IDs, personnel, coverage, pressure, routes"),
    ("ftn_charting", "ftn_charting_{s}.parquet", 2022, "FTN manual charting: play action, RPO, blitz, drops, read thrown"),
    ("stats_player", "stats_player_week_{s}.parquet", 2006, "Player weekly box statistics"),
    ("weekly_rosters", "roster_weekly_{s}.parquet", 2006, "Weekly roster status"),
    ("injuries", "injuries_{s}.parquet", 2009, "Official injury reports and practice participation"),
    ("snap_counts", "snap_counts_{s}.parquet", 2012, "PFR snap counts"),
    ("depth_charts", "depth_charts_{s}.parquet", 2006, "Weekly depth charts"),
]
SINGLE = [
    ("schedules", "games.csv", "Schedules, results, rest, roof, weather, market benchmark lines"),
    ("players", "players.parquet", "Player identity, measurements, draft"),
    ("nextgen_stats", "ngs_passing.parquet", "NGS passing aggregates"),
    ("nextgen_stats", "ngs_receiving.parquet", "NGS receiving aggregates"),
    ("nextgen_stats", "ngs_rushing.parquet", "NGS rushing aggregates"),
    ("combine", "combine.parquet", "Combine measurements"),
    ("draft_picks", "draft_picks.parquet", "Draft history"),
]


def assets(first: int, last: int, releases: set[str] | None = None) -> list[Asset]:
    out = [Asset(r, n, None, d) for r, n, d in SINGLE]
    out.append(Asset("stadiums", "stadiums.csv", None, "Stadium coordinates, altitude, roof, timezone",
                     "https://raw.githubusercontent.com/greerreNFL/stadiums/main/data/stadiums.csv"))
    for release, tmpl, start, desc in SEASONAL:
        for s in range(max(first, start), last + 1):
            out.append(Asset(release, tmpl.format(s=s), s, desc))
    return [a for a in out if releases is None or a.release in releases]


class Manifest:
    def __init__(self, path: Path):
        self.path = path
        self.data = json.loads(path.read_text()) if path.exists() else {"assets": {}}

    def get(self, key: str) -> dict | None:
        return self.data["assets"].get(key)

    def put(self, key: str, record: dict) -> None:
        prior = self.data["assets"].get(key)
        if prior and prior.get("sha256") != record.get("sha256"):
            record["history"] = prior.get("history", []) + [
                {k: prior.get(k) for k in ("sha256", "retrieved_at", "bytes", "etag")}
            ]
        elif prior:
            record["history"] = prior.get("history", [])
        self.data["assets"][key] = record

    def save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=1, sort_keys=True))
        tmp.replace(self.path)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch(asset: Asset, raw_dir: Path, manifest: Manifest, session: requests.Session,
          timeout: float, retries: int, force: bool = False) -> str:
    """Download one asset. Returns status: 'unchanged' | 'new' | 'updated' | 'missing' | 'error'."""
    dest = raw_dir / asset.release / asset.name
    dest.parent.mkdir(parents=True, exist_ok=True)
    prior = manifest.get(asset.key)
    headers = {"User-Agent": "nflquant-ingest/1.0"}
    if prior and dest.exists() and not force:
        if prior.get("etag"):
            headers["If-None-Match"] = prior["etag"]
        if prior.get("last_modified"):
            headers["If-Modified-Since"] = prior["last_modified"]
    delay = 2.0
    for attempt in range(retries):
        try:
            resp = session.get(asset.url, headers=headers, timeout=timeout, stream=True)
            if resp.status_code == 304:
                prior["checked_at"] = _now()
                return "unchanged"
            if resp.status_code == 404:
                manifest.put(asset.key, {"url": asset.url, "status": "missing", "checked_at": _now(),
                                         "release": asset.release, "season": asset.season})
                return "missing"
            if resp.status_code in (403, 429) or resp.status_code >= 500:
                wait = float(resp.headers.get("Retry-After", delay))
                time.sleep(min(wait, 120))
                delay *= 2
                continue
            resp.raise_for_status()
            tmp = dest.with_suffix(dest.suffix + ".part")
            with tmp.open("wb") as f:
                for chunk in resp.iter_content(1 << 20):
                    f.write(chunk)
            digest = sha256(tmp)
            if prior and prior.get("sha256") == digest and dest.exists():
                tmp.unlink()
                prior["checked_at"] = _now()
                return "unchanged"
            tmp.replace(dest)
            manifest.put(asset.key, {
                "url": asset.url, "release": asset.release, "season": asset.season,
                "description": asset.description, "status": "ok", "path": str(dest.relative_to(raw_dir)),
                "sha256": digest, "bytes": dest.stat().st_size, "retrieved_at": _now(), "checked_at": _now(),
                "etag": resp.headers.get("ETag"), "last_modified": resp.headers.get("Last-Modified"),
                "license": {"ftn_charting": "CC-BY-SA 4.0", "stadiums": "see greerreNFL/stadiums repository"}.get(asset.release, "CC-BY 4.0"),
            })
            return "updated" if prior and prior.get("sha256") else "new"
        except requests.RequestException:
            time.sleep(delay)
            delay *= 2
    return "error"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def run(first: int | None = None, last: int | None = None, releases: set[str] | None = None,
        force: bool = False, verbose: bool = True) -> dict:
    cfg = settings()
    first = first or cfg.first_season
    last = last or cfg.last_season
    cfg.raw_dir.mkdir(parents=True, exist_ok=True)
    manifest = Manifest(cfg.raw_dir / "manifest.json")
    counts: dict[str, int] = {}
    with requests.Session() as session:
        for asset in assets(first, last, releases):
            status = fetch(asset, cfg.raw_dir, manifest, session, cfg.http_timeout, cfg.max_retries, force)
            counts[status] = counts.get(status, 0) + 1
            if verbose:
                print(f"{status:9s} {asset.key}", flush=True)
            manifest.save()
    return counts


def main() -> None:
    p = argparse.ArgumentParser(description="Download nflverse assets incrementally")
    p.add_argument("--first", type=int)
    p.add_argument("--last", type=int)
    p.add_argument("--release", action="append")
    p.add_argument("--force", action="store_true")
    a = p.parse_args()
    print(run(a.first, a.last, set(a.release) if a.release else None, a.force))


if __name__ == "__main__":
    main()
