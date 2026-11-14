import Foundation

/// Playlists made, filled, emptied, renamed and removed off the iPod.
///
/// What a playlist record must look like comes from the iPod itself, never from a format description:
/// a new list is a copy of an ordinary list already in the database (on a Classic 7G, the On-The-Go
/// lists made on the device), with its own name, id and times; only an iPod without any ordinary list
/// starts from the master list, without its browse tables. An entry is likewise a copy of an entry the
/// iPod wrote, with the track, its `persistentID`, a time and an id of its own.
///
/// Measured on a Classic 7G (5 lists, 798 entries): every list is kept twice, in the sections of
/// type 2 and 3, byte for byte the same - so every change here goes into both, with the same ids.
/// The master list and smart lists are never changed here: the master list is all songs and is kept
/// by adding and removing songs, and a smart list is filled by the iPod from its rules.
public enum PlaylistEditError: Error, CustomStringConvertible, Equatable {
    case noPlaylistSection
    case noTemplate
    case unknownPlaylist(UInt64)
    case notEditable(String)
    case emptyName
    case unknownTrack(UInt32)

    public var description: String {
        switch self {
        case .noPlaylistSection: "the database has no playlist section"
        case .noTemplate: "the database has no playlist to model a new one on"
        case .unknownPlaylist(let id): String(format: "no playlist %016llx", id)
        case .notEditable(let name): "\(name) is the list of all songs or a smart playlist, and is not changed by hand"
        case .emptyName: "a playlist needs a name"
        case .unknownTrack(let id): "song \(id) is not on the iPod"
        }
    }
}

extension ITPlaylist {
    /// A list the iPod fills itself from rules.
    public var isSmart: Bool { mhods.contains { $0.type == .smartPlaylistData || $0.type == .smartPlaylistRules } }
    /// A list songs can be put into and taken out of by hand.
    public var isEditable: Bool { !isMaster && !isSmart }
}

extension ITLibrary {

    /// Ordinary playlists, as the first section that carries lists has them.
    public var editablePlaylists: [ITPlaylist] { playlists.filter(\.isEditable) }

    /// A new playlist with these songs, in this order, in every section that carries lists.
    public func creatingPlaylist(named name: String, trackIDs: [UInt32], persistentID: UInt64 = .random(in: 1...UInt64.max),
                                 date: Date = Date()) throws -> ITLibrary {
        let name = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty else { throw PlaylistEditError.emptyName }
        guard sections.contains(where: { if case .playlists = $0.content { return true }; return false }) else {
            throw PlaylistEditError.noPlaylistSection
        }
        let (header, mhods) = try playlistTemplate()
        let now = MacTime.value(from: date)
        var record = header
        record.set(MhypLayout.isMaster, 0)
        record.set(MhypLayout.persistentID, persistentID)
        record.set(MhypLayout.created, now)
        record.set(MhypLayout.modified, now)
        let entries = try newEntries(trackIDs, date: date)
        let named = mhods.map { $0.type == .title ? $0.replacingText(name) : $0 }
        let playlist = ITPlaylist(name: name, isMaster: false, persistentID: persistentID, entries: entries,
                                  mhods: named.contains { $0.type == .title } ? named : [Mhod.text(type: .title, value: name)] + named,
                                  rawHeader: record)
        var result = self
        result.sections = sections.map { section in
            guard case .playlists(let lists) = section.content else { return section }
            var changed = section
            changed.content = .playlists(lists + [playlist])
            return changed
        }
        return result
    }

    /// Songs at the end of a playlist. A song already in it is not put in twice.
    public func addingTracks(_ trackIDs: [UInt32], toPlaylist id: UInt64, date: Date = Date()) throws -> ITLibrary {
        let present = Set(try editable(id).entries.map(\.trackID))
        var wanted: [UInt32] = []
        for track in trackIDs where !present.contains(track) && !wanted.contains(track) { wanted.append(track) }
        guard !wanted.isEmpty else { return self }
        let entries = try newEntries(wanted, date: date)
        return try changingPlaylist(id, date: date) { $0.entries += entries }
    }

    /// Songs out of a playlist; the songs themselves stay on the iPod.
    public func removingTracks(_ trackIDs: Set<UInt32>, fromPlaylist id: UInt64, date: Date = Date()) throws -> ITLibrary {
        _ = try editable(id)
        return try changingPlaylist(id, date: date) { $0.entries.removeAll { trackIDs.contains($0.trackID) } }
    }

    public func renamingPlaylist(_ id: UInt64, to name: String, date: Date = Date()) throws -> ITLibrary {
        let name = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty else { throw PlaylistEditError.emptyName }
        _ = try editable(id)
        return try changingPlaylist(id, date: date) { playlist in
            playlist = ITPlaylist(name: name, isMaster: false, persistentID: playlist.persistentID, entries: playlist.entries,
                                  mhods: playlist.mhods.contains { $0.type == .title }
                                    ? playlist.mhods.map { $0.type == .title ? $0.replacingText(name) : $0 }
                                    : [Mhod.text(type: .title, value: name)] + playlist.mhods,
                                  rawHeader: playlist.rawHeader)
        }
    }

    /// The playlist goes; its songs stay on the iPod.
    public func deletingPlaylist(_ id: UInt64) throws -> ITLibrary {
        _ = try editable(id)
        var result = self
        result.sections = sections.map { section in
            guard case .playlists(let lists) = section.content else { return section }
            var changed = section
            changed.content = .playlists(lists.filter { $0.persistentID != id })
            return changed
        }
        return result
    }

    // MARK: - Helpers

    private func editable(_ id: UInt64) throws -> ITPlaylist {
        guard let playlist = playlists.first(where: { $0.persistentID == id }) else { throw PlaylistEditError.unknownPlaylist(id) }
        guard playlist.isEditable else { throw PlaylistEditError.notEditable(playlist.name ?? "this list") }
        return playlist
    }

    /// The same change to the list in every section, and its time of change.
    private func changingPlaylist(_ id: UInt64, date: Date, _ change: (inout ITPlaylist) -> Void) throws -> ITLibrary {
        var result = self, found = false
        result.sections = sections.map { section in
            guard case .playlists(let lists) = section.content else { return section }
            var changed = section
            changed.content = .playlists(lists.map { list in
                guard list.persistentID == id else { return list }
                found = true
                var edited = list
                change(&edited)
                var header = edited.rawHeader
                header.set(MhypLayout.modified, MacTime.value(from: date))
                return ITPlaylist(name: edited.name, isMaster: edited.isMaster, persistentID: edited.persistentID,
                                  entries: edited.entries, mhods: edited.mhods, rawHeader: header)
            })
            return changed
        }
        guard found else { throw PlaylistEditError.unknownPlaylist(id) }
        return result
    }

    /// The header and records of a new list: an ordinary list the iPod has, else the master list without its browse tables.
    private func playlistTemplate() throws -> (header: [UInt8], mhods: [Mhod]) {
        if let ordinary = editablePlaylists.first(where: { !$0.mhods.isEmpty }) {
            return (ordinary.rawHeader, ordinary.mhods.filter { [.title, .playlistEntryData, .playlistPreference].contains($0.type) })
        }
        guard let master = masterPlaylist else { throw PlaylistEditError.noTemplate }
        return (master.rawHeader, master.mhods.filter { [.title, .playlistEntryData, .playlistPreference].contains($0.type) })
    }

    /// Entries for these songs: copies of an entry the iPod wrote, each with its song, a time, and an id and place of its own.
    /// The place (`order`) follows the highest in the whole database, as the iPod's own lists do.
    private func newEntries(_ trackIDs: [UInt32], date: Date) throws -> [ITPlaylistEntry] {
        guard !trackIDs.isEmpty else { return [] }
        let all = sections.flatMap { section -> [ITPlaylist] in if case .playlists(let lists) = section.content { return lists }; return [] }
        guard let template = all.first(where: \.isEditable)?.entries.first ?? masterPlaylist?.entries.last else {
            throw PlaylistEditError.noTemplate
        }
        let byID = Dictionary(tracks.map { ($0.uniqueID, $0) }, uniquingKeysWith: { first, _ in first })
        var order = all.flatMap(\.entries).map(\.order).max() ?? 0
        let now = MacTime.value(from: date)
        return try trackIDs.map { id in
            guard let track = byID[id] else { throw PlaylistEditError.unknownTrack(id) }
            order += 1
            var header = template.rawHeader
            header.set(MhipLayout.added, now)
            header.set(MhipLayout.trackPersistentID, track.persistentID)
            header.set(MhipLayout.entryID, UInt64.random(in: 1...UInt64.max))
            return ITPlaylistEntry(trackID: id, order: order, mhods: template.mhods, rawHeader: header)
        }
    }
}
