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
        let sizeBytes: Int?

        init(id: String, label: String, sizeBytes: Int? = nil) {
            self.id = id
            self.label = label
            self.sizeBytes = sizeBytes
        }

        private enum CodingKeys: String, CodingKey {
            case id
            case label
            case sizeBytes = "size_bytes"
        }
    }

    struct Active: Decodable {
        let provider: String
        let model: String
    }

    struct ProviderCapability: Decodable {
        let supportsDownload: Bool
        let supportsLoad: Bool

        private enum CodingKeys: String, CodingKey {
            case supportsDownload = "supports_download"
            case supportsLoad = "supports_load"
        }
    }

    struct ProviderPayload: Decodable {
        let available: Bool
        let error: String?
        let items: [APIModelItem]
    }

    let active: Active
    let providers: [String: ProviderPayload]
    let capabilities: [String: ProviderCapability]?
    let suggested: [String: [SuggestedModel]]?
    let routingModes: [String]?

    private enum CodingKeys: String, CodingKey {
        case active
        case providers
        case capabilities
        case suggested
        case routingModes = "routing_modes"
    }
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

struct APIModelDownloadJob: Decodable, Identifiable {
    let id: String
    let provider: String
    let model: String
    let status: String
    let progress: Double
    let completedBytes: Int?
    let totalBytes: Int?
    let message: String?
    let error: String?

    var isTerminal: Bool {
        let normalized = status.lowercased()
        return normalized == "succeeded" || normalized == "failed"
    }

    private enum CodingKeys: String, CodingKey {
        case id
        case provider
        case model
        case status
        case progress
        case completedBytes = "completed_bytes"
        case totalBytes = "total_bytes"
        case message
        case error
    }
}

struct APIModelDownloadJobResponse: Decodable {
    let requestId: String?
    let job: APIModelDownloadJob
    let alreadyRunning: Bool

    private enum CodingKeys: String, CodingKey {
        case requestId = "request_id"
        case job
        case alreadyRunning = "already_running"
    }
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
    let userId: String?
    let sessionId: String?
    let messages: [APIChatMessage]
    let stream: Bool
    let temperature: Double
    let maxTokens: Int
    let tools: [APIChatToolDefinition]?
    let permissionIds: [String]?
    let routing: APIChatRoutingOptions?

    private enum CodingKeys: String, CodingKey {
        case model
        case provider
        case userId = "user_id"
        case sessionId = "session_id"
        case messages
        case stream
        case temperature
        case maxTokens = "max_tokens"
        case tools
        case permissionIds = "permission_ids"
        case routing
    }
}

struct APIChatRoutingOptions: Encodable {
    let mode: String
    let requireStream: Bool
    let requireTools: Bool
    let preferLocal: Bool?
    let minParamsB: Double?
    let maxParamsB: Double?
    let includeSuggested: Bool

    private enum CodingKeys: String, CodingKey {
        case mode
        case requireStream = "require_stream"
        case requireTools = "require_tools"
        case preferLocal = "prefer_local"
        case minParamsB = "min_params_b"
        case maxParamsB = "max_params_b"
        case includeSuggested = "include_suggested"
    }
}

struct APIChatRouteTarget: Decodable {
    let provider: String
    let model: String
    let score: Double?
    let reason: String?
    let guardrailPenalty: Double?

    private enum CodingKeys: String, CodingKey {
        case provider
        case model
        case score
        case reason
        case guardrailPenalty = "guardrail_penalty"
    }
}

struct APIChatRoutingFinal: Decodable {
    let provider: String
    let model: String
    let fallbackUsed: Bool?

    private enum CodingKeys: String, CodingKey {
        case provider
        case model
        case fallbackUsed = "fallback_used"
    }
}

struct APIChatRoutingFailoverEvent: Decodable {
    let attempt: Int?
    let provider: String?
    let model: String?
    let errorClass: String?
    let retryable: Bool?
    let message: String?

    private enum CodingKeys: String, CodingKey {
        case attempt
        case provider
        case model
        case errorClass = "error_class"
        case retryable
        case message
    }
}

struct APIChatRoutingDecision: Decodable {
    let mode: String?
    let selected: APIChatRouteTarget?
    let fallbacks: [APIChatRouteTarget]?
    let consideredCount: Int?
    let final: APIChatRoutingFinal?
    let failoverEvents: [APIChatRoutingFailoverEvent]?

    private enum CodingKeys: String, CodingKey {
        case mode
        case selected
        case fallbacks
        case consideredCount = "considered_count"
        case final
        case failoverEvents = "failover_events"
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
    let routing: APIChatRoutingDecision?

    private enum CodingKeys: String, CodingKey {
        case id
        case model
        case provider
        case choices
        case toolEvents = "tool_events"
        case routing
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

struct APICreateAgentRunRequest: Encodable {
    let userId: String
    let message: String
    let sessionId: String?
    let maxAttempts: Int?

    private enum CodingKeys: String, CodingKey {
        case userId = "user_id"
        case message
        case sessionId = "session_id"
        case maxAttempts = "max_attempts"
    }
}

struct APIAgentRunCheckpoint: Codable, Identifiable {
    let data: [String: JSONValue]

    var id: String { "\(timestamp):\(stage ?? "-"):\(attempt ?? -1)" }
    var timestamp: String {
        data["timestamp"]?.stringValue ?? ""
    }
    var stage: String? {
        data["stage"]?.stringValue
    }
    var message: String? {
        data["message"]?.stringValue
    }
    var attempt: Int? {
        data["attempt"]?.intValue
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        data = try container.decode([String: JSONValue].self)
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        try container.encode(data)
    }
}

struct APIAgentRunRecord: Codable, Identifiable {
    let id: String
    let agentId: String
    let userId: String
    let sessionId: String?
    let inputMessage: String
    let status: String
    let attempts: Int
    let maxAttempts: Int
    let cancelRequested: Int
    let result: [String: JSONValue]?
    let errorMessage: String?
    let checkpoints: [APIAgentRunCheckpoint]
    let createdAt: String
    let updatedAt: String
    let startedAt: String?
    let finishedAt: String?

    var isCancelRequested: Bool { cancelRequested == 1 }

    private enum CodingKeys: String, CodingKey {
        case id
        case agentId = "agent_id"
        case userId = "user_id"
        case sessionId = "session_id"
        case inputMessage = "input_message"
        case status
        case attempts
        case maxAttempts = "max_attempts"
        case cancelRequested = "cancel_requested"
        case result
        case errorMessage = "error_message"
        case checkpoints
        case createdAt = "created_at"
        case updatedAt = "updated_at"
        case startedAt = "started_at"
        case finishedAt = "finished_at"
    }
}

struct APIAgentRunSingleResponse: Codable {
    let run: APIAgentRunRecord
    let requestId: String?

    private enum CodingKeys: String, CodingKey {
        case run
        case requestId = "request_id"
    }
}

struct APIAgentRunListResponse: Codable {
    let items: [APIAgentRunRecord]
    let count: Int
    let requestId: String?

    private enum CodingKeys: String, CodingKey {
        case items
        case count
        case requestId = "request_id"
    }
}

struct APIAgentRunReplayTimelineItem: Codable, Identifiable {
    let index: Int
    let timestamp: String
    let stage: String
    let attempt: Int?
    let message: String
    let retryable: Bool?

    var id: String { "\(index):\(stage)" }
}

struct APIAgentRunReplayAttemptSummary: Codable, Identifiable {
    let attempt: Int
    let stageCounts: [String: Int]
    let startedAt: String?
    let finishedAt: String?
    let toolRounds: Int
    let verificationRepairs: Int
    let errors: [String]

    var id: Int { attempt }

    private enum CodingKeys: String, CodingKey {
        case attempt
        case stageCounts = "stage_counts"
        case startedAt = "started_at"
        case finishedAt = "finished_at"
        case toolRounds = "tool_rounds"
        case verificationRepairs = "verification_repairs"
        case errors
    }
}

struct APIAgentRunReplaySnapshot: Codable, Identifiable {
    let timestamp: String
    let attempt: Int?
    let completedSteps: [String]

    var id: String { "\(timestamp):\(attempt ?? -1)" }

    private enum CodingKeys: String, CodingKey {
        case timestamp
        case attempt
        case completedSteps = "completed_steps"
    }
}

struct APIAgentRunReplayPayload: Codable {
    let runId: String
    let agentId: String?
    let userId: String?
    let sessionId: String?
    let status: String?
    let attempts: Int
    let maxAttempts: Int
    let checkpointCount: Int
    let timeline: [APIAgentRunReplayTimelineItem]
    let attemptSummary: [APIAgentRunReplayAttemptSummary]
    let resumeSnapshots: [APIAgentRunReplaySnapshot]
    let latestResumeState: [String: JSONValue]?
    let hasResult: Bool
    let errorMessage: String?

    private enum CodingKeys: String, CodingKey {
        case runId = "run_id"
        case agentId = "agent_id"
        case userId = "user_id"
        case sessionId = "session_id"
        case status
        case attempts
        case maxAttempts = "max_attempts"
        case checkpointCount = "checkpoint_count"
        case timeline
        case attemptSummary = "attempt_summary"
        case resumeSnapshots = "resume_snapshots"
        case latestResumeState = "latest_resume_state"
        case hasResult = "has_result"
        case errorMessage = "error_message"
    }
}

struct APIAgentRunReplayResponse: Codable {
    let replay: APIAgentRunReplayPayload
    let requestId: String?

    private enum CodingKeys: String, CodingKey {
        case replay
        case requestId = "request_id"
    }
}

struct APICreateAutomationRequest: Encodable {
    let agentId: String
    let userId: String
    let message: String
    let sessionId: String?
    let intervalSec: Int?
    let scheduleType: String?
    let schedule: [String: JSONValue]?
    let timezone: String
    let startImmediately: Bool

    private enum CodingKeys: String, CodingKey {
        case agentId = "agent_id"
        case userId = "user_id"
        case message
        case sessionId = "session_id"
        case intervalSec = "interval_sec"
        case scheduleType = "schedule_type"
        case schedule
        case timezone
        case startImmediately = "start_immediately"
    }
}

struct APIUpdateAutomationRequest: Encodable {
    let message: String?
    let sessionId: String?
    let intervalSec: Int?
    let scheduleType: String?
    let schedule: [String: JSONValue]?
    let timezone: String?

    private enum CodingKeys: String, CodingKey {
        case message
        case sessionId = "session_id"
        case intervalSec = "interval_sec"
        case scheduleType = "schedule_type"
        case schedule
        case timezone
    }
}

struct APIAutomationRecord: Decodable, Identifiable {
    let id: String
    let agentId: String
    let userId: String
    let sessionId: String?
    let message: String
    let intervalSec: Int
    let scheduleType: String
    let schedule: [String: JSONValue]
    let timezone: String
    let isEnabled: Bool
    let nextRunAt: String
    let lastRunAt: String?
    let lastError: String?
    let consecutiveFailures: Int
    let escalationLevel: String
    let createdAt: String
    let updatedAt: String

    private enum CodingKeys: String, CodingKey {
        case id
        case agentId = "agent_id"
        case userId = "user_id"
        case sessionId = "session_id"
        case message
        case intervalSec = "interval_sec"
        case scheduleType = "schedule_type"
        case schedule
        case timezone
        case isEnabled = "is_enabled"
        case nextRunAt = "next_run_at"
        case lastRunAt = "last_run_at"
        case lastError = "last_error"
        case consecutiveFailures = "consecutive_failures"
        case escalationLevel = "escalation_level"
        case createdAt = "created_at"
        case updatedAt = "updated_at"
    }
}

struct APIAutomationEvent: Decodable, Identifiable {
    let id: Int
    let automationId: String
    let eventType: String
    let message: String
    let runId: String?
    let createdAt: String

    private enum CodingKeys: String, CodingKey {
        case id
        case automationId = "automation_id"
        case eventType = "event_type"
        case message
        case runId = "run_id"
        case createdAt = "created_at"
    }
}

struct APIAutomationSingleResponse: Decodable {
    let automation: APIAutomationRecord
    let requestId: String?

    private enum CodingKeys: String, CodingKey {
        case automation
        case requestId = "request_id"
    }
}

struct APIAutomationListResponse: Decodable {
    let items: [APIAutomationRecord]
    let count: Int
    let requestId: String?

    private enum CodingKeys: String, CodingKey {
        case items
        case count
        case requestId = "request_id"
    }
}

struct APIAutomationDeleteResponse: Decodable {
    let status: String
    let automationId: String
    let requestId: String?

    private enum CodingKeys: String, CodingKey {
        case status
        case automationId = "automation_id"
        case requestId = "request_id"
    }
}

struct APIAutomationEventsResponse: Decodable {
    let items: [APIAutomationEvent]
    let count: Int
    let requestId: String?

    private enum CodingKeys: String, CodingKey {
        case items
        case count
        case requestId = "request_id"
    }
}

struct APIInboxItem: Decodable, Identifiable {
    let id: String
    let userId: String
    let category: String
    let severity: String
    let title: String
    let body: String
    let sourceType: String?
    let sourceId: String?
    let runId: String?
    let metadata: [String: JSONValue]
    let isRead: Bool
    let requiresAction: Bool
    let createdAt: String
    let updatedAt: String

    private enum CodingKeys: String, CodingKey {
        case id
        case userId = "user_id"
        case category
        case severity
        case title
        case body
        case sourceType = "source_type"
        case sourceId = "source_id"
        case runId = "run_id"
        case metadata
        case isRead = "is_read"
        case requiresAction = "requires_action"
        case createdAt = "created_at"
        case updatedAt = "updated_at"
    }
}

struct APIInboxListResponse: Decodable {
    let items: [APIInboxItem]
    let count: Int
    let requestId: String?

    private enum CodingKeys: String, CodingKey {
        case items
        case count
        case requestId = "request_id"
    }
}

struct APIInboxSingleResponse: Decodable {
    let item: APIInboxItem
    let requestId: String?

    private enum CodingKeys: String, CodingKey {
        case item
        case requestId = "request_id"
    }
}

struct APIMemoryWorkingItem: Codable, Identifiable {
    let key: String
    let value: String
    let sessionId: String
    let kind: String
    let confidence: Double
    let importance: Double
    let updatedAt: String

    var id: String { "\(sessionId):\(key)" }

    private enum CodingKeys: String, CodingKey {
        case key
        case value
        case sessionId = "session_id"
        case kind
        case confidence
        case importance
        case updatedAt = "updated_at"
    }
}

struct APIMemoryEpisodicItem: Codable, Identifiable {
    let role: String
    let content: String
    let createdAt: String
    let sessionId: String?
    let kind: String
    let confidence: Double
    let importance: Double
    let fingerprint: String?

    var id: String { "\(createdAt):\(role):\(fingerprint ?? String(content.prefix(12)))" }

    private enum CodingKeys: String, CodingKey {
        case role
        case content
        case createdAt = "created_at"
        case sessionId = "session_id"
        case kind
        case confidence
        case importance
        case fingerprint
    }
}

struct APIMemorySemanticItem: Codable, Identifiable {
    let text: String
    let score: Double
    let vectorScore: Double?
    let recencyScore: Double?
    let metadata: [String: JSONValue]
    let kind: String
    let confidence: Double
    let importance: Double

    var id: String { "\(kind):\(text.prefix(24)):\(score)" }

    private enum CodingKeys: String, CodingKey {
        case text
        case score
        case vectorScore = "vector_score"
        case recencyScore = "recency_score"
        case metadata
        case kind
        case confidence
        case importance
    }
}

struct APIMemoryProfileItem: Codable, Identifiable {
    let key: String
    let value: String
    let updatedAt: String
    let confidence: Double
    let importance: Double
    let source: String?

    var id: String { key }

    private enum CodingKeys: String, CodingKey {
        case key
        case value
        case updatedAt = "updated_at"
        case confidence
        case importance
        case source
    }
}

struct APIMemoryContextPayload: Codable {
    let working: [APIMemoryWorkingItem]
    let episodic: [APIMemoryEpisodicItem]
    let semantic: [APIMemorySemanticItem]
    let profile: [APIMemoryProfileItem]
}

struct APIMemoryContextResponse: Codable {
    let requestId: String
    let userId: String
    let agentId: String?
    let sessionId: String?
    let query: String
    let context: APIMemoryContextPayload

    private enum CodingKeys: String, CodingKey {
        case requestId = "request_id"
        case userId = "user_id"
        case agentId = "agent_id"
        case sessionId = "session_id"
        case query
        case context
    }
}

struct APIMemoryRetrievalItem: Codable, Identifiable {
    let rank: Int
    let semanticId: Int?
    let kind: String
    let text: String
    let score: Double
    let vectorScore: Double
    let recencyScore: Double
    let confidence: Double
    let importance: Double
    let createdAt: String?
    let metadata: [String: JSONValue]

    var id: String { "\(semanticId ?? -1):\(rank)" }

    private enum CodingKeys: String, CodingKey {
        case rank
        case semanticId = "semantic_id"
        case kind
        case text
        case score
        case vectorScore = "vector_score"
        case recencyScore = "recency_score"
        case confidence
        case importance
        case createdAt = "created_at"
        case metadata
    }
}

struct APIMemoryRetrievalResponse: Codable {
    let requestId: String
    let userId: String
    let query: String
    let topK: Int
    let items: [APIMemoryRetrievalItem]

    private enum CodingKeys: String, CodingKey {
        case requestId = "request_id"
        case userId = "user_id"
        case query
        case topK = "top_k"
        case items
    }
}

struct APIMemoryExtractionCandidate: Codable, Identifiable {
    let kind: String
    let text: String
    let key: String?
    let value: String?
    let confidence: Double

    var id: String { "\(kind):\(text.prefix(16)):\(confidence)" }
}

struct APIMemoryExtractionPayload: Codable {
    let facts: [APIMemoryExtractionCandidate]
    let preferences: [APIMemoryExtractionCandidate]
    let tasks: [APIMemoryExtractionCandidate]
}

struct APIMemoryExtractionItem: Codable, Identifiable {
    let userId: String
    let agentId: String?
    let sessionId: String?
    let sourceRole: String
    let sourceText: String
    let extracted: APIMemoryExtractionPayload
    let createdAt: String

    var id: String { "\(createdAt):\(sourceRole):\(sourceText.prefix(12))" }

    private enum CodingKeys: String, CodingKey {
        case userId = "user_id"
        case agentId = "agent_id"
        case sessionId = "session_id"
        case sourceRole = "source_role"
        case sourceText = "source_text"
        case extracted = "extracted_json"
        case createdAt = "created_at"
    }
}

struct APIMemoryExtractionsResponse: Codable {
    let requestId: String
    let userId: String
    let count: Int
    let items: [APIMemoryExtractionItem]

    private enum CodingKeys: String, CodingKey {
        case requestId = "request_id"
        case userId = "user_id"
        case count
        case items
    }
}

struct APIMemoryConflictItem: Codable, Identifiable {
    let layer: String
    let key: String
    let previousValue: String?
    let incomingValue: String?
    let resolution: String
    let confidencePrev: Double?
    let confidenceNew: Double?
    let createdAt: String

    var id: String { "\(createdAt):\(layer):\(key)" }

    private enum CodingKeys: String, CodingKey {
        case layer
        case key
        case previousValue = "previous_value"
        case incomingValue = "incoming_value"
        case resolution
        case confidencePrev = "confidence_prev"
        case confidenceNew = "confidence_new"
        case createdAt = "created_at"
    }
}

struct APIMemoryConflictsResponse: Codable {
    let requestId: String
    let userId: String
    let count: Int
    let items: [APIMemoryConflictItem]

    private enum CodingKeys: String, CodingKey {
        case requestId = "request_id"
        case userId = "user_id"
        case count
        case items
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

extension JSONValue {
    var stringValue: String? {
        switch self {
        case .string(let value):
            return value
        case .number(let value):
            return String(value)
        case .bool(let value):
            return value ? "true" : "false"
        default:
            return nil
        }
    }

    var intValue: Int? {
        switch self {
        case .number(let value):
            return Int(value)
        case .string(let value):
            return Int(value)
        default:
            return nil
        }
    }

    var boolValue: Bool? {
        switch self {
        case .bool(let value):
            return value
        case .string(let value):
            let normalized = value.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
            if ["1", "true", "yes", "on"].contains(normalized) {
                return true
            }
            if ["0", "false", "no", "off"].contains(normalized) {
                return false
            }
            return nil
        default:
            return nil
        }
    }

    var objectValue: [String: JSONValue]? {
        if case .object(let value) = self {
            return value
        }
        return nil
    }

    var arrayValue: [JSONValue]? {
        if case .array(let value) = self {
            return value
        }
        return nil
    }
}
