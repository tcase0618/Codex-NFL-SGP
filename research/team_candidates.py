"""Full roster candidate coverage, independent of recent box appearances."""
from .source_based_parlays import norm
from football_engine.player_metrics import CATALOG, libraries_for_position


def team_candidates(sources: list[dict], team: str) -> list[dict]:
    records = [s for s in sources if s["team"] == team and s["kind"] in ("roster", "depth")]
    profiles = {}
    for source in records:
        for row in source["rows"]:
            key = norm(row["player"])
            profile = profiles.setdefault(key, {"player": row["player"], "team_id": team,
                                                "roster_sections": [], "depth_roles": [], "sources": [],
                                                "eligibility": "REQUIRES_GAME_DAY_CONFIRMATION",
                                                "forecast": None, "sportsbook_player_id": None})
            profile["sources"].append({"reference": source["reference"],
                                        "retrieved_at": source["retrieved_at"],
                                        "content_sha256": source["content_sha256"]})
            if source["kind"] == "roster":
                profile["roster_sections"].append(row["section"])
                profile["position"] = row["position"]
                profile["player_reference"] = row["player_reference"]
            else:
                profile["depth_roles"].append({k: row[k] for k in ("position", "depth", "alignment_row")})
    # Preserve reserves and practice squad visibly; never silently enable them.
    for profile in profiles.values():
        profile["review_flags"] = []
        roles=libraries_for_position(profile.get("position", ""))
        profile["quant_metric_library"]={"schema_version":"player-metrics-v1","roles":roles,
            "metric_ids":{role:[metric["id"] for metric in CATALOG[role]] for role in roles},
            "identity_join":"REQUIRES_STABLE_PROVIDER_ID", "numerical_values_attached":False,
            "role_review_required":len(roles)!=1}
        if not profile["roster_sections"]:
            profile["review_flags"].append("DEPTH_PLAYER_MISSING_FROM_ROSTER")
        elif profile["roster_sections"] != ["Active"]:
            profile["review_flags"].append("ROSTER_STATUS_REQUIRES_RECONCILIATION")
    return sorted(profiles.values(), key=lambda p: norm(p["player"]))
