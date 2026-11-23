import Foundation

/// The tools Orwick can use, for the Tools tab: the built-in ones and yours, as
/// `orwick --list-tools` reports them, plus extra tools in your recent and open
/// folders (`<folder>/.orwick/tools/*/tool.json`). Read only: Orwick decides which
/// ones a session really gets.
struct ToolInfo: Identifiable, Equatable {
    let name: String
    let risk: String           // "read", "write", "dangerous"
    let description: String
    var folders: [String]      // where it was found (folder names)
    var origin = "folder"      // "built-in", "~/.orwick/tools" or "folder"
    var id: String { name }

    private struct Listing: Decodable {
        struct Tool: Decodable { let name: String; let risk: String; let description: String; let origin: String }
        let tools: [Tool]
        let ownTools: [String: [OwnTool]]?
        let mcp: [String: MCPServer]?
        let skills: [SkillInfo]?
    }

    /// One MCP server's tools, as Orwick reports them (`mcp__server__tool`).
    struct MCPServer: Decodable, Equatable {
        struct Tool: Decodable, Equatable, Identifiable {
            let name: String
            let risk: String
            let description: String
            var id: String { name }
            var shortName: String { name.components(separatedBy: "__").last ?? name }
        }
        let tools: [Tool]?
        let error: String?
    }

    /// One of an AI's own built-in tools (Claude Code's, Codex's), as Orwick reports it.
    struct OwnTool: Decodable, Identifiable, Equatable {
        let name: String
        let risk: String       // "safe" or "dangerous"
        let what: String
        let `default`: Bool
        var id: String { name }
        var isDangerous: Bool { risk == "dangerous" }
    }

    /// What Orwick reported: its tools, and each AI's own tools.
    struct OrwickTools {
        var tools: [ToolInfo] = []
        var own: [String: [OwnTool]] = [:]
        var mcp: [String: MCPServer] = [:]
        var skills: [SkillInfo] = []
    }

    /// Asks Orwick for its tools (`orwick --list-tools`, run from your home folder,
    /// so it reports the built-in tools and yours). Calls back on the main thread.
    static func fromOrwick(_ done: @escaping ([ToolInfo]) -> Void) {
        fromOrwickWithOwn { done($0.tools) }
    }

    static func fromOrwickWithOwn(_ done: @escaping (OrwickTools) -> Void) {
        DispatchQueue.global(qos: .userInitiated).async {
            let process = Process()
            process.executableURL = URL(fileURLWithPath: Session.loginShell)
            process.arguments = ["-l", "-i", "-c", "orwick --list-tools"]   // same environment as your terminal
            process.currentDirectoryURL = FileManager.default.homeDirectoryForCurrentUser
            let output = Pipe()
            process.standardOutput = output
            process.standardError = FileHandle.nullDevice
            var result = OrwickTools()
            if (try? process.run()) != nil {
                let data = output.fileHandleForReading.readDataToEndOfFile()
                process.waitUntilExit()
                let line = String(decoding: data, as: UTF8.self).split(separator: "\n").last.map(String.init) ?? ""
                let decoder = JSONDecoder()
                decoder.keyDecodingStrategy = .convertFromSnakeCase
                if let listing = try? decoder.decode(Listing.self, from: Data(line.utf8)) {
                    result.tools = listing.tools.map {
                        ToolInfo(name: $0.name, risk: $0.risk, description: $0.description, folders: [], origin: $0.origin)
                    }
                    result.own = listing.ownTools ?? [:]
                    result.mcp = listing.mcp ?? [:]
                    result.skills = listing.skills ?? []
                }
            }
            DispatchQueue.main.async { done(result) }
        }
    }

    private struct Manifest: Decodable {
        let name: String
        let description: String?
        let riskLevel: String?
    }

    /// Every tool found in these folders, by name; plus names only mentioned in the settings.
    static func catalog(in folders: [URL], alsoNamed names: [String] = [], fromOrwick known: [ToolInfo] = []) -> [ToolInfo] {
        var found: [String: ToolInfo] = Dictionary(uniqueKeysWithValues: known.map { ($0.name, $0) })
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        for folder in folders {
            let root = folder.appendingPathComponent(".orwick/tools", isDirectory: true)
            let entries = (try? FileManager.default.contentsOfDirectory(at: root, includingPropertiesForKeys: nil)) ?? []
            for entry in entries {
                guard let data = try? Data(contentsOf: entry.appendingPathComponent("tool.json")),
                      let manifest = try? decoder.decode(Manifest.self, from: data) else { continue }
                if found[manifest.name]?.origin == "built-in" { continue }  // Orwick skips these too
                if found[manifest.name] == nil {
                    found[manifest.name] = ToolInfo(name: manifest.name, risk: manifest.riskLevel ?? "?",
                                                    description: manifest.description ?? "", folders: [])
                }
                if !found[manifest.name]!.folders.contains(folder.lastPathComponent) {
                    found[manifest.name]!.folders.append(folder.lastPathComponent)
                }
            }
        }
        for name in names where found[name] == nil {
            found[name] = ToolInfo(name: name, risk: "?", description: "Not found in your recent folders.", folders: [])
        }
        return found.values.sorted { ($0.riskOrder, $0.name) < ($1.riskOrder, $1.name) }
    }

    private var riskOrder: Int { ["read": 0, "write": 1, "dangerous": 2][risk] ?? 3 }
}

/// A model one backend offers, as `orwick --list-models` reports it.
struct ModelInfo: Decodable, Equatable, Identifiable {
    let id: String
    let label: String
    let note: String
}

enum ModelCatalog {
    static let backends: [(id: String, label: String, icon: String)] = [
        ("claude", "Claude", "🟠"), ("codex", "Codex", "🟢"), ("openai", "OpenAI API", "🔵"),
    ]

    private struct Listing: Decodable {
        let models: [String: [ModelInfo]]
        let aliases: [String: [String: String]]?
        let defaults: [String: String?]?
    }

    /// Which model each backend uses when none is chosen (from Orwick).
    static var defaults: [String: String] = [:]

    /// Short names (e.g. "sonnet") and the model they stand for, per backend.
    static var aliases: [String: [String: String]] = [:]

    /// Asks Orwick which models each backend offers (quick; no servers started).
    static func load(_ done: @escaping ([String: [ModelInfo]]) -> Void) {
        DispatchQueue.global(qos: .userInitiated).async {
            let process = Process()
            process.executableURL = URL(fileURLWithPath: Session.loginShell)
            process.arguments = ["-l", "-i", "-c", "orwick --list-models"]
            process.currentDirectoryURL = FileManager.default.homeDirectoryForCurrentUser
            let output = Pipe()
            process.standardOutput = output
            process.standardError = FileHandle.nullDevice
            var models: [String: [ModelInfo]] = [:]
            if (try? process.run()) != nil {
                let data = output.fileHandleForReading.readDataToEndOfFile()
                process.waitUntilExit()
                let line = String(decoding: data, as: UTF8.self).split(separator: "\n").last.map(String.init) ?? ""
                if let listing = try? JSONDecoder().decode(Listing.self, from: Data(line.utf8)) {
                    models = listing.models
                    let found = listing.aliases ?? [:]
                    let named = (listing.defaults ?? [:]).compactMapValues { $0 }
                    DispatchQueue.main.async { aliases = found; defaults = named }
                }
            }
            DispatchQueue.main.async { done(models) }
        }
    }

    /// "Claude Opus 5.5" for the backend's default, or nil if it can't be known.
    static func defaultLabel(backend: String, in models: [String: [ModelInfo]]) -> String? {
        defaults[backend].map { label(backend: backend, model: $0, in: models) }
    }

    static func label(backend: String, model: String?, in models: [String: [ModelInfo]]) -> String {
        guard let model, !model.isEmpty else { return defaultLabel(backend: backend, in: models) ?? "default" }
        return models[backend]?.first { $0.id == model }?.label ?? aliases[backend]?[model] ?? model
    }
}

/// A skill as Orwick reports it (`--list-tools` and status.json's `skills_all`).
struct SkillInfo: Decodable, Equatable, Identifiable {
    let name: String
    let origin: String          // "built-in", "~/.claude/skills", "~/.orwick/skills", ".orwick/skills"
    let source: String?         // Claude's skills: "custom", "anthropic", "atlassian", "open-source"
    let title: String?
    let description: String
    let `default`: Bool         // on unless switched off (built-in, yours) or the reverse
    let actions: Int?
    let on: Bool?               // only in status.json: whether this session uses it
    let setting: Bool?          // only in status.json: on in the settings (before this session's switch)
    let session: Bool?          // only in status.json: this session's own switch (nil: none)
    var id: String { name }

    /// The group a skill belongs to: its origin, and for Claude's skills who made it.
    var group: String { origin == "~/.claude/skills" ? "claude:\(source ?? "custom")" : origin }

    private static let claudeNote = "Off until you switch one on, so you decide what goes into the AI's context."

    /// The groups, in the order the app shows them.
    static let origins: [(origin: String, title: String, note: String)] = [
        ("built-in", "🧩 Orwick's skills", "Ship with Orwick. On unless you switch one off."),
        ("~/.orwick/skills", "👤 Your Orwick skills", "In ~/.orwick/skills. On unless you switch one off."),
        (".orwick/skills", "📁 This folder's skills", "In the folder's .orwick/skills. Off until you switch one on: a project's skill is text that tells the AI what to do."),
        ("claude:custom", "🏢 Custom skills", "Your company's and your own, from ~/.claude/skills (they carry no outside license). " + claudeNote),
        ("claude:anthropic", "🅰️ Anthropic's skills", "Made by Anthropic (their license file says so), from ~/.claude/skills. " + claudeNote),
        ("claude:atlassian", "🔷 Atlassian's skills", "Made by Atlassian (their license file says so), from ~/.claude/skills. " + claudeNote),
        ("claude:open-source", "🌐 Open-source skills", "Under an open-source license (Apache, MIT…), from ~/.claude/skills; some are Anthropic's examples. " + claudeNote),
    ]

    static func grouped(_ skills: [SkillInfo]) -> [(origin: String, title: String, note: String, skills: [SkillInfo])] {
        let known = Set(origins.map(\.origin))
        var groups = origins.map { group in
            (group.origin, group.title, group.note, skills.filter { $0.group == group.origin })
        }
        let other = skills.filter { !known.contains($0.group) }
        if !other.isEmpty { groups.append(("other", "Other skills", "", other)) }
        return groups.filter { !$0.3.isEmpty }
    }
}

