import XCTest
@testable import OrwickApp

final class StatusTests: XCTestCase {
    /// Status files written by the real Orwick code (status_file.py).
    private func fixture(_ name: String) throws -> URL {
        try XCTUnwrap(Bundle.module.url(forResource: name, withExtension: "json", subdirectory: "Fixtures"))
    }

    func testReadsTheFileOrwickWritesAtStartup() throws {
        let url = try fixture("status-startup")
        let status = try XCTUnwrap(OrwickStatus.read(from: url))
        XCTAssertFalse(status.tools.isEmpty)
        XCTAssertNil(status.skills)
    }

    func testReadsTokensLimitsAndOtherBackends() throws {
        let url = try fixture("status-usage")
        let status = try XCTUnwrap(OrwickStatus.read(from: url))
        let claude = try XCTUnwrap(status.current)
        XCTAssertEqual(claude.inputTokens, 9800)
        XCTAssertEqual(claude.cachedTokens, 8624)
        XCTAssertEqual(claude.cacheWriteTokens, 1100)
        XCTAssertEqual(claude.newInputTokens, 9800 - 8624)
        XCTAssertNil(try XCTUnwrap(status.usage["codex"]).cacheWriteTokens)   // older Orwick / Codex: not reported
        XCTAssertEqual(claude.limits.map(\.name), ["5-hour", "7-day"])
        XCTAssertEqual(status.usage["codex"]?.requests, 1)
        XCTAssertEqual(status.session.toolCalls, 1)
        XCTAssertEqual(status.tools.first { $0.name == "read_file" }?.uses, 1)
    }

    func testAnUnknownFormatOrBrokenFileIsIgnored() throws {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("status-\(UUID()).json")
        defer { try? FileManager.default.removeItem(at: url) }
        try Data("{\"format\": 2}".utf8).write(to: url)
        XCTAssertNil(OrwickStatus.read(from: url))
        try Data("{broken".utf8).write(to: url)
        XCTAssertNil(OrwickStatus.read(from: url))
    }

    func testSessionIDsHaveOrwicksShape() {
        XCTAssertTrue(Session.isSessionID(Session.newID(now: Date(timeIntervalSince1970: 0))))
        XCTAssertFalse(Session.isSessionID("../../etc"))
        XCTAssertFalse(Session.isSessionID("2026-11-12_114105"))
    }

    func testReadsTheContinuedInPointer() throws {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("status-\(UUID()).json")
        defer { try? FileManager.default.removeItem(at: url) }
        var json = try String(contentsOf: try fixture("status-usage"), encoding: .utf8)
        json = json.replacingOccurrences(of: "\"session_id\": null", with: "\"session_id\": \"2026-11-12_100000_aaaa\"")
        json = json.replacingOccurrences(of: "{\"format\": 1,", with: "{\"format\": 1, \"continued_in\": \"2026-11-12_100500_bbbb\",")
        try Data(json.utf8).write(to: url)
        XCTAssertEqual(OrwickStatus.read(from: url)?.continuedIn, "2026-11-12_100500_bbbb")
    }

    func testReadsHowFullTheConversationIs() throws {
        let level = try XCTUnwrap(OrwickStatus.read(from: try fixture("status-fill"))?.conversation)
        XCTAssertEqual(level.budget, 10_000)
        XCTAssertEqual(level.percent, 65)
        XCTAssertTrue(level.isWarning)          // over the 50% warning point
        XCTAssertFalse(level.isSummarizing)     // under 75%
        XCTAssertNil(try XCTUnwrap(OrwickStatus.read(from: try fixture("status-usage"))).conversation)  // older Orwick: no data
    }

    func testSessionToolSwitchesAreWrittenAndRemoved() throws {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("tools-\(UUID()).json")
        defer { try? FileManager.default.removeItem(at: url) }
        Session.writeToolOverrides(["run_command": false, "grep": true], to: url)
        XCTAssertEqual(Session.readToolOverrides(from: url), ["run_command": false, "grep": true])
        if let out = ProcessInfo.processInfo.environment["ORWICK_SAMPLE_SESSION_TOOLS"] {
            Session.writeToolOverrides(["run_command": false, "grep": true], to: URL(fileURLWithPath: out))
        }
        Session.writeToolOverrides([:], to: url)
        XCTAssertFalse(FileManager.default.fileExists(atPath: url.path))
    }

    func testReadsSkillActionsAndTheWorkingState() throws {
        let status = try XCTUnwrap(OrwickStatus.read(from: try fixture("status-actions")))
        XCTAssertFalse(status.isWaiting)                       // "working": buttons are greyed out
        let actions = try XCTUnwrap(status.actions)
        XCTAssertEqual(actions.map(\.label), ["Read ticket", "Update docs"])
        XCTAssertEqual(actions.first?.command, "/do feature-docs read-ticket")
        XCTAssertEqual(actions.first?.icon, "ticket")
        XCTAssertTrue(try XCTUnwrap(OrwickStatus.read(from: try fixture("status-usage"))).isWaiting)  // older Orwick
    }

    func testFormatting() {
        XCTAssertEqual(Format.tokens(481), "481")
        XCTAssertEqual(Format.tokens(9_800), "9.8k")
        XCTAssertEqual(Format.tokens(30_000), "30k")
        XCTAssertEqual(Format.tokens(1_000_000), "1.0M")
        XCTAssertEqual(Format.duration(79), "1m 19s")
        XCTAssertEqual(Session.shellQuoted("/a b/it's.json"), "'/a b/it'\\''s.json'")
    }
}
