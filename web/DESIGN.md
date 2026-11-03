# UI standards

How the iPod library page looks and behaves. Every new screen follows this; if a screen needs
something this file doesn't cover, add it here first. The source of truth for values is
`style.css` (the `:root` tokens) - never hard-code a colour, size or radius in a screen.

The reference is Apple's Human Interface Guidelines for macOS. It is a Mac app that happens to
run in a browser, so it should feel like one.

## Principles

1. **Clarity.** Text is legible at every size, icons are plain, and each screen has one obvious
   thing to do. If a screen needs a paragraph of explanation, the screen is wrong.
2. **Deference.** The content is the music: album art, names, numbers. Chrome (sidebar, toolbar,
   controls) stays quiet - neutral colours, thin lines, no heavy boxes. Colour is for meaning.
3. **Depth.** Layers tell you where you are: floating glass for navigation and controls over the
   content, sheets above the window, and motion that explains where things came from.
4. **Direct and reversible.** Show the effect before anything is written ("Preview"), confirm
   before anything leaves the Mac or changes the iPod, and say what was done afterwards.

## Layout

- **Window = a floating panel + content.** Navigation floats on both sides: an iPod classic to scale on the left for the library, centred in the
  height of the window, and a slim Manage rail on the right for the tools, with the device's own controls under a line. It floats, clear of the window's edges, and things pass beneath it. It stays modest (`--side`: 240 to 320 px, at most 18% of the width), and everything in it scales together (`--px`). Every part of the iPod is a share of its real 61.8 x 103.5 mm front: the 4:3 screen, the 35 mm wheel, the 13.6 mm centre
  button. Change those shares and it stops being an iPod; a short window scrolls instead. In dark mode the body is black tinted glass, like the black iPod classic. The screen is a lit white pane in either theme, with its own dark text: the library (Albums, Artists, Songs, with their
  counts, the current one always solid blue) and a quiet storage bar with what is free; there is no battery, because nothing can read it. The rail holds the Manage screens, each a round button like the wheel's centre, its short name and count (the current one is solid blue), then
  Reload, Eject, appearance (one button that steps through them) and sound, and a small **Write mode** or **Read mode** mark, which says what `ipodctl info` says about this iPod (`--developer` only shows in its tooltip). The iPod's screen is for the library alone. The rail stays about as tall as the iPod. It never scrolls the content.
- **The iPod and the Manage rail go wherever you put them.** Drag the iPod by its body, or the rail by anything on it; a flick throws it. Nothing stops a panel covering the content: that is the user's call. Three kinds of magnet act on them (`MAGNET`):
  the two panels pull together and lock side by side, moving as one until the rail is pulled hard, and the rail turns slim, icons only, while it is locked; the window's edges give a little and push back; and the two **bays** of the tidy layout, shown only during a drag, pull a panel in and park it. Parking on the other side sends the other panel across. A pair docks whole: dragged to a bay, or locked to a panel already parked, it stands in the iPod's column with the slim rail on the inside, inside the 64px margin, so it covers nothing; on the right the A-Z strip moves to the far side of the list to make room. Whenever anything is loose, a button on the rail puts everything back, as does a double-click on the iPod's body. Option with left or right parks the iPod from the wheel. Places are remembered.
- **The click wheel works, with an iPod's two levels.** In the menu, turning it (drag round it, or scroll over it, or the arrow keys when it has focus)
  moves the highlight and the centre goes in. Inside a screen the wheel drives the view: covers in Cover Flow, rows in a list. The
  centre then acts on the centre cover. Menu turns a flipped cover back, or returns to the menu. Play, back and forward are the player's: back goes to the start of a song first, as on an iPod.
- **What stays put:** the sidebar, the toolbar (title, count, the view switch, search and the view's
  main actions), a table's header row, and the Covers filter row. Everything else scrolls.
- **Bars have no background of their own.** The toolbar and the Covers filter row are transparent;
  the scroll edge effect (see Materials) keeps text legible when content scrolls under them.
- **Content is centred on the window, not on what is left of it.** The right margin matches what the iPod takes on the left (`--inset`); a narrow window gives some of that up, down to the room the Manage rail needs. The A-Z rail sits just outside the content's right edge. The title starts on the content's left edge and the toolbar ends on its right edge.
- **Spacing is a 4 px grid.** Use 4, 8, 12, 16, 24, 32. Nothing else.
- **Places are remembered.** Each screen, and each view of it (list, grid, Cover Flow), keeps its scroll position
  or selected cover while the page is open, so switching away and back returns you to where you were. Each screen keeps its search too.
- Minimum window width is 760 px; below that the sidebar collapses to icons. The toolbar wraps and grows, and the content starts under it (`--bar` follows its real height).
- **In a narrow list the name is what stays.** Columns go by the list's own width, least needed first (a container query, because the iPod and the rail can be anywhere): year and length, then album and country, then artist.

## Materials: Liquid Glass

Follows Apple's "Adopting Liquid Glass". Glass is a distinct functional layer for **navigation and
controls**, floating above the content. The content is never glass.

- **Navigation layer (glass):** the iPod and the Manage rail, and the controls: buttons, segmented switches, the search
  field, the activity pill, sheets. Use it sparingly; it exists to put the content in focus.
- **Content layer (one neutral pane):** tables, cards, proposals and the toolbar's controls share one material (`--pane`): a neutral at low opacity, so it takes its colour from the window behind it and sits with the iPod, the artwork and the accent on any screen. No per-screen colours, no gradients, no glow: a flat pane and a hairline edge (`--pane-line`). Tiles and artwork stay plain. A table's header is near-opaque (`--pane-solid`), so rows passing under it never show through. The Manage rail floats over artwork, so it takes the iPod's body instead: smoked glass in dark, frosted in light.
- **Never glass on glass,** with one exception: the iPod's screen is white glass, because it stands for a lit display. Inside a panel everything else is a plain fill (`--fill`). Don't
  stack glass surfaces or crowd controls together.
- **Bars have no background of their own.** The toolbar and the sticky filter row are transparent.
  Content that scrolls under the toolbar fades out completely (a mask on the scroll area: Apple's
  *scroll edge effect*), so nothing can be read through it. Never tint or blur it into a band: a flat
  colour can't match the glow behind it and draws a box. A table's sticky header row is solid (`--bg-grouped`), never translucent: text
  must not show through it. Legibility and tidiness come before showing the glow.
- **Glass recipe** (one token set, `--glass*` in `style.css`): a translucent fill, `blur(22px)
  saturate(190%)` (the sidebar uses `--glass-panel` and 30 px), a bright 1 px top and left edge, a
  faint full outline, a soft glow along the bottom edge and a diagonal sheen from top-left. Sheets use
  a more opaque fill so text keeps its contrast.
- **Shapes float and are concentric.** The iPod and the rail are inset panels, 12 px from the
  window edge. Controls are capsules. Groups use 16 px. An inner radius is the outer radius minus
  the gap between them.
- **Something to blur.** The window has a soft ambient gradient and a very faint grain (so dark
  gradients dither instead of banding). Without something behind it, glass looks like flat grey.
- **Not true refraction.** Browsers cannot bend the content behind glass reliably (Safari has no
  support). We do the material, not the lens. Do not add SVG displacement filters.
- **Accessibility.** Reduce Transparency swaps glass for opaque fills; Reduce Motion removes the
  sliding. Colour in controls is a system colour with a light and a dark value.

## Theme

- Three choices: **Auto** (follows the Mac), **Light**, **Dark**. The choice is remembered in the
  browser and applied before the first paint, so there is no flash.
- The switch lives on the Manage rail. Colours are only ever read from tokens, so a screen
  needs no theme code of its own.

## Typography

- System font only: `-apple-system` (SF Pro on a Mac). No web fonts.
- Sizes: Large title 26/700 (one per view), Title 17/600, Body 14/400, Callout 13, Footnote 12,
  Caption 11. Weights 400, 500, 600, 700 - no 300 or 800.
- Numbers in columns use tabular figures. Letter-spacing -0.01em on titles.
- Sentence case everywhere ("Find covers online", not "Find Covers Online"). Buttons that open a
  sheet end with an ellipsis ("Add to iPod…"); buttons that act at once do not.

## Colour

- Semantic tokens only: `--label`, `--label-2`, `--label-3`, `--separator`, `--fill`,
  `--bg-window`, `--bg-content`, `--bg-grouped`.
- **One accent** (system blue). Green = done/safe, orange = needs a look, red = destructive or
  failed. These three never decorate; they only mean something.
- **Indicators use Apple's system colours, flat.** Seal, arcs, score bars and the low-res marker use
  `--ind-green`, `--ind-orange`, `--ind-red` (light #34c759 / #ff9500 / #ff3b30, dark #30d158 /
  #ff9f0a / #ff453a), solid, with no tint, gradient or glow, because they sit on artwork. Text uses
  the darker `--green`, `--orange`, `--red` so it keeps its contrast on white.
- Light and dark are both first-class; every token has both values. Never use pure black or white.
- Contrast: body text at least 4.5:1, secondary text at least 3:1.

## Components

- **Borrow from the iPod, not from a web kit.** No hero cards (a title, a paragraph and a row of buttons in a box): a Manage screen's actions sit in the toolbar, with one line under it saying what is safe. A chosen row is the solid blue bar of the iPod's screen, a tick is a small round mark that appears when the row is reached, nothing to show is a little lit screen with a title bar, and a count on the rail is a plain number. No gloss or coloured glow on buttons, nothing grows on hover, and pressed things go darker.
- **Buttons.** Three kinds. *Default* (grey fill) for ordinary actions; *Prominent* (flat accent fill)
  for the one main action of a view, at most one per toolbar or sheet; *Destructive* (red text on
  grey, red fill only inside a confirmation sheet). Height 30 px, radius 8 px, 14 px text.
  A button that is busy shows a spinner and its verb in progress ("Looking up…"), not just a grey state.
- **Segmented control** for switching a view of the same content (the cover filters).
- **Search field** is a rounded field with a magnifier, no button.
- **Lists** are inset grouped: one rounded container, hairline separators, 44 px rows, header row
  in Footnote secondary. Section headers use title case, never all caps. Row hover is a faint fill. No zebra stripes.
- **Album tiles** (Apple Music style): the art with an 8 px radius and a soft shadow, the title
  and the artist *below* it, not inside a bordered card. At rest a tile shows only the art, a
  seal (status and match in one), the name, the artist and the song count. Nothing else.
- **Quiet controls.** Per-item decisions (accept, skip, other image, re-check) are icon-only
  buttons that appear when the item is hovered or has keyboard focus, in one glass pill on the
  art, top-right. Each has a tooltip and an `aria-label`. At rest, an item shows no buttons.
- **One seal per item: status and match together.** A 40 px glass disc, bottom-left of the art,
  glowing softly in its colour. The glyph in the middle is the status; the thin arcs around it are
  the match. Never two separate indicators. The glyph set is drawn for the app, never a generic
  tick or cross: a four-point spark (ready to write), an eye (take a look), a dashed empty ring
  (not found), three dots (not looked up), a pause (skipped) and a bolt (error). The word is the
  tooltip, the card's header and the name of the filter: one word for each status (`LABELS`). Decision icons follow the same hand: a calligraphic swoosh to
  accept, a leaping arrow to skip, offset frames for another image.
- **Scores are drawn, not written.** The arcs are 1.8 px with a gradient and a soft glow: outer =
  title match, inner = artist match. Green is 88 % and up, amber 60-87 %, red below that. An item
  with no match gets the same seal as a bare ring with only the glyph. Never print a raw score such
  as "0.842" on a tile. The only other tile marker is the low-resolution warning, top-left.
- **Details are written on the cover, not in a popup.** Hovering or focusing the seal dims the art
  and shows, over it, the matched album name with its score drawn as a thin glowing bar beneath,
  then the matched artist name with its bar. Names are clamped to two lines and the overlay is
  clipped to the cover, so it never spills onto the grid. It is always light-on-dark because it
  sits on artwork. What is on the iPod stays in the tile's own caption below.
- **Views: List, Grid, Cover Flow.** Covers has Grid and Cover Flow; Albums and Artists have all
  three (the switch sits above the content, right-aligned, icon-only, and is remembered). Songs is a
  list only, because a song has no cover of its own. In the library the art is what is on the iPod
  now, read from its own artwork on every load; an album without one shows a quiet placeholder. An
  artist uses the cover of its first album that has one.
- **Cover Flow is full bleed.** It uses the whole window under the toolbar: no box, no frame. The selected cover faces front, the others fan out at an angle on a reflective
  floor, and they run out beneath the sidebar. Cover, reflection, name and A-Z strip are one composition (`--stage-top`, `--fs`), centred on the window, so nothing is pinned to its edge. It opens in the middle of the list, with covers on both sides. The Covers filters float over the top of the stage. On Covers only
  the selected cover carries the seal and decisions.
- **In Cover Flow a swipe is the covers'.** A sideways swipe never reaches the browser, wherever the pointer is, so it can't go Back or Forward (`holdSwipe`, and `overscroll-behavior` on `html.no-swipe`).
- **Cover Flow moves like a flywheel.** The position is continuous, in covers. The wheel or trackpad
  adds spin; friction slows it; below about half a cover a second it settles onto the nearest cover with
  a spring, like a detent. Top speed is capped (24 covers a second; a jump to a far cover is allowed
  1.4 times that), so a hard flick never becomes a blur. Every cover tilts and slides by its distance
  from the position, so a wave of covers passes through the middle, and the name changes as each one
  crosses it. Arrow keys, Home and End, a click on a side cover and the scrubber all use the same
  spring. The numbers live in one place (`FLOW` in `index.html`). Scrolling accelerates: each tick pushes harder the longer the wheel keeps going (up to 9 times, building slowly so a small swipe moves one or two covers), and
it falls back after a pause; top speed is 24 covers a second. With reduced motion, covers switch
  without sliding.
- **A-Z index.** Every list is sorted by name (Albums by album, Artists by artist, Songs by title, Covers
  by album); digits, symbols and other alphabets sort last under **#**. Lists and grids get a thin vertical
  rail on the right edge; Cover Flow gets a horizontal strip in place of a scrubber. The letter you are at is
  highlighted as you scroll or spin; press or drag along the strip to jump (instantly, with no glide, in both lists and Cover Flow). A letter with nothing under it is
  dimmed and goes to the next letter that has something. It is plain text, not glass.
- **The songs of an album.** Opening an album shows its song list, in a sheet or on the back of the centre cover in Cover Flow: a click
  on the cover turns it over to show the same list on its
  back; it turns back on Escape, on a click anywhere else, or when you move to another cover. The back is a pane of glass seen from behind: the
  cover shows through it mirrored and softly blurred, under a dark tint, with light text whatever the theme. It has no ticks: the four icon
  actions at the bottom (cover, edit, move, delete) work on the whole album, or on the songs picked by clicking them. Every change asks first, says a backup is
  made, then shows the progress pill, and the library reloads afterwards. Demo mode runs all of it without writing.
- **One line of extra text at most.** The only line allowed under a tile's artist is the one a
  decision needs: for a match that needs review, "Matched: <title>", in the secondary colour.
- **One main action, the rest in More.** The Covers toolbar shows the next step as the only prominent button:
  Find covers online until the albums are looked up, then Write N to iPod… (with Preview write beside it).
  Look up again, Refresh not found and Clear cache… live in a glass **More** (…) menu at its right end; Clear
  cache… is red. The menu closes on Escape or any click elsewhere.
  **Demo mode** (last item of the menu, off after a reload) runs every button's whole flow with pretend work:
  the sheets, the progress pill and the log all appear, and nothing is looked up, written or deleted. Decisions stay on the page. It shows on every screen, with Leave Demo in the toolbar, and leaving it brings back what is really there. A new action needs a pretend flow in `demoJob`.
- **Sheets** replace every `alert`, `confirm` and `prompt`. A sheet has a title, one or two lines
  of message, optional input, and buttons on the right: Cancel, then the action. The action is
  prominent, or red when it discards something. Escape cancels, Return confirms, except that a red sheet starts on Cancel: Return alone must never delete. A red sheet says what goes by name (`nameFew`), not only how many.
- **A form keeps what was typed.** What is wrong is said inside it, in red, on the field (`check` in `askForm`). A form never closes to report a mistake.
- **Activity.** Long work shows a pill at the bottom of the window (spinner, what it is doing,
  progress as "12 of 105"). A Details control opens the full log. When finished it says what happened, with the number ("Added 2 songs to the iPod"),
  and can be dismissed. A raw log is never the main thing on screen. What happened comes from what `ipodctl` printed (`outcome`), not only its exit code: done, partly done, nothing changed, or failed, and the reason is on the pill, in words (`friendly`), so nobody needs Details to learn that Music is open.
  Whatever only reads can be stopped and keeps what it found. A write is never stopped. While a job runs only the pill changes: the screen stays exactly as it is.
- **Changing a cover.** Any album with a name can get a new cover: a **Cover…** link on its row, the frames icon in the hover chips of
  a tile or a Cover Flow cover, and **Cover…** beside Edit in its song list. The sheet searches MusicBrainz, iTunes and Deezer when
  Search is pressed (the words can be changed), or takes a file or an address; the next sheet shows the image, Cancel there comes back to
  the results, and nothing is written until Write to iPod. An album without a cover gets its first the same way, and Covers uses the same
  sheet for a different image.
- **Grouping is a board.** New album is in the toolbar. Its shelf lists what to look at (To fix), every album, the one-song albums and every song, and the toolbar's search reaches all of them. Any album opens as a list that reads like the iPod's screen. An album is only as tall as its songs. Drag songs into an order or between albums, a whole album from the shelf onto another to join them, or anything to New album, which appears as you drag; shift and ⌘ select several, and the keyboard does the same (arrows, Space, ⌥ with arrows to reorder, Return for the menu). The name, album artist, year and Compilation are edited on the album. Two albums given one name and artist are one album, because that is what the iPod files.
  Nothing is written while you work: the board is a draft (`board.edits`), kept in the browser, undone with ⌘Z, shown as a blue dot on what changed, and everything on it is worked out from the songs as they would be. **Review** says what each album gets and loses, and writes it all as one `retag` with one backup. A song dropped at a place gets numbers that hold that order; a whole album joining another keeps its numbers, and a clash is said on the album.
  To fix (`tidy.hints`) proposes, and Fix stages the proposal into the draft: albums named after a folder or website, one album under two names or one name per disc, one album split by artist, artists spelled two ways, artists buried in the titles of loose songs. What only the iPod can settle (may show twice in Cover Flow) stays apart, under Check on the iPod first. A selection in Songs or Albums opens on the board with **Group…**; **Move to album…** on one song keeps its short form.
- **Duplicates** sits under Manage: copies on one album, copies on two albums, and those worth a listen (`tidy.duplicates`). A remix, a live take or another artist is never a copy. Every set suggests a copy and says why, in this order: clearly better quality, then the album that has songs found nowhere else, then plays. Click a copy to keep it, play any of them to compare, or Keep all, which is remembered on this Mac until Ask again. A best-of is decided as one album. **Remove…** is a red sheet that says what goes, what each album loses and that a backup holds no song files.
- **Metadata** and **Backups** sit under Manage. Metadata is a review list, never an editor: each
  MusicBrainz proposal is a card with the album, a match strength (Strong match, Check this, Weak
  match), chips for the kinds of change (track numbers, year, album artist, album name, titles,
  artists) that can each be unticked, and Accept / Skip. A new album's name, titles and artists start
  unticked, because renaming is the riskier half. Nothing is written until Apply, which confirms first.
  Backups lists every saved state with Check and Restore, says plainly that song files are not in a
  backup, and offers a stoppable copy of the songs to this Mac. The newest 10 stay, and any with a star; the rest go after a write that went through (`prune_backups`), and the screen says how many are waiting to go. The oldest is starred the first time, because it is the library from before any of this.
- **The whole iPod can come back.** Copy songs to this Mac copies every song, reads each back against a checksum, and saves a backup straight after, so the two describe one moment (`restore.json` in the folder lists every file). That backup is never cleaned up. It refuses first if the disk is too small, and carries on where it stopped.
  **Restore everything** is on Backups and on the empty screen, because a reset iPod has no library to open. Everything that can refuse does so before the iPod is touched: every file of the copy is checked, the backup must belong to this iPod and be sound, and there must be room. Nothing on the iPod is overwritten: a file there that differs stops it. Songs go first, each read back from the iPod, and the library last, so it never lists a song that isn't there. A reset iPod starts from the backup's own library, because `ipodctl` opens nothing without one. It can always simply be run again. `dev/restore_drill.py` rehearses all of it with the real `ipodctl` on a disk image.
- **Health** is on the rail beside Reload and Eject, because a seventh tile would make the rail taller than the iPod; a dot says something needs a look, and the Write mode mark opens it too. It runs on request, because reading every song takes a while, and it only reads. Songs the iPod can't play are listed by album with **Convert…**, which asks where the originals go; converting without a copy takes a second, red sheet. What needs a look is on show, and what's fine folds away. Albums split by hidden sort fields aren't here: they are on Grouping's To fix, found on every read (`fix-sort`).
- **Adding** can look the songs up first: with Look them up on MusicBrainz first ticked, the button says Look up…, and a stoppable job asks MusicBrainz about the songs while they are still in their folder (`meta.proposals`), writing nothing. Each album it knows is a card, a strong match already ticked. Add to iPod… then adds the songs and gives the ticked ones their names, as one job: `add` takes a file's own tags, so that is an `add` and a `retag`. Off, adding is as fast as it was.
- **The logo** is the iPod drawn in `assets/` (light, dark and an adaptive version). The
  empty state draws it inline in the page, so it follows the page's own light/dark choice; the tab icon
  is the click wheel, `favicon.svg`, which follows the Mac (`favicon.png` is its light version, for browsers
  that take no SVG). The app's icon is the click wheel too: `app-icon.png`, 1024 px with the margin and
  shadow of a Mac icon, drawn with `dev/render-icon.swift`, which also draws `apple-touch-icon.png`. A title on a cover or in a song list copies itself when
  double-clicked, and says so.
- **Suggestions** are one list everywhere (`comboShow`), never the browser's: a field with `data-list` offers what the library has as you type, best matches first with what you typed in bold. Arrow keys and Return choose, Escape closes it and nothing else. It works inside a sheet.
- **Empty states** say what to do next in one sentence and offer the button that does it, on a little iPod screen. A list that could not be read says that and offers Try again; it never claims there is nothing.
- **Icons** are simple line icons, 1.5 px stroke, drawn on a 16 px grid, in the label colour.

## The iPod comes and goes

The page looks for the iPod every 3 seconds (`/api/device`: a look at `/Volumes`, nothing on the iPod). When it goes, the library stays on screen, marked as the last read; everything that needs the iPod is disabled here (`locked()`) and refused by the server (`NEEDS_IPOD`). When it comes back it is read again by itself. A read, a job and an eject never overlap: they share one claim, in `Job`, across tabs.

## Performance

Jank that grows with scroll speed is paint cost, not script. Rules:

- **A table holds only the rows near the view,** with a gap above and below that keeps the scrollbar true. What needs every row works on `table`, never on the page: the selection, the A-Z rail, Select All. A cost that grows with the library belongs in data, not in the DOM.
- **Going back is instant.** A built library screen is kept and put back (`keptScreen`), so its pictures stay decoded; new data, a tick or a search drops them.
- **Pictures are the size they are shown.** An artist photo is stored at 480 px, not as downloaded, and images decode off the main thread.
- **One blur per pane.** Moving covers pass under the panel, and a blur inside a blur costs twice and shows nothing.
- **No blur on anything repeated.** Tiles, rows, seals and hover pills use plain translucent fills; blur
  is for the few big controls (sidebar, toolbar controls, sheets) and for one overlay at a time on hover.
- **No full-window overlays or blend modes.** The grain is baked into the backdrop image.
- **Never mask or filter a scrolling area.** Cover what scrolls under a bar with a small strip painted
  with the window's own backdrop (`--stage`), faded at its lower edge.
- **Cover Flow effects stay near the centre:** reflections and layer promotion for the nearest nine
  covers only, and covers far away are hidden.

## Motion

- 200 ms with `cubic-bezier(.2, .8, .2, 1)` for things moving in; 120 ms for things leaving.
- Sheets fade in and scale from 96 %; tiles and rows do not animate on load.
- Honour `prefers-reduced-motion`: no scaling or sliding, only opacity.

## Interaction and accessibility

- Everything works with the keyboard. Visible focus ring: 3 px accent at 40 % opacity. `/` searches, Space plays or pauses, Return opens the centre cover, Escape clears a selection or backs out of whatever is open.
- Every popup menu goes through `showMenu`: first item focused, arrow keys, Home and End, Escape back to where you were.
- Click targets are at least 28 px high.
- Buttons are `<button>`s, the sidebar items are `<button>`s in a `<nav>`, sheets are `<dialog>`
  so focus is trapped and Escape works. Images have `alt`.
- Never rely on colour alone: a status has a word as well as a colour.

## Actions

What can be done to a thing depends on what it is, never on the view it is seen in. There are three kinds:

| | Play | Open | Edit | Cover | Group | Delete |
|---|---|---|---|---|---|---|
| **Song** | it, then on | its album | Edit… | its album's | Move to album…, or the board for several | Delete… |
| **Album** | its songs | its songs | Edit… | Change cover… | Move songs to album… | Delete… |
| **Artist** | its songs | its sheet: who they are, and their albums | Rename… | Change picture… (kept on this Mac; an iPod has no place for it) | Merge (a selection) | none: an artist is a name, not a thing on the iPod |

The same verbs in the same order, reached the same ways, in every view:

- **Open** goes one level in. Double-click in a list or grid (a song plays instead); a click on the centre cover in Cover Flow (an album turns over, an artist opens its sheet).
- **Every item has one menu.** A `…` button (on hover in a row, in the hover chips of a tile or cover) and a right-click open the same menu. No text links in rows.
- **Sorting is the same everywhere.** A sort button in the toolbar for every view, and in a list the column headers do the same; both show the arrow. Names go A-Z, numbers largest first, and the same choice again reverses it. It is remembered per screen. The A-Z strip is always there: it follows a sort by letters, and in any other order a letter goes to the first name with it.
- **Artists are looked up on request.** Look up N artists in the toolbar (stoppable, and it carries on where it stopped) fetches facts from MusicBrainz and a picture: the one MusicBrainz links to, else Deezer. A picture chosen by hand stays.
- **A cover is changed on the cover.** The art itself is the control: the thumbnail beside a name in a list, the frames chip on a tile or Cover Flow cover, the art at the top of the album sheet. There is no cover column: the art shows what is there, and a placeholder shows what is not.
- **A selection works the same everywhere.** Ticks on rows and tiles (shift-click for a run, ⌘A for all shown, Escape to clear). The subtitle says how many, and the toolbar takes the actions: Clear, Delete…, Edit…, Group…, which opens the songs on the board (Rename… or Merge… for artists). A right-click on a picked item acts on the whole selection. Cover Flow has no selection: it acts on the centre cover.
- **The list of an album's songs** has one form in the sheet and on the back of a cover. Nothing is ticked; its icons (cover, edit, move, delete) act on the whole album, or on the songs picked by clicking them.
- Covers and Grouping are review screens: they keep the decisions of their own task and open an album the same way.

## Playing

Songs play in the page, streamed from the iPod (`/audio/<id>`, with byte ranges so they can seek), and carry on whatever screen is open. A song played from the Songs list goes on through the list as shown; from an album, through the album. The screen's storage bar gives way to the current song and its progress, with the state in the title bar, and the current song's row is marked wherever it shows. Apple Lossless, which Chrome cannot decode, reaches the browser as FLAC, made once with macOS's `afconvert` and kept in a 2 GB cache in `~/.repod/playing`; the next song in the queue is prepared ahead, and the title bar shows … while one is getting ready. A song the browser still cannot play is named and skipped; three in a row stop the queue. Eject stops it first.

## Sound

The click wheel, made in the page (`SOUNDS` in `index.html`), no files, and only for the library: songs, albums, artists and what is done to them. The sidebar, the toolbar, the A-Z strips, the sheets and the Manage screens are quiet (`SOUND_HOVERS` and `SOUND_ACTIONS`).

- **The sound is measured.** `WHEEL` is 45 clicks of a real iPod classic, averaged: five tones around 5.5 kHz, held for 1.2 ms and gone in about 5, with a dry edge of noise, and no thump. Its numbers were fitted against the recording band by band. Change them only against a recording. Its level is separate (`gain`) and kept low, because hearing is at its sharpest around 5 kHz and a page can't see the Mac's volume.
- A **tick**, the same sound a little quieter, when the pointer reaches a row, tile, cover or song, once per item, and for each cover crossing the middle of Cover Flow.
- A **click** for an action on an item. Right-click the speaker to choose between iPod, Soft, Button and Sharp; each plays as it is chosen.
- One sound at a time: clicking a side cover only moves the wheel, so it ticks without a click, and a tick right after a click is dropped. A fast sweep purrs instead of buzzing, and a list scrolling under a still pointer is silent.

The speaker button on the Manage rail turns it off, and the choice is remembered. A browser plays nothing until the first click or key press on the page, unless the site is allowed sound in its settings; the page asks for sound as it loads, so an allowed site ticks at once.

## Writing

- Say what happens, in the user's words: "Write 52 covers to the iPod", not "Apply manifest".
- Say what is safe: "A backup is made first", "Nothing on the iPod changes".
- Errors say what to do: "Connect the iPod and press Reload", not a stack trace.

## Editing

One form edits song name, artist, album artist, album, year and genre. It works on a song, a selection, a whole album or an artist (a rename, as artist and album artist). Each field takes typed text, a value the library already has (suggestions as you type), or a MusicBrainz result (**Search MusicBrainz** fills the fields; the user can still change them). Empty fields keep the current value, a backup is saved first, and Save is the confirmation. Demo mode runs the same flow without writing.

## The app

`Sources/AppLauncher` is a thin launcher with no features of its own: it starts `web.py`, opens the page and stays in the Dock. Quitting is Ctrl-C, so a write finishes first. What the command line can say has to be on the page too, because the app has no command line: `--developer` is the setting on Health, kept in `settings.json`, and offered only when an iPod needs it. Every part of the app that is named after the project is at the top of `package.sh`.

## Working on this page

`python3 web/dev/serve.py --scenario messy` runs it on a pretend iPod, with nothing real touched (`--library ~/.repod/library.json` starts from a copy of a real library): `dev/fake_ipodctl.py` follows the commands and output of `Sources/ipodctl`, so a change there belongs in the fake too. `web/dev/check.sh` is the whole check. It uses no iPod and no network.
