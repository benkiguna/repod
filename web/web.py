#!/usr/bin/env python3
"""A local web page for managing the iPod library: browse it, add songs, find missing covers.

    python3 web/web.py --developer          then open http://127.0.0.1:8765

The order of things is deliberate. Opening the page and loading the library only reads the iPod.
Nothing goes to the internet until you press "Find covers online", and nothing is written to the
iPod until you press a Write / Add button and confirm. Writes go through `ipodctl`, with a backup
first, exactly as on the command line.

It listens on this Mac only.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

import enrich
import meta
import tidy

PAGE = Path(__file__).with_name("index.html")
STYLE = Path(__file__).with_name("style.css")
ASSETS = Path(__file__).with_name("assets")
MISSING = enrich.HOME / "missing.json"
BACKUPS = Path(os.environ.get("REPOD_BACKUPS") or Path.home() / "Library/Application Support/RePod/Backups")
KEEP_BACKUPS = 10  # the newest ten stay, and any that are starred; the rest go after a write
BACKUP_ID = re.compile(r"^[0-9A-Za-z-]{1,40}$")
ART = enrich.HOME / "art"  # the covers already on the iPod, exported on every load
CHOSEN = enrich.HOME / "chosen"  # an image picked for an album, until it is written
CONVERTED = enrich.HOME / "playing"  # lossless songs a browser can play, kept for a while (see `playable`)
CONVERTED_LIMIT = 2_000_000_000
AUDIO_KINDS = {".flac": "audio/flac", ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".m4b": "audio/mp4", ".m4p": "audio/mp4", ".aac": "audio/aac", ".wav": "audio/wav", ".aif": "audio/aiff", ".aiff": "audio/aiff"}
IMAGE_KINDS = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".heic": "image/heic", ".tif": "image/tiff", ".tiff": "image/tiff", ".webp": "image/webp"}
WRITES = {"write", "cover", "edit", "remove", "add", "regroup", "metawrite", "restore", "rebuild", "convert", "sortfix"}  # the jobs that change the iPod
STOPPABLE = {"copy", "copycheck", "artistscan", "lookup", "metascan", "addscan"}  # these only read, so they can stop between two items; a write never does
NEEDS_IPOD = {"/api/preview", "/api/write", "/api/add-preview", "/api/add", "/api/add-lookup", "/api/backup-create", "/api/backup-clean", "/api/rebuild", "/api/health", "/api/audio-fix", "/api/fix-sort", "/api/backup-verify", "/api/backup-restore",
              "/api/meta-preview", "/api/meta-apply", "/api/cover-set", "/api/regroup-preview", "/api/regroup", "/api/songs-retag", "/api/songs-edit",
              "/api/songs-remove", "/api/copy-songs", "/api/eject"}
KEPT = enrich.HOME / "kept-both.json"
STARRED = enrich.HOME / "starred-backups.json"
ADD_REVIEW = enrich.HOME / "add-review.json"  # what MusicBrainz said about songs not yet on the iPod
SONG_COPY = enrich.HOME / "song-copy.json"  # where the last copy of the songs is; the copy describes itself in its own RESTORE file
RESTORE_FILE = "restore.json"
HEALTH = enrich.HOME / "health.json"  # the last check of the iPod
SETTINGS = enrich.HOME / "settings.json"
CHUNK = 1 << 20


BUSY = "Another job is still running. Try again when it has finished."
PLAIN = (  # what ipodctl says, and what to tell someone about it
    (re.compile(r"no iPod found|no iPod is connected", re.I), "The iPod isn't connected. Connect it, then press Reload."),
    (re.compile(r"Music is running", re.I), "Music is open, and it can overwrite the iPod's library. Quit Music and try again. Nothing was written."),
    (re.compile(r"connected iPods: \d+|more than one iPod", re.I), "More than one iPod is connected. Unplug the others, or start the page with --volume to choose one."),
    (re.compile(r"read-only", re.I), "This iPod can only be read, so nothing was written. Health says why."),
    (re.compile(r"operation not permitted|not permitted to", re.I), "macOS isn't letting this app read the iPod. In System Settings, open Privacy & Security, then Files & Folders, and turn on Removable Volumes for RePod. Then press Reload."),
    (re.compile(r"no audio files found", re.I), "There are no songs there that can be added."),
    (re.compile(r"(missing|does not exist|not found).*iTunesDB", re.I), "This iPod has no library. If it was just reset, Restore everything puts it back."),
    (re.compile(r"cannot load the image .*", re.I), "the image could not be read"),
)


def friendly(text: str) -> str:
    """A message from `ipodctl` as a sentence that says what to do (DESIGN.md > Writing)."""
    text = re.sub(r"^error:\s*", "", (text or "").strip())
    for pattern, plain in PLAIN:
        if pattern.search(text):
            return pattern.sub(plain, text) if plain[0].islower() else plain
    return text


def last_line(run) -> str:
    """The message of a failed `ipodctl`, in plain words."""
    lines = (run.stderr or run.stdout).strip().splitlines()
    return friendly(lines[-1]) if lines else "ipodctl failed"


COUNTED = {  # how many: where `ipodctl` says it, and how to say it to the user
    "add": (r"Added tracks: (\d+)", "Added {} to the iPod", "song", "songs"),
    "remove": (r"tracks\s+(\d+) of", "Deleted {} from the iPod", "song", "songs"),
    "edit": (r"songs to change\s+(\d+)", "Changed {}", "song", "songs"),
    "metawrite": (r"songs to change\s+(\d+)", "Changed {}", "song", "songs"),
    "write": (r"covers to write\s+(\d+)", "Wrote {} to the iPod", "cover", "covers"),
    "regroup": (r"albums to rebuild\s+(\d+)", "Put {} back in order", "album", "albums"),
    "backup-clean": (r"Removed (\d+) older", "Removed {}", "older backup", "older backups"),
    "rebuild": (r"Restored (\d+) songs", "Restored {} to the iPod", "song", "songs"),
    "copy": (r"Saved (\d+) songs", "Saved {} and the library with them", "song", "songs"),
    "convert": (r"To transcode: (\d+)", "Converted {}", "song", "songs"),
    "sortfix": (r"^tracks\s+(\d+)", "Repaired {}", "song", "songs"),
    "copycheck": (r"All (\d+) files match", "Checked {}: every one matches", "file", "files"),
}


def summary(kind: str, lines: list):
    """A finished write in one line with its number, or None to say it the usual way."""
    if kind not in COUNTED:
        return None
    pattern, sentence, one, many = COUNTED[kind]
    found = next((m for line in lines if (m := re.search(pattern, line))), None)
    if not found:
        return None
    count = int(found[1])
    return sentence.format(f"{count:,} {one if count == 1 else many}")


def outcome(kind: str, ok: bool, log: list, stopped: bool = False) -> dict:
    """What came of a job, for the page. The exit code alone is not the answer: `ipodctl` exits 0 when it skipped
    every entry, and when a write went through but part of it did not."""
    lines = [line.strip() for line in log]
    if stopped and not ok:
        return {"result": "stopped", "reason": None}
    if not ok:
        errors = [line[len("error: "):] for line in lines if line.startswith("error: ")]
        return {"result": "failed", "reason": friendly(errors[-1] if errors else next((line for line in reversed(lines) if line), ""))}
    if kind in WRITES | {"copy", "copycheck"}:
        skipped = [line[len("- SKIPPED "):] for line in lines if line.startswith("- SKIPPED ")]
        warned = [line[len("WARNING: "):] for line in lines if line.startswith("WARNING: ")]
        if "Nothing to write." in lines:
            return {"result": "nothing", "reason": friendly(skipped[0]) if skipped else None}
        if skipped or warned:
            return {"result": "partial", "reason": friendly((skipped + warned)[0])}
    return {"result": "done", "reason": None, "summary": summary(kind, lines)}


class Job:
    """One background task at a time (a lookup, an add, a write) with its log. A read of the iPod or an
    eject claims the same place, so nothing can start underneath them, from this tab or another."""

    def __init__(self):
        self.lock = threading.Lock()
        self.running = False
        self.reading = False
        self.kind = ""
        self.log = []
        self.ok = None
        self.outcome = None
        self.cancel = threading.Event()  # set to ask a stoppable job to stop; only the copy looks at it
        self.after_write = None  # housekeeping once a write has gone through; it can't undo the write by failing

    def claim(self) -> bool:
        """The iPod for a moment, outside a job. False when a job or another read has it."""
        with self.lock:
            if self.running or self.reading:
                return False
            self.reading = True
            return True

    def release(self) -> None:
        self.reading = False

    def start(self, kind, target, then=None) -> bool:
        with self.lock:
            if self.running or self.reading:
                return False
            self.running, self.kind, self.log, self.ok, self.outcome = True, kind, [], None, None
            self.cancel.clear()
        threading.Thread(target=self._run, args=(target, then), daemon=True).start()
        return True

    def _run(self, target, then):
        try:
            self.ok = bool(target(self.log.append))
        except enrich.ScanError as error:
            self.log.append(f"error: {error}")
            self.ok = False
        except Exception as error:  # the page shows it; the server keeps running
            self.log.append(f"error: {type(error).__name__}: {error}")
            self.ok = False
        self.outcome = outcome(self.kind, self.ok, self.log, self.cancel.is_set())
        if then and self.ok:  # still running: the page must not see "done" with the library from before the write
            try:
                then()
            except Exception as error:
                self.log.append(f"error: {type(error).__name__}: {error}")
        if self.after_write and self.ok and self.kind in WRITES | {"backup"}:
            try:
                self.after_write(self.log.append)
            except Exception as error:
                self.log.append(f"Older backups were left alone: {error}")
        self.running = False

    def clear(self):
        """Forget a finished job, so a dismissed message stays gone after a reload."""
        with self.lock:
            if not self.running:
                self.kind, self.log, self.ok, self.outcome = "", [], None, None
                self.cancel.clear()

    def snapshot(self):
        return {"running": self.running, "kind": self.kind, "ok": self.ok, "outcome": self.outcome, "log": self.log[-400:],
                "cancelled": self.cancel.is_set(), "stoppable": self.kind in STOPPABLE}


def folded(name: str) -> str:
    """An album name for comparing: "Side-A" and "side a" are the same album."""
    return re.sub(r"[\W_]+", " ", name.casefold()).strip()


def likely_artist(songs: list) -> str:
    """The name most of the songs share, whole or as one of several credited ("A, B & C")."""
    counts, spelling = {}, {}
    for s in songs:
        names = {n.strip() for n in re.split(r",|&|\bfeat\.?|\bft\.?", s["artist"]) if n.strip()} | {s["albumArtist"]} - {""}
        for name in names:
            key = name.casefold()
            counts[key] = counts.get(key, 0) + 1
            spelling.setdefault(key, name)
    return spelling[max(counts, key=counts.get)] if counts else ""


def find_splits(songs: list) -> list:
    """Albums the iPod shows as several: one name (give or take spelling) under more than one artist."""
    names = {}
    for s in songs:
        if s["album"]:
            names.setdefault(folded(s["album"]), []).append(s)
    splits = []
    for group in names.values():
        parts = {}
        for s in group:
            parts[(s["album"], s["albumArtist"])] = parts.get((s["album"], s["albumArtist"]), 0) + 1
        if len(parts) > 1:
            spellings = [s["album"] for s in group]
            splits.append({"album": max(set(spellings), key=spellings.count), "artist": likely_artist(group),
                           "parts": [{"album": a, "artist": o, "songs": n} for (a, o), n in sorted(parts.items(), key=lambda p: -p[1])],
                           "ids": [s["id"] for s in group]})
    return sorted(splits, key=lambda x: x["album"].casefold())


def find_doubles(songs: list) -> list:
    """Albums the iPod may show twice in Cover Flow: an album artist that is on none of the songs.
    The rule of `albumsDuplicatedByArtistMismatch`, measured on one album, so the page only offers."""
    albums = {}
    for s in songs:
        if s["album"] and s["albumartist"]:
            albums.setdefault((s["album"], s["albumartist"]), []).append(s)
    found = []
    for (album, owner), group in albums.items():
        artists = {s["artist"].casefold() for s in group if s["artist"]}
        if artists and not any(s["compilation"] for s in group) \
                and not any(a in owner.casefold() or owner.casefold() in a for a in artists):
            found.append({"album": album, "artist": owner, "ids": [s["id"] for s in group]})
    return sorted(found, key=lambda x: x["album"].casefold())


def load_kept() -> set:
    """Sets of copies the user said to leave alone. Kept on this Mac, by the songs in them, so a new copy asks again."""
    try:
        return set(json.loads(KEPT.read_text()))
    except (OSError, ValueError):
        return set()


def load_starred() -> set:
    try:
        return set(json.loads(STARRED.read_text()))
    except (OSError, ValueError):
        return set()


def save_starred(ids: set) -> None:
    enrich.HOME.mkdir(parents=True, exist_ok=True)
    STARRED.write_text(json.dumps(sorted(ids)))


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        for block in iter(lambda: file.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def copy_checked(source: Path, out: Path) -> str:
    """A file copied and read back. Its name appears only once it is whole and right, so a stop or a pulled cable leaves no half song."""
    part = out.with_name(f".{out.name}.part")
    digest = hashlib.sha256()
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(source, "rb") as read, open(part, "wb") as write:
            for block in iter(lambda: read.read(CHUNK), b""):
                digest.update(block)
                write.write(block)
            write.flush()
            os.fsync(write.fileno())
        if sha256_of(part) != digest.hexdigest():
            raise enrich.ScanError(f"{out.name} did not read back the same. Nothing is wrong with the original; try again.")
        part.replace(out)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    return digest.hexdigest()


def load_copy():
    """What the last copy of the songs holds, or None: `folder`, and the copy's own description."""
    try:
        folder = Path(json.loads(SONG_COPY.read_text())["folder"])
        return {"folder": folder, **json.loads((folder / RESTORE_FILE).read_text())}
    except (OSError, ValueError, KeyError):
        return None


def copy_summary():
    copy = load_copy()
    return copy and {"folder": str(copy["folder"]), "made": copy["made"], "backup": copy.get("backup"), "complete": copy.get("complete", False),
                     "songs": len(copy["files"]), "bytes": sum(f["size"] for f in copy["files"])}


def parse_diagnose(text: str) -> dict:
    """`ipodctl diagnose` as data: each check, and the sentence that sums them up."""
    checks, after = [], None
    lines = text.splitlines()
    for number, line in enumerate(lines):
        found = re.match(r"^  (OK|WARN|FAIL|INFO)\s+(\w+)\s+(.*?)(?:\s+\[(required|advisory)\S*)?$", line)
        if found:
            checks.append({"status": found[1], "id": found[2], "detail": found[3], "weight": found[4] or "advisory"})
            after = number
    verdict = next((line for line in lines[(after or 0) + 1:] if line and not line.startswith(" ") and line not in ("device", "library", "files", "artwork", "disk", "measurements")), "")
    return {"checks": checks, "verdict": verdict}


def parse_audio(text: str) -> dict:
    """`ipodctl audio` as data: how many play, and the songs that don't."""
    numbers = {k: int(v) for k, v in re.findall(r"^  (the iPod plays|the iPod does not play|unreadable)\s+(\d+)$", text, re.M)}
    songs = []
    for head, detail in re.findall(r"^  id=(\d+  .*)\n      (.*)$", text, re.M):
        number, _, name = head.partition("  ")
        album, _, title = name.partition(" / ")
        kind, _, why = detail.partition(": ")
        reason, _, target = why.partition(" -> ")
        songs.append({"id": int(number), "album": album, "title": title, "format": kind, "reason": reason.replace(" (cannot be fixed)", ""), "target": target, "fixable": bool(target)})
    return {"plays": numbers.get("the iPod plays", 0), "unreadable": numbers.get("unreadable", 0), "songs": songs}


def parse_sort(text: str) -> list:
    """The albums `ipodctl fix-sort` would repair."""
    return re.findall(r"\n\n  ([^\n]+)\n      ", text)


def issues(health: dict) -> int:
    return len(health.get("audio", {}).get("songs", [])) + sum(c["status"] in ("WARN", "FAIL") for c in health.get("checks", []))


def load_settings() -> dict:
    try:
        return json.loads(SETTINGS.read_text())
    except (OSError, ValueError):
        return {}


def load_health() -> dict:
    try:
        return json.loads(HEALTH.read_text())
    except (OSError, ValueError):
        return {}


def read_drafts(lines: list) -> list:
    """The songs `ipodctl add` would add, from its preview: what their files say. Ids below zero: they have none yet."""
    songs = []
    for head, tags in zip(lines, lines[1:]):
        name, found = re.match(r"^  [ !] (?:(\d{2,}) |   )(.*)$", head), re.match(r"^      (.*?) / (.*?)  \S", tags)
        if name and found:
            artist, album = (("" if x == "?" else x) for x in found.groups())
            songs.append({"id": -1 - len(songs), "title": name[2], "artist": artist, "album": album, "albumartist": artist if album else "",
                          "track": int(name[1] or 0), "tracks": 0, "disc": 0, "discs": 0, "year": 0, "seconds": 0})
    return songs


def format_of(track: dict) -> str:
    """From the database, so it needs no look at the files. An M4A is AAC or Apple Lossless, and the bitrate tells them apart: AAC ends at 320 kbps."""
    kind = Path(track.get("path") or "").suffix.lower().lstrip(".")
    if kind in ("m4a", "m4p", "mp4"):
        return "ALAC" if (track.get("bitRate") or 0) > 350 else "AAC"
    return kind.upper()


def build_library(dump: dict, missing: list, art: dict | None = None) -> dict:
    """Songs, albums and artists from `ipodctl dump`, with a cover flag from `artwork --missing`."""
    without_cover = {enrich.key_of(m) for m in missing}
    albums, artists, songs = {}, {}, []
    stamp = int(time.time())  # a new cover keeps its file name; the page must not show the old one from memory
    for t in dump["tracks"]:
        album = t.get("album") or ""
        owner = t.get("albumArtist") or t.get("artist") or ""
        songs.append({"id": t.get("uniqueID"), "albumArtist": owner, "albumartist": t.get("albumArtist") or "", "genre": t.get("genre") or "",
                      "compilation": bool(t.get("compilation")), "title": t.get("title") or "", "artist": t.get("artist") or "", "album": album,
                      "track": t.get("trackNumber") or 0, "disc": t.get("discNumber") or 0,
                      "seconds": round(t.get("durationSeconds") or 0), "year": t.get("year") or 0,
                      "format": format_of(t), "kbps": t.get("bitRate") or 0, "khz": (t.get("sampleRate") or 0) / 1000,
                      "size": t.get("sizeBytes") or 0, "plays": t.get("playCount") or 0, "tracks": t.get("totalTracks") or 0, "discs": t.get("totalDiscs") or 0})
        entry = albums.setdefault((album, owner), {
            "album": album, "artist": owner, "songs": 0, "year": 0, "seconds": 0,
            "hasCover": f"{owner} | {album}" not in without_cover,
            "art": f"/art/{art[f'{owner} | {album}']}?v={stamp}" if art and f"{owner} | {album}" in art else None})
        entry["songs"] += 1
        entry["seconds"] += round(t.get("durationSeconds") or 0)
        entry["year"] = entry["year"] or t.get("year") or 0
        person = artists.setdefault(owner, {"artist": owner, "albums": set(), "songs": 0, "art": None})
        person["art"] = person["art"] or entry["art"]
        person["albums"].add(album)
        person["songs"] += 1
    splits, doubles = find_splits(songs), find_doubles(songs)
    return {
        "device": dump.get("deviceName"),
        "songs": songs,
        "albums": sorted(albums.values(), key=lambda a: (a["artist"].lower(), a["album"].lower())),
        "artists": sorted(({**p, "albums": len(p["albums"])} for p in artists.values()), key=lambda p: p["artist"].lower()),
        "missingCount": len(missing),
        "splits": splits,
        "doubles": doubles,
        "hints": tidy.hints(songs, splits, doubles),
        "duplicates": tidy.duplicates(songs),
        "outOfPlace": [{"album": a["album"], "artist": a["albumArtist"]} for a in dump.get("albumsOutOfPlace") or []],
    }


class App:
    def __init__(self, options):
        self.options = options
        self.job = Job()
        self.job.after_write = self.prune_backups
        self.library = None
        self.stamp = 0  # goes up with every read of the iPod, so the page can tell when the library is the one it has
        self.missing = []
        self.load_error = None
        self.ejected = False
        self.storage = None
        self.writing = None
        self.song_paths = {}
        self.converting = {}  # one lock per song, so two requests never convert it twice
        self.converting_lock = threading.Lock()

    # -- ipodctl -----------------------------------------------------------------------------

    def ctl(self, *args) -> list:
        command = [str(enrich.IPODCTL), *args]
        if self.options.volume:
            command += ["--volume", self.options.volume]
        if self.options.developer:
            command.append("--developer")
        return command

    def load(self) -> None:
        """Reads the library and the albums without cover from the iPod. Local and read-only."""
        if not enrich.IPODCTL.exists():
            raise enrich.ScanError(f"ipodctl not built at {enrich.IPODCTL} - run: swift build --product ipodctl")
        enrich.HOME.mkdir(parents=True, exist_ok=True)
        dump_file = enrich.HOME / "library.json"
        for args, out in ((("dump", "--out", str(dump_file)), dump_file),
                          (("artwork", "--missing", "--out", str(MISSING)), MISSING)):
            run = subprocess.run(self.ctl(*args), capture_output=True, text=True)
            if run.returncode != 0 or not out.exists():
                raise enrich.ScanError(last_line(run))
        self.missing = json.loads(MISSING.read_text())
        self.library = build_library(json.loads(dump_file.read_text()), self.missing, self.export_art())
        self.stamp += 1
        self.storage = self.read_storage()
        self.writing = self.read_writing()
        sort = subprocess.run(self.ctl("fix-sort"), capture_output=True, text=True)
        self.library["hints"] = tidy.sort_hints(parse_sort(sort.stdout), self.library["songs"]) + self.library["hints"]
        self.song_paths = {t.get("uniqueID"): t.get("path") for t in json.loads(dump_file.read_text())["tracks"]}
        threading.Thread(target=meta.shrink_pictures, daemon=True).start()
        # The review list belongs to the previous library; decisions and cached lookups stay.
        known = {enrich.key_of(m) for m in self.missing}
        everything = enrich.load_review()
        review = [e for e in everything if e["key"] in known]
        dropped = [e for e in everything if e["key"] not in known]
        enrich.write_outputs(review, enrich.load_decisions())
        # An album that now has a cover (it was written) no longer needs its cached image.
        enrich.prune_cache(review, dropped)

    def read_writing(self):
        """Whether this iPod takes writes, as `ipodctl info` says. None when it doesn't say."""
        run = subprocess.run(self.ctl("info"), capture_output=True, text=True)
        found = re.search(r"^\s*writing\s+(allowed|BLOCKED)\b(?:\s*[-:]\s*(.*))?$", run.stdout, re.M)
        return {"allowed": found[1] == "allowed", "why": found[2] or ""} if found else None

    def mounts(self) -> list:
        """Every iPod that could be meant: the one asked for by path, else all that are connected."""
        if self.options.volume and Path(self.options.volume).is_dir():
            return [Path(self.options.volume)] if (Path(self.options.volume) / "iPod_Control").is_dir() else []
        return [m for m in Path("/Volumes").glob("*") if (m / "iPod_Control").is_dir()]

    def mount(self):
        """Where the iPod is, or None when more than one could be meant."""
        mounts = self.mounts()
        return mounts[0] if len(mounts) == 1 else None

    def song_file(self, song_id: int):
        """A song's audio file, only ever inside the iPod's Music folder."""
        mount, path = self.mount(), self.song_paths.get(song_id)
        if not mount or not path:
            return None
        file = (mount / path).resolve()
        return file if file.is_file() and file.is_relative_to((mount / "iPod_Control/Music").resolve()) else None

    def playable(self, file: Path) -> Path:
        """Apple Lossless is a song Chrome cannot decode, so it goes to the browser as FLAC: as lossless, and every browser plays it.
        Done with macOS's own afconvert, once; if that fails, the original is sent and the page says it cannot play it."""
        if file.suffix.lower() not in (".m4a", ".m4b"):
            return file
        name = hashlib.sha1(f"{file}:{file.stat().st_size}".encode()).hexdigest() + ".flac"
        out = CONVERTED / name
        with self.converting_lock:
            lock = self.converting.setdefault(name, threading.Lock())
        with lock:
            if out.exists():
                os.utime(out)
                return out
            info = subprocess.run(["afinfo", str(file)], capture_output=True, text=True)
            if "alac" not in info.stdout:
                return file
            CONVERTED.mkdir(parents=True, exist_ok=True)
            part = out.with_suffix(".part")
            done = subprocess.run(["afconvert", "-f", "flac", "-d", "flac", str(file), str(part)], capture_output=True)
            if done.returncode != 0 or not part.exists():
                part.unlink(missing_ok=True)
                return file
            part.replace(out)
        kept = sorted(CONVERTED.glob("*.flac"), key=lambda f: f.stat().st_mtime, reverse=True)
        total = 0
        for old in kept:
            total += old.stat().st_size
            if total > CONVERTED_LIMIT and old != out:
                old.unlink(missing_ok=True)
        return out

    def read_storage(self):
        """Used and total bytes on the iPod, for its screen."""
        mount = self.mount()
        if not mount:
            return None
        try:
            usage = shutil.disk_usage(mount)
        except OSError:
            return None
        return {"used": usage.total - usage.free, "total": usage.total}

    def export_art(self) -> dict:
        """The covers already on the iPod, as {album key: file}. A failure only means no art in the library views."""
        shutil.rmtree(ART, ignore_errors=True)
        run = subprocess.run(self.ctl("artwork", "--export", str(ART)), capture_output=True, text=True)
        try:
            return {enrich.key_of(e): e["file"] for e in json.loads((ART / "index.json").read_text())} if run.returncode == 0 else {}
        except (OSError, ValueError, KeyError):
            return {}

    def try_load(self) -> None:
        try:
            self.load()
            self.load_error = None
            self.ejected = False
        except (enrich.ScanError, OSError, ValueError, KeyError) as error:
            self.library, self.missing, self.load_error = None, [], str(error)

    # -- state for the page --------------------------------------------------------------------

    def covers(self) -> list:
        decisions = enrich.load_decisions()
        reviewed = {e["key"]: e for e in enrich.load_review()}
        entries = []
        for album in self.missing:
            key = enrich.key_of(album)
            entry = reviewed.get(key)
            if entry:
                status = entry.get("status") or enrich.derive_status(entry, decisions)
            else:
                status = "skip" if key in decisions["skip"] else "pending"
            image = Path(entry["file"]).name if entry and entry.get("file") else None
            entries.append({"key": key, "album": album, "status": status,
                            "match": entry.get("match") if entry else None,
                            "detail": entry.get("detail") if entry else None,
                            "image": f"/img/{image}" if image else None})
        return entries

    def state(self) -> dict:
        entries = self.covers()
        counts = {}
        for entry in entries:
            counts[entry["status"]] = counts.get(entry["status"], 0) + 1
        return {"library": self.library, "stamp": self.stamp, "loadError": self.load_error, "covers": entries, "counts": counts,
                "job": self.job.snapshot(), "ipodctl": enrich.IPODCTL.exists(),
                "artistInfo": meta.load_artists(), "storage": self.storage, "reading": self.job.reading, "connected": bool(self.mounts()),
                "developer": self.options.developer, "writing": self.writing, "ejected": self.ejected, "bothKept": sorted(load_kept()), "songCopy": copy_summary(), "healthIssues": issues(load_health())}

    # -- jobs ----------------------------------------------------------------------------------

    def find_covers(self, limit):
        def work(log):
            enrich.run_scan(input_file=str(MISSING), limit=limit, log=log, stop=self.job.cancel.is_set)
            return not self.job.cancel.is_set()
        return self.job.start("lookup", work)

    def recheck(self, albums: list):
        def work(log):
            enrich.refresh(albums, log, self.job.cancel.is_set)
            return not self.job.cancel.is_set()
        return self.job.start("lookup", work)

    def stream(self, args, log) -> bool:
        # A write is never stopped halfway. In its own session Ctrl-C in the terminal does not reach it, and with
        # SIGPIPE left ignored it finishes even if this server is gone by then.
        process = subprocess.Popen(self.ctl(*args), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                   start_new_session=True, restore_signals=False)
        for line in process.stdout:
            log(line.rstrip().replace("That was a preview. To write: add --apply", "That was a preview. Nothing was changed."))
        process.stdout.close()
        return process.wait() == 0

    def run_ctl(self, kind, args, then=None):
        return self.job.start(kind, lambda log: self.stream(args, log), then)

    def apply_covers(self, write: bool):
        return self.run_ctl("write" if write else "preview",
                            ["apply", str(enrich.MANIFEST), "--unverified-formats"] + (["--apply"] if write else []),
                            self.try_load if write else None)

    def edit_songs(self, ids: list, fields: dict, write: bool):
        args = ["edit", "--id", ",".join(str(i) for i in ids)]
        for name, value in fields.items():
            args += ["--set", f"{name}={value}"]
        return self.run_ctl("edit" if write else "edit-preview", args + (["--apply"] if write else []),
                            self.try_load if write else None)

    def retag_songs(self, songs: list, write: bool):
        """Different values for different songs, through `ipodctl retag`."""
        path = enrich.HOME / "edit-manifest.json"
        enrich.HOME.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"version": 1, "songs": songs}))
        return self.run_ctl("edit" if write else "edit-preview", ["retag", str(path)] + (["--apply"] if write else []),
                            self.try_load if write else None)

    def prepare_cover(self, path: str, url: str) -> str:
        """Keeps a copy of the image picked for an album, so the page can show it before anything is written."""
        if url:
            source = enrich.download_image(url)
            if not source:
                raise enrich.ScanError("no JPEG or PNG image at that address")
        else:
            source = Path(path).expanduser()
            if not source.is_file() or source.suffix.lower() not in IMAGE_KINDS:
                raise enrich.ScanError(f"{source} is not an image file")
        data = source.read_bytes()
        shutil.rmtree(CHOSEN, ignore_errors=True)
        CHOSEN.mkdir(parents=True)
        name = hashlib.sha1(data).hexdigest()
        if source.suffix.lower() in enrich.NEEDS_PNG:  # the next sheet has to show it
            enrich.to_png(source, CHOSEN / f"{name}.png")
            return f"{name}.png"
        (CHOSEN / (name + source.suffix.lower())).write_bytes(data)
        return name + source.suffix.lower()

    def write_cover(self, album: dict, name: str):
        """One album's cover, new or in place of the old one, through `ipodctl apply`."""
        path = enrich.HOME / "cover-manifest.json"
        path.write_text(json.dumps({"version": 1, "covers": [{"album": album["album"], "albumArtist": album["artist"],
                                                              "file": str(CHOSEN / name), "replace": True}]}))
        return self.run_ctl("cover", ["apply", str(path), "--unverified-formats", "--apply"], self.try_load)

    def artist_scan(self, names, again: bool):
        """Facts and pictures for the given artists, or for all not looked up yet. Goes to the internet; touches nothing on the iPod."""
        songs = (self.library or {}).get("songs", [])
        known = meta.load_artists()
        wanted = {}
        for s in songs:
            for name in {s["albumArtist"], s["artist"]} if names else {s["albumArtist"]}:
                if name and (name in names if names else again or not known.get(name, {}).get("looked")):
                    albums = wanted.setdefault(name, [])
                    if s["album"] and s["album"] not in albums:
                        albums.append(s["album"])
        if not wanted:
            return None
        def work(log):
            return meta.scan_artists(wanted, log, self.job.cancel.is_set)
        return self.job.start("artistscan", work)

    def regroup(self, write: bool):
        """Albums older edits left out of place on the iPod, rebuilt from their songs."""
        return self.run_ctl("regroup" if write else "regroup-preview", ["regroup"] + (["--apply"] if write else []),
                            self.try_load if write else None)

    def remove_songs(self, ids: list, write: bool):
        args = ["remove", "--id", ",".join(str(i) for i in ids)]
        return self.run_ctl("remove" if write else "remove-preview", args + (["--apply"] if write else []),
                            self.try_load if write else None)

    def backups(self) -> list:
        """The iPod's backups, newest first, from `ipodctl snapshots`."""
        run = subprocess.run(self.ctl("snapshots"), capture_output=True, text=True)
        if run.returncode != 0:
            raise enrich.ScanError(last_line(run))
        found = []
        for line in run.stdout.splitlines():
            m = re.match(r"^(\S+)  (\d+) files  (\S+\s\S+)(?:  (.*))?$", line)  # \s: some languages put a non-breaking space in a size
            if m:
                found.append({"id": m[1], "files": int(m[2]), "size": m[3], "note": m[4] or ""})
        if found and not STARRED.exists():  # the oldest is the library from before any of this: it stays until you say otherwise
            save_starred({found[-1]["id"]})
        starred, paired = load_starred(), (load_copy() or {}).get("backup")
        for position, backup in enumerate(found):
            backup["starred"] = backup["id"] in starred
            backup["paired"] = backup["id"] == paired  # the songs in the copy are the ones this library lists, so the two stay together
            backup["stays"] = position < KEEP_BACKUPS or backup["starred"] or backup["paired"]
        return found

    def prune_backups(self, log) -> int:
        """As `SnapshotStore.prune`: one that can't be removed stays, and goes next time. Backups share their unchanged files as
        hard links, so removing a folder takes nothing from the others."""
        removed = 0
        for backup in self.backups():
            folders = list(BACKUPS.glob(f"*/{backup['id']}")) if BACKUP_ID.match(backup["id"]) else []
            if not backup["stays"] and len(folders) == 1 and folders[0].is_dir():
                shutil.rmtree(folders[0], ignore_errors=True)
                removed += not folders[0].exists()
        if removed:
            log(f"Removed {removed} older backups.")
        return removed

    def backup_now(self):
        return self.run_ctl("backup", ["backup", "--note", "RePod web: manual backup"])

    def backup_verify(self, backup_id: str):
        return self.run_ctl("verify", ["verify", backup_id])

    def backup_restore(self, backup_id: str):
        return self.run_ctl("restore", ["restore", backup_id], self.try_load)

    def copy_songs(self, dest: Path):
        """The song files and, straight after, a backup of the library that lists them: together they are the whole iPod.
        `restore.json` in the folder says what belongs to the copy, with a checksum for each file."""
        def work(log):
            sources = [m / "iPod_Control/Music" for m in self.mounts() if (m / "iPod_Control/Music").is_dir()]
            if len(sources) != 1:
                raise enrich.ScanError("could not tell which iPod to copy from" if sources else "no iPod music folder found")
            source = sources[0]
            target = dest / "iPod-songs"
            log("Counting the songs…")
            files = [f for f in sorted(source.rglob("*")) if f.is_file() and not f.name.startswith(".")]
            total = len(files)
            log(f"Copying {total:,} files from {source} to {target}")
            log("Files already there are skipped, so this can be run again to carry on.")
            log(f"[0/{total}] starting")
            try:
                known = {f["path"]: f for f in json.loads((target / RESTORE_FILE).read_text())["files"]}
            except (OSError, ValueError, KeyError):
                known = {}
            need, free = sum(f.stat().st_size for f in files if f.relative_to(source).as_posix() not in known), shutil.disk_usage(dest).free
            if need + (1 << 26) > free:
                raise enrich.ScanError(f"The copy needs {need / 1e9:.1f} GB and {dest} has {free / 1e9:.1f} GB free. Nothing was copied: choose a folder with more room, such as an external drive.")
            target.mkdir(parents=True, exist_ok=True)
            entries, done, skipped, last = [], 0, 0, time.monotonic()

            def describe(complete, backup=None, entries=entries):
                record = {"version": 1, "made": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "complete": complete, "backup": backup, "files": entries}
                part = target / f".{RESTORE_FILE}.part"
                part.write_text(json.dumps(record))
                part.replace(target / RESTORE_FILE)
                enrich.HOME.mkdir(parents=True, exist_ok=True)
                SONG_COPY.write_text(json.dumps({"folder": str(target)}))

            def unfinished():  # what is in the folder so far, so a stop or a crash only costs what was not yet copied
                describe(False, entries=entries + [known[k] for k in sorted(known.keys() - {e["path"] for e in entries})])

            saved = time.monotonic()
            for number, file in enumerate(files, 1):
                if self.job.cancel.is_set():
                    unfinished()
                    log(f"Stopped after {done:,} copied. Run the copy again to carry on; what is there is kept.")
                    return False
                if time.monotonic() - saved >= 30:
                    saved = time.monotonic()
                    unfinished()
                path = file.relative_to(source).as_posix()
                info, out, before = file.stat(), target / path, known.get(path)
                if before and before["size"] == info.st_size and before["mtime"] == int(info.st_mtime) and out.exists() and out.stat().st_size == info.st_size:
                    entries.append(before)
                    skipped += 1
                else:
                    entries.append({"path": path, "size": info.st_size, "mtime": int(info.st_mtime), "sha256": copy_checked(file, out)})
                    done += 1
                if number == total or time.monotonic() - last >= 0.7:  # about once a second, whatever the speed
                    last = time.monotonic()
                    log(f"[{number}/{total}] {file.parent.name}/{file.name}")
            copied = {e["path"] for e in entries}
            lost = [p for p in self.song_paths.values() if p and p.replace("iPod_Control/Music/", "", 1) not in copied]
            if lost:
                log(f"WARNING: {len(lost)} songs in the library have no file on the iPod, so they aren't in the copy either.")
            log("Saving the library with them…")
            run = subprocess.run(self.ctl("backup", "--note", "RePod web: with the song copy"), capture_output=True, text=True)
            backup = re.search(r"identifier\s+(\S+)", run.stdout)
            if run.returncode != 0 or not backup:
                describe(False)
                raise enrich.ScanError(f"The songs are copied, but the library could not be saved: {last_line(run)}")
            describe(True, backup[1])
            log(f"Saved {total} songs: copied {done:,}, skipped {skipped:,} that were already there. Keep the whole folder: the names are scrambled.")
            return True
        return self.job.start("copy", work)

    def check_copy(self):
        """Reads the copy back and compares every file with its checksum. Only the Mac's disk is touched, so no iPod is needed."""
        def work(log):
            copy = load_copy()
            if not copy:
                raise enrich.ScanError("There is no copy of the songs to check.")
            bad, total = self.damaged(copy, log)
            if self.job.cancel.is_set():
                log("Stopped. Nothing was changed.")
                return False
            log(f"All {total} files match." if not bad else f"WARNING: {len(bad):,} of {total:,} files are damaged or missing: {', '.join(bad[:5])}")
            return True
        return self.job.start("copycheck", work)

    def damaged(self, copy: dict, log) -> tuple:
        bad, total, last = [], len(copy["files"]), time.monotonic()
        for number, entry in enumerate(copy["files"], 1):
            if self.job.cancel.is_set():
                break
            file = copy["folder"] / entry["path"]
            if not file.is_file() or file.stat().st_size != entry["size"] or sha256_of(file) != entry["sha256"]:
                bad.append(entry["path"])
            if number == total or time.monotonic() - last >= 0.7:
                last = time.monotonic()
                log(f"[{number}/{total}] checking {entry['path']}")
        return bad, total

    def rebuild(self):
        """The whole iPod back from the song copy and its backup. Everything that can refuse does so first, and nothing is overwritten:
        a file already there and right is kept, so this can simply be run again. The library goes last, so it never lists a song that isn't there."""
        def work(log):
            copy, mounts = load_copy(), self.mounts()
            if not copy or not copy.get("complete") or not copy.get("backup"):
                raise enrich.ScanError("There is no finished copy of the songs. Nothing was changed.")
            if len(mounts) != 1:
                raise enrich.ScanError("no iPod is connected" if not mounts else "more than one iPod is connected")
            control, backup = mounts[0] / "iPod_Control", copy["backup"]
            database = control / "iTunes/iTunesDB"
            saved = list(BACKUPS.glob(f"*/{backup}/files/iTunes/iTunesDB")) if BACKUP_ID.match(backup) else []
            if len(saved) != 1:
                raise enrich.ScanError("The backup saved with this copy is gone. Nothing was changed.")
            log("Checking the copy…")
            bad, total = self.damaged(copy, log)
            if bad:
                raise enrich.ScanError(f"{len(bad):,} files in the copy are damaged or missing ({', '.join(bad[:3])}). Nothing was changed.")
            seeded = not database.exists()
            if seeded:  # a reset iPod has no library, and `ipodctl` opens nothing without one; the backup's own is the right one to start from
                database.parent.mkdir(parents=True, exist_ok=True)
                for file in saved[0].parent.iterdir():  # with the small files beside it, which `restore` leaves alone
                    if not (database.parent / file.name).exists():
                        shutil.copyfile(file, database.parent / file.name)
                log("This iPod has no library yet: starting from the one in the backup.")
            try:
                run = subprocess.run(self.ctl("snapshots"), capture_output=True, text=True)
                if not any(line.startswith(backup + " ") for line in run.stdout.splitlines()):
                    raise enrich.ScanError("That backup belongs to another iPod. Nothing was changed.")
                if not self.stream(["verify", backup], log):
                    raise enrich.ScanError("The backup saved with this copy is damaged. Nothing was changed.")
                log("Looking at what is on the iPod…")
                music, missing, clash = control / "Music", [], []
                for entry in copy["files"]:
                    file = music / entry["path"]
                    if not file.exists():
                        missing.append(entry)
                    elif file.stat().st_size != entry["size"] or sha256_of(file) != entry["sha256"]:
                        clash.append(entry["path"])
                if clash:
                    raise enrich.ScanError(f"{len(clash):,} files on the iPod differ from the copy ({', '.join(clash[:3])}). Nothing was changed.")
                need, free = sum(e["size"] for e in missing), shutil.disk_usage(mounts[0]).free
                if need + (1 << 26) > free:
                    raise enrich.ScanError(f"The iPod needs {need / 1e9:.1f} GB free and has {free / 1e9:.1f} GB. Nothing was changed.")
                log(f"{len(missing):,} songs to put back, {total - len(missing):,} already there.")
                log(f"[0/{len(missing)}] starting")
                last = time.monotonic()
                for number, entry in enumerate(missing, 1):
                    if copy_checked(copy["folder"] / entry["path"], music / entry["path"]) != entry["sha256"]:
                        raise enrich.ScanError(f"{entry['path']} changed while it was being copied.")
                    if number == len(missing) or time.monotonic() - last >= 0.7:
                        last = time.monotonic()
                        log(f"[{number}/{len(missing)}] {entry['path']}")
            except OSError as error:
                if seeded:
                    database.unlink(missing_ok=True)
                raise enrich.ScanError(f"The copy stopped: {error.strerror or error}. What went across is kept: run this again to carry on.")
            except BaseException:
                if seeded:  # no library is better than one listing songs that aren't there
                    database.unlink(missing_ok=True)
                raise
            log("Restoring the library…")
            if not self.stream(["restore", backup], log):
                raise enrich.ScanError("The songs are back, but the library could not be restored. Run this again: it won't copy them twice.")
            out = enrich.HOME / "rebuilt.json"
            run = subprocess.run(self.ctl("dump", "--out", str(out)), capture_output=True, text=True)
            if run.returncode != 0:
                log("WARNING: the songs and the library are back, but they could not be checked.")
                return True
            tracks = json.loads(out.read_text())["tracks"]
            absent = [t.get("title") or "?" for t in tracks if t.get("path") and not (mounts[0] / t["path"]).exists()]
            if absent:
                log(f"WARNING: {len(absent):,} songs in the library have no file: {', '.join(absent[:5])}")
            log(f"Restored {len(tracks)} songs.")
            return True
        return self.job.start("rebuild", work, self.try_load)

    def health_check(self):
        """Every check, and the format of every song. Only reads: the file scan is the slow part."""
        def work(log):
            log("Running the checks…")
            run = subprocess.run(self.ctl("diagnose"), capture_output=True, text=True)
            if run.returncode != 0:
                raise enrich.ScanError(last_line(run))
            result = parse_diagnose(run.stdout)
            log("Reading the format of every song…")
            run = subprocess.run(self.ctl("audio"), capture_output=True, text=True)
            if run.returncode != 0:
                raise enrich.ScanError(last_line(run))
            result = {**result, "audio": parse_audio(run.stdout), "checked": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
            enrich.HOME.mkdir(parents=True, exist_ok=True)
            HEALTH.write_text(json.dumps(result))
            log(f"Done. {len(result['checks'])} checks, {len(result['audio']['songs'])} songs the iPod can't play. Nothing was changed.")
            return True
        return self.job.start("health", work)

    def convert_songs(self, ids: list, originals):
        """Songs the iPod can't play, in a format it can. `ipodctl audio` keeps the song and its place in every playlist; the file changes after the library is written."""
        def then():
            health = load_health()
            if health.get("audio"):
                health["audio"]["songs"] = [s for s in health["audio"]["songs"] if s["id"] not in ids]
                HEALTH.write_text(json.dumps(health))
            self.try_load()
        keep = ["--keep-originals", originals] if originals else ["--discard-originals"]
        return self.run_ctl("convert", ["audio", "--fix", ",".join(str(i) for i in ids), *keep, "--apply"], then)

    def eject(self) -> None:
        """Unmounts and ejects the iPod, so it can be unplugged. The page keeps showing what it already read."""
        mounts = self.mounts()
        if len(mounts) != 1:
            raise enrich.ScanError("could not tell which iPod to eject" if mounts else "no iPod is connected")
        run = subprocess.run(["diskutil", "eject", str(mounts[0])], capture_output=True, text=True)
        if run.returncode != 0:
            raise enrich.ScanError(last_line(run))
        self.ejected = True

    def meta_scan(self):
        """Looks the songs up on MusicBrainz and saves proposals. Reads the library file from the last load; changes nothing."""
        def work(log):
            dump = json.loads((enrich.HOME / "library.json").read_text())
            meta.scan(meta.songs_of(dump), log, self.job.cancel.is_set)
            return not self.job.cancel.is_set()
        return self.job.start("metascan", work)

    def meta_apply(self, write: bool):
        manifest = meta.manifest(meta.load(), meta.load_decisions())
        if not manifest["songs"]:
            return None
        path = enrich.HOME / "meta-manifest.json"
        path.write_text(json.dumps(manifest, indent=1))
        return self.run_ctl("metawrite" if write else "metapreview", ["retag", str(path)] + (["--apply"] if write else []),
                            (lambda: (meta.consume(), self.try_load())) if write else None)

    def add_lookup(self, path: str):
        """MusicBrainz on songs still in a folder: nothing is written, and the page shows what it found."""
        def work(log):
            run = subprocess.run(self.ctl("add", path), capture_output=True, text=True)
            if run.returncode != 0:
                raise enrich.ScanError(last_line(run))
            songs = read_drafts(run.stdout.splitlines())
            log(f"Files found: {len(songs)}")
            review = meta.proposals(songs, log, self.job.cancel.is_set)
            enrich.HOME.mkdir(parents=True, exist_ok=True)
            ADD_REVIEW.write_text(json.dumps({"path": path, "songs": songs, "proposals": review}))
            log(f"Stopped. Found {len(review)} so far." if self.job.cancel.is_set() else f"Done. {len(review)} proposals. Nothing was changed.")
            return True
        return self.job.start("addscan", work)

    def add_named(self, path: str, accepted: list):
        """Adds the songs, and at once gives them the names that were accepted. `add` takes a file's own tags, so that is two steps."""
        review = json.loads(ADD_REVIEW.read_text())
        decided = {key: {"status": "accepted", "fields": list(meta.GROUPS)} for key in accepted}
        wanted, drafts = meta.manifest(review["proposals"], decided)["songs"], {s["id"]: s for s in review["songs"]}
        before = {s["id"] for s in (self.library or {}).get("songs", [])}

        def work(log):
            if not self.stream(["add", path, "--apply"], log):
                return False
            out = enrich.HOME / "added.json"
            run = subprocess.run(self.ctl("dump", "--out", str(out)), capture_output=True, text=True)
            if run.returncode != 0:
                log("WARNING: the songs were added, but keep the names in their files.")
                return True
            pool = {}
            for track in json.loads(out.read_text())["tracks"]:
                if track.get("uniqueID") not in before:
                    pool.setdefault(((track.get("title") or "").casefold(), (track.get("album") or "").casefold()), []).append(track["uniqueID"])
            songs = []
            for entry in wanted:
                draft = drafts[entry["id"]]
                found = pool.get((draft["title"].casefold(), draft["album"].casefold()))
                if found:
                    songs.append({"id": found.pop(0), "set": entry["set"]})
                else:
                    log(f"  - SKIPPED {draft['title']}: not found after adding")
            if songs:
                manifest = enrich.HOME / "add-manifest.json"
                manifest.write_text(json.dumps({"version": 1, "songs": songs}))
                if not self.stream(["retag", str(manifest), "--apply"], log):
                    log("WARNING: the songs were added, but keep the names in their files.")
            return True

        def then():
            ADD_REVIEW.unlink(missing_ok=True)
            self.try_load()
        return self.job.start("add", work, then)

    def add_songs(self, path: str, write: bool):
        return self.run_ctl("add" if write else "add-preview", ["add", path] + (["--apply"] if write else []),
                            self.try_load if write else None)


def choose(kind: str, prompt: str = "", images: bool = False):
    """The macOS file or folder dialog, asked from the local server. Returns a path or None."""
    prompt = re.sub(r'["\\]', "", prompt)[:80] or ("Choose a folder of songs" if kind == "folder" else "Choose a song file")
    only = ' of type {"public.image"}' if images and kind != "folder" else ""
    # Asked through System Events so the dialog comes to the front; from a background process it opens behind the browser.
    script = ('tell application "System Events"\nactivate\n'
              f'POSIX path of (choose {"folder" if kind == "folder" else "file"} with prompt "{prompt}"{only})\nend tell')
    run = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if run.returncode != 0 and "-128" not in run.stderr:  # -128 is Cancel; anything else means the window never opened
        raise enrich.ScanError("The Mac would not open the file window. Type the path instead, or allow this Terminal under System Settings › Privacy & Security › Automation.")
    return run.stdout.strip() or None if run.returncode == 0 else None


def pin_problem(pin: dict):
    """Why this image can't be used for an album, or None."""
    if pin.get("file"):
        file = Path(str(pin["file"])).expanduser()
        if not file.is_file():
            return f"There is no file at {file}. Check the path, or use Choose file."
        return None if file.suffix.lower() in IMAGE_KINDS else f"{file.name} is not an image file."
    if pin.get("url"):
        try:
            return None if enrich.download_image(str(pin["url"])) else "There is no JPEG or PNG image at that address."
        except enrich.LOOKUP_FAILED:
            return "That address could not be reached. Check it and try again."
    return None


def make_handler(app: App, port: int):
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def _send(self, status, body: bytes, content_type="application/json", cache=False):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            # A picture's address changes with the picture, so it can be kept; everything else is live.
            self.send_header("Cache-Control", "max-age=31536000, immutable" if cache else "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _audio(self, file: Path):
            """A song, in as many pieces as the player asks for: it seeks with byte ranges."""
            size = file.stat().st_size
            wanted = re.match(r"bytes=(\d*)-(\d*)$", self.headers.get("Range", ""))
            start, end = 0, size - 1
            if wanted and (wanted[1] or wanted[2]):
                start = int(wanted[1]) if wanted[1] else max(0, size - int(wanted[2]))
                end = min(int(wanted[2]), size - 1) if wanted[1] and wanted[2] else size - 1
                if start > end:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    return self.end_headers()
            self.send_response(206 if wanted else 200)
            self.send_header("Content-Type", AUDIO_KINDS.get(file.suffix.lower(), "application/octet-stream"))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(end - start + 1))
            if wanted:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            try:
                with file.open("rb") as audio:
                    audio.seek(start)
                    left = end - start + 1
                    while left and (chunk := audio.read(min(65536, left))):
                        self.wfile.write(chunk)
                        left -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass  # the player moved on

        def _json(self, payload, status=200):
            self._send(status, json.dumps(payload).encode())

        def _trusted(self) -> bool:
            # Only this page may talk to the server: a page on another site must not be able to
            # make the browser press Write (DNS rebinding / cross-site requests).
            if self.headers.get("Host") not in allowed_hosts:
                return False
            origin = self.headers.get("Origin")
            return origin is None or origin.replace("http://", "") in allowed_hosts

        def _guarded(self, answer):
            try:
                answer()
            except (BrokenPipeError, ConnectionResetError):
                pass  # the page went away; there is no one to tell
            except enrich.ScanError as error:
                self._json({"error": friendly(str(error))}, 503)
            except Exception as error:  # an error the page can show, not a dropped connection
                self._json({"error": f"Something unexpected happened ({type(error).__name__}: {error})"}, 500)

        def do_GET(self):
            self._guarded(self._get)

        def do_POST(self):
            self._guarded(self._post)

        def _get(self):
            if not self._trusted():
                return self._send(403, b"forbidden", "text/plain")
            path = unquote(urlparse(self.path).path)  # a picture kept under its own name can have a space in it
            if path == "/":
                return self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            if path.startswith("/assets/") or path == "/favicon.ico":
                file = ASSETS / ("favicon.png" if path == "/favicon.ico" else Path(path[8:]).name)
                kind = {".svg": "image/svg+xml", ".png": "image/png"}.get(file.suffix)
                if kind and file.is_file():
                    return self._send(200, file.read_bytes(), kind)
                return self._send(404, b"not found", "text/plain")
            if path == "/style.css":
                return self._send(200, STYLE.read_bytes(), "text/css; charset=utf-8")
            if path == "/api/state":
                if app.library is None and app.load_error is None and app.job.claim():
                    try:
                        app.try_load()  # the first visit reads the iPod, and only reads it
                    finally:
                        app.job.release()
                return self._json(app.state())
            if path == "/api/device":  # asked every few seconds: costs a directory listing, and touches nothing on the iPod
                return self._json({"connected": bool(app.mounts()), "stamp": app.stamp, "running": app.job.running, "reading": app.job.reading})
            if path == "/api/job":
                return self._json({"job": app.job.snapshot(), "reading": app.job.reading})  # a poll: small, whatever the size of the library
            if path == "/api/meta":
                return self._json({"proposals": meta.load(), "decisions": meta.load_decisions(),
                                   "groups": list(meta.GROUPS), "default": meta.DEFAULT})
            if path == "/api/health":
                return self._json(load_health())
            if path == "/api/add-review":
                return self._json(json.loads(ADD_REVIEW.read_text()) if ADD_REVIEW.exists() else {})
            if path == "/api/backups":
                return self._json({"backups": app.backups(), "folder": str(BACKUPS), "keep": KEEP_BACKUPS})
            if path.startswith("/audio/") and path[7:].isdigit():
                file = app.song_file(int(path[7:]))
                return self._audio(app.playable(file)) if file else self._send(404, b"not found", "text/plain")
            if path.startswith("/chosen/"):
                file = CHOSEN / Path(path[8:]).name
                if file.is_file() and file.suffix in IMAGE_KINDS:
                    return self._send(200, file.read_bytes(), IMAGE_KINDS[file.suffix])
            if path.startswith("/artist-art/"):
                file = meta.PICTURES / Path(path[12:]).name
                if file.is_file():
                    return self._send(200, file.read_bytes(), IMAGE_KINDS.get(file.suffix, "image/jpeg"), cache=True)
            if path.startswith("/art/"):
                file = ART / Path(path[5:]).name
                if file.is_file():
                    return self._send(200, file.read_bytes(), "image/png", cache=True)
            if path.startswith("/img/"):
                file = enrich.COVERS / Path(path[5:]).name
                if file.is_file():
                    kind = "image/png" if file.suffix == ".png" else "image/jpeg"
                    return self._send(200, file.read_bytes(), kind)
            self._send(404, b"not found", "text/plain")

        def _post(self):
            if not self._trusted() or self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                return self._send(403, b"forbidden", "text/plain")
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            except ValueError:
                return self._json({"error": "bad json"}, 400)
            path = urlparse(self.path).path
            busy = {"error": BUSY}
            if path in NEEDS_IPOD and not app.mounts():
                return self._json({"error": friendly("no iPod found")}, 503)
            if path == "/api/dismiss":
                app.job.clear()
                return self._json(app.state())
            if path == "/api/load":
                if not app.job.claim():
                    return self._json(busy, 409)
                try:
                    app.try_load()
                finally:
                    app.job.release()
                return self._json(app.state())
            if path == "/api/choose":
                return self._json({"path": choose("folder" if body.get("kind") == "folder" else "file", str(body.get("prompt", "")), body.get("images") is True)})
            if path == "/api/find-covers":
                started = app.find_covers(body.get("limit") or None)
            elif path == "/api/recheck":
                albums = [m for m in app.missing if enrich.key_of(m) == body.get("key")]
                if not albums:
                    return self._json({"error": "no such album"}, 400)
                started = app.recheck(albums)
            elif path == "/api/refresh-missing":
                retry = {e["key"] for e in app.covers() if e["status"] in ("none", "error")}
                started = app.recheck([m for m in app.missing if enrich.key_of(m) in retry])
            elif path == "/api/clear-cache":
                if body.get("confirm") is not True:
                    return self._json({"error": "clear needs confirm: true"}, 400)
                if app.job.running:
                    return self._json(busy, 409)
                enrich.clear_cache()
                return self._json(app.state())
            elif path == "/api/preview":
                started = app.apply_covers(False)
            elif path == "/api/write":
                if body.get("confirm") is not True:
                    return self._json({"error": "write needs confirm: true"}, 400)
                started = app.apply_covers(True)
            elif path in ("/api/add-preview", "/api/add"):
                song_path = Path(str(body.get("path", "")).strip()).expanduser()
                if not song_path.exists():
                    return self._json({"error": f"{song_path} does not exist"}, 400)
                if path == "/api/add" and body.get("confirm") is not True:
                    return self._json({"error": "add needs confirm: true"}, 400)
                accepted = body.get("accepted")
                if accepted and not (ADD_REVIEW.exists() and json.loads(ADD_REVIEW.read_text())["path"] == str(song_path)):
                    return self._json({"error": "Look these songs up again: that answer was for another folder."}, 400)
                started = app.add_named(str(song_path), accepted) if path == "/api/add" and accepted else app.add_songs(str(song_path), path == "/api/add")
            elif path == "/api/add-lookup":
                song_path = Path(str(body.get("path", "")).strip()).expanduser()
                if not song_path.exists():
                    return self._json({"error": f"{song_path} does not exist"}, 400)
                started = app.add_lookup(str(song_path))
            elif path == "/api/backup-star":
                backup_id = body.get("id")
                if not isinstance(backup_id, str) or not BACKUP_ID.match(backup_id):
                    return self._json({"error": "unknown backup"}, 400)
                save_starred(load_starred() | {backup_id} if body.get("star", True) else load_starred() - {backup_id})
                return self._json({"starred": sorted(load_starred())})
            elif path == "/api/backup-clean":
                if body.get("confirm") is not True:
                    return self._json({"error": "clean needs confirm: true"}, 400)
                started = app.job.start("backup-clean", lambda log: app.prune_backups(log) >= 0)
            elif path == "/api/backup-create":
                started = app.backup_now()
            elif path == "/api/meta-scan":
                if app.library is None:
                    return self._json({"error": "read the iPod first"}, 400)
                started = app.meta_scan()
            elif path == "/api/meta-decide":
                key, status, fields = body.get("key"), body.get("status"), body.get("fields")
                if key not in {p["key"] for p in meta.load()} or status not in ("accepted", "skipped", "reset") \
                        or not (fields is None or (isinstance(fields, list) and set(fields) <= set(meta.GROUPS))):
                    return self._json({"error": "unknown proposal, status or fields"}, 400)
                return self._json({"decisions": meta.decide(key, status, fields)})
            elif path == "/api/keep-both":
                key = re.fullmatch(r"\d+(-\d+)+", str(body.get("key") or ""))
                if not key:
                    return self._json({"error": "unknown set of songs"}, 400)
                kept = load_kept() | {key.group()} if body.get("keep", True) else load_kept() - {key.group()}
                enrich.HOME.mkdir(parents=True, exist_ok=True)
                KEPT.write_text(json.dumps(sorted(kept)))
                return self._json({"bothKept": sorted(kept)})
            elif path == "/api/meta-accept":  # many at once: one request, and nothing can start between them
                known = {p["key"] for p in meta.load()}
                accepted = body.get("accept")
                if not isinstance(accepted, dict) or not set(accepted) <= known or not all(isinstance(f, list) and set(f) <= set(meta.GROUPS) for f in accepted.values()):
                    return self._json({"error": "unknown proposal or fields"}, 400)
                return self._json({"decisions": meta.accept_all(accepted)})
            elif path in ("/api/meta-preview", "/api/meta-apply"):
                if path == "/api/meta-apply" and body.get("confirm") is not True:
                    return self._json({"error": "apply needs confirm: true"}, 400)
                started = app.meta_apply(path == "/api/meta-apply")
                if started is None:
                    return self._json({"error": "nothing is accepted"}, 400)
            elif path == "/api/eject":
                if not app.job.claim():
                    return self._json(busy, 409)
                try:
                    app.eject()
                except enrich.ScanError as error:
                    return self._json({"error": f"The iPod was not ejected. {friendly(str(error))}"}, 400)
                finally:
                    app.job.release()
                return self._json(app.state())
            elif path == "/api/cancel":
                if app.job.running and app.job.kind in STOPPABLE:
                    app.job.cancel.set()
                return self._json({"cancelled": app.job.cancel.is_set()})
            elif path == "/api/backup-open":
                subprocess.run(["open", str(BACKUPS)])
                return self._json({})
            elif path in ("/api/backup-verify", "/api/backup-restore"):
                backup_id = body.get("id")
                if not isinstance(backup_id, str) or not BACKUP_ID.match(backup_id):
                    return self._json({"error": "id must be a backup identifier"}, 400)
                if path == "/api/backup-restore":
                    if body.get("confirm") is not True:
                        return self._json({"error": "restore needs confirm: true"}, 400)
                    started = app.backup_restore(backup_id)
                else:
                    started = app.backup_verify(backup_id)
            elif path == "/api/copy-songs":
                dest = Path(str(body.get("dest", "")).strip()).expanduser()
                if not dest.is_dir():
                    return self._json({"error": f"{dest} is not a folder"}, 400)
                if body.get("confirm") is not True:
                    return self._json({"error": "copy needs confirm: true"}, 400)
                started = app.copy_songs(dest)
            elif path == "/api/settings":
                # The app has no command line, so what `--developer` does is a choice on the page, kept on this Mac.
                if body.get("confirm") is not True or not isinstance(body.get("developer"), bool):
                    return self._json({"error": "settings need developer and confirm: true"}, 400)
                if not app.job.claim():
                    return self._json({"error": BUSY}, 409)
                try:
                    app.options.developer = body["developer"]
                    enrich.HOME.mkdir(parents=True, exist_ok=True)
                    SETTINGS.write_text(json.dumps({**load_settings(), "developer": body["developer"]}))
                    app.writing = app.read_writing() if app.mounts() else app.writing
                finally:
                    app.job.release()
                return self._json({"developer": app.options.developer, "writing": app.writing})
            elif path == "/api/health":
                started = app.health_check()
            elif path == "/api/fix-sort":
                if body.get("confirm") is not True:
                    return self._json({"error": "fix-sort needs confirm: true"}, 400)
                started = app.run_ctl("sortfix", ["fix-sort", "--apply"], app.try_load)
            elif path == "/api/audio-fix":
                ids, originals = body.get("ids"), body.get("originals")
                if not isinstance(ids, list) or not ids or not all(isinstance(i, int) for i in ids):
                    return self._json({"error": "ids must be a list of song numbers"}, 400)
                if body.get("confirm") is not True:
                    return self._json({"error": "audio-fix needs confirm: true"}, 400)
                if not originals and body.get("discard") is not True:
                    return self._json({"error": "Say where the originals go, or that they aren't kept."}, 400)
                if originals:
                    folder = Path(str(originals)).expanduser()
                    if not folder.is_dir():
                        return self._json({"error": f"{folder} is not a folder"}, 400)
                    wanted, sizes = set(ids), {s["id"]: s["size"] for s in (app.library or {}).get("songs", [])}
                    need, free = sum(sizes.get(i, 0) for i in wanted), shutil.disk_usage(folder).free
                    if need + (1 << 26) > free:
                        return self._json({"error": f"The originals need {need / 1e9:.1f} GB and {folder} has {free / 1e9:.1f} GB free. Nothing was changed."}, 400)
                    originals = str(folder)
                started = app.convert_songs(ids, originals)
            elif path == "/api/copy-check":
                started = app.check_copy()
            elif path == "/api/rebuild":
                if body.get("confirm") is not True:
                    return self._json({"error": "rebuild needs confirm: true"}, 400)
                started = app.rebuild()
            elif path == "/api/mb-search":
                mode = body.get("mode")
                text = {k: str(body.get(k) or "").strip()[:200] for k in ("title", "artist", "album")}
                if mode not in ("song", "album", "artist"):
                    return self._json({"error": "mode must be song, album or artist"}, 400)
                try:
                    return self._json({"results": meta.lookup(mode, **text)})
                except Exception as error:  # offline, or MusicBrainz is busy: the form says so and carries on
                    return self._json({"error": f"MusicBrainz could not be reached ({type(error).__name__})"}, 502)
            elif path == "/api/artist-scan":
                names = body.get("names")
                if not (names is None or (isinstance(names, list) and all(isinstance(n, str) for n in names))):
                    return self._json({"error": "names must be a list"}, 400)
                started = app.artist_scan(names, body.get("again") is True)
                if started is None:
                    return self._json({"error": "every artist is looked up"}, 400)
            elif path == "/api/artist-search":
                name = str(body.get("album") or "").strip()[:200]
                if not name:
                    return self._json({"error": "give a name"}, 400)
                return self._json(meta.search_artist_pictures(name))
            elif path == "/api/artist-picture":
                name, url, file = str(body.get("name") or "").strip(), str(body.get("url") or "").strip(), Path(str(body.get("path") or "").strip()).expanduser()
                if not name or not (re.match(r"^https?://", url) or (file.is_file() and file.suffix.lower() in IMAGE_KINDS)):
                    return self._json({"error": "That image can't be used. Give a picture file or a web address."}, 400)
                try:
                    picture = meta.picture_from(url) if url else meta.keep_picture(file)
                except OSError as error:
                    return self._json({"error": f"That image can't be used: {error}"}, 400)
                if not picture:
                    return self._json({"error": "That image can't be used: no JPEG or PNG at that address."}, 400)
                meta.save_artist(name, {**meta.load_artists().get(name, {}), "picture": picture, "pinned": True})
                return self._json(app.state())
            elif path == "/api/cover-search":
                album, artist = str(body.get("album") or "").strip()[:200], str(body.get("artist") or "").strip()[:200]
                if not album:
                    return self._json({"error": "give an album name"}, 400)
                return self._json(enrich.search_covers(album, artist))
            elif path == "/api/cover-prepare":
                url, file = str(body.get("url") or "").strip(), str(body.get("path") or "").strip()
                if url and not re.match(r"^https?://", url):
                    return self._json({"error": "give a web address or an image file"}, 400)
                try:
                    return self._json({"name": (name := app.prepare_cover(file, url)), "image": f"/chosen/{name}"})
                except (enrich.ScanError, OSError) as error:
                    return self._json({"error": f"That image can't be used: {error}"}, 400)
            elif path == "/api/cover-set":
                album = next((a for a in (app.library or {}).get("albums", []) if a["album"] and f"{a['artist']} | {a['album']}" == body.get("key")), None)
                name = Path(str(body.get("name") or "")).name
                if album is None or not name or not (CHOSEN / name).is_file():
                    return self._json({"error": "unknown album or image"}, 400)
                if body.get("confirm") is not True:
                    return self._json({"error": "cover needs confirm: true"}, 400)
                started = app.write_cover(album, name)
            elif path in ("/api/regroup-preview", "/api/regroup"):
                if path == "/api/regroup" and body.get("confirm") is not True:
                    return self._json({"error": "regroup needs confirm: true"}, 400)
                started = app.regroup(path == "/api/regroup")
            elif path == "/api/songs-retag":
                songs = body.get("songs")
                texts = {"title", "artist", "albumartist", "album", "genre", "composer"}
                numbers = {"year", "track", "tracks", "disc", "discs"}
                def valid(entry):
                    values = entry.get("set") if isinstance(entry, dict) else None
                    return (isinstance(values, dict) and values and isinstance(entry.get("id"), int) and entry["id"] >= 0
                            and set(values) <= texts | numbers | {"compilation"}
                            and isinstance(values.get("compilation", False), bool)
                            and all(isinstance(v, str) for k, v in values.items() if k in texts)
                            and all(isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 9999 for k, v in values.items() if k in numbers))
                if not isinstance(songs, list) or not songs or len(songs) > 5000 or not all(valid(e) for e in songs):
                    return self._json({"error": "songs must be a list of {id, set} with known fields"}, 400)
                started = app.retag_songs([{"id": e["id"], "set": e["set"]} for e in songs], body.get("confirm") is True)
            elif path in ("/api/songs-edit", "/api/songs-remove"):
                ids = body.get("ids")
                if not isinstance(ids, list) or not ids or not all(isinstance(i, int) and i >= 0 for i in ids):
                    return self._json({"error": "ids must be a list of song ids"}, 400)
                write = body.get("confirm") is True
                if path == "/api/songs-remove":
                    started = app.remove_songs(ids, write)
                else:
                    fields = body.get("set")
                    allowed = {"title", "artist", "albumartist", "album", "genre", "composer"}
                    if not isinstance(fields, dict) or not fields or not set(fields) <= allowed \
                            or not all(isinstance(v, str) for v in fields.values()):
                        return self._json({"error": f"set must map some of {sorted(allowed)} to text"}, 400)
                    started = app.edit_songs(ids, fields, write)
            elif path == "/api/decide":
                if app.job.running:
                    return self._json(busy, 409)
                pin = body.get("pin")
                if body.get("action") == "pin" and not (isinstance(pin, dict) and len(pin) == 1):
                    return self._json({"error": "pin needs one of url, file, mbid"}, 400)
                album = next((m for m in app.missing if enrich.key_of(m) == body.get("key")), None)
                if body.get("action") == "pin" and (problem := pin_problem(pin)):
                    return self._json({"error": problem}, 400)  # before it is saved: a pin stays, and every later scan would meet it
                try:
                    enrich.apply_decision(body["key"], body["action"], pin, album)
                except (KeyError, ValueError) as error:
                    return self._json({"error": str(error)}, 400)
                return self._json(app.state())
            else:
                return self._send(404, b"not found", "text/plain")
            if not started:
                return self._json(busy, 409)
            self._json({"started": True})

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--volume", help="which iPod, as for ipodctl --volume")
    parser.add_argument("--developer", action="store_true", help="pass --developer to ipodctl")
    options = parser.parse_args()
    app = App(options)
    options.developer = options.developer or load_settings().get("developer", False)
    server = ThreadingHTTPServer(("127.0.0.1", options.port), make_handler(app, options.port))
    print(f"iPod library: http://127.0.0.1:{options.port}  (Ctrl-C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        finish_write(app)


def finish_write(app: App) -> None:
    """Ctrl-C during a write: the write goes on, and the server stays until it is done, to read its result."""
    if not (app.job.running and app.job.kind in WRITES):
        return
    print("\nThe iPod is being written to. Waiting for that to finish... (Ctrl-C again to leave; the write carries on without this window)")
    try:
        while app.job.running:
            time.sleep(.1)
        print("Done." if app.job.ok else "The write did not go through. Nothing was lost: see the backup in the page.")
    except KeyboardInterrupt:
        print("\nLeaving. The write is still running. Do not unplug the iPod until it has finished.")


if __name__ == "__main__":
    main()
