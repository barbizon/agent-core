import XCTest
@testable import OrwickApp

final class SessionLogTests: XCTestCase {
    func testMessagesStepsAndToolCallsAreReadFromTheSessionFiles() throws {
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: folder) }
        func event(_ time: String, _ type: String, _ details: [String: Any], call: String? = nil, run: String = "r1") -> String {
            var e: [String: Any] = ["timestamp": "2026-11-18T06:42:\(time)+00:00", "run_id": run, "event_type": type, "details": details]
            if let call { e["call_id"] = call }
            return String(decoding: try! JSONSerialization.data(withJSONObject: e), as: UTF8.self)
        }
        let audit = [
            event("50.000000", "agent.started", ["message": "Map it"]),
            event("50.100000", "model.requested", ["step": 1]),
            event("53.100000", "model.responded", ["step": 1, "tool_calls": ["read_file", "run_command"]]),
            event("53.200000", "tool.requested", ["tool": "read_file", "arguments": ["path": "main.go"]], call: "a"),
            event("53.300000", "tool.execution_succeeded", ["tool": "read_file", "duration_ms": 40.5], call: "a"),
            event("53.400000", "tool.requested", ["tool": "run_command", "arguments": ["command": "ls"]], call: "b"),
            event("53.500000", "tool.approval_requested", ["tool": "run_command"], call: "b"),
            event("55.500000", "tool.approval_denied", ["tool": "run_command"], call: "b"),
            event("55.600000", "model.requested", ["step": 2]),
            event("57.600000", "model.responded", ["step": 2, "tool_calls": []]),
            event("57.700000", "agent.finished", ["steps": 2]),
            event("58.000000", "agent.started", ["message": "Again"], run: "r2"),
            event("59.000000", "agent.failed", ["error_type": "KeyboardInterrupt", "error": ""], run: "r2"),
        ].joined(separator: "\n")
        try audit.write(to: folder.appendingPathComponent("audit.jsonl"), atomically: true, encoding: .utf8)
        let conversation: [String: Any] = ["format": 1, "messages": [
            ["role": "user", "content": "Map it"],
            ["role": "assistant", "content": NSNull(), "tool_calls": [
                ["id": "c1", "type": "function", "function": ["name": "read_file", "arguments": "{\"path\": \"main.go\"}"]]]],
            ["role": "tool", "tool_call_id": "c1", "content": "package main"],
            ["role": "assistant", "content": "Done: **1 flow**."],
            ["role": "user", "content": "Again"],
        ]]
        try JSONSerialization.data(withJSONObject: conversation).write(to: folder.appendingPathComponent("conversation.json"))

        let log = SessionLog.load(folder)
        XCTAssertEqual(log.turns.count, 2)
        XCTAssertEqual(log.stepCount, 2)
        XCTAssertEqual(log.callCount, 2)
        let first = log.turns[0]
        XCTAssertEqual(first.answer, "Done: **1 flow**.")
        XCTAssertEqual(first.steps[0].thinking ?? 0, 3.0, accuracy: 0.01)
        XCTAssertEqual(first.steps[0].toolsTime ?? 0, 2.5, accuracy: 0.01)
        XCTAssertEqual(first.calls[0].result, "package main")
        XCTAssertEqual(first.calls[0].summary, "main.go")
        XCTAssertEqual(first.calls[1].ok, false)
        XCTAssertEqual(first.calls[1].approval, .denied)
        XCTAssertEqual(log.turns[1].error, "stopped by you (Ctrl+C)")
        XCTAssertNil(log.turns[1].answer)
    }
}
