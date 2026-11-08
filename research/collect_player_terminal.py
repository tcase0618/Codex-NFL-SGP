"""Archive public multi-season player statistics; never invent missing seasons."""
import argparse
import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import requests


def collect(output):
    output.mkdir(parents=True, exist_ok=False)
    assets, discovery = [], []
    for tag in ("player_stats", "nextgen_stats", "players"):
        url = f"https://api.github.com/repos/nflverse/nflverse-data/releases/tags/{tag}"
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        (output / f"release-{tag}.json").write_bytes(response.content)
        data = response.json()
        discovery.append({"reference": url, "sha256": hashlib.sha256(response.content).hexdigest()})
        for asset in data.get("assets", []):
            name = asset["name"]
            stats = re.fullmatch(r"stats_player_week_(\d{4})\.csv", name)
            ngs = re.fullmatch(r"ngs_(\d{4})_(passing|receiving|rushing)\.csv\.gz", name)
            if (stats and 2006 <= int(stats[1]) <= 2026) or (ngs and 2006 <= int(ngs[1]) <= 2026) or name == "players.csv":
                assets.append({"name": name, "reference": asset["browser_download_url"],
                               "provider_updated_at": asset["updated_at"], "kind": tag})
    def fetch(asset):
        try:
            response = requests.get(asset["reference"], timeout=45)
            response.raise_for_status()
            path = output / asset["name"]
            with path.open("xb") as handle: handle.write(response.content)
            return {**asset, "path": str(path), "sha256": hashlib.sha256(response.content).hexdigest(),
                    "observed_at": datetime.now(timezone.utc).isoformat(), "status": "ARCHIVED",
                    "historical_available_at_verified": False}
        except Exception as exc:
            return {**asset, "status": "UNAVAILABLE", "error": str(exc)}
    with ThreadPoolExecutor(max_workers=4) as executor:
        sources = list(executor.map(fetch, assets))
    result = {"kind": "CURRENT_VINTAGE_HISTORICAL_PLAYER_DATA", "sources": sources, "discovery": discovery,
              "requested_range": [2006, 2026], "historically_pregame_certified": False,
              "missing_weekly_seasons": [year for year in range(2006, 2027) if not any(
                  source["name"] == f"stats_player_week_{year}.csv" and source["status"] == "ARCHIVED" for source in sources)]}
    (output / "manifest.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return {"archived": sum(source["status"] == "ARCHIVED" for source in sources),
            "failed": [source["name"] for source in sources if source["status"] != "ARCHIVED"],
            "missing_weekly_seasons": result["missing_weekly_seasons"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    print(json.dumps(collect(parser.parse_args().output), indent=2))
