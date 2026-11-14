import Foundation
import ITDBKit

/// Added tracks: `TrackAdditionRunner` does the work (conversion, copying the
/// file, the entry, a new album's artwork), and this phase places tracks added
/// from the album view where they were dropped.
struct AdditionPhase: WritePhase {
    let additions: [PendingAddition]
    let progress: (@Sendable (String) -> Void)?

    /// Throws `deviceGone` when the same volume is no longer at the iPod's path -
    /// the copied files are then already in the ledger for rollback.
    func run(_ library: inout ITLibrary, on device: IPodDevice, ledger: inout WriteLedger) async throws {
        guard !additions.isEmpty else { return }
        let volume = device.volume
        let outcome = await TrackAdditionRunner.run(additions, into: library, paths: device.paths,
                                                    artworkFormats: device.firmwareDescription?.albumArt ?? [],
                                                    rules: .init(for: device),
                                                    ensureMounted: { try volume.ensureStillMounted() },
                                                    progress: progress)
        ledger.placed += outcome.copied
        if outcome.aborted { throw ITDBError.writeRefused(.deviceGone) }
        var problems = outcome.failed
        let placed = additions.filter { $0.placement != nil && outcome.done.contains($0.id) }
        library = Self.placing(placed, ids: outcome.uniqueIDs, in: outcome.library, problems: &problems)
        library = Self.filing(additions.filter { outcome.done.contains($0.id) }, ids: outcome.uniqueIDs, in: library, problems: &problems)
        ledger.completed.additions.formUnion(outcome.done)
        ledger.addedTracks += outcome.done.count
        ledger.additionProblems += problems
        ledger.coversFailed += outcome.coversFailed
        ledger.coversWritten += outcome.covers
        ledger.coverImages.merge(outcome.coverImages) { _, new in new }
        if outcome.covers > 0 { ledger.artworkChanged = true }
    }

    /// Added songs into the playlists chosen for them, in the order they were added. A new playlist is made once,
    /// with every song that names it. A playlist that cannot take them leaves the songs on the iPod, and says why.
    static func filing(_ added: [PendingAddition], ids: [UUID: UInt32], in library: ITLibrary, problems: inout [WriteProblem]) -> ITLibrary {
        var wanted: [(PlaylistTarget, [UInt32])] = []
        for addition in added {
            guard let id = ids[addition.id] else { continue }
            for target in addition.playlists {
                if let at = wanted.firstIndex(where: { $0.0 == target }) { wanted[at].1.append(id) } else { wanted.append((target, [id])) }
            }
        }
        var result = library
        for (target, songs) in wanted {
            do {
                switch target {
                case .new(let name): result = try result.creatingPlaylist(named: name, trackIDs: songs)
                case .existing(let id): result = try result.addingTracks(songs, toPlaylist: id)
                }
            } catch {
                problems.append(WriteProblem("playlist \(target)", .notPlaced(error)))
            }
        }
        return result
    }

    /// Tracks with a place in an album are moved to where they were dropped. The
    /// same permutation as when dragging tracks, so the iPod sees the order
    /// the same way. When that is not possible (e.g. two albums with this title), the track stays
    /// where adding put it, and the reason goes to the report.
    static func placing(_ placed: [PendingAddition], ids: [UUID: UInt32], in library: ITLibrary,
                        problems: inout [WriteProblem]) -> ITLibrary {
        var result = library
        for addition in placed.sorted(by: { ($0.placement?.index ?? 0) < ($1.placement?.index ?? 0) }) {
            guard let placement = addition.placement, let id = ids[addition.id] else { continue }
            do {
                var order = try result.trackOrder(album: placement.album).map(\.uniqueID)
                guard let from = order.firstIndex(of: id) else { continue }
                order.remove(at: from)
                order.insert(id, at: min(placement.index, order.count))
                result = try result.settingTrackOrder(album: placement.album, uniqueIDs: order)
            } catch {
                problems.append(WriteProblem(addition.draft.title, .notPlaced(error)))
            }
        }
        return result
    }
}
