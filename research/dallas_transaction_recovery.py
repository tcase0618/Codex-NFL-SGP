"""Bounded, research-only Dallas transaction evidence; never certifies season history.

Only publisher-provided next links are followed. Empty windows remain unverified.
The club news tracker supplies dated statements, not normalized player identities.
"""
import argparse
import calendar
import hashlib
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

CATEGORIES = ("trades", "signings", "reserve-list", "waivers", "terminations", "other")
NFL = "https://www.nfl.com"
CLUB = "https://www.dallascowboys.com"
TEAM = "/teams/dallas-cowboys"
TRACKER = CLUB + "/news/transactions-guard-signed-snowden-waived-injured"
HEADERS = ["From", "To", "Date", "Name", "Position", "Transaction"]


def _key(url):
    p = urlparse(url)
    return p.scheme, p.netloc, p.path, tuple(sorted(parse_qsl(p.query, keep_blank_values=True)))


def _official(url):
    p = urlparse(url)
    return (p.scheme == "https" and p.netloc in ("www.nfl.com", "www.dallascowboys.com")
            and not p.fragment)


def archive_fetch(url, archive):
    """Archive successful and failed HTTP bodies; never follow redirects or retry."""
    if not _official(url):
        raise ValueError("Non-official destination rejected")
    archive = Path(archive)
    archive.mkdir(parents=True, exist_ok=True)
    source = {"reference": url, "retrieved_at": datetime.now(timezone.utc).isoformat()}
    try:
        with requests.get(url, timeout=(5, 20), allow_redirects=False, stream=True) as response:
            source.update(resolved_reference=response.url, status_code=response.status_code)
            chunks, size = [], 0
            for chunk in response.iter_content(65536):
                size += len(chunk)
                if size > 4_000_000:
                    source["error"] = "response_size_limit"
                    break
                chunks.append(chunk)
            raw = b"".join(chunks)
            digest = hashlib.sha256(raw).hexdigest()
            path = archive / (digest + ".html")
            if not path.exists():
                path.write_bytes(raw)
            source.update(archive_file=str(path.resolve()), sha256=digest,
                          archived_bytes=len(raw), body_truncated=size > 4_000_000)
            if response.status_code != 200:
                source["error"] = "http_" + str(response.status_code)
    except requests.RequestException as exc:
        source["error"] = type(exc).__name__
    identifier = hashlib.sha256((url + source["retrieved_at"]).encode()).hexdigest()
    (archive / (identifier + ".json")).write_text(json.dumps(source, indent=2), encoding="utf-8")
    return source


def _read(source, requested):
    if not isinstance(source, dict) or source.get("error"):
        raise ValueError("fetch_failed")
    if (source.get("status_code") != 200 or source.get("body_truncated") or
            not _official(source.get("resolved_reference", "")) or
            _key(source.get("reference", "")) != _key(requested) or
            _key(source.get("resolved_reference", "")) != _key(requested)):
        raise ValueError("response_destination_or_status_unverified")
    stamp = datetime.fromisoformat(source["retrieved_at"].replace("Z", "+00:00"))
    if stamp.utcoffset() is None:
        raise ValueError("timestamp_unverified")
    raw = Path(source["archive_file"]).read_bytes()
    if hashlib.sha256(raw).hexdigest() != source["sha256"]:
        raise ValueError("archive_hash_mismatch")
    return raw


def parse_page(raw, url, year, month, category):
    """Validate selected scope and every row before extracting exact Dallas links."""
    soup = BeautifulSoup(raw, "html.parser")
    path = f"/transactions/league/{category}/{year}/{month}"
    selected = soup.select("select option[selected]")
    labels = {str(year), calendar.month_name[month]}
    if (len(selected) != 2 or {o.get_text(strip=True) for o in selected} != labels or
            any(urljoin(url, o.get("value", "")) != NFL + path for o in selected)):
        raise ValueError("selected_month_year_unverified")
    active = soup.select('a[aria-label$=" selected"]')
    if len(active) != 1 or urljoin(url, active[0].get("href", "")) != NFL + path:
        raise ValueError("selected_category_unverified")
    reports = soup.select(".nfl-c-transactions-report")
    if len(reports) != 1:
        raise ValueError("transaction_report_unverified")
    report = reports[0]
    tables = report.select("table")
    empty = "No Transactions Available" in list(report.stripped_strings)
    if not tables and empty:
        return {"rows": [], "signatures": [], "next_url": None,
                "empty_marker_observed": True, "scope": {"year": year, "month": month,
                "category": category, "team_filter": TEAM, "publisher_team_scope": "league"}}
    if len(tables) != 1 or empty:
        raise ValueError("table_or_empty_marker_conflict")
    table = tables[0]
    if [h.get_text(" ", strip=True) for h in table.select("thead th")] != HEADERS:
        raise ValueError("transaction_schema_changed")
    rows, signatures = [], []
    for tr in table.select("tbody tr"):
        cells = tr.find_all("td", recursive=False)
        if len(cells) != 6:
            raise ValueError("transaction_row_schema_changed")
        columns = [c.get_text(" ", strip=True) for c in cells]
        if not all(columns[i] for i in (0, 1, 2, 3, 5)):
            raise ValueError("transaction_row_missing_fields")
        parsed_date = datetime.strptime(f"{year}/{columns[2]}", "%Y/%m/%d").date()
        if parsed_date.month != month:
            raise ValueError("transaction_date_outside_selected_month")
        teams = []
        for cell in cells[:2]:
            links = cell.select("a[href]")
            if not links:
                if cell.get_text(strip=True) != "--":
                    raise ValueError("team_identity_missing")
                continue
            if len(links) != 1:
                raise ValueError("team_identity_ambiguous")
            link = urlparse(urljoin(url, links[0]["href"]))
            if (link.scheme != "https" or link.netloc != "www.nfl.com" or link.query or
                    link.fragment or not re.fullmatch(r"/teams/[^/]+/?", link.path)):
                raise ValueError("team_identity_unverified")
            teams.append(link.path.rstrip("/"))
        players = cells[3].select("a[href]")
        player_url = urljoin(url, players[0]["href"]) if len(players) == 1 else ""
        player = urlparse(player_url)
        if (not teams or player.scheme != "https" or player.netloc != "www.nfl.com" or
                player.query or player.fragment or not re.fullmatch(r"/players/[^/]+/?", player.path)):
            raise ValueError("player_or_team_identity_unverified")
        signature = tuple(columns) + tuple(teams) + (player_url,)
        signatures.append(signature)
        if TEAM in teams:
            rows.append({"team_id": "DAL", "team_references": [NFL + t for t in teams],
                         "date": parsed_date.isoformat(), "category": category, "columns": columns,
                         "player_reference": player_url})
    if not signatures:
        raise ValueError("empty_table_unverified")
    next_links = [a for a in report.select("a[href]")
                  if (a.get_text(" ", strip=True) == "Next Page" or "next" in a.get("rel", []))
                  and a.get("aria-disabled") != "true" and "disabled" not in a.get("class", [])]
    next_urls = {urljoin(url, a["href"]) for a in next_links}
    if len(next_urls) > 1:
        raise ValueError("ambiguous_next_page")
    next_url = next(iter(next_urls), None)
    if next_url:
        p = urlparse(next_url)
        query = parse_qsl(p.query, keep_blank_values=True)
        if (not _official(next_url) or p.netloc != "www.nfl.com" or p.path != path or
                len(query) != 1 or query[0][0] != "after" or not query[0][1]):
            raise ValueError("unsafe_or_undisclosed_pagination")
    return {"rows": rows, "signatures": signatures, "next_url": next_url,
            "empty_marker_observed": False, "scope": {"year": year, "month": month,
            "category": category, "team_filter": TEAM, "publisher_team_scope": "league"}}


def collect_window(year, month, archive, fetch=archive_fetch, max_pages=3):
    date(year, month, 1)
    if not 1 <= max_pages <= 10:
        raise ValueError("max_pages must be between 1 and 10")
    categories, rows = [], []
    for category in CATEGORIES:
        url = f"{NFL}/transactions/league/{category}/{year}/{month}"
        result = {"category": category, "status": "PARTIAL", "sources": [], "errors": [],
                  "pages": [], "pagination_exhausted": False}
        seen_urls, digests, signatures = set(), set(), set()
        for _ in range(max_pages):
            if _key(url) in seen_urls:
                result["errors"].append("pagination_cycle")
                break
            seen_urls.add(_key(url))
            try:
                source = fetch(url, archive)
                result["sources"].append(source)
                raw = _read(source, url)
                digest = hashlib.sha256(raw).hexdigest()
                if digest in digests:
                    raise ValueError("pagination_repeated_content")
                digests.add(digest)
                page = parse_page(raw, url, year, month, category)
                new = page.pop("signatures")
                if len(set(new)) != len(new) or signatures.intersection(new):
                    raise ValueError("pagination_repeated_rows")
                signatures.update(new)
                result["pages"].append({k: v for k, v in page.items() if k != "rows"})
                rows.extend({**r, "source": source} for r in page["rows"])
                if page["empty_marker_observed"]:
                    result["errors"].append("empty_window_not_independently_verified")
                    break
                if not page["next_url"]:
                    result.update(status="OBSERVED_NONEMPTY_WINDOW_EXHAUSTED", pagination_exhausted=True)
                    break
                url = page["next_url"]
            except (ValueError, KeyError, TypeError, OSError, AttributeError) as exc:
                result["errors"].append(str(exc))
                break
        else:
            result["errors"].append("page_limit_reached")
        result["coverage_status"] = "PARTIAL" if result["errors"] else "OBSERVED_NONEMPTY_PAGES_ONLY"
        result["missing_external_facts"] = (["Verified continuation or authoritative category export"]
                                            if result["errors"] else ["Independent completeness confirmation"])
        result["recovery_action"] = ("Obtain an official Dallas transaction export with an explicit date range, "
                                     "or publisher-confirmed working continuation; preserve this evidence until then.")
        categories.append(result)
    exhausted = all(c["pagination_exhausted"] for c in categories)
    return {"kind": "DALLAS_TRANSACTION_RECOVERY", "status": "OBSERVED_WINDOW_EXHAUSTED" if exhausted else "PARTIAL_OFFICIAL_WINDOW",
            "year": year, "month": month, "team_id": "DAL", "team_reference": NFL + TEAM,
            "coverage_status": "PARTIAL", "coverage_type": "SELECTED_CALENDAR_MONTH_SOURCE_ROWS",
            "missing_external_facts": ["Verified complete Dallas history for the requested month",
                                       "Verified complete season history"],
            "all_six_categories_complete": False, "all_six_nonempty_windows_exhausted": exhausted,
            "season_history_complete": False, "runtime_eligible": False,
            "limits": {"max_pages_per_category": max_pages, "max_requests": 6 * max_pages,
                       "calendar_month_only": True, "empty_windows_certify_nothing": True},
            "categories": categories, "rows": rows,
            "remaining_gap": "Publisher pagination exhaustion is not independent proof of complete history; empty or repeated windows remain unverified."}


def collect_tracker(year, month, archive, fetch=archive_fetch):
    """Preserve literal dated club statements, including multi-player statements."""
    date(year, month, 1)
    result = {"reference": TRACKER, "status": "PARTIAL_CLUB_NEWS", "rows": [],
              "requested_year": year, "requested_month": month, "team_id": "DAL",
              "team_identity_basis": "Official Dallas Cowboys club article",
              "coverage_status": "PARTIAL", "coverage_type": "LATEST_CLUB_NEWS_STATEMENTS",
              "missing_external_facts": ["Exhaustive transaction list", "Resolved player identities",
                                         "Reconciled league and club effective dates"],
              "recovery_action": "Obtain dated official transaction ledger; reconcile literal statements before normalization",
              "season_history_complete": False, "runtime_eligible": False, "errors": []}
    try:
        source = fetch(TRACKER, archive)
        result["source"] = source
        soup = BeautifulSoup(_read(source, TRACKER), "html.parser")
        headings = [h.get_text(" ", strip=True) for h in soup.select("h2")]
        if f"News - Regular Season | {year}" not in headings:
            raise ValueError("club_article_year_unverified")
        dates = soup.select(".nfl-c-article__dates")
        if len(dates) != 1 or not re.search(rf"\b{year}\b", dates[0].get_text()):
            raise ValueError("club_publication_year_unverified")
        result["publication_label"] = dates[0].get_text(" ", strip=True)
        publication = datetime.strptime(result["publication_label"].split(" at ")[0], "%b %d, %Y").date()
        result["publication_date"] = publication.isoformat()
        tables = [t for t in soup.select(".nfl-c-body-part table")
                  if t.select_one("tr") and t.select_one("tr").get_text(strip=True) == "Transactions"]
        if len(tables) != 1:
            raise ValueError("club_tracker_table_unverified")
        for index, tr in enumerate(tables[0].select("tr")[1:], 1):
            text = tr.get_text(" ", strip=True)
            match = re.fullmatch(r"([A-Za-z]+)\.? (\d{1,2}) \| (.+)", text)
            if not match:
                raise ValueError("club_tracker_row_unverified")
            months = {calendar.month_abbr[m]: m for m in range(1, 13)}
            months["Sept"] = 9
            day = date(year, months[match[1]], int(match[2]))
            if day > publication:
                raise ValueError("club_statement_after_publication_date")
            if day.month == month:
                result["rows"].append({"date": day.isoformat(), "team_id": "DAL", "team_reference": CLUB,
                    "statement": match[3], "player_identity": "UNRESOLVED",
                    "date_year_basis": "Matching article year; row specifies month/day only",
                    "table_row": index, "source": source})
        result["reason"] = "Club labels these latest transactions; omissions and season completeness are unverified. Year inferred only within matching article year; no cross-year inference."
    except (ValueError, KeyError, TypeError, OSError, AttributeError) as exc:
        result["errors"].append(str(exc))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--months", type=int, nargs="+", required=True)
    parser.add_argument("--max-pages", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True,
                        help="New research evidence directory; existing directories are never overwritten")
    args = parser.parse_args()
    if len(args.months) > 2 or len(set(args.months)) != len(args.months):
        parser.error("Select one or two distinct calendar months per bounded run")
    for month in args.months:
        date(args.year, month, 1)
    if not 1 <= args.max_pages <= 10:
        parser.error("max-pages must be between 1 and 10")
    args.output.mkdir(parents=True, exist_ok=False)
    archive = args.output / "archive"
    club_route = archive_fetch(f"{CLUB}/team/transactions/{args.year}", archive)
    windows = [collect_window(args.year, m, archive, max_pages=args.max_pages) for m in args.months]
    # One tracker request per run, reused for each requested month.
    cached = archive_fetch(TRACKER, archive)
    trackers = [collect_tracker(args.year, m, archive, fetch=lambda *_: cached) for m in args.months]
    artifact = {"observed_at": datetime.now(timezone.utc).isoformat(), "club_annual_route": club_route,
                "windows": windows, "club_trackers": trackers, "season_history_complete": False,
                "runtime_state_changed": False}
    (args.output / "recovery.json").write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(args.output.resolve()), "windows": [
        {"month": w["month"], "status": w["status"], "rows": len(w["rows"])} for w in windows],
        "club_statements": [len(t["rows"]) for t in trackers], "season_history_complete": False}))


if __name__ == "__main__":
    main()
