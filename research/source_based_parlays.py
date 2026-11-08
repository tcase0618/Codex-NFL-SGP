"""Source-informed historical drafts, without a postgame participation filter.

Current source contents and lagged statistics are not version-certified before
kickoff. These drafts must not be labeled clean pregame backtest results.
"""

import argparse
import json
import re
import unicodedata
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from .complete_parlays import candidates, load_games, box_with_td
from .parlay_replay import digest, _save_once

DOMAINS = dict(zip(
    "BUF MIA NE NYJ BAL CIN CLE PIT HOU IND JAX TEN DEN KC LV LAC DAL NYG PHI WSH CHI DET GB MIN ATL CAR NO TB ARI LAR SF SEA".split(),
    "buffalobills miamidolphins patriots newyorkjets baltimoreravens bengals clevelandbrowns steelers houstontexans colts jaguars tennesseetitans denverbroncos chiefs raiders chargers dallascowboys giants philadelphiaeagles commanders chicagobears detroitlions packers vikings atlantafalcons panthers neworleanssaints buccaneers azcardinals therams 49ers seahawks".split()))
LABELS = dict(zip("BUF MIA NE NYJ BAL CIN CLE PIT HOU IND JAX TEN DEN KC LV LAC DAL NYG PHI WSH CHI DET GB MIN ATL CAR NO TB ARI LAR SF SEA".split(),
                 "BILLS DOLPHINS PATRIOTS JETS RAVENS BENGALS BROWNS STEELERS TEXANS COLTS JAGUARS TITANS BRONCOS CHIEFS RAIDERS CHARGERS COWBOYS GIANTS EAGLES COMMANDERS BEARS LIONS PACKERS VIKINGS FALCONS PANTHERS SAINTS BUCCANEERS CARDINALS RAMS NINERS SEAHAWKS".split()))
POSITION = re.compile(r"^(?:\d+\s+)?(?:QB|RB|FB|WR|TE|OL|OT|OG|C|G|T|DL|DT|DE|LB|OLB|ILB|CB|DB|S|FS|SS|K|P|LS)\s+", re.I)


def norm(value):
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower()
    value = re.sub(r"\b(jr|sr|iii|ii|iv)\b", "", value)
    return re.sub(r"[^a-z0-9]", "", value)


def instant(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def explicit_roster_names(record):
    # Only list items under positional headings, or caption/alt strings that
    # explicitly begin with a player position. Narrative mentions of cuts do
    # not establish roster membership.
    soup = BeautifulSoup(Path(record["html_file"]).read_bytes(), "html.parser")
    names = set()
    position_words = r"quarterbacks?|running backs?|fullbacks?|wide receivers?|receivers?|tight ends?|offensive lin(?:e|emen)|defensive lin(?:e|emen)|defensive tackles?|linebackers?|outside linebackers?|inside linebackers?|cornerbacks?|defensive backs?|safet(?:y|ies)|specialists?|QB|RB|FB|WR|TE|OL|DL|DT|LB|CB|DB|Spec\."
    heading_pattern = re.compile(r"^(?:" + position_words + r")(?:\s*/\s*(?:" + position_words + r"))?\s*(?:\(\d+\))?\s*$", re.I)

    def add_name(value):
        value = re.sub(r"^\d+\s+", "", POSITION.sub("", value.strip())).strip()
        if 2 <= len(value.split()) <= 5 and not any(mark in value for mark in (":", "!", "?", ";", "[", "]")):
            names.add(norm(value))

    for heading in soup.find_all(["h2", "h3", "h4"]):
        label = heading.get_text(" ", strip=True)
        if not heading_pattern.fullmatch(label):
            continue
        for sibling in heading.next_elements:
            if sibling is heading:
                continue
            if getattr(sibling, "name", None) in ("h2", "h3", "h4"):
                break
            if getattr(sibling, "name", None) == "ul":
                for item in sibling.find_all("li"):
                    add_name(item.get_text(" ", strip=True))
    # Some clubs use bold paragraphs rather than HTML headings.
    in_position = False
    for line in Path(record["text_file"]).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        if heading_pattern.fullmatch(line):
            in_position = True
            continue
        if ":" in line and heading_pattern.fullmatch(line.split(":", 1)[0].strip()):
            for part in line.split(":", 1)[1].split(","):
                add_name(part)
            in_position = False
            continue
        if in_position:
            if len(line.split()) > 6 or re.match(r"reserves|injured|practice squad|waived|released", line, re.I):
                in_position = False
            else:
                add_name(line)
    return names


def transaction_state(description):
    value = description.lower()
    if re.search(r"\b(released|waived|terminated|traded)\b", value):
        return "excluded"
    if "designated" in value and ("return" in value or "practice" in value):
        return "reserve"
    if any(term in value for term in ("injured reserve", "reserve/", "reserve list", "physically unable", "non-football", "suspended")):
        return "reserve"
    if "elevated" in value:
        return "temporary_elevation"
    if "practice squad" in value and "from" not in value:
        return "practice_squad"
    if re.search(r"\b(signed|activated|acquired|claimed|reinstated|promoted)\b", value):
        return "active_roster"
    return None


def dated_transactions(records):
    result = []
    for record in records:
        for row in record.get("transaction_rows", []):
            if len(row) < 2:
                continue
            label = row[0].strip()
            try:
                if re.fullmatch(r"\d{1,2}/\d{1,2}", label):
                    date = datetime.strptime("2026/" + label, "%Y/%m/%d").date()
                else:
                    date = datetime.strptime(re.sub(r"\.", "", label) + " 2026", "%b %d %Y").date()
            except ValueError:
                continue
            text = " ".join(row[1:])
            for sentence in re.split(r"(?<=[.;])\s+(?=(?:Signed|Released|Waived|Placed|Activated|Elevated|Acquired|Designated|Traded|Promoted|Claimed|Reinstated)\b)", text):
                result.append((date, sentence, record["reference"]))
    return sorted(result, key=lambda item: item[0])


def eligible(leg, game, initial_names, initial_sources, transactions, sections):
    team = leg["team_id"]
    cutoff = instant(game["kickoff"]) - timedelta(minutes=30)
    target_date = cutoff.astimezone(ZoneInfo("America/New_York")).date()
    name = norm(leg["player"])
    state = "active_roster" if name in initial_names.get(team, set()) else "unknown_membership"
    proof = list(initial_sources.get((team, name), []))
    for date, text, url in transactions.get(team, []):
        if date > target_date or name not in norm(text):
            continue
        action = transaction_state(text)
        if action is None:
            continue
        if date == target_date:
            # Date-only changes cannot be placed before the cutoff safely.
            return False, "same_day_transaction_time_unknown", proof + [url]
        state = action
        proof.append(url)
        if action == "temporary_elevation" and (target_date - date).days > 1:
            state = "practice_squad"
    if state not in ("active_roster", "temporary_elevation"):
        return False, state, proof
    if not sections:
        return False, "missing_team_inactive_report", proof
    for section in sections:
        if not section.get("published_at") or instant(section["published_at"]) > cutoff:
            return False, "inactive_report_published_after_cutoff", proof
        proof.append(section["source"])
        for listed in section["names_as_published"]:
            if name == norm(re.sub(r"\([^)]*\)", "", POSITION.sub("", listed))):
                return False, "listed_inactive", proof
    return True, "source_inferred_active_CONTENT_VERSION_UNVERIFIED", sorted(set(proof))


def choose(pool, length):
    ordered = sorted(pool, key=lambda leg: (-leg["ranking_score"], -leg["prior_appearances"], -leg["prior_market_mean"], leg["player_id"], leg["market"]))
    teams = sorted({leg["team_id"] for leg in ordered})
    if len(teams) != 2:
        return []
    chosen = [next(leg for leg in ordered if leg["team_id"] == team) for team in teams]
    used = {leg["player_id"] for leg in chosen}
    for leg in ordered:
        if len(chosen) == length:
            break
        if leg["player_id"] not in used:
            used.add(leg["player_id"])
            chosen.append(leg)
    return chosen if len(chosen) == length else []


def run(output: Path, cache: Path):
    inventory = json.loads((output / "pregame-source-inventory.json").read_text(encoding="utf-8"))
    sources = [record for record in inventory["sources"] if "error" not in record]
    games = load_games(cache)
    first_kickoff = min(instant(game["kickoff"]) for game in games if game["season"] == 2026)
    initial_names, initial_sources, transactions = defaultdict(set), defaultdict(list), {}
    for team, domain in DOMAINS.items():
        records = [record for record in sources if record["reference"].startswith(f"https://www.{domain}.com/")]
        transactions[team] = dated_transactions(records)
        for record in records:
            title = record.get("title", "").lower()
            if not ("roster" in title and ("initial" in title or "53" in title)):
                continue
            if not record.get("published_at") or instant(record["published_at"]) >= first_kickoff:
                continue
            for name in explicit_roster_names(record):
                initial_names[team].add(name)
                initial_sources[(team, name)].append(record["reference"])
    history = defaultdict(list)
    drafts, excluded = [], []
    coverage = {row["game_id"]: row["sources"] for row in inventory["game_coverage"]}
    for game in games:
        if game["season"] == 2026:
            pool = []
            relevant = [section for section in inventory["inactive_sections"] if section["source"] in coverage[game["id"]]]
            for leg in candidates(game, history):
                sections = [section for section in relevant if section["team_label"] in (LABELS[leg["team_id"]], "49ERS" if leg["team_id"] == "SF" else "EAGLS" if leg["team_id"] == "PHI" else LABELS[leg["team_id"]])]
                ok, status, proof = eligible(leg, game, initial_names, initial_sources, transactions, sections)
                if ok:
                    pool.append({**leg, "eligibility": status, "eligibility_sources": proof})
                else:
                    excluded.append({"game_id": game["id"], "player": leg["player"], "market": leg["market"], "team_id": leg["team_id"], "reason": status, "sources": proof})
            for kind, length in (("3", 3), ("5", 5), ("8", 8), ("TD", 3)):
                legs = choose([leg for leg in pool if (leg["market"] == "anytime_td") == (kind == "TD")], length)
                drafts.append({"ticket_id": f"source-draft-{game['id']}-{kind}", "game_id": game["id"], "game": game["name"],
                               "week": game["week"], "type": kind, "kickoff": game["kickoff"],
                               "status": "DRAFT_SOURCE_VERSION_UNVERIFIED" if legs else "BLOCKED_INSUFFICIENT_ELIGIBILITY_EVIDENCE",
                               "legs": legs})
        # A final box may enter history only after its own prediction step.
        box = box_with_td(game, cache)
        for team in box["teams"]:
            history[team].append(box)
    artifact = {"kind": "SOURCE_INFORMED_HISTORICAL_DRAFTS", "valid_pregame_backtest": False,
                "uses_postgame_participation_for_selection": False, "settled": False,
                "source_version_warning": "Current source content and prior-stat versions are not certified before kickoff; all selections require verification.",
                "tickets_sha256": digest(drafts), "tickets": drafts, "excluded": excluded,
                "initial_roster_name_counts": {team: len(names) for team, names in initial_names.items()}}
    assert len(drafts) == 484
    _save_once(output / "source-informed-parlays-v4.json", artifact)
    lines = ["# Source-informed historical parlay drafts", "",
             "Four slots are retained for every Week 1–8 game. Completed draft selections use prior statistics, initial-roster position lists, dated transaction records, and the published inactive lists. There is no didNotPlay or final-game participation filter. Missing/ambiguous team membership and date-only same-day changes are excluded. Initial-roster parsing is conservative and may miss names or teams, which blocks some tickets.", "",
             "These are provisional reconstructions, not certified pregame tickets. Current article versions, transaction timestamps, prior-stat publication availability, and exact sportsbook lines/prices remain unverified. All selected legs are explicitly marked source-version-unverified. No outcomes are graded in this selection artifact. Yardage thresholds and TD construction match the preceding all-game research rules.", "",
             "| Week | Game | Type | Status | Legs |", "|---:|---|---|---|---|"]
    for ticket in drafts:
        detail = "; ".join(f"{leg['player']} ({leg['team_id']}) {leg['market']} {leg['line']}+" for leg in ticket["legs"]) or "Insufficient eligible prior-history candidates"
        lines.append(f"| {ticket['week']} | {ticket['game']} | {ticket['type']} | {ticket['status']} | {detail} |")
    lines.extend(["", "Exact player IDs, candidate exclusions, prior game IDs, eligibility source URLs and the selection hash are in source-informed-parlays-v4.json. All earlier manifests remain unchanged.", ""])
    (output / "source-informed-parlays-v4.md").write_text("\n".join(lines), encoding="utf-8")
    result = {"slots": len(drafts), "draft_tickets": sum(bool(ticket["legs"]) for ticket in drafts),
              "blocked_slots": sum(not ticket["legs"] for ticket in drafts),
              "by_type": {kind: sum(bool(ticket["legs"]) for ticket in drafts if ticket["type"] == kind) for kind in ("3", "5", "8", "TD")},
              "roster_name_counts": artifact["initial_roster_name_counts"]}
    print(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    args = parser.parse_args()
    run(args.output, args.cache)
