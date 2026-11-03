import Foundation

public enum AlbumRegroupingError: Error, CustomStringConvertible {
    case trackListChanged
    case permutationBroken(criterion: UInt32)

    public var description: String {
        switch self {
        case .trackListChanged:
            return "RePod stopped this change because the song list changed while it was being edited. Nothing was changed."
        case .permutationBroken(let criterion):
            return "RePod stopped this change because one of the iPod's menus didn't check out (\(criterion)). Nothing was changed."
        }
    }
}

/// Keeps what the iPod holds about an album in step with the fields of its tracks.
///
/// An album is not one thing in the database. Besides the album and artist text of each track
/// there are the sort fields, which the device compares to tell covers apart, the `mhia` record
/// each track points to, and the browse tables, which hold the order of the menus and of the songs
/// inside an album. Changing the text alone leaves all of those behind: a song moved into an album
/// stays in its old place in the table, keeps the sort fields of its old album and points to the
/// old record - and a record that lost its tracks stays in the list under its old name.
extension ITLibrary {

    /// The `mhia` list is usable: a database with field 0x120 empty (such databases exist) has
    /// nothing to keep in step.
    private var hasAlbumRecords: Bool {
        !albums.isEmpty && tracks.contains { $0.albumRecordIndex != 0 }
    }

    /// Albums whose records or place in the table do not match their tracks. Writes nothing.
    public func albumsOutOfPlace() -> [AlbumKey] {
        let groups = albumGroups().filter { !$0.key.album.isEmpty }
        let split = Set(Dictionary(grouping: albumOrder(), by: \.album).filter { $0.value.count > 1 }.keys)
        let records = hasAlbumRecords ? Dictionary(albums.map { (UInt32($0.index), $0) }, uniquingKeysWith: { first, _ in first }) : nil
        return groups.filter { key, tracks in
            if split.contains(key.album) { return true }
            if Set(tracks.map(SortFields.init)).count > 1 { return true }
            guard let records else { return false }
            let pointers = Set(tracks.map(\.albumRecordIndex))
            return pointers.count > 1 || pointers.first.flatMap { records[$0] }?.title != key.album
        }
        .keys.sorted { ($0.album, $0.albumArtist) < ($1.album, $1.albumArtist) }
    }

    /// Rebuilds the given albums: one set of sort fields, one record, and one run of the album
    /// table in disc and track order.
    public func regroupingAlbums(_ keys: Set<AlbumKey>) throws -> ITLibrary {
        let aligned = aligning(keys, settled: { _ in false })
        var moved = [BrowseIndex.Criterion.album: aligned.runs(of: Set(keys.map(\.album)))]
        for (criterion, positions) in aligned.keyChanges(since: self) where criterion != .album {
            moved[criterion] = positions.map { [$0] }
        }
        return try aligned.reseating(moved)
    }

    /// `self` is `original` with the fields of some tracks changed, the tracks in the same places.
    /// Everything derived from those fields follows them; the fields themselves stay as given.
    public func reconciled(after original: ITLibrary) throws -> ITLibrary {
        let before = original.tracks
        guard tracks.count == before.count else { throw AlbumRegroupingError.trackListChanged }

        // A sort field belongs to the old value: left in place it files the song under the old name.
        let cleared = zip(before, tracks).map { old, new -> ITTrack in
            var track = new
            let pairs: [(MhodType, MhodType)] = [(.album, .sortAlbum), (.albumArtist, .sortAlbumArtist), (.artist, .sortArtist),
                                                 (.title, .sortTitle), (.composer, .sortComposer)]
            for (field, sort) in pairs where old.fields[field] != new.fields[field] && old.fields[sort] == new.fields[sort] {
                track = track.setting(sort, to: nil)
            }
            return track
        }
        var changed = self
        changed.sections = sections.map { section in
            guard case .tracks = section.content else { return section }
            var updated = section
            updated.content = .tracks(cleared)
            return updated
        }

        var moved = Set<UInt32>()
        var keys = Set<AlbumKey>()
        var compilations: [AlbumKey: Bool] = [:]    // a flag that was set is what the album is now
        for (old, new) in zip(before, cleared) where old.albumKey != new.albumKey || old.isCompilation != new.isCompilation {
            moved.insert(new.uniqueID)
            keys.insert(new.albumKey)
            if old.isCompilation != new.isCompilation { compilations[new.albumKey] = new.isCompilation }
        }
        let aligned = changed.aligning(keys.filter { !$0.album.isEmpty }, compilations: compilations, settled: { !moved.contains($0.uniqueID) })

        var groups: [BrowseIndex.Criterion: [[Int]]] = [:]
        var inOrder = Set<Int>()
        for (criterion, positions) in aligned.keyChanges(since: original) {
            if criterion == .album {
                // The whole album moves, so that it ends up as one run in track order. An album that
                // only changed its name keeps its own order: it may have been set by hand.
                let all = aligned.tracks
                groups[criterion] = aligned.runs(of: Set(positions.map { all[$0].album ?? "" }))
                for group in groups[criterion] ?? [] {
                    let was = before[group[0]].album
                    let wholeAlbum = before.filter { $0.album == was }.count == group.count
                        && group.allSatisfy { before[$0].album == was }
                    if wholeAlbum, group.allSatisfy({ Self.sameSongOrder(before[$0], all[$0]) }) { inOrder.formUnion(group) }
                }
            } else {
                groups[criterion] = positions.map { [$0] }
            }
        }
        return try aligned.reseating(groups, keepingOrderOf: inOrder)
    }

    private static func sameSongOrder(_ old: ITTrack, _ new: ITTrack) -> Bool {
        old.discNumber == new.discNumber && old.trackNumber == new.trackNumber && old.title == new.title
    }

    // MARK: - Sort fields and records

    /// The fields the device compares when it decides which tracks are one cover.
    private struct SortFields: Hashable {
        let album, albumArtist, artist: String?
        init(_ track: ITTrack) {
            album = track.sortAlbum
            albumArtist = track.sortAlbumArtist
            artist = track.sortArtist
        }
    }

    /// Track positions of each album, ordered by album, so the result is repeatable.
    private func positions(of keys: Set<AlbumKey>) -> [[Int]] {
        let all = tracks
        return Dictionary(grouping: all.indices.filter { keys.contains(all[$0].albumKey) }, by: { all[$0].albumKey })
            .sorted { ($0.key.album, $0.key.albumArtist) < ($1.key.album, $1.key.albumArtist) }
            .map(\.value)
    }

    /// Track positions of each album name: the table keeps a name in one run, whatever its artists.
    private func runs(of names: Set<String>) -> [[Int]] {
        let all = tracks
        return Dictionary(grouping: all.indices.filter { names.contains(all[$0].album ?? "") }, by: { all[$0].album ?? "" })
            .sorted { $0.key < $1.key }
            .map(\.value)
    }

    /// Gives every track of an album the same sort fields, compilation flag and record.
    /// `settled` tracks were there already, so the album follows them, not the newcomers.
    private func aligning(_ keys: Set<AlbumKey>, compilations: [AlbumKey: Bool] = [:], settled: (ITTrack) -> Bool) -> ITLibrary {
        guard !keys.isEmpty else { return self }
        var all = tracks
        var records = albums
        let useRecords = hasAlbumRecords

        for positions in positions(of: keys) {
            let members = positions.map { all[$0] }
            let key = members[0].albumKey
            let established = members.filter(settled)
            let anchor = established.isEmpty ? members : established

            let tally = Dictionary(grouping: anchor, by: SortFields.init)
            let sort = anchor.map(SortFields.init).first { tally[$0]?.count == tally.values.map(\.count).max() } ?? SortFields(members[0])
            let compilation = compilations[key] ?? (anchor.filter(\.isCompilation).count * 2 > anchor.count)

            let record = useRecords ? Self.record(for: key, of: members, anchor: anchor, among: &records, tracks: all) : nil
            for position in positions {
                var track = all[position].setting(.sortAlbum, to: sort.album)
                    .setting(.sortAlbumArtist, to: sort.albumArtist)
                    .setting(.sortArtist, to: sort.artist)
                track.isCompilation = compilation
                if let record { track.albumRecordIndex = record }
                all[position] = track
            }
        }

        // A record no track points to is gone from the iPod's point of view, even when its name is
        // still in use: each artist of a split album had one.
        if useRecords {
            let live = Set(all.map(\.albumRecordIndex))
            records.removeAll { !live.contains(UInt32($0.index)) }
        }
        var copy = self
        copy.sections = sections.map { section in
            var updated = section
            switch section.content {
            case .tracks: updated.content = .tracks(all)
            case .albums: updated.content = .albums(records)
            default: break
            }
            return updated
        }
        return copy
    }

    /// The record of an album: the one its tracks already use, one with its name and artist, the old
    /// record renamed when the whole album moved (it keeps its artwork owner), or a new one.
    private static func record(for key: AlbumKey, of members: [ITTrack], anchor: [ITTrack],
                               among records: inout [ITAlbum], tracks: [ITTrack]) -> UInt32 {
        let inside = Set(members.map(\.uniqueID))
        let pointers = anchor.map(\.albumRecordIndex) + members.map(\.albumRecordIndex)
        func shared(_ index: UInt32) -> Bool {
            tracks.contains { $0.albumRecordIndex == index && !inside.contains($0.uniqueID) }
        }
        let used = pointers.compactMap { index in records.firstIndex { UInt32($0.index) == index } }

        if let at = used.first(where: { records[$0].title == key.album && records[$0].artist == key.albumArtist })
            ?? records.firstIndex(where: { $0.title == key.album && $0.artist == key.albumArtist }) {
            return UInt32(records[at].index)
        }
        if let at = used.first(where: { !shared(UInt32(records[$0].index)) }) {
            records[at] = records[at].renamed(title: key.album, artist: key.albumArtist)
            return UInt32(records[at].index)
        }

        let index = (records.map { UInt32($0.index) }.max() ?? 0) + 1
        let persistentID = newAlbumPersistentID(albums: records, tracks: tracks)
        var created = ITAlbum(index: UInt16(clamping: index), persistentID: persistentID, title: key.album, artist: key.albumArtist, sortArtist: nil,
                              mhods: [Mhod.text(type: .albumTitle, value: key.album), Mhod.text(type: .albumArtistName, value: key.albumArtist)],
                              rawHeader: records.last?.rawHeader ?? [])
        let owners = Set(members.map(\.persistentID))
        created.artworkOwnerPersistentID = records.lazy.map(\.artworkOwnerPersistentID).first { owners.contains($0) } ?? 0
        records.append(created)
        return index
    }

    // MARK: - Browse tables

    /// Track positions whose place in a browse table is no longer the same as in `original`.
    private func keyChanges(since original: ITLibrary) -> [BrowseIndex.Criterion: [Int]] {
        let before = original.tracks, after = tracks
        var result: [BrowseIndex.Criterion: [Int]] = [:]
        for criterion in Self.knownCriteria {
            let changed = after.indices.filter { Self.browseKey(criterion, for: before[$0]) != Self.browseKey(criterion, for: after[$0]) }
            if !changed.isEmpty { result[criterion] = changed }
        }
        return result
    }

    /// Takes the given track positions out of their tables and puts each group back where it
    /// belongs, the jump bars with them. Every other entry keeps its order. A table of a criterion
    /// we cannot name follows the table it is a copy of; any other is left alone.
    /// `kept`: tracks of the album table that stay in their present order inside their block.
    private func reseating(_ moved: [BrowseIndex.Criterion: [[Int]]], keepingOrderOf kept: Set<Int> = []) throws -> ITLibrary {
        guard let master = masterPlaylist else { return self }
        let all = tracks
        let tables = master.mhods.compactMap(BrowseIndex.init(mhod:))
        var result = self

        for criterion in Self.knownCriteria {
            guard let groups = moved[criterion], let table = tables.first(where: { $0.criterion == criterion }) else { continue }
            let keys = all.map { Self.browseKey(criterion, for: $0) }
            let entries = Self.reseated(groups, in: table.entries, keys: keys, keepingOrderOf: criterion == .album ? kept : [])
            let twins = tables.filter { $0.criterion == criterion || (!Self.knownCriteria.contains($0.criterion) && $0.entries == table.entries) }

            for twin in twins {
                var updated = twin
                updated.entries = entries
                guard updated.isPermutation(of: all.count) else {
                    throw AlbumRegroupingError.permutationBroken(criterion: twin.criterion.rawValue)
                }
                result = result.replacingBrowseIndex(twin.criterion, entries: entries)

                guard let jump = jumpTable(twin.criterion) else { continue }
                var letters: [UInt32: UInt32] = [:]
                let perPosition = jump.entries.flatMap { Array(repeating: $0.letter, count: Int($0.count)) }
                if perPosition.count == table.entries.count {
                    for (entry, letter) in zip(table.entries, perPosition) { letters[entry] = letter }
                }
                for position in groups.joined() { letters[UInt32(position)] = nil }
                let runs = entries.map { entry in
                    (letter: letters[entry] ?? Self.letterCode(criterion, for: all[Int(entry)]), count: 1)
                }
                result = result.replacingJumpTable(twin.criterion, with: jump.rebuilt(fromRuns: runs))
            }
        }
        return result
    }

    /// A group is placed as a block, in key order, where it conflicts with the fewest neighbors.
    static func reseated(_ groups: [[Int]], in entries: [UInt32], keys: [BrowseKey], keepingOrderOf kept: Set<Int> = []) -> [UInt32] {
        let moving = Set(groups.joined().map(UInt32.init))
        var result = entries.filter { !moving.contains($0) }
        let place = Dictionary(entries.enumerated().map { (Int($0.element), $0.offset) }, uniquingKeysWith: { first, _ in first })
        let ordered = groups.map { group in
            kept.contains(group[0]) ? group.sorted { (place[$0] ?? 0) < (place[$1] ?? 0) }
                : group.sorted { keys[$0] != keys[$1] ? keys[$0] < keys[$1] : $0 < $1 }
        }
        for block in ordered.sorted(by: { keys[$0[0]] < keys[$1[0]] }) {
            let position = insertionPosition(of: keys[block[0]], in: result, keys: keys)
            result.insert(contentsOf: block.map(UInt32.init), at: position)
        }
        return result
    }
}

extension ITAlbum {
    /// The same record under another name. The sort artist belonged to the old name.
    func renamed(title: String, artist: String) -> ITAlbum {
        var texts = mhods.filter { $0.type != .albumSortArtist }
        for (type, value) in [(MhodType.albumTitle, title), (.albumArtistName, artist)] {
            if let at = texts.firstIndex(where: { $0.type == type }) {
                texts[at] = texts[at].replacingText(value)
            } else {
                texts.append(Mhod.text(type: type, value: value))
            }
        }
        return ITAlbum(index: index, persistentID: persistentID, title: title, artist: artist, sortArtist: nil,
                       mhods: texts, rawHeader: rawHeader)
    }
}
