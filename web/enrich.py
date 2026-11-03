#!/usr/bin/env python3
"""Looks up album covers for the albums on an iPod that have none, and writes a manifest
for `ipodctl apply`. It never touches the iPod itself.

    enrich.py scan                  find albums without a cover, look them up, write the manifest
    enrich.py review                what the last scan decided, album by album
    enrich.py accept "Artist | Album"   include a reviewed match in the manifest
    enrich.py skip   "Artist | Album"   never propose this album again
    enrich.py pin    "Artist | Album" (--mbid ID | --url URL | --file PATH)
                                        use this exact release, image URL or local file

Then, with the iPod connected and Music closed:

    ipodctl apply ~/.repod/manifest.json --unverified-formats --apply

State lives in ~/.repod (override with REPOD_HOME): the cache of API answers and
images, decisions.json (skip / accept / pin), review.json and manifest.json. Running scan again
after adding songs only looks at albums still missing a cover; lookups are cached.

Matches are scored on title and artist similarity. Only a confident match goes into the
manifest on its own; the rest are listed for review and wait for `accept`.
"""
from __future__ import annotations
import argparse
import difflib
import hashlib
import http.client
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HOME = Path(os.environ.get("REPOD_HOME", Path.home() / ".repod"))
CACHE = HOME / "cache"
COVERS = HOME / "covers"
DECISIONS = HOME / "decisions.json"
REVIEW = HOME / "review.json"
MANIFEST = HOME / "manifest.json"
IPODCTL = Path(os.environ.get("IPODCTL", Path(__file__).resolve().parents[1] / ".build/debug/ipodctl"))

# MusicBrainz asks every client to identify itself; REPOD_CONTACT can replace the project URL.
USER_AGENT = "RePod/0.1 ({})".format(os.environ.get("REPOD_CONTACT", "https://github.com/benkiguna/repod"))
CONFIDENT = 0.88   # both title and artist at least this similar
MIN_SCORE = 0.6    # below this a candidate is not even worth showing
MB_DELAY = 1.1     # seconds between MusicBrainz requests
LOOKUP_FAILED = (urllib.error.URLError, OSError, ValueError, http.client.HTTPException)  # offline, throttled, or an answer that is not what was asked for
NEEDS_PNG = {".heic", ".tif", ".tiff"}  # a browser can't show these, and the page has to

_last_mb = 0.0
BYPASS_CACHE = False  # set while re-checking: fetch() asks the internet again and overwrites the cache


def key_of(album: dict) -> str:
    return f"{album['albumArtist']} | {album['album']}"


def normalize(text: str) -> str:
    """Comparison form: no edition suffixes, brackets, punctuation or case."""
    text = text.lower()
    text = re.sub(r"[\(\[][^\)\]]*[\)\]]", " ", text)
    text = re.sub(r"\b(deluxe|expanded|remastered|remaster|edition|first pressing|bonus tracks?)\b", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return text.strip()


def similar(a: str, b: str) -> float:
    a, b = normalize(a), normalize(b)
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def fetch(url: str, *, binary: bool = False, mb: bool = False):
    """GET with an on-disk cache (negative answers included). Returns bytes/JSON, or None for 404."""
    global _last_mb
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / hashlib.sha1(url.encode()).hexdigest()
    if path.exists() and not BYPASS_CACHE:
        data = path.read_bytes()
        if data == b"\x00404":
            return None
        return data if binary else json.loads(data)
    if mb:
        wait = MB_DELAY - (time.monotonic() - _last_mb)
        if wait > 0:
            time.sleep(wait)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                data = response.read()
            break
        except urllib.error.HTTPError as error:
            if error.code in (404, 400):
                path.write_bytes(b"\x00404")
                return None
            # 429/503: the service is throttling; wait and retry rather than fail the album.
            if error.code not in (429, 503) or attempt == 4:
                raise
            time.sleep(2 ** attempt * 2)
        finally:
            if mb:
                _last_mb = time.monotonic()
    answer = data if binary else json.loads(data)  # before it is kept: a maintenance page must not stay in the cache as the answer
    path.write_bytes(data)
    return answer


def lucene(text: str) -> str:
    return re.sub(r'([+\-&|!(){}\[\]^"~*?:\\/])', r"\\\1", text)


def search_musicbrainz(title: str, artist: str) -> list:
    """Release-group candidates, best first, each with a front cover URL to try."""
    query = f'releasegroup:"{lucene(normalize(title))}"'
    if artist and artist.lower() not in ("various artists", ""):
        query += f' AND artist:"{lucene(artist)}"'
    url = "https://musicbrainz.org/ws/2/release-group/?" + urllib.parse.urlencode(
        {"query": query, "fmt": "json", "limit": 5})
    found = []
    for group in (fetch(url, mb=True) or {}).get("release-groups", []):
        credit = " ".join(c.get("name", "") for c in group.get("artist-credit", []))
        score = min(similar(title, group.get("title", "")), 1.0), similar(artist, credit)
        found.append({
            "source": "musicbrainz", "id": group["id"], "title": group.get("title", ""), "artist": credit,
            "titleScore": round(score[0], 3), "artistScore": round(score[1], 3),
            "image": f"https://coverartarchive.org/release-group/{group['id']}/front-500",
            "thumb": f"https://coverartarchive.org/release-group/{group['id']}/front-250",
        })
    return found


def search_itunes(title: str, artist: str, country: str = "US") -> list:
    url = "https://itunes.apple.com/search?" + urllib.parse.urlencode(
        {"term": f"{artist} {normalize(title)}".strip(), "entity": "album", "limit": 5, "country": country})
    found = []
    for item in (fetch(url) or {}).get("results", []):
        art = item.get("artworkUrl100")
        if not art:
            continue
        found.append({
            "source": "itunes", "id": str(item.get("collectionId")), "title": item.get("collectionName", ""),
            "artist": item.get("artistName", ""),
            "titleScore": round(similar(title, item.get("collectionName", "")), 3),
            "artistScore": round(similar(artist, item.get("artistName", "")), 3),
            "image": re.sub(r"/\d+x\d+bb", "/600x600bb", art),
            "thumb": re.sub(r"/\d+x\d+bb", "/200x200bb", art),
        })
    return found


def search_deezer(title: str, artist: str) -> list:
    url = "https://api.deezer.com/search/album?" + urllib.parse.urlencode(
        {"q": f"{artist} {normalize(title) or title}".strip(), "limit": 8})
    found = []
    for item in (fetch(url) or {}).get("data", []):
        if not item.get("cover_xl"):
            continue
        credit = (item.get("artist") or {}).get("name", "")
        found.append({
            "source": "deezer", "id": str(item.get("id")), "title": item.get("title", ""), "artist": credit,
            "titleScore": round(similar(title, item.get("title", "")), 3),
            "artistScore": round(similar(artist, credit), 3),
            "image": item["cover_xl"], "thumb": item.get("cover_medium") or item["cover_xl"],
        })
    return found


def search_covers(title: str, artist: str) -> dict:
    """Images to choose from, best match first, from every service at once. A service that fails only
    leaves its results out; `failed` names it. A long list of credits finds nothing, so the first name is asked."""
    who = re.split(r",|&|\bfeat\.?|\bft\.?", artist)[0].strip()
    who = "" if who.lower() == "various artists" else who
    searches = [("MusicBrainz", search_musicbrainz, ()), ("iTunes", search_itunes, ()), ("iTunes", search_itunes, ("IN",)), ("Deezer", search_deezer, ())]
    found, failed = [], set()

    def ask(name, search, extra, person):
        try:
            found.extend(search(title, person, *extra))
        except LOOKUP_FAILED:
            failed.add(name)

    for person in ([who, ""] if who else [""]):
        threads = [threading.Thread(target=ask, args=(*s, person)) for s in searches]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        if len({c["image"] for c in found}) >= 6:
            break  # enough; otherwise the artist's name may be what is in the way, so ask without it

    for candidate in found:  # the whole credit decides the order, though only the first name was asked
        candidate["artistScore"] = round(similar(artist, candidate["artist"]), 3)
    found.sort(key=lambda c: c["titleScore"] * 0.6 + c["artistScore"] * 0.4, reverse=True)
    unique = list({c["image"]: None for c in found})
    return {"results": [next(c for c in found if c["image"] == image) for image in unique][:24], "failed": sorted(failed)}


def to_png(source: Path, target: Path) -> None:
    """With macOS's own sips. Raises OSError when it can't read the file."""
    run = subprocess.run(["sips", "-s", "format", "png", str(source), "--out", str(target)], capture_output=True, text=True)
    if run.returncode != 0 or not target.exists():
        raise OSError("the image could not be read")


def download_image(url: str):
    data = fetch(url, binary=True)
    if not data or len(data) < 2048:
        return None
    if not (data[:3] == b"\xff\xd8\xff" or data[:8] == b"\x89PNG\r\n\x1a\n"):
        return None
    COVERS.mkdir(parents=True, exist_ok=True)
    path = COVERS / (hashlib.sha1(url.encode()).hexdigest() + (".png" if data[:1] == b"\x89" else ".jpg"))
    path.write_bytes(data)
    return path


def resolve(album: dict, pin: dict | None):
    """The best candidate that really has an image: (candidate | None, image path | None)."""
    if pin and pin.get("file"):
        return {"source": "file", "title": album["album"], "artist": album["albumArtist"],
                "titleScore": 1.0, "artistScore": 1.0, "image": pin["file"]}, Path(pin["file"]).expanduser()
    if pin and pin.get("url"):
        return ({"source": "url", "title": album["album"], "artist": album["albumArtist"],
                 "titleScore": 1.0, "artistScore": 1.0, "image": pin["url"]}, download_image(pin["url"]))
    if pin and pin.get("mbid"):
        url = f"https://coverartarchive.org/release-group/{pin['mbid']}/front-500"
        return ({"source": "musicbrainz", "id": pin["mbid"], "title": album["album"], "artist": album["albumArtist"],
                 "titleScore": 1.0, "artistScore": 1.0, "image": url}, download_image(url))
    candidates = []
    for search in (search_musicbrainz, search_itunes):
        candidates += search(album["album"], album["albumArtist"])
        if any(c["titleScore"] >= CONFIDENT and c["artistScore"] >= CONFIDENT for c in candidates):
            break
    candidates.sort(key=lambda c: c["titleScore"] * 0.6 + c["artistScore"] * 0.4, reverse=True)
    for candidate in candidates:
        if candidate["titleScore"] < MIN_SCORE:
            continue
        try:
            image = download_image(candidate["image"])
        except LOOKUP_FAILED:
            continue  # this image host failed; the next candidate may be fine
        if image:
            return candidate, image
    return None, None


def load_decisions() -> dict:
    if DECISIONS.exists():
        return json.loads(DECISIONS.read_text())
    return {"skip": [], "accept": [], "pin": {}}


def save_decisions(decisions: dict) -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    DECISIONS.write_text(json.dumps(decisions, indent=2, ensure_ascii=False, sort_keys=True))


class ScanError(Exception):
    """Something the user has to fix (ipodctl missing, iPod not connected)."""


def missing_albums(volume=None, developer=False, input_file=None) -> list:
    if input_file:
        return json.loads(Path(input_file).read_text())
    HOME.mkdir(parents=True, exist_ok=True)
    out = HOME / "missing.json"  # a file, not stdout: ipodctl prints banners on stdout
    command = [str(IPODCTL), "artwork", "--missing", "--out", str(out)]
    if volume:
        command += ["--volume", volume]
    if developer:
        command.append("--developer")
    if not IPODCTL.exists():
        raise ScanError(f"ipodctl not found at {IPODCTL} - build it (swift build --product ipodctl) or set IPODCTL")
    run = subprocess.run(command, capture_output=True, text=True)
    if run.returncode != 0:
        raise ScanError(run.stderr.strip() or "ipodctl failed")
    return json.loads(out.read_text())


def derive_status(entry: dict, decisions: dict) -> str:
    """auto = goes into the manifest; review = a weak match waiting for accept; skip; none; error."""
    key = entry["key"]
    if key in decisions["skip"]:
        return "skip"
    if not entry.get("match"):
        return entry.get("failure", "pending")  # pending: not looked up (yet)
    if entry["confident"] or key in decisions["accept"] or key in decisions["pin"]:
        return "auto"
    return "review"


def write_outputs(review: list, decisions: dict) -> list:
    """Recomputes every status from the decisions and rewrites review.json and manifest.json."""
    covers = []
    for entry in review:
        entry["status"] = derive_status(entry, decisions)
        if entry["status"] == "auto":
            covers.append({"album": entry["album"]["album"], "albumArtist": entry["album"]["albumArtist"],
                           "file": entry["file"]})
    HOME.mkdir(parents=True, exist_ok=True)
    REVIEW.write_text(json.dumps(review, indent=2, ensure_ascii=False))
    MANIFEST.write_text(json.dumps({"version": 1, "covers": covers}, indent=2, ensure_ascii=False))
    return covers


def lookup(album: dict, decisions: dict) -> dict:
    key = key_of(album)
    entry = {"key": key, "album": album}
    if not album["album"] and key not in decisions["pin"]:
        return {**entry, "failure": "none", "detail": "This album has no name to look up."}
    try:  # whatever goes wrong here is this album's problem: one bad answer or a pinned file that has gone must not end a scan
        candidate, image = resolve(album, decisions["pin"].get(key))
        if not candidate or not image:
            return {**entry, "failure": "none"}
        if image.parent != COVERS:  # a pinned local file: keep a copy next to the others so the page can show it
            COVERS.mkdir(parents=True, exist_ok=True)
            name = hashlib.sha1(str(image.resolve()).encode()).hexdigest()
            if image.suffix.lower() in NEEDS_PNG:
                copy = COVERS / f"{name}.png"
                to_png(image, copy)
            else:
                copy = COVERS / (name + image.suffix.lower())
                shutil.copyfile(image, copy)
            image = copy
    except LOOKUP_FAILED as error:
        return {**entry, "failure": "error", "detail": str(error)}
    confident = candidate["titleScore"] >= CONFIDENT and candidate["artistScore"] >= CONFIDENT
    return {**entry, "match": candidate, "file": str(image), "confident": confident}


def describe(entry: dict) -> str:
    match = entry.get("match")
    if not match:
        return entry.get("detail") or ""
    return f"{match['source']} '{match['title']}' / '{match['artist']}' ({match['titleScore']}, {match['artistScore']})"


def run_scan(volume=None, developer=False, input_file=None, limit=None, log=print, stop=None) -> list:
    decisions = load_decisions()
    albums = missing_albums(volume, developer, input_file)
    log(f"{len(albums)} albums without a cover")
    review = []
    for number, album in enumerate(albums, 1):
        if limit and number > limit:
            break
        if stop and stop():  # what is known about the rest stays as it was
            log(f"Stopped after {number - 1}. What was found is kept.")
            looked_at = {entry["key"] for entry in review}
            review += [entry for entry in load_review() if entry["key"] not in looked_at]
            break
        key = key_of(album)
        if key in decisions["skip"]:
            review.append({"key": key, "album": album})
            continue
        entry = lookup(album, decisions)
        review.append(entry)
        log(f"[{number}/{len(albums)}] {key}: {derive_status(entry, decisions)} {describe(entry)}")
    return write_outputs(review, decisions)


def load_review() -> list:
    if not REVIEW.exists():
        return []
    # Entries written by the first version of this tool had no "album"; a new scan replaces them.
    return [entry for entry in json.loads(REVIEW.read_text()) if "album" in entry]


def apply_decision(key: str, action: str, pin: dict | None = None, album: dict | None = None) -> None:
    """accept / skip / unskip / pin for one album; a pin looks it up again. `album` adds an album
    that no scan has looked at yet."""
    decisions = load_decisions()
    if action == "pin":
        decisions["pin"][key] = pin
    elif action == "unskip":
        decisions["skip"] = [k for k in decisions["skip"] if k != key]
    elif action in ("accept", "skip"):
        if key not in decisions[action]:
            decisions[action].append(key)
    else:
        raise ValueError(f"unknown action {action}")
    save_decisions(decisions)
    review = load_review()
    if album and not any(entry["key"] == key for entry in review):
        review.append({"key": key, "album": album})
    for index, entry in enumerate(review):
        if entry["key"] == key and action == "pin":
            review[index] = lookup(entry["album"], decisions)
    write_outputs(review, decisions)


def scan(args) -> None:
    try:
        covers = run_scan(args.volume, args.developer, args.input, args.limit)
    except ScanError as error:
        sys.exit(str(error))
    review = load_review()
    counts = {s: sum(1 for r in review if r["status"] == s) for s in ("auto", "review", "none", "error", "skip")}
    print(f"\nManifest: {len(covers)} covers -> {MANIFEST}")
    print(f"To review: {counts['review']} (accept / pin / skip), nothing found: {counts['none']}, errors: {counts['error']}")
    print(f"Preview the write:  {IPODCTL.name} apply {MANIFEST} --unverified-formats")


def show_review(_args) -> None:
    if not REVIEW.exists():
        sys.exit("no scan yet")
    for entry in load_review():
        print(f"{entry['status']:7} {entry['key']}  {describe(entry)}")
        if entry.get("match") and entry["status"] == "review":
            print(f"        image: {entry['file']}")


def decide(args) -> None:
    pin = None
    if args.command == "pin":
        pin = {name: getattr(args, name) for name in ("mbid", "url", "file") if getattr(args, name)}
        if len(pin) != 1:
            sys.exit("give exactly one of --mbid, --url, --file")
    apply_decision(args.key, args.command, pin)
    print(f"{args.command}: {args.key}  (manifest updated)")


def refresh(albums: list, log=print, stop=None) -> None:
    """Looks these albums up again, ignoring the cache for them (and replacing it)."""
    global BYPASS_CACHE
    decisions = load_decisions()
    review = load_review()
    BYPASS_CACHE = True
    try:
        for number, album in enumerate(albums, 1):
            if stop and stop():
                log(f"Stopped after {number - 1}. What was found is kept.")
                break
            key = key_of(album)
            entry = lookup(album, decisions)
            index = next((i for i, e in enumerate(review) if e["key"] == key), None)
            if index is None:
                review.append(entry)
            else:
                review[index] = entry
            log(f"[{number}/{len(albums)}] {key}: {derive_status(entry, decisions)} {describe(entry)}")
    finally:
        BYPASS_CACHE = False
    write_outputs(review, decisions)


def clear_cache() -> None:
    """Deletes every cached answer and downloaded image. Decisions stay; every album goes back to
    'not looked up'."""
    shutil.rmtree(CACHE, ignore_errors=True)
    shutil.rmtree(COVERS, ignore_errors=True)
    review = [{"key": e["key"], "album": e["album"]} for e in load_review()]
    write_outputs(review, load_decisions())


def prune_cache(review: list, dropped: list) -> int:
    """After a reload: forgets albums that are no longer missing (their cover was written) - their
    chosen image, and the cached download it came from. Returns how many files were deleted."""
    removed = 0
    keep = {Path(e["file"]).name for e in review if e.get("file")}
    for entry in dropped:
        match = entry.get("match") or {}
        if match.get("image"):
            cached = CACHE / hashlib.sha1(match["image"].encode()).hexdigest()
            if cached.exists():
                cached.unlink()
                removed += 1
    if COVERS.exists():
        for file in COVERS.iterdir():
            if file.is_file() and file.name not in keep:
                file.unlink()
                removed += 1
    return removed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    s = commands.add_parser("scan")
    s.add_argument("--volume", help="which iPod, as for ipodctl --volume")
    s.add_argument("--developer", action="store_true", help="pass --developer to ipodctl")
    s.add_argument("--input", help="a missing-albums JSON file instead of asking the iPod")
    s.add_argument("--limit", type=int, help="look at the first N albums only")
    s.set_defaults(run=scan)
    commands.add_parser("review").set_defaults(run=show_review)
    for name in ("accept", "skip", "unskip", "pin"):
        c = commands.add_parser(name)
        c.add_argument("key", help='"Album artist | Album", as review prints it')
        if name == "pin":
            c.add_argument("--mbid")
            c.add_argument("--url")
            c.add_argument("--file")
        c.set_defaults(run=decide)
    args = parser.parse_args()
    args.run(args)


if __name__ == "__main__":
    main()
