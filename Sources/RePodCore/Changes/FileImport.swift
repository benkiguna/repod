import Foundation
import ITDBKit

/// A file chosen for an entry that has no file. It waits for `apply()` together
/// with the rest of the changes - until then nothing happens on the device.
///
/// This is not the same as adding a track (`PendingAddition`). There a new
/// `mhit` record is created; here the record already exists - it has its identifier, positions
/// in the tables, plays and artwork - and lacks only the file. That is why
/// it goes through `replacingFile`, which changes the path and parameters and does not touch
/// the rest of the record.
public struct PendingFileImport: Identifiable, Sendable {
    /// The `uniqueID` of the track whose file we restore.
    public let id: UInt32
    public let album: String
    public let title: String
    public let source: URL
    public let verdict: AudioCompatibility.Verdict
    public let formatLabel: String
    public let durationMilliseconds: UInt32
    public let bitRate: UInt32
    public let sampleRate: UInt32
    public let kind: TrackFileKind
    /// The file the track has on the iPod today - `nil` when restoring, where
    /// there simply is no file. When replacing, it goes away **only after** a successful
    /// database write, the same way as the original after a conversion: at no moment
    /// is there a state in which the entry points to an empty place.
    public var replacing: URL? = nil

    public var isReplacement: Bool { replacing != nil }

    public var target: AudioCompatibility.Target? {
        if case .convertible(_, let target) = verdict { return target }
        return nil
    }
    public var needsConversion: Bool { !verdict.isSupported }
}

/// A file that has been examined but not assigned yet. The interface shows its format
/// verdict and length from this, so it is clear whether it matched the same track before
/// anything goes into the queue.
public struct ImportCandidate: Identifiable, Sendable, Hashable {
    public let id = UUID()
    public let url: URL
    public let verdict: AudioCompatibility.Verdict
    public let formatLabel: String
    public let durationMilliseconds: UInt32
    /// The title from the file's tags; empty when the file has no tags.
    public let taggedTitle: String
    public let trackNumber: UInt32
    public let sizeBytes: UInt32
    public let bitRate: UInt32
    public let sampleRate: UInt32
    public let kind: TrackFileKind

    public var fileName: String { url.lastPathComponent }
    public var duration: TimeInterval { Double(durationMilliseconds) / 1000 }
    public var needsConversion: Bool { !verdict.isSupported }
    /// It will not play, and we cannot transcode it.
    public var isHopeless: Bool {
        if case .rejected = verdict { return true }
        return false
    }
    public var target: AudioCompatibility.Target? {
        if case .convertible(_, let target) = verdict { return target }
        return nil
    }
}

public enum FileImportError: Error, CustomStringConvertible {
    case unreadable(URL)
    case rejected(URL, reason: String)
    case notMissing(String)
    case sameFile(String)
    case currentFileUnknown(String)

    public var description: String {
        switch self {
        case .unreadable(let url): "unreadable audio file \(url.lastPathComponent)"
        case .rejected(let url, let reason): "\(url.lastPathComponent) rejected: \(reason)"
        case .notMissing(let title): "\(title) already has a file"
        case .sameFile(let title): "\(title) already uses this file"
        case .currentFileUnknown(let title): "current file of \(title) unknown (snapshot)"
        }
    }
}
