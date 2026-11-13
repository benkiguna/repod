#!/usr/bin/env python3
"""A stand-in for `ipodctl`, so the web page can be driven without an iPod. It keeps a pretend library in
a JSON file and never looks at a real device: the commands, the arguments and what they print follow
Sources/ipodctl, so `web.py` cannot tell the difference.

    FAKE_IPOD=<folder> fake_ipodctl.py init --songs 500 [--scenario typical|messy|empty]

`dev/serve.py` does all of this for you. Without FAKE_IPOD it refuses to run.

The folder holds:
    library.json    the songs, which albums have a cover, which albums are out of place
    backups/        one JSON file per backup
    mount/          the pretend volume, with iPod_Control/Music in it
    fault           words that make the next commands misbehave, one or more of:
                      unplugged     every command fails: no iPod found
                      other-ipod    the backups on file are another iPod's
                      music         a write fails: Music is running
                      read-only     a write fails: the iPod opens read-only
                      write-fails   a write fails halfway, and changes nothing
                      cover-warn    a write of covers goes through, but one cover did not go in
                      cover-skip    `apply` skips every cover
                      slow          every command takes a few seconds
"""
import json
import os
import random
import re
import shutil
import struct
import sys
import time
import wave
import zlib
from datetime import datetime, timezone
from pathlib import Path

if not os.environ.get("FAKE_IPOD"):
    sys.exit("error: FAKE_IPOD is not set; this is the test stand-in for ipodctl, not the real one")
ROOT = Path(os.environ["FAKE_IPOD"])
LIBRARY = ROOT / "library.json"
BACKUPS = ROOT / "backups"
MOUNT = ROOT / "mount"
DEVICE = "000A27001FAKE"  # the store keeps each iPod's backups in its own folder
FILES = 8  # songs share a few short files: a big library must not cost a big folder


class Refused(Exception):
    pass


DATABASE = MOUNT / "iPod_Control/iTunes/iTunesDB"


def faults() -> set:
    file = ROOT / "fault"
    return set(file.read_text().split()) if file.exists() else set()


def wait(seconds: float) -> None:
    time.sleep(seconds * (6 if "slow" in faults() else 1))


def read() -> dict:
    return json.loads(LIBRARY.read_text())


def save(library: dict) -> None:
    temporary = LIBRARY.with_suffix(".tmp")
    temporary.write_text(json.dumps(library))
    temporary.replace(LIBRARY)


def owner(track: dict) -> str:
    return track.get("albumArtist") or track.get("artist") or ""


def key(track: dict) -> tuple:
    return (track.get("album") or "", owner(track))


def groups(library: dict) -> dict:
    found = {}
    for track in library["tracks"]:
        found.setdefault(key(track), []).append(track)
    return found


def has_cover(library: dict, album: tuple) -> bool:
    return f"{album[1]} | {album[0]}" in library["covers"]


def flag(args: list, name: str):
    return args[args.index(name) + 1] if name in args and args.index(name) + 1 < len(args) else None


def positional(args: list) -> list:
    """What `Invocation.positional` gives: not a flag, and not --volume's value."""
    found, skip = [], False
    for a in args:
        if skip:
            skip = False
        elif a.startswith("--"):
            skip = a in ("--volume", "--note", "--out", "--export", "--id", "--manifest", "--art-dir")
        else:
            found.append(a)
    return found


def check_device() -> None:
    if "unplugged" in faults() or not (MOUNT / "iPod_Control").is_dir():
        raise Refused("no iPod found")
    if not DATABASE.exists():  # as `IPodDevice.open`: a reset iPod has no library, and nothing opens it
        raise Refused(f"path is missing: {DATABASE}")


def write(library: dict, note: str, steps: list) -> None:
    """The steps of a real write, with their progress, and the same way of failing."""
    bad = faults()
    if "read-only" in bad:
        raise Refused("read-only: volume mounted read-only - the database was not written")
    if "music" in bad:
        raise Refused("Music is running - the database was not written")
    print("  Saving a backup of the library", flush=True)
    wait(.05)
    snapshot = capture(note)
    if "write-fails" in bad:
        raise Refused("the iPod stopped answering while the database was written - the database was not written")
    for number, step in enumerate(steps, 1):
        print(f"  [{number}/{len(steps)}] {step}", flush=True)
        wait(.04)
    save(library)
    print(f"\n  Written. Backup: {snapshot}")
    print(f"  Database size: {len(json.dumps(library))} B")
    print("  Order intent saved.")


def capture(note: str) -> str:
    BACKUPS.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    number, name = 1, stamp
    while (BACKUPS / DEVICE / name).exists():
        number, name = number + 1, f"{stamp}-{number:02}"
    (BACKUPS / DEVICE / name).mkdir(parents=True)
    (BACKUPS / DEVICE / name / "manifest.json").write_text(json.dumps({"note": note, "library": read()}))
    (BACKUPS / DEVICE / name / "files/iTunes").mkdir(parents=True)
    shutil.copyfile(DATABASE, BACKUPS / DEVICE / name / "files/iTunes/iTunesDB")
    return name


def backup_size(path: Path) -> str:
    return f"{path.stat().st_size / 1_000_000:.1f} MB"


# -- reading -----------------------------------------------------------------------------------

def dump(args):
    check_device()
    wait(.1)
    library = read()
    out = {"version": 1, "deviceName": library["name"], "tracks": library["tracks"],
           "albumsOutOfPlace": [{"album": a, "albumArtist": b} for a, b in library["outOfPlace"]]}
    text = json.dumps(out)
    Path(flag(args, "--out")).write_text(text)
    print(f"Wrote {len(text)} B to {flag(args, '--out')}")


def artwork(args):
    check_device()
    library = read()
    if "--missing" in args:
        entries = []
        for album, tracks in groups(library).items():
            if not has_cover(library, album):
                entries.append({"album": album[0], "albumArtist": album[1], "artist": tracks[0].get("artist"), "genre": tracks[0].get("genre"),
                                "year": next((t["year"] for t in tracks if t["year"]), 0), "trackCount": len(tracks)})
        entries.sort(key=lambda e: (e["albumArtist"], e["album"]))
        Path(flag(args, "--out")).write_text(json.dumps(entries))
        print(f"{len(entries)} albums without artwork -> {flag(args, '--out')}")
    elif "--export" in args:
        folder = Path(flag(args, "--export"))
        folder.mkdir(parents=True, exist_ok=True)
        entries = []
        for album in sorted(a for a in groups(library) if has_cover(library, a)):
            file = f"{len(entries)}.png"
            (folder / file).write_bytes(png(f"{album[1]} | {album[0]}" + str(library["covers"][f"{album[1]} | {album[0]}"])))
            entries.append({"album": album[0], "albumArtist": album[1], "file": file})
        (folder / "index.json").write_text(json.dumps(entries))
        print(f"{len(entries)} covers exported to {folder}")


def png(seed: str, size: int = 96) -> bytes:
    """A small flat cover, its colour taken from its name."""
    tone = random.Random(seed)
    a, b = [tone.randrange(40, 220) for _ in range(3)], [tone.randrange(40, 220) for _ in range(3)]
    rows = b"".join(b"\0" + bytes(c for x in range(size) for c in (a if (x // 24 + y // 24) % 2 else b)) for y in range(size))
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    header = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


# -- writing -----------------------------------------------------------------------------------

def report(title: str, rows: list) -> None:
    width = max(len(r[0]) for r in rows)
    print(title)
    for label, value in rows:
        print(f"  {label.ljust(width)}  {value}")


def writing() -> str:
    return "BLOCKED: volume mounted read-only" if "read-only" in faults() else "allowed"


def apply(args):
    manifest = json.loads(Path(positional(args)[0]).read_text())
    check_device()
    library = read()
    albums = groups(library)
    queued, skipped = [], []
    for cover in manifest.get("covers") or []:
        album, artist = cover["album"], cover.get("albumArtist")
        label = f"{album} / {artist}" if artist else album
        if "cover-skip" in faults():
            skipped.append(f"{label}: cannot load the image {cover['file']}")
        elif (album, artist) not in albums:
            skipped.append(f"{label}: no such album")
        elif has_cover(library, (album, artist)) and not cover.get("replace"):
            skipped.append(f"{label}: already has artwork (on '{albums[(album, artist)][0]['title']}')")
        elif not Path(cover["file"]).is_file():
            skipped.append(f"{label}: cannot load the image {cover['file']}")
        else:
            queued.append(cover)
    report("Apply", [("iPod", f"{library['name']} - iPod classic"), ("covers to write", str(len(queued))),
                     ("skipped", str(len(skipped))), ("writing", writing())])
    for cover in queued:
        print(f"  + {cover['album']} / {cover.get('albumArtist')} - {len(albums[(cover['album'], cover.get('albumArtist'))])} songs, 600x600 from {Path(cover['file']).name}")
    for line in skipped:
        print(f"  - SKIPPED {line}")
    if not queued:
        print("\n  Nothing to write.")
        return
    if "--apply" not in args:
        print("\n  That was a preview. To write: add --apply")
        return
    print("")
    for cover in queued:
        name = f"{cover.get('albumArtist')} | {cover['album']}"
        library["covers"][name] = library["covers"].get(name, 0) + 1
    write(library, f"ipodctl apply {Path(positional(args)[0]).name}", [c["album"] for c in queued])
    if "cover-warn" in faults():
        print("  WARNING: a cover could not be written: the image is too small")


FIELDS = {"title": "title", "artist": "artist", "albumartist": "albumArtist", "album": "album", "genre": "genre", "composer": "composer",
          "year": "year", "track": "trackNumber", "tracks": "totalTracks", "disc": "discNumber", "discs": "totalDiscs", "compilation": "compilation"}


def retag(args):
    manifest = json.loads(Path(positional(args)[0]).read_text())
    check_device()
    library = read()
    by_id = {t["uniqueID"]: t for t in library["tracks"]}
    lines, skipped, unchanged, seen = [], [], 0, set()
    for song in manifest["songs"]:
        track = by_id.get(song["id"])
        if track is None:
            skipped.append(f"song {song['id']}: not on the iPod")
            continue
        if song["id"] in seen:
            skipped.append(f"song {song['id']}: listed twice")
            continue
        changes = [(k, track.get(FIELDS[k]), v) for k, v in song["set"].items() if (track.get(FIELDS[k]) or (0 if isinstance(v, int) else "")) != v]
        if not changes:
            unchanged += 1
            continue
        seen.add(song["id"])
        lines.append(f"{track['title']} - " + "; ".join(f"{k}: '{a}' -> '{b}'" for k, a, b in changes))
        for name, _, value in changes:
            track[FIELDS[name]] = value
    report("Retag", [("iPod", f"{library['name']} - iPod classic"), ("songs to change", str(len(lines))), ("already as asked", str(unchanged)),
                     ("skipped", str(len(skipped))), ("writing", writing())])
    for line in lines[:40]:
        print(f"  + {line}")
    if len(lines) > 40:
        print(f"  ... and {len(lines) - 40} more")
    for line in skipped:
        print(f"  - SKIPPED {line}")
    if not lines:
        print("\n  Nothing to write.")
        return
    if "--apply" not in args:
        print("\n  That was a preview. To write: add --apply")
        return
    print("")
    write(library, f"ipodctl retag ({len(lines)} songs)", [])


def edit(args):
    check_device()
    library = read()
    wanted = {int(n) for n in flag(args, "--id").split(",")}
    changes = {}
    for i, a in enumerate(args):
        if a == "--set":
            name, value = args[i + 1].split("=", 1)
            changes[FIELDS[name]] = value
    affected = [t for t in library["tracks"] if t["uniqueID"] in wanted]
    if len(affected) != len(wanted):
        raise Refused(f"{len(wanted) - len(affected)} of the songs are not in the library")
    report("Editing songs", [("songs", str(len(affected)))])
    if "--apply" not in args:
        print("\n  That was a preview. To write: add --apply")
        return
    for track in affected:
        track.update(changes)
    write(library, f"ipodctl edit ({len(affected)} songs)", [])


def regroup(args):
    check_device()
    library = read()
    if not library["outOfPlace"]:
        print("Every album matches its songs. Nothing to do.")
        return
    report("Regroup", [("iPod", f"{library['name']} - iPod classic"), ("albums to rebuild", str(len(library["outOfPlace"]))), ("writing", writing())])
    for album, artist in library["outOfPlace"][:40]:
        print(f"  + {album} / {artist}")
    if "--apply" not in args:
        print("\n  That was a preview. To write: add --apply")
        return
    print("")
    count = len(library["outOfPlace"])
    library["outOfPlace"] = []
    write(library, f"ipodctl regroup ({count} albums)", [])


def remove(args):
    check_device()
    library = read()
    wanted = {int(n) for n in flag(args, "--id").split(",")}
    gone = [t for t in library["tracks"] if t["uniqueID"] in wanted]
    report("Removing tracks", [("tracks", f"{len(gone)} of {len(library['tracks'])}"), ("files to delete", str(len(gone)))])
    for track in gone[:8]:
        print(f"      {track['title']}")
    if "--apply" not in args:
        print("\n  That was a preview. To write: add --apply")
        return
    library["tracks"] = [t for t in library["tracks"] if t["uniqueID"] not in wanted]
    write(library, f"removing {len(gone)} tracks", [t["title"] for t in gone[:20]])
    print(f"  Deleted files: {len(gone)}")


def tags_of(file: Path) -> tuple:
    """What a real file's tags would say: "03 Song" in a folder is an album, "Artist - Song" is a loose single, and anything else has none."""
    numbered = re.match(r"(\d+) (.+)", file.stem)
    if numbered:
        return numbered[2], "Fake Artist", file.parent.name, int(numbered[1])
    artist, _, title = file.stem.rpartition(" - ")
    return title, artist, "", 0


def diagnose(args):
    check_device()
    print(f"iPod classic at {MOUNT} - policy standard")
    print("\nlibrary")
    print("  OK   rebuild              rebuilt in memory: byte-identical  [required]")
    print("\nartwork")
    print("  WARN artwork              12 images · formats 1028 not measured · new artwork disabled  [advisory]")
    print("\ndisk")
    print("  OK   music                Music is not running  [advisory]")
    print("\nAll required checks passed.")
    print("\nmeasurements\n  sysinfo: FamilyID 6")


def audio(args):
    check_device()
    library = read()
    bad = sorted((t for t in library["tracks"] if t.get("hiRes")), key=lambda t: t["album"])
    report("File formats", [("the iPod plays", str(len(library["tracks"]) - len(bad))), ("the iPod does not play", str(len(bad))), ("unreadable", "0")])
    for track in bad:
        print(f"  id={track['uniqueID']}  {track['album']} / {track['title']}")
        print("      ALAC 24-bit 48 kHz: 24-bit Apple Lossless; the iPod plays 16-bit up to 48 kHz -> Apple Lossless 16-bit 44.1 kHz")
    if not bad:
        return
    wanted = {int(n) for n in (flag(args, "--fix") or "").split(",") if n}
    if not wanted:
        print("\n  To fix: --fix ID[,ID] or --fix-all (add --apply to write).")
        return
    chosen = [t for t in bad if t["uniqueID"] in wanted]
    if not chosen:
        raise Refused("none of the given ids needs conversion")
    print(f"\nTo transcode: {len(chosen)}")
    keep = flag(args, "--keep-originals")
    if not keep and "--discard-originals" not in args:
        print("\n  Where should the originals go? A 24 -> 16 bit conversion cannot be undone.")
        return
    if "--apply" not in args:
        print("  That was a preview. To write: add --apply")
        return
    for track in chosen:
        if keep:
            (Path(keep) / f"{track['uniqueID']}.m4a").write_bytes(b"original")
        track["hiRes"] = False
        track["bitRate"] = 900
    write(library, f"ipodctl audio --fix ({len(chosen)})", [t["title"] for t in chosen])


def fix_sort(args):
    check_device()
    library = read()
    albums = {}
    for track in library["tracks"]:
        albums.setdefault(track["album"], []).append(track)
    split = {a: v for a, v in albums.items() if a and len({t.get("sortAlbum") for t in v}) > 1}
    if not split:
        print("No sort fields split any albums. Nothing to repair.")
        return
    count = sum(len(v) for v in split.values())
    report("Sort field repair", [("albums", str(len(split))), ("tracks", str(count)), ("field changes", str(count))])
    for album, tracks in split.items():
        print(f"\n  {album}")
        for track in tracks:
            print(f"      {track['title']}\n          sort album: -> (empty)")
    if "--apply" not in args:
        print("\n  That was a preview. To write: ipodctl fix-sort --apply")
        return
    for tracks in split.values():
        for track in tracks:
            track["sortAlbum"] = None
    write(library, f"sort field repair in {len(split)} albums", [])


def info(args):
    check_device()
    blocked = "read-only" in faults()
    report("iPod", [("model", "iPod classic"), ("writing", "BLOCKED - this model has not been confirmed" if blocked else "allowed")])


def add(args):
    source = Path(positional(args)[0])
    files = [f for f in ([source] if source.is_file() else sorted(source.rglob("*"))) if f.suffix.lower() in (".mp3", ".m4a", ".wav", ".aif", ".flac")]
    if "--json" in args:  # what the files say; no iPod needed, as in the real one
        art = flag(args, "--art-dir")
        listed = []
        for file in files:
            title, artist, album, track = tags_of(file)
            entry = {"path": str(file), "title": title, "artist": artist, "albumartist": artist if album else "", "album": album, "genre": "", "composer": "",
                     "year": 0, "track": track, "tracks": 0, "disc": 0, "discs": 0, "compilation": False, "seconds": 200.0,
                     "format": "FLAC 44.1 kHz" if file.suffix.lower() == ".flac" else "MP3 192 kbps", "cover": "none"}
            if file.suffix.lower() == ".flac":
                entry["convert"] = "Apple Lossless 16-bit 44.1 kHz"
            cover = file.parent / "cover.png"
            if cover.exists():
                entry.update(cover="folder", art=str(cover))
            elif art and "art" in file.stem.lower():  # a file named so carries a picture in its tags
                Path(art).mkdir(parents=True, exist_ok=True)
                (Path(art) / "1.png").write_bytes(png(file.stem))
                entry.update(cover="tags", art=str(Path(art) / "1.png"))
            listed.append(entry)
        print(json.dumps({"files": listed, "problems": []}))
        return
    check_device()
    library = read()
    if not files:
        raise Refused("no audio files found")
    tagged = [dict(zip(("title", "artist", "album", "track"), tags_of(file)), path=str(file)) for file in files]
    for entry in tagged:
        entry["albumartist"] = entry["artist"] if entry["album"] else ""
    manifest = flag(args, "--manifest")
    if manifest:
        choices = {c["path"]: c for c in json.loads(Path(manifest).read_text())["files"]}
        kept = []
        for entry in tagged:
            choice = choices.get(entry["path"], {})
            if choice.get("skip"):
                print(f"  SKIPPED: {entry['title']}: left out when reviewed")
                continue
            if choice.get("cover") and not Path(choice["cover"]).is_file():
                raise Refused(f"the cover {choice['cover']} is not there")
            entry.update({k: v for k, v in (choice.get("set") or {}).items() if not (k == "title" and not v)})
            kept.append(entry)
        tagged = kept
        if not tagged:
            raise Refused("every file was left out")
    print(f"Files found: {len(tagged)}")
    for entry in tagged:
        track = entry["track"]
        print(f"    {f'{track:02} ' if track else '   '}{entry['title']}\n      {entry['artist'] or '?'} / {entry['album'] or '?'}  MP3 192 kbps  1 MB")
    report("Invariants", [("tracks", f"{len(library['tracks'])} -> {len(library['tracks']) + len(tagged)}")])
    if "--apply" not in args:
        print("\n  That was a preview. To write: add --apply")
        return
    print(f"\nWriting to: iPod classic at {MOUNT}")
    top = max([t["uniqueID"] for t in library["tracks"]] or [100])
    for offset, entry in enumerate(tagged, 1):
        added = song(top + offset, entry["title"], entry["artist"], entry["album"], entry["albumartist"], entry["track"], entry.get("year", 0), 200, entry.get("genre", ""),
                     entry.get("compilation", False))
        added.update(totalTracks=entry.get("tracks", 0), discNumber=entry.get("disc", 0), totalDiscs=entry.get("discs", 0))
        library["tracks"].append(added)
    write(library, "ipodctl add", [t["title"] for t in tagged])
    print(f"  Added tracks: {len(tagged)}")


def backup(args):
    check_device()
    name = capture(flag(args, "--note") or "")
    report("Backup created", [("identifier", name), ("files", "12, of which 11 linked to older backups"), ("size", "1.8 MB, added on disk 0.1 MB")])


def snapshots(args):
    check_device()
    found = sorted(BACKUPS.glob(f"{DEVICE}/*/manifest.json"), reverse=True) if BACKUPS.exists() and "other-ipod" not in faults() else []
    if not found:
        print("No backups for fake.")
    for file in found:
        note = json.loads(file.read_text())["note"]
        print(f"{file.parent.name}  12 files  {backup_size(file)}" + (f"  {note}" if note else ""))


def verify(args):
    check_device()
    name = positional(args)[0]
    if not (BACKUPS / DEVICE / name / "manifest.json").exists():
        raise Refused(f"no such backup {name}")
    wait(.05)
    print(f"Backup {name} is consistent - 12 files match their checksums.")


def restore(args):
    check_device()
    name = positional(args)[0]
    file = BACKUPS / DEVICE / name / "manifest.json"
    if not file.exists():
        raise Refused(f"no such backup {name}")
    before = capture("before a restore")
    save(json.loads(file.read_text())["library"])
    shutil.copyfile(file.parent / "files/iTunes/iTunesDB", DATABASE)
    print(f"Restored backup {name} - 12 files.")
    print(f"  State from before the restore: backup {before}")


# -- a pretend library -------------------------------------------------------------------------

def song(number, title, artist, album, album_artist, track, year, seconds, genre, compilation=False):
    return {"uniqueID": number, "title": title, "artist": artist, "albumArtist": album_artist, "album": album, "genre": genre, "composer": None,
            "trackNumber": track, "totalTracks": 0, "discNumber": 0, "totalDiscs": 0, "year": year, "durationSeconds": float(seconds),
            "sizeBytes": seconds * 24000, "bitRate": random.choice((192, 256, 320)), "sampleRate": 44100, "playCount": 0, "rating": 0,
            "compilation": compilation, "path": f"iPod_Control/Music/F00/SONG{number % FILES}.wav"}


WORDS = "Blue Night River Glass Summer Paper Echo Silver Hollow Wild Golden Northern Quiet Electric Velvet Broken Lost Distant Bright Slow".split()
NOUNS = "Lights Roads Dreams Hearts Waves Rooms Stars Songs Horses Letters Fields Mirrors Cities Machines Shadows Gardens".split()
PEOPLE = ["The %s %s" % (a, b) for a in WORDS[:10] for b in NOUNS[:6]] + ["Sigur Rós", "Björk", "J. S. Bach", "Nina Simone", "坂本龍一", "Мумий Тролль", "فيروز"]
MESSY = [  # what real libraries hold
    ("A very long song title that goes on and on, with a subtitle, a remix credit and a live recording note (Live at the Royal Albert Hall, London, 14 February 1998) [Remastered 2011 Deluxe Edition]",
     "Somebody Who Has An Extraordinarily Long Name And Also Several Friends, feat. Another Person & The Whole Orchestra", "An Album With A Name So Long That No Table Column Could Ever Hope To Show All Of It At Once, Volume Two", "Somebody Who Has An Extraordinarily Long Name And Also Several Friends"),
    ("Hoppípolla", "Sigur Rós", "Takk…", "Sigur Rós"), ("Jóga", "Björk", "Homogenic", "Björk"),
    ("戦場のメリークリスマス", "坂本龍一", "Merry Christmas Mr. Lawrence", "坂本龍一"), ("Зимняя", "Мумий Тролль", "Морская", "Мумий Тролль"),
    ("كيفك إنت", "فيروز", "كيفك إنت", "فيروز"), ("🎵 Emoji 🌈 Song", "😀 Artist", "🎉 Album", "😀 Artist"),
    ("<b>Bold</b> & <script>alert(1)</script>", "AT&T \"Quoted\" 'Band'", "Rock & Roll | Part \"2\"", "AT&T \"Quoted\" 'Band'"),
    ("No album, no artist", "", "", ""), ("", "", "", ""), ("Untagged - Song", "", "", ""),
    ("ßpecial Letter First", "Ångström", "Éclair", "Ångström"), ("99 Luftballons", "Nena", "99 Luftballons", "Nena"),
]


def init(args):
    count = int(flag(args, "--songs") or 400)
    scenario = flag(args, "--scenario") or "typical"
    random.seed(7)
    shutil.rmtree(ROOT, ignore_errors=True)
    music = MOUNT / "iPod_Control/Music/F00"
    music.mkdir(parents=True)
    for number in range(FILES):
        with wave.open(str(music / f"SONG{number}.wav"), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(8000)
            audio.writeframes(b"\0\0" * 8000 * 30)  # thirty seconds of silence
    tracks, covers, number = [], {}, 100
    if scenario != "empty":
        while len(tracks) < count:
            artist = random.choice(PEOPLE)
            album = f"{random.choice(WORDS)} {random.choice(NOUNS)}"
            year = random.choice((0, 1977, 1984, 1991, 1999, 2004, 2011, 2019))
            genre = random.choice(("Rock", "Jazz", "Electronic", "Classical", "Hindi", ""))
            for position in range(1, random.randint(6, 14)):
                number += 1
                tracks.append(song(number, f"{random.choice(WORDS)} {random.choice(NOUNS)}", artist, album, artist, position, year, random.randint(90, 420), genre))
            if random.random() < .8:
                covers[f"{artist} | {album}"] = 1
        tracks = tracks[:count]
    out_of_place = []
    if scenario == "messy":
        for title, artist, album, album_artist in MESSY:
            for position in range(1, 3 if title.startswith("A very") else 2):
                number += 1
                tracks.append(song(number, title, artist, album, album_artist, position, 0, 245, ""))
        for artist in ("Lata Mangeshkar", "Kishore Kumar", "Lata Mangeshkar, Kishore Kumar"):  # one album under three names: Grouping
            number += 1
            tracks.append(song(number, f"Duet with {artist}", artist, "Aandhi", artist, 1, 1975, 200, "Hindi"))
        for title in ("One", "Two"):  # an album artist on none of the songs: may show twice
            number += 1
            tracks.append(song(number, title, "Session Player", "Soundtrack Sampler", "Label Records", 0, 2001, 180, ""))
        out_of_place = [["Morning Glass", "The Blue Lights"]]
        for album, artist, disc in (("Double Feature", "Nena", 1), ("Double Feature CD2", "Nena", 2)):  # one album, one name for each disc
            for position in (1, 2):
                number += 1
                tracks.append(song(number, f"Disc {disc} part {position}", artist, album, artist, position, 1984, 200, ""))
        for album, artist in (("transfer", "Nena"), ("https://music.example.com", "Nena")):  # a folder for an album
            number += 1
            tracks.append(song(number, f"From {album}", artist, album, artist, 0, 0, 180, ""))
        for artist in ("Bjork Guest", "BJORK GUEST"):  # one artist, two spellings
            number += 1
            tracks.append(song(number, f"Sung by {artist}", artist, f"Guest {artist}", artist, 1, 0, 210, ""))
        for title in ("Stray Band - First Song (Official Video)", "Stray Band - Second Song", "Lonely Singer - Only Song"):  # a download: both in the title
            number += 1
            tracks.append(song(number, title, "", "", "", 0, 0, 230, ""))
        for position, title in enumerate(("Master One", "Master Two"), 1):  # 24-bit: this iPod can't play them
            number += 1
            tracks.append({**song(number, title, "Nena", "Studio Master", "Nena", position, 2001, 200, ""), "hiRes": True, "bitRate": 2000})
        for position, sort in enumerate(("Split", "split "), 1):  # one album, two hidden sort names: two covers on the iPod
            number += 1
            tracks.append({**song(number, f"Sorted {position}", "Nena", "Sort Split", "Nena", position, 2001, 200, ""), "sortAlbum": sort})
        best = [t for t in tracks if t["album"] and t["albumArtist"] == tracks[0]["albumArtist"]][:3] or tracks[:3]
        for position, source in enumerate(best, 1):  # a best-of: every song is also on its album
            number += 1
            tracks.append({**source, "uniqueID": number, "album": "The Best Of", "trackNumber": position, "bitRate": 128, "sizeBytes": source["sizeBytes"] // 2})
        for source in best[:1]:  # one song twice on one album
            number += 1
            tracks.append({**source, "uniqueID": number})
    (ROOT / "backups").mkdir(exist_ok=True)
    DATABASE.parent.mkdir(parents=True)
    DATABASE.write_bytes(b"mhbd")
    LIBRARY.write_text(json.dumps({"name": "Fake iPod classic", "tracks": tracks, "covers": covers, "outOfPlace": out_of_place}))
    print(f"{len(tracks)} songs in {ROOT}")


COMMANDS = {"init": init, "dump": dump, "artwork": artwork, "apply": apply, "retag": retag, "edit": edit, "regroup": regroup, "remove": remove,
            "add": add, "info": info, "diagnose": diagnose, "audio": audio, "fix-sort": fix_sort, "backup": backup, "snapshots": snapshots, "verify": verify, "restore": restore}


def main() -> None:
    name = sys.argv[1] if len(sys.argv) > 1 else ""
    if name not in COMMANDS:
        sys.exit(f"error: unknown command '{name}'")
    if name != "init" and "--developer" in sys.argv:
        print("DEVELOPER MODE: writing outside the confirmed models (iPod classic) - a backup comes first")
    try:
        COMMANDS[name]([a for a in sys.argv[2:] if a != "--keep-backups"])  # the real one's retention switch: this one keeps every backup
    except Refused as error:
        print(f"error: {error}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
