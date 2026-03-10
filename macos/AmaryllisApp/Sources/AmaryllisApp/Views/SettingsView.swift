import SwiftUI

struct SettingsView: View {
    @EnvironmentObject private var appState: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Runtime Settings")
                .font(AmaryllisTheme.titleFont(size: 30))
                .foregroundStyle(AmaryllisTheme.textPrimary)

            VStack(alignment: .leading, spacing: 10) {
                Text("API Endpoint")
                    .font(AmaryllisTheme.bodyFont(size: 12, weight: .semibold))
                    .foregroundStyle(AmaryllisTheme.textSecondary)
                TextField("http://localhost:8000", text: $appState.endpoint)
                    .textFieldStyle(.roundedBorder)

                Text("Runtime Directory")
                    .font(AmaryllisTheme.bodyFont(size: 12, weight: .semibold))
                    .foregroundStyle(AmaryllisTheme.textSecondary)
                TextField("Path to repository root", text: $appState.runtimeDirectory)
                    .textFieldStyle(.roundedBorder)

                Text("OpenAI Base URL")
                    .font(AmaryllisTheme.bodyFont(size: 12, weight: .semibold))
                    .foregroundStyle(AmaryllisTheme.textSecondary)
                TextField("https://api.openai.com/v1", text: $appState.openAIBaseURL)
                    .textFieldStyle(.roundedBorder)

                Text("OpenAI API Key")
                    .font(AmaryllisTheme.bodyFont(size: 12, weight: .semibold))
                    .foregroundStyle(AmaryllisTheme.textSecondary)
                SecureField("sk-...", text: $appState.openAIAPIKey)
                    .textFieldStyle(.roundedBorder)

                Text("OpenRouter Base URL")
                    .font(AmaryllisTheme.bodyFont(size: 12, weight: .semibold))
                    .foregroundStyle(AmaryllisTheme.textSecondary)
                TextField("https://openrouter.ai/api/v1", text: $appState.openRouterBaseURL)
                    .textFieldStyle(.roundedBorder)

                Text("OpenRouter API Key")
                    .font(AmaryllisTheme.bodyFont(size: 12, weight: .semibold))
                    .foregroundStyle(AmaryllisTheme.textSecondary)
                SecureField("or-...", text: $appState.openRouterAPIKey)
                    .textFieldStyle(.roundedBorder)

                HStack(spacing: 8) {
                    Button("Save") {
                        appState.persistSettings()
                    }
                    .buttonStyle(AmaryllisSecondaryButtonStyle())

                    Button("Start Runtime") {
                        Task { await appState.startRuntimeFromSettings() }
                    }
                    .buttonStyle(AmaryllisPrimaryButtonStyle())
                    .disabled(appState.runtimeManager.isRunning)

                    Button("Stop Runtime") {
                        Task { await appState.stopRuntimeFromSettings() }
                    }
                    .buttonStyle(AmaryllisSecondaryButtonStyle())

                    Button("Check API") {
                        Task { await appState.refreshHealth() }
                    }
                    .buttonStyle(AmaryllisSecondaryButtonStyle())
                }

                Text("After updating cloud provider keys, restart runtime.")
                    .font(AmaryllisTheme.bodyFont(size: 11, weight: .medium))
                    .foregroundStyle(AmaryllisTheme.textSecondary)

                Text("If API is already online while Runtime is offline, it means an external server is running on this endpoint.")
                    .font(AmaryllisTheme.bodyFont(size: 11, weight: .medium))
                    .foregroundStyle(AmaryllisTheme.textSecondary)

                if let error = appState.lastError {
                    Text(error)
                        .font(AmaryllisTheme.bodyFont(size: 12, weight: .medium))
                        .foregroundStyle(AmaryllisTheme.accent)
                }
            }
            .amaryllisCard()

            VStack(alignment: .leading, spacing: 8) {
                Text("Runtime Logs")
                    .font(AmaryllisTheme.sectionFont(size: 17))
                    .foregroundStyle(AmaryllisTheme.textPrimary)

                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 4) {
                        ForEach(Array(appState.runtimeManager.logs.enumerated()), id: \.offset) { _, line in
                            Text(line)
                                .font(AmaryllisTheme.monoFont(size: 11, weight: .regular))
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
