"""What is proposed for albums and copies. These are the shapes a real library has."""
import unittest

from harness import Case, tidy


def song(number, title="Song", artist="Band", album="Album", owner=None, **more):
    owner = artist if owner is None else owner
    return {"id": number, "title": title, "artist": artist, "album": album, "albumArtist": owner, "albumartist": owner, "track": 0, "disc": 0, "seconds": 200,
            "year": 0, "format": "MP3", "kbps": 192, "size": 1000 + number, "plays": 0, "compilation": False, **more}


def kinds(songs, splits=(), doubles=()):
    return {h["kind"]: h for h in tidy.hints(songs, list(splits), list(doubles))}


class Names(unittest.TestCase):
    def test_accents_case_and_order_do_not_make_another_name(self):
        self.assertEqual(tidy.compact("Renée"), tidy.compact("RENEE"))
        self.assertEqual(tidy.credit("Chitra, Ilaiyaraaja"), tidy.credit("Ilaiyaraaja & Chitra"))

    def test_what_a_download_site_adds_is_not_part_of_the_title(self):
        self.assertEqual(tidy.core_title("Moon (Official Audio)"), "moon")
        self.assertEqual(tidy.core_title("Moon [HD]"), "moon")

    def test_a_version_is_part_of_the_title(self):
        self.assertNotEqual(tidy.core_title("Moon"), tidy.core_title("Moon (Slowed & Reverb)"))
        self.assertNotEqual(tidy.core_title("New Rules"), tidy.core_title("New Rules (Live)"))


class Hints(unittest.TestCase):
    def test_an_album_named_after_a_folder_loses_the_name(self):
        for album in ("transfer", "ipod-music", "Downloads", "https://djsoundtop.com", "SenSongsMp3.Co"):
            with self.subTest(album):
                self.assertEqual(kinds([song(1, album=album)])["folder"]["fix"], [{"id": 1, "set": {"album": ""}}])

    def test_a_real_album_is_not_a_folder(self):
        self.assertNotIn("folder", kinds([song(1, album="Music for Airports"), song(2, album="Homework")]))

    def test_two_names_of_one_album_become_one(self):
        fix = kinds([song(1, album="Harbor (Original Motion Picture Soundtrack)"), song(2, album="Harbor"), song(3, album="Harbor")])["edition"]["fix"]
        self.assertEqual(fix, [{"id": 1, "set": {"album": "Harbor"}}])

    def test_two_different_scores_stay_two_albums(self):
        self.assertNotIn("edition", kinds([song(1, album="96 (Original Motion Picture Soundtrack)"), song(2, album="96 (Original Background Score)")]))

    def test_discs_keep_their_number(self):
        fix = kinds([song(1, album="Abbey Road CD1"), song(2, album="Abbey Road CD2")])["edition"]["fix"]
        self.assertEqual({x["id"]: x["set"] for x in fix}, {1: {"album": "Abbey Road", "disc": 1}, 2: {"album": "Abbey Road", "disc": 2}})

    def test_a_spelling_follows_the_most_songs(self):
        hint = kinds([song(1, artist="ZHU"), song(2, artist="ZHU"), song(3, artist="Zhu")])["spelling"]
        self.assertEqual(hint["title"], "ZHU")
        self.assertEqual(hint["fix"], [{"id": 3, "set": {"artist": "ZHU", "albumartist": "ZHU"}}])

    def test_the_artist_comes_out_of_a_loose_title(self):
        songs = [song(1, "Marlow - Still Awake (Official Audio)", "", ""), song(2, "Marlow  Other One", "", ""), song(3, "Nuits sans sommeil", "", "")]
        hint = kinds(songs)["loose"]
        self.assertEqual(hint["title"], "Marlow")
        self.assertEqual(hint["fix"], [{"id": 1, "set": {"artist": "Marlow", "title": "Still Awake"}}, {"id": 2, "set": {"artist": "Marlow", "title": "Other One"}}])

    def test_a_song_with_an_artist_keeps_its_title(self):
        self.assertNotIn("loose", kinds([song(1, "Half - Full", "Band", "")]))

    def test_a_repeated_number_says_so_and_changes_nothing_by_itself(self):
        hint = kinds([song(1, track=1), song(2, track=1)])["numbers"]
        self.assertEqual((hint["ids"], hint["fix"]), ([1, 2], []))

    def test_two_discs_can_both_have_a_track_one(self):
        self.assertNotIn("numbers", kinds([song(1, track=1, disc=1), song(2, track=1, disc=2)]))

    def test_what_only_the_ipod_can_settle_is_marked(self):
        hint = kinds([song(1)], doubles=[{"album": "Album", "artist": "Label", "ids": [1]}])["double"]
        self.assertEqual((hint["level"], hint["fix"]), ("check", [{"id": 1, "set": {"compilation": True}}]))


class Copies(unittest.TestCase):
    def find(self, *songs):
        return tidy.duplicates(list(songs))

    def test_the_same_song_twice_on_an_album(self):
        found = self.find(song(1, size=500), song(2, size=500))["sets"]
        self.assertEqual([(x["kind"], x["keep"]) for x in found], [("same", 1)])
        self.assertIn("same size", found[0]["why"])

    def test_lengths_must_agree(self):
        self.assertEqual(self.find(song(1, seconds=200), song(2, seconds=300))["sets"], [])

    def test_a_close_length_is_only_worth_a_look(self):
        self.assertEqual(self.find(song(1, seconds=223), song(2, seconds=252))["sets"][0]["kind"], "maybe")

    def test_a_remix_is_not_a_copy(self):
        self.assertEqual(self.find(song(1, "Moon"), song(2, "Moon (Remix)"))["sets"], [])

    def test_two_artists_with_one_song_title_are_not_copies(self):
        self.assertEqual(self.find(song(1, "The Fall", "A"), song(2, "The Fall", "B"))["sets"], [])

    def test_quality_decides_first(self):
        best = self.find(song(1, album="One", kbps=192, plays=9), song(2, album="Two", kbps=886, format="ALAC"))["sets"][0]
        self.assertEqual(best["keep"], 2)
        self.assertIn("Apple Lossless", best["why"])

    def test_the_album_that_would_lose_something_keeps_it(self):
        songs = [song(1, "Hit", album="Best Of", owner="Band", kbps=900), song(2, "Hit", album="Record", owner="Band", kbps=880), song(3, "Other", album="Record", owner="Band")]
        best = self.find(*songs)["sets"][0]
        self.assertEqual(best["keep"], 2)
        self.assertIn("Record", best["why"])

    def test_a_best_of_is_decided_as_an_album(self):
        songs = [song(n, f"Hit {n}", album="Best Of") for n in (1, 2, 3)] + [song(n + 10, f"Hit {n}", album=f"Record {n}") for n in (1, 2, 3)]
        self.assertEqual(self.find(*songs)["repeats"], [{"album": "Best Of", "artist": "Band", "ids": [1, 2, 3], "songs": 3}])

    def test_a_file_name_for_a_title_is_noticed(self):
        found = self.find(song(1, "North-and-South", "", "", seconds=365), song(2, "YTDown.com_YouTube_For-North-and-South_360p", "", "", seconds=365))["sets"]
        self.assertEqual([x["kind"] for x in found], ["maybe"])


class OverHttp(Case):
    def test_the_library_says_what_to_look_at(self):
        library = self.state()["library"]
        kinds = {h["kind"] for h in library["hints"]}
        self.assertLessEqual({"folder", "split", "edition", "spelling", "loose", "double"}, kinds)
        self.assertLessEqual({"same", "albums"}, {x["kind"] for x in library["duplicates"]["sets"]})
        self.assertEqual(library["duplicates"]["repeats"][0]["album"], "The Best Of")

    def test_every_fix_is_something_retag_takes(self):
        library = self.state()["library"]
        fix = [x for h in library["hints"] for x in h["fix"]]
        job = self.job("/api/songs-retag", {"songs": list({x["id"]: x for x in fix}.values())})
        self.assertTrue(job["ok"], job["log"][-3:])

    def test_keeping_both_is_remembered(self):
        self.state()
        self.assertEqual(self.post("/api/keep-both", {"key": "1-2"})[1], {"bothKept": ["1-2"]})
        self.assertEqual(self.state()["bothKept"], ["1-2"])
        self.assertEqual(self.post("/api/keep-both", {"key": "1-2", "keep": False})[1], {"bothKept": []})

    def test_nonsense_is_not_stored(self):
        self.assertEqual(self.post("/api/keep-both", {"key": "../x"})[0], 400)

    def test_removing_the_copies_empties_the_list(self):
        sets = self.state()["library"]["duplicates"]["sets"]
        extras = [i for x in sets for i in x["ids"] if i != x["keep"]]
        self.assertTrue(self.job("/api/songs-remove", {"ids": extras, "confirm": True})["ok"])
        self.assertEqual(self.state()["library"]["duplicates"]["sets"], [])
