import SwiftUI

struct ChatView: View {
    struct MessageRow: Identifiable {
        let id: UUID
        let role: String
        var content: String

        init(id: UUID = UUID(), role: String, content: String) {
            self.id = id
            self.role = role
            self.content = content
        }
    }

    @EnvironmentObject private var appState: AppState

    @State private var inputText: String = ""
    @State private var messages: [MessageRow] = []
    @State private var isStreaming: Bool = true
    @State private var selectedModelID: String = ""
    @State private var selectedProvider: String = ""
    @State private var isSending: Bool = false

    private let systemPrompt = "You are Amaryllis, a concise and practical local AI assistant."

    var body: some View {
        VStack(spacing: 10) {
            controlBar

            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 8) {
                        ForEach(messages) { message in
                            bubble(for: message)
                                .id(message.id)
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
                .onChange(of: messages.count) { _ in
                    if let last = messages.last {
                        withAnimation(.easeOut(duration: 0.2)) {
                            proxy.scrollTo(last.id, anchor: .bottom)
                        }
                    }
                }
            }
            .amaryllisCard()

            HStack(alignment: .bottom, spacing: 8) {
                TextEditor(text: $inputText)
                    .font(.system(size: 14, weight: .medium, design: .rounded))
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
                            .font(.system(size: 14, weight: .bold))
                            .frame(width: 88)
                    }
                }
                .buttonStyle(.borderedProminent)
                .tint(AmaryllisTheme.accent)
                .disabled(isSending || inputText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
            .amaryllisCard()
        }
        .onAppear {
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
                .font(.system(size: 12, weight: .semibold))
                .frame(width: 120)
        }
        .amaryllisCard()
    }

    private func bubble(for message: MessageRow) -> some View {
        let isUser = message.role == "user"

        return HStack {
            if isUser { Spacer() }
            VStack(alignment: .leading, spacing: 4) {
                Text(isUser ? "You" : "Amaryllis")
                    .font(.system(size: 10, weight: .bold))
                    .foregroundStyle(AmaryllisTheme.textSecondary)
                Text(message.content)
                    .font(.system(size: 14, weight: .medium, design: .rounded))
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

        messages.append(MessageRow(role: "user", content: text))
        inputText = ""

        let assistantID = UUID()
        messages.append(MessageRow(id: assistantID, role: "assistant", content: ""))

        isSending = true
        appState.clearError()

        var payload: [APIChatMessage] = [
            APIChatMessage(role: "system", content: systemPrompt, name: nil)
        ]

        for row in messages where row.role == "user" || row.role == "assistant" {
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
                            replaceMessage(id: assistantID, content: combined)
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
                    replaceMessage(id: assistantID, content: content)
                }

                await appState.refreshHealth()
            } catch {
                await MainActor.run {
                    replaceMessage(id: assistantID, content: "Error: \(error.localizedDescription)")
                    appState.lastError = error.localizedDescription
                }
            }

            await MainActor.run {
                isSending = false
            }
        }
    }

    private func replaceMessage(id: UUID, content: String) {
        guard let index = messages.firstIndex(where: { $0.id == id }) else { return }
        messages[index].content = content
    }
}
