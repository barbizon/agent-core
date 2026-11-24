import XCTest
@testable import OrwickApp

final class ConnectionsTests: XCTestCase {
    func testConnectingJiraDefinesTheServerAndSwitchesItsReadToolsOn() throws {
        var config = OrwickConfig()
        config.mcpServersJSON = #"{"other": {"command": "run-it"}}"#
        config.tools = ["run_command": false]
        XCTAssertFalse(JiraConnection.isConnected(config))

        let connected = JiraConnection.connected(config)

        XCTAssertTrue(JiraConnection.isConnected(connected))
        XCTAssertEqual(connected.tools["mcp__mcp-atlassian__jira_get_issue"], true)
        XCTAssertEqual(connected.tools["mcp__mcp-atlassian__confluence_get_page"], true)
        XCTAssertEqual(connected.tools["run_command"], false)
        // Saved and read back: Orwick's format, the other server kept, no token in the file.
        let file = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString + ".json")
        try connected.save(to: file)
        let text = try String(contentsOf: file, encoding: .utf8)
        XCTAssertTrue(text.contains(#""JIRA_API_TOKEN" : "${JIRA_TOKEN}""#))
        XCTAssertTrue(text.contains(#""CONFLUENCE_URL" : "${JIRA_URL}/wiki""#))
        XCTAssertTrue(text.contains("run-it"))
        XCTAssertEqual(try OrwickConfig.load(from: file).tools, connected.tools)
    }

    func testTheLoginIsSavedBesideWhatTheFileAlreadyHolds() throws {
        let file = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString + ".env")
        try Data("# logins\nBITBUCKET_TOKEN=abc\nJIRA_TOKEN=old\n".utf8).write(to: file)

        try JiraConnection.saveLogin(site: " team.atlassian.net/ ", email: "me@example.com", token: "new=token\n", to: file)

        XCTAssertEqual(try String(contentsOf: file, encoding: .utf8),
                       "# logins\nBITBUCKET_TOKEN=abc\nJIRA_TOKEN=new=token\nJIRA_URL=https://team.atlassian.net\nJIRA_EMAIL=me@example.com\n")
        let mode = try FileManager.default.attributesOfItem(atPath: file.path)[.posixPermissions] as? Int
        XCTAssertEqual(mode, 0o600)
    }
}
