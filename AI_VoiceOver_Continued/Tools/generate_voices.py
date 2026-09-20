"""Generate voiceovers for lines that VoiceOver Continued collected in-game.

The addon saves quest and gossip texts without a recording in
WTF/Account/<account>/SavedVariables/AI_VoiceOver_Continued.lua
(VoiceOverDB.global.MissingLines). This script turns them into MP3 files and
writes a data module next to the addon:

    AI_VoiceOverData_Forever        English  (--language en, default)
    AI_VoiceOverData_Forever_deDE   German   (--language de)

    python generate_voices.py                      # free Microsoft voices (edge-tts)
    python generate_voices.py --dry-run            # show what would be generated, with voices
    python generate_voices.py --language de        # German; English lines are translated
    python generate_voices.py --engine elevenlabs  # paid, needs ELEVENLABS_API_KEY
    python generate_voices.py --engine elevenlabs --clone   # clone voices of NPCs with recordings

Voices
    voices.json maps races to voices. The race comes from the NPC's 3D model
    (captured in-game) or is guessed from the zone. NPCs that already have
    recordings in AI_VoiceOverData_Vanilla keep a similar voice: with edge-tts
    the voice and pitch closest to the recordings are picked (needs ffmpeg and
    numpy), with ElevenLabs --clone creates a voice clone from the recordings.
    Exclamations, questions and trailing "..." are spoken with a matching tone.

Translation (only when a line's language differs from --language)
    Claude (default): uses the Anthropic API credentials (ANTHROPIC_API_KEY or `ant auth login`).
    DeepL: --translator deepl with DEEPL_API_KEY.

Restart the game afterwards (a /reload does not pick up new sound files).
"""

import argparse
import asyncio
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
ADDON_DIR = TOOLS_DIR.parent
ADDONS_DIR = ADDON_DIR.parent
GAME_DIR = ADDONS_DIR.parents[1]
BASE_MODULE_NAME = "AI_VoiceOverData_Forever"
VANILLA_MODULE = "AI_VoiceOverData_Vanilla"
INTERFACE = "16001"
VOICES_FILE = TOOLS_DIR / "voices.json"

LANGUAGES = {
    # language: (client locale, module suffix, module priority, voices.json profile key, preview text)
    "en": ("enUS", "", 50, "profiles", "Well met, traveler. These are dark times, and I could use a hand. Will you help me?"),
    "de": ("deDE", "_deDE", 150, "profiles_de", "Seid gegrüßt, Reisender. Dies sind finstere Zeiten, und ich könnte Hilfe gebrauchen. Helft Ihr mir?"),
}
LANGUAGE_NAMES = {"enUS": "English", "deDE": "German", "frFR": "French", "esES": "Spanish", "esMX": "Spanish",
                  "itIT": "Italian", "ptBR": "Portuguese", "ruRU": "Russian"}

# Bytes per second of the constant bitrate MP3 formats requested from each engine
MP3_BYTES_PER_SECOND = {
    "edge": 48000 / 8,         # audio-24khz-48kbitrate-mono-mp3 (edge-tts default)
    "elevenlabs": 128000 / 8,  # mp3_44100_128
    "openai": 32000 / 8,       # rough fallback, ffprobe is used when available
}
OPENAI_API = "https://api.openai.com/v1/audio/speech"
OPENAI_MODEL = "gpt-4o-mini-tts"
ELEVENLABS_API = "https://api.elevenlabs.io/v1"
ELEVENLABS_MODEL = "eleven_multilingual_v2"
CLAUDE_MODEL = "claude-opus-5"

# Character effects applied with ffmpeg after synthesis. Shifting the sample rate and
# compensating with atempo moves the formants too, which changes the apparent body size
# of the speaker - that is what makes a goblin sound small and a tauren huge.
def resample(factor, extra=""):
    return f"asetrate=24000*{factor},aresample=24000,atempo={1 / factor:.4f}" + (("," + extra) if extra else "")


EFFECTS = {
    "huge":    resample(0.78, "lowpass=f=7000"),                       # tauren, monsters
    "big":     resample(0.88),                                          # orc, vrykul
    "small":   resample(1.14),                                          # goblin
    "tiny":    resample(1.22),                                          # gnome
    "hollow":  resample(0.93, "aecho=0.8:0.7:45:0.35,highpass=f=170"),  # undead
    "rough":   resample(0.92, "vibrato=f=5:d=0.12"),                    # troll
    "sibilant": resample(0.95, "aecho=0.8:0.5:25:0.2,treble=g=4"),      # naga
    "airy":    resample(1.04, "aecho=0.9:0.4:60:0.15"),                 # night elf, blood elf
    "stone":   resample(0.86, "aecho=0.8:0.6:70:0.3,lowpass=f=6500"),   # draenei
}


def apply_effect(path, effect):
    chain = EFFECTS.get(effect)
    if not chain:
        return
    temp = path.with_suffix(".tmp.mp3")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(path), "-af", chain,
                    "-ar", "24000", "-ac", "1", "-b:a", "48k", str(temp)], check=True)
    temp.replace(path)


# Tone per sentence type: (rate change, pitch change) for edge-tts
MOODS = {
    "exclaim": ("+8%", "+6Hz"),
    "question": ("+0%", "+4Hz"),
    "trailing": ("-8%", "-3Hz"),
    "neutral": ("+0%", "+0Hz"),
}


# --- Minimal parser for WoW SavedVariables / generated lookups (a subset of Lua) ---

class LuaParser:
    TOKEN = re.compile(r"""
        \s+|--\[\[.*?\]\]|--[^\n]*|
        (?P<str>"(?:\\.|[^"\\])*")|
        (?P<num>-?(?:0x[0-9a-fA-F]+|\d+\.?\d*(?:[eE][+-]?\d+)?|\.\d+))|
        (?P<name>[A-Za-z_][A-Za-z0-9_.]*)|
        (?P<sym>[{}\[\]=,;])
    """, re.S | re.X)

    def __init__(self, text):
        # Generated data files start with a guard line that isn't a table assignment
        text = re.sub(r"^if not VoiceOver.*$", "", text, flags=re.M)
        self.tokens = []
        pos = 0
        while pos < len(text):
            m = self.TOKEN.match(text, pos)
            if not m:
                raise ValueError(f"Unexpected character at {pos}: {text[pos:pos + 20]!r}")
            pos = m.end()
            for kind in ("str", "num", "name", "sym"):
                if m.group(kind) is not None:
                    self.tokens.append((kind, m.group(kind)))
                    break
        self.i = 0

    def peek(self):
        return self.tokens[self.i] if self.i < len(self.tokens) else (None, None)

    def next(self):
        token = self.peek()
        self.i += 1
        return token

    def expect(self, value):
        kind, v = self.next()
        if v != value:
            raise ValueError(f"Expected {value!r}, got {v!r}")

    @staticmethod
    def unescape(s):
        s = s[1:-1]

        def repl(m):
            e = m.group(1)
            if e.isdigit():
                return chr(int(e))
            return {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", '"': '"', "'": "'", "\n": "\n"}.get(e, e)
        raw = re.sub(r"\\(\d{1,3}|.)", repl, s, flags=re.S)
        # \ddd escapes are bytes; re-decode as UTF-8
        try:
            return raw.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            return raw

    def value(self):
        kind, v = self.next()
        if kind == "str":
            return self.unescape(v)
        if kind == "num":
            return int(v, 16) if v.lower().startswith(("0x", "-0x")) else (float(v) if re.search(r"[.eE]", v) else int(v))
        if kind == "name":
            return {"true": True, "false": False, "nil": None}.get(v, v)
        if v == "{":
            return self.table()
        raise ValueError(f"Unexpected token {v!r}")

    def table(self):
        result, index = {}, 1
        while True:
            kind, v = self.peek()
            if v == "}":
                self.next()
                return result
            if v == "[":
                self.next()
                key = self.value()
                self.expect("]")
                self.expect("=")
                result[key] = self.value()
            elif kind == "name" and self.tokens[self.i + 1][1] == "=":
                self.next()
                self.next()
                result[v] = self.value()
            else:
                result[index] = self.value()
                index += 1
            if self.peek()[1] in (",", ";"):
                self.next()

    def assignments(self):
        result = {}
        while self.i < len(self.tokens):
            kind, name = self.next()
            self.expect("=")
            result[name.split(".")[-1]] = self.value()
        return result


def lua_string(s):
    s = str(s).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "")
    return f'"{s}"'


def lua_key(k):
    return f"[{k}]" if isinstance(k, int) else f"[{lua_string(k)}]"


def lua_value(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        return repr(round(v, 3))
    if isinstance(v, int):
        return str(v)
    if isinstance(v, dict):
        return "{ " + ", ".join(f"{k} = {lua_value(x)}" for k, x in v.items()) + " }"
    return lua_string(v)


def lua_table(module, name, data, indent="\t"):
    lines = [f"{module}.{name} = {{"]
    items = data.items() if isinstance(data, dict) else enumerate(data, 1)
    for key, value in sorted(items, key=lambda kv: str(kv[0])) if isinstance(data, dict) else items:
        prefix = f"{indent}{lua_key(key)} = " if isinstance(data, dict) else indent
        if isinstance(data, dict) and isinstance(value, dict):
            lines.append(f"{prefix}{{")
            for k2 in sorted(value, key=str):
                lines.append(f"{indent}{indent}{lua_key(k2)} = {lua_value(value[k2])},")
            lines.append(f"{indent}}},")
        else:
            lines.append(f"{prefix}{lua_value(value)},")
    lines.append("}")
    return "\n".join(lines)


def read_api_key(name):
    """Key from the environment, or from Tools/.env (one KEY=value per line)."""
    if os.environ.get(name):
        return os.environ[name]
    env_file = TOOLS_DIR / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition("=")
            if key.strip() == name:
                return value.strip().strip("\"'")
    return None


def read_json(path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


# --- Text helpers ---

def clean_text(text):
    text = re.sub(r"\|c[0-9a-fA-F]{8}", "", text)
    text = text.replace("|r", "").replace("|n", " ")
    text = re.sub(r"\|T.*?\|t", "", text)
    text = re.sub(r"\|H.*?\|h(.*?)\|h", r"\1", text)
    text = re.sub(r"<([^>]*)>", r"\1", text)  # <Name> style placeholders
    return re.sub(r"\s+", " ", text).strip()


def add_percent(a, b):
    return f"{int(a.rstrip('%')) + int(b.rstrip('%')):+d}%"


def add_hz(a, b):
    return f"{int(a.rstrip('Hz')) + int(b.rstrip('Hz')):+d}Hz"


def mood_of(sentence):
    s = sentence.rstrip("\"'» ")
    if s.endswith("..."):
        return "trailing"
    if s.endswith("!"):
        return "exclaim"
    if s.endswith("?"):
        return "question"
    return "neutral"


def mood_segments(text):
    """Splits text into consecutive runs of sentences with the same tone."""
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    segments = []
    for sentence in sentences:
        mood = mood_of(sentence)
        if segments and segments[-1][0] == mood:
            segments[-1][1] += " " + sentence
        else:
            segments.append([mood, sentence])
    return segments or [["neutral", text]]


# --- Voice selection ---

def guess_race(entry, config):
    """Race from the NPC's model if known, otherwise a guess from the zone."""
    if entry.get("race"):
        return entry["race"], "model"
    if entry.get("npcType") in ("object", "item") or entry.get("sex") is False:
        return "narrator", "object"
    if entry.get("model"):
        return "monster", "model"  # model loaded, but it's not a playable-race character model
    zones = config.get("zones", {})
    for zone in (entry.get("subzone"), entry.get("zone")):
        if zone and zone in zones:
            return zones[zone], "zone"
    return "default", "none"


def stable_index(seed, count):
    return int(hashlib.md5(str(seed).encode("utf-8")).hexdigest(), 16) % count


def profile_for(config, profiles_key, race, sex, engine):
    profiles = config[profiles_key]
    profile = profiles.get(race, {}).get(sex) or {}
    if profile.get(engine):
        return profile[engine], profile.get("pitch", "+0Hz"), profile.get("rate", "+0%"), profile.get("effect", "")
    fallback = profiles["default"][sex]
    if not fallback.get(engine):
        raise SystemExit(f"voices.json ({profiles_key}) has no {engine} voices for {race}/{sex} or default/{sex}")
    return fallback[engine], "+0Hz", "+0%", ""


def speaker_seed(entry):
    return entry.get("npcID") or entry.get("npc") or ""


# --- Existing recordings (voice matching) ---

class Recordings:
    """Finds recordings of an NPC in the Vanilla data module."""

    def __init__(self):
        self.base = ADDONS_DIR / VANILLA_MODULE
        self.gossip, self.quest_npc, self.files = {}, {}, set()
        generated = self.base / "generated"
        if not generated.exists():
            return
        for name, target in (("gossip_file_lookups.lua", "gossip"), ("questlog_npc_lookups.lua", "quest_npc")):
            path = generated / name
            if path.exists():
                data = LuaParser(path.read_text(encoding="utf-8", errors="replace")).assignments()
                key = "GossipLookupByNPCID" if target == "gossip" else "NPCIDLookupByQuestID"
                setattr(self, target, data.get(key, {}))
        self.npc_quests = {}
        for quest, npc in self.quest_npc.items():
            self.npc_quests.setdefault(npc, []).append(quest)

    def samples(self, npc_id, limit=3):
        paths = []
        for file_hash in (self.gossip.get(npc_id) or {}).values():
            paths.append(self.base / "generated" / "sounds" / "gossip" / f"{file_hash}.mp3")
        for quest in self.npc_quests.get(npc_id, []):
            for event in ("accept", "complete", "progress"):
                for prefix in ("", "m-", "f-"):
                    paths.append(self.base / "generated" / "sounds" / "quests" / f"{prefix}{quest}-{event}.mp3")
        existing = [p for p in paths if p.exists()]
        existing.sort(key=lambda p: p.stat().st_size, reverse=True)  # longer samples first
        return existing[:limit]


def measure_pitch(path):
    """Median fundamental frequency in Hz of a sound file (ffmpeg + numpy), or None."""
    import numpy as np
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", "16000", "-f", "s16le", "-"],
                         capture_output=True, check=True).stdout
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768
    if len(audio) < 4096:
        return None
    frame, hop, rate = 1024, 512, 16000
    threshold = 0.1 * np.max(np.abs(audio))
    pitches = []
    for start in range(0, len(audio) - frame, hop):
        chunk = audio[start:start + frame] * np.hanning(frame)
        if np.sqrt(np.mean(chunk ** 2)) < threshold * 0.3:
            continue
        spectrum = np.fft.rfft(chunk, n=2 * frame)
        corr = np.fft.irfft(spectrum * np.conj(spectrum))[:frame]
        if corr[0] <= 0:
            continue
        corr /= corr[0]
        low, high = rate // 400, rate // 60
        lag = low + int(np.argmax(corr[low:high]))
        if corr[lag] > 0.4:
            pitches.append(rate / lag)
    return float(np.median(pitches)) if len(pitches) >= 10 else None


def can_match_voices():
    try:
        import numpy  # noqa: F401
    except ImportError:
        return False
    return shutil.which("ffmpeg") is not None


# --- Engines ---

EDGE_TIMEOUT = 90       # A stalled request would otherwise hang the whole run
EDGE_ATTEMPTS = 3


async def edge_segment(text, voice, rate, pitch):
    import edge_tts
    data = b""
    async for chunk in edge_tts.Communicate(text, voice, rate=rate, pitch=pitch).stream():
        if chunk["type"] == "audio":
            data += chunk["data"]
    if not data:
        raise RuntimeError("no audio received")
    return data


async def synthesize_edge(text, voice, path, pitch, rate, emotion):
    segments = mood_segments(text) if emotion else [["neutral", text]]
    data = b""
    for mood, segment in segments:
        mood_rate, mood_pitch = MOODS[mood]
        for attempt in range(1, EDGE_ATTEMPTS + 1):
            try:
                data += await asyncio.wait_for(
                    edge_segment(segment, voice, add_percent(rate, mood_rate), add_hz(pitch, mood_pitch)),
                    timeout=EDGE_TIMEOUT)
                break
            except Exception:
                if attempt == EDGE_ATTEMPTS:
                    raise
                await asyncio.sleep(2 * attempt)
    path.write_bytes(data)  # MP3 frames of the same format can simply be concatenated


def synthesize_openai(text, voice, instructions, path, api_key):
    import urllib.request
    body = json.dumps({"model": OPENAI_MODEL, "input": text, "voice": voice,
                       "instructions": instructions, "response_format": "mp3"}).encode("utf-8")
    request = urllib.request.Request(OPENAI_API, data=body, method="POST", headers={
        "Authorization": f"Bearer {api_key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=180) as response:
        path.write_bytes(response.read())


def audio_duration(path, engine):
    """Length in seconds - measured with ffprobe, estimated from the bitrate otherwise."""
    if shutil.which("ffprobe"):
        try:
            out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                  "-of", "csv=p=0", str(path)], capture_output=True, check=True, text=True)
            return round(float(out.stdout.strip()), 3)
        except (subprocess.CalledProcessError, ValueError):
            pass
    return path.stat().st_size / MP3_BYTES_PER_SECOND[engine]


def elevenlabs_request(method, url, api_key, body=None, content_type="application/json"):
    import urllib.request
    request = urllib.request.Request(url, data=body, method=method, headers={"xi-api-key": api_key, "Content-Type": content_type})
    with urllib.request.urlopen(request, timeout=180) as response:
        return response.read()


def synthesize_elevenlabs(text, voice, path, api_key, emotion):
    expressive = emotion and text.count("!") >= 2
    body = json.dumps({
        "text": text,
        "model_id": ELEVENLABS_MODEL,
        "voice_settings": {"stability": 0.3 if expressive else 0.5, "similarity_boost": 0.75,
                           "style": 0.35 if expressive else 0.1},
    }).encode("utf-8")
    path.write_bytes(elevenlabs_request("POST", f"{ELEVENLABS_API}/text-to-speech/{voice}?output_format=mp3_44100_128", api_key, body))


def elevenlabs_clone(name, samples, api_key):
    """Creates an instant voice clone from sample files and returns its voice ID."""
    boundary = "----voiceover" + hashlib.md5(name.encode()).hexdigest()
    parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="name"\r\n\r\n{name}\r\n'.encode()]
    for sample in samples:
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="{sample.name}"\r\n'
                     f"Content-Type: audio/mpeg\r\n\r\n".encode() + sample.read_bytes() + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    response = elevenlabs_request("POST", f"{ELEVENLABS_API}/voices/add", api_key, b"".join(parts),
                                  f"multipart/form-data; boundary={boundary}")
    return json.loads(response)["voice_id"]


# --- Translation ---

class Translator:
    def __init__(self, kind, target_locale, cache_path):
        self.kind, self.target, self.cache_path = kind, target_locale, cache_path
        self.cache = read_json(cache_path, {})
        self.client = None

    def available(self):
        if self.kind == "deepl":
            return bool(os.environ.get("DEEPL_API_KEY"))
        try:
            import anthropic  # noqa: F401
        except ImportError:
            return False
        return True

    def translate(self, text, source_locale):
        if text in self.cache:
            return self.cache[text]
        result = self._deepl(text) if self.kind == "deepl" else self._claude(text, source_locale)
        self.cache[text] = result
        write_json(self.cache_path, self.cache)
        return result

    def _deepl(self, text):
        import urllib.parse
        import urllib.request
        key = os.environ["DEEPL_API_KEY"]
        host = "api-free.deepl.com" if key.endswith(":fx") else "api.deepl.com"
        body = urllib.parse.urlencode({"text": text, "target_lang": self.target[:2].upper()}).encode()
        request = urllib.request.Request(f"https://{host}/v2/translate", data=body,
                                         headers={"Authorization": f"DeepL-Auth-Key {key}"})
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read())["translations"][0]["text"]

    def _claude(self, text, source_locale):
        import anthropic
        if self.client is None:
            self.client = anthropic.Anthropic()
        source = LANGUAGE_NAMES.get(source_locale, "English")
        target = LANGUAGE_NAMES.get(self.target, "German")
        response = self.client.beta.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=4000,
            betas=["server-side-fallback-2026-07-01"],
            extra_body={"fallbacks": "default"},
            output_config={"effort": "low"},
            system=(f"You translate World of Warcraft quest and NPC dialogue from {source} to {target} for voice acting. "
                    f"Use the official {target} World of Warcraft names for places, creatures, items and characters. "
                    "Keep the speaker's tone and form of address. Reply with the translation only."),
            messages=[{"role": "user", "content": text}],
        )
        if response.stop_reason == "refusal":
            raise RuntimeError("translation was declined")
        return "".join(block.text for block in response.content if block.type == "text").strip()


# --- In-game data ---

def find_saved_variables(account):
    return sorted(GAME_DIR.glob(f"WTF/Account/{account or '*'}/SavedVariables/AI_VoiceOver_Continued.lua"))


def load_missing_lines(paths):
    lines = {}
    for path in paths:
        data = LuaParser(path.read_text(encoding="utf-8", errors="replace")).assignments()
        lines.update((data.get("VoiceOverDB") or {}).get("global", {}).get("MissingLines", {}))
    return lines


def file_name_for(entry):
    if entry["event"] == "gossip":
        seed = f"{entry.get('npcID') or entry.get('npc')}:{entry['text']}"
        return "gen-" + hashlib.md5(seed.encode("utf-8")).hexdigest()
    # Texts that differ by player gender get the m-/f- prefix the addon looks for
    gender = entry.get("gender")
    return f"{gender}-{entry['questID']}-{entry['event']}" if gender else f"{entry['questID']}-{entry['event']}"


def spoken_text(entry):
    text = clean_text(entry["text"])
    if entry["event"] == "accept" and entry.get("title"):
        text = f"{clean_text(entry['title'])}. {text}"
    return text


# --- Module output ---

def write_module(module_dir, module_name, language, manifest, previews):
    locale, _, priority, _, _ = LANGUAGES[language]
    (module_dir / "generated").mkdir(parents=True, exist_ok=True)

    toc = f"""## Interface: 100000, {INTERFACE}
## Title: VoiceOver Data - Forever ({LANGUAGE_NAMES[locale]}, generated)
## Notes: Voiceovers generated by Tools\\generate_voices.py from lines collected in-game.
## Version: {len(manifest)}
## LoadOnDemand: 1
## X-Part-Of: VoiceOver
## X-VoiceOver-DataModule-Version: 1
## X-VoiceOver-DataModule-Priority: {priority}
## X-VoiceOver-DataModule-Language: {locale}
## X-VoiceOver-DataModule-Maps: 0, 1, 530, 571

Module.lua
generated\\lookups.lua
"""
    (module_dir / f"{module_name}.toc").write_text(toc, encoding="utf-8")
    (module_dir / "Module.lua").write_text(f"""if not VoiceOver or not VoiceOver.DataModules then return end

{module_name} = {{}}

function {module_name}:GetSoundPath(fileName, event)
    return format([[generated\\sounds\\%s.mp3]], fileName)
end

VoiceOver.DataModules:Register("{module_name}", {module_name})
""", encoding="utf-8")

    lengths, by_npc_id, by_object_id, by_npc_name, by_object_name = {}, {}, {}, {}, {}
    npc_by_quest, npc_names = {}, {}
    for item in manifest.values():
        entry, name = item["entry"], item["file"]
        lengths[name] = item["length"]
        npc_id, npc_type, npc = entry.get("npcID"), entry.get("npcType"), entry.get("npc")
        if entry["event"] == "gossip":
            text = entry["text"].replace('"', "'")
            if npc_id and npc_type == "object":
                by_object_id.setdefault(npc_id, {})[text] = name
            elif npc_id:
                by_npc_id.setdefault(npc_id, {})[text] = name
            elif npc:
                target = by_object_name if npc_type == "object" else by_npc_name
                target.setdefault(npc.replace('"', "'"), {})[text] = name
        elif entry["event"] == "accept" and npc_id and npc_type == "creature":
            npc_by_quest[entry["questID"]] = npc_id
        if npc_id and npc and npc_type == "creature":
            npc_names[npc_id] = npc
    for preview in previews:
        lengths[preview["file"]] = preview["length"]

    parts = ["if not VoiceOver or not VoiceOver.DataModules then return end"]
    parts.append(lua_table(module_name, "SoundLengthLookupByFileName", lengths))
    parts.append(lua_table(module_name, "GossipLookupByNPCID", by_npc_id))
    parts.append(lua_table(module_name, "GossipLookupByObjectID", by_object_id))
    parts.append(lua_table(module_name, "GossipLookupByNPCName", by_npc_name))
    parts.append(lua_table(module_name, "GossipLookupByObjectName", by_object_name))
    parts.append(lua_table(module_name, "NPCIDLookupByQuestID", npc_by_quest))
    parts.append(lua_table(module_name, "NPCNameLookupByNPCID", npc_names))
    parts.append(lua_table(module_name, "VoicePreviews", [
        {k: p[k] for k in ("race", "sex", "voice", "file", "length", "text")} for p in previews]))
    (module_dir / "generated" / "lookups.lua").write_text("\n".join(parts) + "\n", encoding="utf-8")


# --- Main ---

async def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--account", help="WoW account folder name (default: all)")
    parser.add_argument("--import", dest="imports", type=Path, nargs="+", default=[],
                        help="Also generate lines from import_database.py output files")
    parser.add_argument("--language", choices=sorted(LANGUAGES), default="en", help="Language of the generated voiceovers")
    parser.add_argument("--engine", choices=("edge", "openai", "elevenlabs"), default="edge",
                        help="edge = free Microsoft voices, openai = gpt-4o-mini-tts (needs OPENAI_API_KEY), "
                             "elevenlabs = best quality (needs ELEVENLABS_API_KEY)")
    parser.add_argument("--voice-samples", action="store_true",
                        help="Only render every voice variant of every race into cache/voice_samples_<locale>")
    parser.add_argument("--translator", choices=("claude", "deepl"), default="claude",
                        help="Used for lines in another language than --language")
    parser.add_argument("--clone", action="store_true", help="ElevenLabs: clone the voices of NPCs that have recordings")
    parser.add_argument("--no-match", action="store_true", help="Don't match voices to existing recordings")
    parser.add_argument("--no-emotion", action="store_true", help="Speak every sentence in the same tone")
    parser.add_argument("--previews", action="store_true", help="(Re)generate voice previews for /vo voices")
    parser.add_argument("--rate", default="+0%", help='Extra speech rate for edge-tts, e.g. "+10%%"')
    parser.add_argument("--parallel", type=int, default=4, help="Concurrent requests")
    parser.add_argument("--limit", type=int, default=0, help="Generate at most N lines")
    parser.add_argument("--regenerate", action="store_true", help="Regenerate lines that already have a voiceover")
    parser.add_argument("--dry-run", action="store_true", help="Only list what would be generated")
    parser.add_argument("--yes", action="store_true", help="Don't ask before using paid services")
    args = parser.parse_args()

    config = json.loads(VOICES_FILE.read_text(encoding="utf-8"))
    locale, suffix, _, profiles_key, preview_text = LANGUAGES[args.language]
    module_name = BASE_MODULE_NAME + suffix
    module_dir = ADDONS_DIR / module_name
    sounds_dir = module_dir / "generated" / "sounds"
    manifest_path = module_dir / "manifest.json"
    manifest = read_json(manifest_path, {})
    previews = read_json(module_dir / "previews.json", [])
    cache_dir = TOOLS_DIR / "cache"

    paths = find_saved_variables(args.account)
    if not paths and not args.imports and not args.voice_samples:
        sys.exit("No AI_VoiceOver_Continued SavedVariables found. Play with the addon, then /reload or log out.")
    missing = load_missing_lines(paths)
    for path in args.imports:
        missing.update(read_json(path, {}))

    # Voice matching to existing recordings
    matching = args.engine == "edge" and not args.no_match and can_match_voices()
    recordings = Recordings() if (matching or args.clone) else None
    npc_pitch = read_json(cache_dir / "npc_pitch.json", {})
    voice_pitch = read_json(cache_dir / "voice_pitch.json", {})
    clones = read_json(cache_dir / "elevenlabs_clones.json", {})

    async def base_pitch(voice):
        if voice not in voice_pitch:
            sample = cache_dir / "voice_samples" / f"{voice}.mp3"
            sample.parent.mkdir(parents=True, exist_ok=True)
            await synthesize_edge(preview_text if voice.startswith(locale[:2]) else LANGUAGES["en"][4],
                                  voice, sample, "+0Hz", "+0%", False)  # measured without effects
            voice_pitch[voice] = measure_pitch(sample)
            write_json(cache_dir / "voice_pitch.json", voice_pitch)
        return voice_pitch[voice]

    def measure_npc(npc_id):
        values = [p for p in (measure_pitch(s) for s in recordings.samples(npc_id)) if p]
        return str(npc_id), (sorted(values)[len(values) // 2] if values else None)

    async def measure_npcs(npc_ids):
        """Measures the recordings of many NPCs at once - ffmpeg is the slow part here."""
        todo_ids = [n for n in dict.fromkeys(npc_ids) if str(n) not in npc_pitch]
        if not todo_ids:
            return
        print(f"Measuring the recordings of {len(todo_ids)} NPCs to match their voices...")
        semaphore = asyncio.Semaphore(8)

        async def run(npc_id):
            async with semaphore:
                key, value = await asyncio.to_thread(measure_npc, npc_id)
                npc_pitch[key] = value
        await asyncio.gather(*(run(n) for n in todo_ids))
        write_json(cache_dir / "npc_pitch.json", npc_pitch)

    def target_pitch(npc_id):
        key = str(npc_id)
        if key not in npc_pitch:
            key, value = measure_npc(npc_id)
            npc_pitch[key] = value
            write_json(cache_dir / "npc_pitch.json", npc_pitch)
        return npc_pitch[key]

    async def choose(entry):
        race, source = guess_race(entry, config)
        npc_id = entry.get("npcID") if entry.get("npcType") == "creature" else None
        sex = "female" if entry.get("sex") == 3 else "male"
        if entry.get("sex") is None and matching and npc_id and recordings.samples(npc_id, 1):
            # Imported lines don't know the NPC's sex - take it from the pitch of its recordings
            measured = target_pitch(npc_id)
            if measured:
                sex = "female" if measured > 165 else "male"
        voices, pitch, rate, effect = profile_for(config, profiles_key, race, sex, args.engine)
        choice = voices[stable_index(speaker_seed(entry), len(voices))]
        voice, style = (choice["voice"], choice.get("style", "")) if isinstance(choice, dict) else (choice, "")
        if args.engine == "elevenlabs" and args.clone and npc_id and str(npc_id) in clones:
            voice, source = clones[str(npc_id)], "clone"
        elif matching and npc_id and recordings.samples(npc_id, 1):
            wanted = target_pitch(npc_id)
            if wanted:
                # Pick the voice of this race whose natural pitch is closest, then shift the rest
                bases = {v: await base_pitch(v) for v in voices}
                bases = {v: b for v, b in bases.items() if b}
                if bases:
                    voice = min(bases, key=lambda v: abs(bases[v] - wanted))
                    shift = max(-40, min(40, round(wanted - bases[voice])))
                    pitch, source = f"{shift:+d}Hz", "match"
        if args.engine == "edge":
            rate = add_percent(rate, args.rate)
        return race, source, {"engine": args.engine, "voice": voice, "style": style, "pitch": pitch, "rate": rate,
                              "effect": effect, "emotion": not args.no_emotion, "language": args.language}

    key_name = "ELEVENLABS_API_KEY" if args.engine == "elevenlabs" else "OPENAI_API_KEY"
    api_key = read_api_key(key_name)
    if args.engine != "edge" and not api_key and not args.dry_run:
        sys.exit(f"No {key_name} found. Put it in Tools/.env as {key_name}=... or set it as an environment variable.")

    # Voice clones (ElevenLabs) for NPCs with recordings
    if args.clone and args.engine == "elevenlabs" and not args.dry_run:
        wanted = {e["npcID"] for e in missing.values() if isinstance(e, dict) and e.get("npcType") == "creature"
                  and e.get("npcID") and str(e["npcID"]) not in clones}
        wanted = [n for n in wanted if recordings.samples(n, 1)]
        if wanted:
            print(f"Creating {len(wanted)} ElevenLabs voice clones (your plan limits how many custom voices you can have).")
            for npc_id in wanted:
                name = next((e.get("npc") for e in missing.values() if isinstance(e, dict) and e.get("npcID") == npc_id), str(npc_id))
                try:
                    clones[str(npc_id)] = elevenlabs_clone(f"WoW {name} {npc_id}", recordings.samples(npc_id), api_key)
                    write_json(cache_dir / "elevenlabs_clones.json", clones)
                    print(f"  cloned {name}")
                except Exception as error:
                    print(f"  clone failed for {name}: {error} - using the race voice instead")
                    break

    # Work list
    todo, skipped, untranslatable = [], 0, 0
    translator = Translator(args.translator, locale, cache_dir / f"translations_{locale}.json")
    # One entry per sound file; a line collected in the target language beats one that needs translation
    by_file = {}
    for entry in missing.values():
        if not isinstance(entry, dict) or not entry.get("text"):
            continue
        if entry.get("event") != "gossip" and not entry.get("questID"):
            skipped += 1  # quest without an ID cannot be looked up by the addon
            continue
        name = file_name_for(entry)
        if name not in by_file or (entry.get("locale") or "enUS") == locale:
            by_file[name] = entry
    candidates = []
    for name, entry in by_file.items():
        entry_locale = entry.get("locale") or "enUS"
        if entry_locale != locale and not translator.available():
            untranslatable += 1
            continue
        candidates.append((name, entry, entry_locale))
    # With a limit, only look at lines that have no voiceover yet - picking a voice can mean
    # measuring the pitch of existing recordings, which is far too slow to do for every line
    if args.limit:
        candidates = [c for c in candidates if c[0] not in manifest][:args.limit]

    if matching:
        await measure_npcs([e.get("npcID") for _, e, _ in candidates
                            if e.get("npcType") == "creature" and e.get("npcID")])

    for name, entry, entry_locale in candidates:
        race, source, settings = await choose(entry)
        known = manifest.get(name)
        # Skip lines that already exist with the same voice; a changed voice (better race info,
        # edited voices.json, other engine) is regenerated automatically
        if known and not args.regenerate and all(known.get(k) == v for k, v in settings.items()):
            continue
        todo.append((entry, race, source, settings, entry_locale))

    notes = []
    if skipped:
        notes.append(f"{skipped} skipped (quest without ID)")
    if untranslatable:
        notes.append(f"{untranslatable} in another language ({args.translator} not available)")
    print(f"{len(missing)} collected lines, {len(manifest)} generated in {module_name}, {len(todo)} to generate"
          + (", " + ", ".join(notes) if notes else ""))
    if args.dry_run:
        for entry, race, source, settings, entry_locale in todo:
            label = f"{entry.get('npc') or '?'}: {file_name_for(entry)}"
            print(f"  {label[:50]:50} {race:9} ({source:6}) {settings['voice']} {settings['pitch']} {settings['rate']}"
                  + (" [translate]" if entry_locale != locale else ""))
        return

    if args.engine == "edge":
        try:
            import edge_tts  # noqa: F401
        except ImportError:
            sys.exit("edge-tts is missing: pip install edge-tts")
    if todo and args.engine == "elevenlabs":
        characters = sum(len(spoken_text(e)) for e, *_ in todo)
        print(f"ElevenLabs will be charged for about {characters} characters.")
        if not args.yes and input("Continue? [y/N] ").strip().lower() not in ("y", "yes", "j", "ja"):
            return

    sounds_dir.mkdir(parents=True, exist_ok=True)
    semaphore = asyncio.Semaphore(max(1, args.parallel))
    done = 0

    async def speak(text, settings, path):
        if settings["engine"] == "openai":
            await asyncio.to_thread(synthesize_openai, text, settings["voice"], settings.get("style", ""), path, api_key)
        elif settings["engine"] == "edge":
            await synthesize_edge(text, settings["voice"], path, settings["pitch"], settings["rate"], settings["emotion"])
        else:
            await asyncio.to_thread(synthesize_elevenlabs, text, settings["voice"], path, api_key, settings["emotion"])
        if settings.get("effect"):
            await asyncio.to_thread(apply_effect, path, settings["effect"])

    async def generate(entry, race, source, settings, entry_locale):
        nonlocal done
        name = file_name_for(entry)
        path = sounds_dir / f"{name}.mp3"
        async with semaphore:
            try:
                text = spoken_text(entry)
                if entry_locale != locale:
                    text = await asyncio.to_thread(translator.translate, text, entry_locale)
                await speak(text, settings, path)
            except Exception as error:  # keep going, retry on the next run
                print(f"  failed {name}: {error}")
                return
        manifest[name] = {"file": name, "length": audio_duration(path, settings["engine"]),
                          "race": race, "source": source, **settings, "entry": entry}
        done += 1
        print(f"  [{done}/{len(todo)}] {entry.get('npc') or name} ({race}/{source}, {settings['voice']})")

    # Render every voice variant of every race so they can be compared before a big run
    if args.voice_samples:
        sample_dir = cache_dir / f"voice_samples_{locale}"
        sample_dir.mkdir(parents=True, exist_ok=True)
        jobs = []
        for race, sexes in config[profiles_key].items():
            for sex in ("male", "female"):
                if sexes and sex not in sexes:
                    continue
                voices, pitch, rate, effect = profile_for(config, profiles_key, race, sex, args.engine)
                for index, choice in enumerate(voices, 1):
                    voice, style = (choice["voice"], choice.get("style", "")) if isinstance(choice, dict) else (choice, "")
                    settings = {"engine": args.engine, "voice": voice, "style": style, "pitch": pitch,
                                "rate": rate, "effect": effect, "emotion": True}
                    jobs.append((sample_dir / f"{race}-{sex}-{index:02d}-{voice}.mp3", settings))
        print(f"Rendering {len(jobs)} voice samples with {args.engine} ...")

        async def render(path, settings):
            async with semaphore:
                try:
                    await speak(preview_text, settings, path)
                except Exception as error:
                    print(f"  failed {path.name}: {error}")
        await asyncio.gather(*(render(*job) for job in jobs))
        print(f"Done: samples in {sample_dir}")
        return

    await asyncio.gather(*(generate(*item) for item in todo))
    write_json(manifest_path, manifest)

    # Voice previews for /vo voices: one sample per race and sex
    if args.engine == "edge" or args.previews:
        known = {(p["race"], p["sex"]): p for p in previews}
        new_previews = []
        for race, sexes in config[profiles_key].items():
            for sex in ("male", "female"):
                if sexes and sex not in sexes:  # e.g. vrykul has no female profile
                    continue
                voices, pitch, rate, effect = profile_for(config, profiles_key, race, sex, args.engine)
                choice = voices[0]
                voice, style = (choice["voice"], choice.get("style", "")) if isinstance(choice, dict) else (choice, "")
                old = known.get((race, sex))
                if (old and old["voice"] == voice and old.get("pitch") == pitch and old.get("rate") == rate
                        and old.get("effect") == effect and not args.previews):
                    new_previews.append(old)
                    continue
                name = f"preview-{race}-{sex}"
                path = sounds_dir / f"{name}.mp3"
                try:
                    await speak(preview_text, {"engine": args.engine, "voice": voice, "style": style, "pitch": pitch,
                                               "rate": rate, "effect": effect, "emotion": True}, path)
                except Exception as error:
                    print(f"  preview failed for {race}/{sex}: {error}")
                    continue
                new_previews.append({"race": race, "sex": sex, "voice": voice, "pitch": pitch, "rate": rate,
                                     "effect": effect, "file": name, "text": preview_text,
                                     "length": audio_duration(path, args.engine)})
        previews = new_previews
        write_json(module_dir / "previews.json", previews)

    write_module(module_dir, module_name, args.language, manifest, previews)
    print(f"Done: {done} voiceovers written to {module_dir}. Restart the game to hear them (/vo voices plays samples).")


if __name__ == "__main__":
    asyncio.run(main())
