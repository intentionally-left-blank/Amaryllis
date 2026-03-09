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
    @Published var isBusy: Bool = false
    @Published var lastError: String?

    let runtimeManager = RuntimeProcessManager()

    private let endpointKey = "amaryllis.endpoint"
    private let runtimeDirKey = "amaryllis.runtimeDirectory"

    lazy var apiClient = AmaryllisAPIClient(baseURLProvider: { [unowned self] in
        self.endpoint
    })

    init() {
        let defaults = UserDefaults.standard
        self.endpoint = defaults.string(forKey: endpointKey) ?? "http://localhost:8000"

        let defaultRuntimeDir = FileManager.default.currentDirectoryPath
        self.runtimeDirectory = defaults.string(forKey: runtimeDirKey) ?? defaultRuntimeDir
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
}
