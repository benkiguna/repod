"""The whole iPod back from a copy of its songs and the backup saved with them. A failure here costs someone their music,
so most of these change something at random, from a fixed seed, and check that nothing is lost or wrongly accepted."""
import hashlib
import json
import random
import shutil
import tempfile
from pathlib import Path

from harness import Case, IPOD, MOUNT, web

MUSIC = MOUNT / "iPod_Control/Music"
DATABASE = MOUNT / "iPod_Control/iTunes/iTunesDB"


def fingerprint() -> dict:
    return {str(f.relative_to(MUSIC)): hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(MUSIC.rglob("*")) if f.is_file()}


class Rebuild(Case):
    def setUp(self):
        super().setUp()
        self.state()
        self.folder = Path(tempfile.mkdtemp(prefix="songs-"))
        self.addCleanup(shutil.rmtree, self.folder, ignore_errors=True)
        music = MUSIC / "F00"
        rng = random.Random(7)
        for name in sorted(music.iterdir()):  # the fake's songs are all silence; a checksum should see them apart
            name.write_bytes(rng.randbytes(rng.randrange(1000, 40000)))
        self.songs = fingerprint()
        self.tracks_before = self.tracks()

    def copy(self):
        job = self.job("/api/copy-songs", {"dest": str(self.folder), "confirm": True})
        self.assertTrue(job["ok"], job["log"][-3:])
        return job

    def reset(self):
        """What a reset leaves: an empty disk, no library, and an iPod nothing can open."""
        shutil.rmtree(MUSIC)
        MUSIC.mkdir()
        DATABASE.unlink()
        library = json.loads((IPOD / "library.json").read_text())
        (IPOD / "library.json").write_text(json.dumps({**library, "tracks": [], "covers": {}, "outOfPlace": []}))
        self.post("/api/load")

    def rebuild(self):
        return self.job("/api/rebuild", {"confirm": True})

    def assertWhole(self):
        self.assertEqual(fingerprint(), self.songs)
        self.assertEqual(self.tracks(), self.tracks_before)

    def test_a_copy_describes_itself_and_keeps_its_backup(self):
        job = self.copy()
        self.assertEqual(job["outcome"]["summary"], "Saved 8 songs and the library with them")
        copy = self.state()["songCopy"]
        self.assertEqual((copy["songs"], copy["complete"]), (len(self.songs), True))
        listed = {b["id"]: b for b in self.get("/api/backups")["backups"]}
        self.assertTrue(listed[copy["backup"]]["paired"])

    def test_clean_up_never_takes_the_backup_of_the_copy(self):
        self.copy()
        paired = self.state()["songCopy"]["backup"]
        for _ in range(12):
            self.job("/api/backup-create")
        self.assertIn(paired, [b["id"] for b in self.get("/api/backups")["backups"]])

    def test_after_a_reset_everything_comes_back(self):
        self.copy()
        self.reset()
        state = self.state()
        self.assertIsNone(state["library"])
        self.assertIn("Restore everything", state["loadError"])
        self.assertIsNotNone(state["songCopy"])
        self.assertTrue(state["connected"])
        job = self.rebuild()
        self.assertEqual(job["outcome"]["summary"], f"Restored {len(self.tracks_before)} songs to the iPod")
        self.assertWhole()
        self.assertEqual(len(self.state()["library"]["songs"]), len(self.tracks_before))

    def test_edits_made_after_the_copy_are_undone_by_it(self):
        song = self.state()["library"]["songs"][0]
        self.copy()
        self.job("/api/songs-retag", {"songs": [{"id": song["id"], "set": {"title": "Renamed later"}}], "confirm": True})
        self.assertTrue(self.rebuild()["ok"])
        self.assertWhole()

    def test_it_can_run_twice(self):
        self.copy()
        self.reset()
        self.rebuild()
        job = self.rebuild()
        self.assertTrue(job["ok"], job["log"][-3:])
        self.assertIn("0 songs to put back", " ".join(job["log"]))
        self.assertWhole()

    def test_whatever_is_left_after_a_partial_loss_is_made_whole(self):
        self.copy()
        for seed in range(25):
            rng = random.Random(seed)
            with self.subTest(seed=seed):
                for name in self.songs:
                    if rng.random() < .5:
                        (MUSIC / name).unlink(missing_ok=True)
                job = self.rebuild()
                self.assertTrue(job["ok"], job["log"][-3:])
                self.assertEqual(fingerprint(), self.songs)

    def test_any_damage_to_the_copy_stops_it_before_the_ipod_is_touched(self):
        self.copy()
        copied = sorted(f for f in (self.folder / "iPod-songs").rglob("*") if f.is_file() and f.name != web.RESTORE_FILE)
        originals = {f: f.read_bytes() for f in copied}
        self.reset()
        for seed in range(25):
            rng = random.Random(seed)
            victim, kind = rng.choice(copied), rng.choice(("flip", "cut", "gone", "grow"))
            data = bytearray(originals[victim])
            if kind == "flip":
                data[rng.randrange(len(data))] ^= 1 << rng.randrange(8)
            elif kind == "cut":
                del data[rng.randrange(1, len(data)):]
            elif kind == "grow":
                data += b"\0"
            with self.subTest(seed=seed, kind=kind):
                victim.unlink() if kind == "gone" else victim.write_bytes(data)
                job = self.rebuild()
                self.assertFalse(job["ok"])
                self.assertIn("damaged or missing", job["outcome"]["reason"])
                self.assertEqual(fingerprint(), {})
                self.assertFalse(DATABASE.exists())
                victim.write_bytes(originals[victim])
        self.assertTrue(self.rebuild()["ok"])
        self.assertWhole()

    def test_a_check_finds_the_same_damage_without_an_ipod(self):
        self.copy()
        self.assertEqual(self.job("/api/copy-check")["outcome"]["summary"], f"Checked {len(self.songs)} files: every one matches")
        victim = next(f for f in (self.folder / "iPod-songs").rglob("*.wav"))
        victim.write_bytes(b"x")
        outcome = self.job("/api/copy-check")["outcome"]
        self.assertEqual(outcome["result"], "partial")
        self.assertIn("damaged or missing", outcome["reason"])

    def test_a_different_file_on_the_ipod_is_never_overwritten(self):
        self.copy()
        name = next(iter(self.songs))
        (MUSIC / name).write_bytes(b"something newer")
        job = self.rebuild()
        self.assertFalse(job["ok"])
        self.assertIn("differ from the copy", job["outcome"]["reason"])
        self.assertEqual((MUSIC / name).read_bytes(), b"something newer")
        self.assertEqual(self.tracks(), self.tracks_before)

    def test_another_ipods_backup_is_refused_and_leaves_nothing_behind(self):
        self.copy()
        self.reset()
        self.fault("other-ipod")
        job = self.rebuild()
        self.assertIn("another iPod", job["outcome"]["reason"])
        self.assertFalse(DATABASE.exists())
        self.assertEqual(fingerprint(), {})

    def test_a_missing_backup_is_refused(self):
        self.copy()
        backup = self.state()["songCopy"]["backup"]
        for folder in web.BACKUPS.glob(f"*/{backup}"):
            shutil.rmtree(folder)
        self.reset()
        self.assertIn("is gone", self.rebuild()["outcome"]["reason"])

    def test_no_room_is_found_out_first(self):
        self.copy()
        self.reset()
        from collections import namedtuple
        from unittest import mock
        with mock.patch.object(web.shutil, "disk_usage", return_value=namedtuple("Usage", "total used free")(1 << 30, 1 << 30, 1000)):
            job = self.rebuild()
        self.assertIn("needs", job["outcome"]["reason"])
        self.assertEqual(fingerprint(), {})
        self.assertFalse(DATABASE.exists())

    def test_a_pulled_cable_halfway_can_be_run_again(self):
        self.copy()
        self.reset()
        from unittest import mock
        real, calls = web.copy_checked, []

        def dies(source, out):
            calls.append(out)
            if len(calls) == 4:
                raise OSError(5, "Input/output error")
            return real(source, out)
        with mock.patch.object(web, "copy_checked", dies):
            job = self.rebuild()
        self.assertIn("run this again", job["outcome"]["reason"])
        self.assertEqual(len(fingerprint()), 3)
        self.assertEqual([f for f in MUSIC.rglob("*") if ".part" in f.name], [])
        self.assertFalse(DATABASE.exists())  # no library listing songs that aren't there
        self.assertTrue(self.rebuild()["ok"])
        self.assertWhole()

    def test_a_copy_that_was_stopped_can_be_run_again_and_restores_nothing_until_then(self):
        from unittest import mock

        def stop_soon(source, out, real=web.copy_checked):
            self.app.job.cancel.set()
            return real(source, out)
        with mock.patch.object(web, "copy_checked", stop_soon):
            self.post("/api/copy-songs", {"dest": str(self.folder), "confirm": True})
            job = self.wait()
        self.assertEqual(job["outcome"]["result"], "stopped")
        self.assertFalse(self.state()["songCopy"]["complete"])
        self.assertIn("no finished copy", self.rebuild()["outcome"]["reason"])
        self.copy()
        self.assertTrue(self.state()["songCopy"]["complete"])

    def test_a_second_copy_only_takes_what_changed(self):
        self.copy()
        name = next(iter(self.songs))
        import os
        (MUSIC / name).write_bytes(b"a song that came later")
        os.utime(MUSIC / name, (2_000_000_000, 2_000_000_000))
        job = self.copy()
        self.assertIn("copied 1, skipped 7", " ".join(job["log"]))

    def test_a_copy_too_big_for_the_disk_is_refused_first(self):
        from collections import namedtuple
        from unittest import mock
        with mock.patch.object(web.shutil, "disk_usage", return_value=namedtuple("Usage", "total used free")(1 << 30, 1 << 30, 1000)):
            job = self.job("/api/copy-songs", {"dest": str(self.folder), "confirm": True})
        self.assertIn("more room", job["outcome"]["reason"])
        self.assertEqual(list(self.folder.iterdir()), [])
        self.assertIsNone(self.state()["songCopy"])

    def test_a_song_added_after_the_copy_keeps_its_file(self):
        self.copy()
        later = MUSIC / "F00/LATER.mp3"
        later.write_bytes(b"added since")
        self.assertTrue(self.rebuild()["ok"])
        self.assertEqual(later.read_bytes(), b"added since")

    def test_a_restore_needs_confirm(self):
        self.assertEqual(self.post("/api/rebuild")[0], 400)

    def test_no_copy_no_restore(self):
        self.assertIn("no finished copy", self.rebuild()["outcome"]["reason"])
