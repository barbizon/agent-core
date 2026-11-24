import Foundation

/// What Orwick writes to `.orwick/sessions/<id>/status.json` (Orwick's
/// src/agent_core/status_file.py): counts, tools and token usage for one run.
/// Never the conversation.
struct OrwickStatus: Decodable, Equatable {
    struct Tool: Decodable, Equatable, Identifiable {
        let name: String
        let risk: String          // "read", "write" or "dangerous"
        let description: String
        let uses: Int
        let enabled: Bool?        // what this session uses (older Orwick: missing)
        let `default`: Bool?      // the settings' tool list
        let session: Bool?        // this session's own switch, nil when it follows the settings
        let origin: String?       // "built-in", "~/.orwick/tools" or ".orwick/tools"
        var id: String { name }
        var isOn: Bool { enabled ?? true }
        var settingsValue: Bool { self.default ?? true }
    }

    struct Counts: Decodable, Equatable {
        let startedAt: Double
        let messages: Int
        let steps: Int
        let toolCalls: Int
        let toolsOk: Int
        let toolsFailed: Int
        let approvalsAsked: Int
        let approvalsYes: Int
        let approvalsNo: Int
        let historyMessages: Int
        let historyChars: Int
    }

    struct Limit: Decodable, Equatable, Identifiable {
        let name: String
        let usedPercent: Double
        let resetsAt: Double?
        let status: String?
        var id: String { name }

        enum CodingKeys: String, CodingKey { case name, usedPercent, resetsAt, status }

        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            name = try c.decode(String.self, forKey: .name)
            usedPercent = try c.decode(Double.self, forKey: .usedPercent)
            resetsAt = try? c.decodeIfPresent(Double.self, forKey: .resetsAt)
            status = try? c.decodeIfPresent(String.self, forKey: .status)
        }
    }

    struct Usage: Decodable, Equatable {
        let requests: Int
        let seconds: Double
        let inputTokens: Int
        let cachedTokens: Int
        let cacheWriteTokens: Int?   // older Orwick versions don't report it
        let outputTokens: Int
        let costUsd: Double?
        let model: String?
        let contextTokens: Int?
        let contextWindow: Int?
        let limits: [Limit]
        let limitsNote: String
        let limitsSeenAt: Double?

        /// Input that wasn't a cache re-read: what the requests really added.
        var newInputTokens: Int { max(inputTokens - cachedTokens, 0) }
    }

    /// How full the conversation is against its budget (Orwick's compaction.py).
    struct Conversation: Decodable, Equatable {
        let tokens: Int
        let budget: Int
        let percent: Int
        let warnPercent: Int?
        let trimPercent: Int
        let summarizePercent: Int

        var warn: Int { warnPercent ?? 50 }
        var isWarning: Bool { percent >= warn }
        var isSummarizing: Bool { percent >= summarizePercent }
    }

    /// A skill's UI action (its ui.json), as Orwick reports it: a button that types `command`.
    struct Action: Decodable, Equatable, Identifiable {
        let skill: String
        let id: String
        let label: String
        let icon: String
        let command: String
        let inputs: [String]
        let hint: String?        // what the button does, in plain words (older Orwick: missing)
        let on: Bool?            // whether its skill is on (older Orwick lists only those: nil)
        var key: String { "\(skill)/\(id)" }
    }

    let format: Int
    let updatedAt: Double
    let state: String?           // "waiting" (at the prompt) or "working" (answering)
    let activity: String?        // while working: what Orwick does now, e.g. "Read Jira issue PROJ-1"
    let calls: [Call]?           // this session's tool calls, oldest first (older Orwick: missing)

    struct Call: Decodable, Equatable {
        let title: String        // e.g. "Read Jira issue PROJ-1"
        let tool: String
        let ok: Bool?            // nil while it runs
        let at: Double
    }
    let actions: [Action]?
    let actionGroups: [ActionGroup]?   // one per skill with buttons (older Orwick: missing)

    struct ActionGroup: Decodable, Equatable {
        let skill: String
        let title: String
        let hint: String?
    }
    var isWaiting: Bool { state != "working" }
    let conversation: Conversation?
    let sessionId: String?
    let continuedIn: String?     // set when /clear started a new session in the same folder
    let workspace: String
    let backend: String
    let model: String
    let modelChoice: String?     // the chosen model name; nil = the backend's default
    let debug: Bool
    let tools: [Tool]
    /// The answering AI's own built-in tools that are switched on (they don't go through Orwick).
    let ownTools: [OwnTool]?
    struct OwnTool: Decodable, Equatable, Identifiable {
        let name: String
        let risk: String
        let what: String
        var id: String { name }
    }
    let skills: [String]?        // the skills that are on (older Orwick: null)
    let skillsAll: [SkillInfo]?  // every skill found, with on/off (older Orwick: null)
    let docs: [String]?          // this session's docs (names)
    let feature: String?         // the ticket the session is linked to
    let session: Counts
    let usage: [String: Usage]

    var current: Usage? { usage[backend] }

    static func read(from url: URL) -> OrwickStatus? {
        guard let data = try? Data(contentsOf: url) else { return nil }
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        guard let status = try? decoder.decode(OrwickStatus.self, from: data), status.format == 1 else { return nil }
        return status
    }
}

/// Short, friendly numbers: 9.8k, 1.2M, 3m 20s.
enum Format {
    static func tokens(_ value: Int) -> String {
        switch value {
        case ..<1_000: return "\(value)"
        case ..<1_000_000: return String(format: value < 10_000 ? "%.1fk" : "%.0fk", Double(value) / 1_000)
        default: return String(format: "%.1fM", Double(value) / 1_000_000)
        }
    }

    static func duration(_ seconds: Double) -> String {
        let s = Int(max(0, seconds))
        if s < 60 { return "\(s)s" }
        if s < 3600 { return "\(s / 60)m \(s % 60)s" }
        if s < 86400 { return "\(s / 3600)h \(s % 3600 / 60)m" }
        return "\(s / 86400)d \(s % 86400 / 3600)h"
    }

    static func resets(_ time: Double, now: Date = Date()) -> String {
        let date = Date(timeIntervalSince1970: time)
        let formatter = DateFormatter()
        formatter.dateFormat = Calendar.current.isDate(date, inSameDayAs: now) ? "HH:mm" : "EEE HH:mm"
        return "resets \(formatter.string(from: date)) (in \(duration(date.timeIntervalSince(now))))"
    }
}
