"""Refresh reviewed stadium point candidates; run manually, never in the UI request path."""
import csv
import hashlib
import io
import json
from pathlib import Path
import re

import requests

CSV_URL = "https://raw.githubusercontent.com/greerreNFL/stadiums/main/data/stadiums.csv"
SCHEDULE = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
ALIASES = {"Arrowhead Stadium": "GEHA Field at Arrowhead Stadium"}


def normalized(name):
    return re.sub(r"[^a-z0-9]", "", name.lower())


def main():
    response = requests.get(CSV_URL, timeout=20)
    response.raise_for_status()
    rows = list(csv.DictReader(io.StringIO(response.text)))
    lookup = {normalized(row["stadium_name"]): row for row in rows if row["country"] == "United States"}
    venues = {}
    for week in range(1, 19):
        data = requests.get(SCHEDULE, params={"limit": 1000, "dates": 2026, "seasontype": 2, "week": week}, timeout=20).json()
        for event in data.get("events", []):
            competition = event["competitions"][0]
            venue = competition.get("venue", {})
            if not venue.get("id") or not venue.get("fullName"):
                continue
            name = venue["fullName"]
            if venue.get("address", {}).get("country") not in ("USA", "United States", "US"):
                continue
            row = lookup.get(normalized(ALIASES.get(name, name)))
            if not row:
                print("UNMATCHED", name, venue.get("id"))
                continue
            entry = {"name": name, "latitude": float(row["lat"]), "longitude": float(row["lon"]),
                     "coordinate_source_name": row["stadium_name"], "roof_type_reference": row["roof_type"]}
            key = str(venue["id"])
            if key in venues and venues[key] != entry:
                raise ValueError("venue identity changed: " + key)
            venues[key] = entry
    result = {"coordinate_source": CSV_URL, "coordinate_source_sha256": hashlib.sha256(response.content).hexdigest(),
              "note": "Point coordinates are third-party candidates bound to ESPN venue ID and name; roof state must be checked separately.",
              "venues": dict(sorted(venues.items()))}
    path = Path(__file__).with_name("stadium_points.json")
    path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(len(venues), "US venues", path)


if __name__ == "__main__":
    main()
