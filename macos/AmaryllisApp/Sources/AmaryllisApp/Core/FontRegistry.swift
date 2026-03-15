import AppKit
import CoreText
import Foundation

enum AmaryllisFontRegistry {
    static let preferredPostScriptName = "Mx437_OlivettiThin_9x14"

    private static var didRegister = false
    private static let bundledFileNames: [String] = [
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
        var bundles: [Bundle] = [Bundle.main]
        #if SWIFT_PACKAGE
        bundles.insert(Bundle.module, at: 0)
        #endif

        var found: [URL] = []
        var seen: Set<String> = []
        for fileName in bundledFileNames {
            let nsName = fileName as NSString
            let stem = nsName.deletingPathExtension
            let ext = nsName.pathExtension

            for bundle in bundles {
                let candidates = [
                    bundle.url(forResource: stem, withExtension: ext, subdirectory: "Fonts"),
                    bundle.url(forResource: stem, withExtension: ext, subdirectory: "Resources/Fonts"),
                    bundle.url(forResource: stem, withExtension: ext),
                ]
                for maybeURL in candidates {
                    guard let url = maybeURL else { continue }
                    let normalized = url.standardizedFileURL.path
                    guard seen.insert(normalized).inserted else { continue }
                    found.append(url)
                }
            }
        }
        return found
    }
}
