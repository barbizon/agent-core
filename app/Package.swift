// swift-tools-version:5.10
import PackageDescription

let package = Package(
    name: "OrwickApp",
    platforms: [.macOS(.v14)],
    dependencies: [
        .package(url: "https://github.com/migueldeicaza/SwiftTerm", from: "1.2.0"),
    ],
    targets: [
        .executableTarget(
            name: "OrwickApp",
            dependencies: [.product(name: "SwiftTerm", package: "SwiftTerm")]
        ),
        .testTarget(name: "OrwickAppTests", dependencies: ["OrwickApp"], resources: [.copy("Fixtures")]),
    ]
)
