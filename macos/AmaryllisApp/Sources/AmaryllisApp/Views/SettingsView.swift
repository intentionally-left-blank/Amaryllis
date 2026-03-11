import SwiftUI

struct SettingsView: View {
    @EnvironmentObject private var appState: AppState
    @State private var debugUserID: String = "user-001"
    @State private var debugAgentID: String = ""
    @State private var debugSessionID: String = ""
    @State private var debugQuery: String = ""
    @State private var debugTopK: String = "8"
    @State private var debugLimit: String = "20"
    @State private var debugOutput: String = "No debug output yet."
    @State private var isDebugLoading: Bool = false

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

            VStack(alignment: .leading, spacing: 10) {
                Text("Memory Debug")
                    .font(AmaryllisTheme.sectionFont(size: 17))
                    .foregroundStyle(AmaryllisTheme.textPrimary)

                HStack(spacing: 10) {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("User ID")
                            .font(AmaryllisTheme.bodyFont(size: 11, weight: .semibold))
                            .foregroundStyle(AmaryllisTheme.textSecondary)
                        TextField("user-001", text: $debugUserID)
                            .textFieldStyle(.roundedBorder)
                    }

                    VStack(alignment: .leading, spacing: 4) {
                        Text("Agent ID (optional)")
                            .font(AmaryllisTheme.bodyFont(size: 11, weight: .semibold))
                            .foregroundStyle(AmaryllisTheme.textSecondary)
                        TextField("agent-id", text: $debugAgentID)
                            .textFieldStyle(.roundedBorder)
                    }

                    VStack(alignment: .leading, spacing: 4) {
                        Text("Session ID (optional)")
                            .font(AmaryllisTheme.bodyFont(size: 11, weight: .semibold))
                            .foregroundStyle(AmaryllisTheme.textSecondary)
                        TextField("session-001", text: $debugSessionID)
                            .textFieldStyle(.roundedBorder)
                    }
                }

                HStack(spacing: 10) {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("Query")
                            .font(AmaryllisTheme.bodyFont(size: 11, weight: .semibold))
                            .foregroundStyle(AmaryllisTheme.textSecondary)
                        TextField("name / preferences / task", text: $debugQuery)
                            .textFieldStyle(.roundedBorder)
                    }
                    .frame(maxWidth: .infinity)

                    VStack(alignment: .leading, spacing: 4) {
                        Text("Top K")
                            .font(AmaryllisTheme.bodyFont(size: 11, weight: .semibold))
                            .foregroundStyle(AmaryllisTheme.textSecondary)
                        TextField("8", text: $debugTopK)
                            .textFieldStyle(.roundedBorder)
                            .frame(width: 80)
                    }

                    VStack(alignment: .leading, spacing: 4) {
                        Text("Limit")
                            .font(AmaryllisTheme.bodyFont(size: 11, weight: .semibold))
                            .foregroundStyle(AmaryllisTheme.textSecondary)
                        TextField("20", text: $debugLimit)
                            .textFieldStyle(.roundedBorder)
                            .frame(width: 80)
                    }
                }

                HStack(spacing: 8) {
                    Button("Context") {
                        Task { await runMemoryDebug(.context) }
                    }
                    .buttonStyle(AmaryllisSecondaryButtonStyle())
                    .disabled(isDebugLoading)

                    Button("Retrieval") {
                        Task { await runMemoryDebug(.retrieval) }
                    }
                    .buttonStyle(AmaryllisSecondaryButtonStyle())
                    .disabled(isDebugLoading)

                    Button("Extractions") {
                        Task { await runMemoryDebug(.extractions) }
                    }
                    .buttonStyle(AmaryllisSecondaryButtonStyle())
                    .disabled(isDebugLoading)

                    Button("Conflicts") {
                        Task { await runMemoryDebug(.conflicts) }
                    }
                    .buttonStyle(AmaryllisSecondaryButtonStyle())
                    .disabled(isDebugLoading)

                    Button("Clear") {
                        debugOutput = "No debug output yet."
                    }
                    .buttonStyle(AmaryllisSecondaryButtonStyle())
                    .disabled(isDebugLoading)

                    if isDebugLoading {
                        ProgressView()
                            .controlSize(.small)
                    }
                }

                ScrollView {
                    Text(debugOutput)
                        .font(AmaryllisTheme.monoFont(size: 11, weight: .regular))
                        .foregroundStyle(AmaryllisTheme.textSecondary)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .textSelection(.enabled)
                }
                .frame(maxWidth: .infinity, minHeight: 140, maxHeight: 220)
                .padding(10)
                .background(AmaryllisTheme.surface)
                .clipShape(RoundedRectangle(cornerRadius: 12))
                .overlay(
                    RoundedRectangle(cornerRadius: 12)
                        .stroke(AmaryllisTheme.border.opacity(0.35), lineWidth: 1)
                )
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

    private enum MemoryDebugAction {
        case context
        case retrieval
        case extractions
        case conflicts
    }

    private func runMemoryDebug(_ action: MemoryDebugAction) async {
        let userID = debugUserID.trimmingCharacters(in: .whitespacesAndNewlines)
        let query = debugQuery.trimmingCharacters(in: .whitespacesAndNewlines)

        guard !userID.isEmpty else {
            appState.lastError = "Memory Debug: user_id is required."
            return
        }

        isDebugLoading = true
        defer { isDebugLoading = false }

        do {
            let topK = clampInt(debugTopK, fallback: 8, min: 1, max: 64)
            let limit = clampInt(debugLimit, fallback: 20, min: 1, max: 200)

            switch action {
            case .context:
                let content = try await appState.apiClient.debugMemoryContext(
                    userId: userID,
                    agentId: emptyToNil(debugAgentID),
                    sessionId: emptyToNil(debugSessionID),
                    query: query,
                    semanticTopK: topK
                )
                debugOutput = content
            case .retrieval:
                let retrievalQuery = query.isEmpty ? "memory" : query
                let content = try await appState.apiClient.debugMemoryRetrieval(
                    userId: userID,
                    query: retrievalQuery,
                    topK: topK
                )
                debugOutput = content
            case .extractions:
                let content = try await appState.apiClient.debugMemoryExtractions(
                    userId: userID,
                    limit: limit
                )
                debugOutput = content
            case .conflicts:
                let content = try await appState.apiClient.debugMemoryConflicts(
                    userId: userID,
                    limit: limit
                )
                debugOutput = content
            }
            appState.lastError = nil
        } catch {
            appState.lastError = error.localizedDescription
            debugOutput = "Error: \(error.localizedDescription)"
        }
    }

    private func emptyToNil(_ value: String) -> String? {
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }

    private func clampInt(_ raw: String, fallback: Int, min: Int, max: Int) -> Int {
        let parsed = Int(raw.trimmingCharacters(in: .whitespacesAndNewlines)) ?? fallback
        return Swift.max(min, Swift.min(max, parsed))
    }
}
