import SwiftUI

enum AmaryllisTheme {
    static let background = Color(red: 0.07, green: 0.07, blue: 0.08)
    static let surface = Color(red: 0.12, green: 0.12, blue: 0.14)
    static let surfaceAlt = Color(red: 0.16, green: 0.16, blue: 0.18)
    static let border = Color(red: 0.25, green: 0.25, blue: 0.28)
    static let accent = Color(red: 0.66, green: 0.09, blue: 0.18)
    static let accentSoft = Color(red: 0.48, green: 0.08, blue: 0.16)
    static let textPrimary = Color(red: 0.95, green: 0.95, blue: 0.96)
    static let textSecondary = Color(red: 0.72, green: 0.72, blue: 0.75)
}

extension View {
    func amaryllisCard() -> some View {
        self
            .padding(14)
            .background(AmaryllisTheme.surface)
            .overlay(
                RoundedRectangle(cornerRadius: 14)
                    .stroke(AmaryllisTheme.border.opacity(0.4), lineWidth: 1)
            )
            .clipShape(RoundedRectangle(cornerRadius: 14))
    }
}
