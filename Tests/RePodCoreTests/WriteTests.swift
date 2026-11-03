import Foundation
import CoreGraphics
import Testing
@testable import ITDBKit
@testable import RePodCore

/// A successful write to the iPod through `WriteTransaction` - on a fake signed
/// iPod in a temporary folder. Characterization tests: they record the current
/// behavior before the write transaction is broken into parts.
@Suite("Writing to the iPod")
@MainActor
struct WriteTests {

    /// The fake's GUID. The database is signed with it, and `SysInfoExtended` gives it -
    /// as on an iPod that was once connected to a Mac.
    nonisolated static let guid = "000A270011223344"

    /// What the fake should have apart from the signed database and the files in Music.
    struct Fixture {
        var musicRunning = false
        /// Artwork for the album Aftermath (format 1027), added the same way the
        /// app adds first artwork.
        var cover = false
        /// "Eventually" (16) is named like "Let It Happen" (15) - a duplicate.
        var duplicate = false
        /// A track without a file in Music.
        var missingFile: UInt32? = nil
        /// A track with a WAV 24 bit / 96 kHz file - to convert.
        var hiRes: UInt32? = nil
        /// A track with the artwork marker (0x7E) zeroed by an old generator bug
        /// (described in `TrackArtworkFields`).
        var brokenMarker: UInt32? = nil
        /// A track whose path in the database points to the `F00` folder with the files
        /// of the other tracks - a path from the database is not trusted.
        var pointsAtFolder: UInt32? = nil
        /// The fake's GUID - a different one when a test needs two different iPods.
        var guid = WriteTests.guid
    }

    /// A fake iPod classic: a signed database, a file in Music for every track,
    /// backups and the order intent in the same temporary folder.
    @MainActor
    struct Rig {
        let root: URL
        let paths: IPodPaths
        let environment: LibraryEnvironment
        let device: IPodDevice

        var backups: SnapshotStore { get throws { try environment.backups() } }

        func onDisk() throws -> ITLibrary { try ITunesDBParser.parse(Data(contentsOf: paths.iTunesDB)) }

        func artworkOnDisk() throws -> ArtworkLibrary { try ArtworkDBParser.parse(Data(contentsOf: paths.artworkDB)) }

        func signedWithDeviceKey() throws -> Bool {
            FireWireGUID.signs(try Hash58.fireWireID(fromHex: WriteTests.guid), try Data(contentsOf: paths.iTunesDB))
        }

        func musicFile(_ id: Int, _ ext: String = "mp3") -> URL {
            paths.musicDirectory.appendingPathComponent("F00/\(id).\(ext)")
        }

        func musicFiles() throws -> Set<String> {
            Set(try FileManager.default.contentsOfDirectory(atPath: paths.musicDirectory.appendingPathComponent("F00").path))
        }

        func cleanUp() { try? FileManager.default.removeItem(at: root) }
    }

    static func rig(_ fixture: Fixture = Fixture()) async throws -> Rig {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("repod-write-\(UUID().uuidString)", isDirectory: true)
        let paths = IPodPaths(mountPoint: root.appendingPathComponent("iPod", isDirectory: true))
        let manager = FileManager.default
        try manager.createDirectory(at: paths.iTunesDB.deletingLastPathComponent(), withIntermediateDirectories: true)
        try manager.createDirectory(at: paths.musicDirectory.appendingPathComponent("F00"), withIntermediateDirectories: true)
        try manager.createDirectory(at: paths.sysInfoExtended.deletingLastPathComponent(), withIntermediateDirectories: true)

        var titles: [UInt32: String] = [:]
        if fixture.duplicate { titles[16] = "Let It Happen" }
        var extensions: [UInt32: String] = [:]
        if let id = fixture.hiRes { extensions[id] = "wav" }
        // Records described as MP3 and with the artwork marker, like records from iTunes:
        // a new track is created on the template of a record of the same format.
        var library = try ITunesDBParser.parse(SyntheticLibrary.database(titles: titles, extensions: extensions))
            .mappingTracks { track in
                let kind: TrackFileKind = track.uniqueID == fixture.hiRes ? .wav : .mp3
                var typed = track.settingFileKind(kind)
                if track.uniqueID == fixture.pointsAtFolder { typed = typed.setting(.location, to: ":iPod_Control:Music:F00") }
                return track.uniqueID == fixture.brokenMarker ? typed : typed.restoringArtworkMarker()
            }
        if fixture.cover {
            let owner = try #require(library.tracks.first { $0.album == "Aftermath" })
            _ = try ArtworkAddition.add(ownerPersistentID: owner.persistentID, sourceByteCount: 5000, trackCount: 2, in: paths,
                                        deviceFormats: [.init(formatID: 1027, width: 100, height: 100, pixelFormat: "L565")]) { w, h in
                try ITHMBImage(rendering: Self.image(red: 1, green: 0, blue: 0), width: w, height: h)
            }
            library = library.linkingAlbumArtwork(album: "Aftermath", ownerPersistentID: owner.persistentID)
                .markingAlbumArtwork(album: "Aftermath", sourceByteCount: 5000)
        }
        try Hash58.sign(ITunesDBWriter.serialize(library), fireWireID: Hash58.fireWireID(fromHex: fixture.guid)).write(to: paths.iTunesDB)
        try Data("""
            <?xml version="1.0" encoding="UTF-8"?>
            <plist version="1.0"><dict><key>FireWireGUID</key><string>\(fixture.guid)</string></dict></plist>
            """.utf8).write(to: paths.sysInfoExtended)
        for id in UInt32(10)...16 where id != fixture.missingFile {
            if id == fixture.hiRes {
                try Self.wave(bitDepth: 24, sampleRate: 96_000).write(to: paths.musicDirectory.appendingPathComponent("F00/\(id).wav"))
            } else {
                try Data(repeating: UInt8(id), count: 1000).write(to: paths.musicDirectory.appendingPathComponent("F00/\(id).mp3"))
            }
        }

        var environment = LibraryEnvironment.isolated(in: root.appendingPathComponent("Support", isDirectory: true))
        environment.isMusicRunning = { fixture.musicRunning }
        let volume = try #require(IPodVolume.at(paths.mountPoint))
        let device = try IPodDevice.open(volume)
        #expect(device.supportsWriting, "the fake must accept writing: \(device.readOnlyReason?.description ?? "")")

        return Rig(root: root, paths: paths, environment: environment, device: device)
    }

    /// A uniform square in the given color.
    nonisolated static func image(red: CGFloat, green: CGFloat, blue: CGFloat) throws -> CGImage {
        let context = try #require(CGContext(data: nil, width: 64, height: 64, bitsPerComponent: 8, bytesPerRow: 0,
                                             space: CGColorSpaceCreateDeviceRGB(),
                                             bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue))
        context.setFillColor(red: red, green: green, blue: blue, alpha: 1)
        context.fill(CGRect(x: 0, y: 0, width: 64, height: 64))
        return try #require(context.makeImage())
    }

    /// A WAV file (PCM, stereo, 0.2 s of silence).
    nonisolated static func wave(bitDepth: Int, sampleRate: Int) -> Data {
        let channels = 2, frames = sampleRate / 5
        let blockAlign = channels * bitDepth / 8
        let dataSize = frames * blockAlign
        var data = Data()
        func u32(_ v: Int) { withUnsafeBytes(of: UInt32(v).littleEndian) { data.append(contentsOf: $0) } }
        func u16(_ v: Int) { withUnsafeBytes(of: UInt16(v).littleEndian) { data.append(contentsOf: $0) } }
        data.append(contentsOf: Array("RIFF".utf8)); u32(36 + dataSize); data.append(contentsOf: Array("WAVE".utf8))
        data.append(contentsOf: Array("fmt ".utf8)); u32(16); u16(1); u16(channels); u32(sampleRate)
        u32(sampleRate * blockAlign); u16(blockAlign); u16(bitDepth)
        data.append(contentsOf: Array("data".utf8)); u32(dataSize)
        data.append(Data(count: dataSize))
        return data
    }

    /// The same path as `ipodctl remove`: the removal plan in `ChangeSet`, the write
    /// through the transaction, without the window model.
    @Test("removal through the transaction (ipodctl): the folder neither goes into the queue nor disappears")
    func transactionKeepsFolderOfRemovedRecord() async throws {
        let rig = try await Self.rig(Fixture(pointsAtFolder: 10)); defer { rig.cleanUp() }
        let device = try #require(rig.device)
        let filesBefore = try rig.musicFiles()
        let database = try Data(contentsOf: rig.paths.iTunesDB)
        let library = try ITunesDBParser.parse(database)
        let updated = try library.removingTracks(uniqueIDs: [10])
        var changes = ChangeSet()
        let queued = changes.queueFiles(of: library.tracks.filter { $0.uniqueID == 10 }, keeping: updated,
                                        paths: rig.paths, files: LocalFileSystem(), keepCopiesIn: nil)
        #expect(queued.isEmpty)
        var transaction = WriteTransaction(library: updated, changes: changes, device: device,
                                           capabilities: IPodCapabilities.resolve(format: device.format, model: device.model),
                                           environment: rig.environment, note: "test")
        transaction.expectedSourceDigest = IPodDevice.digest(of: database)

        let result = try await transaction.run()

        #expect(!(try rig.onDisk().tracks.contains { $0.uniqueID == 10 }))
        #expect(result.outcome.deletedFiles == 0)
        #expect(try rig.musicFiles() == filesBefore)
    }

    @Test("a folder in the deletion queue despite the rule: the after-write phase checks again and leaves it")
    func afterWriteKeepsFolder() async throws {
        let rig = try await Self.rig(); defer { rig.cleanUp() }
        let device = try #require(rig.device)
        let filesBefore = try rig.musicFiles()
        let folder = rig.paths.musicDirectory.appendingPathComponent("F00")

        var ledger = WriteLedger()
        AfterWritePhase(filesToDelete: [folder], archiveBeforeDelete: [:], replacements: [:], files: LocalFileSystem())
            .run(on: device, ledger: &ledger)

        #expect(ledger.deletedFiles == 0)
        #expect(ledger.keptFiles == [folder])
        #expect(ledger.completed.deletionsToRetry.isEmpty, "the rule will reject it on the next write too - we do not retry")
        #expect(try rig.musicFiles() == filesBefore)
    }

    /// `ipodctl artwork --per-track`: the nano 2G looks up artwork by track.
    @Test("the album image for another track: a new mhii with the same slices, coverage settled")
    func coverShared() async throws {
        let rig = try await Self.rig(Fixture(cover: true)); defer { rig.cleanUp() }
        let device = try #require(rig.device)
        let database = try Data(contentsOf: rig.paths.iTunesDB)
        let library = try ITunesDBParser.parse(database)
        let tracks = library.tracks.filter { $0.album == "Aftermath" }
        let image = try #require(try rig.artworkOnDisk().images.first)
        let other = try #require(tracks.first { $0.persistentID != image.trackPersistentID })
        var changes = ChangeSet()
        changes.queueCoverSharing(imageID: image.id, with: [other], album: "Aftermath", for: AlbumID(albumOf: tracks))
        var transaction = WriteTransaction(library: library, changes: changes, device: device,
                                           capabilities: IPodCapabilities.resolve(format: device.format, model: device.model),
                                           environment: rig.environment, note: "test")
        transaction.expectedSourceDigest = IPodDevice.digest(of: database)

        let result = try await transaction.run()

        #expect(result.outcome.coversWritten == 1)
        #expect(result.remaining.artworkSharing.isEmpty)
        let images = try rig.artworkOnDisk().images
        #expect(images.count == 2)
        let shared = try #require(images.first { $0.trackPersistentID == other.persistentID })
        #expect(shared.variants.map(\.byteOffset) == image.variants.map(\.byteOffset), "the same slices")
        #expect(result.outcome.repairs.coverage.count == 1, "two images with 1 track each instead of 2 + 1")
        #expect(result.sourceDigest == IPodDevice.digest(of: try Data(contentsOf: rig.paths.iTunesDB)),
                "the digest from the bytes written and read back from the medium")
        #expect(try rig.backups.list(deviceGUID: Self.guid).count == 2, "a backup before the artwork and before the database")
    }

    /// A device that finds artwork by the track: the firmware family of the nano 2G,
    /// on the fake's signed database (no firmware GUID, so no identity conflict).
    static func findingArtworkByTrack(_ device: IPodDevice) throws -> IPodDevice {
        var nano = device
        nano.firmwareDescription = try DeviceDescription.parse(xml: Data("""
            <?xml version="1.0" encoding="UTF-8"?>
            <plist version="1.0"><dict><key>FamilyID</key><integer>9</integer></dict></plist>
            """.utf8))
        return nano
    }

    /// Writes the library unchanged on `device` - the repairs run as on every write.
    static func writeUnchanged(_ rig: Rig, on device: IPodDevice) async throws -> WriteTransaction.Result {
        let database = try Data(contentsOf: rig.paths.iTunesDB)
        var transaction = WriteTransaction(library: try ITunesDBParser.parse(database), changes: ChangeSet(), device: device,
                                           capabilities: IPodCapabilities.resolve(format: device.format, model: device.model),
                                           environment: rig.environment, note: "test")
        transaction.expectedSourceDigest = IPodDevice.digest(of: database)
        return try await transaction.run()
    }

    @Test("an iPod that finds artwork by the track: every song of the album gets its own image record on any write")
    func coverPerTrackOnNano() async throws {
        let rig = try await Self.rig(Fixture(cover: true)); defer { rig.cleanUp() }
        let device = try Self.findingArtworkByTrack(try #require(rig.device))
        #expect(device.supportsWriting && device.findsArtworkByTrack)
        let aftermath = try rig.onDisk().tracks.filter { $0.album == "Aftermath" }
        let image = try #require(try rig.artworkOnDisk().images.first)
        #expect(aftermath.count > 1, "the fixture album must have more songs than the one owner")

        let result = try await Self.writeUnchanged(rig, on: device)

        let images = try rig.artworkOnDisk().images
        #expect(Set(images.map(\.trackPersistentID)) == Set(aftermath.map(\.persistentID)))
        #expect(images.allSatisfy { $0.variants.map(\.byteOffset) == image.variants.map(\.byteOffset) }, "the same slices")
        #expect(result.outcome.repairs.trackImages == ["Aftermath": aftermath.count - 1])
        #expect(result.outcome.repairs.problems.isEmpty)
        #expect(try rig.backups.list(deviceGUID: Self.guid).contains { $0.reason == .beforeArtworkRepair },
                "a backup before ArtworkDB changes")

        let again = try await Self.writeUnchanged(rig, on: device)
        #expect(again.outcome.repairs.trackImages.isEmpty, "the second write has nothing to add")
        #expect(try rig.artworkOnDisk().images.count == images.count)
    }

    /// The nano 2G shows a track's artwork only through `mhit` 0x160. The step that gives
    /// every song its own record runs before the pointer repair, so the pointer repair
    /// must accept an album whose records are all the same picture.
    @Test("an album's first artwork on an iPod that finds artwork by the track: every song points to an image")
    func firstCoverOnNanoIsPointedTo() async throws {
        let rig = try await Self.rig(Fixture(cover: true)); defer { rig.cleanUp() }
        let device = try Self.findingArtworkByTrack(try #require(rig.device))
        let database = try Data(contentsOf: rig.paths.iTunesDB)
        let library = try ITunesDBParser.parse(database)
        let bends = library.tracks.filter { $0.album == "The Bends" }
        #expect(bends.count > 1, "the fixture album must have more songs than the one owner")
        var changes = ChangeSet()
        changes.queueFirstCover(try Self.image(red: 0, green: 0, blue: 1), sourceByteCount: 7000, owner: bends[0],
                                tracks: bends, album: "The Bends", for: AlbumID(albumOf: bends))
        var transaction = WriteTransaction(library: library, changes: changes, device: device,
                                           capabilities: IPodCapabilities.resolve(format: device.format, model: device.model),
                                           environment: rig.environment, note: "test")
        transaction.expectedSourceDigest = IPodDevice.digest(of: database)

        let result = try await transaction.run()

        #expect(result.outcome.coversWritten == 1)
        let images = try rig.artworkOnDisk().images
        let written = try rig.onDisk().tracks.filter { $0.album == "The Bends" }
        #expect(Set(images.map(\.trackPersistentID)).isSuperset(of: written.map(\.persistentID)),
                "every song of the album has its own image record")
        let identifiers = Set(images.map(\.id))
        #expect(written.allSatisfy { identifiers.contains($0.artworkIdentifier) },
                "every song points (0x160) to an existing image record")
        #expect(Set(written.map(\.artworkIdentifier)).count == 1, "all songs point to the same picture")
    }

    @Test("the iPod classic keeps one image record per album")
    func coverPerAlbumOnClassic() async throws {
        let rig = try await Self.rig(Fixture(cover: true)); defer { rig.cleanUp() }
        let device = try #require(rig.device)
        #expect(!device.findsArtworkByTrack)

        let result = try await Self.writeUnchanged(rig, on: device)

        #expect(result.outcome.repairs.trackImages.isEmpty)
        #expect(try rig.artworkOnDisk().images.count == 1)
    }

}
