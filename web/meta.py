"""Finds better metadata for the songs on the iPod from MusicBrainz, and turns what you accept into a manifest for `ipodctl retag`.

Two kinds of proposal:
  album  an album with songs that lack a track number, year or album artist: its MusicBrainz release is
         found, each song is matched to a track of it, and the missing numbers are proposed.
  loose  songs with no album: the recording is found from "Artist - Title", its release is chosen, and
         songs of the same release become one album.

Nothing is written here. A proposal is a list of values per song; `ipodctl retag` does the writing.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import urllib.parse
from collections import defaultdict

import enrich

REVIEW = enrich.HOME / "meta-review.json"
DECISIONS = enrich.HOME / "meta-decisions.json"
ROOT = "https://musicbrainz.org/ws/2/"
ARTISTS = enrich.HOME / "artists.json"  # what was found about each artist; only on this Mac, an iPod has no place for it
PICTURES = enrich.HOME / "artist-art"

# What an accepted proposal may change, as the page offers it. The values are `ipodctl retag` fields.
GROUPS = {"tracks": ["track", "tracks", "disc", "discs"], "year": ["year"], "albumartist": ["albumartist"],
          "album": ["album"], "title": ["title"], "artist": ["artist"]}
# What is ticked at first. An existing album keeps its name, its titles and its artists unless asked.
DEFAULT = {"album": ["tracks", "year", "albumartist"], "loose": list(GROUPS)}


def mb(path: str, **params):
    return enrich.fetch(ROOT + path + "?" + urllib.parse.urlencode({**params, "fmt": "json"}), mb=True)


_PLAIN = str.maketrans({"\u2010": "-", "\u2011": "-", "\u2019": "'", "\u2018": "'", "\u201c": '"', "\u201d": '"'})


def clean(text: str) -> str:
    """MusicBrainz writes typographic hyphens and quotes; the songs on the iPod use plain ones, so keep those."""
    return (text or "").translate(_PLAIN)


def credit(parts) -> str:
    return clean("".join(p.get("name", "") + p.get("joinphrase", "") for p in parts or []).strip())


def year_of(date) -> int:
    return int(date[:4]) if date and date[:4].isdigit() else 0


def songs_of(dump: dict) -> list:
    return [{"id": t["uniqueID"], "title": t.get("title") or "", "artist": t.get("artist") or "",
             "album": t.get("album") or "", "albumartist": t.get("albumArtist") or "",
             "track": t.get("trackNumber") or 0, "tracks": t.get("totalTracks") or 0,
             "disc": t.get("discNumber") or 0, "discs": t.get("totalDiscs") or 0,
             "year": t.get("year") or 0, "seconds": t.get("durationSeconds") or 0} for t in dump["tracks"]]


def level(score: float) -> str:
    return "high" if score >= 0.85 else "medium" if score >= 0.7 else "low"


def release_tracks(release: dict) -> list:
    media = release.get("media") or []
    tracks = []
    for medium in media:
        for t in medium.get("tracks") or []:
            recording = t.get("recording") or {}
            tracks.append({"title": clean(t.get("title") or recording.get("title") or ""), "pos": int(t.get("position") or 0),
                           "disc": int(medium.get("position") or 1), "of": int(medium.get("track-count") or len(medium.get("tracks") or [])),
                           "discs": len(media), "recording": recording.get("id"),
                           "artist": credit(t.get("artist-credit")) or credit(release.get("artist-credit")),
                           "ms": t.get("length") or recording.get("length") or 0})
    return tracks


def release_info(mbid: str):
    release = mb(f"release/{mbid}", inc="recordings artist-credits release-groups")
    if not release:
        return None
    group = release.get("release-group") or {}
    return {"id": mbid, "title": clean(release.get("title", "")), "artist": credit(release.get("artist-credit")),
            "year": year_of(group.get("first-release-date")) or year_of(release.get("date")),
            "tracks": release_tracks(release)}


def wanted(info: dict, track: dict) -> dict:
    """The values this release gives a song that matches `track`."""
    want = {"track": track["pos"], "tracks": track["of"], "year": info["year"], "albumartist": info["artist"],
            "album": info["title"], "title": track["title"], "artist": track["artist"]}
    if track["discs"] > 1:
        want.update({"disc": track["disc"], "discs": track["discs"]})
    return want


def change(song: dict, want: dict) -> dict:
    return {k: v for k, v in want.items() if v and song.get(k) != v}


def match_tracks(songs: list, tracks: list) -> dict:
    """song id -> (track, score): the best title match for each song, each track used once."""
    pairs = []
    for s in songs:
        for t in tracks:
            score = enrich.similar(s["title"], t["title"])
            if t["ms"] and s["seconds"] and abs(t["ms"] / 1000 - s["seconds"]) <= 4:
                score = min(1.0, score + 0.1)
            if score >= 0.62:
                pairs.append((score, s["id"], t["pos"], t["disc"], t))
    taken_songs, taken_tracks, found = set(), set(), {}
    for score, sid, pos, disc, t in sorted(pairs, key=lambda p: -p[0]):
        if sid in taken_songs or (pos, disc) in taken_tracks:
            continue
        taken_songs.add(sid)
        taken_tracks.add((pos, disc))
        found[sid] = (t, score)
    return found


def proposal(kind: str, key: str, info: dict, songs: list, found: dict, score: float, current: str) -> dict | None:
    if kind == "loose" and info["artist"].lower() == "various artists":
        score *= 0.8  # a compilation is rarely where the song belongs; ask for a look
    rows, left = [], []
    for s in songs:
        if s["id"] not in found:
            left.append({"id": s["id"], "title": s["title"]})
            continue
        track, fit = found[s["id"]]
        changes = change(s, wanted(info, track))
        if changes:
            rows.append({"id": s["id"], "title": s["title"], "score": round(fit, 3), "set": changes,
                         "before": {k: s.get(k) or "" for k in changes}})
    if not rows:
        return None
    return {"key": key, "kind": kind, "mbid": info["id"], "album": info["title"], "albumArtist": info["artist"],
            "year": info["year"], "current": current, "score": round(score, 3), "level": level(score),
            "songs": rows, "unmatched": left}


def propose_album(owner: str, name: str, songs: list) -> tuple:
    """(proposal or None, a few words on what happened)."""
    query = f'release:"{enrich.lucene(enrich.normalize(name))}"'
    if owner and owner.lower() != "various artists":
        query += f' AND artist:"{enrich.lucene(owner)}"'
    best = None
    for r in (mb("release/", query=query, limit=8) or {}).get("releases", []):
        fit = enrich.similar(name, r.get("title", ""))
        who = enrich.similar(owner, credit(r.get("artist-credit"))) if owner and owner.lower() != "various artists" else 0.8
        if fit < 0.6:
            continue
        count = r.get("track-count") or 0
        rank = (fit * 0.6 + who * 0.4, r.get("status") == "Official", count >= len(songs), -abs(count - len(songs)))
        if best is None or rank > best[0]:
            best = (rank, r)
    if best is None or best[0][0] < 0.65:
        return None, "no matching release"
    info = release_info(best[1]["id"])
    if not info:
        return None, "release could not be read"
    found = match_tracks(songs, info["tracks"])
    if not found:
        return None, "no song matched a track"
    covered = len(found) / len(songs)
    result = proposal("album", f"album:{owner} | {name}", info, songs, found, best[0][0] * (0.5 + 0.5 * covered), name)
    return result, f"{info['title']} - {len(found)} of {len(songs)} songs matched" if result else "nothing to improve"


def split_title(song: dict) -> tuple:
    title, artist = song["title"], song["artist"]
    if not artist:
        m = re.match(r"^(.+?)\s+[-–—]\s+(.+)$", title)
        if m:
            artist, title = m[1].strip(), m[2].strip()
    return title, artist


def pick_release(recording: dict, artist: str = ""):
    """Releases credited to the song's own artist first, then official albums, then the earliest."""
    def rank(r):
        group = r.get("release-group") or {}
        plain = not group.get("secondary-types")
        theirs = bool(artist) and enrich.similar(artist, credit(r.get("artist-credit"))) >= 0.7
        return (theirs, r.get("status") == "Official", group.get("primary-type") == "Album", plain, -(year_of(r.get("date")) or 9999))
    return max(recording.get("releases") or [None], key=lambda r: rank(r) if r else (-1,))


def find_recording(song: dict):
    title, artist = split_title(song)
    if not artist or not title:
        return None, 0.0
    query = f'recording:"{enrich.lucene(enrich.normalize(title))}" AND artist:"{enrich.lucene(artist)}"'
    best = None
    for r in (mb("recording/", query=query, limit=6) or {}).get("recordings", []):
        fit, who = enrich.similar(title, r.get("title", "")), enrich.similar(artist, credit(r.get("artist-credit")))
        if fit < 0.8 or who < 0.7 or not r.get("releases"):
            continue
        near = 0.1 if r.get("length") and song["seconds"] and abs(r["length"] / 1000 - song["seconds"]) <= 5 else 0
        score = min(1.0, fit * 0.55 + who * 0.35 + near + 0.0)
        if best is None or score > best[1]:
            best = (r, score)
    return best if best else (None, 0.0)


def proposals(songs: list, log=print, stop=lambda: False) -> list:
    """What MusicBrainz has for these songs, whether they are on the iPod or still in a folder. Nothing is kept here."""
    albums = defaultdict(list)
    loose = []
    for s in songs:
        if s["album"]:
            albums[(s["albumartist"] or s["artist"], s["album"])].append(s)
        else:
            loose.append(s)
    need = {k: v for k, v in albums.items() if any(not x["track"] or not x["year"] or not x["albumartist"] for x in v)}
    total = len(need) + len(loose)
    log(f"{len(need)} albums are missing numbers and {len(loose)} songs have no album.")
    review, done = [], 0
    for (owner, name), group in sorted(need.items()):
        if stop():
            break
        done += 1
        try:
            result, note = propose_album(owner, name, group)
        except Exception as error:  # one album must not stop the scan
            result, note = None, f"failed: {error}"
        log(f"[{done}/{total}] {name} - {note}")
        if result:
            review.append(result)
    picked = defaultdict(list)  # release id -> [(song, recording, score)]
    for s in loose:
        if stop():
            break
        done += 1
        try:
            recording, score = find_recording(s)
        except Exception as error:
            recording, score = None, 0.0
            log(f"[{done}/{total}] {s['title']} - failed: {error}")
            continue
        release = pick_release(recording, split_title(s)[1]) if recording else None
        log(f"[{done}/{total}] {s['title']} - {'found on ' + release['title'] if release else 'not found'}")
        if release:
            picked[release["id"]].append((s, recording, score))
    for mbid, items in picked.items():
        info = release_info(mbid)
        if not info:
            continue
        found = {}
        for s, recording, score in items:
            track = next((t for t in info["tracks"] if t["recording"] == recording["id"]), None)
            if track is None:
                title, _ = split_title(s)
                track = max(info["tracks"], key=lambda t: enrich.similar(title, t["title"]), default=None)
            if track:
                found[s["id"]] = (track, score)
        mean = sum(score for _, score in found.values()) / len(found) if found else 0
        result = proposal("loose", f"loose:{mbid}", info, [s for s, _, _ in items], found, mean, "No album") if found else None
        if result:
            review.append(result)
    return review


def scan(songs: list, log=print, stop=lambda: False) -> list:
    review = proposals(songs, log, stop)
    if stop():  # what the last scan proposed for the rest stays
        found = {p["key"] for p in review}
        review += [p for p in load() if p["key"] not in found]
    review.sort(key=lambda p: (-p["score"], p["album"].lower()))
    enrich.HOME.mkdir(parents=True, exist_ok=True)
    REVIEW.write_text(json.dumps(review, indent=1))
    log("Stopped. Nothing was changed." if stop() else f"Done. {len(review)} proposals, {sum(len(p['songs']) for p in review)} songs. Nothing was changed.")
    return review


def load() -> list:
    return json.loads(REVIEW.read_text()) if REVIEW.exists() else []


def load_decisions() -> dict:
    return json.loads(DECISIONS.read_text()) if DECISIONS.exists() else {}


def decide(key: str, status: str, fields=None) -> dict:
    decisions = load_decisions()
    if status == "reset":
        decisions.pop(key, None)
    else:
        decisions[key] = {"status": status, **({"fields": fields} if fields is not None else {})}
    enrich.HOME.mkdir(parents=True, exist_ok=True)
    DECISIONS.write_text(json.dumps(decisions, indent=1))
    return decisions


def accept_all(accepted: dict) -> dict:
    """`accepted`: proposal key -> the field groups ticked on it."""
    decisions = load_decisions()
    decisions.update({key: {"status": "accepted", "fields": fields} for key, fields in accepted.items()})
    enrich.HOME.mkdir(parents=True, exist_ok=True)
    DECISIONS.write_text(json.dumps(decisions, indent=1))
    return decisions


def manifest(review: list, decisions: dict) -> dict:
    """The accepted proposals, with only the field groups that are ticked, as an `ipodctl retag` manifest."""
    songs, seen = [], set()
    for p in review:
        d = decisions.get(p["key"])
        if not d or d.get("status") != "accepted":
            continue
        allowed = {f for g in d.get("fields", DEFAULT[p["kind"]]) if g in GROUPS for f in GROUPS[g]}
        for s in p["songs"]:
            values = {k: v for k, v in s["set"].items() if k in allowed}
            if values and s["id"] not in seen:
                seen.add(s["id"])
                songs.append({"id": s["id"], "set": values})
        # A song of the album that was not matched goes with the rest: on its old name it would be a second album.
        together = {k: v for k, v in (("album", p["album"]), ("albumartist", p["albumArtist"])) if k in allowed and v}
        for u in p["unmatched"] if p["kind"] == "album" and together else []:
            if u["id"] not in seen:
                seen.add(u["id"])
                songs.append({"id": u["id"], "set": together})
    return {"version": 1, "songs": songs}


def consume() -> None:
    """After a write: the accepted proposals are done, so they leave the review and the decisions."""
    review, decisions = load(), load_decisions()
    done = {k for k, d in decisions.items() if d.get("status") == "accepted"}
    REVIEW.write_text(json.dumps([p for p in review if p["key"] not in done], indent=1))
    DECISIONS.write_text(json.dumps({k: d for k, d in decisions.items() if k not in done}, indent=1))


def search_words(text: str) -> str:
    """Words to search for: no bracketed extras or "ft. ..." tails, and every alphabet kept, so an accent or a Kannada title survives."""
    text = re.sub(r"[\(\[][^\)\]]*[\)\]]", " ", text)
    text = re.sub(r"\s(ft|feat)\b.*$", " ", text, flags=re.I)
    return " ".join(re.sub(r"[^\w]+", " ", text).split())


def attempts(field: str, text: str, artist: str) -> list:
    """Searches from strict to loose, the exact words first: what is found first is the closest. A list of credits is asked as its first name, and a name as
    plain words ("J.S. Bach" is "J. S. Bach" there), then the title alone, then each word, then each word roughly."""
    words = search_words(text)
    if not words:
        return []
    tokens = [enrich.lucene(w) for w in words.split()]
    who = search_words(first_credit(artist)) if artist.lower() != "various artists" else ""
    phrase = f'{field}:"{enrich.lucene(words)}"'
    exact = f'{field}:"{enrich.lucene(text.strip())}"'
    found = [f'{exact} AND artist:"{enrich.lucene(who)}"'] if who and exact != phrase else []
    if who:
        found += [f'{phrase} AND artist:"{enrich.lucene(who)}"',
                  f"{phrase} AND artist:({' AND '.join(enrich.lucene(w) for w in who.split())})"]
    found += [phrase, " AND ".join(f"{field}:{t}" for t in tokens)]
    if len(words) >= 3:
        found.append(" AND ".join(f"{field}:{t}~" for t in tokens))
    return found


def best_first(items: list, name, wanted: str, who, artist: str) -> list:
    """The closest name and artist first. Scores are Latin-only, so anything else keeps the order MusicBrainz gave."""
    return sorted(items, key=lambda r: -(enrich.similar(wanted, name(r)) * 0.7 + enrich.similar(first_credit(artist), who(r)) * 0.3))


def lookup(mode: str, title: str = "", artist: str = "", album: str = "") -> list:
    """Candidates for the edit form, as the values it can fill in: song -> title/artist/album/albumartist/year,
    album -> album/albumartist/year, artist -> artist. Read-only; answers are cached like every other lookup."""
    found, seen = [], set()
    def add(item):
        key = tuple(item.values())
        if key not in seen:
            seen.add(key)
            found.append(item)
    if mode == "song":
        if not artist:
            title, artist = split_title({"title": title, "artist": ""})
        recordings = []
        for query in attempts("recording", title, artist):
            recordings = (mb("recording/", query=query, limit=10) or {}).get("recordings", [])
            if recordings:
                break
        for r in best_first(recordings, lambda r: r.get("title", ""), title, lambda r: credit(r.get("artist-credit")), artist)[:8]:
            release = pick_release(r, artist) or {}
            add({"title": clean(r.get("title", "")), "artist": credit(r.get("artist-credit")), "album": clean(release.get("title", "")),
                 "albumartist": credit(release.get("artist-credit")), "year": year_of(release.get("date"))})
    elif mode == "album":
        releases = []
        for query in attempts("release", album, artist):
            releases = (mb("release/", query=query, limit=10) or {}).get("releases", [])
            if releases:
                break
        for r in best_first(releases, lambda r: r.get("title", ""), album, lambda r: credit(r.get("artist-credit")), artist)[:8]:
            add({"album": clean(r.get("title", "")), "albumartist": credit(r.get("artist-credit")), "year": year_of(r.get("date"))})
    elif mode == "artist":
        if not artist:
            return []
        # Plain words, not a phrase: "J.S. Bach" is "J. S. Bach" there, and a phrase would miss it.
        for r in (mb("artist/", query=search_words(artist) or artist, limit=8) or {}).get("artists", []):
            add({"artist": clean(r.get("name", "")), "note": r.get("disambiguation") or r.get("type") or ""})
    return found


# -- artists ------------------------------------------------------------------------------------

def first_credit(name: str) -> str:
    return re.split(r",|&|\bfeat\.?|\bft\.?", name)[0].strip()


def find_artist(who: str, albums: list):
    """A MusicBrainz artist id. An album of theirs tells two people of one name apart, so it is asked first.
    Plain words, not a phrase: "J.S. Bach" is "J. S. Bach" there."""
    for album in albums[:2]:
        query = f'release:"{enrich.lucene(enrich.normalize(album) or album)}" AND artist:"{enrich.lucene(who)}"'
        for r in (mb("release/", query=query, limit=5) or {}).get("releases", []):
            for c in r.get("artist-credit") or []:
                artist = c.get("artist") or {}
                if enrich.similar(who, artist.get("name", "")) >= 0.8:
                    return artist["id"]
    for r in (mb("artist/", query=enrich.normalize(who) or who, limit=5) or {}).get("artists", []):
        if enrich.similar(who, r.get("name", "")) >= 0.85:
            return r["id"]
    return None


def wikimedia_picture(artist: dict, width: int = 600):
    """The address of the photo a MusicBrainz artist links to: its own image link, or the one on Wikidata."""
    relations = artist.get("relations") or []
    links = {r.get("type"): (r.get("url") or {}).get("resource", "") for r in relations}
    name = links.get("image", "").rsplit("File:", 1)[-1] if "File:" in links.get("image", "") else None
    if not name and links.get("wikidata"):
        entity = links["wikidata"].rsplit("/", 1)[-1]
        data = enrich.fetch(f"https://www.wikidata.org/wiki/Special:EntityData/{entity}.json") or {}
        claims = ((data.get("entities") or {}).get(entity) or {}).get("claims") or {}
        for claim in claims.get("P18") or []:
            name = ((claim.get("mainsnak") or {}).get("datavalue") or {}).get("value")
            if name:
                break
    return f"https://commons.wikimedia.org/wiki/Special:FilePath/{urllib.parse.quote(urllib.parse.unquote(name))}?width={width}" if name else None


def deezer_artists(name: str) -> list:
    """Artists of this name with a real picture (an empty one has no id in its address), best known first."""
    url = "https://api.deezer.com/search/artist?" + urllib.parse.urlencode({"q": name, "limit": 8})
    found = [a for a in (enrich.fetch(url) or {}).get("data", [])
             if a.get("picture_xl") and "/artist//" not in a["picture_xl"] and enrich.similar(name, a.get("name", "")) >= 0.85]
    return sorted(found, key=lambda a: -(a.get("nb_fan") or 0))


def load_artists() -> dict:
    return json.loads(ARTISTS.read_text()) if ARTISTS.exists() else {}


def save_artist(name: str, record: dict) -> None:
    artists = load_artists()
    artists[name] = record
    enrich.HOME.mkdir(parents=True, exist_ok=True)
    temporary = ARTISTS.with_suffix(".tmp")  # the page may be reading it
    temporary.write_text(json.dumps(artists, indent=1))
    temporary.replace(ARTISTS)


PICTURE_SIZE = 480  # the largest it is ever shown; a photo at full size only costs time to decode


def shrink_picture(file) -> None:
    sizes = subprocess.run(["sips", "-g", "pixelWidth", "-g", "pixelHeight", str(file)], capture_output=True, text=True).stdout
    if max([int(n) for n in re.findall(r"pixel\w+: (\d+)", sizes)] or [0]) > PICTURE_SIZE:
        subprocess.run(["sips", "-Z", str(PICTURE_SIZE), str(file)], capture_output=True)


def keep_picture(source) -> str:
    """Kept under its content, not its name: two artists' `cover.jpg` are two pictures, and a name needs no escaping."""
    PICTURES.mkdir(parents=True, exist_ok=True)
    name = hashlib.sha1(source.read_bytes()).hexdigest()
    if source.suffix.lower() in enrich.NEEDS_PNG:
        name += ".png"
        enrich.to_png(source, PICTURES / name)
    else:
        name += source.suffix.lower()
        shutil.copyfile(source, PICTURES / name)
    shrink_picture(PICTURES / name)
    return name


def shrink_pictures() -> None:
    """Pictures kept before they were sized. Once is enough: a new one is sized as it arrives."""
    done = PICTURES / ".sized"
    if PICTURES.exists() and not done.exists():
        for file in PICTURES.glob("*.*"):
            shrink_picture(file)
        done.touch()


def picture_from(url: str):
    source = enrich.download_image(url)
    return keep_picture(source) if source else None


def look_up_artist(name: str, albums: list) -> dict:
    """One artist's facts from MusicBrainz and a picture: the one MusicBrainz links to, so it is surely them,
    else Deezer. A picture chosen by hand stays. Several credited names are looked up as the first of them."""
    old = load_artists().get(name) or {}
    record = {"looked": True, "picture": old.get("picture"), "pinned": old.get("pinned", False)}
    if name.lower() == "various artists":
        return record
    who = first_credit(name)
    artist_id = find_artist(who, albums)
    artist = mb(f"artist/{artist_id}", inc="url-rels tags") if artist_id else None
    if artist:
        span = artist.get("life-span") or {}
        tags = sorted(artist.get("tags") or [], key=lambda t: -(t.get("count") or 0))
        record.update({"mbid": artist_id, "credit": clean(artist.get("name", "")) if who != name else None, "type": artist.get("type"),
                       "country": artist.get("country") or (artist.get("area") or {}).get("name"),
                       "begin": year_of(span.get("begin")), "end": year_of(span.get("end")), "about": artist.get("disambiguation") or "",
                       "tags": [t["name"] for t in tags[:4]]})
    if not record["pinned"]:
        candidates = [wikimedia_picture(artist)] if artist else []
        candidates += [a["picture_xl"] for a in deezer_artists(who)[:1]]
        for url in filter(None, candidates):
            try:
                picture = picture_from(url)
            except OSError:
                continue
            if picture:
                record["picture"] = picture
                break
    return record


def scan_artists(wanted: dict, log, stop) -> bool:
    """`wanted`: artist -> their album names. Saved one by one, so a stopped scan keeps what it found."""
    total = len(wanted)
    log(f"Looking up {total} artists on MusicBrainz. Nothing changes on the iPod.")
    log(f"[0/{total}] starting")
    found = 0
    for number, (name, albums) in enumerate(sorted(wanted.items()), 1):
        if stop():
            log(f"Stopped after {number - 1}. What was found is kept; press the button again to carry on.")
            return False
        try:
            record = look_up_artist(name, albums)
            save_artist(name, record)
            found += bool(record.get("picture"))
            log(f"[{number}/{total}] {name} - {'picture found' if record.get('picture') else 'no picture'}")
        except Exception as error:  # one artist must not stop the rest
            log(f"[{number}/{total}] {name} - failed: {error}")
    log(f"Done. Pictures for {found} of {total}.")
    return True


def search_artist_pictures(name: str) -> dict:
    """Pictures to choose from for the page, in the shape of `enrich.search_covers`."""
    results, failed = [], []
    try:
        artist_id = find_artist(first_credit(name), [])
        linked = wikimedia_picture(mb(f"artist/{artist_id}", inc="url-rels")) if artist_id else None
        if linked:
            results.append({"source": "musicbrainz", "title": name, "artist": "", "image": linked, "thumb": linked.replace("width=600", "width=200")})
    except (OSError, ValueError):
        failed.append("MusicBrainz")
    try:
        results += [{"source": "deezer", "title": a.get("name", ""), "artist": "", "image": a["picture_xl"], "thumb": a.get("picture_medium") or a["picture_xl"]}
                    for a in deezer_artists(first_credit(name))]
    except (OSError, ValueError):
        failed.append("Deezer")
    return {"results": results, "failed": failed}
