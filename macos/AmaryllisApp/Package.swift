// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "AmaryllisApp",
    platforms: [
        .macOS(.v13)
    ],
    products: [
        .executable(name: "AmaryllisApp", targets: ["AmaryllisApp"])
    ],
    targets: [
        .executableTarget(
            name: "AmaryllisApp",
            path: "Sources/AmaryllisApp",
            resources: [
                .process("Resources")
            ]
        )
    ]
)
