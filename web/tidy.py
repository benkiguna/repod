"""What looks wrong in a library: albums to put back together, and songs that are on the iPod twice.

Plain functions over the page's song list (`web.build_library`). Nothing here reads or writes anything. A hint
only proposes: what it would change is in its `fix`, one `{"id", "set"}` per song in the form `ipodctl retag`
takes, and the page stages that for the user to look at. Nothing is written from here.
"""
from __future__ import annotations
import re
import unicodedata
from functools import cmp_to_key

SAME_LENGTH = 2  # seconds; two rips of one song differ by a little (`SongIndex.sameLength` in the app)
EDIT_LENGTH = 30  # further apart than this it is another recording, whatever it is called
CLEAR_QUALITY = .15  # 846 and 852 kbps are one quality; 192 and 886 are not
MOSTLY_REPEATS = .75  # of an album's songs: more than this on other albums, and the album is a best-of

FOLDERS = {"transfer", "ipod music", "ipod", "downloads", "download", "music", "my music", "unknown", "unknown album", "new folder", "untitled", "songs", "mp3", "audio", "desktop", "temp", "tmp", "misc"}
SITE = re.compile(r"^[a-z]+://|^www\.|\.(com|net|org|co|in|info|me|io|to|cc|fm)\s*$", re.I)
EDITION = re.compile(r"\s*(?:[(\[][^)\]]*\b(?:deluxe|edition|remaster\w*|bonus|expanded|anniversary|soundtrack|ost|version)\b[^)\]]*[)\]]|[-–—:]\s*(?:\w+\s+)?(?:deluxe|edition)\b.*)\s*$", re.I)
DISC = re.compile(r"\s*[(\[–—:,-]*\s*(?:disc|disk|cd)\s*(\d+)\s*[)\]]?\s*$", re.I)
NOISE = re.compile(r"\s*[(\[|]?\s*\b(?:official\s+)?(?:music\s+|lyric\s+|full\s+)?(?:video|audio|lyrics?|visuali[sz]er)\b\s*[)\]]?|\s*[(\[]\s*(?:hd|hq|4k)\s*[)\]]|\s*[(\[]\s*remaster(?:ed)?(?:\s+\d{4})?\s*[)\]]|\.(?:mp3|m4a|wav|flac)$", re.I)
CREDIT = re.compile(r",|&|;|\+|/|\bx\b|\band\b|\bwith\b|\bfeat\.?|\bft\.?|\bfeaturing\b", re.I)
HEADLINE = re.compile(r"^(.{2,48}?)(?:\s+[-–—]\s+|\s{2,})(.+)$")
FILLER = {"a", "an", "and", "the", "for", "of", "to", "in", "on"}


def plain(text: str) -> str:
    """Letters and digits only, without accents or case: "Renée" and "RENEE" are one name."""
    text = unicodedata.normalize("NFKD", text or "")
    return re.sub(r"[\W_]+", " ", "".join(c for c in text if not unicodedata.combining(c)).casefold()).strip()


def compact(text: str) -> str:
    return plain(text).replace(" ", "")


def most_common(names: list) -> str:
    """The spelling most songs use; of two as common, the one with the accents and the capitals."""
    counts = {}
    for name in names:
        counts[name] = counts.get(name, 0) + 1
    return max(sorted(counts), key=lambda n: (counts[n], len(unicodedata.normalize("NFKD", n)), sum(c.islower() for c in n)))


def credit(artist: str) -> tuple:
    """The people on a song, in any order: "Chitra, Ilaiyaraaja" and "Ilaiyaraaja & Chitra" are one credit."""
    return tuple(sorted({compact(name) for name in CREDIT.split(artist or "")} - {""}))


def core_title(title: str) -> str:
    """A title without what only a download site adds. A version stays: a remix is another recording."""
    return plain(NOISE.sub("", title or "") or title)


# -- grouping ------------------------------------------------------------------------------------------

def hint(kind: str, title: str, note: str, ids: list, fix: list | None = None, level: str = "fix") -> dict:
    return {"key": f"{kind}:{compact(title)[:60]}", "kind": kind, "title": title, "note": note, "ids": ids, "fix": fix or [], "level": level}


def count(n: int, one: str, many: str) -> str:
    return f"{n:,} {one if n == 1 else many}"


def by_album(songs: list) -> dict:
    albums = {}
    for s in songs:
        if s["album"]:
            albums.setdefault((s["album"], s["albumArtist"]), []).append(s)
    return albums


def folder_albums(albums: dict) -> list:
    """An album called what its folder was called: a tool filled it in from the path."""
    named = [(key, group) for key, group in albums.items() if plain(key[0]) in FOLDERS or SITE.search(key[0].strip())]
    if not named:
        return []
    songs = [s for _, group in named for s in group]
    names = ", ".join(f"“{key[0]}”" for key, _ in named[:3]) + (f" and {len(named) - 3} more" if len(named) > 3 else "")
    return [hint("folder", f"Named after a folder: {names}", f"{count(len(songs), 'song', 'songs')}. Takes the album name away; they become loose songs.",
                 [s["id"] for s in songs], [{"id": s["id"], "set": {"album": ""}} for s in songs])]


def split_albums(splits: list, by_id: dict) -> list:
    found = []
    for x in splits:
        fix = []
        for number in x["ids"]:
            s = by_id[number]
            change = {}
            if s["album"] != x["album"]:
                change["album"] = x["album"]
            if s["albumArtist"] != x["artist"] or not s["albumartist"]:
                change["albumartist"] = x["artist"]
            if change:
                fix.append({"id": number, "set": change})
        found.append(hint("split", x["album"], f"Shows as {len(x['parts'])} albums. Files all {len(x['ids'])} songs under {x['artist'] or 'one artist'}.", x["ids"], fix))
    return found


def edition_albums(albums: dict) -> list:
    """One album under two names: "Harbor" and "Harbor (Original Soundtrack)", or "Abbey Road CD2"."""
    bases = {}
    for (album, owner), group in albums.items():
        disc = DISC.search(album)
        base = EDITION.sub("", DISC.sub("", album)) or album
        bases.setdefault((plain(base), compact(owner)), []).append((album, owner, group, int(disc.group(1)) if disc else 0))
    found = []
    for parts in bases.values():
        if len(parts) < 2 or len({plain(p[0]) for p in parts}) < 2:
            continue
        target = max(parts, key=lambda p: (len(p[2]), -p[3]))
        base = EDITION.sub("", DISC.sub("", target[0])) if any(p[3] for p in parts) else target[0]
        fix = []
        for album, _, group, disc in parts:
            for s in group:
                change = {} if album == base else {"album": base}
                if s["albumArtist"] != target[1]:
                    change["albumartist"] = target[1]
                if disc and s["disc"] != disc:
                    change["disc"] = disc
                if change:
                    fix.append({"id": s["id"], "set": change})
        names = " and ".join(f"“{p[0]}”" for p in parts[:2]) + (f" and {len(parts) - 2} more" if len(parts) > 2 else "")
        found.append(hint("edition", base, f"{names} look like one album.", [s["id"] for p in parts for s in p[2]], fix))
    return sorted(found, key=lambda h: h["title"].casefold())


def artist_spellings(songs: list) -> list:
    """One artist spelled more than one way, which is two artists on the iPod, and two of every album they share."""
    names = {}
    for s in songs:
        for name in {s["artist"], s["albumartist"]} - {""}:
            names.setdefault(compact(name), []).append(name)
    found = []
    for spellings in names.values():
        if len(set(spellings)) < 2:
            continue
        best = most_common(spellings)
        others = set(spellings) - {best}
        fix = []
        for s in songs:
            change = {}
            if s["artist"] in others:
                change["artist"] = best
            if s["albumartist"] in others:
                change["albumartist"] = best
            if change:
                fix.append({"id": s["id"], "set": change})
        shown = " and ".join(f"“{n}”" for n in sorted(others)[:2])
        found.append(hint("spelling", best, f"Also {shown}. Uses one spelling on {count(len(fix), 'song', 'songs')}.", [x["id"] for x in fix], fix))
    return sorted(found, key=lambda h: h["title"].casefold())


def headline(song: dict) -> tuple:
    """The artist and the title out of a title like "Artist - Song (Official Video)". Empty when it doesn't read that way."""
    if song["artist"] or song["album"]:
        return "", ""
    found = HEADLINE.match(song["title"].strip())
    if not found or re.search(r"\b(?:ft|feat)\b", found.group(1), re.I):
        return "", ""
    return found.group(1).strip(), NOISE.sub("", found.group(2)).strip() or found.group(2).strip()


def loose_songs(songs: list) -> list:
    """Downloads: no artist and no album, with both in the title. By artist, then one hint for those alone."""
    people, alone = {}, []
    for s in songs:
        artist, title = headline(s)
        if artist:
            people.setdefault(compact(artist), []).append((s, artist, title))
    found = []
    for group in people.values():
        if len(group) == 1:
            alone += group
            continue
        name = most_common([artist for _, artist, _ in group])
        found.append(hint("loose", name, f"{count(len(group), 'loose song', 'loose songs')}. Moves the artist out of the title.",
                          [s["id"] for s, _, _ in group], [{"id": s["id"], "set": {"artist": name, "title": title}} for s, _, title in group]))
    if alone:
        found.append(hint("loose", "Other loose songs", f"{count(len(alone), 'song', 'songs')}. Moves the artist out of the title; check the names read right.",
                          [s["id"] for s, _, _ in alone], [{"id": s["id"], "set": {"artist": artist, "title": title}} for s, artist, title in alone]))
    return sorted(found, key=lambda h: -len(h["ids"]))


def repeated_numbers(albums: dict) -> list:
    found = []
    for (album, _), group in albums.items():
        seen = {}
        for s in group:
            if s["track"]:
                seen.setdefault((s["disc"], s["track"]), []).append(s)
        repeats = sum(len(v) - 1 for v in seen.values())
        if repeats:
            found.append(hint("numbers", album, f"{count(repeats, 'track number is', 'track numbers are')} used twice.", [s["id"] for s in group]))
    return sorted(found, key=lambda h: h["title"].casefold())


def hints(songs: list, splits: list, doubles: list) -> list:
    """Everything to look at, the surest first. `level` "check" is what only the iPod itself can settle."""
    albums, by_id = by_album(songs), {s["id"]: s for s in songs}
    found = folder_albums(albums) + split_albums(splits, by_id) + edition_albums(albums) + artist_spellings(songs) + loose_songs(songs) + repeated_numbers(albums)
    found += [hint("double", x["album"], f"“{x['artist']}” is on none of the songs. A compilation shows once.", x["ids"],
                   [{"id": i, "set": {"compilation": True}} for i in x["ids"]], "check") for x in doubles]
    return found


def sort_hints(albums: list, songs: list) -> list:
    """Albums the iPod splits because one song's hidden sort fields differ: nothing on the page shows those, so `ipodctl fix-sort` finds them."""
    if not albums:
        return []
    names = set(albums)
    found = hint("sort", ", ".join(f"“{a}”" for a in albums[:2]) + (f" and {len(albums) - 2} more" if len(albums) > 2 else ""),
                 f"The iPod shows {'this album' if len(albums) == 1 else f'these {len(albums)} albums'} as several covers, because of hidden sort fields. This makes them match.",
                 [s["id"] for s in songs if s["album"] in names])
    return [{**found, "action": "fix-sort"}]


# -- duplicates ----------------------------------------------------------------------------------------

def lengths(songs: list, limit: float) -> list:
    """Runs of the same length. Each is measured from the first of its run, so 3:00, 3:02, 3:04 doesn't chain."""
    runs = []
    for s in sorted(songs, key=lambda x: x["seconds"]):
        if runs and s["seconds"] - runs[-1][0]["seconds"] <= limit:
            runs[-1].append(s)
        else:
            runs.append([s])
    return runs


def clusters(songs: list) -> list:
    """Sets of copies with how sure we are: `same` one album, `albums` two albums, `maybe` a look is worth it."""
    titled = {}
    for s in songs:
        name = core_title(s["title"])
        if name:
            titled.setdefault((name, credit(s["artist"])), []).append(s)
    found, used = [], set()
    for group in titled.values():
        for run in lengths(group, SAME_LENGTH):
            if len(run) > 1:
                sure = len({plain(s["title"]) for s in run}) == 1
                found.append((run, ("same" if len({(plain(s["album"]), compact(s["albumArtist"])) for s in run}) == 1 else "albums") if sure else "maybe"))
                used.update(s["id"] for s in run)
    for group in titled.values():  # one name, not quite one length: another edit, most likely
        left = [s for s in group if s["id"] not in used]
        for run in lengths(left, EDIT_LENGTH):
            if len(run) > 1:
                found.append((run, "maybe"))
                used.update(s["id"] for s in run)
    timed = {}
    for s in songs:
        if s["id"] not in used and s["seconds"] >= 60:
            timed.setdefault(s["seconds"], []).append(s)
    words = {s["id"]: set(plain(s["title"]).split()) - FILLER for s in songs}
    for seconds in sorted(timed):  # a file name for a title: the same length, and all of one title's words in the other's
        group = timed[seconds] + [s for s in timed.get(seconds + 1, []) if s["id"] not in used]
        run = []
        for s in sorted(group, key=lambda x: len(words[x["id"]])):
            if s["id"] in used:
                continue
            if not run and len(words[s["id"]]) >= 2:
                run = [s]
            elif run and words[run[0]["id"]] <= words[s["id"]]:
                run.append(s)
        if len(run) > 1:
            found.append((run, "maybe"))
            used.update(s["id"] for s in run)
    return found


def quality(s: dict) -> str:
    name = "Apple Lossless" if s["format"] == "ALAC" else s["format"] or "Audio"
    return f"{name} {s['kbps']} kbps" if s["kbps"] else name


def plural_times(n: int) -> str:
    return "once" if n == 1 else "twice" if n == 2 else f"{n} times"


def duplicates(songs: list) -> dict:
    """The sets of copies, each with the copy to keep and why, and the albums that are mostly copies."""
    found = clusters(songs)
    albums = by_album(songs)
    copied = {s["id"] for run, _ in found for s in run}
    worth = {key: sum(s["id"] not in copied for s in group) / len(group) for key, group in albums.items()}  # what would be lost: the share of it found nowhere else

    def best(a: dict, b: dict) -> int:
        high = max(a["kbps"], b["kbps"]) or 1
        if abs(a["kbps"] - b["kbps"]) / high > CLEAR_QUALITY:
            return b["kbps"] - a["kbps"]
        ours, theirs = worth.get((a["album"], a["albumArtist"]), -1), worth.get((b["album"], b["albumArtist"]), -1)
        if ours != theirs:
            return 1 if theirs > ours else -1
        if a["plays"] != b["plays"]:
            return b["plays"] - a["plays"]
        filled = lambda s: bool(s["track"]) + bool(s["year"]) + bool(s["album"])
        return (filled(b) - filled(a)) or (a["id"] - b["id"])

    sets = []
    for run, kind in found:
        ranked = sorted(run, key=cmp_to_key(best))
        keep, second = ranked[0], ranked[1]
        if len({(s["size"], s["seconds"]) for s in run}) == 1 and keep["size"]:
            why = "The files are the same size and length."
        elif abs(keep["kbps"] - second["kbps"]) / (max(keep["kbps"], second["kbps"]) or 1) > CLEAR_QUALITY:
            why = f"Best quality: {quality(keep)} against {quality(second)}."
        elif keep["album"] != second["album"] and worth.get((keep["album"], keep["albumArtist"]), -1) > worth.get((second["album"], second["albumArtist"]), -1):
            why = f"“{keep['album']}” has songs found nowhere else."
        elif keep["plays"] != second["plays"]:
            why = f"Played {plural_times(keep['plays'])}, the most of the copies."
        else:
            why = "The copies are alike, so this is the first of them."
        gap = max(s["seconds"] for s in run) - min(s["seconds"] for s in run)
        sets.append({"key": "-".join(str(n) for n in sorted(s["id"] for s in run)), "kind": kind, "title": keep["title"], "artist": keep["artist"],
                     "ids": [s["id"] for s in ranked], "keep": keep["id"], "why": why, "gap": gap})
    sets.sort(key=lambda x: (x["artist"].casefold(), x["title"].casefold()))
    elsewhere = {i for x in sets if x["kind"] == "albums" for i in x["ids"]}
    repeats = []  # a best-of: decided as one album, not song by song
    for (album, artist), group in albums.items():
        shared = [s["id"] for s in group if s["id"] in elsewhere]
        if len(group) > 2 and len(shared) / len(group) >= MOSTLY_REPEATS:
            repeats.append({"album": album, "artist": artist, "ids": shared, "songs": len(group)})
    return {"sets": sets, "repeats": sorted(repeats, key=lambda a: -len(a["ids"]))}
