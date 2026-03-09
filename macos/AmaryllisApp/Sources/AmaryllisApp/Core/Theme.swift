import SwiftUI

enum AmaryllisTheme {
    static let background = Color(red: 0.04, green: 0.04, blue: 0.05)
    static let surface = Color(red: 0.09, green: 0.09, blue: 0.10)
    static let surfaceAlt = Color(red: 0.13, green: 0.13, blue: 0.15)
    static let border = Color(red: 0.24, green: 0.24, blue: 0.26)
    static let accent = Color(red: 0.64, green: 0.09, blue: 0.17)
    static let accentSoft = Color(red: 0.26, green: 0.06, blue: 0.11)
    static let textPrimary = Color(red: 0.95, green: 0.95, blue: 0.96)
    static let textSecondary = Color(red: 0.70, green: 0.70, blue: 0.73)

    static func titleFont(size: CGFloat = 28) -> Font {
        Font.custom("Didot", size: size).weight(.semibold)
    }

    static func sectionFont(size: CGFloat = 19) -> Font {
        Font.custom("Didot", size: size).weight(.medium)
    }

    static func bodyFont(size: CGFloat = 13, weight: Font.Weight = .regular) -> Font {
        Font.custom("Avenir Next", size: size).weight(weight)
    }

    static func monoFont(size: CGFloat = 11, weight: Font.Weight = .regular) -> Font {
        Font.system(size: size, weight: weight, design: .monospaced)
    }
}

struct AmaryllisPrimaryButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(AmaryllisTheme.bodyFont(size: 13, weight: .semibold))
            .foregroundStyle(AmaryllisTheme.textPrimary.opacity(configuration.isPressed ? 0.85 : 1.0))
            .padding(.horizontal, 12)
            .padding(.vertical, 7)
            .background(
                RoundedRectangle(cornerRadius: 10)
                    .fill(configuration.isPressed ? AmaryllisTheme.accentSoft.opacity(0.85) : AmaryllisTheme.accentSoft)
            )
            .overlay(
                RoundedRectangle(cornerRadius: 10)
                    .stroke(AmaryllisTheme.accent.opacity(0.95), lineWidth: 1)
            )
    }
}

struct AmaryllisSecondaryButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(AmaryllisTheme.bodyFont(size: 13, weight: .semibold))
            .foregroundStyle(AmaryllisTheme.textPrimary.opacity(configuration.isPressed ? 0.82 : 1.0))
            .padding(.horizontal, 12)
            .padding(.vertical, 7)
            .background(
                RoundedRectangle(cornerRadius: 10)
                    .fill(configuration.isPressed ? AmaryllisTheme.surface.opacity(0.95) : AmaryllisTheme.surfaceAlt)
            )
            .overlay(
                RoundedRectangle(cornerRadius: 10)
                    .stroke(AmaryllisTheme.border.opacity(0.8), lineWidth: 1)
            )
    }
}

extension View {
    func amaryllisCard() -> some View {
        self
            .padding(12)
            .background(AmaryllisTheme.surface)
            .overlay(
                RoundedRectangle(cornerRadius: 12)
                    .stroke(AmaryllisTheme.border.opacity(0.4), lineWidth: 1)
            )
            .clipShape(RoundedRectangle(cornerRadius: 12))
    }
}
