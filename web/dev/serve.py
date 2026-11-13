#!/usr/bin/env python3
"""The web page on a pretend iPod, for working on it without hardware. Nothing real is touched:

    python3 web/dev/serve.py                     400 songs, http://127.0.0.1:8799
    python3 web/dev/serve.py --songs 10000
    python3 web/dev/serve.py --scenario messy    long names, unicode, gaps, albums to group
    python3 web/dev/serve.py --scenario empty    an iPod with no songs
    python3 web/dev/serve.py --library ~/.repod/library.json
                                                          a copy of a real library, read once and never written back

- `ipodctl` is `fake_ipodctl.py`; state lives in --state, never in ~/.repod.
- `diskutil`, `osascript` and `open` are the shims in dev/bin, so no disk is ejected and no window opens on the Mac.
- The internet is replaced by canned answers, so no fake album goes to MusicBrainz, iTunes or Deezer.

To make the device misbehave while it runs, write words to <state>/ipod/fault (see fake_ipodctl.py),
or `offline` to make the internet go away. <state>/ipod/choose is what the file dialog "picks".
"""
import argparse
import json
import os
import random
import re
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from io import BytesIO
from pathlib import Path

DEV = Path(__file__).resolve().parent


class Answer(BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def noise_png(seed: str, size: int = 320) -> bytes:
    """Big enough that `download_image` takes it (it refuses anything under 2 KB)."""
    tone = random.Random(seed)
    rows = b"".join(b"\0" + bytes(tone.randrange(90, 200) for _ in range(size * 3)) for _ in range(size))
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(rows, 1)) + chunk(b"IEND", b"")


def words_of(query: str) -> str:
    found = re.findall(r'"((?:[^"\\]|\\.)*)"', query)
    return (found[0] if found else query).replace("\\", "")


def canned(url: str) -> bytes:
    """What the services would answer, just enough of it for every path through the page."""
    parts = urllib.parse.urlparse(url)
    query = dict(urllib.parse.parse_qsl(parts.query))
    if parts.netloc == "coverartarchive.org" or parts.netloc.endswith(("fake.test",)) or "Special:FilePath" in parts.path:
        return noise_png(url)
    if parts.netloc == "itunes.apple.com":
        term = query.get("term", "")
        if "nothing" in term.lower():
            return b'{"results": []}'
        return json.dumps({"results": [{"collectionId": 1, "collectionName": term, "artistName": term.split(" ")[0],
                                        "artworkUrl100": "https://covers.fake.test/itunes/100x100bb.jpg"}]}).encode()
    if parts.netloc == "api.deezer.com":
        if "artist" in parts.path:
            return json.dumps({"data": [{"name": query.get("q", ""), "nb_fan": 10, "picture_xl": f"https://covers.fake.test/artist/{query.get('q')}.png",
                                         "picture_medium": f"https://covers.fake.test/artist/{query.get('q')}-m.png"}]}).encode()
        return json.dumps({"data": [{"id": 2, "title": query.get("q", ""), "artist": {"name": query.get("q", "").split(" ")[0]},
                                     "cover_xl": "https://covers.fake.test/deezer/xl.png", "cover_medium": "https://covers.fake.test/deezer/m.png"}]}).encode()
    if parts.netloc == "musicbrainz.org":
        path = parts.path.replace("/ws/2/", "")
        text = words_of(query.get("query", ""))
        if path == "release-group/":
            return json.dumps({"release-groups": [{"id": "1111", "title": text, "artist-credit": [{"name": text.split(" ")[0]}]}]}).encode()
        if path == "release/":
            return json.dumps({"releases": [{"id": "2222", "title": text, "status": "Official", "track-count": 10, "artist-credit": [{"name": "Fake Artist", "artist": {"id": "3333", "name": "Fake Artist"}}]}]}).encode()
        if path.startswith("release/"):
            return json.dumps({"title": "Fake Release", "date": "1999-01-01", "artist-credit": [{"name": "Fake Artist"}], "release-group": {"first-release-date": "1999"},
                               "media": [{"position": 1, "track-count": 3, "tracks": [{"title": f"Song {n}", "position": n, "recording": {"id": f"r{n}", "title": f"Song {n}"}} for n in (1, 2, 3)]}]}).encode()
        if path == "recording/":
            if "nothing" in text.lower() or text.startswith(("rid:", "isrc:")):
                return json.dumps({"recordings": []}).encode()
            def release(rid, album, kind, year, number, count):
                return {"id": rid, "title": album, "status": "Official", "date": f"{year}-03-01", "country": "GB", "count": 1,
                        "release-group": {"id": f"g{rid}", "primary-type": kind}, "artist-credit": [{"name": "Fake Artist"}],
                        "media": [{"position": 1, "format": "CD", "track-count": count, "track-offset": number - 1,
                                   "track": [{"id": f"t{rid}{number}", "number": str(number), "title": text, "length": 201000}]}]}
            number = int(m[0]) if (m := re.search(r"\d+$", text)) else 1
            return json.dumps({"recordings": [
                {"id": f"rec-{text}", "title": text, "length": 201000, "artist-credit": [{"name": "Fake Artist"}],
                 "releases": [release("2222", "Fake Release", "Album", 1999, number, 3), release("4444", "Fake Hits", "Compilation", 2005, number + 4, 18)]},
                {"id": f"live-{text}", "title": f"{text} (live)", "length": 260000, "artist-credit": [{"name": "Fake Artist"}],
                 "releases": [release("5555", "Fake Live", "Album", 2001, number, 9)]}]}).encode()
        if path == "artist/":
            return json.dumps({"artists": [{"id": "3333", "name": query.get("query", ""), "type": "Group"}]}).encode()
        if path.startswith("artist/"):
            return json.dumps({"name": "Fake Artist", "type": "Group", "country": "IS", "life-span": {"begin": "1994"}, "disambiguation": "made up for testing", "tags": [{"name": "testing", "count": 1}], "relations": []}).encode()
    raise urllib.error.HTTPError(url, 404, "not found", {}, None)


def fake_urlopen(request, timeout=None):
    url = request.full_url if hasattr(request, "full_url") else request
    if "offline" in (Path(os.environ["FAKE_IPOD"]) / "fault").read_text().split() if (Path(os.environ["FAKE_IPOD"]) / "fault").exists() else False:
        raise urllib.error.URLError("the network is unreachable")
    time.sleep(.02)
    return Answer(canned(url))


def prepare(state: Path, songs: int, scenario: str, keep: bool) -> None:
    """The environment of everything below. It has to be set before `enrich` is imported, which reads it once."""
    os.environ.update(REPOD_HOME=str(state / "home"), IPODCTL=str(DEV / "fake_ipodctl.py"), FAKE_IPOD=str(state / "ipod"), REPOD_BACKUPS=str(state / "ipod/backups"),
                      PATH=f"{DEV / 'bin'}{os.pathsep}{os.environ['PATH']}")
    if not keep or not (state / "ipod/library.json").exists():
        subprocess.run([sys.executable, str(DEV / "fake_ipodctl.py"), "init", "--songs", str(songs), "--scenario", scenario], check=True)


def seed(state: Path, dump: Path) -> None:
    """A real `ipodctl dump` as the pretend iPod: its names and numbers, with the fake's own silent audio."""
    source = json.loads(dump.read_text())
    tracks = [{**t, "path": t.get("path") or "", "albumArtist": t.get("albumArtist")} for t in source["tracks"]]
    covers = {f"{t.get('albumArtist') or t.get('artist') or ''} | {t.get('album') or ''}": 1 for t in tracks if t.get("album")}
    (state / "ipod/library.json").write_text(json.dumps({"name": source.get("deviceName") or "A copy of an iPod", "tracks": tracks, "covers": covers,
                                                         "outOfPlace": [[a["album"], a["albumArtist"]] for a in source.get("albumsOutOfPlace") or []]}))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8799)
    parser.add_argument("--songs", type=int, default=400)
    parser.add_argument("--scenario", choices=("typical", "messy", "empty"), default="typical")
    parser.add_argument("--state", default=str(Path(tempfile.gettempdir()) / "repod-fake-ipod"))
    parser.add_argument("--keep", action="store_true", help="carry on with the pretend iPod of the last run")
    parser.add_argument("--developer", action="store_true")
    parser.add_argument("--library", type=Path, help="start from this dump of a real library; it is only read")
    options = parser.parse_args()
    state = Path(options.state)
    state.mkdir(parents=True, exist_ok=True)
    prepare(state, options.songs, options.scenario, options.keep)
    if options.library:
        seed(state, options.library.expanduser())
    sys.path.insert(0, str(DEV.parent))
    urllib.request.urlopen = fake_urlopen
    import web
    sys.argv = ["web.py", "--port", str(options.port), "--volume", str(state / "ipod/mount")] + (["--developer"] if options.developer else [])
    web.main()


if __name__ == "__main__":
    main()
