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

    @State private var systemPrompt: String = "You are Amaryllis, a concise and practical local AI assistant."
    @State private var inputText: String = ""
    @State private var messages: [MessageRow] = []
    @State private var isStreaming: Bool = true
    @State private var enableTools: Bool = false
    @State private var selectedModelID: String = ""
    @State private var selectedProvider: String = ""
    @State private var isSending: Bool = false

    var body: some View {
        VStack(spacing: 12) {
            controlBar

            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 10) {
                        ForEach(messages) { message in
                            bubble(for: message)
                                .id(message.id)
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
                .background(AmaryllisTheme.background)
                .onChange(of: messages.count) { _ in
                    if let last = messages.last {
                        withAnimation(.easeOut(duration: 0.25)) {
                            proxy.scrollTo(last.id, anchor: .bottom)
                        }
                    }
                }
            }

            HStack(alignment: .bottom, spacing: 8) {
                TextEditor(text: $inputText)
                    .font(.system(size: 14, weight: .medium, design: .rounded))
                    .frame(minHeight: 68, maxHeight: 130)
                    .padding(8)
                    .background(AmaryllisTheme.surface)
                    .clipShape(RoundedRectangle(cornerRadius: 12))
                    .overlay(
                        RoundedRectangle(cornerRadius: 12)
                            .stroke(AmaryllisTheme.border.opacity(0.5), lineWidth: 1)
                    )

                Button(action: send) {
                    if isSending {
                        ProgressView()
                            .controlSize(.small)
                            .tint(.white)
                            .frame(width: 96)
                    } else {
                        Text("Send")
                            .font(.system(size: 14, weight: .bold))
                            .frame(width: 96)
                    }
                }
                .buttonStyle(.borderedProminent)
                .tint(AmaryllisTheme.accent)
                .disabled(isSending || inputText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }
        .padding(6)
        .background(AmaryllisTheme.background)
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
        VStack(spacing: 10) {
            HStack(spacing: 10) {
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

                Toggle("Tools", isOn: $enableTools)
                    .toggleStyle(.switch)
                    .font(.system(size: 12, weight: .semibold))
            }

            TextField("System prompt", text: $systemPrompt)
                .textFieldStyle(.roundedBorder)
                .font(.system(size: 12, weight: .medium))
        }
        .amaryllisCard()
    }

    private func bubble(for message: MessageRow) -> some View {
        let isUser = message.role == "user"

        return HStack {
            if isUser { Spacer() }
            VStack(alignment: .leading, spacing: 6) {
                Text(isUser ? "YOU" : "AMARYLLIS")
                    .font(.system(size: 10, weight: .black, design: .rounded))
                    .foregroundStyle(AmaryllisTheme.textSecondary)
                Text(message.content)
                    .font(.system(size: 14, weight: .medium, design: .rounded))
                    .foregroundStyle(AmaryllisTheme.textPrimary)
                    .textSelection(.enabled)
                    .multilineTextAlignment(.leading)
            }
            .padding(12)
            .background(isUser ? AmaryllisTheme.accentSoft : AmaryllisTheme.surface)
            .clipShape(RoundedRectangle(cornerRadius: 14))
            .overlay(
                RoundedRectangle(cornerRadius: 14)
                    .stroke(isUser ? AmaryllisTheme.accent : AmaryllisTheme.border.opacity(0.4), lineWidth: 1)
            )
            .frame(maxWidth: 700, alignment: .leading)
            if !isUser { Spacer() }
        }
    }

    private var modelOptions: [APIModelItem] {
        guard let catalog = appState.modelCatalog else { return [] }
        return catalog.providers
            .values
            .flatMap { $0.items }
            .sorted { $0.id < $1.id }
    }

    private var providerNames: [String] {
        guard let catalog = appState.modelCatalog else { return [] }
        return catalog.providers.keys.sorted()
    }

    private var toolDefinitions: [APIChatToolDefinition] {
        [
            APIChatToolDefinition(
                type: "function",
                function: APIChatToolFunction(
                    name: "filesystem",
                    description: "Read, write and list files in the workspace",
                    parameters: [
                        "type": .string("object"),
                        "properties": .object([
                            "action": .object([
                                "type": .string("string"),
                                "enum": .array([.string("list"), .string("read"), .string("write")])
                            ]),
                            "path": .object(["type": .string("string")]),
                            "content": .object(["type": .string("string")])
                        ]),
                        "required": .array([.string("action"), .string("path")])
                    ]
                )
            ),
            APIChatToolDefinition(
                type: "function",
                function: APIChatToolFunction(
                    name: "web_search",
                    description: "Search web pages and return links",
                    parameters: [
                        "type": .string("object"),
                        "properties": .object([
                            "query": .object(["type": .string("string")]),
                            "limit": .object(["type": .string("integer")])
                        ]),
                        "required": .array([.string("query")])
                    ]
                )
            ),
            APIChatToolDefinition(
                type: "function",
                function: APIChatToolFunction(
                    name: "python_exec",
                    description: "Execute small Python snippets",
                    parameters: [
                        "type": .string("object"),
                        "properties": .object([
                            "code": .object(["type": .string("string")]),
                            "timeout": .object(["type": .string("integer")])
                        ]),
                        "required": .array([.string("code")])
                    ]
                )
            )
        ]
    }

    private func send() {
        let text = inputText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !isSending else { return }

        let userMessage = MessageRow(role: "user", content: text)
        messages.append(userMessage)
        inputText = ""

        let assistantID = UUID()
        messages.append(MessageRow(id: assistantID, role: "assistant", content: ""))

        isSending = true
        appState.clearError()

        var payload: [APIChatMessage] = []
        let trimmedSystem = systemPrompt.trimmingCharacters(in: .whitespacesAndNewlines)
        if !trimmedSystem.isEmpty {
            payload.append(APIChatMessage(role: "system", content: trimmedSystem, name: nil))
        }
        for row in messages where row.role == "user" || row.role == "assistant" {
            if !row.content.isEmpty {
                payload.append(APIChatMessage(role: row.role, content: row.content, name: nil))
            }
        }

        let provider = selectedProvider.isEmpty ? nil : selectedProvider
        let model = selectedModelID.isEmpty ? nil : selectedModelID
        let tools = enableTools ? toolDefinitions : nil

        Task {
            do {
                if isStreaming {
                    var combined = ""
                    let stream = appState.apiClient.streamChatCompletions(
                        model: model,
                        provider: provider,
                        messages: payload,
                        tools: tools
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
                        tools: tools
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
