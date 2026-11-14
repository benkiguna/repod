import Foundation
import CoreGraphics
import ImageIO
import ITDBKit

/// A track waiting to be added. The file is still on the Mac - it reaches the iPod
/// only on "Write to iPod", together with the rest of the changes.
public struct PendingAddition: Identifiable, Sendable {
    public let id: UUID
    public var draft: TrackDraft
    public let source: URL
    /// The source format verdict. Conversion, if needed, happens during the write -
    /// the original stays untouched on the Mac, so there is nothing to ask about.
    public let verdict: AudioCompatibility.Verdict
    public let formatLabel: String
    /// Where to get artwork for this track's album. Used only when
    /// the album is new - we do not replace an existing album's artwork when adding
    /// a song, because nobody asked for that.
    public let cover: AudioImport.CoverSource?
    /// A place in a specific album (adding from the album view). Without it the track
    /// goes where its tags point.
    public var placement: AlbumPlacement?
    /// Playlists the song goes into once it is on the iPod, in the same write.
    public var playlists: [PlaylistTarget] = []

    public init(id: UUID, draft: TrackDraft, source: URL, verdict: AudioCompatibility.Verdict,
                formatLabel: String, cover: AudioImport.CoverSource?) {
        self.id = id
        self.draft = draft
        self.source = source
        self.verdict = verdict
        self.formatLabel = formatLabel
        self.cover = cover
    }

    public init(_ candidate: AudioImport.Candidate) {
        self.init(id: candidate.id, draft: candidate.draft, source: candidate.url,
                  verdict: candidate.verdict, formatLabel: candidate.formatLabel,
                  cover: candidate.coverSource)
    }

    public var needsConversion: Bool { !verdict.isSupported }
    public var target: AudioCompatibility.Target? {
        if case .convertible(_, let target) = verdict { return target }
        return nil
    }
    /// A file we can neither play nor transcode.
    public var isHopeless: Bool {
        if case .rejected = verdict { return true }
        return false
    }
}

/// A playlist a song being added goes into: one on the iPod (its id as `dump` writes it), or a new one, made once
/// for all the songs that name it. Written as "6d10bd68cd10bd68" or "new:Road trip".
public enum PlaylistTarget: Sendable, Hashable, CustomStringConvertible {
    case existing(UInt64)
    case new(String)

    public init?(_ text: String) {
        if text.hasPrefix("new:") {
            let name = String(text.dropFirst(4)).trimmingCharacters(in: .whitespacesAndNewlines)
            guard !name.isEmpty else { return nil }
            self = .new(name)
        } else if let id = UInt64(text, radix: 16) {
            self = .existing(id)
        } else {
            return nil
        }
    }

    public var description: String {
        switch self {
        case .existing(let id): String(format: "%016llx", id)
        case .new(let name): "new:\(name)"
        }
    }
}
