"""Following a job costs almost nothing, and the page can tell when the library is the one it has."""
import json

from harness import Case


class Polling(Case):
    def test_a_poll_carries_the_job_and_not_the_library(self):
        self.state()
        self.fault("slow")
        self.post("/api/regroup-preview")
        answer = self.get("/api/job")
        self.assertTrue(answer["job"]["running"])
        self.assertNotIn("library", answer)
        self.assertLess(len(json.dumps(answer)), 2000)
        self.wait()

    def test_the_stamp_moves_only_when_the_library_was_read_again(self):
        first = self.state()
        self.job("/api/regroup-preview")
        self.assertEqual(self.state()["stamp"], first["stamp"])
        song = first["library"]["songs"][0]
        self.job("/api/songs-retag", {"songs": [{"id": song["id"], "set": {"title": "Changed"}}], "confirm": True})
        self.assertGreater(self.state()["stamp"], first["stamp"])
