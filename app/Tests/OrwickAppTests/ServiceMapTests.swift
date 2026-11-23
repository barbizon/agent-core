import XCTest
import WebKit
@testable import OrwickApp

final class ServiceMapTests: XCTestCase {
    func testAMapReadsItsDocsInTheContractsOrderWithTitles() throws {
        let library = FileManager.default.temporaryDirectory.appendingPathComponent("maps-\(UUID().uuidString)")
        let folder = library.appendingPathComponent("orders")
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: library) }
        for doc in ["flows", "map", "extra"] { try Data("# \(doc)".utf8).write(to: folder.appendingPathComponent("\(doc).md")) }
        let meta: [String: Any] = ["docs": ["map", "flows", "gone"], "folder": "/Users/me/go/orders",
                                   "related": ["/Users/me/go/billing"], "question": "Price lists",
                                   "updated_at": 1_800_000_000.0, "titles": ["map": "Map", "flows": "Flows"]]
        try JSONSerialization.data(withJSONObject: meta).write(to: folder.appendingPathComponent("meta.json"))
        try FileManager.default.createDirectory(at: library.appendingPathComponent("not-a-map"), withIntermediateDirectories: true)

        let maps = ServiceMap.all(in: library)
        XCTAssertEqual(maps.map(\.name), ["orders"])            // a folder without meta.json isn't a map
        XCTAssertEqual(maps[0].docs, ["map", "flows", "extra"])       // missing docs dropped, unknown ones last
        XCTAssertEqual(maps[0].title(of: "flows"), "Flows")
        XCTAssertEqual(maps[0].title(of: "extra"), "extra")
        XCTAssertEqual(maps[0].related.map(\.lastPathComponent), ["billing"])
        XCTAssertEqual(maps[0].question, "Price lists")
    }

    func testAMapNameIsLowercaseWordsJoinedByDashes() {
        XCTAssertEqual(ServiceMap.slug("orders refund job"), "orders-refund-job")
        XCTAssertEqual(ServiceMap.slug("  Price / lists!  "), "price-lists")
        XCTAssertEqual(ServiceMap.slug("orders "), "orders")
    }

    func testTheDiagramPageEscapesTheSourceAndLoadsNothingFromTheNetwork() {
        let page = Mermaid.page("flowchart LR\n A[\"<b>x</b>\"] --> B", dark: false)
        XCTAssertTrue(page.contains("A[\"&lt;b&gt;x&lt;/b&gt;\"]"))
        XCTAssertTrue(page.contains("default-src 'none'"))
        XCTAssertNotNil(Mermaid.script)
        XCTAssertFalse(page.contains("http://") && page.contains("<script src"))
    }

    /// Draws a real diagram with the bundled Mermaid, offline, under the page's security policy.
    @MainActor func testTheBundledMermaidDrawsADiagram() throws {
        final class Handler: NSObject, WKScriptMessageHandler {
            var height: Double?
            let done: XCTestExpectation
            init(_ done: XCTestExpectation) { self.done = done }
            func userContentController(_ c: WKUserContentController, didReceive message: WKScriptMessage) {
                if height == nil { height = message.body as? Double ?? (message.body as? Int).map(Double.init); done.fulfill() }
            }
        }
        let done = expectation(description: "drawn")
        let handler = Handler(done)
        let config = WKWebViewConfiguration()
        config.userContentController.add(handler, name: "size")
        let view = WKWebView(frame: NSRect(x: 0, y: 0, width: 600, height: 400), configuration: config)
        view.loadHTMLString(Mermaid.page("flowchart LR\n  MKT[\"orders\"] -->|\"price.list.changed\"| BON[\"billing\"]", dark: false), baseURL: nil)
        wait(for: [done], timeout: 20)
        XCTAssertGreaterThan(handler.height ?? 0, 30)
        let check = expectation(description: "svg")
        var svgs = 0, errors = 0
        view.evaluateJavaScript("[document.querySelectorAll('svg').length, document.querySelectorAll('pre').length]") { result, _ in
            if let pair = result as? [Int] { svgs = pair[0]; errors = pair[1] }
            check.fulfill()
        }
        wait(for: [check], timeout: 5)
        XCTAssertEqual(svgs, 1)
        XCTAssertEqual(errors, 0)
    }
}
