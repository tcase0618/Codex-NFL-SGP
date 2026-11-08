"""Join complete candidate profiles and hash-bound visual depth transcriptions."""
import argparse
import hashlib
import json
from pathlib import Path

from .source_based_parlays import DOMAINS
from .team_candidates import team_candidates


def run(context_path: Path, reviewed_path: Path, output_path: Path | None = None):
    artifact = json.loads(context_path.read_text(encoding="utf-8"))
    reviewed = json.loads(reviewed_path.read_text(encoding="utf-8"))
    for source in artifact["sources"]:
        if source["kind"] != "depth" or source["rows"]:
            continue
        for image in source.get("image_sources", []):
            for record in reviewed:
                if (record["team"] == source["team"] and record["content_sha256"] == image["content_sha256"]
                        and hashlib.sha256(Path(image["file"]).read_bytes()).hexdigest() == image["content_sha256"]):
                    source["rows"] = record["rows"]
                    source["status"] = "VISUALLY_TRANSCRIBED"
                    source["transcription"] = {k: record[k] for k in ("content_sha256", "reviewed_at", "chart_label_date")}
    artifact["candidate_profiles"] = {team: team_candidates(artifact["sources"], team) for team in DOMAINS}
    target = output_path or context_path.with_name(context_path.stem + "-reviewed.json")
    if target.exists():
        raise FileExistsError(target)
    target.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--context", type=Path, required=True)
    parser.add_argument("--reviewed", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    print(run(args.context, args.reviewed, args.output))
