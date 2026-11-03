// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "ITDBKit",
    platforms: [
        .macOS(.v14)
    ],
    products: [
        .executable(name: "ipodctl", targets: ["ipodctl"]),
        .executable(name: "AppLauncher", targets: ["AppLauncher"]),
    ],
    targets: [
        .target(name: "ITDBKit"),
        // What a write to the iPod is: the change queue, the transaction and its phases, imports.
        .target(name: "RePodCore", dependencies: ["ITDBKit"]),
        .executableTarget(name: "ipodctl", dependencies: ["ITDBKit", "RePodCore"]),
        // The double-clickable app: starts the page's server and stays in the Dock.
        .executableTarget(name: "AppLauncher", swiftSettings: [.swiftLanguageMode(.v5)]),
        .testTarget(name: "ITDBKitTests", dependencies: ["ITDBKit"]),
        // The `ipodctl` command tests use the fake iPod from the write tests.
        .testTarget(name: "RePodCoreTests", dependencies: ["RePodCore", "ITDBKit", "ipodctl"]),
    ]
)
