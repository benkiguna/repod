#!/usr/bin/env python3
"""A fire drill for Restore everything: the real `ipodctl`, on a disk image, with no real iPod written to.

    python3 web/dev/restore_drill.py --sample 12 --rounds 3

The image gets the library from your newest backup. A random sample of songs brings their real audio, read from
the connected iPod; the rest are small files of random bytes under the same names, because a checksum can't tell
and 20 GB would make this a day's work. Then the image is reset, in several ways, and rebuilt through the same
job the page runs, and the songs, the library, the playlists and the artwork are compared with how they started.

What keeps this away from the real thing:
- the image is mounted outside /Volumes and hidden, so no running server sees a second iPod;
- every `ipodctl` command names the image with --volume;
- HOME points at a scratch folder, so the backups it makes and removes are not yours. Yours are only read.
"""
import argparse
import hashlib
import json
import os
import pwd
import random
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
REAL_HOME = Path(pwd.getpwuid(os.getuid()).pw_dir)
PLAYER = Path("/Volumes/iPod")
MB = 1 << 20

parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
parser.add_argument("--sample", type=int, default=12, help="songs that bring their real audio; the disk needs about 2.5 times their size free")
parser.add_argument("--rounds", type=int, default=3, help="how many times to go through the resets")
parser.add_argument("--seed", type=int, default=int(time.time()))
parser.add_argument("--work", type=Path, default=None, help="scratch folder")
parser.add_argument("--backups", type=Path, default=REAL_HOME / "Library/Application Support/RePod/Backups", help="where your backups are; they are only read")
parser.add_argument("--library", type=Path, default=REAL_HOME / ".repod/library.json", help="a dump of your library; it is only read")
options = parser.parse_args()
rng = random.Random(options.seed)
work = (options.work or Path(tempfile.mkdtemp(prefix="restore-drill-"))).resolve()
mount, home, songs = work / "mnt", work / "home", work / "mac"
for folder in (mount, home, songs):
    folder.mkdir(parents=True, exist_ok=True)

os.environ.update(HOME=str(home), CFFIXED_USER_HOME=str(home), REPOD_HOME=str(home / "enrich"), REPOD_BACKUPS=str(home / "Library/Application Support/RePod/Backups"),
                  IPODCTL=os.environ.get("IPODCTL") or str(ROOT / ".build/debug/ipodctl"))
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import web  # noqa: E402

CONTROL, MUSIC = mount / "iPod_Control", mount / "iPod_Control/Music"
results = []


def sh(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree(folder):
    return {f.relative_to(folder).as_posix(): sha(f) for f in sorted(folder.rglob("*")) if f.is_file() and not f.name.startswith(".")}


def build():
    newest = sorted(d for d in options.backups.glob("*/*") if (d / "files/iTunes/iTunesDB").exists())[-1]
    return newest


def dump(app):
    out = work / "dump.json"
    run = subprocess.run(app.ctl("dump", "--out", str(out)), capture_output=True, text=True)
    return json.loads(out.read_text()) if run.returncode == 0 else None


def essentials(library):
    """What the iPod is to a person: every song's fields, and the playlists. The file is rewritten, so its bytes and its private ids may differ."""
    drop = {"sourceByteCount"}
    return {k: v for k, v in library.items() if k not in drop}


def job(app, start):
    assert start(), "the job did not start"
    while app.job.running:
        time.sleep(.05)
    return app.job.snapshot()


def differences(app, want):
    """Everything that has to be as it was."""
    now = dump(app)
    faults = []
    if now is None:
        faults.append("the iPod could not be read")
    else:
        for part in ("tracks", "playlists"):
            if now[part] != want["library"][part]:
                differing = sum(a != b for a, b in zip(now[part], want["library"][part])) + abs(len(now[part]) - len(want["library"][part]))
                faults.append(f"{differing} {part} differ")
    if tree(MUSIC) != want["music"]:
        faults.append("the song files differ")
    if tree(CONTROL / "Artwork") != want["artwork"]:
        faults.append("the artwork differs")
    return faults


def compare(app, want, label, started):
    faults = differences(app, want)
    results.append((label, not faults, time.monotonic() - started, "; ".join(faults) or f"{len(want['music'])} files, {len(want['library']['tracks'])} songs, {len(want['library']['playlists'])} playlists identical"))
    print(f"  {'ok  ' if not faults else 'FAIL'} {label}: {results[-1][3]}", flush=True)


def main():
    newest = build()
    print(f"seed {options.seed}. Library from {newest.name}; scratch in {work}", flush=True)
    assert not str(mount).startswith("/Volumes")
    # 1. An image, shaped like the iPod
    probe = json.loads(options.library.read_text())["tracks"]
    sample = rng.sample([t for t in probe if t.get("path")], min(options.sample, len(probe))) if (PLAYER / "iPod_Control").is_dir() else []
    tall = [t for t in probe if (t.get("path") or "").endswith(".m4a") and (t.get("bitRate") or 0) > 1100 and t not in sample]  # most likely 24-bit: the conversion has real work to do
    sample += rng.sample(tall, min(2, len(tall))) if sample else []
    size = sum(t.get("sizeBytes") or 0 for t in sample)
    print(f"{len(sample)} songs of real audio, {size / 1e9:.2f} GB", flush=True)
    sh("hdiutil", "create", "-size", f"{int(size / MB * 2.4) + 1200}m", "-fs", "HFS+J", "-volname", "iPodDrill", "-type", "SPARSE", "-quiet", str(work / "drill"))
    sh("hdiutil", "attach", str(work / "drill.sparseimage"), "-nobrowse", "-mountpoint", str(mount), "-quiet")
    try:
        (CONTROL / "Device").mkdir(parents=True)
        (CONTROL / "Device/SysInfo").write_text(f"FirewireGuid: 0x{newest.parent.name}\n")
        shutil.copytree(newest / "files/iTunes", CONTROL / "iTunes")
        shutil.copytree(newest / "files/Artwork", CONTROL / "Artwork")
        for number in range(50):
            (MUSIC / f"F{number:02}").mkdir(parents=True, exist_ok=True)
        app = web.App(SimpleNamespace(volume=str(mount), developer=True))
        library = dump(app)
        real = {t["path"] for t in sample}
        for track in library["tracks"]:
            path = track.get("path")
            if not path:
                continue
            out = mount / path
            out.parent.mkdir(parents=True, exist_ok=True)
            if path in real and (PLAYER / path).exists():
                with open(PLAYER / path, "rb") as source, open(out, "wb") as target:  # the real iPod is only ever read
                    shutil.copyfileobj(source, target, MB)
            else:
                out.write_bytes(rng.randbytes(rng.randrange(4096, 65536)))
        app.try_load()
        assert app.library, app.load_error
        want = {"library": dump(app), "music": tree(MUSIC), "artwork": tree(CONTROL / "Artwork")}
        ids = [t["uniqueID"] for t in want["library"]["tracks"]]
        print(f"The image: {len(ids)} songs, {len(want['music'])} files", flush=True)

        # 2. The copy
        started = time.monotonic()
        copied = job(app, lambda: app.copy_songs(songs))
        assert copied["ok"], copied["log"][-4:]
        results.append(("copy the songs", True, time.monotonic() - started, copied["outcome"]["summary"]))
        print(f"  ok   copy: {copied['outcome']['summary']}", flush=True)
        checked = job(app, app.check_copy)
        assert checked["outcome"]["result"] == "done", checked["log"][-2:]

        def bare():
            for name in ("iTunes", "Artwork", "Music"):
                shutil.rmtree(CONTROL / name)
            MUSIC.mkdir()
            app.try_load()
            assert app.library is None, "a bare iPod should not open"

        def empty_library():
            """As Finder leaves it: a library with nothing in it, and no songs."""
            run = subprocess.run(app.ctl("remove", "--id", ",".join(map(str, ids)), "--apply"), capture_output=True, text=True)
            assert run.returncode == 0, run.stdout[-300:] + run.stderr[-300:]
            app.try_load()
            assert app.library is not None and not app.library["songs"]

        def partial():
            files = [f for f in MUSIC.rglob("*") if f.is_file()]
            for file in rng.sample(files, len(files) // 3):
                file.unlink()

        def edited():
            victims = rng.sample(ids, min(40, len(ids)))
            path = work / "edit.json"
            path.write_text(json.dumps({"version": 1, "songs": [{"id": i, "set": {"album": "Drill", "title": f"Changed {i}", "year": 1999}} for i in victims]}))
            run = subprocess.run(app.ctl("retag", str(path), "--apply"), capture_output=True, text=True)
            assert run.returncode == 0, run.stdout[-300:]

        def edited_and_lost():
            edited()
            partial()

        for number in range(options.rounds):
            print(f"Round {number + 1}", flush=True)
            for label, damage in (("reset, nothing left", bare), ("reset, then set up in Finder", empty_library), ("a third of the files lost", partial),
                                  ("songs renamed since the copy", edited), ("renamed and files lost", edited_and_lost)):
                started = time.monotonic()
                damage()
                broken = differences(app, want)  # a test that can't fail proves nothing: the damage has to show
                print(f"       damage shows: {'; '.join(broken) or 'NOTHING'}", flush=True)
                results.append((f"damage shows before: {label}", bool(broken), 0, "; ".join(broken)))
                done = job(app, app.rebuild)
                if not done["ok"]:
                    results.append((label, False, 0, f"{done['outcome']['reason']} | {done['log'][-3:]}"))
                    print(f"  FAIL {label}: {results[-1][3]}", flush=True)
                    continue
                compare(app, want, label, started)

            # 3. A cable pulled at a random song, then run again
            started = time.monotonic()
            bare()
            real_copy, count, stop_at = web.copy_checked, [0], rng.randrange(1, len(want["music"]))

            def dies(source, out):
                count[0] += 1
                if count[0] == stop_at:
                    raise OSError(5, "Input/output error")
                return real_copy(source, out)
            web.copy_checked = dies
            first = job(app, app.rebuild)
            web.copy_checked = real_copy
            refused = not first["ok"] and not (CONTROL / "iTunes/iTunesDB").exists()
            second = job(app, app.rebuild)
            if refused and second["ok"]:
                compare(app, want, f"cable pulled at song {stop_at}, then run again", started)
            else:
                results.append(("cable pulled", False, 0, str(first["outcome"]) + str(second["outcome"])))
                print("  FAIL cable pulled", results[-1][3], flush=True)

            # 4. One flipped bit in the copy: nothing may be touched
            started = time.monotonic()
            victim = rng.choice([f for f in (songs / "iPod-songs").rglob("*") if f.is_file() and f.name != web.RESTORE_FILE])
            good = victim.read_bytes()
            bad = bytearray(good)
            bad[rng.randrange(len(bad))] ^= 1 << rng.randrange(8)
            victim.write_bytes(bad)
            before = tree(MUSIC)
            refused = job(app, app.rebuild)
            victim.write_bytes(good)
            fine = not refused["ok"] and "damaged" in (refused["outcome"]["reason"] or "") and tree(MUSIC) == before
            results.append(("one flipped bit is caught", fine, time.monotonic() - started, refused["outcome"]["reason"] or "not caught"))
            print(f"  {'ok  ' if fine else 'FAIL'} one flipped bit: {refused['outcome']['reason']}", flush=True)
        # 5. Songs the iPod can't play become songs it can, and nothing else about them changes
        started = time.monotonic()
        checked = job(app, app.health_check)
        assert checked["ok"], checked["log"][-3:]
        found = web.load_health()
        print(f"  Health: {len(found['checks'])} checks, {found['audio']['plays']} play, {len(found['audio']['songs'])} can't", flush=True)
        real_paths = {t["uniqueID"]: t["path"] for t in want["library"]["tracks"]}
        bad = [s for s in found["audio"]["songs"] if s["fixable"] and (mount / real_paths[s["id"]]).stat().st_size > 100_000][:2]
        if bad:
            ids = [s["id"] for s in bad]
            originals = work / "originals"
            originals.mkdir(exist_ok=True)
            before = {t["uniqueID"]: t for t in dump(app)["tracks"]}
            digests = {sha(mount / real_paths[i]) for i in ids}
            done = job(app, lambda: app.convert_songs(ids, str(originals)))
            after = {t["uniqueID"]: t for t in dump(app)["tracks"]}
            kept = {sha(f) for f in originals.rglob("*") if f.is_file()}
            formats = [sh("afinfo", str(mount / after[i]["path"])) for i in ids]
            bits = all("16-bit" in text or "from 16-bit" in text for text in formats)
            changed = {k for i in ids for k in before[i] if before[i][k] != after[i][k]}
            others = all(before[i] == after[i] for i in before if i not in ids)
            fine = done["ok"] and digests <= kept and bits and others and not {"title", "artist", "album", "albumArtist", "trackNumber", "playCount", "uniqueID"} & changed and len(after) == len(before)
            results.append(("24-bit songs converted, originals kept", fine, time.monotonic() - started, f"{done['outcome']['summary']}; fields that changed: {sorted(changed)}; 16-bit: {bits}; originals kept: {digests <= kept}"))
            print(f"  {'ok  ' if fine else 'FAIL'} convert: {results[-1][3]}", flush=True)
        else:
            print("  (no real 24-bit song in the sample, so no conversion to try)", flush=True)
        same = [t["path"] for t in sample if (PLAYER / t["path"]).exists() and sha(PLAYER / t["path"]) == want["music"].get(t["path"].replace("iPod_Control/Music/", "", 1))]
        results.append(("the sampled songs match the real iPod, byte for byte", len(same) == len(sample), 0, f"{len(same)} of {len(sample)}"))
        print(f"  {'ok  ' if results[-1][1] else 'FAIL'} sampled songs against the real iPod: {results[-1][3]}", flush=True)
        check = subprocess.run(app.ctl("check"), capture_output=True, text=True)
        print("\n".join(check.stdout.strip().splitlines()[-6:]), flush=True)
    finally:
        sh("hdiutil", "detach", str(mount), "-force", "-quiet")
        (work / "drill.sparseimage").unlink(missing_ok=True)
        shutil.rmtree(songs, ignore_errors=True)
    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)} of {len(results)} passed. Seed {options.seed}.")
    (work / "report.json").write_text(json.dumps(results, indent=1))
    return 1 if failed else 0


sys.exit(main())
