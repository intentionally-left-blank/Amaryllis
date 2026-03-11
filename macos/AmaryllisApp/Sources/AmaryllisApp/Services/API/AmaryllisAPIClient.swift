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
    private let jsonEncoder: JSONEncoder
    private let jsonDecoder: JSONDecoder

    init(baseURLProvider: @escaping () -> String, session: URLSession = .shared) {
        self.baseURLProvider = baseURLProvider
        self.session = session

        self.jsonEncoder = JSONEncoder()
        self.jsonDecoder = JSONDecoder()
    }

    func health() async throws -> APIHealthResponse {
        try await request(path: "/health", method: "GET", body: Optional<Data>.none)
    }

    func listModels() async throws -> APIModelCatalog {
        try await request(path: "/models", method: "GET", body: Optional<Data>.none)
    }

    func downloadModel(modelId: String, provider: String?) async throws -> APIModelActionResponse {
        let payload = APIDownloadModelRequest(modelId: modelId, provider: provider)
        let body = try jsonEncoder.encode(payload)
        return try await request(path: "/models/download", method: "POST", body: body)
    }

    func loadModel(modelId: String, provider: String?) async throws -> APIModelActionResponse {
        let payload = APILoadModelRequest(modelId: modelId, provider: provider)
        let body = try jsonEncoder.encode(payload)
        return try await request(path: "/models/load", method: "POST", body: body)
    }

    func chatCompletions(
        model: String?,
        provider: String?,
        messages: [APIChatMessage],
        tools: [APIChatToolDefinition]? = nil,
        temperature: Double = 0.7,
        maxTokens: Int = 512
    ) async throws -> APIChatCompletionsResponse {
        let payload = APIChatCompletionsRequest(
            model: model,
            provider: provider,
            messages: messages,
            stream: false,
            temperature: temperature,
            maxTokens: maxTokens,
            tools: tools
        )
        let body = try jsonEncoder.encode(payload)
        return try await request(path: "/v1/chat/completions", method: "POST", body: body)
    }

    func streamChatCompletions(
        model: String?,
        provider: String?,
        messages: [APIChatMessage],
        tools: [APIChatToolDefinition]? = nil,
        temperature: Double = 0.7,
        maxTokens: Int = 512
    ) -> AsyncThrowingStream<String, Error> {
        AsyncThrowingStream { continuation in
            Task {
                do {
                    let payload = APIChatCompletionsRequest(
                        model: model,
                        provider: provider,
                        messages: messages,
                        stream: true,
                        temperature: temperature,
                        maxTokens: maxTokens,
                        tools: tools
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

    private func request<T: Decodable>(path: String, method: String, body: Data?) async throws -> T {
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

        do {
            return try jsonDecoder.decode(T.self, from: data)
        } catch {
            throw APIClientError.decoding(error.localizedDescription)
        }
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
}
