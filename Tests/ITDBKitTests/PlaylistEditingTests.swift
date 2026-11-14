import Foundation
import Testing
@testable import ITDBKit

@Suite("Playlists")
struct PlaylistEditingTests {

    static func lists(_ library: ITLibrary) -> [[ITPlaylist]] {
        library.sections.compactMap { if case .playlists(let lists) = $0.content { return lists }; return nil }
    }

    /// What the iPod reads back: the bytes, parsed again.
    static func reread(_ library: ITLibrary) throws -> ITLibrary {
        let bytes = ITunesDBWriter.serialize(library)
        let again = try ITunesDBParser.parse(bytes)
        #expect(ITunesDBWriter.serialize(again) == bytes, "write -> read -> write the same bytes")
        return again
    }

    @Test("a new playlist goes into both sections, the same in each, with its songs in the order given")
    func create() throws {
        let library = try LibraryFixture.standard()
        let made = try Self.reread(library.creatingPlaylist(named: "Road trip", trackIDs: [15, 10, 13], persistentID: 0xFEED))
        let sections = Self.lists(made)
        #expect(sections.count == 2)
        for lists in sections {
            let list = try #require(lists.first { $0.persistentID == 0xFEED })
            #expect(list.name == "Road trip")
            #expect(!list.isMaster && list.isEditable)
            #expect(list.entries.map(\.trackID) == [15, 10, 13])
            #expect(!list.mhods.contains { $0.type == .libraryPlaylistIndex || $0.type == .libraryPlaylistJumpTable },
                    "the browse tables belong to the master list only")
            #expect(list.rawHeader[MhypLayout.persistentID] == 0xFEED)
            #expect((list.rawHeader[MhypLayout.created] ?? 0) > 0)
        }
        let entries = sections.map { $0.first { $0.persistentID == 0xFEED }!.entries }
        #expect(entries[0].map(\.rawHeader) == entries[1].map(\.rawHeader), "both copies carry the same entry ids")
        let first = try #require(entries[0].first)
        #expect(first.rawHeader[MhipLayout.trackPersistentID] == 0x1000 + 15, "an entry names its song's persistentID")
        let highest = try #require(library.masterPlaylist?.entries.map(\.order).max())
        #expect(entries[0].map(\.order) == [highest + 1, highest + 2, highest + 3])
        #expect(made.masterPlaylist?.entries.count == library.masterPlaylist?.entries.count, "the list of all songs is untouched")
        #expect(made.editablePlaylists.map(\.name) == ["Road trip"])
    }

    @Test("a second playlist is modelled on the first ordinary one, not on the master list")
    func modelledOnAnOrdinaryList() throws {
        let one = try LibraryFixture.standard().creatingPlaylist(named: "One", trackIDs: [10], persistentID: 1)
        let two = try one.creatingPlaylist(named: "Two", trackIDs: [11], persistentID: 2)
        let first = try #require(two.playlists.first { $0.persistentID == 1 }), second = try #require(two.playlists.first { $0.persistentID == 2 })
        #expect(second.mhods.map(\.type) == first.mhods.map(\.type))
        #expect(second.name == "Two")
    }

    @Test("adding skips what is already there; removing, renaming and deleting touch only that list")
    func edit() throws {
        let base = try LibraryFixture.standard().creatingPlaylist(named: "Mix", trackIDs: [10, 11], persistentID: 7)
        let added = try Self.reread(base.addingTracks([11, 12, 12, 13], toPlaylist: 7))
        #expect(added.playlists.first { $0.persistentID == 7 }?.entries.map(\.trackID) == [10, 11, 12, 13])
        let removed = try added.removingTracks([10, 13], fromPlaylist: 7)
        #expect(Self.lists(removed).allSatisfy { $0.first { $0.persistentID == 7 }?.entries.map(\.trackID) == [11, 12] })
        #expect(removed.tracks.count == base.tracks.count, "the songs stay on the iPod")
        let renamed = try Self.reread(removed.renamingPlaylist(7, to: "Evening"))
        #expect(Self.lists(renamed).allSatisfy { $0.first { $0.persistentID == 7 }?.name == "Evening" })
        let gone = try renamed.deletingPlaylist(7)
        #expect(Self.lists(gone).allSatisfy { lists in !lists.contains { $0.persistentID == 7 } })
        #expect(gone.masterPlaylist?.entries.count == base.masterPlaylist?.entries.count)
    }

    @Test("the list of all songs, an unknown list, an unknown song and an empty name are refused")
    func refusals() throws {
        let library = try LibraryFixture.standard()
        let master = try #require(library.masterPlaylist)
        #expect(throws: PlaylistEditError.notEditable(master.name ?? "")) { try library.addingTracks([10], toPlaylist: master.persistentID) }
        #expect(throws: PlaylistEditError.unknownPlaylist(99)) { try library.deletingPlaylist(99) }
        #expect(throws: PlaylistEditError.unknownTrack(999)) { try library.creatingPlaylist(named: "X", trackIDs: [999]) }
        #expect(throws: PlaylistEditError.emptyName) { try library.creatingPlaylist(named: "  ", trackIDs: [10]) }
    }

    @Test("the id is read where the iPod keeps it, after the time the list was made")
    func headerLayout() throws {
        let library = try LibraryFixture.standard()
        #expect(library.masterPlaylist?.persistentID == 0xABCD)
    }
}
