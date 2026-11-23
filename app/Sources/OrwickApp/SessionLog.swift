import Foundation

/// What happened in a session, read from the files Orwick keeps in its folder, without the AI:
/// `audit.jsonl` (every message, AI step and tool call, with times) and `conversation.json`
/// (the answers and each tool's result).
struct SessionLog: Equatable {
    struct Turn: Identifiable, Equatable {
        let id: String              // the run's id
        let number: Int
        var message: String
        var started: Date
        var finished: Date?
        var error: String?
        var answer: String?
        var steps: [Step] = []
        var calls: [Call] = []

        var running: Bool { finished == nil && error == nil }
        var failedCalls: Int { calls.filter { $0.ok == false }.count }
    }

    struct Step: Identifiable, Equatable {
        let id: String
        let number: Int
        var requested: Date
        var responded: Date?
        var tools: [String] = []
        /// When the next step started (or the message finished): the tools ran until then.
        var nextAt: Date?

        var thinking: Double? { responded.map { $0.timeIntervalSince(requested) } }
        var toolsTime: Double? {
            guard let responded, let nextAt else { return nil }
            return nextAt.timeIntervalSince(responded)
        }
    }

    struct Call: Identifiable, Equatable {
        let id: String
        let tool: String
        let arguments: String       // pretty JSON
        let summary: String         // the first argument, short
        let at: Date
        let step: Int
        var ok: Bool?               // nil: still running (or waiting for your approval)
        var error: String?
        var durationMs: Double?
        var approval: Approval?
        var result: String?

        enum Approval: Equatable { case asked, granted(after: Double), denied }
    }

    var turns: [Turn] = []

    var stepCount: Int { turns.reduce(0) { $0 + $1.steps.count } }
    var callCount: Int { turns.reduce(0) { $0 + $1.calls.count } }

    // MARK: Reading

    static func load(_ folder: URL) -> SessionLog {
        var log = SessionLog()
        guard let text = try? String(contentsOf: folder.appendingPathComponent("audit.jsonl"), encoding: .utf8) else {
            return log
        }
        var turnIndex: [String: Int] = [:]
        var callIndex: [String: (Int, Int)] = [:]
        var approvalAsked: [String: Date] = [:]

        for line in text.split(separator: "\n") {
            guard let event = (try? JSONSerialization.jsonObject(with: Data(line.utf8))) as? [String: Any],
                  let type = event["event_type"] as? String,
                  let run = event["run_id"] as? String else { continue }
            let details = event["details"] as? [String: Any] ?? [:]
            let at = date(event["timestamp"] as? String) ?? Date.distantPast
            if type == "agent.started" {
                turnIndex[run] = log.turns.count
                log.turns.append(Turn(id: run, number: log.turns.count + 1,
                                      message: details["message"] as? String ?? "", started: at))
                continue
            }
            guard let t = turnIndex[run] else { continue }
            switch type {
            case "agent.finished":
                log.turns[t].finished = at
            case "agent.failed":
                switch details["error_type"] as? String {
                case "KeyboardInterrupt": log.turns[t].error = "stopped by you (Ctrl+C)"
                case "SystemExit": log.turns[t].error = "stopped: Orwick was closed"
                default:
                    let message = details["error"] as? String ?? ""
                    log.turns[t].error = message.isEmpty ? (details["error_type"] as? String ?? "failed") : message
                }
                log.turns[t].finished = at
            case "model.requested":
                let number = details["step"] as? Int ?? log.turns[t].steps.count + 1
                if let last = log.turns[t].steps.indices.last { log.turns[t].steps[last].nextAt = at }
                log.turns[t].steps.append(Step(id: "\(run)-\(number)", number: number, requested: at))
            case "model.responded":
                if let last = log.turns[t].steps.indices.last {
                    log.turns[t].steps[last].responded = at
                    log.turns[t].steps[last].tools = details["tool_calls"] as? [String] ?? []
                }
            case "tool.requested":
                guard let id = event["call_id"] as? String else { break }
                let arguments = details["arguments"] as? [String: Any] ?? [:]
                callIndex[id] = (t, log.turns[t].calls.count)
                log.turns[t].calls.append(Call(
                    id: id, tool: details["tool"] as? String ?? "?",
                    arguments: pretty(arguments), summary: summary(arguments), at: at,
                    step: log.turns[t].steps.last?.number ?? 0))
            default:
                guard let id = event["call_id"] as? String, let (ti, ci) = callIndex[id] else { break }
                switch type {
                case "tool.execution_succeeded":
                    log.turns[ti].calls[ci].ok = true
                    log.turns[ti].calls[ci].durationMs = details["duration_ms"] as? Double
                case "tool.execution_failed", "tool.rejected", "tool.invalid_arguments", "tool.preview_failed",
                     "tool.authorization_failed", "tool.approval_failed":
                    log.turns[ti].calls[ci].ok = false
                    log.turns[ti].calls[ci].error = (details["error"] as? String) ?? (details["reason"] as? String)
                        ?? type.replacingOccurrences(of: "tool.", with: "").replacingOccurrences(of: "_", with: " ")
                    log.turns[ti].calls[ci].durationMs = details["duration_ms"] as? Double
                case "tool.permission_checked" where details["permitted"] as? Bool == false:
                    log.turns[ti].calls[ci].ok = false
                    log.turns[ti].calls[ci].error = "not permitted"
                case "tool.approval_requested":
                    approvalAsked[id] = at
                    log.turns[ti].calls[ci].approval = .asked
                case "tool.approval_granted":
                    log.turns[ti].calls[ci].approval = .granted(after: approvalAsked[id].map { at.timeIntervalSince($0) } ?? 0)
                case "tool.approval_denied":
                    log.turns[ti].calls[ci].approval = .denied
                    log.turns[ti].calls[ci].ok = false
                    log.turns[ti].calls[ci].error = "you said no"
                default:
                    break
                }
            }
        }
        for t in log.turns.indices {
            if let last = log.turns[t].steps.indices.last, log.turns[t].steps[last].nextAt == nil {
                log.turns[t].steps[last].nextAt = log.turns[t].finished
            }
        }
        addConversation(to: &log, folder: folder)
        return log
    }

    /// Answers and tool results from conversation.json: a turn's answer is the AI's last text
    /// after its message; a call's result is matched by tool name and arguments, in order.
    private static func addConversation(to log: inout SessionLog, folder: URL) {
        guard let data = try? Data(contentsOf: folder.appendingPathComponent("conversation.json")),
              let root = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              let messages = root["messages"] as? [[String: Any]] else { return }
        var results: [String: String] = [:]       // tool_call_id → content
        for message in messages where message["role"] as? String == "tool" {
            if let id = message["tool_call_id"] as? String { results[id] = text(message["content"]) }
        }
        var pending: [String: [String]] = [:]     // "tool|args" → result contents, in order
        var answers: [(user: String, answer: String?)] = []
        for message in messages {
            switch message["role"] as? String {
            case "user":
                answers.append((text(message["content"]), nil))
            case "assistant":
                for call in message["tool_calls"] as? [[String: Any]] ?? [] {
                    let function = call["function"] as? [String: Any] ?? [:]
                    let arguments = (function["arguments"] as? String).flatMap {
                        (try? JSONSerialization.jsonObject(with: Data($0.utf8))) as? [String: Any]
                    } ?? [:]
                    let key = "\(function["name"] as? String ?? "")|\(pretty(arguments))"
                    if let id = call["id"] as? String, let result = results[id] { pending[key, default: []].append(result) }
                }
                let content = text(message["content"])
                if !content.isEmpty, content != "None", !answers.isEmpty { answers[answers.count - 1].answer = content }
            default:
                break
            }
        }
        var used = Set<Int>()
        for t in log.turns.indices {
            if let index = answers.indices.first(where: { !used.contains($0) && answers[$0].user == log.turns[t].message }) {
                used.insert(index)
                log.turns[t].answer = answers[index].answer
            }
            for c in log.turns[t].calls.indices {
                let key = "\(log.turns[t].calls[c].tool)|\(log.turns[t].calls[c].arguments)"
                if var queue = pending[key], !queue.isEmpty {
                    log.turns[t].calls[c].result = queue.removeFirst()
                    pending[key] = queue
                }
            }
        }
    }

    // MARK: Helpers

    private static let isoFractional: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter
    }()
    private static let iso = ISO8601DateFormatter()

    static func date(_ text: String?) -> Date? {
        guard let text else { return nil }
        // Python writes microseconds; ISO8601DateFormatter takes milliseconds.
        let trimmed = text.replacingOccurrences(of: #"(\.\d{3})\d+"#, with: "$1", options: .regularExpression)
        return isoFractional.date(from: trimmed) ?? iso.date(from: trimmed)
    }

    static func pretty(_ value: [String: Any]) -> String {
        guard JSONSerialization.isValidJSONObject(value),
              let data = try? JSONSerialization.data(withJSONObject: value, options: [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes])
        else { return "{}" }
        return String(decoding: data, as: UTF8.self)
    }

    static func summary(_ arguments: [String: Any]) -> String {
        let preferred = ["command", "path", "pattern", "name", "service", "query", "url", "issue_key"]
        let key = preferred.first { arguments[$0] != nil } ?? arguments.keys.sorted().first
        guard let key, let value = arguments[key] else { return "" }
        let text = (value as? String) ?? "\(value)"
        let line = text.replacingOccurrences(of: "\n", with: " ")
        return line.count > 120 ? String(line.prefix(120)) + "…" : line
    }

    private static func text(_ value: Any?) -> String {
        if let string = value as? String { return string }
        if let parts = value as? [[String: Any]] {
            return parts.compactMap { $0["text"] as? String }.joined(separator: "\n")
        }
        return ""
    }
}
