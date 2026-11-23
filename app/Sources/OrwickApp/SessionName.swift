import Foundation

/// The name you give a session, in `<session folder>/name.txt` (Orwick's `/name` writes the
/// same file). Kept apart from meta.json, which Orwick rewrites after every message.
enum SessionName {
    static let fileName = "name.txt"
    static let maxLength = 80

    /// The saved name, or nil when the session has none.
    static func read(_ sessionFolder: URL) -> String? {
        guard let text = try? String(contentsOf: sessionFolder.appendingPathComponent(fileName), encoding: .utf8)
        else { return nil }
        let name = clean(text)
        return name.isEmpty ? nil : name
    }

    /// One line, single spaces, at most `maxLength` characters (the same rules as Orwick).
    static func clean(_ text: String) -> String {
        String(text.split(whereSeparator: \.isWhitespace).joined(separator: " ").prefix(maxLength))
    }

    /// Saves the name; an empty one removes the file. Returns the name as saved (nil: none).
    @discardableResult
    static func write(_ name: String, to sessionFolder: URL) throws -> String? {
        let cleaned = clean(name)
        let file = sessionFolder.appendingPathComponent(fileName)
        if cleaned.isEmpty {
            if FileManager.default.fileExists(atPath: file.path) { try FileManager.default.removeItem(at: file) }
            return nil
        }
        try FileManager.default.createDirectory(at: sessionFolder, withIntermediateDirectories: true)
        try (cleaned + "\n").write(to: file, atomically: true, encoding: .utf8)
        return cleaned
    }
}
