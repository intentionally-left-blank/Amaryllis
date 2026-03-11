import AppKit
import CoreText
import Foundation

enum AmaryllisFontRegistry {
    static let preferredPostScriptName = "Mx437_OlivettiThin_9x14"

    private static var didRegister = false
    private static let bundledFileNames: Set<String> = [
        "Mx437_OlivettiThin_9x14.ttf",
        "Ac437_OlivettiThin_9x14.ttf",
        "Px437_OlivettiThin_9x14.ttf",
    ]

    static func registerBundledFonts() {
        guard !didRegister else { return }
        didRegister = true

        for url in discoverBundledFontURLs() {
            CTFontManagerRegisterFontsForURL(url as CFURL, .process, nil)
        }
    }

    static func resolvedFontName() -> String {
        let candidates = [
            preferredPostScriptName,
            "Mx437_OlivettiThin_9x14",
            "Ac437_OlivettiThin_9x14",
            "Px437_OlivettiThin_9x14",
            "Mx437 OlivettiThin 9x14",
            "Ac437 OlivettiThin 9x14",
            "Px437 OlivettiThin 9x14",
            "Px437_OlivettiThin_9x14",
        ]

        for candidate in candidates where NSFont(name: candidate, size: 12) != nil {
            return candidate
        }
        return "Menlo"
    }

    private static func discoverBundledFontURLs() -> [URL] {
        let fileManager = FileManager.default
        var roots: [URL] = []
        if let url = Bundle.main.resourceURL {
            roots.append(url)
        }
        roots.append(Bundle.main.bundleURL)
        if let url = Bundle.main.executableURL?.deletingLastPathComponent() {
            roots.append(url)
        }
        for bundle in Bundle.allBundles + Bundle.allFrameworks {
            if let url = bundle.resourceURL {
                roots.append(url)
            }
        }

        var found: [URL] = []
        var seen: Set<String> = []

        for root in roots {
            guard let enumerator = fileManager.enumerator(
                at: root,
                includingPropertiesForKeys: [.isRegularFileKey],
                options: [.skipsHiddenFiles],
                errorHandler: nil
            ) else {
                continue
            }

            for case let fileURL as URL in enumerator {
                guard fileURL.pathExtension.lowercased() == "ttf" else {
                    continue
                }
                guard bundledFileNames.contains(fileURL.lastPathComponent) else {
                    continue
                }
                let normalized = fileURL.standardizedFileURL.path
                guard seen.insert(normalized).inserted else {
                    continue
                }
                found.append(fileURL)
            }
        }

        return found
    }
}
