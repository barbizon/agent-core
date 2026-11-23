import Foundation

/// Orwick's settings as the Settings screen edits them: the main file
/// `~/.orwick/config.json`, used by every session.
///
/// This mirrors Orwick's own rules (src/agent_core/config.py): the same keys, the
/// same checks, and `null` meaning "not set here" (then Orwick's built-in default
/// decides). The main file may not set `workspace`. A folder's own
/// `.orwick/config.json` and `.orwick/.env` beat the main file; the app only reads
/// which keys a folder file sets, and never reads `.env`, because it holds secrets.
struct OrwickConfig: Equatable {
    var backend = ""            // "" = not set
    var model = ""
    var maxSteps = ""
    var requestTimeoutSeconds = ""
    var maxToolResultChars = ""
    var auditLog = ""
    var codeRoot = ""          // the folder of all repos, read by service maps; "" = found
    var workspace = ""
    var historyBudgetTokens = ""
    var historyWarnPercent = ""
    var tools: [String: Bool] = [:]   // switched on/off by name; not listed = on
    var skills: [String: Bool] = [:]  // switched on/off by name; not listed = the skill's default
    /// Each AI's own built-in tools to switch on; nil = Orwick's safe defaults, [] = all off.
    var claudeTools: [String]? = nil
    var codexTools: [String]? = nil
    /// `mcpServers`, kept exactly as written (the app doesn't edit server definitions).
    var mcpServersJSON: String? = nil
    var debug: Bool? = nil      // nil only when the file doesn't say; shown and saved as off
    var trace: Bool? = nil      // tool title lines in the chat; nil = on (Orwick's default)

    /// Keys in the order Orwick lists them; Orwick refuses any other key.
    static let keys = ["backend", "model", "max_steps", "request_timeout_seconds",
                       "max_tool_result_chars", "audit_log", "workspace", "code_root", "debug", "history_budget_tokens",
                       "history_warn_percent", "tools", "skills",
                       "claude_tools", "codex_tools", "mcpServers", "trace"]
    static let backends = ["codex", "claude", "openai"]

    /// The main settings file, used by every session.
    static let mainFile = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent(".orwick/config.json")

    /// The settings a folder's own `.orwick/config.json` sets (not null), which beat the main file.
    static func folderOverrides(in folder: URL) -> [String] {
        let url = folder.appendingPathComponent(".orwick/config.json")
        guard let data = try? Data(contentsOf: url),
              let values = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else { return [] }
        return keys.filter { key in values[key].map { !($0 is NSNull) } ?? false }
    }

    /// Orwick's built-in defaults, shown as hints only.
    enum Default {
        static let backend = "codex"
        static let maxSteps = "25"
        static let requestTimeoutSeconds = "120"
        static let maxToolResultChars = "20000"
        static let historyBudgetTokens = "100000"
        static let historyWarnPercent = "70"
        static let auditLog = "each session's own folder"
    }

    // MARK: Checks (the same as Orwick's)

    var problems: [String: String] {
        var found: [String: String] = [:]
        if !backend.isEmpty && !Self.backends.contains(backend) {
            found["backend"] = "Must be one of: \(Self.backends.joined(separator: ", "))"
        }
        if let value = trimmed(maxSteps), (Int(value) ?? -1) < 0 {
            found["max_steps"] = "A whole number: 0 for no limit, or more"
        }
        // Every limit: 0 means no limit (the same rules as Orwick's).
        if let value = trimmed(requestTimeoutSeconds), (Double(value) ?? -1) < 0 {
            found["request_timeout_seconds"] = "Seconds: 0 for no limit, or more"
        }
        if let value = trimmed(maxToolResultChars), !Self.zeroOrAtLeast(value, 100) {
            found["max_tool_result_chars"] = "0 for no limit, or at least 100"
        }
        if let value = trimmed(historyBudgetTokens), !Self.zeroOrAtLeast(value, 8_000) {
            found["history_budget_tokens"] = "0 for no limit, or at least 8000"
        }
        if let value = trimmed(historyWarnPercent), !(0...100).contains(Int(value) ?? -1) {
            found["history_warn_percent"] = "0 (never warn) to 100"
        }
        return found
    }

    private static func zeroOrAtLeast(_ text: String, _ minimum: Int) -> Bool {
        guard let value = Int(text) else { return false }
        return value == 0 || value >= minimum
    }

    // MARK: Reading

    static func load(from url: URL = mainFile) throws -> OrwickConfig {
        guard FileManager.default.fileExists(atPath: url.path) else { return OrwickConfig() }
        let data = try Data(contentsOf: url)
        guard let object = try? JSONSerialization.jsonObject(with: data),
              let values = object as? [String: Any] else {
            throw ConfigFileError.invalid("It isn't valid JSON with an object {…} at the top.")
        }
        let unknown = Set(values.keys).subtracting(keys).sorted()
        guard unknown.isEmpty else {
            throw ConfigFileError.invalid("It has setting(s) Orwick doesn't know: \(unknown.joined(separator: ", ")).")
        }
        if let workspace = values["workspace"], !(workspace is NSNull) {
            throw ConfigFileError.invalid("It sets workspace, which only a folder's own .orwick/config.json may set. Orwick refuses to start with it.")
        }
        var config = OrwickConfig()
        config.backend = text(values["backend"])
        config.model = text(values["model"])
        config.maxSteps = text(values["max_steps"])
        config.requestTimeoutSeconds = text(values["request_timeout_seconds"])
        config.maxToolResultChars = text(values["max_tool_result_chars"])
        config.auditLog = text(values["audit_log"])
        config.codeRoot = text(values["code_root"])
        config.historyBudgetTokens = text(values["history_budget_tokens"])
        config.historyWarnPercent = text(values["history_warn_percent"])
        if let flag = values["trace"] as? NSNumber, CFGetTypeID(flag) == CFBooleanGetTypeID() {
            config.trace = flag.boolValue
        }
        if let servers = values["mcpServers"] as? [String: Any], !servers.isEmpty,
           let data = try? JSONSerialization.data(withJSONObject: servers, options: [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]) {
            config.mcpServersJSON = String(decoding: data, as: UTF8.self)
                .split(separator: "\n").joined(separator: "\n  ")   // indented like the rest of the file
        }
        for (key, assign) in [("claude_tools", { (v: [String]) in config.claudeTools = v }),
                              ("codex_tools", { (v: [String]) in config.codexTools = v })] {
            guard let raw = values[key], !(raw is NSNull) else { continue }
            guard let list = raw as? [String] else {
                throw ConfigFileError.invalid("\(key) must be a list of tool names, [] for none, or null.")
            }
            assign(list)
        }
        if let raw = values["skills"], !(raw is NSNull) {
            guard let map = raw as? [String: Any] else {
                throw ConfigFileError.invalid("skills must be an object like {\"feature-docs\": true}.")
            }
            for (name, value) in map {
                guard let flag = value as? NSNumber, CFGetTypeID(flag) == CFBooleanGetTypeID() else {
                    throw ConfigFileError.invalid("skills.\(name) must be true or false.")
                }
                config.skills[name] = flag.boolValue
            }
        }
        if let raw = values["tools"], !(raw is NSNull) {
            guard let map = raw as? [String: Any] else {
                throw ConfigFileError.invalid("tools must be an object like {\"run_command\": false}.")
            }
            for (name, value) in map {
                guard let flag = value as? NSNumber, CFGetTypeID(flag) == CFBooleanGetTypeID() else {
                    throw ConfigFileError.invalid("tools.\(name) must be true or false.")
                }
                config.tools[name] = flag.boolValue
            }
        }
        config.workspace = text(values["workspace"])
        if let number = values["debug"] as? NSNumber, CFGetTypeID(number) == CFBooleanGetTypeID() {
            config.debug = number.boolValue
        } else if let raw = values["debug"] as? String {
            config.debug = ["1", "true", "yes", "on"].contains(raw.lowercased())
        }
        return config
    }

    private static func text(_ value: Any?) -> String {
        switch value {
        case let string as String:
            return string
        case let number as NSNumber where CFGetTypeID(number) != CFBooleanGetTypeID():
            return number.doubleValue == number.doubleValue.rounded() && abs(number.doubleValue) < 1e15
                ? String(Int64(number.doubleValue)) : number.stringValue
        default:
            return ""
        }
    }

    // MARK: Writing

    /// Writes every key in Orwick's order (except workspace), `null` for the ones not set, atomically.
    func save(to url: URL = mainFile) throws {
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(),
                                                withIntermediateDirectories: true)
        let values: [(String, String)] = [
            ("backend", string(backend)),
            ("model", string(model)),
            ("max_steps", number(maxSteps)),
            ("request_timeout_seconds", number(requestTimeoutSeconds)),
            ("max_tool_result_chars", number(maxToolResultChars)),
            ("audit_log", string(auditLog)),
            ("code_root", string(codeRoot)),
            ("debug", (debug ?? false) ? "true" : "false"),  // on or off, never "not set"
            ("trace", (trace ?? true) ? "true" : "false"),
            ("history_budget_tokens", number(historyBudgetTokens)),
            ("history_warn_percent", number(historyWarnPercent)),
            ("tools", toolsJSON),
            ("skills", Self.flagsJSON(skills)),
            ("claude_tools", listJSON(claudeTools)),
            ("codex_tools", listJSON(codexTools)),
            ("mcpServers", mcpServersJSON ?? "{}"),
        ]
        let body = values.map { "  \"\($0.0)\": \($0.1)" }.joined(separator: ",\n")
        try Data("{\n\(body)\n}\n".utf8).write(to: url, options: .atomic)
    }

    private func listJSON(_ list: [String]?) -> String {
        guard let list else { return "null" }
        return "[" + list.map { "\"\($0)\"" }.joined(separator: ", ") + "]"
    }

    private static func flagsJSON(_ flags: [String: Bool]) -> String {
        "{" + flags.keys.sorted().map { "\"\($0)\": \(flags[$0]! ? "true" : "false")" }.joined(separator: ", ") + "}"
    }

    /// `{"run_command": false}`, names sorted, so the file stays tidy.
    private var toolsJSON: String {
        let items = tools.keys.sorted().map { "\"\($0)\": \(tools[$0]! ? "true" : "false")" }
        return "{" + items.joined(separator: ", ") + "}"
    }

    private func trimmed(_ value: String) -> String? {
        let result = value.trimmingCharacters(in: .whitespacesAndNewlines)
        return result.isEmpty ? nil : result
    }

    private func string(_ value: String) -> String {
        guard let value = trimmed(value) else { return "null" }
        let encoder = JSONEncoder()
        encoder.outputFormatting = .withoutEscapingSlashes
        return (try? encoder.encode(value)).flatMap { String(data: $0, encoding: .utf8) } ?? "null"
    }

    private func number(_ value: String) -> String {
        guard let value = trimmed(value), let parsed = Double(value) else { return "null" }
        return parsed == parsed.rounded() ? String(Int64(parsed)) : String(parsed)
    }
}

enum ConfigFileError: LocalizedError {
    case invalid(String)

    var errorDescription: String? {
        if case .invalid(let reason) = self { return reason }
        return nil
    }
}
