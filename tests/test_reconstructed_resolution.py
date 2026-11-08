import json

from research.parlay_replay import digest
from research import reconstructed_resolution as replay


def test_settlement_diagnosis_does_not_reselect_or_hide_nonparticipants(tmp_path, monkeypatch):
    tickets = [{"ticket_id": "one", "game_id": "g"}, {"ticket_id": "two", "game_id": "g"}]
    manifest = {"tickets": tickets}
    grades = [{"ticket_id": "one", "type": "3", "status": "unresolved", "legs": [
        {"player_id": "1", "team_id": "CHI", "player": "Zero", "status": "unresolved"}]},
        {"ticket_id": "two", "type": "5", "status": "lost", "legs": [
            {"player_id": "2", "team_id": "CHI", "player": "Absent", "status": "unresolved"},
            {"player_id": "3", "team_id": "CHI", "player": "Miss", "status": "lost"}]}]
    (tmp_path / "selection-manifest.json").write_text(json.dumps(manifest))
    (tmp_path / "statistical-outcomes.json").write_text(json.dumps({"tickets_sha256": digest(tickets), "grades": grades}))
    before = (tmp_path / "selection-manifest.json").read_bytes()
    monkeypatch.setattr(replay, "cached_json", lambda *args: {"header": {"competitions": [{"competitors": [
        {"team": {"abbreviation": "CHI", "id": "1"}}, {"team": {"abbreviation": "SEA", "id": "2"}}]}]}})
    entries = [{"playerId": "1", "didNotPlay": False}, {"playerId": "2", "didNotPlay": True}]
    entries += [{"playerId": str(i), "didNotPlay": False} for i in range(3, 21)]
    monkeypatch.setattr(replay, "fetch_json", lambda *args: {"entries": entries})
    result = replay.run(tmp_path)
    assert result["counts"] == {"lost": 1, "nonparticipant_ticket": 1}
    assert (tmp_path / "selection-manifest.json").read_bytes() == before
