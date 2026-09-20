"""Checks contributed line files and rebuilds the combined file.

Run by the workflow; no audio is produced or downloaded here.

    python community/validate_lines.py --check      # validate every contribution
    python community/validate_lines.py --rebuild    # rebuild collected_lines.json
"""

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LINES_DIR = ROOT / "lines"
COMBINED = ROOT / "collected_lines.json"

ALLOWED = {"event", "questID", "title", "npc", "npcID", "npcType", "sex", "model",
           "race", "zone", "subzone", "text", "locale", "gender"}
EVENTS = {"accept", "progress", "complete", "gossip"}
MAX_TEXT = 6000
MAX_FILE_BYTES = 5 * 1024 * 1024
FORBIDDEN = re.compile(r"https?://|[\w.+-]+@[\w-]+\.[\w.]+", re.I)


def check_file(path):
    problems = []
    if path.stat().st_size > MAX_FILE_BYTES:
        problems.append(f"{path.name}: larger than 5 MB")
        return problems
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        return [f"{path.name}: invalid JSON ({error})"]
    if not isinstance(data, dict):
        return [f"{path.name}: expected an object of lines"]
    for key, entry in data.items():
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


def rebuild():
    combined = {}
    for path in sorted(LINES_DIR.glob("*.json")):
        for key, entry in json.loads(path.read_text(encoding="utf-8")).items():
            known = combined.get(key)
            if not known or (not known.get("npcID") and entry.get("npcID")) or (not known.get("race") and entry.get("race")):
                combined[key] = entry
    COMBINED.write_text(json.dumps(combined, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    return combined


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--rebuild", action="store_true")
    args = parser.parse_args()

    LINES_DIR.mkdir(parents=True, exist_ok=True)
    problems = []
    for path in sorted(LINES_DIR.glob("*.json")):
        problems.extend(check_file(path))
    if problems:
        print("\n".join(problems))
        sys.exit(1)
    print(f"{len(list(LINES_DIR.glob('*.json')))} contribution files are fine")
    if args.rebuild:
        combined = rebuild()
        print(f"{len(combined)} lines in {COMBINED.relative_to(ROOT.parent)}")


if __name__ == "__main__":
    main()
