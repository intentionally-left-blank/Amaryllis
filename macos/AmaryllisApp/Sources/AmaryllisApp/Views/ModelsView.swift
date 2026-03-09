import SwiftUI

struct ModelsView: View {
    @EnvironmentObject private var appState: AppState

    @State private var modelToDownload: String = "mlx-community/Qwen2.5-1.5B-Instruct-4bit"
    @State private var providerForDownload: String = "mlx"
    @State private var loadingModelID: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("Models")
                    .font(.system(size: 22, weight: .bold, design: .rounded))
                    .foregroundStyle(AmaryllisTheme.textPrimary)
                Spacer()
                Button("Refresh") {
                    Task { await appState.refreshModels() }
                }
                .buttonStyle(.bordered)
            }

            HStack(spacing: 8) {
                Text("Active")
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(AmaryllisTheme.textSecondary)
                Text("\(appState.modelCatalog?.active.provider ?? "-") / \(appState.modelCatalog?.active.model ?? "-")")
                    .font(.system(size: 12, weight: .semibold, design: .monospaced))
                    .foregroundStyle(AmaryllisTheme.textPrimary)
            }
            .amaryllisCard()

            downloadCard

            if let suggested = appState.modelCatalog?.suggested {
                suggestedCard(suggested)
            }

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
                            .foregroundStyle(AmaryllisTheme.textSecondary)
                    }
                }
            }

            if let error = appState.lastError {
                Text(error)
                    .font(.system(size: 12, weight: .medium))
                    .foregroundStyle(AmaryllisTheme.accent)
            }
        }
        .onAppear {
            Task { await appState.refreshModels() }
        }
    }

    private var downloadCard: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Download")
                .font(.system(size: 13, weight: .bold))
                .foregroundStyle(AmaryllisTheme.textPrimary)

            HStack(spacing: 8) {
                TextField("Model id", text: $modelToDownload)
                    .textFieldStyle(.roundedBorder)

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
                        await appState.downloadModel(modelId: trimmed, provider: providerForDownload)
                    }
                } label: {
                    if appState.isBusy {
                        ProgressView().controlSize(.small).frame(width: 82)
                    } else {
                        Text("Download").frame(width: 82)
                    }
                }
                .buttonStyle(.borderedProminent)
                .tint(AmaryllisTheme.accent)
                .disabled(appState.isBusy)
            }
        }
        .amaryllisCard()
    }

    private func suggestedCard(_ suggested: [String: [APIModelCatalog.SuggestedModel]]) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Suggested models")
                .font(.system(size: 13, weight: .bold))
                .foregroundStyle(AmaryllisTheme.textPrimary)

            ForEach(suggested.keys.sorted(), id: \.self) { provider in
                if let items = suggested[provider], !items.isEmpty {
                    VStack(alignment: .leading, spacing: 6) {
                        Text(provider.uppercased())
                            .font(.system(size: 11, weight: .bold))
                            .foregroundStyle(AmaryllisTheme.textSecondary)

                        ForEach(items) { item in
                            HStack(spacing: 8) {
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(item.label)
                                        .font(.system(size: 12, weight: .semibold))
                                        .foregroundStyle(AmaryllisTheme.textPrimary)
                                    Text(item.id)
                                        .font(.system(size: 11, weight: .regular, design: .monospaced))
                                        .foregroundStyle(AmaryllisTheme.textSecondary)
                                }
                                Spacer()
                                Button("Use") {
                                    providerForDownload = provider
                                    modelToDownload = item.id
                                }
                                .buttonStyle(.bordered)
                                .disabled(appState.isBusy)

                                Button {
                                    Task {
                                        providerForDownload = provider
                                        modelToDownload = item.id
                                        await appState.downloadModel(modelId: item.id, provider: provider)
                                    }
                                } label: {
                                    if appState.isBusy && modelToDownload == item.id {
                                        ProgressView().controlSize(.small).frame(width: 68)
                                    } else {
                                        Text("Download").frame(width: 68)
                                    }
                                }
                                .buttonStyle(.borderedProminent)
                                .tint(AmaryllisTheme.accent)
                                .disabled(appState.isBusy)
                            }
                            .padding(.vertical, 2)
                        }
                    }
                }
            }
        }
        .amaryllisCard()
    }

    private func providerSection(name: String, payload: APIModelCatalog.ProviderPayload) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text(name.uppercased())
                    .font(.system(size: 11, weight: .bold))
                    .foregroundStyle(AmaryllisTheme.textSecondary)
                Spacer()
                Text(payload.available ? "ready" : "unavailable")
                    .font(.system(size: 11, weight: .medium))
                    .foregroundStyle(payload.available ? .green : AmaryllisTheme.accent)
            }

            if let error = payload.error {
                Text(error)
                    .font(.system(size: 11, weight: .medium))
                    .foregroundStyle(AmaryllisTheme.accent)
            }

            if payload.items.isEmpty {
                Text("No local models")
                    .font(.system(size: 12, weight: .regular))
                    .foregroundStyle(AmaryllisTheme.textSecondary)
            } else {
                ForEach(payload.items) { item in
                    HStack(spacing: 8) {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(item.id)
                                .font(.system(size: 12, weight: .semibold, design: .rounded))
                                .foregroundStyle(AmaryllisTheme.textPrimary)
                            if let path = item.path {
                                Text(path)
                                    .font(.system(size: 10, weight: .regular, design: .monospaced))
                                    .foregroundStyle(AmaryllisTheme.textSecondary)
                                    .lineLimit(1)
                                    .truncationMode(.middle)
                            }
                        }
                        Spacer()

                        if item.active || appState.modelCatalog?.active.model == item.id {
                            Text("active")
                                .font(.system(size: 10, weight: .bold))
                                .foregroundStyle(AmaryllisTheme.accent)
                        }

                        Button {
                            loadingModelID = item.id
                            Task {
                                await appState.loadModel(modelId: item.id, provider: name)
                                loadingModelID = nil
                            }
                        } label: {
                            if loadingModelID == item.id && appState.isBusy {
                                ProgressView().controlSize(.small).frame(width: 58)
                            } else {
                                Text("Load").frame(width: 58)
                            }
                        }
                        .buttonStyle(.bordered)
                        .disabled(appState.isBusy)
                    }
                    .padding(.vertical, 2)
                }
            }
        }
        .amaryllisCard()
    }

    private var providerOptions: [String] {
        if let catalog = appState.modelCatalog {
            let keys = catalog.providers.keys.sorted()
            if !keys.isEmpty {
                return keys
            }
        }
        return ["mlx", "ollama"]
    }
}
