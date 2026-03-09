import SwiftUI

struct ModelsView: View {
    @EnvironmentObject private var appState: AppState

    @State private var modelToDownload: String = "mlx-community/Qwen2.5-1.5B-Instruct-4bit"
    @State private var providerForDownload: String = "mlx"
    @State private var loadingModelID: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("Model Control")
                    .font(.system(size: 26, weight: .black, design: .rounded))
                    .foregroundStyle(AmaryllisTheme.textPrimary)
                Spacer()
                Button("Refresh") {
                    Task { await appState.refreshModels() }
                }
                .buttonStyle(.bordered)
            }

            statusCard

            downloadCard

            ScrollView {
                LazyVStack(alignment: .leading, spacing: 10) {
                    if let catalog = appState.modelCatalog {
                        ForEach(catalog.providers.keys.sorted(), id: \.self) { providerName in
                            if let payload = catalog.providers[providerName] {
                                providerSection(name: providerName, payload: payload)
                            }
                        }
                    } else {
                        Text("No model data yet.")
                            .foregroundStyle(AmaryllisTheme.textSecondary)
                    }
                }
            }
        }
        .onAppear {
            Task { await appState.refreshModels() }
        }
    }

    private var statusCard: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Active")
                .font(.system(size: 12, weight: .bold))
                .foregroundStyle(AmaryllisTheme.textSecondary)
            Text("\(appState.modelCatalog?.active.provider ?? "-") / \(appState.modelCatalog?.active.model ?? "-")")
                .font(.system(size: 14, weight: .semibold, design: .rounded))
                .foregroundStyle(AmaryllisTheme.textPrimary)

            if let error = appState.lastError {
                Text(error)
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(AmaryllisTheme.accent)
            }
        }
        .amaryllisCard()
    }

    private var downloadCard: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Download model")
                .font(.system(size: 14, weight: .bold))
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
                        await appState.downloadModel(
                            modelId: trimmed,
                            provider: providerForDownload
                        )
                    }
                } label: {
                    if appState.isBusy {
                        ProgressView()
                            .controlSize(.small)
                            .frame(width: 80)
                    } else {
                        Text("Download")
                            .frame(width: 80)
                    }
                }
                .buttonStyle(.borderedProminent)
                .tint(AmaryllisTheme.accent)
                .disabled(appState.isBusy)
            }
        }
        .amaryllisCard()
    }

    private func providerSection(name: String, payload: APIModelCatalog.ProviderPayload) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text(name.uppercased())
                    .font(.system(size: 12, weight: .black, design: .rounded))
                    .foregroundStyle(AmaryllisTheme.textSecondary)

                Spacer()

                Circle()
                    .fill(payload.available ? Color.green : AmaryllisTheme.accent)
                    .frame(width: 8, height: 8)
                Text(payload.available ? "AVAILABLE" : "UNAVAILABLE")
                    .font(.system(size: 11, weight: .bold))
                    .foregroundStyle(payload.available ? Color.green : AmaryllisTheme.accent)
            }

            if let error = payload.error {
                Text(error)
                    .font(.system(size: 12, weight: .medium))
                    .foregroundStyle(AmaryllisTheme.accent)
            }

            if payload.items.isEmpty {
                Text("No models registered for this provider.")
                    .font(.system(size: 12, weight: .regular))
                    .foregroundStyle(AmaryllisTheme.textSecondary)
            } else {
                ForEach(payload.items) { item in
                    HStack(spacing: 8) {
                        VStack(alignment: .leading, spacing: 3) {
                            Text(item.id)
                                .font(.system(size: 13, weight: .semibold, design: .rounded))
                                .foregroundStyle(AmaryllisTheme.textPrimary)
                            if let path = item.path {
                                Text(path)
                                    .font(.system(size: 11, weight: .regular, design: .monospaced))
                                    .foregroundStyle(AmaryllisTheme.textSecondary)
                                    .lineLimit(1)
                                    .truncationMode(.middle)
                            }
                        }

                        Spacer()

                        if item.active || appState.modelCatalog?.active.model == item.id {
                            Text("ACTIVE")
                                .font(.system(size: 10, weight: .black))
                                .padding(.horizontal, 8)
                                .padding(.vertical, 5)
                                .background(AmaryllisTheme.accentSoft)
                                .clipShape(Capsule())
                        }

                        Button {
                            loadingModelID = item.id
                            Task {
                                await appState.loadModel(modelId: item.id, provider: name)
                                loadingModelID = nil
                            }
                        } label: {
                            if loadingModelID == item.id && appState.isBusy {
                                ProgressView()
                                    .controlSize(.small)
                                    .frame(width: 70)
                            } else {
                                Text("Load")
                                    .frame(width: 70)
                            }
                        }
                        .buttonStyle(.bordered)
                        .disabled(appState.isBusy)
                    }
                    .padding(.vertical, 4)
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
