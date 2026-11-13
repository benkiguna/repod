"""A write says what really happened, and nothing can cut one short."""
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

from harness import Case, DEV, enrich, real_urlopen, serve, web
import meta  # noqa: E402  (after harness, which sets up the environment)


class Outcome(unittest.TestCase):
    def test_an_exit_code_is_not_the_whole_story(self):
        for kind, ok, log, result, reason in (
            ("edit", True, ["  Written. Backup: x"], "done", None),
            ("edit", False, ["error: Music is running - the database was not written", "Retag"], "failed", web.friendly("Music is running")),
            ("cover", True, ["  - SKIPPED A / B: no such album", "", "  Nothing to write."], "nothing", "A / B: no such album"),
            ("edit", True, ["", "  Nothing to write."], "nothing", None),
            ("write", True, ["  - SKIPPED A / B: bad image", "  Written. Backup: x"], "partial", "A / B: bad image"),
            ("cover", True, ["  Written. Backup: x", "  WARNING: too small"], "partial", "too small"),
            ("preview", True, ["  - SKIPPED A / B: bad image", "", "  Nothing to write."], "done", None),
        ):
            with self.subTest(log=log):
                answer = web.outcome(kind, ok, log)
                self.assertEqual((answer["result"], answer["reason"]), (result, reason))

    def test_a_stopped_job_was_stopped(self):
        self.assertEqual(web.outcome("copy", False, ["Stopped"], True)["result"], "stopped")


class Writes(Case):
    def cover(self):
        album = next(a for a in self.state()["library"]["albums"] if a["album"] and not a["art"])
        name = self.post("/api/cover-prepare", {"path": str(DEV.parent / "assets/apple-touch-icon.png")})[1]["name"]
        return {"key": f"{album['artist']} | {album['album']}", "name": name, "confirm": True}

    def test_a_cover_that_was_skipped_is_not_reported_as_written(self):
        body = self.cover()
        self.fault("cover-skip")
        self.assertEqual(self.job("/api/cover-set", body)["outcome"]["result"], "nothing")

    def test_a_write_that_partly_worked_reloads_the_library(self):
        body = self.cover()
        before = self.state()["library"]["missingCount"]
        self.fault("cover-warn")
        job = self.job("/api/cover-set", body)
        self.assertEqual(job["outcome"]["result"], "partial")
        self.assertEqual(self.state()["library"]["missingCount"], before - 1)

    def test_a_failed_write_says_why_and_changes_nothing(self):
        song = self.state()["library"]["songs"][0]
        self.fault("music")
        job = self.job("/api/songs-remove", {"ids": [song["id"]], "confirm": True})
        self.assertEqual(job["outcome"]["result"], "failed")
        self.assertIn("Quit Music", job["outcome"]["reason"])
        self.assertIn(song["id"], [t["uniqueID"] for t in self.tracks()])


class OneAtATime(Case):
    def test_nothing_starts_during_a_read(self):
        self.state()
        self.assertTrue(self.app.job.claim())
        self.addCleanup(self.app.job.release)
        self.assertEqual(self.post("/api/backup-create")[0], 409)
        self.assertEqual(self.post("/api/load")[0], 409)
        self.assertEqual(self.post("/api/eject")[0], 409)
        self.assertTrue(self.state()["reading"])

    def test_a_second_tab_does_not_read_at_the_same_time(self):
        self.assertTrue(self.app.job.claim())
        self.addCleanup(self.app.job.release)
        state = self.state()
        self.assertIsNone(state["library"])
        self.assertTrue(state["reading"])

    def test_a_read_gives_the_ipod_back(self):
        self.state()
        self.assertEqual(self.post("/api/load")[0], 200)
        self.assertFalse(self.app.job.reading)
        self.assertEqual(self.post("/api/backup-create")[0], 200)


class ControlC(unittest.TestCase):
    def test_a_write_outlives_ctrl_c(self):
        state = Path(tempfile.mkdtemp(prefix="enrich-ctrl-c-"))
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        server = subprocess.Popen([sys.executable, str(DEV / "serve.py"), "--port", str(port), "--songs", "40", "--state", str(state)],
                                  start_new_session=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
        self.addCleanup(lambda: server.poll() is None and server.kill())
        base = f"http://127.0.0.1:{port}"
        songs = None
        for _ in range(200):
            try:
                with real_urlopen(f"{base}/api/state") as response:
                    songs = json.load(response)["library"]["songs"]
                break
            except OSError:
                time.sleep(.05)
        (state / "ipod/fault").write_text("slow")
        body = json.dumps({"ids": [s["id"] for s in songs[:20]], "confirm": True}).encode()
        with real_urlopen(urllib.request.Request(f"{base}/api/songs-remove", body, {"Content-Type": "application/json"})) as response:
            self.assertEqual(response.status, 200)
        time.sleep(.4)
        os.killpg(server.pid, signal.SIGINT)  # what Ctrl-C does: the whole foreground group
        output = server.communicate(timeout=30)[0]
        self.assertIn("being written", output)
        self.assertEqual(len(json.loads((state / "ipod/library.json").read_text())["tracks"]), 20)


class PlainWords(unittest.TestCase):
    def test_what_ipodctl_says_becomes_what_to_do(self):
        for said, meant in (("error: no iPod found", "isn't connected"), ("Music is running - the database was not written", "Quit Music"),
                            ("connected iPods: 2 - choose one with --volume (nano, classic, GUID or path)", "More than one iPod"),
                            ("read-only: volume mounted read-only - the database was not written", "can only be read"),
                            ("no audio files found", "no songs there"),
                            ("couldn't be opened: Operation not permitted", "Removable Volumes")):
            with self.subTest(said):
                self.assertIn(meant, web.friendly(said))

    def test_an_unknown_message_is_kept(self):
        self.assertEqual(web.friendly("error: the disk is full"), "the disk is full")

    def test_a_path_does_not_reach_the_page(self):
        self.assertEqual(web.friendly("A / B: cannot load the image /private/tmp/x.png"), "A / B: the image could not be read")


class ErrorsOverHttp(Case):
    def test_no_exception_names_reach_the_page(self):
        self.state()
        self.fault("unplugged")
        status, answer = self.request("/api/backups")
        self.assertEqual(status, 503)
        self.assertEqual(answer["error"], web.friendly("no iPod found"))

    def test_a_load_that_fails_says_what_to_do(self):
        self.fault("unplugged")
        self.assertIn("isn't connected", self.state()["loadError"])

    def test_a_busy_server_says_so_kindly(self):
        self.state()
        self.fault("slow")
        self.post("/api/regroup-preview")
        status, answer = self.post("/api/backup-create")
        self.assertEqual((status, answer["error"]), (409, web.BUSY))


class Counts(unittest.TestCase):
    def test_a_finished_write_says_how_many(self):
        for kind, line, said in (("add", "  Added tracks: 12", "Added 12 songs to the iPod"), ("add", "  Added tracks: 1", "Added 1 song to the iPod"),
                                 ("remove", "  tracks           3 of 40", "Deleted 3 songs from the iPod"), ("edit", "  songs to change   1,", "Changed 1 song"),
                                 ("write", "  covers to write  1200", "Wrote 1,200 covers to the iPod"), ("regroup", "  albums to rebuild  2", "Put 2 albums back in order")):
            with self.subTest(said):
                self.assertEqual(web.outcome(kind, True, [line])["summary"], said)

    def test_no_number_no_sentence(self):
        self.assertIsNone(web.outcome("edit", True, ["something else"])["summary"])
        self.assertIsNone(web.outcome("preview", True, ["  covers to write  3"])["summary"])


class CountsOverHttp(Case):
    def test_an_add_reports_what_was_added(self):
        songs = Path(tempfile.mkdtemp())
        for name in ("one.mp3", "two.mp3"):
            (songs / name).write_bytes(b"x")
        job = self.job("/api/add", {"path": str(songs), "confirm": True}) if self.state() else None
        self.assertEqual(job["outcome"]["summary"], "Added 2 songs to the iPod")


class Backups(Case):
    def make(self, n):
        for number in range(n):
            self.assertTrue(self.job("/api/backup-create")["ok"])

    def ids(self):
        return [b["id"] for b in self.get("/api/backups")["backups"]]

    def test_the_last_ten_stay_and_the_oldest_is_starred_at_first(self):
        self.state()
        self.make(12)
        listed = self.get("/api/backups")["backups"]
        self.assertEqual(len(listed), 11)  # the twelfth backup removed the second oldest; the oldest is starred
        self.assertTrue(listed[-1]["starred"])

    def test_a_starred_backup_stays(self):
        self.state()
        self.make(2)
        second = self.ids()[0]
        self.assertEqual(self.post("/api/backup-star", {"id": second})[0], 200)
        self.make(12)
        self.assertIn(second, self.ids())
        self.assertEqual(len(self.ids()), 12)  # ten newest, and two starred

    def test_an_unstarred_one_goes_when_cleaned(self):
        self.state()
        self.make(11)
        oldest = self.ids()[-1]
        self.post("/api/backup-star", {"id": oldest, "star": False})
        self.assertEqual(self.get("/api/backups")["backups"][-1]["stays"], False)
        self.assertEqual(self.job("/api/backup-clean", {"confirm": True})["outcome"]["summary"], "Removed 1 older backup")
        self.assertEqual(len(self.ids()), 10)

    def test_cleaning_needs_confirm(self):
        self.state()
        self.assertEqual(self.post("/api/backup-clean")[0], 400)

    def test_a_failed_write_keeps_every_backup(self):
        song = self.state()["library"]["songs"][0]
        self.make(10)
        before = self.ids()
        self.fault("music")
        self.job("/api/songs-remove", {"ids": [song["id"]], "confirm": True})
        self.assertEqual(self.ids(), before)

    def test_nonsense_is_not_starred(self):
        self.assertEqual(self.post("/api/backup-star", {"id": "../x"})[0], 400)


class Mode(Case):
    def test_an_ipod_that_takes_writes_says_so(self):
        self.assertEqual(self.state()["writing"], {"allowed": True, "why": ""})

    def test_one_that_can_only_be_read_says_why(self):
        self.fault("read-only")
        self.assertEqual(self.state()["writing"], {"allowed": False, "why": "this model has not been confirmed"})


class AddWithNames(Case):
    """Adding after a MusicBrainz lookup: each song is chosen on its own, and the choices go in with it in one write."""
    def folder(self, cover=False):
        album = Path(tempfile.mkdtemp()) / "Fake Release"
        album.mkdir()
        for name in ("01 Song 1.mp3", "02 Song 2.mp3"):
            (album / name).write_bytes(b"x")
        if cover:
            (album / "cover.png").write_bytes(serve.noise_png("folder"))
        return album

    def looked_up(self, **kw):
        folder = self.folder(**kw)
        self.state()
        self.assertTrue(self.job("/api/add-lookup", {"path": str(folder)})["ok"])
        return folder, self.get("/api/add-review")

    def test_looking_up_writes_nothing_and_gives_each_song_its_candidates(self):
        folder = self.folder()
        count = len(self.state()["library"]["songs"])
        self.assertTrue(self.job("/api/add-lookup", {"path": str(folder)})["ok"])
        self.assertEqual(len(self.tracks()), count)
        review = self.get("/api/add-review")
        self.assertEqual([s["title"] for s in review["songs"]], ["Song 1", "Song 2"])
        self.assertEqual(review["songs"][0]["seconds"], 200)
        first = review["matches"][review["songs"][0]["path"]]
        self.assertGreater(len(first), 1, "more than one to choose from")
        self.assertEqual((first[0]["album"], first[0]["track"], first[0]["level"]), ("Fake Release", 1, "high"))
        self.assertTrue(first[0]["cover"].startswith("https://coverartarchive.org/release/"))

    def test_the_cover_the_files_have_is_shown(self):
        _, review = self.looked_up(cover=True)
        name = review["songs"][0]["art"]
        status, _ = self.request(f"/add-art/{name}")
        self.assertEqual(status, 200)

    def test_each_song_goes_in_with_its_own_values_and_one_can_be_left_out(self):
        folder, review = self.looked_up()
        one, two = (s["path"] for s in review["songs"])
        songs = [{"path": one, "set": {"title": "Opening", "album": "Chosen Album", "albumartist": "Chosen Artist", "year": 2001, "track": 7, "compilation": True}},
                 {"path": two, "skip": True}]
        job = self.job("/api/add", {"path": str(folder), "confirm": True, "songs": songs})
        self.assertEqual(job["outcome"]["summary"], "Added 1 song to the iPod")
        added = [t for t in self.tracks() if t["title"] == "Opening"]
        self.assertEqual([(t["album"], t["albumArtist"], t["year"], t["trackNumber"], t["compilation"]) for t in added], [("Chosen Album", "Chosen Artist", 2001, 7, True)])
        self.assertFalse([t for t in self.tracks() if t["title"] == "Song 2"])
        self.assertEqual(self.get("/api/add-review"), {}, "what was added leaves the review")

    def test_a_cover_picked_for_a_new_album_goes_in_with_it(self):
        folder, review = self.looked_up()
        status, picked = self.post("/api/add-cover-prepare", {"url": "https://coverartarchive.org/release/2222/front-500"})
        self.assertEqual(status, 200)
        self.assertEqual(self.request(picked["image"])[0], 200)
        songs = [{"path": s["path"], "cover": picked["name"]} for s in review["songs"]]
        self.assertTrue(self.job("/api/add", {"path": str(folder), "confirm": True, "songs": songs})["ok"])
        manifest = json.loads((enrich.HOME / "add-manifest.json").read_text())
        self.assertTrue(all(Path(f["cover"]).name == picked["name"] for f in manifest["files"]))

    def test_what_the_page_cannot_vouch_for_is_refused(self):
        folder, review = self.looked_up()
        path = review["songs"][0]["path"]
        for songs in ([{"path": "/etc/passwd"}], [{"path": path, "set": {"rating": 5}}], [{"path": path, "set": {"year": "1999"}}],
                      [{"path": path, "cover": "../../secret.png"}], [{"path": s["path"], "skip": True} for s in review["songs"]]):
            with self.subTest(songs=songs):
                self.assertEqual(self.post("/api/add", {"path": str(folder), "confirm": True, "songs": songs})[0], 400)

    def test_an_answer_for_another_folder_is_refused(self):
        folder, _ = self.looked_up()
        status, answer = self.post("/api/add", {"path": str(folder.parent), "confirm": True, "songs": []})
        self.assertEqual(status, 400)
        self.assertIn("Look these songs up again", answer["error"])

    def test_choices_stay_with_the_review(self):
        folder, review = self.looked_up()
        choices = {"songs": {review["songs"][0]["path"]: {"pick": "file", "edit": {"title": "Kept"}, "skip": False}}, "covers": {}}
        self.assertEqual(self.post("/api/add-choices", {"path": str(folder), "choices": choices})[0], 200)
        self.assertEqual(self.get("/api/add-review")["choices"], choices)
        self.assertEqual(self.post("/api/add-choices", {"path": "/elsewhere", "choices": choices})[0], 400)

    def test_a_song_can_be_searched_for_with_other_words(self):
        self.state()
        status, answer = self.post("/api/add-search", {"title": "Other Song", "artist": "Fake Artist", "album": "Fake Release", "track": 2, "seconds": 201})
        self.assertEqual(status, 200)
        self.assertEqual(answer["results"][0]["title"], "Other Song")
        self.assertEqual(self.post("/api/add-search", {"artist": "Fake Artist"})[0], 400)

    def test_without_the_option_nothing_changes(self):
        folder = self.folder()
        self.state()
        self.assertEqual(self.job("/api/add", {"path": str(folder), "confirm": True})["outcome"]["summary"], "Added 2 songs to the iPod")
        self.assertEqual({t["album"] for t in self.tracks() if t["title"] == "Song 1"}, {"Fake Release"})


class SongQueries(unittest.TestCase):
    """What a song still in its folder is looked up with: everything its file knows, and only the title required."""
    song = {"title": "Open Water", "artist": "Mira Vale feat. Someone", "album": "Tideline (Deluxe)", "year": 2017, "track": 3, "tracks": 12, "seconds": 241.5}

    def test_every_known_field_raises_a_result_and_only_the_title_is_required(self):
        first = meta.recording_queries(self.song)[0]
        self.assertTrue(first.startswith('+recording:"Open Water"'))
        for part in ('artist:"Mira Vale"^3', 'release:"Tideline"^2', "dur:[237500 TO 245500]^2", "tnum:3", "tracks:12", "date:2017*"):
            self.assertIn(part, first)
        self.assertNotIn("+artist", first, "a wrong artist must not hide the right recording")

    def test_ids_come_first(self):
        queries = meta.recording_queries({**self.song, "isrc": "gb-aaa-17-00001", "mbRecording": "abc"})
        self.assertEqual(queries[:2], ["rid:abc", "isrc:GBAAA1700001"])

    def test_a_song_whose_name_says_nothing_is_found_by_its_album_and_place(self):
        queries = meta.recording_queries({**self.song, "title": "Track 03"})
        self.assertEqual(len(queries), 1)
        self.assertTrue(queries[0].startswith('+release:"Tideline" +artist:"Mira Vale"'))
        self.assertIn("tnum:3^4", queries[0])

    def test_what_the_file_does_not_know_is_not_held_against_a_candidate(self):
        bare = {"title": "Open Water", "artist": "Mira Vale"}
        right = {"title": "Open Water", "artist": "Mira Vale", "albumartist": "Mira Vale", "album": "Tideline", "seconds": 300, "year": 1990}
        self.assertGreaterEqual(meta.candidate_score(bare, right), 0.95)
        self.assertLess(meta.candidate_score(self.song, right), meta.candidate_score(self.song, {**right, "seconds": 241, "year": 2017}))
