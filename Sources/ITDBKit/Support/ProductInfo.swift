import Foundation

/// The product name in one place. It also determines the folder for backups
/// and the remembered order, so we never write it anywhere else.
public enum ProductInfo {
    public static let name = "RePod"

    /// Names the data folder went by earlier: it is moved from the first one found, so a rename never strands anyone's backups.
    public static let previousNames: [String] = []

    /// The app's data folder, moved from the old name if needed.
    public static func supportDirectory(
        named productName: String = name, previous: [String] = previousNames
    ) throws -> URL {
        let manager = FileManager.default
        let support = try manager.url(for: .applicationSupportDirectory, in: .userDomainMask,
                                      appropriateFor: nil, create: true)
        let current = support.appendingPathComponent(productName, isDirectory: true)
        guard !manager.fileExists(atPath: current.path) else { return current }

        for old in previous {
            let candidate = support.appendingPathComponent(old, isDirectory: true)
            guard manager.fileExists(atPath: candidate.path) else { continue }
            // Same volume, so the move is cheap and keeps the
            // hard links that incremental backups rely on.
            do { try manager.moveItem(at: candidate, to: current); return current }
            catch { continue }
        }
        return current
    }
}
