import SwiftUI

struct SettingsView: View {
    @EnvironmentObject private var appState: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Runtime Settings")
                .font(.system(size: 26, weight: .black, design: .rounded))
                .foregroundStyle(AmaryllisTheme.textPrimary)

            VStack(alignment: .leading, spacing: 10) {
                Text("API Endpoint")
                    .font(.system(size: 12, weight: .bold))
                    .foregroundStyle(AmaryllisTheme.textSecondary)
                TextField("http://localhost:8000", text: $appState.endpoint)
                    .textFieldStyle(.roundedBorder)

                Text("Runtime Directory")
                    .font(.system(size: 12, weight: .bold))
                    .foregroundStyle(AmaryllisTheme.textSecondary)
                TextField("Path to repository root", text: $appState.runtimeDirectory)
                    .textFieldStyle(.roundedBorder)

                HStack(spacing: 8) {
                    Button("Save") {
                        appState.persistSettings()
                    }
                    .buttonStyle(.bordered)

                    Button("Start Runtime") {
                        appState.startRuntime()
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(AmaryllisTheme.accent)
                    .disabled(appState.runtimeManager.isRunning)

                    Button("Stop Runtime") {
                        appState.stopRuntime()
                    }
                    .buttonStyle(.bordered)
                    .disabled(!appState.runtimeManager.isRunning)

                    Button("Check API") {
                        Task { await appState.refreshHealth() }
                    }
                    .buttonStyle(.bordered)
                }

                if let error = appState.lastError {
                    Text(error)
                        .font(.system(size: 12, weight: .medium))
                        .foregroundStyle(AmaryllisTheme.accent)
                }
            }
            .amaryllisCard()

            VStack(alignment: .leading, spacing: 8) {
                Text("Runtime Logs")
                    .font(.system(size: 14, weight: .bold))
                    .foregroundStyle(AmaryllisTheme.textPrimary)

                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 4) {
                        ForEach(Array(appState.runtimeManager.logs.enumerated()), id: \.offset) { _, line in
                            Text(line)
                                .font(.system(size: 11, weight: .regular, design: .monospaced))
                                .foregroundStyle(AmaryllisTheme.textSecondary)
                                .frame(maxWidth: .infinity, alignment: .leading)
                        }
                    }
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .padding(10)
                .background(AmaryllisTheme.surface)
                .clipShape(RoundedRectangle(cornerRadius: 12))
                .overlay(
                    RoundedRectangle(cornerRadius: 12)
                        .stroke(AmaryllisTheme.border.opacity(0.35), lineWidth: 1)
                )
            }
            .amaryllisCard()
            .frame(maxHeight: .infinity)
        }
    }
}
