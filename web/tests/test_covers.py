"""One bad image or one bad answer is one album's problem, never the whole lookup's."""
import json
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from harness import Case, DEV, enrich, serve


class Pins(Case):
    def key(self):
        return next(c["key"] for c in self.state()["covers"] if c["album"]["album"])

    def test_a_mistyped_path_is_refused_and_not_saved(self):
        key = self.key()
        status, answer = self.post("/api/decide", {"key": key, "action": "pin", "pin": {"file": "/nope/typo.jpg"}})
        self.assertEqual(status, 400)
        self.assertIn("no file", answer["error"])
        self.assertEqual(enrich.load_decisions()["pin"], {})

    def test_something_that_is_not_an_image_is_refused(self):
        status, answer = self.post("/api/decide", {"key": self.key(), "action": "pin", "pin": {"file": str(DEV / "serve.py")}})
        self.assertEqual(status, 400)
        self.assertIn("not an image", answer["error"])

    def test_an_address_with_no_image_is_refused_and_not_saved(self):
        with mock.patch.object(enrich.urllib.request, "urlopen", side_effect=urllib.error.HTTPError("u", 404, "no", {}, None)):
            status, _ = self.post("/api/decide", {"key": self.key(), "action": "pin", "pin": {"url": "https://nothing.fake.test/a.png"}})
        self.assertEqual(status, 400)
        self.assertEqual(enrich.load_decisions()["pin"], {})

    def test_a_scan_survives_a_pin_saved_by_an_older_version(self):
        key = self.key()
        enrich.save_decisions({"skip": [], "accept": [], "pin": {key: {"file": "/nope/typo.jpg"}}})
        job = self.job("/api/find-covers")
        self.assertTrue(job["ok"], job["log"][-3:])
        covers = {c["key"]: c for c in self.state()["covers"]}
        self.assertEqual(covers[key]["status"], "error")
        self.assertGreater(sum(c["status"] in ("review", "auto") for c in covers.values()), 3)


class BadAnswers(Case):
    def test_an_answer_that_is_not_json_fails_one_album(self):
        real, calls = serve.fake_urlopen, []

        def flaky(request, timeout=None):
            calls.append(request)
            return serve.Answer(b"<html>down for maintenance</html>") if len(calls) == 1 else real(request, timeout)
        self.state()
        with mock.patch.object(enrich.urllib.request, "urlopen", flaky):
            job = self.job("/api/find-covers")
        self.assertTrue(job["ok"], job["log"][-3:])
        self.assertEqual(self.state()["counts"].get("error"), 1)

    def test_a_bad_answer_is_not_kept_in_the_cache(self):
        with mock.patch.object(enrich.urllib.request, "urlopen", lambda *_a, **_k: serve.Answer(b"<html>")):
            with self.assertRaises(ValueError):
                enrich.fetch("https://example.fake.test/x")
        self.assertEqual(enrich.fetch("https://covers.fake.test/ok.png", binary=True)[:4], b"\x89PNG")
        self.assertEqual(len(list(enrich.CACHE.iterdir())), 1)

    def test_an_album_with_no_name_asks_nobody(self):
        self.state()
        with mock.patch.object(enrich, "resolve") as asked:
            entry = enrich.lookup({"album": "", "albumArtist": ""}, enrich.load_decisions())
        asked.assert_not_called()
        self.assertEqual(entry["failure"], "none")


class Images(Case):
    def test_a_format_the_browser_cant_show_is_converted(self):
        if not shutil.which("sips"):
            self.skipTest("needs macOS")
        tiff = Path(tempfile.mkdtemp()) / "cover.tiff"
        subprocess.run(["sips", "-s", "format", "tiff", str(DEV.parent / "assets/apple-touch-icon.png"), "--out", str(tiff)], check=True, capture_output=True)
        answer = self.post("/api/cover-prepare", {"path": str(tiff)})[1]
        self.assertTrue(answer["name"].endswith(".png"))
        self.assertEqual(self.request(answer["image"])[0], 200)
