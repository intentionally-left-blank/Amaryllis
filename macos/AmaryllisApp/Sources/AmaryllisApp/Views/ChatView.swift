import SwiftUI

struct ChatView: View {
    @EnvironmentObject private var appState: AppState

    @State private var inputText: String = ""
    @State private var isStreaming: Bool = true
    @State private var selectedModelID: String = ""
    @State private var selectedProvider: String = ""
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

        Task {
            do {
                if isStreaming {
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
                    let response = try await appState.apiClient.chatCompletions(
                        model: model,
                        provider: provider,
                        messages: payload,
                        tools: nil
                    )
                    let content = response.choices.first?.message.content ?? ""
                    await MainActor.run {
                        appState.updateCurrentChatMessage(id: assistantID, content: content)
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
}
