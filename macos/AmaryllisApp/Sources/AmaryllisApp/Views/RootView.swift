import SwiftUI

struct RootView: View {
    @EnvironmentObject private var appState: AppState

    var body: some View {
        ZStack {
            AmaryllisTerminalBackground()
            NavigationSplitView {
                sidebar
                    .navigationSplitViewColumnWidth(min: 215, ideal: 235)
            } detail: {
                VStack(spacing: 0) {
                    topBar
                    content
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                        .padding(14)
                }
                .background(Color.clear)
            }
            .background(Color.clear)
        }
        .task {
            await appState.refreshHealth()
            await appState.refreshModels()
        }
    }

    private var sidebar: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Amaryllis")
                .font(AmaryllisTheme.titleFont(size: 30))
                .tracking(1.2)
                .foregroundStyle(AmaryllisTheme.textPrimary)
                .padding(.top, 8)

            ForEach(AppTab.allCases) { tab in
                Button {
                    appState.selectedTab = tab
                } label: {
                    HStack(spacing: 10) {
                        Image(systemName: tab.icon)
                            .font(.system(size: 14, weight: .medium))
                        Text(tab.rawValue.uppercased())
                            .font(AmaryllisTheme.bodyFont(size: 14, weight: .semibold))
                            .tracking(0.7)
                        Spacer()
                    }
                    .padding(.vertical, 8)
                    .padding(.horizontal, 10)
                    .background(
                        RoundedRectangle(cornerRadius: 3)
                            .fill(appState.selectedTab == tab ? AmaryllisTheme.accentSoft : Color.clear)
                    )
                    .overlay(
                        RoundedRectangle(cornerRadius: 3)
                            .stroke(
                                appState.selectedTab == tab ? AmaryllisTheme.accent.opacity(0.9) : AmaryllisTheme.borderSoft,
                                lineWidth: 1
                            )
                    )
                }
                .buttonStyle(.plain)
                .foregroundStyle(appState.selectedTab == tab ? AmaryllisTheme.textPrimary : AmaryllisTheme.textSecondary)
            }

            Spacer()
        }
        .padding(14)
        .background(
            RoundedRectangle(cornerRadius: 0)
                .fill(AmaryllisTheme.surface.opacity(0.92))
        )
        .overlay(alignment: .trailing) {
            Rectangle()
                .fill(AmaryllisTheme.borderSoft)
                .frame(width: 1)
        }
    }

    private var topBar: some View {
        HStack(spacing: 10) {
            statusDot(label: "RUNTIME", on: appState.runtimeManager.isRunning)
            statusDot(label: "API", on: appState.runtimeManager.connectionState == .online)
            Spacer()
            Text(appState.endpoint)
                .font(AmaryllisTheme.monoFont(size: 12, weight: .regular))
                .foregroundStyle(AmaryllisTheme.textSecondary)
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
        .background(AmaryllisTheme.surface.opacity(0.86))
        .overlay(alignment: .bottom) {
            Rectangle()
                .fill(AmaryllisTheme.borderSoft)
                .frame(height: 1)
        }
    }

    private func statusDot(label: String, on: Bool) -> some View {
        HStack(spacing: 6) {
            Rectangle()
                .fill(on ? AmaryllisTheme.okGreen : AmaryllisTheme.accent)
                .frame(width: 8, height: 8)
            Text(label)
                .font(AmaryllisTheme.bodyFont(size: 11, weight: .semibold))
                .foregroundStyle(AmaryllisTheme.textSecondary)
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 5)
        .background(AmaryllisTheme.surfaceAlt)
        .overlay(
            RoundedRectangle(cornerRadius: 3)
                .stroke(AmaryllisTheme.border.opacity(0.75), lineWidth: 1)
        )
        .clipShape(RoundedRectangle(cornerRadius: 3))
    }

    @ViewBuilder
    private var content: some View {
        switch appState.selectedTab {
        case .chat:
            ChatView()
        case .models:
            ModelsView()
        case .agents:
            AgentsView()
        case .settings:
            SettingsView()
        }
    }
}
