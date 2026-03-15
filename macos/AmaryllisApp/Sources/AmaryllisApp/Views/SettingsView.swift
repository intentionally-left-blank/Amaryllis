import Foundation
import SwiftUI

struct SettingsView: View {
    @EnvironmentObject private var appState: AppState
    @State private var debugUserID: String = "user-001"
    @State private var debugAgentID: String = ""
    @State private var debugSessionID: String = ""
    @State private var debugQuery: String = ""
    @State private var debugTopK: String = "8"
    @State private var debugLimit: String = "20"
    @State private var memoryDebugStatus: String = "No memory debug data yet."
    @State private var memoryContext: APIMemoryContextResponse?
    @State private var memoryRetrieval: APIMemoryRetrievalResponse?
    @State private var memoryExtractions: APIMemoryExtractionsResponse?
    @State private var memoryConflicts: APIMemoryConflictsResponse?
    @State private var showMemoryRawJSON: Bool = false
    @State private var memoryRawJSON: String = "{}"
    @State private var isDebugLoading: Bool = false
    @State private var isToolsLoading: Bool = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                Text("Runtime Settings")
                    .font(AmaryllisTheme.titleFont(size: 30))
                    .foregroundStyle(AmaryllisTheme.textPrimary)

            VStack(alignment: .leading, spacing: 10) {
                Text("API Endpoint")
                    .font(AmaryllisTheme.bodyFont(size: 12, weight: .semibold))
                    .foregroundStyle(AmaryllisTheme.textSecondary)
                TextField("http://localhost:8000", text: $appState.endpoint)
                    .textFieldStyle(AmaryllisTerminalTextFieldStyle())

                Text("Runtime Directory")
                    .font(AmaryllisTheme.bodyFont(size: 12, weight: .semibold))
                    .foregroundStyle(AmaryllisTheme.textSecondary)
                TextField("Path to repository root", text: $appState.runtimeDirectory)
                    .textFieldStyle(AmaryllisTerminalTextFieldStyle())

                Text("Runtime Auth Token")
                    .font(AmaryllisTheme.bodyFont(size: 12, weight: .semibold))
                    .foregroundStyle(AmaryllisTheme.textSecondary)
                SecureField("Bearer token for runtime API", text: $appState.runtimeAuthToken)
                    .textFieldStyle(AmaryllisTerminalTextFieldStyle())

                Text("OpenAI Base URL")
                    .font(AmaryllisTheme.bodyFont(size: 12, weight: .semibold))
                    .foregroundStyle(AmaryllisTheme.textSecondary)
                TextField("https://api.openai.com/v1", text: $appState.openAIBaseURL)
                    .textFieldStyle(AmaryllisTerminalTextFieldStyle())

                Text("OpenAI API Key")
                    .font(AmaryllisTheme.bodyFont(size: 12, weight: .semibold))
                    .foregroundStyle(AmaryllisTheme.textSecondary)
                SecureField("sk-...", text: $appState.openAIAPIKey)
                    .textFieldStyle(AmaryllisTerminalTextFieldStyle())

                Text("OpenRouter Base URL")
                    .font(AmaryllisTheme.bodyFont(size: 12, weight: .semibold))
                    .foregroundStyle(AmaryllisTheme.textSecondary)
                TextField("https://openrouter.ai/api/v1", text: $appState.openRouterBaseURL)
                    .textFieldStyle(AmaryllisTerminalTextFieldStyle())

                Text("OpenRouter API Key")
                    .font(AmaryllisTheme.bodyFont(size: 12, weight: .semibold))
                    .foregroundStyle(AmaryllisTheme.textSecondary)
                SecureField("or-...", text: $appState.openRouterAPIKey)
                    .textFieldStyle(AmaryllisTerminalTextFieldStyle())

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
                Text("Tools & MCP")
                    .font(AmaryllisTheme.sectionFont(size: 17))
                    .foregroundStyle(AmaryllisTheme.textPrimary)

                HStack(spacing: 10) {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("Approval Mode")
                            .font(AmaryllisTheme.bodyFont(size: 11, weight: .semibold))
                            .foregroundStyle(AmaryllisTheme.textSecondary)
                        Picker("Approval Mode", selection: $appState.toolApprovalEnforcement) {
                            Text("prompt_and_allow").tag("prompt_and_allow")
                            Text("strict").tag("strict")
                        }
                        .pickerStyle(.menu)
                        .frame(width: 220)
                    }

                    VStack(alignment: .leading, spacing: 4) {
                        Text("Isolation Profile")
                            .font(AmaryllisTheme.bodyFont(size: 11, weight: .semibold))
                            .foregroundStyle(AmaryllisTheme.textSecondary)
                        Picker("Isolation Profile", selection: $appState.toolIsolationProfile) {
                            Text("balanced").tag("balanced")
                            Text("strict").tag("strict")
                        }
                        .pickerStyle(.menu)
                        .frame(width: 180)
                    }

                    VStack(alignment: .leading, spacing: 4) {
                        Text("MCP Timeout (sec)")
                            .font(AmaryllisTheme.bodyFont(size: 11, weight: .semibold))
                            .foregroundStyle(AmaryllisTheme.textSecondary)
                        TextField("10", text: $appState.mcpTimeoutSec)
                            .textFieldStyle(AmaryllisTerminalTextFieldStyle())
                            .frame(width: 140)
                    }

                    Spacer()
                }

                VStack(alignment: .leading, spacing: 4) {
                    Text("Blocked Tools (comma-separated)")
                        .font(AmaryllisTheme.bodyFont(size: 11, weight: .semibold))
                        .foregroundStyle(AmaryllisTheme.textSecondary)
                    TextField("python_exec,filesystem", text: $appState.blockedTools)
                        .textFieldStyle(AmaryllisTerminalTextFieldStyle())
                }

                VStack(alignment: .leading, spacing: 4) {
                    Text("Allowed High-Risk Tools (comma-separated)")
                        .font(AmaryllisTheme.bodyFont(size: 11, weight: .semibold))
                        .foregroundStyle(AmaryllisTheme.textSecondary)
                    TextField("python_exec", text: $appState.allowedHighRiskTools)
                        .textFieldStyle(AmaryllisTerminalTextFieldStyle())
                }

                HStack(spacing: 10) {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("python_exec max timeout (sec)")
                            .font(AmaryllisTheme.bodyFont(size: 11, weight: .semibold))
                            .foregroundStyle(AmaryllisTheme.textSecondary)
                        TextField("10", text: $appState.toolPythonExecMaxTimeoutSec)
                            .textFieldStyle(AmaryllisTerminalTextFieldStyle())
                            .frame(width: 180)
                    }

                    VStack(alignment: .leading, spacing: 4) {
                        Text("python_exec max code chars")
                            .font(AmaryllisTheme.bodyFont(size: 11, weight: .semibold))
                            .foregroundStyle(AmaryllisTheme.textSecondary)
                        TextField("4000", text: $appState.toolPythonExecMaxCodeChars)
                            .textFieldStyle(AmaryllisTerminalTextFieldStyle())
                            .frame(width: 220)
                    }

                    Toggle("Allow filesystem write", isOn: $appState.toolFilesystemAllowWrite)
                        .toggleStyle(.switch)
                        .font(AmaryllisTheme.bodyFont(size: 11, weight: .semibold))
                        .frame(width: 190)

                    Spacer()
                }

                HStack(spacing: 10) {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("Tool budget window (sec)")
                            .font(AmaryllisTheme.bodyFont(size: 11, weight: .semibold))
                            .foregroundStyle(AmaryllisTheme.textSecondary)
                        TextField("60", text: $appState.toolBudgetWindowSec)
                            .textFieldStyle(AmaryllisTerminalTextFieldStyle())
                            .frame(width: 170)
                    }

                    VStack(alignment: .leading, spacing: 4) {
                        Text("Max calls per tool")
                            .font(AmaryllisTheme.bodyFont(size: 11, weight: .semibold))
                            .foregroundStyle(AmaryllisTheme.textSecondary)
                        TextField("12", text: $appState.toolBudgetMaxCallsPerTool)
                            .textFieldStyle(AmaryllisTerminalTextFieldStyle())
                            .frame(width: 140)
                    }

                    VStack(alignment: .leading, spacing: 4) {
                        Text("Max total tool calls")
                            .font(AmaryllisTheme.bodyFont(size: 11, weight: .semibold))
                            .foregroundStyle(AmaryllisTheme.textSecondary)
                        TextField("40", text: $appState.toolBudgetMaxTotalCalls)
                            .textFieldStyle(AmaryllisTerminalTextFieldStyle())
                            .frame(width: 160)
                    }

                    VStack(alignment: .leading, spacing: 4) {
                        Text("Max high-risk calls")
                            .font(AmaryllisTheme.bodyFont(size: 11, weight: .semibold))
                            .foregroundStyle(AmaryllisTheme.textSecondary)
                        TextField("4", text: $appState.toolBudgetMaxHighRiskCalls)
                            .textFieldStyle(AmaryllisTerminalTextFieldStyle())
                            .frame(width: 150)
                    }

                    Spacer()
                }

                Text(isolationHintText())
                    .font(AmaryllisTheme.bodyFont(size: 11, weight: .medium))
                    .foregroundStyle(AmaryllisTheme.textSecondary)

                VStack(alignment: .leading, spacing: 4) {
                    Text("MCP Endpoints (comma-separated)")
                        .font(AmaryllisTheme.bodyFont(size: 11, weight: .semibold))
                        .foregroundStyle(AmaryllisTheme.textSecondary)
                    TextField("http://localhost:3001,http://localhost:3002", text: $appState.mcpEndpoints)
                        .textFieldStyle(AmaryllisTerminalTextFieldStyle())
                }

                VStack(alignment: .leading, spacing: 4) {
                    Text("Plugin Signing Key (optional)")
                        .font(AmaryllisTheme.bodyFont(size: 11, weight: .semibold))
                        .foregroundStyle(AmaryllisTheme.textSecondary)
                    SecureField("hmac-shared-secret", text: $appState.pluginSigningKey)
                        .textFieldStyle(AmaryllisTerminalTextFieldStyle())
                }

                HStack(spacing: 8) {
                    Button("Save + Restart Hint") {
                        appState.persistSettings()
                        appState.lastError = "Tools/MCP isolation settings saved. Restart runtime to apply policy changes."
                    }
                    .buttonStyle(AmaryllisSecondaryButtonStyle())

                    Button("Refresh Tools") {
                        Task { await refreshToolingState() }
                    }
                    .buttonStyle(AmaryllisSecondaryButtonStyle())
                    .disabled(isToolsLoading)

                    if isToolsLoading {
                        ProgressView()
                            .controlSize(.small)
                    }
                }

                Text("Pending approvals: \(appState.permissionPrompts.count) | Available tools: \(appState.availableTools.count)")
                    .font(AmaryllisTheme.bodyFont(size: 11, weight: .medium))
                    .foregroundStyle(AmaryllisTheme.textSecondary)

                if appState.permissionPrompts.isEmpty {
                    Text("No pending permission prompts.")
                        .font(AmaryllisTheme.bodyFont(size: 12, weight: .medium))
                        .foregroundStyle(AmaryllisTheme.textSecondary)
                } else {
                    ScrollView {
                        LazyVStack(alignment: .leading, spacing: 8) {
                            ForEach(appState.permissionPrompts) { prompt in
                                VStack(alignment: .leading, spacing: 6) {
                                    HStack {
                                        Text(prompt.toolName)
                                            .font(AmaryllisTheme.bodyFont(size: 12, weight: .semibold))
                                            .foregroundStyle(AmaryllisTheme.textPrimary)
                                        Spacer()
                                        Text(prompt.status)
                                            .font(AmaryllisTheme.monoFont(size: 10, weight: .regular))
                                            .foregroundStyle(AmaryllisTheme.textSecondary)
                                    }

                                    Text(prompt.reason)
                                        .font(AmaryllisTheme.bodyFont(size: 11, weight: .medium))
                                        .foregroundStyle(AmaryllisTheme.textSecondary)

                                    Text("request_id: \(prompt.requestId ?? "-")")
                                        .font(AmaryllisTheme.monoFont(size: 10, weight: .regular))
                                        .foregroundStyle(AmaryllisTheme.textSecondary)

                                    Text(renderArgumentsPreview(prompt.argumentsPreview))
                                        .font(AmaryllisTheme.monoFont(size: 10, weight: .regular))
                                        .foregroundStyle(AmaryllisTheme.textSecondary)
                                        .lineLimit(4)

                                    HStack(spacing: 8) {
                                        Button("Approve") {
                                            Task {
                                                await appState.approvePermissionPrompt(promptID: prompt.id)
                                                await refreshToolingState()
                                            }
                                        }
                                        .buttonStyle(AmaryllisPrimaryButtonStyle())

                                        Button("Deny") {
                                            Task {
                                                await appState.denyPermissionPrompt(promptID: prompt.id)
                                                await refreshToolingState()
                                            }
                                        }
                                        .buttonStyle(AmaryllisSecondaryButtonStyle())
                                    }
                                }
                                .padding(8)
                                .background(AmaryllisTheme.surface)
                                .clipShape(RoundedRectangle(cornerRadius: 4))
                            }
                        }
                    }
                    .frame(maxWidth: .infinity, minHeight: 90, maxHeight: 190)
                }

                if !appState.availableTools.isEmpty {
                    ScrollView {
                        LazyVStack(alignment: .leading, spacing: 6) {
                            ForEach(appState.availableTools) { tool in
                                HStack(alignment: .top, spacing: 8) {
                                    Text(tool.name)
                                        .font(AmaryllisTheme.monoFont(size: 11, weight: .regular))
                                        .foregroundStyle(AmaryllisTheme.textPrimary)
                                        .frame(width: 180, alignment: .leading)
                                    Text("\(tool.source) | \(tool.riskLevel) | \(tool.approvalMode)")
                                        .font(AmaryllisTheme.bodyFont(size: 11, weight: .medium))
                                        .foregroundStyle(AmaryllisTheme.textSecondary)
                                        .frame(maxWidth: .infinity, alignment: .leading)
                                }
                            }
                        }
                    }
                    .frame(maxWidth: .infinity, minHeight: 80, maxHeight: 140)
                    .padding(10)
                    .background(AmaryllisTheme.surface)
                    .clipShape(RoundedRectangle(cornerRadius: 6))
                    .overlay(
                        RoundedRectangle(cornerRadius: 6)
                            .stroke(AmaryllisTheme.border.opacity(0.35), lineWidth: 1)
                    )
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
                            .textFieldStyle(AmaryllisTerminalTextFieldStyle())
                    }

                    VStack(alignment: .leading, spacing: 4) {
                        Text("Agent ID (optional)")
                            .font(AmaryllisTheme.bodyFont(size: 11, weight: .semibold))
                            .foregroundStyle(AmaryllisTheme.textSecondary)
                        TextField("agent-id", text: $debugAgentID)
                            .textFieldStyle(AmaryllisTerminalTextFieldStyle())
                    }

                    VStack(alignment: .leading, spacing: 4) {
                        Text("Session ID (optional)")
                            .font(AmaryllisTheme.bodyFont(size: 11, weight: .semibold))
                            .foregroundStyle(AmaryllisTheme.textSecondary)
                        TextField("session-001", text: $debugSessionID)
                            .textFieldStyle(AmaryllisTerminalTextFieldStyle())
                    }
                }

                HStack(spacing: 10) {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("Query")
                            .font(AmaryllisTheme.bodyFont(size: 11, weight: .semibold))
                            .foregroundStyle(AmaryllisTheme.textSecondary)
                        TextField("name / preferences / task", text: $debugQuery)
                            .textFieldStyle(AmaryllisTerminalTextFieldStyle())
                    }
                    .frame(maxWidth: .infinity)

                    VStack(alignment: .leading, spacing: 4) {
                        Text("Top K")
                            .font(AmaryllisTheme.bodyFont(size: 11, weight: .semibold))
                            .foregroundStyle(AmaryllisTheme.textSecondary)
                        TextField("8", text: $debugTopK)
                            .textFieldStyle(AmaryllisTerminalTextFieldStyle())
                            .frame(width: 80)
                    }

                    VStack(alignment: .leading, spacing: 4) {
                        Text("Limit")
                            .font(AmaryllisTheme.bodyFont(size: 11, weight: .semibold))
                            .foregroundStyle(AmaryllisTheme.textSecondary)
                        TextField("20", text: $debugLimit)
                            .textFieldStyle(AmaryllisTerminalTextFieldStyle())
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

                    Button("Run All") {
                        Task { await runAllMemoryDebug() }
                    }
                    .buttonStyle(AmaryllisPrimaryButtonStyle())
                    .disabled(isDebugLoading)

                    Button("Clear") {
                        clearMemoryDebugState()
                    }
                    .buttonStyle(AmaryllisSecondaryButtonStyle())
                    .disabled(isDebugLoading)

                    Toggle("Raw JSON", isOn: $showMemoryRawJSON)
                        .toggleStyle(.switch)
                        .font(AmaryllisTheme.bodyFont(size: 11, weight: .medium))
                        .frame(width: 110)

                    if isDebugLoading {
                        ProgressView()
                            .controlSize(.small)
                    }
                }

                Text(memoryDebugStatus)
                    .font(AmaryllisTheme.bodyFont(size: 11, weight: .medium))
                    .foregroundStyle(AmaryllisTheme.textSecondary)

                ScrollView {
                    if showMemoryRawJSON {
                        Text(memoryRawJSON)
                            .font(AmaryllisTheme.monoFont(size: 11, weight: .regular))
                            .foregroundStyle(AmaryllisTheme.textSecondary)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .textSelection(.enabled)
                    } else {
                        memoryDebugResultsView
                    }
                }
                .frame(maxWidth: .infinity, minHeight: 140, maxHeight: 320)
                .padding(10)
                .background(AmaryllisTheme.surface)
                .clipShape(RoundedRectangle(cornerRadius: 6))
                .overlay(
                    RoundedRectangle(cornerRadius: 6)
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
                .clipShape(RoundedRectangle(cornerRadius: 6))
                .overlay(
                    RoundedRectangle(cornerRadius: 6)
                        .stroke(AmaryllisTheme.border.opacity(0.35), lineWidth: 1)
                )
            }
                .amaryllisCard()
                .frame(minHeight: 180, maxHeight: 260)
            }
        }
        .onAppear {
            appState.runtimeManager.setLogCaptureEnabled(true)
            Task { await refreshToolingState() }
        }
        .onDisappear {
            appState.runtimeManager.setLogCaptureEnabled(false)
        }
    }

    private var memoryDebugResultsView: some View {
        LazyVStack(alignment: .leading, spacing: 10) {
            if memoryContext == nil,
               memoryRetrieval == nil,
               memoryExtractions == nil,
               memoryConflicts == nil {
                Text("Run Context, Retrieval, Extractions or Conflicts to inspect Memory 2.0 layers.")
                    .font(AmaryllisTheme.bodyFont(size: 12, weight: .medium))
                    .foregroundStyle(AmaryllisTheme.textSecondary)
            }

            if let payload = memoryContext {
                memoryContextCard(payload)
            }
            if let payload = memoryRetrieval {
                memoryRetrievalCard(payload)
            }
            if let payload = memoryExtractions {
                memoryExtractionsCard(payload)
            }
            if let payload = memoryConflicts {
                memoryConflictsCard(payload)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func memoryContextCard(_ payload: APIMemoryContextResponse) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Context Layers")
                .font(AmaryllisTheme.bodyFont(size: 12, weight: .semibold))
                .foregroundStyle(AmaryllisTheme.textPrimary)

            HStack(spacing: 8) {
                memoryCountBadge("Working", payload.context.working.count)
                memoryCountBadge("Episodic", payload.context.episodic.count)
                memoryCountBadge("Semantic", payload.context.semantic.count)
                memoryCountBadge("Profile", payload.context.profile.count)
            }

            if !payload.context.profile.isEmpty {
                Text("Profile")
                    .font(AmaryllisTheme.bodyFont(size: 11, weight: .semibold))
                    .foregroundStyle(AmaryllisTheme.textSecondary)
                ForEach(Array(payload.context.profile.prefix(4))) { item in
                    Text("\(item.key): \(item.value) (c=\(formatFloat(item.confidence)), i=\(formatFloat(item.importance)))")
                        .font(AmaryllisTheme.monoFont(size: 10, weight: .regular))
                        .foregroundStyle(AmaryllisTheme.textSecondary)
                }
            }

            if !payload.context.semantic.isEmpty {
                Text("Top Semantic")
                    .font(AmaryllisTheme.bodyFont(size: 11, weight: .semibold))
                    .foregroundStyle(AmaryllisTheme.textSecondary)
                ForEach(Array(payload.context.semantic.prefix(4))) { item in
                    Text("\(item.kind) | score=\(formatFloat(item.score)) | \(truncate(item.text, limit: 90))")
                        .font(AmaryllisTheme.monoFont(size: 10, weight: .regular))
                        .foregroundStyle(AmaryllisTheme.textSecondary)
                }
            }
        }
        .padding(10)
        .background(AmaryllisTheme.surfaceAlt)
        .clipShape(RoundedRectangle(cornerRadius: 4))
    }

    private func memoryRetrievalCard(_ payload: APIMemoryRetrievalResponse) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Retrieval Scoring (\(payload.items.count))")
                .font(AmaryllisTheme.bodyFont(size: 12, weight: .semibold))
                .foregroundStyle(AmaryllisTheme.textPrimary)

            ForEach(Array(payload.items.prefix(8))) { item in
                VStack(alignment: .leading, spacing: 2) {
                    Text("#\(item.rank) \(item.kind) score=\(formatFloat(item.score)) vec=\(formatFloat(item.vectorScore)) rec=\(formatFloat(item.recencyScore))")
                        .font(AmaryllisTheme.monoFont(size: 10, weight: .regular))
                        .foregroundStyle(AmaryllisTheme.textSecondary)
                    Text(truncate(item.text, limit: 120))
                        .font(AmaryllisTheme.bodyFont(size: 11, weight: .medium))
                        .foregroundStyle(AmaryllisTheme.textPrimary)
                }
            }
        }
        .padding(10)
        .background(AmaryllisTheme.surfaceAlt)
        .clipShape(RoundedRectangle(cornerRadius: 4))
    }

    private func memoryExtractionsCard(_ payload: APIMemoryExtractionsResponse) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Extraction Timeline (\(payload.items.count))")
                .font(AmaryllisTheme.bodyFont(size: 12, weight: .semibold))
                .foregroundStyle(AmaryllisTheme.textPrimary)

            ForEach(Array(payload.items.reversed().prefix(6))) { item in
                VStack(alignment: .leading, spacing: 2) {
                    let total = item.extracted.facts.count + item.extracted.preferences.count + item.extracted.tasks.count
                    Text("\(item.createdAt) | \(item.sourceRole) | extracted=\(total)")
                        .font(AmaryllisTheme.monoFont(size: 10, weight: .regular))
                        .foregroundStyle(AmaryllisTheme.textSecondary)
                    Text(truncate(item.sourceText, limit: 120))
                        .font(AmaryllisTheme.bodyFont(size: 11, weight: .medium))
                        .foregroundStyle(AmaryllisTheme.textPrimary)
                    Text("facts=\(item.extracted.facts.count), prefs=\(item.extracted.preferences.count), tasks=\(item.extracted.tasks.count)")
                        .font(AmaryllisTheme.monoFont(size: 10, weight: .regular))
                        .foregroundStyle(AmaryllisTheme.textSecondary)
                }
            }
        }
        .padding(10)
        .background(AmaryllisTheme.surfaceAlt)
        .clipShape(RoundedRectangle(cornerRadius: 4))
    }

    private func memoryConflictsCard(_ payload: APIMemoryConflictsResponse) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Conflict Log (\(payload.items.count))")
                .font(AmaryllisTheme.bodyFont(size: 12, weight: .semibold))
                .foregroundStyle(AmaryllisTheme.textPrimary)

            ForEach(Array(payload.items.reversed().prefix(8))) { item in
                VStack(alignment: .leading, spacing: 2) {
                    Text("\(item.layer).\(item.key) | \(item.resolution)")
                        .font(AmaryllisTheme.monoFont(size: 10, weight: .regular))
                        .foregroundStyle(item.resolution.contains("kept_previous") ? AmaryllisTheme.accent : AmaryllisTheme.textSecondary)
                    Text("prev=\(item.previousValue ?? "-") | new=\(item.incomingValue ?? "-")")
                        .font(AmaryllisTheme.bodyFont(size: 11, weight: .medium))
                        .foregroundStyle(AmaryllisTheme.textPrimary)
                    Text("conf_prev=\(formatFloat(item.confidencePrev)) conf_new=\(formatFloat(item.confidenceNew)) @ \(item.createdAt)")
                        .font(AmaryllisTheme.monoFont(size: 10, weight: .regular))
                        .foregroundStyle(AmaryllisTheme.textSecondary)
                }
            }
        }
        .padding(10)
        .background(AmaryllisTheme.surfaceAlt)
        .clipShape(RoundedRectangle(cornerRadius: 4))
    }

    private func memoryCountBadge(_ title: String, _ count: Int) -> some View {
        HStack(spacing: 6) {
            Text(title)
                .font(AmaryllisTheme.bodyFont(size: 11, weight: .semibold))
            Text("\(count)")
                .font(AmaryllisTheme.monoFont(size: 11, weight: .regular))
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 4)
        .background(AmaryllisTheme.surface)
        .clipShape(Capsule())
    }

    private func truncate(_ text: String, limit: Int) -> String {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard trimmed.count > limit else { return trimmed }
        let index = trimmed.index(trimmed.startIndex, offsetBy: max(0, limit))
        return "\(trimmed[..<index])..."
    }

    private func formatFloat(_ value: Double?) -> String {
        guard let value else { return "-" }
        return String(format: "%.3f", value)
    }

    private func clearMemoryDebugState() {
        memoryContext = nil
        memoryRetrieval = nil
        memoryExtractions = nil
        memoryConflicts = nil
        memoryDebugStatus = "Memory debug state cleared."
        memoryRawJSON = "{}"
    }

    private enum MemoryDebugAction {
        case context
        case retrieval
        case extractions
        case conflicts
    }

    private func runAllMemoryDebug() async {
        await runMemoryDebug(.context)
        await runMemoryDebug(.retrieval)
        await runMemoryDebug(.extractions)
        await runMemoryDebug(.conflicts)
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
                let response = try await appState.apiClient.debugMemoryContext(
                    userId: userID,
                    agentId: emptyToNil(debugAgentID),
                    sessionId: emptyToNil(debugSessionID),
                    query: query,
                    semanticTopK: topK
                )
                memoryContext = response
                memoryRawJSON = prettyJSON(from: response)
                memoryDebugStatus = "Context loaded: working \(response.context.working.count), episodic \(response.context.episodic.count), semantic \(response.context.semantic.count), profile \(response.context.profile.count)."
            case .retrieval:
                let retrievalQuery = query.isEmpty ? "memory" : query
                let response = try await appState.apiClient.debugMemoryRetrieval(
                    userId: userID,
                    query: retrievalQuery,
                    topK: topK
                )
                memoryRetrieval = response
                memoryRawJSON = prettyJSON(from: response)
                memoryDebugStatus = "Retrieval loaded: \(response.items.count) items for query \"\(retrievalQuery)\"."
            case .extractions:
                let response = try await appState.apiClient.debugMemoryExtractions(
                    userId: userID,
                    limit: limit
                )
                memoryExtractions = response
                memoryRawJSON = prettyJSON(from: response)
                memoryDebugStatus = "Extractions loaded: \(response.count) records."
            case .conflicts:
                let response = try await appState.apiClient.debugMemoryConflicts(
                    userId: userID,
                    limit: limit
                )
                memoryConflicts = response
                memoryRawJSON = prettyJSON(from: response)
                memoryDebugStatus = "Conflicts loaded: \(response.count) records."
            }
            appState.lastError = nil
        } catch {
            appState.lastError = error.localizedDescription
            memoryDebugStatus = "Error: \(error.localizedDescription)"
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

    private func prettyJSON<T: Encodable>(from value: T) -> String {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        guard let data = try? encoder.encode(value),
              let text = String(data: data, encoding: .utf8) else {
            return "{}"
        }
        return text
    }

    private func refreshToolingState() async {
        isToolsLoading = true
        defer { isToolsLoading = false }
        await appState.refreshToolingState()
    }

    private func renderArgumentsPreview(_ arguments: [String: JSONValue]) -> String {
        guard let data = try? JSONEncoder().encode(arguments),
              let object = try? JSONSerialization.jsonObject(with: data, options: []),
              let pretty = try? JSONSerialization.data(withJSONObject: object, options: [.prettyPrinted, .sortedKeys]),
              let text = String(data: pretty, encoding: .utf8) else {
            return "{}"
        }
        return text
    }

    private func isolationHintText() -> String {
        if appState.toolIsolationProfile.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() == "strict" {
            return "Strict profile: high-risk tools are denied by default unless listed in Allowed High-Risk Tools. Filesystem write can be disabled and tool budgets cap bursty execution."
        }
        return "Balanced profile: tools follow approval mode, per-tool guards, and tool budgets (window/per-tool/total/high-risk). Use strict profile for deny-by-default behavior."
    }
}
