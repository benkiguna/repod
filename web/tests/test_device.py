"""The server knows whether the iPod is there, and long lookups can be stopped."""
import time

from harness import Case, IPOD, MOUNT, enrich, web


class Presence(Case):
    def unplug(self):
        (MOUNT / "iPod_Control").rename(MOUNT / "gone")
        self.addCleanup(lambda: (MOUNT / "gone").exists() and (MOUNT / "gone").rename(MOUNT / "iPod_Control"))

    def test_the_watch_sees_it_go(self):
        self.state()
        self.assertTrue(self.get("/api/device")["connected"])
        self.unplug()
        self.assertFalse(self.get("/api/device")["connected"])

    def test_nothing_that_needs_the_ipod_starts_without_it(self):
        song = self.state()["library"]["songs"][0]
        self.unplug()
        for path, body in (("/api/songs-remove", {"ids": [song["id"]], "confirm": True}), ("/api/backup-create", {}), ("/api/regroup-preview", {}), ("/api/eject", {})):
            with self.subTest(path):
                status, answer = self.post(path, body)
                self.assertEqual(status, 503)
                self.assertIn("isn't connected", answer["error"])

    def test_a_lookup_does_not_need_it(self):
        self.state()
        self.unplug()
        self.assertEqual(self.post("/api/find-covers")[0], 200)

    def test_the_library_stays_after_an_unplug(self):
        self.state()
        self.unplug()
        state = self.state()
        self.assertFalse(state["connected"])
        self.assertIsNotNone(state["library"])

    def test_eject_uses_the_chosen_ipod(self):
        self.state()
        self.addCleanup(lambda: (MOUNT / "ejected-iPod_Control").exists() and (MOUNT / "ejected-iPod_Control").rename(MOUNT / "iPod_Control"))
        status, answer = self.post("/api/eject")
        self.assertEqual(status, 200)
        self.assertTrue(answer["ejected"])
        self.assertFalse(answer["connected"])

    def test_a_refused_eject_says_why(self):
        self.state()
        self.fault("eject-busy")
        status, answer = self.post("/api/eject")
        self.assertEqual(status, 400)
        self.assertIn("not ejected", answer["error"])
        self.assertFalse(self.app.ejected)

    def test_the_copy_uses_the_chosen_ipod(self):
        import tempfile
        self.state()
        job = self.job("/api/copy-songs", {"dest": tempfile.mkdtemp(), "confirm": True})
        self.assertTrue(job["ok"], job["log"][-2:])


class Stopping(Case):
    def test_a_stopped_lookup_keeps_what_it_knew(self):
        self.state()
        self.assertTrue(self.job("/api/find-covers")["ok"])
        before = {c["key"]: c["status"] for c in self.state()["covers"]}
        self.app.job.cancel.clear()
        real = enrich.lookup

        def stop_after_first(album, decisions):
            self.app.job.cancel.set()
            return real(album, decisions)
        enrich.lookup = stop_after_first
        self.addCleanup(lambda: setattr(enrich, "lookup", real))
        job = self.job("/api/find-covers")
        self.assertEqual(job["outcome"]["result"], "stopped")
        self.assertEqual({c["key"]: c["status"] for c in self.state()["covers"]}, before)

    def test_a_scan_can_be_stopped_but_a_write_cannot(self):
        self.state()
        self.fault("slow")
        song = self.state()["library"]["songs"][0]
        self.post("/api/songs-retag", {"songs": [{"id": song["id"], "set": {"title": "X"}}], "confirm": True})
        self.assertFalse(self.get("/api/job")["job"]["stoppable"])
        self.assertEqual(self.post("/api/cancel")[1], {"cancelled": False})
        self.assertTrue(self.wait()["ok"])
        self.assertEqual(self.post("/api/meta-scan")[0], 200)
        self.assertTrue(self.get("/api/job")["job"]["stoppable"])


class Polish(Case):
    def test_a_size_with_a_non_breaking_space_is_still_a_size(self):
        self.state()
        real = web.subprocess.run

        def nbsp(*args, **kw):
            run = real(*args, **kw)
            run.stdout = run.stdout.replace(" MB", "\u00a0MB")
            return run
        self.job("/api/backup-create")
        web.subprocess.run = nbsp
        self.addCleanup(lambda: setattr(web.subprocess, "run", real))
        self.assertEqual(len(self.get("/api/backups")["backups"]), 1)

    def test_a_refused_file_dialog_says_so_and_cancel_does_not(self):
        self.state()
        self.assertEqual(self.post("/api/choose", {"kind": "file"}), (200, {"path": None}))
        self.fault("no-dialog")
        status, answer = self.post("/api/choose", {"kind": "file"})
        self.assertEqual(status, 503)
        self.assertIn("Automation", answer["error"])

    def test_a_failed_copy_leaves_no_half_file(self):
        import os
        import tempfile
        from pathlib import Path
        from unittest import mock
        self.state()
        target = Path(tempfile.mkdtemp())
        with mock.patch.object(os, "fsync", side_effect=OSError("no space left on device")):
            job = self.job("/api/copy-songs", {"dest": str(target), "confirm": True})
        self.assertEqual(job["outcome"]["result"], "failed")
        self.assertEqual([f for f in target.rglob("*") if f.is_file()], [])
