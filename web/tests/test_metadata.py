"""Accepting proposals, one or all at once."""
import json

from harness import Case, enrich

import meta


class Accepting(Case):
    def proposals(self):
        proposal = {"kind": "album", "album": "A", "albumArtist": "B", "year": 1999, "current": "A", "score": .9, "level": "high", "unmatched": []}
        song = self.state()["library"]["songs"][0]
        enrich.HOME.mkdir(parents=True, exist_ok=True)
        rows = [{"id": song["id"], "title": "x", "score": 1, "set": {"year": 1999}, "before": {"year": 0}}]
        meta.REVIEW.write_text(json.dumps([{**proposal, "key": "album:one", "songs": rows}, {**proposal, "key": "album:two", "songs": []}]))

    def test_many_at_once(self):
        self.proposals()
        status, answer = self.post("/api/meta-accept", {"accept": {"album:one": ["year"], "album:two": ["tracks"]}})
        self.assertEqual(status, 200)
        self.assertEqual(answer["decisions"]["album:one"], {"status": "accepted", "fields": ["year"]})
        self.assertEqual(self.get("/api/meta")["decisions"]["album:two"]["status"], "accepted")

    def test_unknown_proposals_and_fields_are_refused(self):
        self.proposals()
        self.assertEqual(self.post("/api/meta-accept", {"accept": {"album:nope": ["year"]}})[0], 400)
        self.assertEqual(self.post("/api/meta-accept", {"accept": {"album:one": ["nonsense"]}})[0], 400)
        self.assertEqual(self.post("/api/meta-accept", {"accept": "everything"})[0], 400)
