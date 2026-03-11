import SwiftUI

@main
struct AmaryllisMacApp: App {
    @StateObject private var appState = AppState()

    init() {
        AmaryllisFontRegistry.registerBundledFonts()
    }

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(appState)
                .preferredColorScheme(.dark)
                .frame(minWidth: 1120, minHeight: 760)
                .background(AmaryllisTheme.background)
        }
        .commands {
            CommandGroup(replacing: .newItem) { }
        }
    }
}
