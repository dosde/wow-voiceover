"""Import all Classic quest and gossip texts from the open cmangos database.

Instead of waiting until you meet an NPC in game, this reads every quest and
NPC greeting of Classic from the cmangos "classic-db" (SQLite export, freely
available on GitHub) and writes them in the format that generate_voices.py
consumes. German texts come from the community translation of the same project.

    python import_database.py                 # English, only what the Vanilla pack lacks
    python import_database.py --language de   # German, everything
    python import_database.py --language de --limit 50   # a small test run

    python generate_voices.py --language de --import cache/imported_deDE.json

The database (about 40 MB) is downloaded once into Tools/cache/db.
"""

import argparse
import json
import re
import sqlite3
import sys
import urllib.request
import zipfile
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
ADDONS_DIR = TOOLS_DIR.parents[1]
CACHE_DIR = TOOLS_DIR / "cache"
DB_DIR = CACHE_DIR / "db"
VANILLA_MODULE = "AI_VoiceOverData_Vanilla"

DB_URL = "https://github.com/cmangos/classic-db/releases/download/latest/classic-sqlite-db.zip"
DB_FILE = "classicmangos.sqlite"
LOCALE_BASE_URL = "https://raw.githubusercontent.com/cmangos/classic-db/master/locales/German"
LOCALE_SQL_URL = f"{LOCALE_BASE_URL}/locales_quest_german.sql"
CREATURE_SQL_URL = f"{LOCALE_BASE_URL}/locales_creature_german.sql"

# Locale suffix used by the database's locales_* tables (loc3 = German)
LANGUAGES = {"en": ("enUS", None), "de": ("deDE", 3)}

PLAYER_WORDS = {
    "enUS": {"name": "adventurer", "class": "champion", "race": "traveler"},
    "deDE": {"name": "Abenteurer", "class": "Held", "race": "Reisender"},
}


def download(url, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {url} ...")
    with urllib.request.urlopen(url, timeout=600) as response, open(target, "wb") as out:
        while chunk := response.read(1 << 20):
            out.write(chunk)


def ensure_database():
    database = DB_DIR / DB_FILE
    if database.exists():
        return database
    archive = DB_DIR / "classic-sqlite-db.zip"
    if not archive.exists():
        download(DB_URL, archive)
    with zipfile.ZipFile(archive) as zf:
        zf.extract(DB_FILE, DB_DIR)
    return database


# --- German quest texts (plain SQL dump of the locales_quest table) ---

def parse_sql_values(text):
    """Yields (columns, values) per row of INSERT ... VALUES (..),(..); statements.

    columns is the explicit column list of the statement, or None when the dump
    relies on the table's own column order.
    """
    pattern = r"INSERT INTO\s+`?\w+`?\s*(\([^)]*\))?\s*VALUES\s*(.*?);\s*$"
    for statement in re.finditer(pattern, text, re.S | re.M):
        columns = [c.strip(" `") for c in statement.group(1)[1:-1].split(",")] if statement.group(1) else None
        body = statement.group(2)
        pos, row, value, in_string = 0, [], [], False
        while pos < len(body):
            char = body[pos]
            if in_string:
                if char == "\\":
                    escaped = body[pos + 1]
                    value.append({"n": "\n", "r": "\r", "t": "\t", "0": ""}.get(escaped, escaped))
                    pos += 2
                    continue
                if char == "'":
                    if pos + 1 < len(body) and body[pos + 1] == "'":
                        value.append("'")
                        pos += 2
                        continue
                    in_string = False
                    row.append("".join(value))
                    value = []
                else:
                    value.append(char)
            elif char == "'":
                in_string = True
                value = []
            elif char in ",)":
                token = "".join(value).strip()
                if token:
                    row.append(None if token.upper() == "NULL" else token)
                value = []
                if char == ")":
                    yield columns, row
                    row = []
            elif char != "(" and not char.isspace():
                value.append(char)
            pos += 1


def load_german_creatures():
    """NPC names in German (locales_creature: entry, name_loc1..8, ...)."""
    path = DB_DIR / "locales_creature_german.sql"
    if not path.exists():
        download(CREATURE_SQL_URL, path)
    names = {}
    for columns, row in parse_sql_values(path.read_text(encoding="utf-8", errors="replace")):
        index = columns.index("name_loc3") if columns and "name_loc3" in columns else 3
        try:
            entry = int(row[0])
        except (TypeError, ValueError):
            continue
        if len(row) > index and row[index]:
            names[entry] = row[index]
    return names


def load_german_quests():
    path = DB_DIR / "locales_quest_german.sql"
    if not path.exists():
        download(LOCALE_SQL_URL, path)
    # Column order of locales_quest: entry, then Title/Details/Objectives/OfferRewardText/RequestItemsText, 8 locales each
    fields = {"Title": 1, "Details": 9, "Objectives": 17, "OfferRewardText": 25, "RequestItemsText": 33}
    german = {}
    for columns, row in parse_sql_values(path.read_text(encoding="utf-8", errors="replace")):
        try:
            entry = int(row[0])
        except (TypeError, ValueError):
            continue
        indexes = {name: (columns.index(f"{name}_loc3") if columns and f"{name}_loc3" in columns else base + 2)
                   for name, base in fields.items()}
        german[entry] = {name: (row[i] if i < len(row) else None) for name, i in indexes.items()}
    return german


# --- Text handling ---

def expand(text, locale):
    """Resolves the game's text placeholders. Gendered texts yield two variants."""
    text = text.replace("$B", "\n").replace("$b", "\n")
    words = PLAYER_WORDS[locale]
    text = re.sub(r"\$[Nn]", words["name"], text)
    text = re.sub(r"\$[Cc]", words["class"], text)
    text = re.sub(r"\$[Rr]", words["race"], text)
    if re.search(r"\$[Gg]", text):
        male = re.sub(r"\$[Gg]\s*([^:;]*):([^;]*);", lambda m: m.group(1).strip(), text)
        female = re.sub(r"\$[Gg]\s*([^:;]*):([^;]*);", lambda m: m.group(2).strip(), text)
        return {"m": male, "f": female}
    return {"": text}


def normalize(text):
    return re.sub(r"\s+", " ", (text or "")).strip().lower()


# --- What the Vanilla pack already covers ---

class VanillaPack:
    def __init__(self):
        self.files, self.gossip = set(), {}
        base = ADDONS_DIR / VANILLA_MODULE / "generated"
        if not base.exists():
            return
        sys.path.insert(0, str(TOOLS_DIR))
        from generate_voices import LuaParser
        lengths = LuaParser((base / "sound_length_table.lua").read_text(encoding="utf-8", errors="replace")).assignments()
        self.files = set(lengths.get("SoundLengthLookupByFileName", {}))
        gossip = LuaParser((base / "gossip_file_lookups.lua").read_text(encoding="utf-8", errors="replace")).assignments()
        for npc, texts in (gossip.get("GossipLookupByNPCID") or {}).items():
            self.gossip[npc] = {normalize(t) for t in texts}

    def has_quest(self, quest_id, event):
        return any(f"{prefix}{quest_id}-{event}" in self.files for prefix in ("", "m-", "f-"))

    def has_gossip(self, npc_id, text):
        return normalize(text) in self.gossip.get(npc_id, ())


# --- Import ---

def import_lines(database, locale, german, pack, with_gossip=True, german_names=None):
    c = sqlite3.connect(database)
    c.text_factory = lambda b: b.decode("utf-8", "replace")
    entries = {}
    german_names = german_names or {}

    quest_npc, quest_object = {}, {}
    for npc, quest in c.execute("select id, quest from creature_questrelation"):
        quest_npc.setdefault(quest, npc)
    for obj, quest in c.execute("select id, quest from gameobject_questrelation"):
        quest_object.setdefault(quest, obj)
    npc_names = dict(c.execute("select Entry, Name from creature_template"))
    npc_names.update(german_names)
    object_names = dict(c.execute("select entry, name from gameobject_template"))

    events = (("Details", "accept"), ("RequestItemsText", "progress"), ("OfferRewardText", "complete"))
    columns = "entry, Title, Details, RequestItemsText, OfferRewardText"
    for quest_id, title, details, request, offer in c.execute(f"select {columns} from quest_template"):
        texts = {"Details": details, "RequestItemsText": request, "OfferRewardText": offer}
        translated = german.get(quest_id, {}) if german else {}
        npc_id, npc_type = quest_npc.get(quest_id), "creature"
        if not npc_id:
            npc_id, npc_type = quest_object.get(quest_id), "object"
        name = (npc_names if npc_type == "creature" else object_names).get(npc_id)

        for column, event in events:
            text = translated.get(column) if german else texts[column]
            if not text or not text.strip():
                continue
            if locale == "enUS" and pack.has_quest(quest_id, event):
                continue  # the Vanilla pack already has a recording
            quest_title = (translated.get("Title") if german else title) or title
            for gender, variant in expand(text, locale).items():
                key = f"db:{locale}:{gender}{quest_id}-{event}"
                entries[key] = {
                    "event": event, "questID": quest_id, "title": quest_title if event == "accept" else None,
                    "npc": name, "npcID": npc_id, "npcType": npc_type if npc_id else None,
                    "text": variant, "gender": gender or None, "locale": locale, "source": "database",
                }

    if not with_gossip:
        return entries

    # Gossip: creature -> gossip menu -> npc_text, German through the broadcast texts
    menu_texts = {}
    for menu, text_id in c.execute("select entry, text_id from gossip_menu where text_id > 0"):
        menu_texts.setdefault(menu, []).append(text_id)
    npc_text_columns = [f"text{i}_0" for i in range(8)]
    npc_texts = {row[0]: row[1:] for row in
                 c.execute(f"select ID, {', '.join(npc_text_columns)} from npc_text")}
    broadcast = {row[0]: row[1:] for row in
                 c.execute("select Id, " + ", ".join(f"BroadcastTextId{i}" for i in range(8)) + " from npc_text_broadcast_text")}
    german_broadcast = dict(c.execute("select Id, Text_lang from broadcast_text_locale where Locale = ?", (locale,))) if german else {}

    for npc_id, name, menu in c.execute("select Entry, Name, GossipMenuId from creature_template where GossipMenuId > 0"):
        for text_id in menu_texts.get(menu, []):
            english_texts = npc_texts.get(text_id) or ()
            broadcast_ids = broadcast.get(text_id) or ()
            # Some menus only exist as broadcast texts, so walk both sources
            for index in range(max(len(english_texts), len(broadcast_ids))):
                text = english_texts[index] if index < len(english_texts) else None
                if german:
                    broadcast_id = broadcast_ids[index] if index < len(broadcast_ids) else None
                    text = german_broadcast.get(broadcast_id) if broadcast_id else None
                if not text or not text.strip():
                    continue
                if locale == "enUS" and pack.has_gossip(npc_id, text):
                    continue
                for gender, variant in expand(text, locale).items():
                    entries[f"db:{locale}:gossip:{npc_id}:{text_id}:{index}{gender}"] = {
                        "event": "gossip", "npc": name, "npcID": npc_id, "npcType": "creature",
                        "text": variant, "gender": gender or None, "locale": locale, "source": "database",
                    }

    # Greetings shown when an NPC offers several quests
    greeting_column = "Text"
    for npc_id, kind, text in c.execute(f"select Entry, Type, {greeting_column} from questgiver_greeting"):
        if kind != 0 or not text or not text.strip():
            continue
        if german:
            continue  # no German translation of the greetings in the database
        if locale == "enUS" and pack.has_gossip(npc_id, text):
            continue
        for gender, variant in expand(text, locale).items():
            entries[f"db:{locale}:greeting:{npc_id}{gender}"] = {
                "event": "gossip", "npc": npc_names.get(npc_id), "npcID": npc_id, "npcType": "creature",
                "text": variant, "gender": gender or None, "locale": locale, "source": "database",
            }
    return entries


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--language", choices=sorted(LANGUAGES), default="en")
    parser.add_argument("--db", type=Path, help="Path to classicmangos.sqlite (downloaded if missing)")
    parser.add_argument("--output", type=Path, help="Output file (default: cache/imported_<locale>.json)")
    parser.add_argument("--no-gossip", action="store_true", help="Quests only")
    parser.add_argument("--all", action="store_true", help="Also import lines the Vanilla pack already covers")
    parser.add_argument("--limit", type=int, default=0, help="Keep at most N lines (for a test run)")
    args = parser.parse_args()

    locale, _ = LANGUAGES[args.language]
    database = args.db or ensure_database()
    german = load_german_quests() if args.language == "de" else None
    pack = VanillaPack()
    if args.all:
        pack.files, pack.gossip = set(), {}
    elif locale == "enUS" and not pack.files:
        print("Note: no Vanilla sound pack found, importing everything.")

    entries = import_lines(database, locale, german, pack, not args.no_gossip,
                           load_german_creatures() if args.language == "de" else None)
    if args.limit:
        entries = dict(list(entries.items())[:args.limit])

    output = args.output or CACHE_DIR / f"imported_{locale}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(entries, ensure_ascii=False, indent=1), encoding="utf-8")

    quests = sum(1 for e in entries.values() if e["event"] != "gossip")
    characters = sum(len(e["text"]) for e in entries.values())
    print(f"{len(entries)} lines ({quests} quest, {len(entries) - quests} gossip), {characters} characters -> {output}")
    print(f"Next: python generate_voices.py --language {args.language} --import \"{output}\"")


if __name__ == "__main__":
    main()
