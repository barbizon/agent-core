import XCTest
@testable import OrwickApp

final class FolderTreeTests: XCTestCase {
    private var root: URL!

    override func setUpWithError() throws {
        root = FileManager.default.temporaryDirectory.appendingPathComponent("folder-tree-\(UUID().uuidString)")
        for path in ["go/orders", "go/.orwick/sessions", "Beta", "alpha", ".hidden", "Tool.app/Contents"] {
            try FileManager.default.createDirectory(at: root.appendingPathComponent(path), withIntermediateDirectories: true)
        }
        try Data("x".utf8).write(to: root.appendingPathComponent("notes.txt"))
    }

    override func tearDownWithError() throws { try? FileManager.default.removeItem(at: root) }

    func testOnlyVisibleFoldersAreListedSortedByName() {
        XCTAssertEqual(FolderTree.subfolders(of: root).map(\.lastPathComponent), ["alpha", "Beta", "go"])
    }

    func testTheTreeOpensDownToTheLastUsedFolder() {
        let target = root.appendingPathComponent("go/orders")
        XCTAssertEqual(FolderTree.pathToShow(target, under: root),
                       [root.standardizedFileURL.path, root.appendingPathComponent("go").standardizedFileURL.path])
        XCTAssertEqual(FolderTree.pathToShow(URL(fileURLWithPath: "/elsewhere/x"), under: root), [])
    }

    func testFoldersWhereOrwickWorkedBeforeAreKnown() {
        XCTAssertTrue(FolderTree.hasSessions(root.appendingPathComponent("go")))
        XCTAssertFalse(FolderTree.hasSessions(root.appendingPathComponent("alpha")))
    }

    func testHomeIsShownAsTilde() {
        let home = FileManager.default.homeDirectoryForCurrentUser
        XCTAssertEqual(FolderTree.short(home.appendingPathComponent("Documents/go")), "~/Documents/go")
        XCTAssertEqual(FolderTree.short(home), "~")
    }
}
