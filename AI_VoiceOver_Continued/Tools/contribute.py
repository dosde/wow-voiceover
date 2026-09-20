"""Share the lines you collected in game with the community.

The addon records every quest and gossip text that has no recording, already with
your character's name, class and race replaced by the game's placeholders. This
script bundles them, checks them once more for personal data and offers them to
the community repository as a pull request. No audio is created or uploaded -
only the texts and what is known about the speaker.

    python contribute.py --dry-run    # show what would be shared
    python contribute.py              # push a branch and open a pull request
    python contribute.py --merge      # (maintainers) rebuild the combined file

A pull request needs a GitHub token with "public_repo" scope in Tools/.env:

    GITHUB_TOKEN=ghp_...

Without a token the branch is pushed and the link for opening the pull request
by hand is printed.
"""

import argparse
import json
import secrets
import os
import re
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
CACHE_DIR = TOOLS_DIR / "cache"
sys.path.insert(0, str(TOOLS_DIR))
from generate_voices import find_saved_variables, load_missing_lines, read_api_key, read_json, write_json  # noqa: E402

REPOSITORY = "dosde/wow-voiceover"
BRANCH_BASE = "master"
LINES_DIR = "community/lines"
COMBINED_FILE = "community/collected_lines.json"
# Fields that are shared; everything else (timestamps, local state) stays on your machine
SHARED_FIELDS = ("event", "questID", "title", "npc", "npcID", "npcType", "sex", "model",
                 "race", "zone", "subzone", "text", "locale", "gender")
NAME_PATTERN = re.compile(r"\b[A-ZÄÖÜ][a-zäöüß]{2,11}\b")


def contributor_id():
    """A random id for this installation, so votes can be counted without knowing who you are."""
    path = CACHE_DIR / "contributor.txt"
    if path.exists():
        value = path.read_text(encoding="utf-8").strip()
        if len(value) == 16:
            return value
    value = secrets.token_hex(8)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")
    return value


def git(*args, cwd, check=True):
    return subprocess.run(["git", *args], cwd=cwd, check=check, capture_output=True, text=True).stdout.strip()


def own_character_names(paths):
    """Character names of this installation, so they can never leak into a contribution."""
    names = set()
    for path in paths:
        for match in re.finditer(r'\["([^"]+) - [^"]+"\]', path.read_text(encoding="utf-8", errors="replace")):
            names.add(match.group(1))
    return names


def clean_entry(entry, names):
    shared = {k: entry[k] for k in SHARED_FIELDS if entry.get(k) is not None}
    for field in ("text", "title", "npc"):
        if field in shared:
            for name in names:
                shared[field] = shared[field].replace(name, "$N")
    return shared


def collect(account=None):
    paths = find_saved_variables(account)
    if not paths:
        sys.exit("No SavedVariables found. Play with the addon, then /reload or log out.")
    names = own_character_names(paths)
    lines = load_missing_lines(paths)
    cleaned = {}
    for key, entry in lines.items():
        if isinstance(entry, dict) and entry.get("text"):
            cleaned[key] = clean_entry(entry, names)
    return cleaned, names


def warn_about_names(entries, names):
    """Very rough check for anything that still looks like a character name."""
    suspicious = set()
    for entry in entries.values():
        for name in names:
            if name in entry.get("text", ""):
                suspicious.add(name)
    return suspicious


def open_pull_request(token, head, title, body):
    request = urllib.request.Request(
        f"https://api.github.com/repos/{REPOSITORY}/pulls",
        data=json.dumps({"title": title, "head": head, "base": BRANCH_BASE, "body": body}).encode("utf-8"),
        headers={"Authorization": f"token {token}", "Accept": "application/vnd.github+json",
                 "User-Agent": "voiceover-contribute"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read())["html_url"]


def merge_lines(checkout):
    """Runs the repository's own validation and vote counting."""
    subprocess.run([sys.executable, str(checkout / "community" / "validate_lines.py"), "--check", "--rebuild"],
                   cwd=checkout, check=True)
    return read_json(checkout / COMBINED_FILE, {})


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--account", help="WoW account folder name (default: all)")
    parser.add_argument("--repository", default=REPOSITORY, help="owner/name of the community repository")
    parser.add_argument("--dry-run", action="store_true", help="Only show what would be shared")
    parser.add_argument("--merge", action="store_true", help="Rebuild the combined file from all contributions")
    parser.add_argument("--checkout", type=Path, default=CACHE_DIR / "community",
                        help="Where the community repository is cloned to")
    args = parser.parse_args()

    checkout = args.checkout
    if not (checkout / ".git").exists():
        checkout.parent.mkdir(parents=True, exist_ok=True)
        print(f"Cloning https://github.com/{args.repository} ...")
        subprocess.run(["git", "clone", "--depth", "50", "--branch", BRANCH_BASE,
                        f"https://github.com/{args.repository}.git", str(checkout)], check=True)
        git("config", "core.longpaths", "true", cwd=checkout)
    else:
        git("fetch", "origin", BRANCH_BASE, cwd=checkout, check=False)
        git("checkout", BRANCH_BASE, cwd=checkout, check=False)
        git("reset", "--hard", f"origin/{BRANCH_BASE}", cwd=checkout, check=False)

    if args.merge:
        combined = merge_lines(checkout)
        print(f"{len(combined)} lines in {COMBINED_FILE}")
        return

    entries, names = collect(args.account)
    existing = read_json(checkout / COMBINED_FILE, {})
    # Lines that are still waiting for confirmation are worth sending again - that is the second vote
    new = {k: v for k, v in entries.items() if k not in existing}
    pending = read_json(checkout / "community/pending_lines.json", {})
    confirming = sum(1 for k in new if k in pending)
    print(f"{len(entries)} collected lines, {len(new)} not in the community file yet"
          f" ({confirming} of them would confirm a line someone else reported)")
    suspicious = warn_about_names(new, names)
    if suspicious:
        print("Stopping: these character names are still in the texts:", ", ".join(sorted(suspicious)))
        return
    if not new:
        return
    if args.dry_run:
        for key, entry in list(new.items())[:20]:
            print(f"  {key[:60]:60} {entry.get('npc') or '?'}")
        print("  ..." if len(new) > 20 else "")
        return

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    contributor = contributor_id()
    name = f"{stamp}-{contributor[:8]}"
    payload = {"_meta": {"contributor": contributor, "collected": stamp}, **new}
    write_json(checkout / LINES_DIR / f"{name}.json", payload)

    branch = f"lines/{name}"
    git("checkout", "-b", branch, cwd=checkout)
    git("add", LINES_DIR, cwd=checkout)
    git("commit", "-m", f"Add {len(new)} collected voice lines", cwd=checkout)
    print("Pushing ...")
    subprocess.run(["git", "push", "-u", "origin", branch], cwd=checkout, check=True)

    token = read_api_key("GITHUB_TOKEN")
    body = (f"{len(new)} lines collected in game.\n\n"
            "Character name, class and race are replaced by the game's placeholders. "
            "No audio is included.")
    if token:
        try:
            print("Pull request:", open_pull_request(token, branch, f"Add {len(new)} collected voice lines", body))
            return
        except Exception as error:
            print(f"Could not open the pull request automatically: {error}")
    print(f"Open the pull request here: https://github.com/{args.repository}/pull/new/{branch}")


if __name__ == "__main__":
    main()
