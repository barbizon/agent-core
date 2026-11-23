import XCTest
@testable import OrwickApp

final class CrashReportTests: XCTestCase {
    func testACrashReportIsSummedUpInAFewLines() throws {
        let body: [String: Any] = [
            "exception": ["type": "EXC_BREAKPOINT", "signal": "SIGTRAP"],
            "termination": ["indicator": "Trace/BPT trap: 5"],
            "faultingThread": 0,
            "usedImages": [["name": "SwiftUI"], ["name": "AppKit"]],
            "threads": [["frames": [["imageIndex": 0, "symbol": "ViewListTree.visitItem(_:force:)"],
                                    ["imageIndex": 1, "symbol": "-[NSOutlineView insertItemsAtIndexes:inParent:withAnimation:]"]]]],
        ]
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("Orwick-test-\(UUID().uuidString).ips")
        let text = "{\"app_name\":\"Orwick\"}\n" + String(decoding: try JSONSerialization.data(withJSONObject: body), as: UTF8.self)
        try text.write(to: url, atomically: true, encoding: .utf8)
        defer { try? FileManager.default.removeItem(at: url) }
        let summary = try XCTUnwrap(CrashReport.summary(of: url))
        XCTAssertTrue(summary.contains("EXC_BREAKPOINT SIGTRAP"))
        XCTAssertTrue(summary.contains("Trace/BPT trap: 5"))
        XCTAssertTrue(summary.contains("SwiftUI  ViewListTree.visitItem(_:force:)"))
        XCTAssertTrue(summary.contains("AppKit  -[NSOutlineView insertItemsAtIndexes:inParent:withAnimation:]"))
    }
}
