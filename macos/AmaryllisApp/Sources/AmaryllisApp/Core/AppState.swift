import Foundation
import SwiftUI

@MainActor
final class AppState: ObservableObject {
    @Published var selectedTab: AppTab = .chat
    @Published var endpoint: String
    @Published var runtimeDirectory: String
    @Published var modelCatalog: APIModelCatalog?
    @Published var selectedModel: String?
    @Published var selectedProvider: String?
    @Published var chatSessions: [LocalChatSession] = []
    @Published var selectedChatID: UUID?
    @Published var isBusy: Bool = false
    @Published var lastError: String?

    let runtimeManager = RuntimeProcessManager()

    private let endpointKey = "amaryllis.endpoint"
    private let runtimeDirKey = "amaryllis.runtimeDirectory"
    private let selectedChatKey = "amaryllis.selectedChatID"

    lazy var apiClient = AmaryllisAPIClient(baseURLProvider: { [unowned self] in
        self.endpoint
    })

    init() {
        let defaults = UserDefaults.standard
        self.endpoint = defaults.string(forKey: endpointKey) ?? "http://localhost:8000"

        let discoveredRuntimeDir = AppState.discoverRuntimeDirectory()
        let persistedRuntimeDir = defaults.string(forKey: runtimeDirKey)
        if let persistedRuntimeDir, AppState.containsRuntime(in: persistedRuntimeDir) {
            self.runtimeDirectory = persistedRuntimeDir
        } else {
            self.runtimeDirectory = discoveredRuntimeDir
        }

        loadChats()
        ensureChatExists()
    }

    func persistSettings() {
        let defaults = UserDefaults.standard
        defaults.set(endpoint, forKey: endpointKey)
        defaults.set(runtimeDirectory, forKey: runtimeDirKey)
    }

    func clearError() {
        lastError = nil
    }

    func refreshHealth() async {
        do {
            _ = try await apiClient.health()
            runtimeManager.connectionState = .online
            lastError = nil
        } catch {
            runtimeManager.connectionState = .offline
            lastError = error.localizedDescription
        }
    }

    func refreshModels() async {
        isBusy = true
        defer { isBusy = false }

        guard await ensureRuntimeOnline() else {
            return
        }

        do {
            let catalog = try await apiClient.listModels()
            modelCatalog = catalog
            selectedModel = catalog.active.model
            selectedProvider = catalog.active.provider
            lastError = nil
        } catch {
            lastError = error.localizedDescription
        }
    }

    func loadModel(modelId: String, provider: String?) async {
        isBusy = true
        defer { isBusy = false }

        guard await ensureRuntimeOnline() else {
            return
        }

        do {
            _ = try await apiClient.loadModel(modelId: modelId, provider: provider)
            await refreshModels()
            lastError = nil
        } catch {
            lastError = error.localizedDescription
        }
    }

    func downloadModel(modelId: String, provider: String?) async {
        isBusy = true
        defer { isBusy = false }

        guard await ensureRuntimeOnline() else {
            return
        }

        do {
            _ = try await apiClient.downloadModel(modelId: modelId, provider: provider)
            await refreshModels()
            lastError = nil
        } catch {
            lastError = error.localizedDescription
        }
    }

    func startRuntime() {
        let host = URL(string: endpoint)?.host ?? "localhost"
        let port = URL(string: endpoint)?.port ?? 8000
        runtimeManager.start(runtimeDirectory: runtimeDirectory, host: host, port: port)
    }

    func stopRuntime() {
        runtimeManager.stop()
    }

    var currentChatMessages: [LocalChatMessage] {
        guard let selectedChatID else { return [] }
        return chatSessions.first(where: { $0.id == selectedChatID })?.messages ?? []
    }

    var currentChatTitle: String {
        guard let selectedChatID else { return "New chat" }
        return chatSessions.first(where: { $0.id == selectedChatID })?.title ?? "New chat"
    }

    func ensureChatExists() {
        if chatSessions.isEmpty {
            _ = createChat()
            return
        }

        if let selectedChatID, chatSessions.contains(where: { $0.id == selectedChatID }) {
            return
        }

        selectedChatID = chatSessions[0].id
        persistSelectedChatID()
    }

    @discardableResult
    func createChat(title: String = "New chat") -> UUID {
        let now = Date()
        let session = LocalChatSession(
            id: UUID(),
            title: title,
            createdAt: now,
            updatedAt: now,
            messages: []
        )

        chatSessions.insert(session, at: 0)
        selectedChatID = session.id
        persistChats()
        persistSelectedChatID()
        return session.id
    }

    func selectChat(id: UUID) {
        guard chatSessions.contains(where: { $0.id == id }) else { return }
        selectedChatID = id
        persistSelectedChatID()
    }

    func deleteCurrentChat() {
        guard let selectedChatID else { return }
        guard let index = chatSessions.firstIndex(where: { $0.id == selectedChatID }) else { return }

        if chatSessions.count == 1 {
            let now = Date()
            chatSessions[0] = LocalChatSession(
                id: chatSessions[0].id,
                title: "New chat",
                createdAt: chatSessions[0].createdAt,
                updatedAt: now,
                messages: []
            )
            self.selectedChatID = chatSessions[0].id
        } else {
            chatSessions.remove(at: index)
            self.selectedChatID = chatSessions.first?.id
        }

        persistChats()
        persistSelectedChatID()
    }

    @discardableResult
    func appendUserMessageToCurrentChat(_ text: String) -> UUID {
        ensureChatExists()
        let message = LocalChatMessage(id: UUID(), role: "user", content: text, createdAt: Date())

        mutateSelectedChat { session in
            session.messages.append(message)
            if session.title == "New chat" || session.title.isEmpty {
                session.title = Self.chatTitle(from: text)
            }
        }
        return message.id
    }

    @discardableResult
    func appendAssistantPlaceholderToCurrentChat() -> UUID {
        ensureChatExists()
        let message = LocalChatMessage(id: UUID(), role: "assistant", content: "", createdAt: Date())
        mutateSelectedChat { session in
            session.messages.append(message)
        }
        return message.id
    }

    func updateCurrentChatMessage(id: UUID, content: String) {
        mutateSelectedChat { session in
            guard let index = session.messages.firstIndex(where: { $0.id == id }) else { return }
            session.messages[index].content = content
        }
    }

    private func mutateSelectedChat(_ update: (inout LocalChatSession) -> Void) {
        ensureChatExists()
        guard let selectedChatID else { return }
        guard let index = chatSessions.firstIndex(where: { $0.id == selectedChatID }) else { return }

        var session = chatSessions.remove(at: index)
        update(&session)
        session.updatedAt = Date()

        chatSessions.insert(session, at: 0)
        self.selectedChatID = session.id
        persistChats()
        persistSelectedChatID()
    }

    private static func chatTitle(from text: String) -> String {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return "New chat" }
        if trimmed.count <= 40 {
            return trimmed
        }
        let end = trimmed.index(trimmed.startIndex, offsetBy: 40)
        return "\(trimmed[..<end])..."
    }

    private func loadChats() {
        let url = chatStoreURL()
        guard FileManager.default.fileExists(atPath: url.path) else {
            chatSessions = []
            selectedChatID = nil
            return
        }

        do {
            let data = try Data(contentsOf: url)
            let decoder = JSONDecoder()
            decoder.dateDecodingStrategy = .iso8601

            let decoded = try decoder.decode([LocalChatSession].self, from: data)
            chatSessions = decoded.sorted(by: { $0.updatedAt > $1.updatedAt })

            let defaults = UserDefaults.standard
            if let rawID = defaults.string(forKey: selectedChatKey),
               let restoredID = UUID(uuidString: rawID),
               chatSessions.contains(where: { $0.id == restoredID }) {
                selectedChatID = restoredID
            } else {
                selectedChatID = chatSessions.first?.id
            }
        } catch {
            chatSessions = []
            selectedChatID = nil
            lastError = "Failed to load saved chats: \(error.localizedDescription)"
        }
    }

    private func persistChats() {
        do {
            let directory = chatStoreURL().deletingLastPathComponent()
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)

            let encoder = JSONEncoder()
            encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
            encoder.dateEncodingStrategy = .iso8601

            let data = try encoder.encode(chatSessions)
            try data.write(to: chatStoreURL(), options: .atomic)
        } catch {
            lastError = "Failed to save chats: \(error.localizedDescription)"
        }
    }

    private func persistSelectedChatID() {
        let defaults = UserDefaults.standard
        if let selectedChatID {
            defaults.set(selectedChatID.uuidString, forKey: selectedChatKey)
        } else {
            defaults.removeObject(forKey: selectedChatKey)
        }
    }

    private func chatStoreURL() -> URL {
        let support = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
            ?? URL(fileURLWithPath: NSHomeDirectory(), isDirectory: true)
        return support
            .appendingPathComponent("amaryllis", isDirectory: true)
            .appendingPathComponent("chat_sessions.json", isDirectory: false)
    }

    private func ensureRuntimeOnline() async -> Bool {
        if await checkHealthOnce() {
            return true
        }

        if !runtimeManager.isRunning {
            startRuntime()
        }

        for _ in 0..<20 {
            try? await Task.sleep(nanoseconds: 500_000_000)
            if await checkHealthOnce() {
                return true
            }
            if runtimeManager.processState == .failed || runtimeManager.processState == .stopped {
                break
            }
        }

        runtimeManager.connectionState = .offline
        let logsHint = runtimeManager.logs.suffix(2).joined(separator: " | ")
        if logsHint.isEmpty {
            lastError = "Could not connect to the server. Open Settings and set Runtime Directory to your Amaryllis project root."
        } else {
            lastError = "Could not connect to the server. \(logsHint)"
        }
        return false
    }

    private func checkHealthOnce() async -> Bool {
        do {
            _ = try await apiClient.health()
            runtimeManager.connectionState = .online
            lastError = nil
            return true
        } catch {
            runtimeManager.connectionState = .offline
            return false
        }
    }

    private static func discoverRuntimeDirectory() -> String {
        let fm = FileManager.default
        let supportRuntime = (PathInfo.homeDirectory as NSString)
            .appendingPathComponent("Library/Application Support/amaryllis/runtime-src")
        if containsRuntime(in: supportRuntime) {
            return supportRuntime
        }

        let cwd = fm.currentDirectoryPath
        if let found = searchUpwardForRuntime(from: cwd, maxDepth: 8) {
            return found
        }

        let bundlePath = Bundle.main.bundleURL.path
        if let found = searchUpwardForRuntime(from: bundlePath, maxDepth: 10) {
            return found
        }

        return cwd
    }

    private static func searchUpwardForRuntime(from startPath: String, maxDepth: Int) -> String? {
        var url = URL(fileURLWithPath: startPath, isDirectory: true)
        for _ in 0..<maxDepth {
            let candidate = url.path
            if containsRuntime(in: candidate) {
                return candidate
            }

            let parent = url.deletingLastPathComponent()
            if parent.path == url.path {
                break
            }
            url = parent
        }
        return nil
    }

    private static func containsRuntime(in directory: String) -> Bool {
        let runtimeServer = URL(fileURLWithPath: directory, isDirectory: true)
            .appendingPathComponent("runtime/server.py")
            .path
        return FileManager.default.fileExists(atPath: runtimeServer)
    }
}

private enum PathInfo {
    static var homeDirectory: String {
        NSHomeDirectory()
    }
}
