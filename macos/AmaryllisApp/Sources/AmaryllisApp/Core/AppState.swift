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
    @Published var openAIBaseURL: String
    @Published var openAIAPIKey: String
    @Published var openRouterBaseURL: String
    @Published var openRouterAPIKey: String
    @Published var runtimeAuthToken: String
    @Published var toolApprovalEnforcement: String
    @Published var toolIsolationProfile: String
    @Published var blockedTools: String
    @Published var allowedHighRiskTools: String
    @Published var toolPythonExecMaxTimeoutSec: String
    @Published var toolPythonExecMaxCodeChars: String
    @Published var toolFilesystemAllowWrite: Bool
    @Published var toolBudgetWindowSec: String
    @Published var toolBudgetMaxCallsPerTool: String
    @Published var toolBudgetMaxTotalCalls: String
    @Published var toolBudgetMaxHighRiskCalls: String
    @Published var pluginSigningKey: String
    @Published var mcpEndpoints: String
    @Published var mcpTimeoutSec: String
    @Published var availableTools: [APIToolItem] = []
    @Published var permissionPrompts: [APIPermissionPrompt] = []
    @Published var modelDownloadJobs: [String: APIModelDownloadJob] = [:]
    @Published var chatSessions: [LocalChatSession] = []
    @Published var selectedChatID: UUID?
    @Published var isBusy: Bool = false
    @Published var isQuickSetupRunning: Bool = false
    @Published var lastError: String?

    let runtimeManager = RuntimeProcessManager()
    private var modelsRefreshInFlight: Bool = false
    private var pendingModelsRefreshWithSuggested: Bool = false
    private var pendingChatPersistTask: Task<Void, Never>?
    private let chatPersistDebounceNanos: UInt64 = 350_000_000
    private var selectedChatIndex: Int?
    private var chatPersistRevision: UInt64 = 0
    private let chatStoreWriter = ChatStoreWriter()

    private let endpointKey = "amaryllis.endpoint"
    private let runtimeDirKey = "amaryllis.runtimeDirectory"
    private let selectedChatKey = "amaryllis.selectedChatID"
    private let openAIBaseURLKey = "amaryllis.openai.baseURL"
    private let openRouterBaseURLKey = "amaryllis.openrouter.baseURL"
    private let runtimeAuthTokenKey = "amaryllis.runtime.authToken"
    private let toolApprovalEnforcementKey = "amaryllis.tools.approvalEnforcement"
    private let toolIsolationProfileKey = "amaryllis.tools.isolationProfile"
    private let blockedToolsKey = "amaryllis.tools.blockedTools"
    private let allowedHighRiskToolsKey = "amaryllis.tools.allowedHighRiskTools"
    private let toolPythonExecMaxTimeoutSecKey = "amaryllis.tools.pythonExecMaxTimeoutSec"
    private let toolPythonExecMaxCodeCharsKey = "amaryllis.tools.pythonExecMaxCodeChars"
    private let toolFilesystemAllowWriteKey = "amaryllis.tools.filesystemAllowWrite"
    private let toolBudgetWindowSecKey = "amaryllis.tools.budget.windowSec"
    private let toolBudgetMaxCallsPerToolKey = "amaryllis.tools.budget.maxCallsPerTool"
    private let toolBudgetMaxTotalCallsKey = "amaryllis.tools.budget.maxTotalCalls"
    private let toolBudgetMaxHighRiskCallsKey = "amaryllis.tools.budget.maxHighRiskCalls"
    private let pluginSigningKeyKey = "amaryllis.tools.pluginSigningKey"
    private let mcpEndpointsKey = "amaryllis.mcp.endpoints"
    private let mcpTimeoutSecKey = "amaryllis.mcp.timeoutSec"
    private let keychainService = "org.amaryllis.app.credentials"
    private let openAIKeychainAccount = "openai_api_key"
    private let openRouterKeychainAccount = "openrouter_api_key"

    lazy var apiClient = AmaryllisAPIClient(
        baseURLProvider: { [unowned self] in
            self.endpoint
        },
        authTokenProvider: { [unowned self] in
            self.runtimeAuthToken
        }
    )

    init() {
        let defaults = UserDefaults.standard
        self.endpoint = defaults.string(forKey: endpointKey) ?? "http://localhost:8000"
        self.openAIBaseURL = defaults.string(forKey: openAIBaseURLKey) ?? "https://api.openai.com/v1"
        self.openRouterBaseURL = defaults.string(forKey: openRouterBaseURLKey) ?? "https://openrouter.ai/api/v1"
        self.openAIAPIKey = KeychainStore.get(service: keychainService, account: openAIKeychainAccount) ?? ""
        self.openRouterAPIKey = KeychainStore.get(service: keychainService, account: openRouterKeychainAccount) ?? ""
        let persistedAuthToken = defaults.string(forKey: runtimeAuthTokenKey) ?? ""
        let normalizedAuthToken = persistedAuthToken.trimmingCharacters(in: .whitespacesAndNewlines)
        if normalizedAuthToken.isEmpty {
            let generated = Self.generateDefaultRuntimeAuthToken()
            self.runtimeAuthToken = generated
            defaults.set(generated, forKey: runtimeAuthTokenKey)
        } else {
            self.runtimeAuthToken = normalizedAuthToken
        }
        self.toolApprovalEnforcement = defaults.string(forKey: toolApprovalEnforcementKey) ?? "strict"
        self.toolIsolationProfile = defaults.string(forKey: toolIsolationProfileKey) ?? "balanced"
        self.blockedTools = defaults.string(forKey: blockedToolsKey) ?? ""
        self.allowedHighRiskTools = defaults.string(forKey: allowedHighRiskToolsKey) ?? ""
        self.toolPythonExecMaxTimeoutSec = defaults.string(forKey: toolPythonExecMaxTimeoutSecKey) ?? "10"
        self.toolPythonExecMaxCodeChars = defaults.string(forKey: toolPythonExecMaxCodeCharsKey) ?? "4000"
        if defaults.object(forKey: toolFilesystemAllowWriteKey) == nil {
            self.toolFilesystemAllowWrite = true
        } else {
            self.toolFilesystemAllowWrite = defaults.bool(forKey: toolFilesystemAllowWriteKey)
        }
        self.toolBudgetWindowSec = defaults.string(forKey: toolBudgetWindowSecKey) ?? "60"
        self.toolBudgetMaxCallsPerTool = defaults.string(forKey: toolBudgetMaxCallsPerToolKey) ?? "12"
        self.toolBudgetMaxTotalCalls = defaults.string(forKey: toolBudgetMaxTotalCallsKey) ?? "40"
        self.toolBudgetMaxHighRiskCalls = defaults.string(forKey: toolBudgetMaxHighRiskCallsKey) ?? "4"
        self.pluginSigningKey = defaults.string(forKey: pluginSigningKeyKey) ?? ""
        self.mcpEndpoints = defaults.string(forKey: mcpEndpointsKey) ?? ""
        self.mcpTimeoutSec = defaults.string(forKey: mcpTimeoutSecKey) ?? "10"

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
        defaults.set(endpoint.trimmingCharacters(in: .whitespacesAndNewlines), forKey: endpointKey)
        defaults.set(runtimeDirectory.trimmingCharacters(in: .whitespacesAndNewlines), forKey: runtimeDirKey)
        defaults.set(openAIBaseURL.trimmingCharacters(in: .whitespacesAndNewlines), forKey: openAIBaseURLKey)
        defaults.set(openRouterBaseURL.trimmingCharacters(in: .whitespacesAndNewlines), forKey: openRouterBaseURLKey)
        defaults.set(normalizedRuntimeAuthToken(), forKey: runtimeAuthTokenKey)
        defaults.set(normalizedApprovalMode(), forKey: toolApprovalEnforcementKey)
        defaults.set(normalizedIsolationProfile(), forKey: toolIsolationProfileKey)
        defaults.set(blockedTools.trimmingCharacters(in: .whitespacesAndNewlines), forKey: blockedToolsKey)
        defaults.set(allowedHighRiskTools.trimmingCharacters(in: .whitespacesAndNewlines), forKey: allowedHighRiskToolsKey)
        defaults.set(normalizedPythonExecMaxTimeout(), forKey: toolPythonExecMaxTimeoutSecKey)
        defaults.set(normalizedPythonExecMaxCodeChars(), forKey: toolPythonExecMaxCodeCharsKey)
        defaults.set(toolFilesystemAllowWrite, forKey: toolFilesystemAllowWriteKey)
        defaults.set(normalizedToolBudgetWindow(), forKey: toolBudgetWindowSecKey)
        defaults.set(normalizedToolBudgetMaxCallsPerTool(), forKey: toolBudgetMaxCallsPerToolKey)
        defaults.set(normalizedToolBudgetMaxTotalCalls(), forKey: toolBudgetMaxTotalCallsKey)
        defaults.set(normalizedToolBudgetMaxHighRiskCalls(), forKey: toolBudgetMaxHighRiskCallsKey)
        defaults.set(pluginSigningKey.trimmingCharacters(in: .whitespacesAndNewlines), forKey: pluginSigningKeyKey)
        defaults.set(mcpEndpoints.trimmingCharacters(in: .whitespacesAndNewlines), forKey: mcpEndpointsKey)
        defaults.set(normalizedMCPTimeout(), forKey: mcpTimeoutSecKey)

        saveSecret(openAIAPIKey, account: openAIKeychainAccount)
        saveSecret(openRouterAPIKey, account: openRouterKeychainAccount)
    }

    func clearError() {
        lastError = nil
    }

    var hasActiveModelConfigured: Bool {
        guard let catalog = modelCatalog else { return false }
        let model = catalog.active.model.trimmingCharacters(in: .whitespacesAndNewlines)
        return !model.isEmpty && model != "-"
    }

    var needsQuickSetup: Bool {
        runtimeManager.connectionState != .online || !hasActiveModelConfigured
    }

    func quickSetup() async {
        if isQuickSetupRunning {
            return
        }
        isQuickSetupRunning = true
        defer { isQuickSetupRunning = false }

        persistSettings()

        if !(await checkHealthOnce()) {
            if runtimeManager.isRunning {
                await refreshHealth()
            } else {
                await startRuntimeFromSettings()
            }
        }

        guard await ensureRuntimeOnline() else {
            return
        }

        await refreshModels(includeSuggested: false)
        await refreshToolingState()

        if !hasActiveModelConfigured {
            lastError = "Runtime is online, but no model is active yet. Open Models and press Install & Use on a recommended model."
        } else {
            lastError = nil
        }
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

    func refreshModels(
        includeSuggested: Bool = false,
        includeRemoteProviders: Bool = false
    ) async {
        if modelsRefreshInFlight {
            if includeSuggested {
                pendingModelsRefreshWithSuggested = true
            }
            return
        }
        let shouldIncludeSuggested = includeSuggested || pendingModelsRefreshWithSuggested
        pendingModelsRefreshWithSuggested = false
        modelsRefreshInFlight = true
        isBusy = true

        guard await ensureRuntimeOnline() else {
            isBusy = false
            modelsRefreshInFlight = false
            return
        }

        do {
            let catalog = try await apiClient.listModels(
                includeSuggested: shouldIncludeSuggested,
                includeRemoteProviders: includeRemoteProviders,
                itemLimit: 80
            )
            modelCatalog = catalog
            selectedModel = catalog.active.model
            selectedProvider = catalog.active.provider
            lastError = nil
        } catch {
            lastError = error.localizedDescription
        }
        isBusy = false
        modelsRefreshInFlight = false
        if pendingModelsRefreshWithSuggested {
            pendingModelsRefreshWithSuggested = false
            await refreshModels(includeSuggested: true, includeRemoteProviders: includeRemoteProviders)
        }
    }

    func isModelInstalled(modelId: String, provider: String) -> Bool {
        guard let catalog = modelCatalog else { return false }
        guard let payload = catalog.providers[provider] else { return false }
        return payload.items.contains(where: { $0.id == modelId })
    }

    func modelDownloadJob(modelId: String, provider: String?) -> APIModelDownloadJob? {
        let key = modelDownloadKey(modelId: modelId, provider: provider)
        return modelDownloadJobs[key]
    }

    func loadModel(modelId: String, provider: String?) async {
        isBusy = true
        defer { isBusy = false }

        guard await ensureRuntimeOnline() else {
            return
        }

        do {
            _ = try await apiClient.loadModel(modelId: modelId, provider: provider)
            await refreshModels(includeSuggested: false)
            lastError = nil
        } catch {
            lastError = error.localizedDescription
        }
    }

    func downloadModel(modelId: String, provider: String?) async {
        guard await ensureRuntimeOnline() else {
            return
        }

        let resolvedProvider = resolveProviderForModelAction(provider)
        let key = modelDownloadKey(modelId: modelId, provider: resolvedProvider)
        let provisionalJobID = "local-\(UUID().uuidString.lowercased())"
        let now = Self.isoNow()
        modelDownloadJobs[key] = APIModelDownloadJob(
            id: provisionalJobID,
            provider: resolvedProvider,
            model: modelId,
            status: "running",
            progress: 0.0,
            completedBytes: nil,
            totalBytes: nil,
            message: "Starting download...",
            error: nil,
            createdAt: now,
            updatedAt: now,
            finishedAt: nil
        )
        do {
            lastError = "Downloading \(modelId)..."
            do {
                let started = try await apiClient.startModelDownload(modelId: modelId, provider: resolvedProvider)
                modelDownloadJobs[key] = started.job
                var job = started.job
                while !job.isTerminal {
                    try await Task.sleep(nanoseconds: 700_000_000)
                    let refreshed = try await apiClient.getModelDownload(jobId: job.id)
                    job = refreshed.job
                    modelDownloadJobs[key] = job
                }

                if job.status.lowercased() == "succeeded" {
                    await refreshModels(includeSuggested: false)
                    lastError = nil
                } else {
                    let message = job.error ?? job.message ?? "Model download failed."
                    lastError = message
                }
            } catch {
                let detail = error.localizedDescription.lowercased()
                let missingAsyncDownloadAPI = detail.contains("404") || detail.contains("not found")
                if missingAsyncDownloadAPI {
                    modelDownloadJobs[key] = APIModelDownloadJob(
                        id: provisionalJobID,
                        provider: resolvedProvider,
                        model: modelId,
                        status: "running",
                        progress: 0.0,
                        completedBytes: nil,
                        totalBytes: nil,
                        message: "Downloading (legacy runtime mode)...",
                        error: nil,
                        createdAt: now,
                        updatedAt: Self.isoNow(),
                        finishedAt: nil
                    )
                    _ = try await apiClient.downloadModel(modelId: modelId, provider: resolvedProvider)
                    modelDownloadJobs[key] = APIModelDownloadJob(
                        id: provisionalJobID,
                        provider: resolvedProvider,
                        model: modelId,
                        status: "succeeded",
                        progress: 1.0,
                        completedBytes: nil,
                        totalBytes: nil,
                        message: "Download completed",
                        error: nil,
                        createdAt: now,
                        updatedAt: Self.isoNow(),
                        finishedAt: Self.isoNow()
                    )
                    await refreshModels(includeSuggested: false)
                    lastError = nil
                    return
                }
                throw error
            }
        } catch {
            modelDownloadJobs[key] = APIModelDownloadJob(
                id: provisionalJobID,
                provider: resolvedProvider,
                model: modelId,
                status: "failed",
                progress: 0.0,
                completedBytes: nil,
                totalBytes: nil,
                message: "Download failed",
                error: error.localizedDescription,
                createdAt: now,
                updatedAt: Self.isoNow(),
                finishedAt: Self.isoNow()
            )
            lastError = error.localizedDescription
        }
    }

    func installAndActivateModel(modelId: String, provider: String?) async {
        guard await ensureRuntimeOnline() else {
            return
        }

        let resolvedProvider = resolveProviderForModelAction(provider)
        if !isModelInstalled(modelId: modelId, provider: resolvedProvider) {
            await downloadModel(modelId: modelId, provider: resolvedProvider)
            let finalJob = modelDownloadJob(modelId: modelId, provider: resolvedProvider)
            if let finalJob, finalJob.status.lowercased() != "succeeded" {
                return
            }
            if finalJob == nil && !isModelInstalled(modelId: modelId, provider: resolvedProvider) {
                return
            }
        }

        do {
            _ = try await apiClient.loadModel(modelId: modelId, provider: resolvedProvider)
            let catalog = try await apiClient.listModels(
                includeSuggested: false,
                includeRemoteProviders: false,
                itemLimit: 80
            )
            modelCatalog = catalog
            selectedModel = catalog.active.model
            selectedProvider = catalog.active.provider
            lastError = nil
        } catch {
            lastError = error.localizedDescription
        }
    }

    func ensureChatReady() async -> Bool {
        guard await ensureRuntimeOnline() else {
            return false
        }

        if modelCatalog == nil {
            await refreshModels(includeSuggested: false)
        }

        if !hasActiveModelConfigured {
            await refreshModels(includeSuggested: false)
            if !hasActiveModelConfigured {
                lastError = "Runtime is online, but no model is active. Open Models and install a model first."
                return false
            }
        }
        return true
    }

    func refreshToolingState() async {
        guard await ensureRuntimeOnline() else {
            return
        }

        do {
            async let toolsResponse = apiClient.listTools()
            async let promptsResponse = apiClient.listPermissionPrompts(status: "pending", limit: 200)
            let (toolsPayload, promptsPayload) = try await (toolsResponse, promptsResponse)

            availableTools = toolsPayload.items.sorted(by: { $0.name < $1.name })
            permissionPrompts = promptsPayload.items
            lastError = nil
        } catch {
            lastError = error.localizedDescription
        }
    }

    func approvePermissionPrompt(promptID: String) async {
        guard await ensureRuntimeOnline() else {
            return
        }

        do {
            _ = try await apiClient.approvePermissionPrompt(promptID: promptID)
            permissionPrompts.removeAll(where: { $0.id == promptID })
            lastError = nil
        } catch {
            lastError = error.localizedDescription
        }
    }

    func denyPermissionPrompt(promptID: String) async {
        guard await ensureRuntimeOnline() else {
            return
        }

        do {
            _ = try await apiClient.denyPermissionPrompt(promptID: promptID)
            permissionPrompts.removeAll(where: { $0.id == promptID })
            lastError = nil
        } catch {
            lastError = error.localizedDescription
        }
    }

    func startRuntime() {
        let host = URL(string: endpoint)?.host ?? "localhost"
        let port = URL(string: endpoint)?.port ?? 8000
        runtimeManager.start(
            runtimeDirectory: runtimeDirectory,
            host: host,
            port: port,
            additionalEnvironment: runtimeEnvironment()
        )
    }

    func startRuntimeFromSettings() async {
        if runtimeManager.isRunning {
            lastError = "Runtime is already running from the app."
            return
        }

        if await checkHealthOnce() {
            lastError = "API is already running from another process on this endpoint. Stop external runtime in terminal, then start from app."
            return
        }

        startRuntime()
        try? await Task.sleep(nanoseconds: 800_000_000)
        await refreshHealth()
    }

    func stopRuntime() {
        runtimeManager.stop()
    }

    func stopRuntimeFromSettings() async {
        if runtimeManager.isRunning {
            stopRuntime()
            try? await Task.sleep(nanoseconds: 500_000_000)
            await refreshHealth()
            return
        }

        if await checkHealthOnce() {
            lastError = "Cannot stop external runtime from app. Stop it in terminal or change endpoint."
        } else {
            lastError = "Runtime is not running."
        }
    }

    var currentChatMessages: [LocalChatMessage] {
        guard let index = resolvedSelectedChatIndex() else { return [] }
        return chatSessions[index].messages
    }

    var currentChatTitle: String {
        guard let index = resolvedSelectedChatIndex() else { return "New chat" }
        return chatSessions[index].title
    }

    func ensureChatExists() {
        if chatSessions.isEmpty {
            _ = createChat()
            return
        }

        if let selectedChatID, chatSessions.contains(where: { $0.id == selectedChatID }) {
            selectedChatIndex = chatSessions.firstIndex(where: { $0.id == selectedChatID })
            return
        }

        selectedChatID = chatSessions[0].id
        selectedChatIndex = 0
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
        selectedChatIndex = 0
        cancelPendingChatPersistence()
        persistChats()
        persistSelectedChatID()
        return session.id
    }

    func selectChat(id: UUID) {
        guard let index = chatSessions.firstIndex(where: { $0.id == id }) else { return }
        selectedChatID = id
        selectedChatIndex = index
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
            selectedChatIndex = 0
        } else {
            chatSessions.remove(at: index)
            self.selectedChatID = chatSessions.first?.id
            selectedChatIndex = chatSessions.isEmpty ? nil : 0
        }

        cancelPendingChatPersistence()
        persistChats()
        persistSelectedChatID()
    }

    @discardableResult
    func appendUserMessageToCurrentChat(_ text: String) -> UUID {
        ensureChatExists()
        let message = LocalChatMessage(id: UUID(), role: "user", content: text, createdAt: Date())

        mutateSelectedChat(
            reorderToTop: true,
            touchUpdatedAt: true,
            persistMode: .debounced
        ) { session in
            session.messages.append(message)
            if session.title == "New chat" || session.title.isEmpty {
                session.title = Self.chatTitle(from: text)
            }
            return true
        }
        return message.id
    }

    @discardableResult
    func appendAssistantPlaceholderToCurrentChat() -> UUID {
        ensureChatExists()
        let message = LocalChatMessage(id: UUID(), role: "assistant", content: "", createdAt: Date())
        mutateSelectedChat(
            reorderToTop: false,
            touchUpdatedAt: true,
            persistMode: .debounced
        ) { session in
            session.messages.append(message)
            return true
        }
        return message.id
    }

    func updateCurrentChatMessage(id: UUID, content: String) {
        mutateSelectedChat(
            reorderToTop: false,
            touchUpdatedAt: true,
            persistMode: .debounced
        ) { session in
            guard let index = messageIndex(in: session, messageID: id) else { return false }
            if session.messages[index].content == content {
                return false
            }
            session.messages[index].content = content
            return true
        }
    }

    func finalizeCurrentChatMessage(id: UUID, content: String) {
        mutateSelectedChat(
            reorderToTop: false,
            touchUpdatedAt: true,
            persistMode: .debounced
        ) { session in
            guard let index = messageIndex(in: session, messageID: id) else { return false }
            if session.messages[index].content == content {
                return false
            }
            session.messages[index].content = content
            return true
        }
    }

    private enum ChatPersistMode {
        case immediate
        case debounced
    }

    private func resolvedSelectedChatIndex() -> Int? {
        guard let selectedChatID else {
            selectedChatIndex = nil
            return nil
        }
        if let index = selectedChatIndex,
           chatSessions.indices.contains(index),
           chatSessions[index].id == selectedChatID {
            return index
        }
        guard let freshIndex = chatSessions.firstIndex(where: { $0.id == selectedChatID }) else {
            selectedChatIndex = nil
            return nil
        }
        selectedChatIndex = freshIndex
        return freshIndex
    }

    private func messageIndex(in session: LocalChatSession, messageID: UUID) -> Int? {
        if let last = session.messages.last, last.id == messageID {
            return session.messages.count - 1
        }
        return session.messages.firstIndex(where: { $0.id == messageID })
    }

    @discardableResult
    private func mutateSelectedChat(
        reorderToTop: Bool,
        touchUpdatedAt: Bool,
        persistMode: ChatPersistMode,
        _ update: (inout LocalChatSession) -> Bool
    ) -> Bool {
        ensureChatExists()
        guard selectedChatID != nil else { return false }
        guard let index = resolvedSelectedChatIndex() else { return false }

        if reorderToTop, index != 0 {
            var session = chatSessions.remove(at: index)
            guard update(&session) else {
                chatSessions.insert(session, at: index)
                return false
            }
            if touchUpdatedAt {
                session.updatedAt = Date()
            }
            chatSessions.insert(session, at: 0)
            selectedChatIndex = 0
            self.selectedChatID = session.id
        } else {
            guard update(&chatSessions[index]) else { return false }
            if touchUpdatedAt {
                chatSessions[index].updatedAt = Date()
            }
            selectedChatIndex = index
            self.selectedChatID = chatSessions[index].id
        }

        switch persistMode {
        case .immediate:
            cancelPendingChatPersistence()
            persistChats()
        case .debounced:
            schedulePersistChatsDebounced()
        }
        return true
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
            selectedChatIndex = selectedChatID.flatMap { id in
                chatSessions.firstIndex(where: { $0.id == id })
            }
        } catch {
            chatSessions = []
            selectedChatID = nil
            selectedChatIndex = nil
            lastError = "Failed to load saved chats: \(error.localizedDescription)"
        }
    }

    private func persistChats() {
        chatPersistRevision &+= 1
        let revision = chatPersistRevision
        let snapshot = chatSessions
        let destination = chatStoreURL()
        let writer = chatStoreWriter

        Task(priority: .utility) {
            do {
                try await writer.persist(snapshot: snapshot, revision: revision, to: destination)
            } catch {
                await MainActor.run {
                    self.lastError = "Failed to save chats: \(error.localizedDescription)"
                }
            }
        }
    }

    private func schedulePersistChatsDebounced() {
        pendingChatPersistTask?.cancel()
        let delay = chatPersistDebounceNanos
        pendingChatPersistTask = Task { [weak self] in
            do {
                try await Task.sleep(nanoseconds: delay)
            } catch {
                return
            }
            guard let self else { return }
            if Task.isCancelled {
                return
            }
            self.persistChats()
            self.pendingChatPersistTask = nil
        }
    }

    private func cancelPendingChatPersistence() {
        pendingChatPersistTask?.cancel()
        pendingChatPersistTask = nil
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

    private func runtimeEnvironment() -> [String: String] {
        var env: [String: String] = [:]
        let trimmedOpenAIBase = openAIBaseURL.trimmingCharacters(in: .whitespacesAndNewlines)
        let trimmedOpenAIKey = openAIAPIKey.trimmingCharacters(in: .whitespacesAndNewlines)
        let trimmedOpenRouterBase = openRouterBaseURL.trimmingCharacters(in: .whitespacesAndNewlines)
        let trimmedOpenRouterKey = openRouterAPIKey.trimmingCharacters(in: .whitespacesAndNewlines)
        let trimmedBlockedTools = blockedTools.trimmingCharacters(in: .whitespacesAndNewlines)
        let trimmedAllowedHighRiskTools = allowedHighRiskTools.trimmingCharacters(in: .whitespacesAndNewlines)
        let trimmedPluginSigningKey = pluginSigningKey.trimmingCharacters(in: .whitespacesAndNewlines)
        let trimmedMCPEndpoints = mcpEndpoints.trimmingCharacters(in: .whitespacesAndNewlines)
        let normalizedAuthToken = normalizedRuntimeAuthToken()

        if !trimmedOpenAIBase.isEmpty {
            env["AMARYLLIS_OPENAI_BASE_URL"] = trimmedOpenAIBase
        }
        if !trimmedOpenAIKey.isEmpty {
            env["AMARYLLIS_OPENAI_API_KEY"] = trimmedOpenAIKey
        }
        if !trimmedOpenRouterBase.isEmpty {
            env["AMARYLLIS_OPENROUTER_BASE_URL"] = trimmedOpenRouterBase
        }
        if !trimmedOpenRouterKey.isEmpty {
            env["AMARYLLIS_OPENROUTER_API_KEY"] = trimmedOpenRouterKey
        }
        env["AMARYLLIS_TOOL_APPROVAL_ENFORCEMENT"] = normalizedApprovalMode()
        env["AMARYLLIS_TOOL_ISOLATION_PROFILE"] = normalizedIsolationProfile()
        env["AMARYLLIS_TOOL_PYTHON_EXEC_MAX_TIMEOUT_SEC"] = normalizedPythonExecMaxTimeout()
        env["AMARYLLIS_TOOL_PYTHON_EXEC_MAX_CODE_CHARS"] = normalizedPythonExecMaxCodeChars()
        env["AMARYLLIS_TOOL_FILESYSTEM_ALLOW_WRITE"] = toolFilesystemAllowWrite ? "true" : "false"
        env["AMARYLLIS_TOOL_BUDGET_WINDOW_SEC"] = normalizedToolBudgetWindow()
        env["AMARYLLIS_TOOL_BUDGET_MAX_CALLS_PER_TOOL"] = normalizedToolBudgetMaxCallsPerTool()
        env["AMARYLLIS_TOOL_BUDGET_MAX_TOTAL_CALLS"] = normalizedToolBudgetMaxTotalCalls()
        env["AMARYLLIS_TOOL_BUDGET_MAX_HIGH_RISK_CALLS"] = normalizedToolBudgetMaxHighRiskCalls()
        env["AMARYLLIS_MCP_TIMEOUT_SEC"] = normalizedMCPTimeout()
        env["AMARYLLIS_BLOCKED_TOOLS"] = trimmedBlockedTools
        env["AMARYLLIS_ALLOWED_HIGH_RISK_TOOLS"] = trimmedAllowedHighRiskTools
        env["AMARYLLIS_PLUGIN_SIGNING_KEY"] = trimmedPluginSigningKey
        env["AMARYLLIS_MCP_ENDPOINTS"] = trimmedMCPEndpoints
        env["AMARYLLIS_REQUEST_TRACE_LOGS_ENABLED"] = "false"
        env["AMARYLLIS_OTEL_ENABLED"] = "false"
        env["AMARYLLIS_RUN_WORKERS"] = "1"
        env["AMARYLLIS_RUN_RECOVER_PENDING_ON_START"] = "false"
        env["AMARYLLIS_AUTOMATION_ENABLED"] = "false"
        env["AMARYLLIS_MEMORY_CONSOLIDATION_ENABLED"] = "false"
        env["AMARYLLIS_BACKUP_ENABLED"] = "false"
        env["AMARYLLIS_BACKUP_RESTORE_DRILL_ENABLED"] = "false"
        env["AMARYLLIS_AUTH_ENABLED"] = "true"
        env["AMARYLLIS_AUTH_TOKENS"] = "\(normalizedAuthToken):user-001:admin|user"
        return env
    }

    private func resolveProviderForModelAction(_ provider: String?) -> String {
        let normalized = provider?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        if !normalized.isEmpty {
            return normalized
        }
        if let selectedProvider, !selectedProvider.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return selectedProvider
        }
        if let activeProvider = modelCatalog?.active.provider,
           !activeProvider.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return activeProvider
        }
        return "mlx"
    }

    private func modelDownloadKey(modelId: String, provider: String?) -> String {
        let providerName = resolveProviderForModelAction(provider)
        return "\(providerName)::\(modelId)"
    }

    private func normalizedApprovalMode() -> String {
        let raw = toolApprovalEnforcement
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .lowercased()
        if raw == "prompt_and_allow" {
            return "prompt_and_allow"
        }
        return "strict"
    }

    private func normalizedRuntimeAuthToken() -> String {
        let trimmed = runtimeAuthToken.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.isEmpty {
            let generated = Self.generateDefaultRuntimeAuthToken()
            runtimeAuthToken = generated
            return generated
        }
        return trimmed
    }

    private func normalizedIsolationProfile() -> String {
        let raw = toolIsolationProfile
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .lowercased()
        if raw == "strict" {
            return "strict"
        }
        return "balanced"
    }

    private func normalizedPythonExecMaxTimeout() -> String {
        let raw = toolPythonExecMaxTimeoutSec.trimmingCharacters(in: .whitespacesAndNewlines)
        let parsed = Int(raw) ?? 10
        return String(max(1, parsed))
    }

    private func normalizedPythonExecMaxCodeChars() -> String {
        let raw = toolPythonExecMaxCodeChars.trimmingCharacters(in: .whitespacesAndNewlines)
        let parsed = Int(raw) ?? 4000
        return String(max(100, parsed))
    }

    private func normalizedToolBudgetWindow() -> String {
        let raw = toolBudgetWindowSec.trimmingCharacters(in: .whitespacesAndNewlines)
        let parsed = Double(raw) ?? 60.0
        return String(max(1.0, parsed))
    }

    private func normalizedToolBudgetMaxCallsPerTool() -> String {
        let raw = toolBudgetMaxCallsPerTool.trimmingCharacters(in: .whitespacesAndNewlines)
        let parsed = Int(raw) ?? 12
        return String(max(1, parsed))
    }

    private func normalizedToolBudgetMaxTotalCalls() -> String {
        let raw = toolBudgetMaxTotalCalls.trimmingCharacters(in: .whitespacesAndNewlines)
        let parsed = Int(raw) ?? 40
        return String(max(1, parsed))
    }

    private func normalizedToolBudgetMaxHighRiskCalls() -> String {
        let raw = toolBudgetMaxHighRiskCalls.trimmingCharacters(in: .whitespacesAndNewlines)
        let parsed = Int(raw) ?? 4
        return String(max(1, parsed))
    }

    private func normalizedMCPTimeout() -> String {
        let raw = mcpTimeoutSec.trimmingCharacters(in: .whitespacesAndNewlines)
        let parsed = Double(raw) ?? 10.0
        return String(max(1.0, parsed))
    }

    private func saveSecret(_ rawValue: String, account: String) {
        let trimmed = rawValue.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.isEmpty {
            _ = KeychainStore.delete(service: keychainService, account: account)
        } else {
            _ = KeychainStore.set(service: keychainService, account: account, value: trimmed)
        }
    }

    private static func generateDefaultRuntimeAuthToken() -> String {
        let suffix = UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()
        return "amaryllis-\(suffix)"
    }

    private static func isoNow() -> String {
        ISO8601DateFormatter().string(from: Date())
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

private actor ChatStoreWriter {
    private var latestRevision: UInt64 = 0

    func persist(snapshot: [LocalChatSession], revision: UInt64, to destination: URL) throws {
        guard revision >= latestRevision else { return }
        latestRevision = revision

        let directory = destination.deletingLastPathComponent()
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)

        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        let data = try encoder.encode(snapshot)
        try data.write(to: destination, options: .atomic)
    }
}

private enum PathInfo {
    static var homeDirectory: String {
        NSHomeDirectory()
    }
}
