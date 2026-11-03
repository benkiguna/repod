import Foundation
import ITDBKit

/// Songs the iPod's own library sync rebuilds from its files (`OnDeviceSync`). Removing one
/// would not last - its file stays in `/Music` and the entry comes back at the next boot - and
/// its cover comes from the sync's cache, not from ArtworkDB. Both are refused here, before
/// anything is queued; titles and details can still be edited.
public enum SyncedTracksRefusal: Error, Equatable, CustomStringConvertible {
    case removal(songs: Int)
    case artwork(album: String)

    public var description: String {
        switch self {
        case .removal(let songs): "\(songs) song(s) owned by the on-device sync: removal refused"
        case .artwork(let album): "\(album) owned by the on-device sync: cover change refused"
        }
    }
}

extension LibraryEditor {

    func refuseRemoval(of tracks: [ITTrack]) throws {
        guard onDeviceSync != nil else { return }
        let owned = tracks.filter(OnDeviceSync.manages).count
        if owned > 0 { throw SyncedTracksRefusal.removal(songs: owned) }
    }

    func refuseArtwork(for tracks: [ITTrack], album: String) throws {
        guard onDeviceSync != nil, tracks.contains(where: OnDeviceSync.manages) else { return }
        throw SyncedTracksRefusal.artwork(album: album)
    }
}
