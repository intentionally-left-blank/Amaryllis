import SwiftUI

struct RootView: View {
    @EnvironmentObject private var appState: AppState

    var body: some View {
        NavigationSplitView {
            sidebar
                .navigationSplitViewColumnWidth(min: 200, ideal: 220)
        } detail: {
            VStack(spacing: 0) {
                topBar
                content
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .padding(14)
            }
            .background(AmaryllisTheme.background)
        }
        .task {
            await appState.refreshHealth()
            await appState.refreshModels()
        }
    }

    private var sidebar: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Amaryllis")
                .font(.system(size: 22, weight: .bold, design: .rounded))
                .foregroundStyle(AmaryllisTheme.textPrimary)
                .padding(.top, 8)

            ForEach(AppTab.allCases) { tab in
                Button {
                    appState.selectedTab = tab
                } label: {
                    HStack(spacing: 10) {
                        Image(systemName: tab.icon)
                            .font(.system(size: 14, weight: .semibold))
                        Text(tab.rawValue)
                            .font(.system(size: 14, weight: .semibold))
                        Spacer()
                    }
                    .padding(.vertical, 8)
                    .padding(.horizontal, 10)
                    .background(
                        RoundedRectangle(cornerRadius: 10)
                            .fill(appState.selectedTab == tab ? AmaryllisTheme.accentSoft : Color.clear)
                    )
                }
                .buttonStyle(.plain)
                .foregroundStyle(appState.selectedTab == tab ? AmaryllisTheme.textPrimary : AmaryllisTheme.textSecondary)
            }

            Spacer()
        }
        .padding(14)
        .background(AmaryllisTheme.surface)
    }

    private var topBar: some View {
        HStack(spacing: 10) {
            statusDot(label: "Runtime", on: appState.runtimeManager.isRunning)
            statusDot(label: "API", on: appState.runtimeManager.connectionState == .online)
            Spacer()
            Text(appState.endpoint)
                .font(.system(size: 12, weight: .medium))
                .foregroundStyle(AmaryllisTheme.textSecondary)
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
        .background(AmaryllisTheme.surface)
    }

    private func statusDot(label: String, on: Bool) -> some View {
        HStack(spacing: 6) {
            Circle()
                .fill(on ? Color.green : AmaryllisTheme.accent)
                .frame(width: 8, height: 8)
            Text(label)
                .font(.system(size: 11, weight: .semibold))
                .foregroundStyle(AmaryllisTheme.textSecondary)
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 5)
        .background(AmaryllisTheme.surfaceAlt)
        .clipShape(Capsule())
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
