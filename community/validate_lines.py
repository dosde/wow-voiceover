"""Checks contributed line files and builds the confirmed collection.

A line is only accepted once several independent contributors reported the very
same text for it. Everything else waits in pending_lines.json with its vote
count, so a single faked contribution cannot enter the collection.

    python community/validate_lines.py --check      # validate every contribution
    python community/validate_lines.py --rebuild    # rebuild the collection

No audio is produced or downloaded here.
"""

import argparse
import hashlib
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LINES_DIR = ROOT / "lines"
COMBINED = ROOT / "collected_lines.json"
PENDING = ROOT / "pending_lines.json"

# How many different contributors must report the same text
CONFIRMATIONS = 2
# When contributors disagree, the winning text also needs this many times the votes of the
# runner-up. That way a single faked variant cannot block a line, but it cannot win either.
MAJORITY_FACTOR = 2

ALLOWED = {"event", "questID", "title", "npc", "npcID", "npcType", "sex", "model",
           "race", "zone", "subzone", "text", "locale", "gender"}
META = {"contributor", "client", "collected"}
EVENTS = {"accept", "progress", "complete", "gossip"}
MAX_TEXT = 6000
MAX_LINES_PER_FILE = 5000
MAX_FILE_BYTES = 5 * 1024 * 1024
FORBIDDEN = re.compile(r"https?://|[\w.+-]+@[\w-]+\.[\w.]+", re.I)
CONTRIBUTOR = re.compile(r"^[0-9a-f]{16}$")


def load(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    meta = data.get("_meta", {}) if isinstance(data, dict) else {}
    lines = {k: v for k, v in data.items() if k != "_meta"} if isinstance(data, dict) else {}
    return meta, lines


def check_file(path):
    problems = []
    if path.stat().st_size > MAX_FILE_BYTES:
        return [f"{path.name}: larger than 5 MB"]
    try:
        meta, lines = load(path)
    except json.JSONDecodeError as error:
        return [f"{path.name}: invalid JSON ({error})"]
    if not CONTRIBUTOR.match(str(meta.get("contributor", ""))):
        problems.append(f"{path.name}: missing or malformed _meta.contributor")
    if set(meta) - META:
        problems.append(f"{path.name}: unexpected fields in _meta {sorted(set(meta) - META)}")
    if len(lines) > MAX_LINES_PER_FILE:
        problems.append(f"{path.name}: more than {MAX_LINES_PER_FILE} lines")
    for key, entry in lines.items():
        where = f"{path.name} [{key[:40]}]"
        if not isinstance(entry, dict):
            problems.append(f"{where}: entry is not an object")
            continue
        unknown = set(entry) - ALLOWED
        if unknown:
            problems.append(f"{where}: unexpected fields {sorted(unknown)}")
        if entry.get("event") not in EVENTS:
            problems.append(f"{where}: unknown event {entry.get('event')!r}")
        text = entry.get("text")
        if not isinstance(text, str) or not text.strip():
            problems.append(f"{where}: missing text")
        elif len(text) > MAX_TEXT:
            problems.append(f"{where}: text longer than {MAX_TEXT} characters")
        elif FORBIDDEN.search(text):
            problems.append(f"{where}: text contains a link or an address")
    return problems


def normalize(text):
    return re.sub(r"\s+", " ", text or "").strip().lower()


def rebuild():
    """Counts votes per line and keeps only what several contributors agree on."""
    votes = defaultdict(lambda: defaultdict(dict))  # key -> text hash -> contributor -> entry
    for path in sorted(LINES_DIR.glob("*.json")):
        meta, lines = load(path)
        contributor = meta.get("contributor")
        for key, entry in lines.items():
            digest = hashlib.sha1(normalize(entry.get("text")).encode("utf-8")).hexdigest()[:12]
            votes[key][digest].setdefault(contributor, entry)

    accepted, pending = {}, {}
    for key, variants in votes.items():
        ranked = sorted(variants.items(), key=lambda kv: len(kv[1]), reverse=True)
        digest, contributors = ranked[0]
        runner_up = len(ranked[1][1]) if len(ranked) > 1 else 0
        votes_for_winner = len(contributors)
        needed = max(CONFIRMATIONS, runner_up * MAJORITY_FACTOR)
        # The entry that knows most about the speaker represents this text
        entry = max(contributors.values(), key=lambda e: (bool(e.get("npcID")), bool(e.get("race")), len(e)))
        if votes_for_winner >= needed:
            accepted[key] = entry
        else:
            pending[key] = {**entry, "_votes": votes_for_winner, "_needed": needed,
                            "_variants": len(ranked), "_hash": digest}

    COMBINED.write_text(json.dumps(accepted, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    PENDING.write_text(json.dumps(pending, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    return accepted, pending


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--rebuild", action="store_true")
    args = parser.parse_args()

    LINES_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(LINES_DIR.glob("*.json"))
    problems = [p for path in files for p in check_file(path)]
    if problems:
        print("\n".join(problems))
        sys.exit(1)
    print(f"{len(files)} contribution files are fine")
    if args.rebuild:
        accepted, pending = rebuild()
        print(f"{len(accepted)} confirmed lines, {len(pending)} waiting for confirmation")


if __name__ == "__main__":
    main()
