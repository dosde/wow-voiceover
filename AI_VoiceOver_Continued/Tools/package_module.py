"""Pack a generated sound pack so others can just install it.

Zips a generated data module the way an addon is installed (one folder inside the
archive) and, with --upload, publishes it as a GitHub release asset.

    python package_module.py --language de
    python package_module.py --language de --upload --tag forever-de-2026-09

A release upload needs a GitHub token with "public_repo" scope in Tools/.env:

    GITHUB_TOKEN=ghp_...
"""

import argparse
import json
import sys
import urllib.request
import zipfile
from datetime import date
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
ADDONS_DIR = TOOLS_DIR.parents[1]
sys.path.insert(0, str(TOOLS_DIR))
from generate_voices import BASE_MODULE_NAME, LANGUAGES, read_api_key, read_json  # noqa: E402

REPOSITORY = "dosde/wow-voiceover"
API = "https://api.github.com"


def request(url, token, data=None, headers=None, method=None):
    request = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"token {token}", "Accept": "application/vnd.github+json",
        "User-Agent": "voiceover-package", **(headers or {})})
    with urllib.request.urlopen(request, timeout=300) as response:
        return json.loads(response.read() or b"{}")


def build_zip(module_dir, module_name, target):
    files = [p for p in module_dir.rglob("*") if p.is_file() and p.name != "manifest.json"]
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in files:
            archive.write(path, Path(module_name) / path.relative_to(module_dir))
    return len(files), target.stat().st_size


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--language", choices=sorted(LANGUAGES), default="en")
    parser.add_argument("--upload", action="store_true", help="Publish the archive as a GitHub release")
    parser.add_argument("--repository", default=REPOSITORY)
    parser.add_argument("--tag", help="Release tag (default: voicepack-<locale>-<date>)")
    args = parser.parse_args()

    locale, suffix, _, _, _ = LANGUAGES[args.language]
    module_name = BASE_MODULE_NAME + suffix
    module_dir = ADDONS_DIR / module_name
    if not module_dir.exists():
        sys.exit(f"{module_dir} does not exist - generate the pack first.")

    manifest = read_json(module_dir / "manifest.json", {})
    tag = args.tag or f"voicepack-{locale}-{date.today():%Y-%m-%d}"
    archive = TOOLS_DIR / "cache" / f"{module_name}-{tag}.zip"
    count, size = build_zip(module_dir, module_name, archive)
    print(f"{archive} - {count} files, {size / 1e6:.1f} MB, {len(manifest)} voice lines")
    if not args.upload:
        return

    token = read_api_key("GITHUB_TOKEN")
    if not token:
        sys.exit("No GITHUB_TOKEN found in Tools/.env")
    engines = sorted({item.get("engine", "?") for item in manifest.values()})
    body = (f"Generated sound pack, {len(manifest)} voice lines, {LANGUAGES[args.language][0]}.\n\n"
            f"Engine: {', '.join(engines)}\n\n"
            f"Unzip into `Interface/AddOns` next to the player addon and restart the game.")
    release = request(f"{API}/repos/{args.repository}/releases", token,
                      data=json.dumps({"tag_name": tag, "name": tag, "body": body}).encode("utf-8"))
    upload_url = release["upload_url"].split("{")[0] + f"?name={archive.name}"
    print("Uploading ...")
    request(upload_url, token, data=archive.read_bytes(),
            headers={"Content-Type": "application/zip"}, method="POST")
    print("Release:", release["html_url"])


if __name__ == "__main__":
    main()
