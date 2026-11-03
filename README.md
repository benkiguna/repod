<h1><picture>
  <source media="(prefers-color-scheme: dark)" srcset="web/assets/wordmark-dark.svg">
  <img src="web/assets/wordmark-light.svg" alt="RePod" width="186">
</picture></h1>

**Manage the music on an iPod from a web page on your Mac, without iTunes or Apple Music.**

It reads and writes the iPod's own library directly on the device, so there is nothing to keep in sync: what you see is what is on the iPod.

- Browse by album, artist or song, with Cover Flow and a working click wheel
- Play songs from the iPod in the page
- Group albums by hand on a board: drag songs, merge albums, number tracks, with undo, and nothing written until you review it
- Find duplicate songs, and decide what to keep with a reason for each
- Fix tags from MusicBrainz, and find covers online
- Add songs, optionally looking them up first
- Check the iPod's health, and convert songs it can't play
- Back up the library automatically before every write, and rebuild a whole iPod after a reset

## Requirements

- macOS 14 or later, on Apple silicon or Intel
- To build from the source: Swift 6, and Python 3.9 or later
- An iPod classic, or an iPod nano (2nd to 4th generation). Other iPods open read-only: the mark at the bottom of the rail says **Read mode**, and **Health** says why.

## Get it

Download the disk image from [Releases](https://github.com/benkiguna/repod/releases), open it and drag **RePod** to Applications. Double-click it: it opens the page in your browser and stays in the Dock until you quit it. Quitting while a write is running lets the write finish first.

- The first time, macOS asks whether it may read a removable volume. Say yes: that is the iPod.
- The app isn't notarized by Apple, so macOS blocks it the first time. Open **System Settings › Privacy & Security** and choose **Open Anyway**.
- Quit Music first: it can overwrite the iPod's library.
- If your iPod isn't on the confirmed list, **Health** offers to allow writing to it. It lifts that list only, never the signature, the identity checks or the backups. Use it at your own risk: a model that isn't confirmed may not read what is written.

## Run it from the source

```bash
swift build --product ipodctl
python3 web/web.py
```

Open <http://127.0.0.1:8765>. `--developer` does what the Health setting does.

`web/package.sh` builds the app and the disk image. It downloads a Python runtime, pinned by checksum, so the app needs nothing installed; `PYTHON_RUNTIME=system` leaves that out, and the app then uses the Mac's own.

To try it with no iPod at all:

```bash
python3 web/dev/serve.py --scenario messy
```

That runs on a pretend iPod at <http://127.0.0.1:8799>, and touches nothing real.

## Your music

- **Backups** go to `~/Library/Application Support/RePod/Backups`. The newest 10 stay, with any you star. A backup holds the library and the artwork, not the song files.
- **Copy songs to this Mac** saves the song files with a backup that matches them. Together they can rebuild the iPod with **Restore everything**, which has been rehearsed on disk images but has not yet been run on a real iPod after a reset. Keep your own copy of anything you can't replace.
- Everything else the page keeps is in `~/.repod`.
- The page talks to MusicBrainz, the Cover Art Archive, iTunes Search, Deezer and Wikimedia only when you ask it to look something up. Set `REPOD_CONTACT` to your email or a URL, as MusicBrainz asks of its clients.

## How it is built

| | |
|---|---|
| `web` | The page and its Python server. `DESIGN.md` is the rules of the interface |
| `Sources/AppLauncher` | The double-clickable app: it starts the server and stays in the Dock |
| `Sources/ipodctl` | The command-line tool the server runs. Without `--apply` a command only previews |
| `Sources/RePodCore` | What a write is: the change queue, the transaction, imports |
| `Sources/ITDBKit` | The iPod's database and artwork formats, the signature, backups |
| `docs/byte-map.md` | Notes on the file format |

`Tools/check.sh` runs all the tests. See [CONTRIBUTING.md](CONTRIBUTING.md).

The database format and the `hash58` checksum were documented by the open-source community. The checksum is an independent Swift implementation written from the algorithm description in libgpod; see [THIRD-PARTY-NOTICES](THIRD-PARTY-NOTICES).

## Licence

Copyright © 2026 Jakub Warzecha. Modified in November 2026 by the RePod authors.

This is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, version 3. It comes with no warranty. See [LICENSE](LICENSE).

iPod, iPod classic, iTunes, Apple Music, Mac and macOS are trademarks of Apple Inc. This product is not supported, written or published by Apple.
