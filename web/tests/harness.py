"""The real server on the fake iPod, for tests: real HTTP, real jobs, real subprocesses of `fake_ipodctl.py`.

Importing this sets up the environment, so it has to come before `web` and `enrich`.
"""
import atexit
import json
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

DEV = Path(__file__).resolve().parents[1] / "dev"
sys.path[:0] = [str(DEV), str(DEV.parent)]
sys.dont_write_bytecode = True

import serve  # noqa: E402

STATE = Path(tempfile.mkdtemp(prefix="enrich-tests-"))
atexit.register(shutil.rmtree, STATE, ignore_errors=True)
serve.prepare(STATE, 40, "typical", keep=False)
urllib.request.urlopen, real_urlopen = serve.fake_urlopen, urllib.request.urlopen

import enrich  # noqa: E402
import tidy  # noqa: E402
import web  # noqa: E402

enrich.MB_DELAY = 0  # the polite wait between MusicBrainz requests; these are canned

IPOD = STATE / "ipod"
MOUNT = IPOD / "mount"


class Case(unittest.TestCase):
    """One fresh pretend iPod and one server for each test."""
    songs = 40
    scenario = "messy"
    developer = False

    def setUp(self):
        serve.subprocess.run([sys.executable, str(DEV / "fake_ipodctl.py"), "init", "--songs", str(self.songs), "--scenario", self.scenario], check=True, capture_output=True)
        shutil.rmtree(enrich.HOME, ignore_errors=True)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]
        self.app = web.App(SimpleNamespace(volume=str(MOUNT), developer=self.developer))
        self.server = ThreadingHTTPServer(("127.0.0.1", self.port), web.make_handler(self.app, self.port))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.wait)

    def request(self, path, body=None, headers=None):
        data = None if body is None else json.dumps(body).encode()
        sent = {"Content-Type": "application/json", **(headers or {})} if body is not None else (headers or {})
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data, sent)
        try:
            with real_urlopen(request, timeout=30) as response:
                raw, status = response.read(), response.status
        except urllib.error.HTTPError as error:
            raw, status = error.read(), error.code
        try:
            return status, json.loads(raw)
        except ValueError:
            return status, raw

    def get(self, path, **kw):
        return self.request(path, **kw)[1]

    def post(self, path, body=None, **kw):
        return self.request(path, {} if body is None else body, **kw)

    def state(self):
        return self.get("/api/state")

    def wait(self, seconds=30):
        end = time.monotonic() + seconds
        while self.app.job.running and time.monotonic() < end:
            time.sleep(.01)
        self.assertFalse(self.app.job.running, "the job did not finish")
        return self.app.job.snapshot()

    def job(self, path, body=None):
        status, answer = self.post(path, body)
        self.assertEqual((status, answer), (200, {"started": True}))
        return self.wait()

    def fault(self, *words):
        (IPOD / "fault").write_text(" ".join(words))
        self.addCleanup(lambda: (IPOD / "fault").unlink(missing_ok=True))

    def tracks(self):
        return json.loads((IPOD / "library.json").read_text())["tracks"]
