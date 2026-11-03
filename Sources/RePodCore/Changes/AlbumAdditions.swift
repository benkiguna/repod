import Foundation
import ITDBKit

/// Where in an album an added track should stand. The position counts on the album's
/// track list, as the app shows it.
public struct AlbumPlacement: Sendable, Equatable {
    public let album: String
    public let albumKey: AlbumKey
    public let index: Int
}
