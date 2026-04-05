import Foundation

final class AmaryllisAPIClient {
    enum APIClientError: LocalizedError {
        case invalidURL
        case invalidResponse
        case server(String)
        case decoding(String)

        var errorDescription: String? {
            switch self {
            case .invalidURL:
                return "Invalid endpoint URL"
            case .invalidResponse:
                return "Invalid server response"
            case .server(let message):
                return message
            case .decoding(let message):
                return "Failed to decode response: \(message)"
            }
        }
    }

    private let session: URLSession
    private let baseURLProvider: () -> String
    private let authTokenProvider: () -> String
    private let jsonEncoder: JSONEncoder
    private let jsonDecoder: JSONDecoder

    init(
        baseURLProvider: @escaping () -> String,
        authTokenProvider: @escaping () -> String,
        session: URLSession = .shared
    ) {
        self.baseURLProvider = baseURLProvider
        self.authTokenProvider = authTokenProvider
        self.session = session

        self.jsonEncoder = JSONEncoder()
        self.jsonDecoder = JSONDecoder()
    }

    func health() async throws -> APIHealthResponse {
        try await request(path: "/health", method: "GET", body: Optional<Data>.none)
    }

    func listModels(
        includeSuggested: Bool = true,
        includeRemoteProviders: Bool = true,
        itemLimit: Int = 80
    ) async throws -> APIModelCatalog {
        let includeSuggestedValue = includeSuggested ? "true" : "false"
        let includeRemoteValue = includeRemoteProviders ? "true" : "false"
        let normalizedLimit = max(1, min(itemLimit, 500))
        let path = "/models?include_suggested=\(includeSuggestedValue)&include_remote_providers=\(includeRemoteValue)&item_limit=\(normalizedLimit)"
        return try await request(path: path, method: "GET", body: Optional<Data>.none)
    }

    func privacyTransparency() async throws -> APIPrivacyTransparencyContract {
        try await request(path: "/privacy/transparency", method: "GET", body: Optional<Data>.none)
    }

    func listModelPackages(
        profile: String? = nil,
        includeRemoteProviders: Bool = true,
        limit: Int = 120
    ) async throws -> APIModelPackageCatalog {
        var queryItems: [URLQueryItem] = [
            URLQueryItem(name: "include_remote_providers", value: includeRemoteProviders ? "true" : "false"),
            URLQueryItem(name: "limit", value: String(max(1, min(limit, 500))))
        ]
        if let profile, !profile.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            queryItems.append(URLQueryItem(name: "profile", value: profile))
        }
        let path = buildPath(path: "/models/packages", queryItems: queryItems)
        return try await request(path: path, method: "GET", body: Optional<Data>.none)
    }

    func installModelPackage(packageId: String, activate: Bool = true) async throws -> APIModelPackageInstallResponse {
        let payload = APIInstallModelPackageRequest(packageID: packageId, activate: activate)
        let body = try jsonEncoder.encode(payload)
        return try await request(path: "/models/packages/install", method: "POST", body: body)
    }

    func downloadModel(modelId: String, provider: String?) async throws -> APIModelActionResponse {
        let payload = APIDownloadModelRequest(modelId: modelId, provider: provider)
        let body = try jsonEncoder.encode(payload)
        return try await request(path: "/models/download", method: "POST", body: body)
    }

    func startModelDownload(modelId: String, provider: String?) async throws -> APIModelDownloadJobResponse {
        let payload = APIDownloadModelRequest(modelId: modelId, provider: provider)
        let body = try jsonEncoder.encode(payload)
        return try await request(path: "/models/download/start", method: "POST", body: body)
    }

    func getModelDownload(jobId: String) async throws -> APIModelDownloadJobResponse {
        try await request(path: "/models/download/\(jobId)", method: "GET", body: Optional<Data>.none)
    }

    func loadModel(modelId: String, provider: String?) async throws -> APIModelActionResponse {
        let payload = APILoadModelRequest(modelId: modelId, provider: provider)
        let body = try jsonEncoder.encode(payload)
        return try await request(path: "/models/load", method: "POST", body: body)
    }

    func listTools() async throws -> APIListToolsResponse {
        try await request(path: "/tools", method: "GET", body: Optional<Data>.none)
    }

    func listPermissionPrompts(
        status: String? = nil,
        limit: Int = 100
    ) async throws -> APIListPermissionPromptsResponse {
        var queryItems = [URLQueryItem(name: "limit", value: String(limit))]
        if let status, !status.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            queryItems.append(URLQueryItem(name: "status", value: status))
        }

        let path = buildPath(path: "/tools/permissions/prompts", queryItems: queryItems)
        return try await request(path: path, method: "GET", body: Optional<Data>.none)
    }

    func approvePermissionPrompt(promptID: String) async throws -> APIPermissionPromptActionResponse {
        try await request(
            path: "/tools/permissions/prompts/\(promptID)/approve",
            method: "POST",
            body: Optional<Data>.none
        )
    }

    func denyPermissionPrompt(promptID: String) async throws -> APIPermissionPromptActionResponse {
        try await request(
            path: "/tools/permissions/prompts/\(promptID)/deny",
            method: "POST",
            body: Optional<Data>.none
        )
    }

    func chatCompletions(
        model: String?,
        provider: String?,
        userId: String? = nil,
        sessionId: String? = nil,
        messages: [APIChatMessage],
        tools: [APIChatToolDefinition]? = nil,
        permissionIds: [String]? = nil,
        routing: APIChatRoutingOptions? = nil,
        temperature: Double = 0.7,
        maxTokens: Int = 512
    ) async throws -> APIChatCompletionsResponse {
        let payload = APIChatCompletionsRequest(
            model: model,
            provider: provider,
            userId: userId,
            sessionId: sessionId,
            messages: messages,
            stream: false,
            temperature: temperature,
            maxTokens: maxTokens,
            tools: tools,
            permissionIds: permissionIds,
            routing: routing
        )
        let body = try jsonEncoder.encode(payload)
        return try await request(path: "/v1/chat/completions", method: "POST", body: body)
    }

    func streamChatCompletions(
        model: String?,
        provider: String?,
        userId: String? = nil,
        sessionId: String? = nil,
        messages: [APIChatMessage],
        tools: [APIChatToolDefinition]? = nil,
        routing: APIChatRoutingOptions? = nil,
        temperature: Double = 0.7,
        maxTokens: Int = 512
    ) -> AsyncThrowingStream<String, Error> {
        AsyncThrowingStream { continuation in
            Task {
                do {
                    let payload = APIChatCompletionsRequest(
                        model: model,
                        provider: provider,
                        userId: userId,
                        sessionId: sessionId,
                        messages: messages,
                        stream: true,
                        temperature: temperature,
                        maxTokens: maxTokens,
                        tools: tools,
                        permissionIds: nil,
                        routing: routing
                    )
                    let body = try jsonEncoder.encode(payload)
                    let request = try makeURLRequest(path: "/v1/chat/completions", method: "POST", body: body)

                    let (bytes, response) = try await session.bytes(for: request)
                    guard let http = response as? HTTPURLResponse else {
                        throw APIClientError.invalidResponse
                    }
                    guard (200 ... 299).contains(http.statusCode) else {
                        var chunks: [String] = []
                        for try await line in bytes.lines {
                            chunks.append(line)
                        }
                        let text = chunks.joined(separator: "\n").trimmingCharacters(in: .whitespacesAndNewlines)
                        if let data = text.data(using: .utf8),
                           let detail = parseErrorDetail(from: data) {
                            throw APIClientError.server(detail)
                        }
                        if !text.isEmpty {
                            throw APIClientError.server(text)
                        }
                        throw APIClientError.server("Streaming request failed with status \(http.statusCode)")
                    }

                    for try await line in bytes.lines {
                        guard line.hasPrefix("data: ") else { continue }
                        let payloadLine = String(line.dropFirst(6)).trimmingCharacters(in: .whitespacesAndNewlines)
                        if payloadLine == "[DONE]" {
                            break
                        }

                        guard let data = payloadLine.data(using: .utf8) else { continue }
                        let chunk = try jsonDecoder.decode(APIChatChunkResponse.self, from: data)

                        if let delta = chunk.choices.first?.delta.content, !delta.isEmpty {
                            continuation.yield(delta)
                        }
                    }

                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
        }
    }

    func createAgent(
        name: String,
        systemPrompt: String,
        model: String?,
        tools: [String],
        userId: String?
    ) async throws -> APIAgentRecord {
        let payload = APICreateAgentRequest(
            name: name,
            systemPrompt: systemPrompt,
            model: model,
            tools: tools,
            userId: userId
        )
        let body = try jsonEncoder.encode(payload)
        return try await request(path: "/agents/create", method: "POST", body: body)
    }

    func planQuickstartAgent(
        request requestText: String,
        model: String?,
        userId: String?,
        sessionId: String?,
        idempotencyKey: String? = nil
    ) async throws -> APIQuickstartAgentPlanResponse {
        let payload = APIQuickstartAgentRequest(
            request: requestText,
            model: model,
            userId: userId,
            sessionId: sessionId,
            idempotencyKey: idempotencyKey
        )
        let body = try jsonEncoder.encode(payload)
        return try await request(path: "/v1/agents/quickstart/plan", method: "POST", body: body)
    }

    func applyQuickstartAgent(payload: APIQuickstartAgentApplyPayload) async throws -> APIQuickstartAgentApplyResponse {
        let body = try jsonEncoder.encode(payload)
        return try await request(path: "/v1/agents/quickstart", method: "POST", body: body)
    }

    func listAgents(userId: String?) async throws -> APIListAgentsResponse {
        var path = "/agents"
        if let userId, !userId.isEmpty {
            path += "?user_id=\(userId.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? userId)"
        }
        return try await request(path: path, method: "GET", body: Optional<Data>.none)
    }

    func chatAgent(
        agentId: String,
        userId: String,
        message: String,
        sessionId: String?
    ) async throws -> APIAgentChatResponse {
        let payload = APIAgentChatRequest(
            userId: userId,
            message: message,
            sessionId: sessionId
        )
        let body = try jsonEncoder.encode(payload)
        let path = "/agents/\(agentId)/chat"
        return try await request(path: path, method: "POST", body: body)
    }

    func createAgentRun(
        agentId: String,
        userId: String,
        message: String,
        sessionId: String?,
        maxAttempts: Int? = nil
    ) async throws -> APIAgentRunRecord {
        let payload = APICreateAgentRunRequest(
            userId: userId,
            message: message,
            sessionId: sessionId,
            maxAttempts: maxAttempts
        )
        let body = try jsonEncoder.encode(payload)
        let response: APIAgentRunSingleResponse = try await request(
            path: "/agents/\(agentId)/runs",
            method: "POST",
            body: body
        )
        return response.run
    }

    func listAgentRuns(
        agentId: String,
        userId: String?,
        status: String? = nil,
        limit: Int = 50,
        includeResult: Bool = false,
        includeCheckpoints: Bool = false
    ) async throws -> APIAgentRunListResponse {
        var queryItems = [
            URLQueryItem(name: "limit", value: String(limit)),
            URLQueryItem(name: "include_result", value: includeResult ? "true" : "false"),
            URLQueryItem(name: "include_checkpoints", value: includeCheckpoints ? "true" : "false")
        ]
        if let userId, !userId.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            queryItems.append(URLQueryItem(name: "user_id", value: userId))
        }
        if let status, !status.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            queryItems.append(URLQueryItem(name: "status", value: status))
        }

        let path = buildPath(path: "/agents/\(agentId)/runs", queryItems: queryItems)
        return try await request(path: path, method: "GET", body: Optional<Data>.none)
    }

    func getAgentRun(runId: String) async throws -> APIAgentRunRecord {
        let response: APIAgentRunSingleResponse = try await request(
            path: "/agents/runs/\(runId)",
            method: "GET",
            body: Optional<Data>.none
        )
        return response.run
    }

    func getAgentRunReplay(runId: String) async throws -> APIAgentRunReplayPayload {
        let response: APIAgentRunReplayResponse = try await request(
            path: "/agents/runs/\(runId)/replay",
            method: "GET",
            body: Optional<Data>.none
        )
        return response.replay
    }

    func getAgentRunReplayFiltered(
        runId: String,
        preset: String,
        timelineLimit: Int = 240
    ) async throws -> APIAgentRunReplayPayload {
        let queryItems: [URLQueryItem] = [
            URLQueryItem(name: "preset", value: preset),
            URLQueryItem(name: "timeline_limit", value: String(max(1, min(timelineLimit, 5_000))))
        ]
        let path = buildPath(path: "/agents/runs/\(runId)/replay", queryItems: queryItems)
        let response: APIAgentRunReplayResponse = try await request(
            path: path,
            method: "GET",
            body: Optional<Data>.none
        )
        return response.replay
    }

    func getAgentRunDiagnostics(runId: String) async throws -> APIAgentRunDiagnosticsPayload {
        let response: APIAgentRunDiagnosticsResponse = try await request(
            path: "/agents/runs/\(runId)/diagnostics",
            method: "GET",
            body: Optional<Data>.none
        )
        return response.diagnostics
    }

    func getAgentRunAudit(
        runId: String,
        includeToolCalls: Bool = true,
        includeSecurityActions: Bool = true,
        limit: Int = 2_000
    ) async throws -> APIAgentRunAuditPayload {
        let queryItems: [URLQueryItem] = [
            URLQueryItem(name: "include_tool_calls", value: includeToolCalls ? "true" : "false"),
            URLQueryItem(name: "include_security_actions", value: includeSecurityActions ? "true" : "false"),
            URLQueryItem(name: "limit", value: String(max(1, min(limit, 20_000))))
        ]
        let path = buildPath(path: "/agents/runs/\(runId)/audit", queryItems: queryItems)
        let response: APIAgentRunAuditResponse = try await request(
            path: path,
            method: "GET",
            body: Optional<Data>.none
        )
        return response.audit
    }

    func exportAgentRunAuditJSON(
        runId: String,
        includeToolCalls: Bool = true,
        includeSecurityActions: Bool = true,
        limit: Int = 2_000
    ) async throws -> APIAgentRunAuditExportJSONResponse {
        let queryItems: [URLQueryItem] = [
            URLQueryItem(name: "format", value: "json"),
            URLQueryItem(name: "include_tool_calls", value: includeToolCalls ? "true" : "false"),
            URLQueryItem(name: "include_security_actions", value: includeSecurityActions ? "true" : "false"),
            URLQueryItem(name: "limit", value: String(max(1, min(limit, 20_000))))
        ]
        let path = buildPath(path: "/agents/runs/\(runId)/audit/export", queryItems: queryItems)
        return try await request(path: path, method: "GET", body: Optional<Data>.none)
    }

    func exportAgentRunAuditCSV(
        runId: String,
        includeToolCalls: Bool = true,
        includeSecurityActions: Bool = true,
        limit: Int = 2_000
    ) async throws -> APIAgentRunAuditCSVExport {
        let queryItems: [URLQueryItem] = [
            URLQueryItem(name: "format", value: "csv"),
            URLQueryItem(name: "include_tool_calls", value: includeToolCalls ? "true" : "false"),
            URLQueryItem(name: "include_security_actions", value: includeSecurityActions ? "true" : "false"),
            URLQueryItem(name: "limit", value: String(max(1, min(limit, 20_000))))
        ]
        let path = buildPath(path: "/agents/runs/\(runId)/audit/export", queryItems: queryItems)
        let (data, response) = try await requestRaw(path: path, method: "GET", body: Optional<Data>.none)
        let content = String(data: data, encoding: .utf8) ?? String(decoding: data, as: UTF8.self)
        let contentType = response.value(forHTTPHeaderField: "Content-Type") ?? "text/csv; charset=utf-8"
        let disposition = response.value(forHTTPHeaderField: "Content-Disposition")
        let filename = parseAttachmentFilename(contentDisposition: disposition) ?? "run-audit-\(runId).csv"
        return APIAgentRunAuditCSVExport(
            filename: filename,
            contentType: contentType,
            content: content
        )
    }

    func streamAgentRunEvents(
        runId: String,
        fromIndex: Int = 0,
        pollIntervalMs: Int = 250,
        timeoutSec: Double = 30,
        includeSnapshot: Bool = true,
        includeHeartbeat: Bool = false
    ) -> AsyncThrowingStream<APIAgentRunStreamEvent, Error> {
        AsyncThrowingStream { continuation in
            Task {
                do {
                    let normalizedFromIndex = max(0, fromIndex)
                    let normalizedPollMs = max(50, min(pollIntervalMs, 2_000))
                    let normalizedTimeout = max(1.0, min(timeoutSec, 300.0))
                    let queryItems: [URLQueryItem] = [
                        URLQueryItem(name: "from_index", value: String(normalizedFromIndex)),
                        URLQueryItem(name: "poll_interval_ms", value: String(normalizedPollMs)),
                        URLQueryItem(name: "timeout_sec", value: String(normalizedTimeout)),
                        URLQueryItem(name: "include_snapshot", value: includeSnapshot ? "true" : "false"),
                        URLQueryItem(name: "include_heartbeat", value: includeHeartbeat ? "true" : "false")
                    ]
                    let path = buildPath(path: "/agents/runs/\(runId)/events", queryItems: queryItems)
                    let request = try makeURLRequest(path: path, method: "GET", body: Optional<Data>.none)

                    let (bytes, response) = try await session.bytes(for: request)
                    guard let http = response as? HTTPURLResponse else {
                        throw APIClientError.invalidResponse
                    }
                    guard (200 ... 299).contains(http.statusCode) else {
                        var chunks: [String] = []
                        for try await line in bytes.lines {
                            chunks.append(line)
                        }
                        let text = chunks.joined(separator: "\n").trimmingCharacters(in: .whitespacesAndNewlines)
                        if let data = text.data(using: .utf8),
                           let detail = parseErrorDetail(from: data) {
                            throw APIClientError.server(detail)
                        }
                        if !text.isEmpty {
                            throw APIClientError.server(text)
                        }
                        throw APIClientError.server("Run stream request failed with status \(http.statusCode)")
                    }

                    for try await line in bytes.lines {
                        guard line.hasPrefix("data: ") else { continue }
                        let payloadLine = String(line.dropFirst(6)).trimmingCharacters(in: .whitespacesAndNewlines)
                        if payloadLine.isEmpty {
                            continue
                        }
                        if payloadLine == "[DONE]" {
                            break
                        }

                        guard let data = payloadLine.data(using: .utf8) else {
                            continue
                        }
                        do {
                            let event = try jsonDecoder.decode(APIAgentRunStreamEvent.self, from: data)
                            continuation.yield(event)
                        } catch {
                            throw APIClientError.decoding(error.localizedDescription)
                        }
                    }

                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
        }
    }

    func cancelAgentRun(runId: String) async throws -> APIAgentRunRecord {
        let response: APIAgentRunSingleResponse = try await request(
            path: "/agents/runs/\(runId)/cancel",
            method: "POST",
            body: Optional<Data>.none
        )
        return response.run
    }

    func resumeAgentRun(runId: String) async throws -> APIAgentRunRecord {
        let response: APIAgentRunSingleResponse = try await request(
            path: "/agents/runs/\(runId)/resume",
            method: "POST",
            body: Optional<Data>.none
        )
        return response.run
    }

    func createAutomation(
        agentId: String,
        userId: String,
        message: String,
        sessionId: String?,
        intervalSec: Int? = nil,
        scheduleType: String? = nil,
        schedule: [String: JSONValue]? = nil,
        timezone: String = "UTC",
        startImmediately: Bool
    ) async throws -> APIAutomationRecord {
        let payload = APICreateAutomationRequest(
            agentId: agentId,
            userId: userId,
            message: message,
            sessionId: sessionId,
            intervalSec: intervalSec,
            scheduleType: scheduleType,
            schedule: schedule,
            timezone: timezone,
            startImmediately: startImmediately
        )
        let body = try jsonEncoder.encode(payload)
        let response: APIAutomationSingleResponse = try await request(
            path: "/automations/create",
            method: "POST",
            body: body
        )
        return response.automation
    }

    func updateAutomation(
        automationId: String,
        message: String? = nil,
        sessionId: String? = nil,
        intervalSec: Int? = nil,
        scheduleType: String? = nil,
        schedule: [String: JSONValue]? = nil,
        timezone: String? = nil
    ) async throws -> APIAutomationRecord {
        let payload = APIUpdateAutomationRequest(
            message: message,
            sessionId: sessionId,
            intervalSec: intervalSec,
            scheduleType: scheduleType,
            schedule: schedule,
            timezone: timezone
        )
        let body = try jsonEncoder.encode(payload)
        let response: APIAutomationSingleResponse = try await request(
            path: "/automations/\(automationId)/update",
            method: "POST",
            body: body
        )
        return response.automation
    }

    func listAutomations(
        userId: String?,
        agentId: String?,
        enabled: Bool? = nil,
        limit: Int = 200
    ) async throws -> APIAutomationListResponse {
        var queryItems: [URLQueryItem] = [
            URLQueryItem(name: "limit", value: String(limit))
        ]
        if let userId, !userId.isEmpty {
            queryItems.append(URLQueryItem(name: "user_id", value: userId))
        }
        if let agentId, !agentId.isEmpty {
            queryItems.append(URLQueryItem(name: "agent_id", value: agentId))
        }
        if let enabled {
            queryItems.append(URLQueryItem(name: "enabled", value: enabled ? "true" : "false"))
        }
        let path = buildPath(path: "/automations", queryItems: queryItems)
        return try await request(path: path, method: "GET", body: Optional<Data>.none)
    }

    func pauseAutomation(automationId: String) async throws -> APIAutomationRecord {
        let response: APIAutomationSingleResponse = try await request(
            path: "/automations/\(automationId)/pause",
            method: "POST",
            body: Optional<Data>.none
        )
        return response.automation
    }

    func resumeAutomation(automationId: String) async throws -> APIAutomationRecord {
        let response: APIAutomationSingleResponse = try await request(
            path: "/automations/\(automationId)/resume",
            method: "POST",
            body: Optional<Data>.none
        )
        return response.automation
    }

    func runAutomationNow(automationId: String) async throws -> APIAutomationRecord {
        let response: APIAutomationSingleResponse = try await request(
            path: "/automations/\(automationId)/run",
            method: "POST",
            body: Optional<Data>.none
        )
        return response.automation
    }

    func deleteAutomation(automationId: String) async throws -> APIAutomationDeleteResponse {
        try await request(
            path: "/automations/\(automationId)",
            method: "DELETE",
            body: Optional<Data>.none
        )
    }

    func listAutomationEvents(
        automationId: String,
        limit: Int = 100
    ) async throws -> APIAutomationEventsResponse {
        let path = buildPath(
            path: "/automations/\(automationId)/events",
            queryItems: [URLQueryItem(name: "limit", value: String(limit))]
        )
        return try await request(path: path, method: "GET", body: Optional<Data>.none)
    }

    func listInbox(
        userId: String?,
        unreadOnly: Bool = false,
        category: String? = nil,
        limit: Int = 200
    ) async throws -> APIInboxListResponse {
        var queryItems: [URLQueryItem] = [
            URLQueryItem(name: "limit", value: String(limit)),
            URLQueryItem(name: "unread_only", value: unreadOnly ? "true" : "false")
        ]
        if let userId, !userId.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            queryItems.append(URLQueryItem(name: "user_id", value: userId))
        }
        if let category, !category.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            queryItems.append(URLQueryItem(name: "category", value: category))
        }
        let path = buildPath(path: "/inbox", queryItems: queryItems)
        return try await request(path: path, method: "GET", body: Optional<Data>.none)
    }

    func setInboxItemRead(itemId: String, isRead: Bool) async throws -> APIInboxItem {
        let endpoint = isRead ? "read" : "unread"
        let response: APIInboxSingleResponse = try await request(
            path: "/inbox/\(itemId)/\(endpoint)",
            method: "POST",
            body: Optional<Data>.none
        )
        return response.item
    }

    func debugMemoryContext(
        userId: String,
        agentId: String?,
        sessionId: String?,
        query: String,
        workingLimit: Int = 12,
        episodicLimit: Int = 16,
        semanticTopK: Int = 8
    ) async throws -> APIMemoryContextResponse {
        var queryItems: [URLQueryItem] = [
            URLQueryItem(name: "user_id", value: userId),
            URLQueryItem(name: "query", value: query),
            URLQueryItem(name: "working_limit", value: String(workingLimit)),
            URLQueryItem(name: "episodic_limit", value: String(episodicLimit)),
            URLQueryItem(name: "semantic_top_k", value: String(semanticTopK))
        ]
        if let agentId, !agentId.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            queryItems.append(URLQueryItem(name: "agent_id", value: agentId))
        }
        if let sessionId, !sessionId.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            queryItems.append(URLQueryItem(name: "session_id", value: sessionId))
        }

        let path = buildPath(path: "/debug/memory/context", queryItems: queryItems)
        return try await request(path: path, method: "GET", body: Optional<Data>.none)
    }

    func debugMemoryRetrieval(
        userId: String,
        query: String,
        topK: Int = 8
    ) async throws -> APIMemoryRetrievalResponse {
        let queryItems: [URLQueryItem] = [
            URLQueryItem(name: "user_id", value: userId),
            URLQueryItem(name: "query", value: query),
            URLQueryItem(name: "top_k", value: String(topK))
        ]
        let path = buildPath(path: "/debug/memory/retrieval", queryItems: queryItems)
        return try await request(path: path, method: "GET", body: Optional<Data>.none)
    }

    func debugMemoryExtractions(userId: String, limit: Int = 20) async throws -> APIMemoryExtractionsResponse {
        let queryItems: [URLQueryItem] = [
            URLQueryItem(name: "user_id", value: userId),
            URLQueryItem(name: "limit", value: String(limit))
        ]
        let path = buildPath(path: "/debug/memory/extractions", queryItems: queryItems)
        return try await request(path: path, method: "GET", body: Optional<Data>.none)
    }

    func debugMemoryConflicts(userId: String, limit: Int = 20) async throws -> APIMemoryConflictsResponse {
        let queryItems: [URLQueryItem] = [
            URLQueryItem(name: "user_id", value: userId),
            URLQueryItem(name: "limit", value: String(limit))
        ]
        let path = buildPath(path: "/debug/memory/conflicts", queryItems: queryItems)
        return try await request(path: path, method: "GET", body: Optional<Data>.none)
    }

    private func request<T: Decodable>(path: String, method: String, body: Data?) async throws -> T {
        let data = try await requestData(path: path, method: method, body: body)

        do {
            return try jsonDecoder.decode(T.self, from: data)
        } catch {
            throw APIClientError.decoding(error.localizedDescription)
        }
    }

    private func requestData(path: String, method: String, body: Data?) async throws -> Data {
        let (data, _) = try await requestRaw(path: path, method: method, body: body)
        return data
    }

    private func requestRaw(path: String, method: String, body: Data?) async throws -> (Data, HTTPURLResponse) {
        let request = try makeURLRequest(path: path, method: method, body: body)
        let (data, response) = try await session.data(for: request)

        guard let http = response as? HTTPURLResponse else {
            throw APIClientError.invalidResponse
        }

        guard (200 ... 299).contains(http.statusCode) else {
            if let detail = parseErrorDetail(from: data) {
                throw APIClientError.server(detail)
            }

            let text = String(data: data, encoding: .utf8) ?? "HTTP \(http.statusCode)"
            throw APIClientError.server(text)
        }

        return (data, http)
    }

    private func buildPath(path: String, queryItems: [URLQueryItem]) -> String {
        guard !queryItems.isEmpty else {
            return path
        }

        var components = URLComponents()
        components.queryItems = queryItems
        if let encoded = components.percentEncodedQuery, !encoded.isEmpty {
            return "\(path)?\(encoded)"
        }
        return path
    }

    private func makeURLRequest(path: String, method: String, body: Data?) throws -> URLRequest {
        let base = baseURLProvider().trimmingCharacters(in: .whitespacesAndNewlines)
        guard let url = URL(string: base + path) else {
            throw APIClientError.invalidURL
        }

        var request = URLRequest(url: url)
        request.httpMethod = method
        request.timeoutInterval = 180
        request.addValue("application/json", forHTTPHeaderField: "Content-Type")
        request.addValue("application/json", forHTTPHeaderField: "Accept")
        let authToken = authTokenProvider().trimmingCharacters(in: .whitespacesAndNewlines)
        if !authToken.isEmpty {
            request.addValue("Bearer \(authToken)", forHTTPHeaderField: "Authorization")
        }
        request.httpBody = body
        return request
    }

    private func parseErrorDetail(from data: Data) -> String? {
        if let errorObject = try? jsonDecoder.decode([String: String].self, from: data),
           let detail = errorObject["detail"],
           !detail.isEmpty {
            return detail
        }

        if let wrapped = try? jsonDecoder.decode([String: [String: String]].self, from: data),
           let error = wrapped["error"],
           let message = error["message"],
           !message.isEmpty {
            return message
        }

        return nil
    }

    private func parseAttachmentFilename(contentDisposition: String?) -> String? {
        guard let contentDisposition else {
            return nil
        }
        let sections = contentDisposition.split(separator: ";")
        for section in sections {
            let trimmed = section.trimmingCharacters(in: .whitespacesAndNewlines)
            guard trimmed.lowercased().hasPrefix("filename=") else {
                continue
            }
            let raw = String(trimmed.dropFirst("filename=".count))
            let filename = raw.trimmingCharacters(in: CharacterSet(charactersIn: "\""))
            if !filename.isEmpty {
                return filename
            }
        }
        return nil
    }
}
