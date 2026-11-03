import Foundation
import Testing
@testable import ITDBKit

@Suite("Keeping albums in step with their tracks")
struct AlbumRegroupingTests {

    private func setting(_ library: ITLibrary, _ ids: Set<UInt32>, _ field: MhodType, to value: String?) -> ITLibrary {
        library.mappingTracks { ids.contains($0.uniqueID) ? $0.setting(field, to: value) : $0 }
    }

    private func id(_ title: String, in library: ITLibrary) throws -> UInt32 {
        try #require(library.tracks.first { $0.title == title }).uniqueID
    }

    @Test("a song moved into an album lands in that album's run of the table, in track order")
    func movedSongJoinsTheRun() throws {
        let library = try LibraryFixture.standard()
        let moved = try id("Bones", in: library)
        var edited = setting(library, [moved], .album, to: "Currents")
        edited = setting(edited, [moved], .artist, to: "Tame Impala")
        let after = try edited.reconciled(after: library)

        let blocks = after.albumOrder().filter { $0.album == "Currents" }
        #expect(blocks.count == 1)
        #expect(blocks[0].trackCount == 3)
        #expect(try after.trackOrder(album: "Currents").map(\.title).contains("Bones"))
        #expect(try #require(after.browseIndex(.album)).isPermutation(of: after.tracks.count))
        let jump = try #require(after.jumpTable(.album))
        #expect(jump.entries.reduce(0) { $0 + Int($1.count) } == after.tracks.count)
    }

    @Test("every copy of the table changes, the two master playlists do not drift apart")
    func bothCopies() throws {
        let library = try LibraryFixture.standard()
        let moved = try id("Bones", in: library)
        let after = try setting(library, [moved], .album, to: "Aftermath").reconciled(after: library)
        let tables = after.sections.flatMap { section -> [[UInt32]] in
            guard case .playlists(let playlists) = section.content else { return [] }
            return playlists.filter(\.isMaster).flatMap { $0.mhods.compactMap(BrowseIndex.init(mhod:)).filter { $0.criterion == .album }.map(\.entries) }
        }
        #expect(tables.count >= 2)
        #expect(Set(tables).count == 1)
    }

    @Test("numbers decide the order inside an album once they change")
    func numbersReorderTheAlbum() throws {
        let library = try LibraryFixture.standard()
        let order = try library.trackOrder(album: "The Bends").map(\.uniqueID)
        let last = try #require(order.last)
        let edited = library.mappingTracks { track in
            var copy = track
            if track.uniqueID == last { copy.trackNumber = 0 }
            return copy
        }
        let after = try edited.reconciled(after: library)
        #expect(try after.trackOrder(album: "The Bends").first?.uniqueID == last)
    }

    @Test("an edit that changes no key leaves the tables as they are")
    func yearLeavesTables() throws {
        let library = try LibraryFixture.standard()
        let edited = library.mappingTracks { track in
            var copy = track
            copy.year = 1999
            return copy
        }
        let after = try edited.reconciled(after: library)
        #expect(after.browseIndex(.album)?.entries == library.browseIndex(.album)?.entries)
    }

    @Test("a sort field of the old album does not follow the song; it takes the album's")
    func sortFields() throws {
        let library = try LibraryFixture.standard()
        let stray = try id("Bones", in: library)
        let withSort = library.mappingTracks { track in
            if track.uniqueID == stray { return track.setting(.sortAlbum, to: "Bends").setting(.sortAlbumArtist, to: "Radiohead") }
            if track.album == "Currents" { return track.setting(.sortAlbumArtist, to: "Impala, Tame") }
            return track
        }
        var edited = setting(withSort, [stray], .album, to: "Currents")
        edited = setting(edited, [stray], .artist, to: "Tame Impala")
        let after = try edited.reconciled(after: withSort)
        let song = try #require(after.tracks.first { $0.uniqueID == stray })
        #expect(song.sortAlbum == nil)
        #expect(song.sortAlbumArtist == "Impala, Tame")
        #expect(after.albumsSplitOnDevice().isEmpty)
    }

    @Test("a whole album renamed keeps its table run and needs nothing else")
    func renameInPlace() throws {
        let library = try LibraryFixture.standard()
        let ids = Set(library.tracks.filter { $0.album == "The Bends" }.map(\.uniqueID))
        let after = try setting(library, ids, .album, to: "Bends, The").reconciled(after: library)
        #expect(after.albumOrder().filter { $0.album == "Bends, The" }.count == 1)
        #expect(after.albumOrder().filter { $0.album == "The Bends" }.isEmpty)
    }

    @Test("a renamed album keeps an order set by hand; new numbers replace it")
    func handOrderSurvivesRename() throws {
        let library = try LibraryFixture.standard()
        let ids = try library.trackOrder(album: "The Bends").map(\.uniqueID)
        let reversed = try library.settingTrackOrder(album: "The Bends", uniqueIDs: ids.reversed())
        let renamed = try setting(reversed, Set(ids), .album, to: "Zebra").reconciled(after: reversed)
        #expect(try renamed.trackOrder(album: "Zebra").map(\.uniqueID) == ids.reversed())

        let renumbered = reversed.mappingTracks { track in
            var copy = track
            if track.uniqueID == ids[0] { copy.trackNumber = 0 }
            return copy
        }
        let sorted = try setting(renumbered, Set(ids), .album, to: "Zebra").reconciled(after: reversed)
        #expect(try sorted.trackOrder(album: "Zebra").first?.uniqueID == ids[0])
        #expect(try sorted.trackOrder(album: "Zebra").map(\.uniqueID) != ids.reversed())
    }

    @Test("what older edits left behind is found and rebuilt")
    func repairsOldDamage() throws {
        let library = try LibraryFixture.standard()
        let stray = try id("Bones", in: library)
        var damaged = setting(library, [stray], .album, to: "Currents")     // the text only, as edits used to do
        damaged = setting(damaged, [stray], .albumArtist, to: "Tame Impala")
        damaged = damaged.mappingTracks { $0.album == "Currents" ? $0.setting(.albumArtist, to: "Tame Impala") : $0 }
        #expect(damaged.albumsOutOfPlace().contains { $0.album == "Currents" })

        let after = try damaged.regroupingAlbums(Set(damaged.albumsOutOfPlace()))
        #expect(after.albumOrder().filter { $0.album == "Currents" }.count == 1)
        #expect(after.albumsOutOfPlace().isEmpty)
        #expect(after.tracks.map(\.title) == damaged.tracks.map(\.title), "the records stay where they are")
    }

    @Test("songs given one album artist share one record; the records of the old split are gone")
    func recordsMerge() throws {
        var library = try ITLibrary.withAlbumRecords(LibraryFixture.standard())
        let songs = library.tracks.filter { $0.album == "The Bends" }.map(\.uniqueID)
        library = library.mappingTracks { $0.uniqueID == songs[0] ? $0.setting(.albumArtist, to: "Someone") : $0 }
        library = try library.regroupingAlbums(Set(library.albumsOutOfPlace()))
        #expect(library.albums.filter { $0.title == "The Bends" }.count == 2)

        let edited = setting(library, Set(songs), .albumArtist, to: "Radiohead")
        let after = try edited.reconciled(after: library)
        #expect(after.albums.filter { $0.title == "The Bends" }.count == 1)
        #expect(Set(after.tracks.filter { $0.album == "The Bends" }.map(\.albumRecordIndex)).count == 1)
        #expect(after.albumsOutOfPlace().isEmpty)
    }

    @Test("survives a write and a read")
    func roundTrip() throws {
        let library = try LibraryFixture.standard()
        let moved = try id("Bones", in: library)
        let after = try setting(library, [moved], .album, to: "Aftermath").reconciled(after: library)
        let reread = try ITunesDBParser.parse(ITunesDBWriter.serialize(after))
        #expect(reread.albumOrder().filter { $0.album == "Aftermath" }.count == 1)
    }
}

private extension ITLibrary {
    /// The fixture has no `mhla` section: one record per album, as iTunes writes it.
    static func withAlbumRecords(_ library: ITLibrary) throws -> ITLibrary {
        let keys = library.albumGroups().keys.sorted { $0.album < $1.album }
        let records = keys.enumerated().map { offset, key in
            ITAlbum(index: UInt16(offset + 1), persistentID: UInt64(0x2_0000_0000) + UInt64(offset + 1), title: key.album, artist: key.albumArtist, sortArtist: nil,
                    mhods: [Mhod.text(type: .albumTitle, value: key.album), Mhod.text(type: .albumArtistName, value: key.albumArtist)],
                    rawHeader: [UInt8](repeating: 0, count: 88))
        }
        var copy = library.mappingTracks { track in
            var updated = track
            updated.albumRecordIndex = UInt32((keys.firstIndex(of: track.albumKey) ?? 0) + 1)
            return updated
        }
        let template = library.sections[0]
        copy.sections.append(ITSection(type: ITSection.Kind.albums.rawValue, content: .albums(records),
                                       rawHeader: template.rawHeader, listRawHeader: template.listRawHeader))
        return copy
    }
}
