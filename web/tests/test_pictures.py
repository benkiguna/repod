"""An artist's picture shows, whatever its file was called."""
import shutil
import tempfile
from pathlib import Path
from urllib.parse import quote

from harness import Case, DEV

import meta

ICON = DEV.parent / "assets/apple-touch-icon.png"
OTHER = DEV.parent / "assets/favicon.png"


class Pictures(Case):
    def pick(self, artist, source, name):
        file = Path(tempfile.mkdtemp()) / name
        shutil.copy(source, file)
        status, state = self.post("/api/artist-picture", {"name": artist, "path": str(file)})
        self.assertEqual(status, 200, state)
        return state["artistInfo"][artist]["picture"]

    def test_a_name_with_a_space_and_an_accent_shows(self):
        self.state()
        picture = self.pick("Björk", ICON, "Mÿ Photo.png")
        self.assertEqual(self.request(f"/artist-art/{quote(picture)}")[0], 200)

    def test_two_artists_with_the_same_file_name_keep_their_own(self):
        self.state()
        one, two = self.pick("Björk", ICON, "cover.png"), self.pick("Nena", OTHER, "cover.png")
        self.assertNotEqual(one, two)
        self.assertEqual((meta.PICTURES / one).exists(), True)

    def test_a_picture_kept_under_its_old_name_still_shows(self):
        self.state()
        meta.PICTURES.mkdir(parents=True, exist_ok=True)
        shutil.copy(ICON, meta.PICTURES / "Old Name.png")
        self.assertEqual(self.request("/artist-art/Old%20Name.png")[0], 200)

    def test_a_path_cannot_leave_the_folder(self):
        self.state()
        for path in ("/artist-art/..%2f..%2fdecisions.json", "/art/..%2f..%2fetc%2fpasswd", "/chosen/%2e%2e%2fmanifest.json"):
            with self.subTest(path):
                self.assertEqual(self.request(path)[0], 404)
