import Foundation
import SwiftUI

struct ChatView: View {
    @EnvironmentObject private var appState: AppState

    @State private var inputText: String = ""
    @State private var isStreaming: Bool = true
    @State private var selectedModelID: String = ""
    @State private var selectedProvider: String = ""
    @State private var autoRoutingEnabled: Bool = true
    @State private var routingMode: String = "balanced"
    @State private var toolsEnabled: Bool = true
    @State private var isSending: Bool = false
    @State private var showAdvancedControls: Bool = false
    @State private var streamingAssistantID: UUID?
    @State private var streamingAssistantText: String = ""
    @State private var showFullHistory: Bool = false

    private let baseSystemPrompt = "You are Amaryllis, a concise and practical local AI assistant."
    private let maxHistoryMessages: Int = 48
    private let maxVisibleMessages: Int = 80
    private let streamingRenderInterval: TimeInterval = 0.22
    private let streamingChunkThreshold: Int = 220

    var body: some View {
        VStack(spacing: 10) {
            if shouldShowSetupCard {
                setupCard
            }
            sessionBar
            simpleControlBar
            if showAdvancedControls {
                advancedControlBar
            }

            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 8) {
                        if isHistoryTruncated {
                            HStack(spacing: 8) {
                                Text("Showing latest \(displayedMessages.count) of \(currentMessages.count) messages.")
                                    .font(AmaryllisTheme.bodyFont(size: 11, weight: .medium))
                                    .foregroundStyle(AmaryllisTheme.textSecondary)
                                Button("Show Full History") {
                                    showFullHistory = true
                                }
                                .buttonStyle(AmaryllisSecondaryButtonStyle())
                                Spacer()
                            }
                        }
                        if currentMessages.isEmpty {
                            Text("Start a new conversation.")
                                .font(AmaryllisTheme.bodyFont(size: 13, weight: .medium))
                                .foregroundStyle(AmaryllisTheme.textSecondary)
                                .padding(.top, 8)
                        } else {
                            ForEach(displayedMessages) { message in
                                bubble(for: message, content: renderedContent(for: message))
                                    .id(message.id)
                            }
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
                .onChange(of: currentMessages.last?.id) { _ in
                    guard let last = displayedMessages.last else { return }
                    proxy.scrollTo(last.id, anchor: .bottom)
                }
            }
            .amaryllisCard()

            HStack(alignment: .bottom, spacing: 8) {
                TextEditor(text: $inputText)
                    .font(AmaryllisTheme.bodyFont(size: 14, weight: .medium))
                    .frame(minHeight: 64, maxHeight: 120)
                    .amaryllisEditorSurface()

                Button(action: send) {
                    if isSending {
                        ProgressView()
                            .controlSize(.small)
                            .tint(.white)
                            .frame(width: 88)
                    } else {
                        Text("Send")
                            .font(AmaryllisTheme.bodyFont(size: 14, weight: .semibold))
                            .frame(width: 88)
                    }
                }
                .buttonStyle(AmaryllisPrimaryButtonStyle())
                .disabled(isSending || inputText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
            .amaryllisCard()
        }
        .onAppear {
            appState.ensureChatExists()
            if selectedModelID.isEmpty {
                selectedModelID = appState.selectedModel ?? ""
            }
            if selectedProvider.isEmpty {
                selectedProvider = appState.selectedProvider ?? ""
            }
        }
        .onChange(of: appState.selectedModel ?? "") { _ in
            if selectedModelID.isEmpty {
                selectedModelID = appState.selectedModel ?? ""
            }
        }
        .onChange(of: selectedModelID) { modelID in
            let normalized = modelID.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !normalized.isEmpty else { return }
            if let inferred = providerForModel(normalized) {
                selectedProvider = inferred
            }
        }
        .onChange(of: appState.selectedChatID) { _ in
            showFullHistory = false
            streamingAssistantID = nil
            streamingAssistantText = ""
        }
    }

    private var currentMessages: [LocalChatMessage] {
        appState.currentChatMessages
    }

    private var isHistoryTruncated: Bool {
        currentMessages.count > maxVisibleMessages && !showFullHistory
    }

    private var displayedMessages: [LocalChatMessage] {
        if showFullHistory || currentMessages.count <= maxVisibleMessages {
            return currentMessages
        }
        return Array(currentMessages.suffix(maxVisibleMessages))
    }

    private func renderedContent(for message: LocalChatMessage) -> String {
        if message.id == streamingAssistantID {
            return streamingAssistantText
        }
        return message.content
    }

    private var sessionBar: some View {
        HStack(spacing: 8) {
            Menu {
                ForEach(appState.chatSessions) { session in
                    Button {
                        appState.selectChat(id: session.id)
                    } label: {
                        Text(session.title)
                    }
                }
            } label: {
                HStack(spacing: 8) {
                    Text(appState.currentChatTitle)
                        .font(AmaryllisTheme.bodyFont(size: 13, weight: .semibold))
                        .tracking(0.5)
                        .foregroundStyle(AmaryllisTheme.textPrimary)
                        .lineLimit(1)
                    Image(systemName: "chevron.down")
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundStyle(AmaryllisTheme.textSecondary)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, 10)
                .padding(.vertical, 7)
                .background(AmaryllisTheme.surfaceAlt)
                .overlay(
                    RoundedRectangle(cornerRadius: 4)
                        .stroke(AmaryllisTheme.border.opacity(0.6), lineWidth: 1)
                )
                .clipShape(RoundedRectangle(cornerRadius: 4))
            }
            .menuStyle(.borderlessButton)
            .disabled(isSending)

            Button("New Chat") {
                _ = appState.createChat()
            }
            .buttonStyle(AmaryllisSecondaryButtonStyle())
            .disabled(isSending)

            Button("Delete") {
                appState.deleteCurrentChat()
            }
            .buttonStyle(AmaryllisSecondaryButtonStyle())
            .disabled(isSending || appState.chatSessions.isEmpty)

            Text("\(appState.chatSessions.count)")
                .font(AmaryllisTheme.monoFont(size: 11, weight: .regular))
                .foregroundStyle(AmaryllisTheme.textSecondary)
        }
        .amaryllisCard()
    }

    private var shouldShowSetupCard: Bool {
        appState.needsQuickSetup || appState.modelCatalog == nil
    }

    private var setupCard: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Quick Start")
                        .font(AmaryllisTheme.sectionFont(size: 17))
                        .foregroundStyle(AmaryllisTheme.textPrimary)
                    Text("One click to connect runtime and prepare a model.")
                        .font(AmaryllisTheme.bodyFont(size: 12, weight: .medium))
                        .foregroundStyle(AmaryllisTheme.textSecondary)
                }
                Spacer()
                Button {
                    Task { await appState.quickSetup() }
                } label: {
                    if appState.isQuickSetupRunning {
                        ProgressView()
                            .controlSize(.small)
                            .tint(AmaryllisTheme.textPrimary)
                            .frame(width: 106)
                    } else {
                        Text("Run Setup")
                            .frame(width: 106)
                    }
                }
                .buttonStyle(AmaryllisPrimaryButtonStyle())
                .disabled(appState.isQuickSetupRunning)
            }

            HStack(spacing: 12) {
                setupStatus(label: "Runtime", ready: appState.runtimeManager.isRunning)
                setupStatus(label: "API", ready: appState.runtimeManager.connectionState == .online)
                setupStatus(label: "Model", ready: appState.hasActiveModelConfigured)
            }
        }
        .amaryllisCard()
    }

    private func setupStatus(label: String, ready: Bool) -> some View {
        HStack(spacing: 6) {
            Rectangle()
                .fill(ready ? AmaryllisTheme.okGreen : AmaryllisTheme.accent)
                .frame(width: 8, height: 8)
            Text(label)
                .font(AmaryllisTheme.bodyFont(size: 11, weight: .semibold))
                .foregroundStyle(AmaryllisTheme.textSecondary)
            Text(ready ? "ready" : "pending")
                .font(AmaryllisTheme.monoFont(size: 11, weight: .regular))
                .foregroundStyle(AmaryllisTheme.textPrimary)
        }
    }

    private var simpleControlBar: some View {
        HStack(spacing: 8) {
            Text("Model")
                .font(AmaryllisTheme.bodyFont(size: 12, weight: .semibold))
                .foregroundStyle(AmaryllisTheme.textSecondary)

            Picker("Model", selection: $selectedModelID) {
                Text("active").tag("")
                ForEach(modelOptions, id: \.id) { item in
                    Text(item.id).tag(item.id)
                }
            }
            .pickerStyle(.menu)
            .frame(maxWidth: 460)

            Toggle("Stream", isOn: $isStreaming)
                .toggleStyle(.switch)
                .font(AmaryllisTheme.bodyFont(size: 12, weight: .semibold))
                .frame(width: 110)

            Toggle("Tools", isOn: $toolsEnabled)
                .toggleStyle(.switch)
                .font(AmaryllisTheme.bodyFont(size: 12, weight: .semibold))
                .frame(width: 95)

            Spacer()

            Button(showAdvancedControls ? "Hide Advanced" : "Advanced") {
                showAdvancedControls.toggle()
            }
            .buttonStyle(AmaryllisSecondaryButtonStyle())
            .disabled(isSending)
        }
        .amaryllisCard()
    }

    private var advancedControlBar: some View {
        HStack(spacing: 8) {
            Picker("Provider", selection: $selectedProvider) {
                Text("auto").tag("")
                ForEach(providerNames, id: \.self) { provider in
                    Text(provider).tag(provider)
                }
            }
            .pickerStyle(.menu)
            .frame(width: 160)
            .disabled(autoRoutingEnabled)

            Toggle("Auto Route", isOn: $autoRoutingEnabled)
                .toggleStyle(.switch)
                .font(AmaryllisTheme.bodyFont(size: 12, weight: .semibold))
                .frame(width: 130)

            Picker("Policy", selection: $routingMode) {
                ForEach(routingModes, id: \.self) { mode in
                    Text(mode).tag(mode)
                }
            }
            .pickerStyle(.menu)
            .frame(width: 170)
            .disabled(!autoRoutingEnabled)

            Spacer()
            Text("Advanced routing")
                .font(AmaryllisTheme.monoFont(size: 11, weight: .regular))
                .foregroundStyle(AmaryllisTheme.textSecondary)
        }
        .amaryllisCard()
    }

    private func bubble(for message: LocalChatMessage, content: String) -> some View {
        let visibleContent = content.isEmpty && message.id == streamingAssistantID ? "…" : content
        return ChatBubbleView(role: message.role, content: visibleContent)
            .equatable()
    }

    private var modelOptions: [APIModelItem] {
        guard let catalog = appState.modelCatalog else { return [] }
        return catalog.providers.values
            .flatMap { $0.items }
            .sorted { $0.id < $1.id }
    }

    private var providerNames: [String] {
        guard let catalog = appState.modelCatalog else { return [] }
        return catalog.providers.keys.sorted()
    }

    private var routingModes: [String] {
        guard let modes = appState.modelCatalog?.routingModes, !modes.isEmpty else {
            return ["balanced", "local_first", "quality_first", "coding", "reasoning"]
        }
        return modes
    }

    private var chatTools: [APIChatToolDefinition] {
        appState.availableTools.map { tool in
            APIChatToolDefinition(
                type: "function",
                function: APIChatToolFunction(
                    name: tool.name,
                    description: tool.description,
                    parameters: tool.inputSchema
                )
            )
        }
    }

    private func send() {
        let text = inputText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !isSending else { return }

        _ = appState.appendUserMessageToCurrentChat(text)
        inputText = ""

        let assistantID = appState.appendAssistantPlaceholderToCurrentChat()

        isSending = true
        appState.clearError()

        let systemPrompt = buildSystemPrompt(for: text)
        var payload: [APIChatMessage] = [
            APIChatMessage(role: "system", content: systemPrompt, name: nil)
        ]

        let chatHistory = appState.currentChatMessages.filter { row in
            (row.role == "user" || row.role == "assistant") && !row.content.isEmpty
        }
        for row in chatHistory.suffix(maxHistoryMessages) {
            payload.append(APIChatMessage(role: row.role, content: row.content, name: nil))
        }

        let provider = selectedProvider.isEmpty ? nil : selectedProvider
        let model = selectedModelID.isEmpty ? nil : selectedModelID
        let modelIsAvailable = model.map { selectedModel in
            modelOptions.contains(where: { $0.id == selectedModel })
        } ?? false
        let effectiveModel = modelIsAvailable ? model : nil
        let inferredProvider = model.flatMap(providerForModel)
        let resolvedProvider = inferredProvider ?? provider
        let useAutoRouting = autoRoutingEnabled && effectiveModel == nil
        let providerSupportsToolCalls = useAutoRouting ? true : providerSupportsTools(resolvedProvider)
        let effectiveToolsEnabled = toolsEnabled && providerSupportsToolCalls
        let tools = effectiveToolsEnabled ? chatTools : []
        let route = useAutoRouting ? APIChatRoutingOptions(
            mode: routingMode,
            requireStream: isStreaming && tools.isEmpty,
            requireTools: false,
            preferLocal: nil,
            minParamsB: nil,
            maxParamsB: nil,
            includeSuggested: false
        ) : nil
        let providerTarget = useAutoRouting ? nil : resolvedProvider
        let modelTarget = useAutoRouting ? nil : effectiveModel
        let shouldUseStreaming = isStreaming && tools.isEmpty
        let chatSessionID = appState.selectedChatID?.uuidString

        Task {
            if model != nil && !modelIsAvailable {
                await MainActor.run {
                    appState.lastError = "Selected model is unavailable. Using active/default model instead."
                }
            }
            if toolsEnabled && appState.availableTools.isEmpty {
                await appState.refreshToolingState()
            }
            if toolsEnabled && !providerSupportsToolCalls {
                await MainActor.run {
                    let providerLabel = (providerTarget ?? resolvedProvider ?? "selected provider")
                    appState.lastError = "Tools are not supported for \(providerLabel). Sent as regular chat."
                }
            }
            let runtimeReady = await appState.ensureChatReady()
            if !runtimeReady {
                await MainActor.run {
                    let message = appState.lastError ?? "Could not connect to runtime."
                    appState.finalizeCurrentChatMessage(id: assistantID, content: "Error: \(message)")
                    isSending = false
                }
                return
            }

            do {
                if shouldUseStreaming {
                    var combined = ""
                    var rendered = ""
                    var lastRender = Date.distantPast
                    await MainActor.run {
                        streamingAssistantID = assistantID
                        streamingAssistantText = ""
                    }
                    do {
                        let stream = appState.apiClient.streamChatCompletions(
                            model: modelTarget,
                            provider: providerTarget,
                            sessionId: chatSessionID,
                            messages: payload,
                            tools: nil,
                            routing: route
                        )

                        for try await chunk in stream {
                            guard !chunk.isEmpty else { continue }
                            combined += chunk
                            let now = Date()
                            let deltaCount = combined.count - rendered.count
                            let shouldRender = rendered.isEmpty
                                || now.timeIntervalSince(lastRender) >= streamingRenderInterval
                                || deltaCount >= streamingChunkThreshold
                            if shouldRender {
                                rendered = combined
                                lastRender = now
                                await MainActor.run {
                                    streamingAssistantText = rendered
                                }
                            }
                        }
                    } catch {
                        if isTransientStreamingError(error) {
                            let fallback = try await appState.apiClient.chatCompletions(
                                model: modelTarget,
                                provider: providerTarget,
                                sessionId: chatSessionID,
                                messages: payload,
                                tools: nil,
                                routing: route
                            )
                            combined = fallback.choices.first?.message.content ?? combined
                            if combined.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                                throw error
                            }
                        } else {
                            throw error
                        }
                    }

                    await MainActor.run {
                        streamingAssistantID = nil
                        streamingAssistantText = ""
                        if combined.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                            appState.finalizeCurrentChatMessage(
                                id: assistantID,
                                content: "Error: Empty response from provider. Check API key, quota, and model access."
                            )
                            appState.lastError = "Empty response from provider."
                        } else {
                            appState.finalizeCurrentChatMessage(id: assistantID, content: combined)
                        }
                    }
                } else {
                    await MainActor.run {
                        streamingAssistantID = nil
                        streamingAssistantText = ""
                    }
                    if isStreaming && !tools.isEmpty {
                        await MainActor.run {
                            appState.lastError = "Streaming with tools is disabled; using non-stream mode for tool loop."
                        }
                    }

                    var response = try await appState.apiClient.chatCompletions(
                        model: modelTarget,
                        provider: providerTarget,
                        sessionId: chatSessionID,
                        messages: payload,
                        tools: tools.isEmpty ? nil : tools,
                        routing: route
                    )

                    let pendingPromptIDs = pendingPermissionPromptIDs(from: response.toolEvents)
                    var content = response.choices.first?.message.content ?? ""
                    let hasToolEvents = !pendingPromptIDs.isEmpty || !(response.toolEvents ?? []).isEmpty
                    if effectiveToolsEnabled && !hasToolEvents && looksLikeToolCallJSON(content) {
                        let retryWithoutTools = try await appState.apiClient.chatCompletions(
                            model: modelTarget,
                            provider: providerTarget,
                            sessionId: chatSessionID,
                            messages: payload,
                            tools: nil,
                            routing: route
                        )
                        response = retryWithoutTools
                        content = retryWithoutTools.choices.first?.message.content ?? content
                    }
                    let routingTrace = renderRoutingTrace(response.routing)
                    if showAdvancedControls && !routingTrace.isEmpty {
                        content += "\n\n\(routingTrace)"
                    }
                    let firstTrace = renderToolTrace(response.toolEvents)
                    if showAdvancedControls && !firstTrace.isEmpty {
                        content += "\n\n\(firstTrace)"
                    }
                    let firstPolicyWarning = renderPolicyWarning(events: response.toolEvents, errorText: nil)
                    if showAdvancedControls && !firstPolicyWarning.isEmpty {
                        content += "\n\n\(firstPolicyWarning)"
                    }

                    await MainActor.run {
                        appState.finalizeCurrentChatMessage(id: assistantID, content: content)
                    }

                    if !pendingPromptIDs.isEmpty {
                        await MainActor.run {
                            appState.lastError = "Tool permission required. Approve in Settings -> Tools & MCP. Waiting for approval..."
                        }
                        let approvalState = try await waitForPromptDecision(promptIDs: pendingPromptIDs, timeoutSec: 120)
                        if approvalState == .approved {
                            response = try await appState.apiClient.chatCompletions(
                                model: modelTarget,
                                provider: providerTarget,
                                sessionId: chatSessionID,
                                messages: payload,
                                tools: tools.isEmpty ? nil : tools,
                                permissionIds: pendingPromptIDs,
                                routing: route
                            )

                            var retriedContent = response.choices.first?.message.content ?? content
                            let retryRoutingTrace = renderRoutingTrace(response.routing)
                            if showAdvancedControls && !retryRoutingTrace.isEmpty {
                                retriedContent += "\n\n\(retryRoutingTrace)"
                            }
                            let retryTrace = renderToolTrace(response.toolEvents)
                            if showAdvancedControls && !retryTrace.isEmpty {
                                retriedContent += "\n\n\(retryTrace)"
                            }
                            let retryPolicyWarning = renderPolicyWarning(events: response.toolEvents, errorText: nil)
                            if showAdvancedControls && !retryPolicyWarning.isEmpty {
                                retriedContent += "\n\n\(retryPolicyWarning)"
                            }
                            await MainActor.run {
                                appState.finalizeCurrentChatMessage(id: assistantID, content: retriedContent)
                                appState.lastError = nil
                            }
                        } else if approvalState == .denied {
                            await MainActor.run {
                                appState.lastError = "Tool permission denied."
                            }
                        } else {
                            await MainActor.run {
                                appState.lastError = "Tool permission timeout. You can resend after approving."
                            }
                        }
                    }
                }

                await appState.refreshHealth()
            } catch {
                await MainActor.run {
                    streamingAssistantID = nil
                    streamingAssistantText = ""
                    let message = renderUserFacingError(error.localizedDescription)
                    appState.finalizeCurrentChatMessage(id: assistantID, content: message)
                    appState.lastError = message
                }
            }

            await MainActor.run {
                isSending = false
            }
        }
    }

    private enum PromptDecision {
        case approved
        case denied
        case timeout
    }

    private func pendingPermissionPromptIDs(from events: [APIChatToolEvent]?) -> [String] {
        guard let events else { return [] }
        var ids: [String] = []
        for event in events {
            if event.status?.lowercased() == "permission_required",
               let promptID = event.permissionPromptId,
               !promptID.isEmpty {
                ids.append(promptID)
            }
        }
        return Array(Set(ids))
    }

    private func waitForPromptDecision(promptIDs: [String], timeoutSec: Int) async throws -> PromptDecision {
        if promptIDs.isEmpty {
            return .approved
        }

        let checks = max(1, timeoutSec / 2)
        for _ in 0..<checks {
            try await Task.sleep(nanoseconds: 2_000_000_000)
            let snapshot = try await appState.apiClient.listPermissionPrompts(status: nil, limit: 500)
            var statusByID: [String: String] = [:]
            for item in snapshot.items {
                statusByID[item.id] = item.status.lowercased()
            }

            let statuses = promptIDs.compactMap { statusByID[$0] }
            if statuses.contains("denied") {
                return .denied
            }
            if statuses.count == promptIDs.count,
               statuses.allSatisfy({ $0 == "approved" || $0 == "consumed" }) {
                return .approved
            }
        }

        return .timeout
    }

    private func renderToolTrace(_ events: [APIChatToolEvent]?) -> String {
        guard let events, !events.isEmpty else { return "" }

        var lines = ["Tool trace:"]
        for event in events {
            let tool = event.tool ?? "-"
            let status = event.status ?? "-"
            let duration = event.durationMs.map { String(format: "%.2fms", $0) } ?? "-"
            var line = "- \(tool): \(status) (\(duration))"
            if let promptID = event.permissionPromptId, !promptID.isEmpty {
                line += " prompt_id=\(promptID)"
            }
            if let error = event.error, !error.isEmpty {
                line += " error=\(error)"
            }
            lines.append(line)
        }
        return lines.joined(separator: "\n")
    }

    private func renderRoutingTrace(_ routing: APIChatRoutingDecision?) -> String {
        guard let routing, let selected = routing.selected else { return "" }
        var lines = ["Routing: mode=\(routing.mode ?? "-") selected=\(selected.provider)/\(selected.model)"]
        if let score = selected.score {
            lines[0] += String(format: " score=%.3f", score)
        }
        if let reason = selected.reason, !reason.isEmpty {
            lines[0] += " reason=\(reason)"
        }
        if let fallbacks = routing.fallbacks, !fallbacks.isEmpty {
            let preview = fallbacks.prefix(3).map { item -> String in
                if let score = item.score {
                    return "\(item.provider)/\(item.model)(\(String(format: "%.2f", score)))"
                }
                return "\(item.provider)/\(item.model)"
            }
            lines.append("Fallbacks: \(preview.joined(separator: ", "))")
        }
        if let final = routing.final {
            let fallbackUsed = final.fallbackUsed == true ? "yes" : "no"
            lines.append("Final: \(final.provider)/\(final.model) fallback=\(fallbackUsed)")
        }
        if let failovers = routing.failoverEvents, !failovers.isEmpty {
            lines.append("Failover trace:")
            for event in failovers.prefix(4) {
                let provider = event.provider ?? "-"
                let model = event.model ?? "-"
                let errorClass = event.errorClass ?? "unknown"
                let retryable = event.retryable == true ? "retryable" : "final"
                let message = (event.message ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
                var line = "- \(provider)/\(model): \(errorClass) [\(retryable)]"
                if !message.isEmpty {
                    line += " \(message)"
                }
                lines.append(line)
            }
        }
        return lines.joined(separator: "\n")
    }

    private func renderPolicyWarning(events: [APIChatToolEvent]?, errorText: String?) -> String {
        var blockedByPolicy = false

        if let events {
            for event in events {
                let status = (event.status ?? "").lowercased()
                let detail = (event.error ?? "").lowercased()
                if status == "blocked" {
                    blockedByPolicy = true
                }
                if detail.contains("blocked by policy")
                    || detail.contains("isolation profile")
                    || detail.contains("high-risk")
                    || detail.contains("filesystem write is disabled")
                    || detail.contains("timeout exceeds limit")
                    || detail.contains("code size exceeds limit")
                    || detail.contains("tool budget limit reached")
                    || detail.contains("high-risk tool budget limit reached") {
                    blockedByPolicy = true
                }
            }
        }

        if !blockedByPolicy, let errorText {
            let lower = errorText.lowercased()
            if lower.contains("blocked by policy")
                || lower.contains("isolation profile")
                || lower.contains("high-risk")
                || lower.contains("filesystem write is disabled")
                || lower.contains("timeout exceeds limit")
                || lower.contains("code size exceeds limit")
                || lower.contains("tool budget limit reached")
                || lower.contains("high-risk tool budget limit reached") {
                blockedByPolicy = true
            }
        }

        if blockedByPolicy {
            return "Policy warning: tool request was blocked by runtime isolation policy. Review Settings -> Tools & MCP (Isolation Profile, Allowed High-Risk Tools, filesystem write, python_exec limits), then restart runtime."
        }
        return ""
    }

    private func renderUserFacingError(_ raw: String) -> String {
        let warning = renderPolicyWarning(events: nil, errorText: raw)
        if warning.isEmpty {
            return "Error: \(raw)"
        }
        return "Error: \(raw)\n\n\(warning)"
    }

    private func isTransientStreamingError(_ error: Error) -> Bool {
        let message = error.localizedDescription.lowercased()
        if message.contains("network connection was lost")
            || message.contains("cancelled")
            || message.contains("timed out")
            || message.contains("streaming request failed")
            || message.contains("connection reset") {
            return true
        }
        return false
    }

    private func providerForModel(_ modelID: String) -> String? {
        let normalized = modelID.trimmingCharacters(in: .whitespacesAndNewlines)
        if normalized.lowercased().hasPrefix("mlx-community/") {
            return "mlx"
        }
        if normalized.lowercased().hasPrefix("claude-") {
            return "anthropic"
        }
        if normalized.lowercased().hasPrefix("gpt-")
            || normalized.lowercased().hasPrefix("o1")
            || normalized.lowercased().hasPrefix("o3")
            || normalized.lowercased().hasPrefix("o4") {
            return "openai"
        }
        guard let catalog = appState.modelCatalog else {
            return nil
        }
        for providerName in catalog.providers.keys.sorted() {
            guard let payload = catalog.providers[providerName] else { continue }
            if payload.items.contains(where: { $0.id == modelID }) {
                return providerName
            }
        }
        return nil
    }

    private func providerSupportsTools(_ providerName: String?) -> Bool {
        guard let providerName, !providerName.isEmpty else {
            return true
        }
        guard let caps = appState.modelCatalog?.capabilities?[providerName] else {
            return true
        }
        return caps.supportsTools ?? true
    }

    private func looksLikeToolCallJSON(_ text: String) -> Bool {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard trimmed.hasPrefix("{"), trimmed.hasSuffix("}") else {
            return false
        }
        guard let data = trimmed.data(using: .utf8),
              let object = try? JSONSerialization.jsonObject(with: data),
              let dict = object as? [String: Any] else {
            return false
        }
        if dict["name"] is String, dict["arguments"] != nil {
            return true
        }
        return false
    }

    private func buildSystemPrompt(for userText: String) -> String {
        let languageRule = languageDirective(for: userText)
        return """
\(baseSystemPrompt)

Language policy:
\(languageRule)
- Do not mix Russian and English in the same answer unless the user explicitly asks for mixed output.
- If previous assistant messages used another language, ignore that drift and follow this policy now.
"""
    }

    private func languageDirective(for userText: String) -> String {
        let text = userText.trimmingCharacters(in: .whitespacesAndNewlines)
        if containsCyrillic(text) {
            return "- Reply strictly in Russian."
        }
        if containsLatin(text) {
            return "- Reply strictly in English."
        }
        return "- Reply in the same language as the latest user message."
    }

    private func containsCyrillic(_ text: String) -> Bool {
        text.unicodeScalars.contains { scalar in
            (0x0400...0x04FF).contains(scalar.value) || (0x0500...0x052F).contains(scalar.value)
        }
    }

    private func containsLatin(_ text: String) -> Bool {
        text.unicodeScalars.contains { scalar in
            (0x0041...0x005A).contains(scalar.value) || (0x0061...0x007A).contains(scalar.value)
        }
    }
}

private struct ChatBubbleView: View, Equatable {
    let role: String
    let content: String

    static func == (lhs: ChatBubbleView, rhs: ChatBubbleView) -> Bool {
        lhs.role == rhs.role && lhs.content == rhs.content
    }

    var body: some View {
        let isUser = role == "user"
        HStack {
            if isUser { Spacer() }
            VStack(alignment: .leading, spacing: 4) {
                Text(isUser ? "You" : "Amaryllis")
                    .font(AmaryllisTheme.bodyFont(size: 10, weight: .bold))
                    .foregroundStyle(AmaryllisTheme.textSecondary)
                Text(content)
                    .font(AmaryllisTheme.bodyFont(size: 14, weight: .medium))
                    .foregroundStyle(AmaryllisTheme.textPrimary)
            }
            .padding(10)
            .background(isUser ? AmaryllisTheme.accentSoft : AmaryllisTheme.surfaceAlt)
            .clipShape(RoundedRectangle(cornerRadius: 4))
            .overlay(
                RoundedRectangle(cornerRadius: 4)
                    .stroke(isUser ? AmaryllisTheme.accent.opacity(0.9) : AmaryllisTheme.border.opacity(0.35), lineWidth: 1)
            )
            .frame(maxWidth: 680, alignment: .leading)
            if !isUser { Spacer() }
        }
    }
}
