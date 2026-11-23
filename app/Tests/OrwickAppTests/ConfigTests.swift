import XCTest
@testable import OrwickApp

final class ConfigTests: XCTestCase {
    private var folder: URL!

    override func setUpWithError() throws {
        folder = FileManager.default.temporaryDirectory.appendingPathComponent("orwick-config-\(UUID())")
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: folder)
    }

    private var file: URL { folder.appendingPathComponent(".orwick/config.json") }

    func testAMissingFileIsAllNotSet() throws {
        XCTAssertEqual(try OrwickConfig.load(from: file), OrwickConfig())
    }

    func testReadsOrwicksOwnFormat() throws {
        try FileManager.default.createDirectory(at: file.deletingLastPathComponent(), withIntermediateDirectories: true)
        try Data("""
        {"backend": "claude", "model": null, "max_steps": 10, "request_timeout_seconds": 60.5,
         "max_tool_result_chars": "8000", "audit_log": ".orwick/logs/audit.jsonl", "workspace": null, "debug": false}
        """.utf8).write(to: file)
        let config = try OrwickConfig.load(from: file)
        XCTAssertEqual(config.backend, "claude")
        XCTAssertEqual(config.model, "")
        XCTAssertEqual(config.maxSteps, "10")
        XCTAssertEqual(config.requestTimeoutSeconds, "60.5")
        XCTAssertEqual(config.maxToolResultChars, "8000")
        XCTAssertEqual(config.debug, false)
    }

    func testSaveCreatesTheFolderAndRoundTrips() throws {
        var config = OrwickConfig()
        config.backend = "codex"
        config.model = "  gpt-5 \"quoted\" "
        config.maxSteps = "5"
        config.requestTimeoutSeconds = "90"
        config.auditLog = "logs/a b.jsonl"
        config.debug = true
        try config.save(to: file)
        let text = try String(contentsOf: file, encoding: .utf8)
        XCTAssertTrue(text.contains("\"max_steps\": 5,"))
        XCTAssertTrue(text.contains("\"audit_log\": \"logs/a b.jsonl\","))
        XCTAssertFalse(text.contains("workspace"))
        var expected = config
        expected.model = "gpt-5 \"quoted\""
        expected.trace = true          // saved as on/off, like debug
        XCTAssertEqual(try OrwickConfig.load(from: file), expected)
    }

    func testDebugIsAlwaysSavedAsOnOrOff() throws {
        try OrwickConfig().save(to: file)
        XCTAssertTrue(try String(contentsOf: file, encoding: .utf8).contains("\"debug\": false"))
        XCTAssertEqual(try OrwickConfig.load(from: file).debug, false)
    }

    func testUnknownKeysAndBrokenJsonAreRefused() throws {
        try FileManager.default.createDirectory(at: file.deletingLastPathComponent(), withIntermediateDirectories: true)
        try Data(#"{"backend": "claude", "temperature": 1}"#.utf8).write(to: file)
        XCTAssertThrowsError(try OrwickConfig.load(from: file))
        try Data("{not json".utf8).write(to: file)
        XCTAssertThrowsError(try OrwickConfig.load(from: file))
    }

    func testAWorkspaceInTheMainFileIsRefused() throws {
        try FileManager.default.createDirectory(at: file.deletingLastPathComponent(), withIntermediateDirectories: true)
        try Data(#"{"workspace": "/"}"#.utf8).write(to: file)
        XCTAssertThrowsError(try OrwickConfig.load(from: file))
        try Data(#"{"workspace": null, "backend": "claude"}"#.utf8).write(to: file)
        XCTAssertEqual(try OrwickConfig.load(from: file).backend, "claude")
    }

    func testFolderOverridesListsOnlyTheKeysTheFolderSets() throws {
        try FileManager.default.createDirectory(at: file.deletingLastPathComponent(), withIntermediateDirectories: true)
        try Data(#"{"backend": "claude", "model": null, "max_steps": 3}"#.utf8).write(to: file)
        XCTAssertEqual(OrwickConfig.folderOverrides(in: folder), ["backend", "max_steps"])
        XCTAssertEqual(OrwickConfig.folderOverrides(in: folder.appendingPathComponent("none")), [])
    }

    func testTheConversationBudgetIsReadCheckedAndSaved() throws {
        var config = OrwickConfig()
        config.historyBudgetTokens = "7999"
        XCTAssertNotNil(config.problems["history_budget_tokens"])
        config.historyBudgetTokens = "150000"
        XCTAssertTrue(config.problems.isEmpty)
        config.historyWarnPercent = "101"
        XCTAssertNotNil(config.problems["history_warn_percent"])
        config.historyWarnPercent = "40"
        try config.save(to: file)
        XCTAssertEqual(try OrwickConfig.load(from: file).historyBudgetTokens, "150000")
        XCTAssertEqual(try OrwickConfig.load(from: file).historyWarnPercent, "40")
    }

    func testToolsAreReadAndSavedAsOnOff() throws {
        var config = OrwickConfig()
        config.tools = ["run_command": false, "grep": false]
        try config.save(to: file)
        let text = try String(contentsOf: file, encoding: .utf8)
        XCTAssertTrue(text.contains(#""tools": {"grep": false, "run_command": false}"#))
        XCTAssertEqual(try OrwickConfig.load(from: file).tools, ["run_command": false, "grep": false])
        if let out = ProcessInfo.processInfo.environment["ORWICK_SAMPLE_TOOLS"] {
            try config.save(to: URL(fileURLWithPath: out))
        }
    }

    func testSkillsAreReadAndSavedAsOnOff() throws {
        var config = OrwickConfig()
        config.skills = ["twg": true, "feature-docs": false]
        try config.save(to: file)
        let text = try String(contentsOf: file, encoding: .utf8)
        XCTAssertTrue(text.contains(#""skills": {"feature-docs": false, "twg": true}"#))
        XCTAssertEqual(try OrwickConfig.load(from: file).skills, ["twg": true, "feature-docs": false])
        try #"{"skills": {"twg": "yes"}}"#.write(to: file, atomically: true, encoding: .utf8)
        XCTAssertThrowsError(try OrwickConfig.load(from: file))
    }

    func testSkillsAreGroupedByWhereTheyComeFrom() throws {
        let json = #"""
        [{"name": "twg", "origin": "~/.claude/skills", "source": "atlassian", "title": null, "description": "Jira.", "default": false, "actions": 0, "on": false},
         {"name": "feature-docs", "origin": "built-in", "title": "Feature docs", "description": "Docs.", "default": true, "actions": 2, "on": true},
         {"name": "mine", "origin": "~/.orwick/skills", "title": null, "description": "Mine.", "default": true, "actions": 0}]
        """#
        let skills = try JSONDecoder().decode([SkillInfo].self, from: Data(json.utf8))
        let groups = SkillInfo.grouped(skills)
        XCTAssertEqual(groups.map(\.origin), ["built-in", "~/.orwick/skills", "claude:atlassian"])
        XCTAssertEqual(groups.last?.skills.first?.default, false)
        XCTAssertNil(groups[1].skills.first?.on)        // --list-tools doesn't say: no session
    }

    func testClaudeSkillsAreGroupedByWhoMadeThem() throws {
        let json = #"""
        [{"name": "golang-testing", "origin": "~/.claude/skills", "source": "custom", "description": "Go.", "default": false},
         {"name": "docx", "origin": "~/.claude/skills", "source": "anthropic", "description": "Word.", "default": false},
         {"name": "old", "origin": "~/.claude/skills", "description": "Older Orwick: no source.", "default": false}]
        """#
        let groups = SkillInfo.grouped(try JSONDecoder().decode([SkillInfo].self, from: Data(json.utf8)))
        XCTAssertEqual(groups.map(\.origin), ["claude:custom", "claude:anthropic"])
        XCTAssertEqual(groups.first?.skills.map(\.name), ["golang-testing", "old"])   // no source: custom
    }

    func testTheAIsOwnToolsAreSavedAsListsOrNull() throws {
        var config = OrwickConfig()
        config.claudeTools = []                       // all off
        config.codexTools = nil                       // safe defaults
        try config.save(to: file)
        let text = try String(contentsOf: file, encoding: .utf8)
        XCTAssertTrue(text.contains(#""claude_tools": []"#) && text.contains(#""codex_tools": null"#))
        config.claudeTools = ["WebSearch", "Bash"]
        try config.save(to: file)
        let loaded = try OrwickConfig.load(from: file)
        XCTAssertEqual(loaded.claudeTools, ["WebSearch", "Bash"])
        XCTAssertNil(loaded.codexTools)
        if let out = ProcessInfo.processInfo.environment["ORWICK_SAMPLE_OWN"] { try config.save(to: URL(fileURLWithPath: out)) }
    }

    func testMcpServersAreKeptExactlyWhenSaving() throws {
        try FileManager.default.createDirectory(at: file.deletingLastPathComponent(), withIntermediateDirectories: true)
        try Data(#"{"backend": "codex", "mcpServers": {"mcp-atlassian": {"command": "uvx", "args": ["mcp-atlassian"], "env": {"JIRA_API_TOKEN": "${JIRA_TOKEN}"}}}, "tools": {"mcp__mcp-atlassian__jira_get_issue": true}}"#.utf8).write(to: file)
        var config = try OrwickConfig.load(from: file)
        config.maxSteps = "7"
        try config.save(to: file)
        let saved = try JSONSerialization.jsonObject(with: Data(contentsOf: file)) as! [String: Any]
        let server = (saved["mcpServers"] as! [String: Any])["mcp-atlassian"] as! [String: Any]
        XCTAssertEqual(server["command"] as? String, "uvx")
        XCTAssertEqual((server["env"] as! [String: String])["JIRA_API_TOKEN"], "${JIRA_TOKEN}")
        XCTAssertEqual((saved["tools"] as! [String: Bool])["mcp__mcp-atlassian__jira_get_issue"], true)
        XCTAssertEqual(saved["max_steps"] as? Int, 7)
        if let out = ProcessInfo.processInfo.environment["ORWICK_SAMPLE_MCP"] { try config.save(to: URL(fileURLWithPath: out)) }
    }

    func testBadToolsValuesAreRefused() throws {
        try FileManager.default.createDirectory(at: file.deletingLastPathComponent(), withIntermediateDirectories: true)
        for bad in [#"{"tools": ["run_command"]}"#, #"{"tools": {"run_command": "no"}}"#] {
            try Data(bad.utf8).write(to: file)
            XCTAssertThrowsError(try OrwickConfig.load(from: file), bad)
        }
    }

    func testEveryLimitAcceptsZeroForNoLimit() {
        var config = OrwickConfig()
        config.maxSteps = "0"; config.requestTimeoutSeconds = "0"; config.maxToolResultChars = "0"
        config.historyBudgetTokens = "0"; config.historyWarnPercent = "0"
        XCTAssertTrue(config.problems.isEmpty, "\(config.problems)")
        config.maxToolResultChars = "50"; config.historyBudgetTokens = "5000"; config.requestTimeoutSeconds = "-1"
        XCTAssertEqual(Set(config.problems.keys), ["max_tool_result_chars", "history_budget_tokens", "request_timeout_seconds"])
    }

    func testTheSameChecksAsOrwick() {
        var config = OrwickConfig()
        XCTAssertTrue(config.problems.isEmpty)
        config.maxSteps = "-1"
        config.requestTimeoutSeconds = "-1"
        config.maxToolResultChars = "99"
        XCTAssertEqual(Set(config.problems.keys), ["max_steps", "request_timeout_seconds", "max_tool_result_chars"])
        config.maxSteps = "abc"
        XCTAssertNotNil(config.problems["max_steps"])
        config = OrwickConfig()
        config.maxSteps = "1"
        config.requestTimeoutSeconds = "0.5"
        config.maxToolResultChars = "100"
        XCTAssertTrue(config.problems.isEmpty)
    }

    /// Writes a sample file for Orwick's own loader to check (see the shell step in the README).
    func testWritesASampleForOrwick() throws {
        guard let out = ProcessInfo.processInfo.environment["ORWICK_SAMPLE_CONFIG"] else { return }
        var config = OrwickConfig()
        config.backend = "claude"
        config.maxSteps = "7"
        config.requestTimeoutSeconds = "90.5"
        config.debug = false
        try config.save(to: URL(fileURLWithPath: out))
    }
}
