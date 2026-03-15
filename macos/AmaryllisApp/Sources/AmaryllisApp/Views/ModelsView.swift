import Foundation
import SwiftUI

struct ModelsView: View {
    @EnvironmentObject private var appState: AppState

    @State private var modelToDownload: String = ""
    @State private var providerForDownload: String = "mlx"
    @State private var quickSearch: String = ""
    @State private var showAdvancedModelManagement: Bool = false
    @State private var loadingModelID: String?

    private let fallbackSuggested: [String: [APIModelCatalog.SuggestedModel]] = [
        "mlx": [
            APIModelCatalog.SuggestedModel(id: "mlx-community/Qwen2.5-1.5B-Instruct-4bit", label: "Qwen 2.5 1.5B Instruct 4bit"),
            APIModelCatalog.SuggestedModel(id: "mlx-community/Qwen2.5-7B-Instruct-4bit", label: "Qwen 2.5 7B Instruct 4bit"),
            APIModelCatalog.SuggestedModel(id: "mlx-community/Qwen2.5-Coder-7B-Instruct-4bit", label: "Qwen 2.5 Coder 7B Instruct 4bit"),
            APIModelCatalog.SuggestedModel(id: "mlx-community/Llama-3.2-3B-Instruct-4bit", label: "Llama 3.2 3B Instruct 4bit"),
            APIModelCatalog.SuggestedModel(id: "mlx-community/Llama-3.1-8B-Instruct-4bit", label: "Llama 3.1 8B Instruct 4bit"),
            APIModelCatalog.SuggestedModel(id: "mlx-community/Mistral-7B-Instruct-v0.3-4bit", label: "Mistral 7B Instruct v0.3 4bit"),
            APIModelCatalog.SuggestedModel(id: "mlx-community/Mixtral-8x7B-Instruct-v0.1-4bit", label: "Mixtral 8x7B Instruct v0.1 4bit"),
            APIModelCatalog.SuggestedModel(id: "mlx-community/Phi-3.5-mini-instruct-4bit", label: "Phi 3.5 Mini Instruct 4bit"),
            APIModelCatalog.SuggestedModel(id: "mlx-community/phi-4-4bit", label: "Phi 4 4bit"),
            APIModelCatalog.SuggestedModel(id: "mlx-community/gemma-2-9b-it-4bit", label: "Gemma 2 9B IT 4bit"),
            APIModelCatalog.SuggestedModel(id: "mlx-community/DeepSeek-R1-Distill-Qwen-7B-4bit", label: "DeepSeek R1 Distill Qwen 7B 4bit"),
            APIModelCatalog.SuggestedModel(id: "mlx-community/DeepSeek-R1-Distill-Llama-8B-4bit", label: "DeepSeek R1 Distill Llama 8B 4bit")
        ],
        "ollama": [
            APIModelCatalog.SuggestedModel(id: "llama3.3", label: "Llama 3.3"),
            APIModelCatalog.SuggestedModel(id: "llama3.2", label: "Llama 3.2"),
            APIModelCatalog.SuggestedModel(id: "qwen2.5", label: "Qwen 2.5"),
            APIModelCatalog.SuggestedModel(id: "qwen2.5-coder", label: "Qwen 2.5 Coder"),
            APIModelCatalog.SuggestedModel(id: "mistral", label: "Mistral"),
            APIModelCatalog.SuggestedModel(id: "mixtral", label: "Mixtral"),
            APIModelCatalog.SuggestedModel(id: "phi4", label: "Phi 4"),
            APIModelCatalog.SuggestedModel(id: "deepseek-r1", label: "DeepSeek R1"),
            APIModelCatalog.SuggestedModel(id: "gemma2", label: "Gemma 2"),
            APIModelCatalog.SuggestedModel(id: "command-r", label: "Command R"),
            APIModelCatalog.SuggestedModel(id: "codellama", label: "CodeLlama"),
            APIModelCatalog.SuggestedModel(id: "starcoder2", label: "StarCoder2")
        ]
    ]

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            header
            activeCard
            installedCard
            simpleLibraryCard

            DisclosureGroup(isExpanded: $showAdvancedModelManagement) {
                VStack(alignment: .leading, spacing: 10) {
                    downloadCard
                    suggestedCard(suggestedForDisplay)
                    advancedProviderCatalog
                }
                .padding(.top, 8)
            } label: {
                Text("Advanced model management")
                    .font(AmaryllisTheme.bodyFont(size: 13, weight: .semibold))
                    .foregroundStyle(AmaryllisTheme.textSecondary)
            }
            .amaryllisCard()

            if let error = appState.lastError {
                Text(error)
                    .font(AmaryllisTheme.bodyFont(size: 12, weight: .medium))
                    .foregroundStyle(AmaryllisTheme.accent)
            }
        }
        .onAppear {
            if modelToDownload.isEmpty {
                modelToDownload = appState.modelCatalog?.active.model ?? "mlx-community/Qwen2.5-1.5B-Instruct-4bit"
            }
            if !providerOptions.contains(providerForDownload) {
                providerForDownload = providerOptions.first ?? "mlx"
            }
            Task { await appState.refreshModels() }
        }
        .onChange(of: providerOptions) { options in
            if !options.contains(providerForDownload) {
                providerForDownload = options.first ?? "mlx"
            }
        }
    }

    private var header: some View {
        HStack {
            Text("Models")
                .font(AmaryllisTheme.titleFont(size: 30))
                .foregroundStyle(AmaryllisTheme.textPrimary)
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
                    Text("Quick Setup")
                        .frame(width: 106)
                }
            }
            .buttonStyle(AmaryllisPrimaryButtonStyle())
            .disabled(appState.isQuickSetupRunning)
            Button("Refresh") {
                Task { await appState.refreshModels() }
            }
            .buttonStyle(AmaryllisSecondaryButtonStyle())
        }
    }

    private var activeCard: some View {
        HStack(spacing: 8) {
            Text("Active")
                .font(AmaryllisTheme.bodyFont(size: 12, weight: .semibold))
                .foregroundStyle(AmaryllisTheme.textSecondary)
            Text("\(appState.modelCatalog?.active.provider ?? "-") / \(appState.modelCatalog?.active.model ?? "-")")
                .font(AmaryllisTheme.monoFont(size: 12, weight: .regular))
                .foregroundStyle(AmaryllisTheme.textPrimary)
            Spacer()
            Text(appState.hasActiveModelConfigured ? "ready" : "install model")
                .font(AmaryllisTheme.bodyFont(size: 11, weight: .semibold))
                .foregroundStyle(appState.hasActiveModelConfigured ? AmaryllisTheme.okGreen : AmaryllisTheme.accent)
        }
        .amaryllisCard()
    }

    private var installedCard: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text("Installed")
                    .font(AmaryllisTheme.sectionFont(size: 17))
                    .foregroundStyle(AmaryllisTheme.textPrimary)
                Spacer()
                Text("\(installedModels.count)")
                    .font(AmaryllisTheme.monoFont(size: 11, weight: .semibold))
                    .foregroundStyle(AmaryllisTheme.textSecondary)
            }

            if installedModels.isEmpty {
                Text("No installed local models yet.")
                    .font(AmaryllisTheme.bodyFont(size: 12, weight: .medium))
                    .foregroundStyle(AmaryllisTheme.textSecondary)
            } else {
                ForEach(installedModels.prefix(8)) { item in
                    HStack(spacing: 8) {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(item.itemID)
                                .font(AmaryllisTheme.bodyFont(size: 12, weight: .semibold))
                                .foregroundStyle(AmaryllisTheme.textPrimary)
                            HStack(spacing: 6) {
                                Text(item.provider.uppercased())
                                    .font(AmaryllisTheme.monoFont(size: 10, weight: .regular))
                                    .foregroundStyle(AmaryllisTheme.textSecondary)
                                if let sizeText = item.sizeText {
                                    Text("•")
                                        .font(AmaryllisTheme.monoFont(size: 10, weight: .regular))
                                        .foregroundStyle(AmaryllisTheme.textSecondary)
                                    Text(sizeText)
                                        .font(AmaryllisTheme.monoFont(size: 10, weight: .regular))
                                        .foregroundStyle(AmaryllisTheme.textSecondary)
                                }
                            }
                        }
                        Spacer()

                        if item.active {
                            Text("active")
                                .font(AmaryllisTheme.bodyFont(size: 10, weight: .bold))
                                .foregroundStyle(AmaryllisTheme.accent)
                        } else {
                            Button {
                                loadingModelID = item.itemID
                                Task {
                                    await appState.loadModel(modelId: item.itemID, provider: item.provider)
                                    loadingModelID = nil
                                }
                            } label: {
                                if loadingModelID == item.itemID, appState.isBusy {
                                    ProgressView().controlSize(.small).frame(width: 56)
                                } else {
                                    Text("Load").frame(width: 56)
                                }
                            }
                            .buttonStyle(AmaryllisSecondaryButtonStyle())
                            .disabled(appState.isBusy)
                        }
                    }
                    .padding(.vertical, 2)
                }
            }
        }
        .amaryllisCard()
    }

    private var simpleLibraryCard: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Simple Library")
                .font(AmaryllisTheme.sectionFont(size: 17))
                .foregroundStyle(AmaryllisTheme.textPrimary)

            Text("Choose a model. If it's already installed, Amaryllis will activate it instantly; otherwise it will download with progress and then activate.")
                .font(AmaryllisTheme.bodyFont(size: 12, weight: .medium))
                .foregroundStyle(AmaryllisTheme.textSecondary)

            TextField("Search model", text: $quickSearch)
                .textFieldStyle(AmaryllisTerminalTextFieldStyle())

            if filteredQuickSuggestions.isEmpty {
                Text("No models found for current filter.")
                    .font(AmaryllisTheme.bodyFont(size: 12, weight: .medium))
                    .foregroundStyle(AmaryllisTheme.textSecondary)
            } else {
                ForEach(filteredQuickSuggestions.prefix(12)) { suggestion in
                    quickLibraryRow(for: suggestion)
                }
            }
        }
        .amaryllisCard()
    }

    private func quickLibraryRow(for suggestion: QuickSuggestion) -> some View {
        let installed = isInstalled(provider: suggestion.provider, modelID: suggestion.model.id)
        let job = appState.modelDownloadJob(modelId: suggestion.model.id, provider: suggestion.provider)
        let isDownloading = job != nil && !(job?.isTerminal ?? true)

        return VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(suggestion.model.label)
                        .font(AmaryllisTheme.bodyFont(size: 12, weight: .semibold))
                        .foregroundStyle(AmaryllisTheme.textPrimary)
                    HStack(spacing: 6) {
                        Text("\(suggestion.provider)/\(suggestion.model.id)")
                            .font(AmaryllisTheme.monoFont(size: 11, weight: .regular))
                            .foregroundStyle(AmaryllisTheme.textSecondary)
                            .lineLimit(1)
                            .truncationMode(.middle)
                        if let sizeText = modelSizeText(for: suggestion.model) {
                            Text("•")
                                .font(AmaryllisTheme.monoFont(size: 11, weight: .regular))
                                .foregroundStyle(AmaryllisTheme.textSecondary)
                            Text(sizeText)
                                .font(AmaryllisTheme.monoFont(size: 11, weight: .regular))
                                .foregroundStyle(AmaryllisTheme.textSecondary)
                        }
                    }
                }
                Spacer()

                if installed {
                    Text("installed")
                        .font(AmaryllisTheme.bodyFont(size: 10, weight: .bold))
                        .foregroundStyle(AmaryllisTheme.okGreen)
                }

                Button {
                    Task {
                        quickSearch = ""
                        appState.lastError = installed
                            ? "Activating \(suggestion.model.id)..."
                            : "Installing \(suggestion.model.id)..."
                        if installed {
                            await appState.loadModel(modelId: suggestion.model.id, provider: suggestion.provider)
                        } else {
                            await installAndActivate(modelID: suggestion.model.id, provider: suggestion.provider)
                        }
                    }
                } label: {
                    if isDownloading {
                        Text("Downloading").frame(width: 110)
                    } else if installed {
                        Text("Use").frame(width: 110)
                    } else {
                        Text("Install & Use").frame(width: 110)
                    }
                }
                .buttonStyle(AmaryllisPrimaryButtonStyle())
            }

            if let job, isDownloading {
                modelDownloadProgress(job: job)
            }
        }
        .padding(.vertical, 4)
        .padding(.horizontal, 6)
        .background(
            RoundedRectangle(cornerRadius: 8)
                .fill(installed ? AmaryllisTheme.border.opacity(0.14) : Color.clear)
        )
    }

    private var downloadCard: some View {
        let manualJob = appState.modelDownloadJob(modelId: modelToDownload, provider: providerForDownload)
        let manualIsDownloading = manualJob != nil && !(manualJob?.isTerminal ?? true)

        return VStack(alignment: .leading, spacing: 8) {
            Text("Install model")
                .font(AmaryllisTheme.sectionFont(size: 17))
                .foregroundStyle(AmaryllisTheme.textPrimary)

            HStack(spacing: 8) {
                TextField("Model id", text: $modelToDownload)
                    .textFieldStyle(AmaryllisTerminalTextFieldStyle())

                Picker("Provider", selection: $providerForDownload) {
                    ForEach(providerOptions, id: \.self) { provider in
                        Text(provider).tag(provider)
                    }
                }
                .pickerStyle(.menu)
                .frame(width: 120)

                Button {
                    let trimmed = modelToDownload.trimmingCharacters(in: .whitespacesAndNewlines)
                    guard !trimmed.isEmpty else { return }
                    Task {
                        appState.lastError = "Installing \(trimmed)..."
                        await startDownload(modelId: trimmed, provider: providerForDownload)
                    }
                } label: {
                    if manualIsDownloading {
                        Text("Downloading").frame(width: 92)
                    } else {
                        Text("Install").frame(width: 92)
                    }
                }
                .buttonStyle(AmaryllisPrimaryButtonStyle())
            }

            if let manualJob, manualIsDownloading {
                modelDownloadProgress(job: manualJob)
            }
        }
        .amaryllisCard()
    }

    private func suggestedCard(_ suggested: [String: [APIModelCatalog.SuggestedModel]]) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Suggested open models")
                .font(AmaryllisTheme.sectionFont(size: 17))
                .foregroundStyle(AmaryllisTheme.textPrimary)

            if !hasSuggestedModels {
                Text("No suggestions available yet.")
                    .font(AmaryllisTheme.bodyFont(size: 12, weight: .medium))
                    .foregroundStyle(AmaryllisTheme.textSecondary)
            } else {
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 8) {
                        ForEach(suggested.keys.sorted(), id: \.self) { provider in
                            if let items = suggested[provider], !items.isEmpty {
                                VStack(alignment: .leading, spacing: 6) {
                                    Text(provider.uppercased())
                                        .font(AmaryllisTheme.bodyFont(size: 11, weight: .bold))
                                        .foregroundStyle(AmaryllisTheme.textSecondary)

                                    ForEach(items) { item in
                                        let installed = isInstalled(provider: provider, modelID: item.id)
                                        let job = appState.modelDownloadJob(modelId: item.id, provider: provider)
                                        let downloading = job != nil && !(job?.isTerminal ?? true)
                                        HStack(spacing: 8) {
                                            VStack(alignment: .leading, spacing: 2) {
                                                Text(item.label)
                                                    .font(AmaryllisTheme.bodyFont(size: 12, weight: .semibold))
                                                    .foregroundStyle(AmaryllisTheme.textPrimary)
                                                HStack(spacing: 6) {
                                                    Text(item.id)
                                                        .font(AmaryllisTheme.monoFont(size: 11, weight: .regular))
                                                        .foregroundStyle(AmaryllisTheme.textSecondary)
                                                    if let sizeText = modelSizeText(for: item) {
                                                        Text("•")
                                                            .font(AmaryllisTheme.monoFont(size: 11, weight: .regular))
                                                            .foregroundStyle(AmaryllisTheme.textSecondary)
                                                        Text(sizeText)
                                                            .font(AmaryllisTheme.monoFont(size: 11, weight: .regular))
                                                            .foregroundStyle(AmaryllisTheme.textSecondary)
                                                    }
                                                }
                                            }
                                            Spacer()

                                            if installed {
                                                Text("installed")
                                                    .font(AmaryllisTheme.bodyFont(size: 10, weight: .bold))
                                                    .foregroundStyle(AmaryllisTheme.okGreen)
                                            }

                                            Button {
                                                Task {
                                                    appState.lastError = installed
                                                        ? "Activating \(item.id)..."
                                                        : "Installing \(item.id)..."
                                                    if installed {
                                                        await appState.loadModel(modelId: item.id, provider: provider)
                                                    } else {
                                                        await startDownload(modelId: item.id, provider: provider)
                                                    }
                                                }
                                            } label: {
                                                if downloading {
                                                    Text("Downloading").frame(width: 92)
                                                } else if installed {
                                                    Text("Use").frame(width: 92)
                                                } else {
                                                    Text("Install").frame(width: 92)
                                                }
                                            }
                                            .buttonStyle(AmaryllisPrimaryButtonStyle())
                                        }
                                        .padding(.vertical, 2)
                                        .contentShape(Rectangle())
                                        .onTapGesture {
                                            providerForDownload = provider
                                            modelToDownload = item.id
                                        }

                                        if let job, downloading {
                                            modelDownloadProgress(job: job)
                                        }
                                    }
                                }
                                .padding(.bottom, 4)
                            }
                        }
                    }
                }
                .frame(maxHeight: 340)
            }
        }
        .amaryllisCard()
    }

    private func providerSection(name: String, payload: APIModelCatalog.ProviderPayload) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text(name.uppercased())
                    .font(AmaryllisTheme.bodyFont(size: 11, weight: .bold))
                    .foregroundStyle(AmaryllisTheme.textSecondary)
                Spacer()
                Text(payload.available ? "ready" : "unavailable")
                    .font(AmaryllisTheme.bodyFont(size: 11, weight: .semibold))
                    .foregroundStyle(payload.available ? .green : AmaryllisTheme.accent)
            }

            if let error = payload.error {
                Text(error)
                    .font(AmaryllisTheme.bodyFont(size: 11, weight: .medium))
                    .foregroundStyle(AmaryllisTheme.accent)
            }

            if payload.items.isEmpty {
                Text("No local models")
                    .font(AmaryllisTheme.bodyFont(size: 12, weight: .regular))
                    .foregroundStyle(AmaryllisTheme.textSecondary)
            } else {
                ForEach(payload.items) { item in
                    HStack(spacing: 8) {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(item.id)
                                .font(AmaryllisTheme.bodyFont(size: 12, weight: .semibold))
                                .foregroundStyle(AmaryllisTheme.textPrimary)
                            HStack(spacing: 6) {
                                if let sizeText = modelSizeText(fromMetadata: item.metadata) {
                                    Text(sizeText)
                                        .font(AmaryllisTheme.monoFont(size: 10, weight: .regular))
                                        .foregroundStyle(AmaryllisTheme.textSecondary)
                                }
                                if let path = item.path {
                                    Text(path)
                                        .font(AmaryllisTheme.monoFont(size: 10, weight: .regular))
                                        .foregroundStyle(AmaryllisTheme.textSecondary)
                                        .lineLimit(1)
                                        .truncationMode(.middle)
                                }
                            }
                        }
                        Spacer()

                        if item.active || appState.modelCatalog?.active.model == item.id {
                            Text("active")
                                .font(AmaryllisTheme.bodyFont(size: 10, weight: .bold))
                                .foregroundStyle(AmaryllisTheme.accent)
                        }

                        Button {
                            loadingModelID = item.id
                            Task {
                                await appState.loadModel(modelId: item.id, provider: name)
                                loadingModelID = nil
                            }
                        } label: {
                            if loadingModelID == item.id, appState.isBusy {
                                ProgressView().controlSize(.small).frame(width: 56)
                            } else {
                                Text("Load").frame(width: 56)
                            }
                        }
                        .buttonStyle(AmaryllisSecondaryButtonStyle())
                        .disabled(appState.isBusy)
                    }
                    .padding(.vertical, 2)
                }
            }
        }
        .amaryllisCard()
    }

    private var advancedProviderCatalog: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 8) {
                if let catalog = appState.modelCatalog {
                    ForEach(catalog.providers.keys.sorted(), id: \.self) { providerName in
                        if let payload = catalog.providers[providerName] {
                            providerSection(name: providerName, payload: payload)
                        }
                    }
                } else {
                    Text("No model data yet")
                        .font(AmaryllisTheme.bodyFont(size: 12, weight: .medium))
                        .foregroundStyle(AmaryllisTheme.textSecondary)
                }
            }
        }
        .frame(maxHeight: 280)
    }

    private func modelDownloadProgress(job: APIModelDownloadJob) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            if job.progress > 0 {
                ProgressView(value: max(0.0, min(1.0, job.progress)))
                    .progressViewStyle(.linear)
                    .tint(AmaryllisTheme.accent)
            } else {
                ProgressView()
                    .controlSize(.small)
            }
            HStack(spacing: 6) {
                if let message = job.message, !message.isEmpty {
                    Text(message)
                        .font(AmaryllisTheme.monoFont(size: 10, weight: .regular))
                        .foregroundStyle(AmaryllisTheme.textSecondary)
                        .lineLimit(1)
                }
                Spacer()
                if let completed = job.completedBytes, let total = job.totalBytes, total > 0 {
                    Text("\(byteCountFormatter.string(fromByteCount: Int64(completed))) / \(byteCountFormatter.string(fromByteCount: Int64(total)))")
                        .font(AmaryllisTheme.monoFont(size: 10, weight: .regular))
                        .foregroundStyle(AmaryllisTheme.textSecondary)
                } else if job.progress > 0 {
                    Text("\(Int((job.progress * 100).rounded()))%")
                        .font(AmaryllisTheme.monoFont(size: 10, weight: .regular))
                        .foregroundStyle(AmaryllisTheme.textSecondary)
                }
            }
        }
        .padding(.leading, 2)
    }

    private var suggestedForDisplay: [String: [APIModelCatalog.SuggestedModel]] {
        let allowedProviders = downloadableProviders

        if let suggested = appState.modelCatalog?.suggested {
            let nonEmpty = suggested.values.contains { !$0.isEmpty }
            if nonEmpty {
                var filtered: [String: [APIModelCatalog.SuggestedModel]] = [:]
                for provider in suggested.keys.sorted() where allowedProviders.contains(provider) {
                    filtered[provider] = suggested[provider] ?? []
                }
                if filtered.values.contains(where: { !$0.isEmpty }) {
                    return filtered
                }
            }
        }
        var filteredFallback: [String: [APIModelCatalog.SuggestedModel]] = [:]
        for provider in fallbackSuggested.keys.sorted() where allowedProviders.contains(provider) {
            filteredFallback[provider] = fallbackSuggested[provider] ?? []
        }
        return filteredFallback
    }

    private var hasSuggestedModels: Bool {
        suggestedForDisplay.values.contains { !$0.isEmpty }
    }

    private var providerOptions: [String] {
        if let capabilities = appState.modelCatalog?.capabilities {
            let downloadable = capabilities
                .filter { $0.value.supportsDownload }
                .map(\.key)
                .sorted()
            if !downloadable.isEmpty {
                return downloadable
            }
        }

        if let catalog = appState.modelCatalog {
            let keys = catalog.providers.keys.sorted().filter { provider in
                provider == "mlx" || provider == "ollama"
            }
            if !keys.isEmpty {
                return keys
            }
        }
        return ["mlx", "ollama"]
    }

    private var downloadableProviders: Set<String> {
        Set(providerOptions)
    }

    private func startDownload(modelId: String, provider: String) async {
        providerForDownload = provider
        modelToDownload = modelId
        await appState.downloadModel(modelId: modelId, provider: provider)
    }

    private func installAndActivate(modelID: String, provider: String) async {
        providerForDownload = provider
        modelToDownload = modelID
        await appState.installAndActivateModel(modelId: modelID, provider: provider)
    }

    private var filteredQuickSuggestions: [QuickSuggestion] {
        let term = quickSearch.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        var items: [QuickSuggestion] = []
        for provider in suggestedForDisplay.keys.sorted() {
            let suggested = suggestedForDisplay[provider] ?? []
            for item in suggested {
                let candidate = "\(provider) \(item.id) \(item.label)".lowercased()
                if term.isEmpty || candidate.contains(term) {
                    items.append(
                        QuickSuggestion(
                            id: "\(provider)::\(item.id)",
                            provider: provider,
                            model: item
                        )
                    )
                }
            }
        }

        return items.sorted { lhs, rhs in
            let lhsInstalled = isInstalled(provider: lhs.provider, modelID: lhs.model.id)
            let rhsInstalled = isInstalled(provider: rhs.provider, modelID: rhs.model.id)
            if lhsInstalled != rhsInstalled {
                return lhsInstalled && !rhsInstalled
            }
            return lhs.model.label.localizedCaseInsensitiveCompare(rhs.model.label) == .orderedAscending
        }
    }

    private var installedModels: [InstalledModel] {
        guard let catalog = appState.modelCatalog else { return [] }
        var rows: [InstalledModel] = []
        for provider in catalog.providers.keys.sorted() {
            guard let payload = catalog.providers[provider] else { continue }
            for item in payload.items {
                let size = modelSizeBytes(fromMetadata: item.metadata)
                rows.append(
                    InstalledModel(
                        id: "\(provider)::\(item.id)",
                        provider: provider,
                        itemID: item.id,
                        active: item.active || (catalog.active.provider == provider && catalog.active.model == item.id),
                        sizeText: size.map { byteCountFormatter.string(fromByteCount: Int64($0)) }
                    )
                )
            }
        }

        return rows.sorted { lhs, rhs in
            if lhs.active != rhs.active {
                return lhs.active && !rhs.active
            }
            if lhs.provider != rhs.provider {
                return lhs.provider < rhs.provider
            }
            return lhs.itemID < rhs.itemID
        }
    }

    private func isInstalled(provider: String, modelID: String) -> Bool {
        appState.isModelInstalled(modelId: modelID, provider: provider)
    }

    private func modelSizeText(for model: APIModelCatalog.SuggestedModel) -> String? {
        if let exact = model.sizeBytes, exact > 0 {
            return byteCountFormatter.string(fromByteCount: Int64(exact))
        }
        if let inferred = inferredSizeBytes(fromModelID: model.id), inferred > 0 {
            return "~" + byteCountFormatter.string(fromByteCount: Int64(inferred))
        }
        return nil
    }

    private func modelSizeText(fromMetadata metadata: [String: JSONValue]?) -> String? {
        guard let bytes = modelSizeBytes(fromMetadata: metadata), bytes > 0 else {
            return nil
        }
        return byteCountFormatter.string(fromByteCount: Int64(bytes))
    }

    private func modelSizeBytes(fromMetadata metadata: [String: JSONValue]?) -> Int? {
        guard let metadata else { return nil }
        if let bytes = metadata["size_bytes"]?.intValue {
            return bytes
        }
        if let bytes = metadata["size"]?.intValue {
            return bytes
        }
        return nil
    }

    private func inferredSizeBytes(fromModelID modelID: String) -> Int? {
        let text = modelID.lowercased()
        let pattern = #"(\d+(?:\.\d+)?)\s*b"#
        guard let regex = try? NSRegularExpression(pattern: pattern, options: []) else {
            return nil
        }
        let range = NSRange(text.startIndex..<text.endIndex, in: text)
        guard let match = regex.firstMatch(in: text, options: [], range: range) else {
            return nil
        }
        guard let valueRange = Range(match.range(at: 1), in: text) else {
            return nil
        }
        guard let paramsB = Double(text[valueRange]) else {
            return nil
        }

        var bytesPerParam = 0.58
        if text.contains("8bit") || text.contains("q8") {
            bytesPerParam = 1.05
        } else if text.contains("6bit") || text.contains("q6") {
            bytesPerParam = 0.78
        } else if text.contains("5bit") || text.contains("q5") {
            bytesPerParam = 0.64
        } else if text.contains("4bit") || text.contains("q4") {
            bytesPerParam = 0.56
        }

        let estimated = Int(paramsB * 1_000_000_000 * bytesPerParam)
        return estimated > 0 ? estimated : nil
    }

    private struct QuickSuggestion: Identifiable {
        let id: String
        let provider: String
        let model: APIModelCatalog.SuggestedModel
    }

    private struct InstalledModel: Identifiable {
        let id: String
        let provider: String
        let itemID: String
        let active: Bool
        let sizeText: String?
    }

    private static let _byteCountFormatter: ByteCountFormatter = {
        let formatter = ByteCountFormatter()
        formatter.countStyle = .file
        formatter.allowedUnits = [.useGB, .useMB, .useKB]
        formatter.includesUnit = true
        formatter.isAdaptive = true
        return formatter
    }()

    private var byteCountFormatter: ByteCountFormatter {
        Self._byteCountFormatter
    }
}
