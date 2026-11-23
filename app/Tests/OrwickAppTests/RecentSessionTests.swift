import XCTest
@testable import OrwickApp

final class RecentSessionTests: XCTestCase {
    /// A folder whose sessions were saved by Orwick's own code (sessions.py).
    private func project() throws -> URL {
        try XCTUnwrap(Bundle.module.url(forResource: "project", withExtension: nil, subdirectory: "Fixtures"))
    }

    func testListsSavedSessionsWithMessagesOnly() throws {
        let found = RecentSession.saved(in: try project()).sorted { $0.updatedAt > $1.updatedAt }
        XCTAssertEqual(found.map(\.id), ["2026-11-12_110000_bbbb", "2026-11-12_100000_aaaa"])
        XCTAssertEqual(found.map(\.title), ["write the README", "fix the login bug"])
        XCTAssertEqual(found.map(\.messages), [1, 2])
    }

    func testTheToolCatalogReadsToolJsonFiles() throws {
        let catalog = ToolInfo.catalog(in: [try project()], alsoNamed: ["old_tool"])
        XCTAssertEqual(catalog.map(\.name), ["read_file", "run_command", "old_tool"])   // read, dangerous, unknown
        XCTAssertEqual(catalog.first?.risk, "read")
        XCTAssertEqual(catalog.first?.folders, ["project"])
        XCTAssertEqual(catalog.last?.description, "Not found in your recent folders.")
    }

    func testAsksTheInstalledOrwickForItsTools() throws {
        let done = expectation(description: "orwick --list-tools")
        var tools: [ToolInfo] = []
        ToolInfo.fromOrwick { tools = $0; done.fulfill() }
        wait(for: [done], timeout: 30)
        XCTAssertEqual(Set(tools.filter { $0.origin == "built-in" }.map(\.name)),
                       ["read_file", "list_folder", "grep", "write_file", "edit_file", "run_command",
                        "use_skill", "read_session_doc", "write_session_doc", "write_session_docs", "run_subagent",
                        "read_service_map", "write_service_map", "read_pull_request",
                        "collect_review_comments", "read_review_comments", "save_review_patterns",
                        "save_analytics_report"])
        // A folder's tool.json with a built-in name doesn't replace the built-in one.
        let merged = ToolInfo.catalog(in: [try project()], fromOrwick: tools)
        XCTAssertEqual(merged.first { $0.name == "read_file" }?.origin, "built-in")
    }

    func testOrwickReportsEachAIsOwnTools() {
        let done = expectation(description: "orwick --list-tools")
        var result = ToolInfo.OrwickTools()
        ToolInfo.fromOrwickWithOwn { result = $0; done.fulfill() }
        wait(for: [done], timeout: 30)
        let claude = Dictionary(uniqueKeysWithValues: (result.own["claude"] ?? []).map { ($0.name, $0) })
        XCTAssertEqual(claude["WebSearch"]?.default, true)
        XCTAssertEqual(claude["Bash"]?.isDangerous, true)
        XCTAssertEqual(claude["Bash"]?.default, false)
        XCTAssertEqual(result.own["codex"]?.map(\.name), ["web_search", "shell"])
    }

    func testRecentSessionsKnowTheirFeatureAndDocs() throws {
        let found = RecentSession.saved(in: try project())
        let linked = try XCTUnwrap(found.first { $0.id == "2026-11-12_100000_aaaa" })
        XCTAssertEqual(linked.feature, "PROJ-16765")
        XCTAssertTrue(linked.hasDocs)
        let tabs = SessionDocs.ordered(SessionDocs.list(in: linked.sessionFolder))
        XCTAssertEqual(tabs.map(\.lastPathComponent), ["product-requirement.md", "todo.md", "extra-notes.md"])
        XCTAssertEqual(tabs.map(SessionDocs.label), ["Product Requirement", "To Do", "extra-notes"])
        let other = try XCTUnwrap(found.first { $0.id == "2026-11-12_110000_bbbb" })
        XCTAssertNil(other.feature)
        XCTAssertFalse(other.hasDocs)
    }

    func testOrwickListsTheModelsForTheMenus() {
        let done = expectation(description: "orwick --list-models")
        var models: [String: [ModelInfo]] = [:]
        ModelCatalog.load { models = $0; done.fulfill() }
        wait(for: [done], timeout: 30)
        XCTAssertEqual(models["claude"]?.map(\.label),
                       ["Claude Fable 5.2", "Claude Opus 5.5", "Claude Sonnet 5.5", "Claude Haiku 5.5"])
        XCTAssertEqual(ModelCatalog.label(backend: "claude", model: "claude-fable-5-2", in: models), "Claude Fable 5.2")
        XCTAssertEqual(ModelCatalog.defaultLabel(backend: "claude", in: models), "Claude Opus 5.5")
        XCTAssertEqual(ModelCatalog.label(backend: "claude", model: nil, in: models), "Claude Opus 5.5")
        XCTAssertNotNil(ModelCatalog.defaultLabel(backend: "codex", in: models))
        XCTAssertEqual(ModelCatalog.label(backend: "codex", model: "my-model", in: models), "my-model")
    }

    func testANamedSessionShowsItsNameAndTheNameFollowsOrwicksRules() throws {
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent("named-\(UUID())")
        let session = folder.appendingPathComponent(".orwick/sessions/2026-11-12_100000_aaaa")
        try FileManager.default.createDirectory(at: session, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: folder) }
        try #"{"title": "first message", "updated_at": 1, "messages": 1}"#
            .write(to: session.appendingPathComponent("meta.json"), atomically: true, encoding: .utf8)
        XCTAssertNil(SessionName.read(session))
        XCTAssertEqual(RecentSession.saved(in: folder).first?.title, "first message")

        XCTAssertEqual(try SessionName.write("  PROJ-16930   Price\nlists ", to: session), "PROJ-16930 Price lists")
        XCTAssertEqual(SessionName.read(session), "PROJ-16930 Price lists")
        XCTAssertEqual(RecentSession.saved(in: folder).first?.title, "PROJ-16930 Price lists")
        XCTAssertEqual(try SessionName.write(String(repeating: "x", count: 500), to: session)?.count, SessionName.maxLength)

        XCTAssertNil(try SessionName.write("   ", to: session))   // empty: the name goes
        XCTAssertFalse(FileManager.default.fileExists(atPath: session.appendingPathComponent("name.txt").path))
        XCTAssertEqual(RecentSession.saved(in: folder).first?.title, "first message")
    }

    func testAFolderWithoutSessionsHasNone() {
        XCTAssertEqual(RecentSession.saved(in: FileManager.default.temporaryDirectory.appendingPathComponent("none-\(UUID())")), [])
    }
}

final class DashboardTests: XCTestCase {
    func testTheDashboardCountsSessionsDocsAndTickets() throws {
        let project = try XCTUnwrap(Bundle.module.url(forResource: "project", withExtension: nil, subdirectory: "Fixtures"))
        let data = DashboardData.load(folders: [project])
        XCTAssertEqual(data.items.map(\.recent.id), ["2026-11-12_110000_bbbb", "2026-11-12_100000_aaaa"])
        XCTAssertEqual(data.items.last?.docs, ["Product Requirement", "To Do", "extra-notes"])
        XCTAssertEqual(data.docs, 3)
        XCTAssertEqual(data.tickets, ["PROJ-16765"])
        XCTAssertEqual(data.folders, 1)
        XCTAssertEqual(data.tokens, 0)      // these sessions have no status.json
        // The code root adds the folders in it that Orwick was used in, once.
        let root = project.deletingLastPathComponent()
        XCTAssertEqual(DashboardData.folders(known: [project], codeRoot: root), [project.standardizedFileURL])
        XCTAssertEqual(DashboardData.folders(known: [], codeRoot: root), [project.standardizedFileURL])
        XCTAssertEqual(DashboardData.folders(known: [], codeRoot: nil), [])
    }
}

final class AnalyticsTests: XCTestCase {
    func testReadsThePatternsAndSavesYourDecisions() throws {
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent("orwick-analytics-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: folder) }
        try Data(#"{"format": 1, "patterns": [{"id": "error-without-context", "name": "Error returned without context", "rule": "Wrap it.", "example": "", "detect": "a bare return err", "item": "E1", "count": 5, "prs": 3, "repos": ["payments"], "reviewers": 2, "last_seen": "2026-10-05"}, {"id": "no-test", "name": "No test", "rule": "Add one.", "count": 1, "prs": 1}]}"#.utf8)
            .write(to: folder.appendingPathComponent("patterns.json"))
        try Data(#"{"comments": [{"id": 1, "pattern": "error-without-context"}, {"id": 2, "pattern": null}]}"#.utf8)
            .write(to: folder.appendingPathComponent("comments.json"))
        try Data(#"{"repos": {"acme/payments": {}}}"#.utf8).write(to: folder.appendingPathComponent("state.json"))

        var data = ReviewAnalytics.load(from: folder)
        XCTAssertEqual(data.patterns.map(\.id), ["error-without-context", "no-test"])
        XCTAssertEqual(data.patterns[0].prs, 3)
        XCTAssertEqual(data.patterns[0].item, "E1")
        XCTAssertEqual(data.repos, ["acme/payments"])
        XCTAssertEqual(data.comments, 2)
        XCTAssertEqual(data.waiting, 1)
        XCTAssertEqual(data.decision(data.patterns[0]), .suggested)

        ReviewAnalytics.decide("error-without-context", .on, in: folder)
        ReviewAnalytics.decide("no-test", .muted, in: folder)
        data = ReviewAnalytics.load(from: folder)
        XCTAssertEqual(data.decisions, ["error-without-context": "on", "no-test": "muted"])
        XCTAssertEqual(data.on, 1)
        ReviewAnalytics.decide("no-test", .suggested, in: folder)
        XCTAssertEqual(ReviewAnalytics.load(from: folder).decisions, ["error-without-context": "on"])
    }
}

final class AnalyticsEntryTests: XCTestCase {
    func testListsOrwicksOwnThenYoursWithTheirLatestReport() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("orwick-entries-\(UUID().uuidString)")
        let skills = root.appendingPathComponent("skills"), store = root.appendingPathComponent("analytics")
        defer { try? FileManager.default.removeItem(at: root) }
        for (name, header) in [("slow-prs", "kind: analytics\ntitle: Slow PRs\n"), ("code-check-company", "extends: code-check\n")] {
            let folder = skills.appendingPathComponent(name)
            try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
            try Data("---\nname: \(name)\ndescription: About \(name): with a colon.\n\(header)---\n# Body\n".utf8)
                .write(to: folder.appendingPathComponent("SKILL.md"))
        }
        try Data(#"{"title": "Slow PRs", "wanted": "Which wait longest?\nPer repo."}"#.utf8)
            .write(to: skills.appendingPathComponent("slow-prs/analytics.json"))
        var entries = AnalyticsEntry.all(skills: skills, store: store)
        XCTAssertEqual(entries.map(\.name), ["review-analytics", "slow-prs"])      // an addition is not an analytics
        XCTAssertEqual(entries[1].wanted, "Which wait longest?\nPer repo.")
        XCTAssertEqual(entries[1].title, "Slow PRs")
        XCTAssertEqual(entries[1].description, "About slow-prs: with a colon.")
        XCTAssertEqual(entries[1].command, "/do slow-prs run")
        XCTAssertEqual(entries[0].command, "/do review-analytics learn")
        XCTAssertTrue(entries[1].report.isEmpty)

        let home = store.appendingPathComponent("slow-prs")
        try FileManager.default.createDirectory(at: home, withIntermediateDirectories: true)
        try Data("**Result:** 3 wait.\n".utf8).write(to: home.appendingPathComponent("report.md"))
        try Data(#"{"updated_at": 1000, "runs": 2}"#.utf8).write(to: home.appendingPathComponent("meta.json"))
        entries = AnalyticsEntry.all(skills: skills, store: store)
        XCTAssertEqual(entries[1].report, "**Result:** 3 wait.\n")
        XCTAssertEqual(entries[1].runs, 2)
        XCTAssertEqual(entries[1].updatedAt, Date(timeIntervalSince1970: 1000))
    }
}
