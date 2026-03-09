import SwiftUI

enum AmaryllisTheme {
    static let background = Color(red: 0.06, green: 0.06, blue: 0.07)
    static let surface = Color(red: 0.10, green: 0.10, blue: 0.11)
    static let surfaceAlt = Color(red: 0.14, green: 0.14, blue: 0.16)
    static let border = Color(red: 0.22, green: 0.22, blue: 0.24)
    static let accent = Color(red: 0.68, green: 0.10, blue: 0.20)
    static let accentSoft = Color(red: 0.33, green: 0.07, blue: 0.12)
    static let textPrimary = Color(red: 0.95, green: 0.95, blue: 0.96)
    static let textSecondary = Color(red: 0.70, green: 0.70, blue: 0.73)
}

extension View {
    func amaryllisCard() -> some View {
        self
            .padding(12)
            .background(AmaryllisTheme.surface)
            .overlay(
                RoundedRectangle(cornerRadius: 12)
                    .stroke(AmaryllisTheme.border.opacity(0.35), lineWidth: 1)
            )
            .clipShape(RoundedRectangle(cornerRadius: 12))
    }
}
