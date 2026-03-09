import SwiftUI

struct RootView: View {
    @EnvironmentObject private var appState: AppState

    var body: some View {
        NavigationSplitView {
            sidebar
                .navigationSplitViewColumnWidth(min: 220, ideal: 240)
        } detail: {
            VStack(spacing: 0) {
                topBar
                Divider().overlay(AmaryllisTheme.border)
                content
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .padding(16)
            }
            .background(AmaryllisTheme.background)
        }
        .task {
            await appState.refreshHealth()
            await appState.refreshModels()
        }
    }

    private var sidebar: some View {
        ZStack {
            LinearGradient(
                colors: [AmaryllisTheme.background, AmaryllisTheme.surface],
                startPoint: .top,
                endPoint: .bottom
            )
            .ignoresSafeArea()

            VStack(alignment: .leading, spacing: 18) {
                VStack(alignment: .leading, spacing: 4) {
                    Text("AMARYLLIS")
                        .font(.system(size: 22, weight: .black, design: .rounded))
                        .foregroundStyle(AmaryllisTheme.textPrimary)
                    Text("Local AI Brain Node")
                        .font(.system(size: 12, weight: .medium))
                        .foregroundStyle(AmaryllisTheme.textSecondary)
                }
                .padding(.top, 8)

                VStack(alignment: .leading, spacing: 8) {
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
                            .padding(.vertical, 9)
                            .padding(.horizontal, 10)
                            .background(
                                RoundedRectangle(cornerRadius: 10)
                                    .fill(appState.selectedTab == tab ? AmaryllisTheme.accentSoft : Color.clear)
                            )
                            .overlay(
                                RoundedRectangle(cornerRadius: 10)
                                    .stroke(appState.selectedTab == tab ? AmaryllisTheme.accent : Color.clear, lineWidth: 1)
                            )
                        }
                        .buttonStyle(.plain)
                        .foregroundStyle(appState.selectedTab == tab ? AmaryllisTheme.textPrimary : AmaryllisTheme.textSecondary)
                    }
                }

                Spacer()

                Text("anonymous local runtime")
                    .font(.system(size: 11, weight: .regular))
                    .foregroundStyle(AmaryllisTheme.textSecondary)
            }
            .padding(16)
        }
    }

    private var topBar: some View {
        HStack(spacing: 12) {
            statusPill(
                title: "Runtime",
                value: appState.runtimeManager.processState.rawValue.uppercased(),
                isPositive: appState.runtimeManager.isRunning
            )

            statusPill(
                title: "API",
                value: appState.runtimeManager.connectionState.rawValue.uppercased(),
                isPositive: appState.runtimeManager.connectionState == .online
            )

            Spacer()

            Text(appState.endpoint)
                .font(.system(size: 12, weight: .medium))
                .foregroundStyle(AmaryllisTheme.textSecondary)
                .padding(.horizontal, 10)
                .padding(.vertical, 6)
                .background(AmaryllisTheme.surface)
                .clipShape(Capsule())
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
        .background(AmaryllisTheme.surface.opacity(0.65))
    }

    private func statusPill(title: String, value: String, isPositive: Bool) -> some View {
        HStack(spacing: 8) {
            Circle()
                .fill(isPositive ? Color.green : AmaryllisTheme.accent)
                .frame(width: 8, height: 8)
            Text(title)
                .font(.system(size: 11, weight: .semibold))
                .foregroundStyle(AmaryllisTheme.textSecondary)
            Text(value)
                .font(.system(size: 11, weight: .bold))
                .foregroundStyle(AmaryllisTheme.textPrimary)
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 6)
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
