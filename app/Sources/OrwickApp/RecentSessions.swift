import Foundation

/// A saved Orwick session, from `<folder>/.orwick/sessions/<id>/meta.json`.
/// Orwick writes these itself (in any terminal); the app only lists them.
struct RecentSession: Identifiable, Equatable {
    let id: String
    let folder: URL
    let title: String
    let updatedAt: Date
    let messages: Int
    var feature: String? = nil

    var key: String { "\(folder.path)#\(id)" }
    var sessionFolder: URL { folder.appendingPathComponent(".orwick/sessions/\(id)", isDirectory: true) }
    var hasDocs: Bool { SessionDocs.hasDocs(sessionFolder) }

    private struct Meta: Decodable {
        let title: String?
        let updatedAt: Double?
        let messages: Int?
        let feature: String?
    }

    /// Saved sessions with at least one message in this folder.
    static func saved(in folder: URL) -> [RecentSession] {
        let root = folder.appendingPathComponent(".orwick/sessions", isDirectory: true)
        let entries = (try? FileManager.default.contentsOfDirectory(at: root, includingPropertiesForKeys: nil)) ?? []
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return entries.compactMap { entry in
            guard Session.isSessionID(entry.lastPathComponent),
                  let data = try? Data(contentsOf: entry.appendingPathComponent("meta.json")),
                  let meta = try? decoder.decode(Meta.self, from: data),
                  let messages = meta.messages, messages > 0 else { return nil }
            return RecentSession(id: entry.lastPathComponent, folder: folder,
                                 title: SessionName.read(entry)
                                     ?? (meta.title?.isEmpty == false ? meta.title! : "(no title)"),
                                 updatedAt: Date(timeIntervalSince1970: meta.updatedAt ?? 0),
                                 messages: messages, feature: meta.feature)
        }
    }
}
