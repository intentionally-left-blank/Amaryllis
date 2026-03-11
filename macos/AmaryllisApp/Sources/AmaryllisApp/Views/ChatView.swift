import Foundation
import SwiftUI

struct ChatView: View {
    @EnvironmentObject private var appState: AppState

    @State private var inputText: String = ""
    @State private var isStreaming: Bool = true
    @State private var selectedModelID: String = ""
    @State private var selectedProvider: String = ""
    @State private var toolsEnabled: Bool = true
    @State private var isSending: Bool = false

    private let systemPrompt = "You are Amaryllis, a concise and practical local AI assistant."

    var body: some View {
        VStack(spacing: 10) {
            sessionBar
            controlBar

            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 8) {
                        if currentMessages.isEmpty {
                            Text("Start a new conversation.")
                                .font(AmaryllisTheme.bodyFont(size: 13, weight: .medium))
                                .foregroundStyle(AmaryllisTheme.textSecondary)
                                .padding(.top, 8)
                        } else {
                            ForEach(currentMessages) { message in
                                bubble(for: message)
                                    .id(message.id)
                            }
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
                .onChange(of: currentMessages.count) { _ in
                    guard let last = currentMessages.last else { return }
                    withAnimation(.easeOut(duration: 0.2)) {
                        proxy.scrollTo(last.id, anchor: .bottom)
                    }
                }
            }
            .amaryllisCard()

            HStack(alignment: .bottom, spacing: 8) {
                TextEditor(text: $inputText)
                    .font(AmaryllisTheme.bodyFont(size: 14, weight: .medium))
                    .frame(minHeight: 64, maxHeight: 120)
                    .padding(6)
                    .background(AmaryllisTheme.surface)
                    .clipShape(RoundedRectangle(cornerRadius: 10))

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
            Task { await appState.refreshToolingState() }
        }
        .onChange(of: appState.selectedModel ?? "") { _ in
            if selectedModelID.isEmpty {
                selectedModelID = appState.selectedModel ?? ""
            }
        }
    }

    private var currentMessages: [LocalChatMessage] {
        appState.currentChatMessages
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
                    RoundedRectangle(cornerRadius: 10)
                        .stroke(AmaryllisTheme.border.opacity(0.6), lineWidth: 1)
                )
                .clipShape(RoundedRectangle(cornerRadius: 10))
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

    private var controlBar: some View {
        HStack(spacing: 8) {
            Picker("Provider", selection: $selectedProvider) {
                Text("auto").tag("")
                ForEach(providerNames, id: \.self) { provider in
                    Text(provider).tag(provider)
                }
            }
            .pickerStyle(.menu)
            .frame(width: 140)

            Picker("Model", selection: $selectedModelID) {
                Text("active").tag("")
                ForEach(modelOptions, id: \.id) { item in
                    Text(item.id).tag(item.id)
                }
            }
            .pickerStyle(.menu)
            .frame(maxWidth: .infinity)

            Toggle("Stream", isOn: $isStreaming)
                .toggleStyle(.switch)
                .font(AmaryllisTheme.bodyFont(size: 12, weight: .semibold))
                .frame(width: 120)

            Toggle("Tools", isOn: $toolsEnabled)
                .toggleStyle(.switch)
                .font(AmaryllisTheme.bodyFont(size: 12, weight: .semibold))
                .frame(width: 110)
        }
        .amaryllisCard()
    }

    private func bubble(for message: LocalChatMessage) -> some View {
        let isUser = message.role == "user"

        return HStack {
            if isUser { Spacer() }
            VStack(alignment: .leading, spacing: 4) {
                Text(isUser ? "You" : "Amaryllis")
                    .font(AmaryllisTheme.bodyFont(size: 10, weight: .bold))
                    .foregroundStyle(AmaryllisTheme.textSecondary)
                Text(message.content)
                    .font(AmaryllisTheme.bodyFont(size: 14, weight: .medium))
                    .foregroundStyle(AmaryllisTheme.textPrimary)
                    .textSelection(.enabled)
            }
            .padding(10)
            .background(isUser ? AmaryllisTheme.accentSoft : AmaryllisTheme.surfaceAlt)
            .clipShape(RoundedRectangle(cornerRadius: 10))
            .overlay(
                RoundedRectangle(cornerRadius: 10)
                    .stroke(isUser ? AmaryllisTheme.accent.opacity(0.9) : AmaryllisTheme.border.opacity(0.35), lineWidth: 1)
            )
            .frame(maxWidth: 680, alignment: .leading)
            if !isUser { Spacer() }
        }
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

        var payload: [APIChatMessage] = [
            APIChatMessage(role: "system", content: systemPrompt, name: nil)
        ]

        for row in appState.currentChatMessages where row.role == "user" || row.role == "assistant" {
            if !row.content.isEmpty {
                payload.append(APIChatMessage(role: row.role, content: row.content, name: nil))
            }
        }

        let provider = selectedProvider.isEmpty ? nil : selectedProvider
        let model = selectedModelID.isEmpty ? nil : selectedModelID
        let tools = toolsEnabled ? chatTools : []
        let shouldUseStreaming = isStreaming && tools.isEmpty

        Task {
            do {
                if shouldUseStreaming {
                    var combined = ""
                    let stream = appState.apiClient.streamChatCompletions(
                        model: model,
                        provider: provider,
                        messages: payload,
                        tools: nil
                    )

                    for try await chunk in stream {
                        combined += chunk
                        await MainActor.run {
                            appState.updateCurrentChatMessage(id: assistantID, content: combined)
                        }
                    }

                    if combined.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                        await MainActor.run {
                            appState.updateCurrentChatMessage(
                                id: assistantID,
                                content: "Error: Empty response from provider. Check API key, quota, and model access."
                            )
                            appState.lastError = "Empty response from provider."
                        }
                    }
                } else {
                    if isStreaming && !tools.isEmpty {
                        await MainActor.run {
                            appState.lastError = "Streaming with tools is disabled; using non-stream mode for tool loop."
                        }
                    }

                    var response = try await appState.apiClient.chatCompletions(
                        model: model,
                        provider: provider,
                        messages: payload,
                        tools: tools.isEmpty ? nil : tools
                    )

                    let pendingPromptIDs = pendingPermissionPromptIDs(from: response.toolEvents)
                    var content = response.choices.first?.message.content ?? ""
                    let firstTrace = renderToolTrace(response.toolEvents)
                    if !firstTrace.isEmpty {
                        content += "\n\n\(firstTrace)"
                    }

                    await MainActor.run {
                        appState.updateCurrentChatMessage(id: assistantID, content: content)
                    }

                    if !pendingPromptIDs.isEmpty {
                        await MainActor.run {
                            appState.lastError = "Tool permission required. Approve in Settings -> Tools & MCP. Waiting for approval..."
                        }
                        let approvalState = try await waitForPromptDecision(promptIDs: pendingPromptIDs, timeoutSec: 120)
                        if approvalState == .approved {
                            response = try await appState.apiClient.chatCompletions(
                                model: model,
                                provider: provider,
                                messages: payload,
                                tools: tools.isEmpty ? nil : tools,
                                permissionIds: pendingPromptIDs
                            )

                            var retriedContent = response.choices.first?.message.content ?? content
                            let retryTrace = renderToolTrace(response.toolEvents)
                            if !retryTrace.isEmpty {
                                retriedContent += "\n\n\(retryTrace)"
                            }
                            await MainActor.run {
                                appState.updateCurrentChatMessage(id: assistantID, content: retriedContent)
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
                    appState.updateCurrentChatMessage(id: assistantID, content: "Error: \(error.localizedDescription)")
                    appState.lastError = error.localizedDescription
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
}
