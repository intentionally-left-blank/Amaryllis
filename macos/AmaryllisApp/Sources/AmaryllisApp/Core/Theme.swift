import SwiftUI

enum AmaryllisTheme {
    static let background = Color(red: 0.02, green: 0.025, blue: 0.022)
    static let backgroundMid = Color(red: 0.035, green: 0.04, blue: 0.036)
    static let surface = Color(red: 0.07, green: 0.08, blue: 0.074)
    static let surfaceAlt = Color(red: 0.10, green: 0.112, blue: 0.102)
    static let border = Color(red: 0.27, green: 0.31, blue: 0.28)
    static let borderSoft = Color(red: 0.17, green: 0.20, blue: 0.18)
    static let accent = Color(red: 0.74, green: 0.11, blue: 0.13)
    static let accentSoft = Color(red: 0.25, green: 0.07, blue: 0.08)
    static let phosphor = Color(red: 0.76, green: 0.86, blue: 0.73)
    static let phosphorDim = Color(red: 0.54, green: 0.62, blue: 0.52)
    static let amber = Color(red: 0.88, green: 0.72, blue: 0.46)
    static let textPrimary = phosphor
    static let textSecondary = phosphorDim
    static let inputBackground = Color(red: 0.04, green: 0.05, blue: 0.045)
    static let inputBorder = Color(red: 0.33, green: 0.40, blue: 0.34)
    static let okGreen = Color(red: 0.42, green: 0.86, blue: 0.54)

    private static let terminalFontName: String = AmaryllisFontRegistry.resolvedFontName()

    static func titleFont(size: CGFloat = 24) -> Font {
        Font.custom(terminalFontName, size: size)
    }

    static func sectionFont(size: CGFloat = 17) -> Font {
        Font.custom(terminalFontName, size: size)
    }

    static func bodyFont(size: CGFloat = 13, weight: Font.Weight = .regular) -> Font {
        Font.custom(terminalFontName, size: size).weight(weight)
    }

    static func monoFont(size: CGFloat = 11, weight: Font.Weight = .regular) -> Font {
        Font.custom(terminalFontName, size: size).weight(weight)
    }
}

struct AmaryllisTerminalBackground: View {
    var body: some View {
        ZStack {
            LinearGradient(
                colors: [AmaryllisTheme.background, AmaryllisTheme.backgroundMid, AmaryllisTheme.background],
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            )
            .ignoresSafeArea()

            GeometryReader { geometry in
                let lineStep: CGFloat = 3
                Canvas { context, size in
                    var path = Path()
                    var y: CGFloat = 0
                    while y < size.height {
                        path.addRect(CGRect(x: 0, y: y, width: size.width, height: 1))
                        y += lineStep
                    }
                    context.fill(path, with: .color(Color.black.opacity(0.20)))
                }
                .frame(width: geometry.size.width, height: geometry.size.height)
                .allowsHitTesting(false)
            }

            RadialGradient(
                colors: [Color.white.opacity(0.04), Color.clear],
                center: .center,
                startRadius: 40,
                endRadius: 900
            )
            .ignoresSafeArea()
            .blendMode(.screen)
            .allowsHitTesting(false)
        }
    }
}

struct AmaryllisPrimaryButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(AmaryllisTheme.bodyFont(size: 13, weight: .semibold))
            .foregroundStyle(AmaryllisTheme.phosphor.opacity(configuration.isPressed ? 0.82 : 1.0))
            .padding(.horizontal, 12)
            .padding(.vertical, 6)
            .background(
                RoundedRectangle(cornerRadius: 3)
                    .fill(configuration.isPressed ? AmaryllisTheme.accentSoft.opacity(0.84) : AmaryllisTheme.accentSoft)
            )
            .overlay(
                RoundedRectangle(cornerRadius: 3)
                    .stroke(AmaryllisTheme.accent, lineWidth: 1)
            )
    }
}

struct AmaryllisSecondaryButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(AmaryllisTheme.bodyFont(size: 13, weight: .semibold))
            .foregroundStyle(AmaryllisTheme.textPrimary.opacity(configuration.isPressed ? 0.78 : 1.0))
            .padding(.horizontal, 12)
            .padding(.vertical, 6)
            .background(
                RoundedRectangle(cornerRadius: 3)
                    .fill(configuration.isPressed ? AmaryllisTheme.surface : AmaryllisTheme.surfaceAlt)
            )
            .overlay(
                RoundedRectangle(cornerRadius: 3)
                    .stroke(AmaryllisTheme.border.opacity(0.9), lineWidth: 1)
            )
    }
}

struct AmaryllisTerminalTextFieldStyle: TextFieldStyle {
    func _body(configuration: TextField<Self._Label>) -> some View {
        configuration
            .font(AmaryllisTheme.bodyFont(size: 13, weight: .regular))
            .foregroundStyle(AmaryllisTheme.textPrimary)
            .padding(.horizontal, 8)
            .padding(.vertical, 6)
            .background(
                RoundedRectangle(cornerRadius: 3)
                    .fill(AmaryllisTheme.inputBackground)
            )
            .overlay(
                RoundedRectangle(cornerRadius: 3)
                    .stroke(AmaryllisTheme.inputBorder, lineWidth: 1)
            )
    }
}

extension View {
    func amaryllisCard() -> some View {
        self
            .padding(12)
            .background(
                RoundedRectangle(cornerRadius: 6)
                    .fill(AmaryllisTheme.surface)
                    .overlay(
                        RoundedRectangle(cornerRadius: 6)
                            .stroke(AmaryllisTheme.borderSoft, lineWidth: 1)
                    )
            )
            .overlay(alignment: .topLeading) {
                Rectangle()
                    .fill(AmaryllisTheme.border.opacity(0.55))
                    .frame(height: 1)
            }
    }

    func amaryllisEditorSurface() -> some View {
        self
            .padding(6)
            .background(
                RoundedRectangle(cornerRadius: 4)
                    .fill(AmaryllisTheme.inputBackground)
                    .overlay(
                        RoundedRectangle(cornerRadius: 4)
                            .stroke(AmaryllisTheme.inputBorder, lineWidth: 1)
                    )
            )
    }
}
