import AppKit
import CoreText
import Foundation

enum AmaryllisFontRegistry {
    static let preferredPostScriptName = "Px437_OlivettiThin_9x14"

    private static var didRegister = false

    static func registerBundledFonts() {
        guard !didRegister else { return }
        didRegister = true

        let fontURLs = Bundle.module.urls(forResourcesWithExtension: "ttf", subdirectory: "Fonts") ?? []
        for url in fontURLs {
            CTFontManagerRegisterFontsForURL(url as CFURL, .process, nil)
        }
    }

    static func resolvedFontName() -> String {
        let candidates = [
            preferredPostScriptName,
            "Px437 OlivettiThin 9x14",
            "Px437_OlivettiThin_9x14",
        ]

        for candidate in candidates where NSFont(name: candidate, size: 12) != nil {
            return candidate
        }
        return "Menlo"
    }
}
