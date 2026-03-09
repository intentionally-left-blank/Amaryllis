import SwiftUI

struct AgentsView: View {
    @EnvironmentObject private var appState: AppState

    @State private var userID: String = "demo-user"
    @State private var sessionID: String = "demo-session"

    @State private var newAgentName: String = "Research Agent"
    @State private var newAgentPrompt: String = "You are a practical AI agent. Use tools when they are needed."
    @State private var newAgentTools: String = "web_search,filesystem"

    @State private var agents: [APIAgentRecord] = []
    @State private var selectedAgentID: String?

    @State private var chatInput: String = ""
    @State private var chatHistory: [String] = []

    @State private var isLoadingAgents: Bool = false
    @State private var isCreatingAgent: Bool = false
    @State private var isSending: Bool = false

    var body: some View {
        HStack(spacing: 12) {
            leftPanel
                .frame(minWidth: 320, idealWidth: 360, maxWidth: 420)

            rightPanel
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .task {
            await refreshAgents()
        }
    }

    private var leftPanel: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Agents")
                .font(AmaryllisTheme.titleFont(size: 28))
                .foregroundStyle(AmaryllisTheme.textPrimary)

            VStack(alignment: .leading, spacing: 8) {
                TextField("User ID", text: $userID)
                    .textFieldStyle(.roundedBorder)
                TextField("Agent name", text: $newAgentName)
                    .textFieldStyle(.roundedBorder)
                TextField("Tools (comma separated)", text: $newAgentTools)
                    .textFieldStyle(.roundedBorder)
                TextEditor(text: $newAgentPrompt)
                    .font(AmaryllisTheme.bodyFont(size: 12, weight: .medium))
                    .frame(height: 80)
                    .padding(8)
                    .background(AmaryllisTheme.surfaceAlt)
                    .clipShape(RoundedRectangle(cornerRadius: 10))

                HStack {
                    Button {
                        Task { await createAgent() }
                    } label: {
                        if isCreatingAgent {
                            ProgressView()
                                .controlSize(.small)
                                .frame(width: 90)
                        } else {
                            Text("Create")
                                .frame(width: 90)
                        }
                    }
                    .buttonStyle(AmaryllisPrimaryButtonStyle())
                    .disabled(isCreatingAgent || newAgentName.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)

                    Button("Refresh") {
                        Task { await refreshAgents() }
                    }
                    .buttonStyle(AmaryllisSecondaryButtonStyle())
                    .disabled(isLoadingAgents)
                }
            }
            .amaryllisCard()

            ScrollView {
                LazyVStack(alignment: .leading, spacing: 8) {
                    ForEach(agents) { agent in
                        Button {
                            selectedAgentID = agent.id
                            chatHistory.removeAll()
                        } label: {
                            VStack(alignment: .leading, spacing: 4) {
                                Text(agent.name)
                                    .font(AmaryllisTheme.bodyFont(size: 14, weight: .semibold))
                                    .foregroundStyle(AmaryllisTheme.textPrimary)
                                Text(agent.id)
                                    .font(AmaryllisTheme.monoFont(size: 11, weight: .regular))
                                    .foregroundStyle(AmaryllisTheme.textSecondary)
                                    .lineLimit(1)
                            }
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(10)
                            .background(
                                RoundedRectangle(cornerRadius: 10)
                                    .fill(selectedAgentID == agent.id ? AmaryllisTheme.accentSoft : AmaryllisTheme.surface)
                            )
                            .overlay(
                                RoundedRectangle(cornerRadius: 10)
                                    .stroke(selectedAgentID == agent.id ? AmaryllisTheme.accent : AmaryllisTheme.border.opacity(0.4), lineWidth: 1)
                            )
                        }
                        .buttonStyle(.plain)
                    }
                }
            }
            .amaryllisCard()

            if let error = appState.lastError {
                Text(error)
                    .font(AmaryllisTheme.bodyFont(size: 12, weight: .medium))
                    .foregroundStyle(AmaryllisTheme.accent)
            }
        }
    }

    private var rightPanel: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Agent Chat")
                .font(AmaryllisTheme.titleFont(size: 28))
                .foregroundStyle(AmaryllisTheme.textPrimary)

            if let selected = selectedAgent {
                Text("Selected: \(selected.name)")
                    .font(AmaryllisTheme.bodyFont(size: 12, weight: .semibold))
                    .foregroundStyle(AmaryllisTheme.textSecondary)
            } else {
                Text("Select an agent to start")
                    .font(AmaryllisTheme.bodyFont(size: 12, weight: .semibold))
                    .foregroundStyle(AmaryllisTheme.textSecondary)
            }

            ScrollView {
                VStack(alignment: .leading, spacing: 8) {
                    ForEach(chatHistory.indices, id: \.self) { index in
                        Text(chatHistory[index])
                            .font(AmaryllisTheme.bodyFont(size: 13, weight: .medium))
                            .foregroundStyle(AmaryllisTheme.textPrimary)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(10)
                            .background(AmaryllisTheme.surface)
                            .clipShape(RoundedRectangle(cornerRadius: 10))
                    }
                }
            }
            .amaryllisCard()

            HStack(spacing: 8) {
                TextField("Session ID", text: $sessionID)
                    .textFieldStyle(.roundedBorder)
                    .frame(width: 180)

                TextField("Message", text: $chatInput)
                    .textFieldStyle(.roundedBorder)

                Button {
                    Task { await sendAgentMessage() }
                } label: {
                    if isSending {
                        ProgressView()
                            .controlSize(.small)
                            .frame(width: 90)
                    } else {
                        Text("Send")
                            .frame(width: 90)
                    }
                }
                .buttonStyle(AmaryllisPrimaryButtonStyle())
                .disabled(isSending || selectedAgent == nil || chatInput.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }
        .amaryllisCard()
    }

    private var selectedAgent: APIAgentRecord? {
        guard let id = selectedAgentID else { return nil }
        return agents.first(where: { $0.id == id })
    }

    private func createAgent() async {
        let trimmedName = newAgentName.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmedName.isEmpty else { return }

        isCreatingAgent = true
        defer { isCreatingAgent = false }

        do {
            let tools = newAgentTools
                .split(separator: ",")
                .map { String($0).trimmingCharacters(in: .whitespacesAndNewlines) }
                .filter { !$0.isEmpty }

            let model = appState.modelCatalog?.active.model
            let agent = try await appState.apiClient.createAgent(
                name: trimmedName,
                systemPrompt: newAgentPrompt,
                model: model,
                tools: tools,
                userId: userID
            )

            selectedAgentID = agent.id
            await refreshAgents()
            appState.clearError()
        } catch {
            appState.lastError = error.localizedDescription
        }
    }

    private func refreshAgents() async {
        isLoadingAgents = true
        defer { isLoadingAgents = false }

        do {
            let response = try await appState.apiClient.listAgents(userId: userID)
            agents = response.items
            if selectedAgentID == nil {
                selectedAgentID = agents.first?.id
            }
            appState.clearError()
        } catch {
            appState.lastError = error.localizedDescription
        }
    }

    private func sendAgentMessage() async {
        guard let agent = selectedAgent else { return }
        let text = chatInput.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }

        isSending = true
        defer { isSending = false }

        chatInput = ""
        chatHistory.append("USER: \(text)")

        do {
            let response = try await appState.apiClient.chatAgent(
                agentId: agent.id,
                userId: userID,
                message: text,
                sessionId: sessionID.isEmpty ? nil : sessionID
            )
            chatHistory.append("AGENT (\(response.strategy)): \(response.response)")
            appState.clearError()
        } catch {
            chatHistory.append("ERROR: \(error.localizedDescription)")
            appState.lastError = error.localizedDescription
        }
    }
}
