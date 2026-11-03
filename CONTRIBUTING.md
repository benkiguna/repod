# Contributing

`Tools/check.sh` runs everything: the Swift tests, then the web page's syntax checks and tests. It uses no iPod and no network.

## The rule that matters most

Work on the pretend iPod. `web/dev/serve.py` runs the page on `dev/fake_ipodctl.py`, which follows the commands and output of `ipodctl`, so a change to `ipodctl` belongs in the fake too. Never test a write on an iPod you care about.

`web/dev/restore_drill.py` rehearses a full restore with the real `ipodctl` on a hidden disk image, with a separate home so your backups are out of reach.

## Keeping it safe

- Everything that writes previews first and saves a backup first.
- Nothing that can fail halfway leaves a half-written file.
- `ConfirmedModels.swift` lists the iPods known to be safe to write to. Adding a model needs a real device and a real test: a write, the iPod playing the result, and a restore.
- `--developer` lifts that list for testing. It never lifts the signature, identity conflicts or backups.

## Privacy

Never put a GUID, serial number, iPod name, song titles, file names or a real `iTunesDB` in an issue, a test or a commit.

## Style

- The page follows `web/DESIGN.md`. If a screen needs something it doesn't cover, add it there first.
- No dependencies: Python standard library, Swift Foundation and what ships with macOS.
- Comments, names and messages in plain English.

## Licence

This project is licensed under the GNU General Public License, version 3. Your contribution is licensed the same way.
