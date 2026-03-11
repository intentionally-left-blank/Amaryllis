import Foundation

enum AppTab: String, CaseIterable, Identifiable {
    case chat = "Chat"
    case models = "Models"
    case agents = "Agents"
    case settings = "Settings"

    var id: String { rawValue }

    var icon: String {
        switch self {
        case .chat:
            return "bubble.left.and.bubble.right.fill"
        case .models:
            return "square.stack.3d.up.fill"
        case .agents:
            return "person.2.fill"
        case .settings:
            return "gearshape.fill"
        }
    }
}

struct APIHealthResponse: Decodable {
    let status: String
    let app: String
    let activeProvider: String
    let activeModel: String

    private enum CodingKeys: String, CodingKey {
        case status
        case app
        case activeProvider = "active_provider"
        case activeModel = "active_model"
    }
}

struct APIModelCatalog: Decodable {
    struct SuggestedModel: Decodable, Identifiable {
        let id: String
        let label: String
    }

    struct Active: Decodable {
        let provider: String
        let model: String
    }

    struct ProviderPayload: Decodable {
        let available: Bool
        let error: String?
        let items: [APIModelItem]
    }

    let active: Active
    let providers: [String: ProviderPayload]
    let suggested: [String: [SuggestedModel]]?
}

struct APIModelItem: Decodable, Identifiable {
    let id: String
    let provider: String
    let path: String?
    let active: Bool
    let metadata: [String: JSONValue]?
}

struct APIDownloadModelRequest: Encodable {
    let modelId: String
    let provider: String?

    private enum CodingKeys: String, CodingKey {
        case modelId = "model_id"
        case provider
    }
}

struct APILoadModelRequest: Encodable {
    let modelId: String
    let provider: String?

    private enum CodingKeys: String, CodingKey {
        case modelId = "model_id"
        case provider
    }
}

struct APIModelActionResponse: Decodable {
    let status: String
    let provider: String
    let model: String
}

struct APIToolItem: Decodable, Identifiable {
    let name: String
    let description: String
    let inputSchema: [String: JSONValue]
    let source: String
    let riskLevel: String
    let approvalMode: String
    let isolation: String

    var id: String { name }

    private enum CodingKeys: String, CodingKey {
        case name
        case description
        case inputSchema = "input_schema"
        case source
        case riskLevel = "risk_level"
        case approvalMode = "approval_mode"
        case isolation
    }
}

struct APIListToolsResponse: Decodable {
    let items: [APIToolItem]
    let count: Int
    let requestId: String?

    private enum CodingKeys: String, CodingKey {
        case items
        case count
        case requestId = "request_id"
    }
}

struct APIPermissionPrompt: Decodable, Identifiable {
    let id: String
    let status: String
    let toolName: String
    let argumentsHash: String
    let argumentsPreview: [String: JSONValue]
    let reason: String
    let requestId: String?
    let userId: String?
    let sessionId: String?
    let createdAt: String
    let updatedAt: String

    private enum CodingKeys: String, CodingKey {
        case id
        case status
        case toolName = "tool_name"
        case argumentsHash = "arguments_hash"
        case argumentsPreview = "arguments_preview"
        case reason
        case requestId = "request_id"
        case userId = "user_id"
        case sessionId = "session_id"
        case createdAt = "created_at"
        case updatedAt = "updated_at"
    }
}

struct APIListPermissionPromptsResponse: Decodable {
    let items: [APIPermissionPrompt]
    let count: Int
    let requestId: String?

    private enum CodingKeys: String, CodingKey {
        case items
        case count
        case requestId = "request_id"
    }
}

struct APIPermissionPromptActionResponse: Decodable {
    let prompt: APIPermissionPrompt
    let requestId: String?

    private enum CodingKeys: String, CodingKey {
        case prompt
        case requestId = "request_id"
    }
}

struct APIChatMessage: Codable {
    let role: String
    let content: String
    let name: String?
}

struct APIChatToolFunction: Encodable {
    let name: String
    let description: String
    let parameters: [String: JSONValue]
}

struct APIChatToolDefinition: Encodable {
    let type: String
    let function: APIChatToolFunction
}

struct APIChatCompletionsRequest: Encodable {
    let model: String?
    let provider: String?
    let messages: [APIChatMessage]
    let stream: Bool
    let temperature: Double
    let maxTokens: Int
    let tools: [APIChatToolDefinition]?
    let permissionIds: [String]?

    private enum CodingKeys: String, CodingKey {
        case model
        case provider
        case messages
        case stream
        case temperature
        case maxTokens = "max_tokens"
        case tools
        case permissionIds = "permission_ids"
    }
}

struct APIChatToolEvent: Decodable {
    let attempt: Int?
    let tool: String?
    let status: String?
    let durationMs: Double?
    let error: String?
    let permissionPromptId: String?

    private enum CodingKeys: String, CodingKey {
        case attempt
        case tool
        case status
        case durationMs = "duration_ms"
        case error
        case permissionPromptId = "permission_prompt_id"
    }
}

struct APIChatCompletionsResponse: Decodable {
    struct Choice: Decodable {
        struct ChoiceMessage: Decodable {
            let role: String
            let content: String?
        }

        let index: Int
        let message: ChoiceMessage
        let finishReason: String?

        private enum CodingKeys: String, CodingKey {
            case index
            case message
            case finishReason = "finish_reason"
        }
    }

    let id: String
    let model: String
    let provider: String?
    let choices: [Choice]
    let toolEvents: [APIChatToolEvent]?

    private enum CodingKeys: String, CodingKey {
        case id
        case model
        case provider
        case choices
        case toolEvents = "tool_events"
    }
}

struct APIChatChunkResponse: Decodable {
    struct Choice: Decodable {
        struct Delta: Decodable {
            let role: String?
            let content: String?
        }

        let index: Int
        let delta: Delta
        let finishReason: String?

        private enum CodingKeys: String, CodingKey {
            case index
            case delta
            case finishReason = "finish_reason"
        }
    }

    let id: String
    let model: String
    let provider: String?
    let choices: [Choice]
}

struct APICreateAgentRequest: Encodable {
    let name: String
    let systemPrompt: String
    let model: String?
    let tools: [String]
    let userId: String?

    private enum CodingKeys: String, CodingKey {
        case name
        case systemPrompt = "system_prompt"
        case model
        case tools
        case userId = "user_id"
    }
}

struct APIAgentRecord: Decodable, Identifiable {
    let id: String
    let name: String
    let systemPrompt: String
    let model: String?
    let tools: [String]
    let userId: String?
    let createdAt: String

    private enum CodingKeys: String, CodingKey {
        case id
        case name
        case systemPrompt = "system_prompt"
        case model
        case tools
        case userId = "user_id"
        case createdAt = "created_at"
    }
}

struct APIListAgentsResponse: Decodable {
    let items: [APIAgentRecord]
    let count: Int
}

struct APIAgentChatRequest: Encodable {
    let userId: String
    let message: String
    let sessionId: String?

    private enum CodingKeys: String, CodingKey {
        case userId = "user_id"
        case message
        case sessionId = "session_id"
    }
}

struct APIAgentChatResponse: Decodable {
    struct PlanStep: Decodable, Identifiable {
        let id: Int
        let description: String
    }

    let agentId: String
    let sessionId: String?
    let strategy: String
    let plan: [PlanStep]
    let provider: String
    let model: String
    let response: String

    private enum CodingKeys: String, CodingKey {
        case agentId = "agent_id"
        case sessionId = "session_id"
        case strategy
        case plan
        case provider
        case model
        case response
    }
}

struct LocalChatMessage: Codable, Identifiable, Equatable {
    let id: UUID
    let role: String
    var content: String
    let createdAt: Date
}

struct LocalChatSession: Codable, Identifiable, Equatable {
    let id: UUID
    var title: String
    let createdAt: Date
    var updatedAt: Date
    var messages: [LocalChatMessage]
}

enum JSONValue: Codable {
    case string(String)
    case number(Double)
    case bool(Bool)
    case object([String: JSONValue])
    case array([JSONValue])
    case null

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if container.decodeNil() {
            self = .null
        } else if let value = try? container.decode(Bool.self) {
            self = .bool(value)
        } else if let value = try? container.decode(Double.self) {
            self = .number(value)
        } else if let value = try? container.decode(String.self) {
            self = .string(value)
        } else if let value = try? container.decode([String: JSONValue].self) {
            self = .object(value)
        } else if let value = try? container.decode([JSONValue].self) {
            self = .array(value)
        } else {
            throw DecodingError.dataCorruptedError(in: container, debugDescription: "Unsupported JSON value")
        }
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case .string(let value):
            try container.encode(value)
        case .number(let value):
            try container.encode(value)
        case .bool(let value):
            try container.encode(value)
        case .object(let value):
            try container.encode(value)
        case .array(let value):
            try container.encode(value)
        case .null:
            try container.encodeNil()
        }
    }
}
