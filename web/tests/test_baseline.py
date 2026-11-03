"""What already worked before the polish, so that it keeps working."""
from harness import Case, enrich


class Reading(Case):
    def test_the_first_visit_reads_the_library(self):
        state = self.state()
        self.assertEqual(state["library"]["device"], "Fake iPod classic")
        self.assertEqual(len(state["library"]["songs"]), len(self.tracks()))
        self.assertIsNone(state["loadError"])
        self.assertTrue(state["library"]["splits"])
        self.assertTrue(state["library"]["doubles"])
        self.assertTrue(state["library"]["outOfPlace"])

    def test_unknown_addresses_are_not_found(self):
        self.assertEqual(self.request("/nothing")[0], 404)
        self.assertEqual(self.post("/api/nothing")[0], 404)

    def test_a_song_streams_with_ranges(self):
        song = self.state()["library"]["songs"][0]
        import urllib.request
        from harness import real_urlopen
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}/audio/{song['id']}", headers={"Range": "bytes=0-99"})
        with real_urlopen(request) as response:
            self.assertEqual(response.status, 206)
            self.assertEqual(len(response.read()), 100)


class Guards(Case):
    def test_another_site_cannot_reach_the_server(self):
        self.assertEqual(self.request("/api/state", headers={"Host": "evil.example"})[0], 403)
        self.assertEqual(self.post("/api/load", headers={"Origin": "http://evil.example"})[0], 403)

    def test_every_write_needs_confirm(self):
        self.state()
        for path, body in (("/api/write", {}), ("/api/add", {"path": "/tmp"}), ("/api/clear-cache", {}), ("/api/backup-restore", {"id": "x"}),
                           ("/api/regroup", {}), ("/api/meta-apply", {}), ("/api/copy-songs", {"dest": "/tmp"})):
            with self.subTest(path):
                self.assertEqual(self.post(path, body)[0], 400)

    def test_an_edit_without_confirm_is_only_a_preview(self):
        song = self.state()["library"]["songs"][0]
        job = self.job("/api/songs-retag", {"songs": [{"id": song["id"], "set": {"title": "Changed"}}]})
        self.assertEqual(job["kind"], "edit-preview")
        self.assertIn("That was a preview. Nothing was changed.", [line.strip() for line in job["log"]])
        self.assertNotIn("Changed", [t["title"] for t in self.tracks()])

    def test_only_one_job_at_a_time(self):
        self.state()
        self.fault("slow")
        self.assertEqual(self.post("/api/regroup-preview")[0], 200)
        self.assertEqual(self.post("/api/backup-create")[0], 409)


class Writing(Case):
    def test_an_edit_reaches_the_ipod_and_the_page(self):
        song = self.state()["library"]["songs"][0]
        job = self.job("/api/songs-retag", {"songs": [{"id": song["id"], "set": {"title": "Changed"}}], "confirm": True})
        self.assertTrue(job["ok"])
        self.assertIn("Changed", [s["title"] for s in self.state()["library"]["songs"]])

    def test_a_delete_reaches_the_ipod_and_the_page(self):
        songs = self.state()["library"]["songs"]
        self.assertTrue(self.job("/api/songs-remove", {"ids": [songs[0]["id"]], "confirm": True})["ok"])
        self.assertEqual(len(self.state()["library"]["songs"]), len(songs) - 1)

    def test_covers_are_found_and_written(self):
        self.state()
        self.assertTrue(self.job("/api/find-covers")["ok"])
        for cover in self.state()["covers"]:
            if cover["status"] == "review":
                enrich.apply_decision(cover["key"], "accept")
        before = self.state()["library"]["missingCount"]
        self.assertTrue(self.job("/api/write", {"confirm": True})["ok"])
        self.assertLess(self.state()["library"]["missingCount"], before)

    def test_a_backup_is_listed_and_restored(self):
        song = self.state()["library"]["songs"][0]
        self.assertTrue(self.job("/api/backup-create")["ok"])
        self.job("/api/songs-retag", {"songs": [{"id": song["id"], "set": {"title": "Changed"}}], "confirm": True})
        backups = self.get("/api/backups")["backups"]
        self.assertEqual(len(backups), 2)
        self.assertTrue(self.job("/api/backup-verify", {"id": backups[0]["id"]})["ok"])
        self.assertTrue(self.job("/api/backup-restore", {"id": backups[-1]["id"], "confirm": True})["ok"])
        self.assertEqual(self.state()["library"]["songs"][0]["title"], song["title"])
