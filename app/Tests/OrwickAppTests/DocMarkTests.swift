import XCTest
import SwiftUI
@testable import OrwickApp

final class DocMarkTests: XCTestCase {
    func testACellStartingWithAMarkGivesTheMarkAndTheRestOfTheText() {
        XCTAssertEqual(DocMark.leading("✏️ add level_id")?.mark, .change)
        XCTAssertEqual(DocMark.leading("✏️ add level_id")?.rest, "add level_id")
        XCTAssertEqual(DocMark.leading(" 🆕 ")?.mark, .new)
        XCTAssertEqual(DocMark.leading("🆕 ")?.rest, "")
        XCTAssertEqual(DocMark.leading("✅")?.mark, .exists)
        XCTAssertEqual(DocMark.leading("✏ no emoji selector")?.mark, .change)
        XCTAssertNil(DocMark.leading("To Do"))
        XCTAssertNil(DocMark.leading("read only ✅"))           // only a leading mark makes a badge
    }

    func testARowTakesItsFirstMarkAndOnlyWorkRowsAreTinted() {
        XCTAssertEqual(DocMark.ofRow(["`price_list`", "🆕", "MySQL"]), .new)
        XCTAssertNil(DocMark.ofRow(["1", "orders", "To Do"]))
        XCTAssertNil(DocMark.exists.rowTint)
        XCTAssertNotNil(DocMark.change.rowTint)
        XCTAssertNotNil(DocMark.new.rowTint)
    }

    func testMarkedLabelsInADiagramAreColoredUpToTheirEnd() {
        let line = #"JOB["✏️ orders job"] -->|"🆕 SelectPriceListsByIDs"| L[("✅ price_list")]"#
        var attributed = AttributedString(line)
        DocMark.colorLabels(in: &attributed)
        func color(of piece: String) -> Color? {
            attributed.range(of: piece).flatMap { attributed[$0].foregroundColor }
        }
        XCTAssertEqual(color(of: "orders job"), .orange)
        XCTAssertEqual(color(of: "SelectPriceListsByIDs"), .blue)
        XCTAssertEqual(color(of: "price_list"), .green)
        XCTAssertNil(color(of: "-->"))                        // the arrows stay plain
        XCTAssertNil(color(of: "JOB"))
    }
}
