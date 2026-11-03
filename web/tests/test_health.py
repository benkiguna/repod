"""What the page shows about an iPod, from what `ipodctl` really prints, and the repairs that follow from it."""
import tempfile
import unittest
from collections import namedtuple
from pathlib import Path
from unittest import mock

from harness import Case, tidy, web

DIAGNOSE = """DEVELOPER MODE: writing outside the confirmed models (iPod (5th generation)) - a backup comes first
iPod (5th generation) at /Volumes/iPod - policy developer

library
  OK   format               scheme 0 (unsigned) · header 244 B · version 0x75  [required]
  OK   rebuild              rebuilt in memory: 1302670 B, byte-identical  [required]

artwork
  WARN artwork              771 images · formats 1028, 1029 · 1028, 1029 not measured · new artwork disabled  [advisory]

disk
  OK   music                Music is not running  [advisory]

Developer Mode: writing outside the confirmed models.

measurements
  sysinfo: FamilyID 6
"""
AUDIO = """DEVELOPER MODE: writing outside the confirmed models (iPod (5th generation)) - a backup comes first
File formats
  the iPod plays          741
  the iPod does not play  2
  unreadable              0
  id=1269  Paper Moons (Original Soundtrack) / Long Way Home
      ALAC 24-bit 48 kHz: 24-bit Apple Lossless; the iPod plays 16-bit up to 48 kHz -> Apple Lossless 16-bit 44.1 kHz
  id=7  One / Two / Three
      FLAC: no way to play this (cannot be fixed)

  To fix: --fix ID[,ID] or --fix-all (add --apply to write).
"""


class Reading(unittest.TestCase):
    def test_diagnose(self):
        found = web.parse_diagnose(DIAGNOSE)
        self.assertEqual([(c["status"], c["id"]) for c in found["checks"]], [("OK", "format"), ("OK", "rebuild"), ("WARN", "artwork"), ("OK", "music")])
        self.assertEqual(found["checks"][2]["weight"], "advisory")
        self.assertTrue(found["checks"][2]["detail"].endswith("new artwork disabled"))
        self.assertEqual(found["verdict"], "Developer Mode: writing outside the confirmed models.")

    def test_audio(self):
        found = web.parse_audio(AUDIO)
        self.assertEqual((found["plays"], found["unreadable"]), (741, 0))
        song = found["songs"][0]
        self.assertEqual((song["id"], song["album"], song["title"], song["format"]), (1269, "Paper Moons (Original Soundtrack)", "Long Way Home", "ALAC 24-bit 48 kHz"))
        self.assertEqual(song["target"], "Apple Lossless 16-bit 44.1 kHz")
        self.assertTrue(song["fixable"])

    def test_a_song_that_cannot_be_converted_says_so(self):
        song = web.parse_audio(AUDIO)["songs"][1]
        self.assertEqual((song["fixable"], song["reason"]), (False, "no way to play this"))

    def test_a_slash_in_a_title_stays_in_it(self):
        self.assertEqual(web.parse_audio(AUDIO)["songs"][1]["title"], "Two / Three")

    def test_sort_albums(self):
        text = "Sort field repair\n  albums  2\n  tracks  3\n\n  One\n      Song\n          sort album: 'a' -> 'b'\n\n  Two\n      Other\n          x\n\n  That was a preview. To write: ipodctl fix-sort --apply\n"
        self.assertEqual(web.parse_sort(text), ["One", "Two"])
        self.assertEqual(web.parse_sort("No sort fields split any albums. Nothing to repair.\n"), [])

    def test_no_split_no_hint(self):
        self.assertEqual(tidy.sort_hints([], []), [])


class Checking(Case):
    def test_nothing_is_changed_by_a_check(self):
        before = self.state()["library"]["songs"]
        self.assertEqual(self.get("/api/health"), {})
        job = self.job("/api/health")
        self.assertTrue(job["ok"], job["log"][-3:])
        self.assertEqual(self.state()["library"]["songs"], before)

    def test_it_is_remembered_and_counted(self):
        self.state()
        self.job("/api/health")
        health = self.get("/api/health")
        self.assertEqual([s["title"] for s in health["audio"]["songs"]], ["Master One", "Master Two"])
        self.assertEqual(self.state()["healthIssues"], 3)  # two songs and the artwork warning

    def test_an_ipod_that_went_away_says_so(self):
        self.state()
        self.fault("unplugged")
        self.assertEqual(self.job("/api/health")["outcome"]["result"], "failed")


class Converting(Case):
    def ids(self):
        self.state()
        self.job("/api/health")
        return [s["id"] for s in self.get("/api/health")["audio"]["songs"]]

    def test_the_originals_go_where_they_were_asked_to(self):
        ids, folder = self.ids(), tempfile.mkdtemp()
        job = self.job("/api/audio-fix", {"ids": ids, "originals": folder, "confirm": True})
        self.assertEqual(job["outcome"]["summary"], "Converted 2 songs")
        self.assertEqual(len(list(Path(folder).iterdir())), 2)
        self.assertEqual(self.get("/api/health")["audio"]["songs"], [])

    def test_one_album_at_a_time_leaves_the_rest(self):
        ids = self.ids()
        self.job("/api/audio-fix", {"ids": ids[:1], "discard": True, "confirm": True})
        self.assertEqual([s["id"] for s in self.get("/api/health")["audio"]["songs"]], ids[1:])

    def test_a_decision_about_the_originals_is_required(self):
        status, answer = self.post("/api/audio-fix", {"ids": self.ids(), "confirm": True})
        self.assertEqual(status, 400)
        self.assertIn("originals", answer["error"])

    def test_it_needs_confirm(self):
        self.assertEqual(self.post("/api/audio-fix", {"ids": self.ids(), "discard": True})[0], 400)

    def test_no_room_for_the_originals_is_refused_first(self):
        ids, folder = self.ids(), tempfile.mkdtemp()
        with mock.patch.object(web.shutil, "disk_usage", return_value=namedtuple("Usage", "total used free")(1 << 30, 1 << 30, 1000)):
            status, answer = self.post("/api/audio-fix", {"ids": ids, "originals": folder, "confirm": True})
        self.assertEqual(status, 400)
        self.assertIn("Nothing was changed", answer["error"])
        self.assertEqual(len(self.get("/api/health")["audio"]["songs"]), 2)

    def test_nonsense_is_refused(self):
        self.state()
        for body in ({"ids": ["1;rm"], "discard": True, "confirm": True}, {"ids": [], "discard": True, "confirm": True}, {"ids": [1], "originals": "/no/such/folder", "confirm": True}):
            self.assertEqual(self.post("/api/audio-fix", body)[0], 400)

    def test_a_failed_conversion_keeps_the_list(self):
        ids = self.ids()
        self.fault("music")
        job = self.job("/api/audio-fix", {"ids": ids, "discard": True, "confirm": True})
        self.assertEqual(job["outcome"]["result"], "failed")
        self.assertEqual(len(self.get("/api/health")["audio"]["songs"]), 2)


class SortFields(Case):
    def hint(self):
        return next((h for h in self.state()["library"]["hints"] if h["kind"] == "sort"), None)

    def test_a_split_album_is_on_the_to_fix_list(self):
        hint = self.hint()
        self.assertEqual(hint["action"], "fix-sort")
        self.assertIn("Sort Split", hint["title"])
        self.assertIn("shows this album as several", hint["note"])
        self.assertEqual(len(hint["ids"]), 2)

    def test_the_repair_clears_it(self):
        self.state()
        job = self.job("/api/fix-sort", {"confirm": True})
        self.assertTrue(job["ok"])
        self.assertEqual(job["outcome"]["summary"], "Repaired 2 songs")
        self.assertIsNone(self.hint())

    def test_it_needs_confirm(self):
        self.state()
        self.assertEqual(self.post("/api/fix-sort")[0], 400)


class Allowing(Case):
    def test_it_is_kept_and_takes_effect_at_once(self):
        self.fault("read-only")
        self.assertFalse(self.state()["writing"]["allowed"])
        status, answer = self.post("/api/settings", {"developer": True, "confirm": True})
        self.assertEqual((status, answer["developer"]), (200, True))
        self.assertTrue(self.state()["developer"])
        self.assertEqual(web.load_settings(), {"developer": True})
        self.assertIn("--developer", self.app.ctl("info"))

    def test_it_can_be_taken_back(self):
        self.state()
        self.post("/api/settings", {"developer": True, "confirm": True})
        self.post("/api/settings", {"developer": False, "confirm": True})
        self.assertFalse(self.state()["developer"])
        self.assertNotIn("--developer", self.app.ctl("info"))

    def test_it_needs_confirm(self):
        self.state()
        self.assertEqual(self.post("/api/settings", {"developer": True})[0], 400)
        self.assertFalse(self.state()["developer"])

    def test_not_while_a_job_runs(self):
        self.state()
        self.assertTrue(self.app.job.claim())
        self.addCleanup(self.app.job.release)
        self.assertEqual(self.post("/api/settings", {"developer": True, "confirm": True})[0], 409)
