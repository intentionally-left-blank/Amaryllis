import Foundation
import AppKit
import SwiftUI
import UniformTypeIdentifiers

struct AgentsView: View {
    @EnvironmentObject private var appState: AppState

    @State private var userID: String = "demo-user"
    @State private var sessionID: String = "demo-session"

    @State private var newAgentName: String = "Research Agent"
    @State private var newAgentPrompt: String = "You are a practical AI agent. Use tools when they are needed."
    @State private var newAgentTools: String = "web_search,filesystem"

    @State private var agents: [APIAgentRecord] = []
    @State private var selectedAgentID: String?

    @State private var chatInput: String = ""
    @State private var chatHistory: [String] = []
    @State private var runMaxAttempts: String = "2"
    @State private var runs: [APIAgentRunRecord] = []
    @State private var selectedRunID: String?
    @State private var consumedRunResponses: Set<String> = []
    @State private var runStatusMessage: String = "No run activity yet."
    @State private var selectedRunReplay: APIAgentRunReplayPayload?
    @State private var replayStatusMessage: String = "Replay not loaded."
    @State private var isLoadingReplay: Bool = false
    @State private var selectedRunDiagnostics: APIAgentRunDiagnosticsPayload?
    @State private var diagnosticsStatusMessage: String = "Diagnostics not loaded."
    @State private var isLoadingDiagnostics: Bool = false
    @State private var selectedRunAudit: APIAgentRunAuditPayload?
    @State private var auditStatusMessage: String = "Audit not loaded."
    @State private var isLoadingAudit: Bool = false
    @State private var runWatchSource: String = "idle"
    @State private var runWatchLastEventAt: Date?
    @State private var replaySearchQuery: String = ""
    @State private var replayStageFilter: String = "all"
    @State private var replayAttemptFilter: String = "all"
    @State private var replayPreset: String = "all"
    @State private var replayCompareLeftAttempt: String = "auto"
    @State private var replayCompareRightAttempt: String = "auto"
    @State private var replayTimelineLimit: Int = 120
    @State private var auditSearchQuery: String = ""
    @State private var auditChannelFilter: String = "all"
    @State private var auditTimelineLimit: Int = 120

    private let replayTimelinePageSize: Int = 120
    private let auditTimelinePageSize: Int = 120

    @State private var newAutomationMessage: String = "Check recent updates and summarize key points."
    @State private var newAutomationScheduleType: String = "interval"
    @State private var newAutomationIntervalSec: String = "300"
    @State private var newAutomationIntervalHours: String = "4"
    @State private var newAutomationHour: String = "9"
    @State private var newAutomationMinute: String = "0"
    @State private var newAutomationWeekdays: String = "MO,TU,WE,TH,FR"
    @State private var newAutomationWatchPath: String = ""
    @State private var newAutomationWatchPollSec: String = "10"
    @State private var newAutomationWatchRecursive: Bool = true
    @State private var newAutomationWatchGlob: String = "*"
    @State private var newAutomationWatchMaxChangedFiles: String = "20"
    @State private var newAutomationTimezone: String = TimeZone.current.identifier
    @State private var automationStartImmediately: Bool = false
    @State private var automations: [APIAutomationRecord] = []
    @State private var selectedAutomationID: String?
    @State private var automationEvents: [APIAutomationEvent] = []
    @State private var inboxItems: [APIInboxItem] = []
    @State private var inboxUnreadOnly: Bool = true
    @State private var inboxCategory: String = "automation"

    @State private var isLoadingAgents: Bool = false
    @State private var isCreatingAgent: Bool = false
    @State private var isSending: Bool = false
    @State private var isLoadingRuns: Bool = false
    @State private var isCreatingRun: Bool = false
    @State private var isRunActionLoading: Bool = false
    @State private var isLoadingAutomations: Bool = false
    @State private var isCreatingAutomation: Bool = false
    @State private var isAutomationActionLoading: Bool = false
    @State private var isLoadingInbox: Bool = false
    @State private var isInboxActionLoading: Bool = false
    @State private var inboxRefreshDebounceTask: Task<Void, Never>?

    var body: some View {
        HStack(spacing: 12) {
            leftPanel
                .frame(minWidth: 320, idealWidth: 360, maxWidth: 420)

            rightPanel
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .task {
            await refreshAgents()
            if selectedAgentID == nil {
                await refreshInbox()
            }
        }
        .onChange(of: selectedAgentID ?? "") { _ in
            Task {
                await refreshRuns()
                await refreshAutomations()
                await refreshInbox()
            }
        }
        .onChange(of: selectedRunID ?? "") { _ in
            guard let run = selectedRun else {
                selectedRunReplay = nil
                replayStatusMessage = "Replay not loaded."
                selectedRunDiagnostics = nil
                diagnosticsStatusMessage = "Diagnostics not loaded."
                selectedRunAudit = nil
                auditStatusMessage = "Audit not loaded."
                return
            }
            if selectedRunReplay?.runId != run.id {
                selectedRunReplay = nil
                replayStatusMessage = "Replay not loaded for selected run. Press Load Replay."
                replaySearchQuery = ""
                replayStageFilter = "all"
                replayAttemptFilter = "all"
                replayPreset = "all"
                replayCompareLeftAttempt = "auto"
                replayCompareRightAttempt = "auto"
                replayTimelineLimit = replayTimelinePageSize
            }
            if selectedRunDiagnostics?.runId != run.id {
                selectedRunDiagnostics = nil
                diagnosticsStatusMessage = "Diagnostics not loaded for selected run. Press Load Diagnostics."
            }
            if selectedRunAudit?.runId != run.id {
                selectedRunAudit = nil
                auditStatusMessage = "Audit not loaded for selected run. Press Load Audit."
                auditSearchQuery = ""
                auditChannelFilter = "all"
                auditTimelineLimit = auditTimelinePageSize
            }
        }
        .onChange(of: inboxUnreadOnly) { _ in
            Task { await refreshInbox() }
        }
        .onChange(of: userID) { _ in
            scheduleDebouncedInboxRefresh()
        }
        .onChange(of: replaySearchQuery) { _ in
            replayTimelineLimit = replayTimelinePageSize
        }
        .onChange(of: replayStageFilter) { _ in
            replayTimelineLimit = replayTimelinePageSize
        }
        .onChange(of: replayAttemptFilter) { _ in
            replayTimelineLimit = replayTimelinePageSize
        }
        .onChange(of: replayPreset) { _ in
            replayTimelineLimit = replayTimelinePageSize
        }
        .onChange(of: auditSearchQuery) { _ in
            auditTimelineLimit = auditTimelinePageSize
        }
        .onChange(of: auditChannelFilter) { _ in
            auditTimelineLimit = auditTimelinePageSize
        }
        .onDisappear {
            inboxRefreshDebounceTask?.cancel()
            inboxRefreshDebounceTask = nil
        }
    }

    private var leftPanel: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Agents")
                .font(AmaryllisTheme.titleFont(size: 28))
                .foregroundStyle(AmaryllisTheme.textPrimary)

            VStack(alignment: .leading, spacing: 8) {
                TextField("User ID", text: $userID)
                    .textFieldStyle(AmaryllisTerminalTextFieldStyle())
                TextField("Agent name", text: $newAgentName)
                    .textFieldStyle(AmaryllisTerminalTextFieldStyle())
                TextField("Tools (comma separated)", text: $newAgentTools)
                    .textFieldStyle(AmaryllisTerminalTextFieldStyle())
                TextEditor(text: $newAgentPrompt)
                    .font(AmaryllisTheme.bodyFont(size: 12, weight: .medium))
                    .frame(height: 80)
                    .amaryllisEditorSurface()

                HStack {
                    Button {
                        Task { await createAgent() }
                    } label: {
                        if isCreatingAgent {
                            ProgressView()
                                .controlSize(.small)
                                .frame(width: 90)
                        } else {
                            Text("Create")
                                .frame(width: 90)
                        }
                    }
                    .buttonStyle(AmaryllisPrimaryButtonStyle())
                    .disabled(isCreatingAgent || newAgentName.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)

                    Button("Refresh") {
                        Task {
                            await refreshAgents()
                            await refreshRuns()
                            await refreshAutomations()
                            await refreshInbox()
                        }
                    }
                    .buttonStyle(AmaryllisSecondaryButtonStyle())
                    .disabled(isLoadingAgents)
                }
            }
            .amaryllisCard()

            ScrollView {
                LazyVStack(alignment: .leading, spacing: 8) {
                    ForEach(agents) { agent in
                        Button {
                            selectedAgentID = agent.id
                            chatHistory.removeAll()
                            runs = []
                            selectedRunID = nil
                            selectedRunReplay = nil
                            replayStatusMessage = "Replay not loaded."
                            selectedRunDiagnostics = nil
                            diagnosticsStatusMessage = "Diagnostics not loaded."
                            selectedRunAudit = nil
                            auditStatusMessage = "Audit not loaded."
                            runWatchSource = "idle"
                            runWatchLastEventAt = nil
                            replaySearchQuery = ""
                            replayStageFilter = "all"
                            replayAttemptFilter = "all"
                            replayPreset = "all"
                            replayCompareLeftAttempt = "auto"
                            replayCompareRightAttempt = "auto"
                            replayTimelineLimit = replayTimelinePageSize
                            auditSearchQuery = ""
                            auditChannelFilter = "all"
                            auditTimelineLimit = auditTimelinePageSize
                            runStatusMessage = "Agent switched. Refreshing runs..."
                        } label: {
                            VStack(alignment: .leading, spacing: 4) {
                                Text(agent.name)
                                    .font(AmaryllisTheme.bodyFont(size: 14, weight: .semibold))
                                    .foregroundStyle(AmaryllisTheme.textPrimary)
                                Text(agent.id)
                                    .font(AmaryllisTheme.monoFont(size: 11, weight: .regular))
                                    .foregroundStyle(AmaryllisTheme.textSecondary)
                                    .lineLimit(1)
                            }
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(10)
                            .background(
                                RoundedRectangle(cornerRadius: 4)
                                    .fill(selectedAgentID == agent.id ? AmaryllisTheme.accentSoft : AmaryllisTheme.surface)
                            )
                            .overlay(
                                RoundedRectangle(cornerRadius: 4)
                                    .stroke(selectedAgentID == agent.id ? AmaryllisTheme.accent : AmaryllisTheme.border.opacity(0.4), lineWidth: 1)
                            )
                        }
                        .buttonStyle(.plain)
                    }
                }
            }
            .amaryllisCard()

            if let error = appState.lastError {
                Text(error)
                    .font(AmaryllisTheme.bodyFont(size: 12, weight: .medium))
                    .foregroundStyle(AmaryllisTheme.accent)
            }
        }
    }

    private var rightPanel: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Agent Chat")
                .font(AmaryllisTheme.titleFont(size: 28))
                .foregroundStyle(AmaryllisTheme.textPrimary)

            if let selected = selectedAgent {
                Text("Selected: \(selected.name)")
                    .font(AmaryllisTheme.bodyFont(size: 12, weight: .semibold))
                    .foregroundStyle(AmaryllisTheme.textSecondary)
            } else {
                Text("Select an agent to start")
                    .font(AmaryllisTheme.bodyFont(size: 12, weight: .semibold))
                    .foregroundStyle(AmaryllisTheme.textSecondary)
            }

            ScrollView {
                VStack(alignment: .leading, spacing: 8) {
                    ForEach(chatHistory.indices, id: \.self) { index in
                        Text(chatHistory[index])
                            .font(AmaryllisTheme.bodyFont(size: 13, weight: .medium))
                            .foregroundStyle(AmaryllisTheme.textPrimary)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(10)
                            .background(AmaryllisTheme.surface)
                            .clipShape(RoundedRectangle(cornerRadius: 4))
                    }
                }
            }
            .frame(maxHeight: 220)
            .amaryllisCard()

            HStack(spacing: 8) {
                TextField("Session ID", text: $sessionID)
                    .textFieldStyle(AmaryllisTerminalTextFieldStyle())
                    .frame(width: 160)

                TextField("Attempts", text: $runMaxAttempts)
                    .textFieldStyle(AmaryllisTerminalTextFieldStyle())
                    .frame(width: 90)

                TextField("Message", text: $chatInput)
                    .textFieldStyle(AmaryllisTerminalTextFieldStyle())

                Button {
                    Task { await sendAgentMessage() }
                } label: {
                    if isSending {
                        ProgressView()
                            .controlSize(.small)
                            .frame(width: 90)
                    } else {
                        Text("Send")
                            .frame(width: 90)
                    }
                }
                .buttonStyle(AmaryllisPrimaryButtonStyle())
                .disabled(isSending || selectedAgent == nil || chatInput.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)

                Button {
                    Task { await queueAgentRunFromInput() }
                } label: {
                    if isCreatingRun {
                        ProgressView()
                            .controlSize(.small)
                            .frame(width: 110)
                    } else {
                        Text("Queue Run")
                            .frame(width: 110)
                    }
                }
                .buttonStyle(AmaryllisSecondaryButtonStyle())
                .disabled(isCreatingRun || selectedAgent == nil || chatInput.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)

                Button("Refresh Runs") {
                    Task { await refreshRuns() }
                }
                .buttonStyle(AmaryllisSecondaryButtonStyle())
                .disabled(isLoadingRuns || isRunActionLoading)
            }
            .amaryllisCard()

            runsPanel
                .frame(maxHeight: 340)

            automationPanel
                .frame(maxHeight: 420)

            inboxPanel
                .frame(maxHeight: .infinity)
        }
    }

    private var runsPanel: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Runs (Work Mode)")
                .font(AmaryllisTheme.sectionFont(size: 18))
                .foregroundStyle(AmaryllisTheme.textPrimary)

            if selectedAgent == nil {
                Text("Select an agent to queue and monitor runs.")
                    .font(AmaryllisTheme.bodyFont(size: 12, weight: .medium))
                    .foregroundStyle(AmaryllisTheme.textSecondary)
            } else {
                Text(runStatusMessage)
                    .font(AmaryllisTheme.bodyFont(size: 11, weight: .medium))
                    .foregroundStyle(AmaryllisTheme.textSecondary)
                Text(runWatchMetaLine())
                    .font(AmaryllisTheme.monoFont(size: 10, weight: .regular))
                    .foregroundStyle(AmaryllisTheme.textSecondary)

                Text("Runs: \(runs.count)")
                    .font(AmaryllisTheme.bodyFont(size: 11, weight: .medium))
                    .foregroundStyle(AmaryllisTheme.textSecondary)

                if runs.isEmpty {
                    Text("No runs yet for selected agent.")
                        .font(AmaryllisTheme.bodyFont(size: 12, weight: .medium))
                        .foregroundStyle(AmaryllisTheme.textSecondary)
                } else {
                    ScrollView {
                        LazyVStack(alignment: .leading, spacing: 8) {
                            ForEach(runs) { run in
                                VStack(alignment: .leading, spacing: 6) {
                                    HStack(spacing: 8) {
                                        Circle()
                                            .fill(runStatusColor(run.status))
                                            .frame(width: 8, height: 8)
                                        Text(run.status.uppercased())
                                            .font(AmaryllisTheme.monoFont(size: 10, weight: .regular))
                                            .foregroundStyle(AmaryllisTheme.textSecondary)
                                        Text("attempts \(run.attempts)/\(run.maxAttempts)")
                                            .font(AmaryllisTheme.monoFont(size: 10, weight: .regular))
                                            .foregroundStyle(AmaryllisTheme.textSecondary)
                                        Spacer()
                                        Text(run.createdAt)
                                            .font(AmaryllisTheme.monoFont(size: 10, weight: .regular))
                                            .foregroundStyle(AmaryllisTheme.textSecondary)
                                            .lineLimit(1)
                                    }

                                    Text(run.inputMessage)
                                        .font(AmaryllisTheme.bodyFont(size: 11, weight: .semibold))
                                        .foregroundStyle(AmaryllisTheme.textPrimary)
                                        .lineLimit(2)

                                    if let error = run.errorMessage, !error.isEmpty {
                                        Text("error: \(error)")
                                            .font(AmaryllisTheme.bodyFont(size: 11, weight: .medium))
                                            .foregroundStyle(AmaryllisTheme.accent)
                                            .lineLimit(3)
                                    }

                                    HStack(spacing: 8) {
                                        Button("Open") {
                                            selectedRunID = run.id
                                            runStatusMessage = "Selected run \(run.id)"
                                        }
                                        .buttonStyle(AmaryllisSecondaryButtonStyle())
                                        .disabled(isRunActionLoading)

                                        Button("Replay") {
                                            selectedRunID = run.id
                                            Task { await loadReplay(runID: run.id) }
                                        }
                                        .buttonStyle(AmaryllisSecondaryButtonStyle())
                                        .disabled(isRunActionLoading || isLoadingReplay)

                                        Button("Cancel") {
                                            Task { await cancelRun(id: run.id) }
                                        }
                                        .buttonStyle(AmaryllisSecondaryButtonStyle())
                                        .disabled(isRunActionLoading || !["queued", "running"].contains(run.status))

                                        Button("Resume") {
                                            Task { await resumeRun(id: run.id) }
                                        }
                                        .buttonStyle(AmaryllisSecondaryButtonStyle())
                                        .disabled(isRunActionLoading || !["failed", "canceled"].contains(run.status))
                                    }
                                }
                                .padding(8)
                                .background(AmaryllisTheme.surface)
                                .clipShape(RoundedRectangle(cornerRadius: 4))
                                .onTapGesture {
                                    selectedRunID = run.id
                                    runStatusMessage = "Selected run \(run.id)"
                                }
                            }
                        }
                    }
                    .frame(maxHeight: 150)
                }

                if let run = selectedRun {
                    VStack(alignment: .leading, spacing: 6) {
                        HStack(spacing: 6) {
                            Text("Selected Run")
                                .font(AmaryllisTheme.bodyFont(size: 12, weight: .semibold))
                                .foregroundStyle(AmaryllisTheme.textPrimary)
                            Spacer()
                            Button {
                                Task { await loadReplay(runID: run.id) }
                            } label: {
                                if isLoadingReplay {
                                    ProgressView()
                                        .controlSize(.small)
                                } else {
                                    Text("Load Replay")
                                }
                            }
                            .buttonStyle(AmaryllisSecondaryButtonStyle())
                            .disabled(isLoadingReplay || isRunActionLoading)
                            Button {
                                Task { await loadDiagnostics(runID: run.id) }
                            } label: {
                                if isLoadingDiagnostics {
                                    ProgressView()
                                        .controlSize(.small)
                                } else {
                                    Text("Load Diagnostics")
                                }
                            }
                            .buttonStyle(AmaryllisSecondaryButtonStyle())
                            .disabled(isLoadingDiagnostics || isRunActionLoading)
                            Button {
                                Task { await loadAudit(runID: run.id) }
                            } label: {
                                if isLoadingAudit {
                                    ProgressView()
                                        .controlSize(.small)
                                } else {
                                    Text("Load Audit")
                                }
                            }
                            .buttonStyle(AmaryllisSecondaryButtonStyle())
                            .disabled(isLoadingAudit || isRunActionLoading)
                            Button("Export Package") {
                                Task { await exportDiagnosticPackage(run: run) }
                            }
                            .buttonStyle(AmaryllisSecondaryButtonStyle())
                            .disabled(isLoadingReplay || isLoadingDiagnostics || isLoadingAudit)
                        }
                        HStack(spacing: 8) {
                            Button("Export Audit JSON") {
                                Task { await exportAuditTimeline(run: run, format: "json") }
                            }
                            .buttonStyle(AmaryllisSecondaryButtonStyle())
                            .disabled(isLoadingAudit || isLoadingReplay || isLoadingDiagnostics)

                            Button("Export Audit CSV") {
                                Task { await exportAuditTimeline(run: run, format: "csv") }
                            }
                            .buttonStyle(AmaryllisSecondaryButtonStyle())
                            .disabled(isLoadingAudit || isLoadingReplay || isLoadingDiagnostics)
                            Spacer()
                        }
                        Text("id: \(run.id)")
                            .font(AmaryllisTheme.monoFont(size: 10, weight: .regular))
                            .foregroundStyle(AmaryllisTheme.textSecondary)
                            .lineLimit(1)
                        Text("status: \(run.status) | started: \(run.startedAt ?? "-") | finished: \(run.finishedAt ?? "-")")
                            .font(AmaryllisTheme.monoFont(size: 10, weight: .regular))
                            .foregroundStyle(AmaryllisTheme.textSecondary)
                            .lineLimit(1)
                        if let response = runResponseText(from: run), !response.isEmpty {
                            Text("response: \(response)")
                                .font(AmaryllisTheme.bodyFont(size: 11, weight: .medium))
                                .foregroundStyle(AmaryllisTheme.textPrimary)
                                .lineLimit(3)
                        }
                        Text(replayStatusMessage)
                            .font(AmaryllisTheme.bodyFont(size: 11, weight: .medium))
                            .foregroundStyle(AmaryllisTheme.textSecondary)
                        Text(diagnosticsStatusMessage)
                            .font(AmaryllisTheme.bodyFont(size: 11, weight: .medium))
                            .foregroundStyle(AmaryllisTheme.textSecondary)
                        Text(auditStatusMessage)
                            .font(AmaryllisTheme.bodyFont(size: 11, weight: .medium))
                            .foregroundStyle(AmaryllisTheme.textSecondary)

                        if let diagnostics = selectedRunDiagnostics, diagnostics.runId == run.id {
                            Text("Mission Diagnostics")
                                .font(AmaryllisTheme.bodyFont(size: 11, weight: .semibold))
                                .foregroundStyle(AmaryllisTheme.textSecondary)
                            Text(
                                "status: \(diagnostics.status) | failure: \(diagnostics.failureClass) | stop: \(diagnostics.stopReason)"
                            )
                            .font(AmaryllisTheme.monoFont(size: 10, weight: .regular))
                            .foregroundStyle(AmaryllisTheme.textSecondary)
                            .lineLimit(1)
                            Text(
                                "attempts: \(diagnostics.attempts)/\(diagnostics.maxAttempts) | checkpoints: \(diagnostics.timelineSummary.checkpointCount)"
                            )
                            .font(AmaryllisTheme.monoFont(size: 10, weight: .regular))
                            .foregroundStyle(AmaryllisTheme.textSecondary)
                            .lineLimit(1)
                            if !diagnostics.timelineSummary.stageBreakdown.isEmpty {
                                Text("stages: \(renderStageCounts(diagnostics.timelineSummary.stageBreakdown))")
                                    .font(AmaryllisTheme.monoFont(size: 10, weight: .regular))
                                    .foregroundStyle(AmaryllisTheme.textSecondary)
                                    .lineLimit(2)
                            }

                            Text("Warnings")
                                .font(AmaryllisTheme.bodyFont(size: 11, weight: .semibold))
                                .foregroundStyle(AmaryllisTheme.textSecondary)
                            if diagnostics.diagnostics.warnings.isEmpty {
                                Text("No warning signals.")
                                    .font(AmaryllisTheme.bodyFont(size: 10, weight: .medium))
                                    .foregroundStyle(Color.green)
                            } else {
                                ScrollView(.horizontal) {
                                    HStack(spacing: 6) {
                                        ForEach(diagnostics.diagnostics.warnings, id: \.self) { warning in
                                            Text(diagnosticsWarningLabel(warning))
                                                .font(AmaryllisTheme.monoFont(size: 10, weight: .regular))
                                                .foregroundStyle(diagnosticsWarningColor(warning))
                                                .padding(.horizontal, 8)
                                                .padding(.vertical, 4)
                                                .background(
                                                    RoundedRectangle(cornerRadius: 4)
                                                        .fill(diagnosticsWarningColor(warning).opacity(0.12))
                                                )
                                                .overlay(
                                                    RoundedRectangle(cornerRadius: 4)
                                                        .stroke(diagnosticsWarningColor(warning).opacity(0.45), lineWidth: 1)
                                                )
                                        }
                                    }
                                }
                                .frame(maxHeight: 32)
                            }

                            Text("Signals")
                                .font(AmaryllisTheme.bodyFont(size: 11, weight: .semibold))
                                .foregroundStyle(AmaryllisTheme.textSecondary)
                            ScrollView(.horizontal) {
                                HStack(spacing: 8) {
                                    diagnosticsSignalBadge(
                                        "blocked issues",
                                        diagnostics.diagnostics.signals.blockedIssues,
                                        tint: diagnostics.diagnostics.signals.blockedIssues > 0 ? AmaryllisTheme.accent : .green
                                    )
                                    diagnosticsSignalBadge(
                                        "tool calls",
                                        diagnostics.diagnostics.signals.toolCallsTotal,
                                        tint: .blue
                                    )
                                    diagnosticsSignalBadge(
                                        "tool failures",
                                        diagnostics.diagnostics.signals.toolCallFailures,
                                        tint: diagnostics.diagnostics.signals.toolCallFailures > 0 ? AmaryllisTheme.accent : .green
                                    )
                                    diagnosticsSignalBadge(
                                        "retry count",
                                        diagnostics.diagnostics.signals.retryCount,
                                        tint: diagnostics.diagnostics.signals.retryCount > 0 ? .orange : .green
                                    )
                                }
                            }
                            .frame(maxHeight: 56)

                            Text("Recommended Actions")
                                .font(AmaryllisTheme.bodyFont(size: 11, weight: .semibold))
                                .foregroundStyle(AmaryllisTheme.textSecondary)
                            if diagnostics.diagnostics.recommendedActions.isEmpty {
                                Text("No explicit corrective action provided.")
                                    .font(AmaryllisTheme.bodyFont(size: 10, weight: .medium))
                                    .foregroundStyle(AmaryllisTheme.textSecondary)
                            } else {
                                VStack(alignment: .leading, spacing: 3) {
                                    ForEach(diagnostics.diagnostics.recommendedActions, id: \.self) { action in
                                        Text("• \(action)")
                                            .font(AmaryllisTheme.bodyFont(size: 10, weight: .medium))
                                            .foregroundStyle(AmaryllisTheme.textPrimary)
                                            .frame(maxWidth: .infinity, alignment: .leading)
                                            .lineLimit(3)
                                    }
                                }
                                .padding(6)
                                .background(AmaryllisTheme.surfaceAlt)
                                .clipShape(RoundedRectangle(cornerRadius: 4))
                            }

                            HStack(spacing: 8) {
                                Text("Quick Replay:")
                                    .font(AmaryllisTheme.bodyFont(size: 10, weight: .semibold))
                                    .foregroundStyle(AmaryllisTheme.textSecondary)
                                Button("Errors") {
                                    Task { await loadReplayPreset(runID: run.id, preset: "errors") }
                                }
                                .buttonStyle(AmaryllisSecondaryButtonStyle())
                                .disabled(isLoadingReplay || isRunActionLoading)
                                Button("Tools") {
                                    Task { await loadReplayPreset(runID: run.id, preset: "tools") }
                                }
                                .buttonStyle(AmaryllisSecondaryButtonStyle())
                                .disabled(isLoadingReplay || isRunActionLoading)
                                Button("Verify") {
                                    Task { await loadReplayPreset(runID: run.id, preset: "verify") }
                                }
                                .buttonStyle(AmaryllisSecondaryButtonStyle())
                                .disabled(isLoadingReplay || isRunActionLoading)
                                Button("Full") {
                                    Task { await loadReplay(runID: run.id) }
                                }
                                .buttonStyle(AmaryllisSecondaryButtonStyle())
                                .disabled(isLoadingReplay || isRunActionLoading)
                                Spacer()
                            }
                        }

                        if let audit = selectedRunAudit, audit.runId == run.id {
                            let filteredAudit = filteredAuditTimeline(audit)
                            let visibleAudit = visibleAuditTimeline(filteredAudit)
                            Text("Mission Audit (\(audit.eventCount) events)")
                                .font(AmaryllisTheme.bodyFont(size: 11, weight: .semibold))
                                .foregroundStyle(AmaryllisTheme.textSecondary)
                            Text("channels: \(renderAuditChannelCounts(audit.summary.channelCounts))")
                                .font(AmaryllisTheme.monoFont(size: 10, weight: .regular))
                                .foregroundStyle(AmaryllisTheme.textSecondary)
                                .lineLimit(2)
                            if !audit.summary.statusCounts.isEmpty {
                                Text("statuses: \(renderAuditChannelCounts(audit.summary.statusCounts))")
                                    .font(AmaryllisTheme.monoFont(size: 10, weight: .regular))
                                    .foregroundStyle(AmaryllisTheme.textSecondary)
                                    .lineLimit(2)
                            }

                            HStack(spacing: 8) {
                                TextField("Search channel/action/message", text: $auditSearchQuery)
                                    .textFieldStyle(AmaryllisTerminalTextFieldStyle())
                                    .frame(minWidth: 180, maxWidth: .infinity)
                                Picker("Channel", selection: $auditChannelFilter) {
                                    Text("all").tag("all")
                                    ForEach(availableAuditChannels(audit), id: \.self) { channel in
                                        Text(channel).tag(channel)
                                    }
                                }
                                .pickerStyle(.menu)
                                .frame(width: 150)
                            }
                            HStack(spacing: 8) {
                                Text("Showing \(visibleAudit.count)/\(filteredAudit.count) events")
                                    .font(AmaryllisTheme.monoFont(size: 10, weight: .regular))
                                    .foregroundStyle(AmaryllisTheme.textSecondary)
                                Spacer()
                                if filteredAudit.count > visibleAudit.count {
                                    Button("Older +\(auditTimelinePageSize)") {
                                        auditTimelineLimit += auditTimelinePageSize
                                    }
                                    .buttonStyle(AmaryllisSecondaryButtonStyle())
                                }
                                if auditTimelineLimit != auditTimelinePageSize {
                                    Button("Latest") {
                                        auditTimelineLimit = auditTimelinePageSize
                                    }
                                    .buttonStyle(AmaryllisSecondaryButtonStyle())
                                }
                                if !filteredAudit.isEmpty, auditTimelineLimit < filteredAudit.count {
                                    Button("All") {
                                        auditTimelineLimit = filteredAudit.count
                                    }
                                    .buttonStyle(AmaryllisSecondaryButtonStyle())
                                }
                            }
                            ScrollView {
                                LazyVStack(alignment: .leading, spacing: 4) {
                                    ForEach(visibleAudit) { item in
                                        HStack(spacing: 6) {
                                            Circle()
                                                .fill(auditChannelColor(item.channel))
                                                .frame(width: 6, height: 6)
                                            Text(renderAuditEventLine(item))
                                                .font(AmaryllisTheme.monoFont(size: 10, weight: .regular))
                                                .foregroundStyle(AmaryllisTheme.textSecondary)
                                                .frame(maxWidth: .infinity, alignment: .leading)
                                                .lineLimit(2)
                                        }
                                    }
                                }
                            }
                            .frame(maxHeight: 120)
                        }

                        if let replay = selectedRunReplay, replay.runId == run.id {
                            let filteredTimeline = filteredReplayTimeline(replay)
                            let visibleTimeline = visibleReplayTimeline(filteredTimeline)
                            let attemptPair = selectedReplayAttemptPair(replay)
                            Text("Replay Summary (\(replay.checkpointCount) events)")
                                .font(AmaryllisTheme.bodyFont(size: 11, weight: .semibold))
                                .foregroundStyle(AmaryllisTheme.textSecondary)

                            if replay.attemptSummary.isEmpty {
                                Text("No attempt summary yet.")
                                    .font(AmaryllisTheme.bodyFont(size: 11, weight: .medium))
                                    .foregroundStyle(AmaryllisTheme.textSecondary)
                            } else {
                                ScrollView(.horizontal) {
                                    HStack(spacing: 8) {
                                        ForEach(replay.attemptSummary) { item in
                                            VStack(alignment: .leading, spacing: 4) {
                                                Text("attempt \(item.attempt)")
                                                    .font(AmaryllisTheme.monoFont(size: 10, weight: .regular))
                                                    .foregroundStyle(AmaryllisTheme.textPrimary)
                                                Text(renderStageCounts(item.stageCounts))
                                                    .font(AmaryllisTheme.monoFont(size: 10, weight: .regular))
                                                    .foregroundStyle(AmaryllisTheme.textSecondary)
                                                    .lineLimit(2)
                                                Text("tools \(item.toolRounds) | repairs \(item.verificationRepairs)")
                                                    .font(AmaryllisTheme.monoFont(size: 10, weight: .regular))
                                                    .foregroundStyle(AmaryllisTheme.textSecondary)
                                                    .lineLimit(1)
                                                if let firstError = item.errors.first, !firstError.isEmpty {
                                                    Text("error: \(firstError)")
                                                        .font(AmaryllisTheme.bodyFont(size: 10, weight: .medium))
                                                        .foregroundStyle(AmaryllisTheme.accent)
                                                        .lineLimit(2)
                                                }
                                            }
                                            .padding(6)
                                            .background(AmaryllisTheme.surfaceAlt)
                                            .clipShape(RoundedRectangle(cornerRadius: 4))
                                        }
                                    }
                                }
                                .frame(maxHeight: 86)
                            }

                            Text("Attempt Diff")
                                .font(AmaryllisTheme.bodyFont(size: 11, weight: .semibold))
                                .foregroundStyle(AmaryllisTheme.textSecondary)
                            HStack(spacing: 8) {
                                Picker("Left", selection: $replayCompareLeftAttempt) {
                                    Text("auto").tag("auto")
                                    ForEach(replay.attemptSummary.map(\.attempt).sorted(), id: \.self) { attempt in
                                        Text("a\(attempt)").tag(String(attempt))
                                    }
                                }
                                .pickerStyle(.menu)
                                .frame(width: 110)
                                Picker("Right", selection: $replayCompareRightAttempt) {
                                    Text("auto").tag("auto")
                                    ForEach(replay.attemptSummary.map(\.attempt).sorted(), id: \.self) { attempt in
                                        Text("a\(attempt)").tag(String(attempt))
                                    }
                                }
                                .pickerStyle(.menu)
                                .frame(width: 110)
                                Button("Auto Pair") {
                                    replayCompareLeftAttempt = "auto"
                                    replayCompareRightAttempt = "auto"
                                }
                                .buttonStyle(AmaryllisSecondaryButtonStyle())
                                Spacer()
                            }
                            if let pair = attemptPair {
                                VStack(alignment: .leading, spacing: 4) {
                                    Text("a\(pair.left.attempt) vs a\(pair.right.attempt)")
                                        .font(AmaryllisTheme.monoFont(size: 10, weight: .regular))
                                        .foregroundStyle(AmaryllisTheme.textPrimary)
                                    ForEach(renderAttemptComparisonLines(left: pair.left, right: pair.right), id: \.self) { line in
                                        Text(line)
                                            .font(AmaryllisTheme.monoFont(size: 10, weight: .regular))
                                            .foregroundStyle(AmaryllisTheme.textSecondary)
                                            .lineLimit(2)
                                    }
                                }
                                .padding(6)
                                .background(AmaryllisTheme.surfaceAlt)
                                .clipShape(RoundedRectangle(cornerRadius: 4))
                            } else {
                                Text("Need at least two distinct attempts for comparison.")
                                    .font(AmaryllisTheme.bodyFont(size: 10, weight: .medium))
                                    .foregroundStyle(AmaryllisTheme.textSecondary)
                            }

                            Text("Replay Timeline (\(replay.timeline.count))")
                                .font(AmaryllisTheme.bodyFont(size: 11, weight: .semibold))
                                .foregroundStyle(AmaryllisTheme.textSecondary)
                            HStack(spacing: 8) {
                                TextField("Search stage/message", text: $replaySearchQuery)
                                    .textFieldStyle(AmaryllisTerminalTextFieldStyle())
                                    .frame(minWidth: 180, maxWidth: .infinity)
                                Picker("Stage", selection: $replayStageFilter) {
                                    Text("all").tag("all")
                                    ForEach(availableReplayStages(replay), id: \.self) { stage in
                                        Text(stage).tag(stage)
                                    }
                                }
                                .pickerStyle(.menu)
                                .frame(width: 130)
                                Picker("Attempt", selection: $replayAttemptFilter) {
                                    Text("all").tag("all")
                                    ForEach(availableReplayAttempts(replay), id: \.self) { attempt in
                                        Text(String(attempt)).tag(String(attempt))
                                    }
                                }
                                .pickerStyle(.menu)
                                .frame(width: 110)
                                Picker("Preset", selection: $replayPreset) {
                                    Text("all").tag("all")
                                    Text("errors").tag("errors_only")
                                    Text("tools").tag("tool_calls")
                                    Text("verify").tag("verification_only")
                                }
                                .pickerStyle(.menu)
                                .frame(width: 110)
                            }
                            HStack(spacing: 8) {
                                Text("Showing \(visibleTimeline.count)/\(filteredTimeline.count) events")
                                    .font(AmaryllisTheme.monoFont(size: 10, weight: .regular))
                                    .foregroundStyle(AmaryllisTheme.textSecondary)
                                Spacer()
                                if filteredTimeline.count > visibleTimeline.count {
                                    Button("Older +\(replayTimelinePageSize)") {
                                        replayTimelineLimit += replayTimelinePageSize
                                    }
                                    .buttonStyle(AmaryllisSecondaryButtonStyle())
                                }
                                if replayTimelineLimit != replayTimelinePageSize {
                                    Button("Latest") {
                                        replayTimelineLimit = replayTimelinePageSize
                                    }
                                    .buttonStyle(AmaryllisSecondaryButtonStyle())
                                }
                                if !filteredTimeline.isEmpty, replayTimelineLimit < filteredTimeline.count {
                                    Button("All") {
                                        replayTimelineLimit = filteredTimeline.count
                                    }
                                    .buttonStyle(AmaryllisSecondaryButtonStyle())
                                }
                            }
                            ScrollView {
                                LazyVStack(alignment: .leading, spacing: 4) {
                                    ForEach(visibleTimeline) { event in
                                        HStack(spacing: 6) {
                                            Circle()
                                                .fill(replayStageColor(event.stage))
                                                .frame(width: 6, height: 6)
                                            Text(
                                                "[\(event.timestamp)] a\(renderReplayAttempt(event.attempt)) \(event.stage) \(event.message)"
                                            )
                                                .font(AmaryllisTheme.monoFont(size: 10, weight: .regular))
                                                .foregroundStyle(AmaryllisTheme.textSecondary)
                                                .frame(maxWidth: .infinity, alignment: .leading)
                                                .lineLimit(2)
                                        }
                                    }
                                }
                            }
                            .frame(maxHeight: 120)
                        }

                        Text("Raw Checkpoints (\(rawCheckpointCount(for: run)))")
                            .font(AmaryllisTheme.bodyFont(size: 11, weight: .semibold))
                            .foregroundStyle(AmaryllisTheme.textSecondary)
                        ScrollView {
                            LazyVStack(alignment: .leading, spacing: 4) {
                                ForEach(Array(rawCheckpointPreviewLines(for: run).enumerated()), id: \.offset) { _, line in
                                    Text(line)
                                        .font(AmaryllisTheme.monoFont(size: 10, weight: .regular))
                                        .foregroundStyle(AmaryllisTheme.textSecondary)
                                        .frame(maxWidth: .infinity, alignment: .leading)
                                }
                            }
                        }
                        .frame(maxHeight: 120)
                    }
                    .padding(8)
                    .background(AmaryllisTheme.surface)
                    .clipShape(RoundedRectangle(cornerRadius: 4))
                }
            }
        }
        .amaryllisCard()
    }

    private var automationPanel: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Automation")
                .font(AmaryllisTheme.sectionFont(size: 18))
                .foregroundStyle(AmaryllisTheme.textPrimary)

            if selectedAgent == nil {
                Text("Select an agent to configure scheduled runs.")
                    .font(AmaryllisTheme.bodyFont(size: 12, weight: .medium))
                    .foregroundStyle(AmaryllisTheme.textSecondary)
            } else {
                VStack(alignment: .leading, spacing: 8) {
                    HStack(spacing: 8) {
                        TextField("Scheduled message", text: $newAutomationMessage)
                            .textFieldStyle(AmaryllisTerminalTextFieldStyle())

                        TextField("Timezone", text: $newAutomationTimezone)
                            .textFieldStyle(AmaryllisTerminalTextFieldStyle())
                            .frame(width: 180)
                    }

                    HStack(spacing: 8) {
                        Picker("Schedule", selection: $newAutomationScheduleType) {
                            Text("interval").tag("interval")
                            Text("hourly").tag("hourly")
                            Text("weekly").tag("weekly")
                            Text("watch_fs").tag("watch_fs")
                        }
                        .pickerStyle(.menu)
                        .frame(width: 140)

                        if newAutomationScheduleType == "interval" {
                            TextField("Interval (sec)", text: $newAutomationIntervalSec)
                                .textFieldStyle(AmaryllisTerminalTextFieldStyle())
                                .frame(width: 130)
                        } else if newAutomationScheduleType == "hourly" {
                            TextField("Every N hours", text: $newAutomationIntervalHours)
                                .textFieldStyle(AmaryllisTerminalTextFieldStyle())
                                .frame(width: 130)
                            TextField("Minute", text: $newAutomationMinute)
                                .textFieldStyle(AmaryllisTerminalTextFieldStyle())
                                .frame(width: 90)
                        } else if newAutomationScheduleType == "weekly" {
                            TextField("Weekdays (MO,TU,...)", text: $newAutomationWeekdays)
                                .textFieldStyle(AmaryllisTerminalTextFieldStyle())
                                .frame(minWidth: 170, maxWidth: 240)
                            TextField("Hour", text: $newAutomationHour)
                                .textFieldStyle(AmaryllisTerminalTextFieldStyle())
                                .frame(width: 80)
                            TextField("Minute", text: $newAutomationMinute)
                                .textFieldStyle(AmaryllisTerminalTextFieldStyle())
                                .frame(width: 80)
                        } else {
                            TextField("Watch path", text: $newAutomationWatchPath)
                                .textFieldStyle(AmaryllisTerminalTextFieldStyle())
                                .frame(minWidth: 220, maxWidth: 320)
                            TextField("Poll sec", text: $newAutomationWatchPollSec)
                                .textFieldStyle(AmaryllisTerminalTextFieldStyle())
                                .frame(width: 90)
                            TextField("Glob", text: $newAutomationWatchGlob)
                                .textFieldStyle(AmaryllisTerminalTextFieldStyle())
                                .frame(width: 110)
                            TextField("Max files", text: $newAutomationWatchMaxChangedFiles)
                                .textFieldStyle(AmaryllisTerminalTextFieldStyle())
                                .frame(width: 90)
                            Toggle("Recursive", isOn: $newAutomationWatchRecursive)
                                .toggleStyle(.switch)
                                .font(AmaryllisTheme.bodyFont(size: 11, weight: .semibold))
                                .frame(width: 110)
                        }

                        Toggle("Run now", isOn: $automationStartImmediately)
                            .toggleStyle(.switch)
                            .font(AmaryllisTheme.bodyFont(size: 11, weight: .semibold))
                            .frame(width: 120)

                        Button {
                            Task { await createAutomation() }
                        } label: {
                            if isCreatingAutomation {
                                ProgressView()
                                    .controlSize(.small)
                                    .frame(width: 90)
                            } else {
                                Text("Create")
                                    .frame(width: 90)
                            }
                        }
                        .buttonStyle(AmaryllisPrimaryButtonStyle())
                        .disabled(
                            isCreatingAutomation
                            || newAutomationMessage.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                            || (newAutomationScheduleType == "watch_fs"
                                && newAutomationWatchPath.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                        )

                        Button("Apply") {
                            Task { await applyAutomationScheduleUpdate() }
                        }
                        .buttonStyle(AmaryllisSecondaryButtonStyle())
                        .disabled(isAutomationActionLoading || selectedAutomationID == nil)

                        Button("Refresh") {
                            Task { await refreshAutomations() }
                        }
                        .buttonStyle(AmaryllisSecondaryButtonStyle())
                        .disabled(isLoadingAutomations || isAutomationActionLoading)
                    }
                }

                Text("Automations: \(automations.count)")
                    .font(AmaryllisTheme.bodyFont(size: 11, weight: .medium))
                    .foregroundStyle(AmaryllisTheme.textSecondary)

                if automations.isEmpty {
                    Text("No automations for this agent yet.")
                        .font(AmaryllisTheme.bodyFont(size: 12, weight: .medium))
                        .foregroundStyle(AmaryllisTheme.textSecondary)
                } else {
                    ScrollView {
                        LazyVStack(alignment: .leading, spacing: 8) {
                            ForEach(automations) { automation in
                                VStack(alignment: .leading, spacing: 6) {
                                    HStack(spacing: 8) {
                                        Circle()
                                            .fill(automation.isEnabled ? Color.green : AmaryllisTheme.accent)
                                            .frame(width: 8, height: 8)
                                        Text(automation.message)
                                            .font(AmaryllisTheme.bodyFont(size: 12, weight: .semibold))
                                            .foregroundStyle(AmaryllisTheme.textPrimary)
                                            .lineLimit(2)
                                        Spacer()
                                        Text(scheduleSummary(for: automation))
                                            .font(AmaryllisTheme.monoFont(size: 10, weight: .regular))
                                            .foregroundStyle(AmaryllisTheme.textSecondary)
                                    }

                                    Text("next: \(automation.nextRunAt) | tz: \(automation.timezone)")
                                        .font(AmaryllisTheme.monoFont(size: 10, weight: .regular))
                                        .foregroundStyle(AmaryllisTheme.textSecondary)

                                    Text(
                                        "failures: \(automation.consecutiveFailures) | escalation: \(automation.escalationLevel)"
                                    )
                                    .font(AmaryllisTheme.monoFont(size: 10, weight: .regular))
                                    .foregroundStyle(escalationColor(level: automation.escalationLevel))

                                    if let error = automation.lastError, !error.isEmpty {
                                        Text("last_error: \(error)")
                                            .font(AmaryllisTheme.bodyFont(size: 11, weight: .medium))
                                            .foregroundStyle(AmaryllisTheme.accent)
                                    }

                                    HStack(spacing: 8) {
                                        Button(automation.isEnabled ? "Pause" : "Resume") {
                                            Task {
                                                if automation.isEnabled {
                                                    await pauseAutomation(id: automation.id)
                                                } else {
                                                    await resumeAutomation(id: automation.id)
                                                }
                                            }
                                        }
                                        .buttonStyle(AmaryllisSecondaryButtonStyle())
                                        .disabled(isAutomationActionLoading)

                                        Button("Run now") {
                                            Task { await runAutomationNow(id: automation.id) }
                                        }
                                        .buttonStyle(AmaryllisSecondaryButtonStyle())
                                        .disabled(isAutomationActionLoading)

                                        Button("Events") {
                                            Task {
                                                selectedAutomationID = automation.id
                                                await loadAutomationEvents(automationID: automation.id)
                                            }
                                        }
                                        .buttonStyle(AmaryllisSecondaryButtonStyle())
                                        .disabled(isAutomationActionLoading)

                                        Button("Delete") {
                                            Task { await deleteAutomation(id: automation.id) }
                                        }
                                        .buttonStyle(AmaryllisSecondaryButtonStyle())
                                        .disabled(isAutomationActionLoading)
                                    }
                                }
                                .padding(8)
                                .background(AmaryllisTheme.surface)
                                .clipShape(RoundedRectangle(cornerRadius: 4))
                                .onTapGesture {
                                    selectedAutomationID = automation.id
                                    applyAutomationToForm(automation)
                                }
                            }
                        }
                    }
                    .frame(maxHeight: 180)
                }

                VStack(alignment: .leading, spacing: 6) {
                    Text("Automation Events")
                        .font(AmaryllisTheme.bodyFont(size: 12, weight: .semibold))
                        .foregroundStyle(AmaryllisTheme.textPrimary)

                    if automationEvents.isEmpty {
                        Text("No events yet.")
                            .font(AmaryllisTheme.bodyFont(size: 11, weight: .medium))
                            .foregroundStyle(AmaryllisTheme.textSecondary)
                    } else {
                        ScrollView {
                            LazyVStack(alignment: .leading, spacing: 6) {
                                ForEach(automationEvents) { event in
                                    VStack(alignment: .leading, spacing: 2) {
                                        Text("[\(event.eventType)] \(event.message)")
                                            .font(AmaryllisTheme.bodyFont(size: 11, weight: .medium))
                                            .foregroundStyle(AmaryllisTheme.textPrimary)
                                        Text("\(event.createdAt)\(event.runId.map { " run_id=\($0)" } ?? "")")
                                            .font(AmaryllisTheme.monoFont(size: 10, weight: .regular))
                                            .foregroundStyle(AmaryllisTheme.textSecondary)
                                    }
                                }
                            }
                        }
                        .frame(maxHeight: 140)
                        .padding(8)
                        .background(AmaryllisTheme.surface)
                        .clipShape(RoundedRectangle(cornerRadius: 4))
                    }
                }
            }
        }
        .amaryllisCard()
    }

    private var inboxPanel: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("Inbox")
                    .font(AmaryllisTheme.sectionFont(size: 18))
                    .foregroundStyle(AmaryllisTheme.textPrimary)
                Spacer()
                Text("items: \(inboxItems.count)")
                    .font(AmaryllisTheme.bodyFont(size: 11, weight: .medium))
                    .foregroundStyle(AmaryllisTheme.textSecondary)
            }

            HStack(spacing: 8) {
                Toggle("Unread only", isOn: $inboxUnreadOnly)
                    .toggleStyle(.switch)
                    .font(AmaryllisTheme.bodyFont(size: 11, weight: .semibold))
                    .frame(width: 130)

                TextField("Category", text: $inboxCategory)
                    .textFieldStyle(AmaryllisTerminalTextFieldStyle())
                    .frame(width: 160)

                Button("Refresh") {
                    Task { await refreshInbox() }
                }
                .buttonStyle(AmaryllisSecondaryButtonStyle())
                .disabled(isLoadingInbox || isInboxActionLoading)
            }

            if inboxItems.isEmpty {
                Text("Inbox is empty.")
                    .font(AmaryllisTheme.bodyFont(size: 12, weight: .medium))
                    .foregroundStyle(AmaryllisTheme.textSecondary)
            } else {
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 8) {
                        ForEach(inboxItems) { item in
                            VStack(alignment: .leading, spacing: 6) {
                                HStack(spacing: 8) {
                                    Circle()
                                        .fill(inboxSeverityColor(item.severity))
                                        .frame(width: 8, height: 8)
                                    Text(item.title)
                                        .font(AmaryllisTheme.bodyFont(size: 12, weight: .semibold))
                                        .foregroundStyle(AmaryllisTheme.textPrimary)
                                        .lineLimit(2)
                                    Spacer()
                                    Text(item.isRead ? "READ" : "UNREAD")
                                        .font(AmaryllisTheme.monoFont(size: 10, weight: .regular))
                                        .foregroundStyle(item.isRead ? AmaryllisTheme.textSecondary : AmaryllisTheme.accent)
                                }

                                Text(item.body)
                                    .font(AmaryllisTheme.bodyFont(size: 11, weight: .medium))
                                    .foregroundStyle(AmaryllisTheme.textPrimary)
                                    .lineLimit(4)

                                HStack(spacing: 8) {
                                    Text(item.createdAt)
                                        .font(AmaryllisTheme.monoFont(size: 10, weight: .regular))
                                        .foregroundStyle(AmaryllisTheme.textSecondary)
                                        .lineLimit(1)
                                    if let sourceId = item.sourceId {
                                        Text("source: \(sourceId)")
                                            .font(AmaryllisTheme.monoFont(size: 10, weight: .regular))
                                            .foregroundStyle(AmaryllisTheme.textSecondary)
                                            .lineLimit(1)
                                    }
                                }

                                HStack(spacing: 8) {
                                    Button(item.isRead ? "Mark unread" : "Mark read") {
                                        Task {
                                            await setInboxItemRead(itemID: item.id, isRead: !item.isRead)
                                        }
                                    }
                                    .buttonStyle(AmaryllisSecondaryButtonStyle())
                                    .disabled(isInboxActionLoading)
                                }
                            }
                            .padding(8)
                            .background(AmaryllisTheme.surface)
                            .clipShape(RoundedRectangle(cornerRadius: 4))
                        }
                    }
                }
                .frame(maxHeight: 220)
            }
        }
        .amaryllisCard()
    }

    private var selectedAgent: APIAgentRecord? {
        guard let id = selectedAgentID else { return nil }
        return agents.first(where: { $0.id == id })
    }

    private var selectedRun: APIAgentRunRecord? {
        guard let id = selectedRunID else { return nil }
        return runs.first(where: { $0.id == id })
    }

    private func createAgent() async {
        let trimmedName = newAgentName.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmedName.isEmpty else { return }

        isCreatingAgent = true
        defer { isCreatingAgent = false }

        do {
            let tools = newAgentTools
                .split(separator: ",")
                .map { String($0).trimmingCharacters(in: .whitespacesAndNewlines) }
                .filter { !$0.isEmpty }

            let model = appState.modelCatalog?.active.model
            let agent = try await appState.apiClient.createAgent(
                name: trimmedName,
                systemPrompt: newAgentPrompt,
                model: model,
                tools: tools,
                userId: userID
            )

            selectedAgentID = agent.id
            await refreshAgents()
            await refreshRuns()
            await refreshAutomations()
            await refreshInbox()
            appState.clearError()
        } catch {
            appState.lastError = error.localizedDescription
        }
    }

    private func refreshAgents() async {
        isLoadingAgents = true
        defer { isLoadingAgents = false }

        do {
            let response = try await appState.apiClient.listAgents(userId: userID)
            agents = response.items
            if selectedAgentID == nil || !agents.contains(where: { $0.id == selectedAgentID }) {
                selectedAgentID = agents.first?.id
            }
            appState.clearError()
        } catch {
            appState.lastError = error.localizedDescription
        }
    }

    private func sendAgentMessage() async {
        guard let agent = selectedAgent else { return }
        let text = chatInput.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }

        isSending = true
        defer { isSending = false }

        chatInput = ""
        chatHistory.append("USER: \(text)")

        do {
            let response = try await appState.apiClient.chatAgent(
                agentId: agent.id,
                userId: userID,
                message: text,
                sessionId: sessionID.isEmpty ? nil : sessionID
            )
            chatHistory.append("AGENT (\(response.strategy)): \(response.response)")
            appState.clearError()
        } catch {
            chatHistory.append("ERROR: \(error.localizedDescription)")
            appState.lastError = error.localizedDescription
        }
    }

    private func queueAgentRunFromInput() async {
        guard let agent = selectedAgent else { return }
        let text = chatInput.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }

        isCreatingRun = true
        defer { isCreatingRun = false }

        chatInput = ""
        chatHistory.append("USER: \(text)")
        runStatusMessage = "Queuing run..."

        do {
            let maxAttempts = clampInt(runMaxAttempts, fallback: 2, min: 1, max: 10)
            let run = try await appState.apiClient.createAgentRun(
                agentId: agent.id,
                userId: userID,
                message: text,
                sessionId: sessionID.isEmpty ? nil : sessionID,
                maxAttempts: maxAttempts
            )
            upsertRun(run)
            selectedRunID = run.id
            runStatusMessage = "Run queued: \(run.id)"
            await refreshRuns()
            await watchRunUntilTerminal(runID: run.id, timeoutSec: 120)
            appState.clearError()
        } catch {
            chatHistory.append("RUN ERROR: \(error.localizedDescription)")
            runStatusMessage = "Run failed to queue."
            appState.lastError = error.localizedDescription
        }
    }

    private func refreshRuns() async {
        guard let agent = selectedAgent else {
            runs = []
            selectedRunID = nil
            selectedRunReplay = nil
            runStatusMessage = "No agent selected."
            replayStatusMessage = "Replay not loaded."
            selectedRunDiagnostics = nil
            diagnosticsStatusMessage = "Diagnostics not loaded."
            selectedRunAudit = nil
            auditStatusMessage = "Audit not loaded."
            runWatchSource = "idle"
            runWatchLastEventAt = nil
            replaySearchQuery = ""
            replayStageFilter = "all"
            replayAttemptFilter = "all"
            replayPreset = "all"
            replayCompareLeftAttempt = "auto"
            replayCompareRightAttempt = "auto"
            replayTimelineLimit = replayTimelinePageSize
            auditSearchQuery = ""
            auditChannelFilter = "all"
            auditTimelineLimit = auditTimelinePageSize
            return
        }

        isLoadingRuns = true
        defer { isLoadingRuns = false }

        do {
            let response = try await appState.apiClient.listAgentRuns(
                agentId: agent.id,
                userId: userID,
                status: nil,
                limit: 30,
                includeResult: false,
                includeCheckpoints: false
            )
            runs = response.items
            if selectedRunID == nil || !runs.contains(where: { $0.id == selectedRunID }) {
                selectedRunID = runs.first?.id
            }
            if runs.isEmpty {
                runStatusMessage = "No runs yet."
            } else if let selected = selectedRun {
                runStatusMessage = "Selected run status: \(selected.status)"
            } else {
                runStatusMessage = "Loaded \(runs.count) runs."
            }
            appState.clearError()
        } catch {
            appState.lastError = error.localizedDescription
        }
    }

    private func loadReplay(runID: String, silent: Bool = false) async {
        if silent,
           let existing = selectedRunReplay,
           existing.runId == runID,
           !existing.timeline.isEmpty {
            return
        }

        isLoadingReplay = true
        defer { isLoadingReplay = false }

        do {
            let replay = try await appState.apiClient.getAgentRunReplay(runId: runID)
            selectedRunReplay = replay
            replaySearchQuery = ""
            replayStageFilter = "all"
            replayAttemptFilter = "all"
            replayPreset = "all"
            replayCompareLeftAttempt = "auto"
            replayCompareRightAttempt = "auto"
            replayTimelineLimit = replayTimelinePageSize
            replayStatusMessage =
                "Replay loaded: \(replay.checkpointCount) events, \(replay.attemptSummary.count) attempts."
            appState.clearError()
        } catch {
            selectedRunReplay = nil
            replayStatusMessage = "Replay load failed."
            if !silent {
                appState.lastError = error.localizedDescription
            }
        }
    }

    private func loadReplayPreset(runID: String, preset: String) async {
        isLoadingReplay = true
        defer { isLoadingReplay = false }

        do {
            let replay = try await appState.apiClient.getAgentRunReplayFiltered(
                runId: runID,
                preset: preset,
                timelineLimit: 240
            )
            selectedRunReplay = replay
            replaySearchQuery = ""
            replayStageFilter = "all"
            replayAttemptFilter = "all"
            replayPreset = localReplayPreset(for: preset)
            replayCompareLeftAttempt = "auto"
            replayCompareRightAttempt = "auto"
            replayTimelineLimit = replayTimelinePageSize
            replayStatusMessage =
                "Replay preset loaded (\(preset)): \(replay.timeline.count) events from server filter."
            appState.clearError()
        } catch {
            replayStatusMessage = "Replay preset load failed."
            appState.lastError = error.localizedDescription
        }
    }

    private func loadDiagnostics(runID: String, silent: Bool = false) async {
        isLoadingDiagnostics = true
        defer { isLoadingDiagnostics = false }

        do {
            let diagnostics = try await appState.apiClient.getAgentRunDiagnostics(runId: runID)
            selectedRunDiagnostics = diagnostics
            diagnosticsStatusMessage =
                "Diagnostics loaded: \(diagnostics.diagnostics.warnings.count) warnings, \(diagnostics.diagnostics.recommendedActions.count) actions."
            appState.clearError()
        } catch {
            selectedRunDiagnostics = nil
            diagnosticsStatusMessage = "Diagnostics load failed."
            if !silent {
                appState.lastError = error.localizedDescription
            }
        }
    }

    private func loadAudit(runID: String, silent: Bool = false) async {
        if silent,
           let existing = selectedRunAudit,
           existing.runId == runID,
           !existing.timeline.isEmpty {
            return
        }

        isLoadingAudit = true
        defer { isLoadingAudit = false }

        do {
            let audit = try await appState.apiClient.getAgentRunAudit(
                runId: runID,
                includeToolCalls: true,
                includeSecurityActions: true,
                limit: 5_000
            )
            selectedRunAudit = audit
            auditSearchQuery = ""
            auditChannelFilter = "all"
            auditTimelineLimit = auditTimelinePageSize
            auditStatusMessage = "Audit loaded: \(audit.eventCount) events."
            appState.clearError()
        } catch {
            selectedRunAudit = nil
            auditStatusMessage = "Audit load failed."
            if !silent {
                appState.lastError = error.localizedDescription
            }
        }
    }

    private func watchRunUntilTerminal(runID: String, timeoutSec: Double) async {
        runWatchSource = "live"
        runWatchLastEventAt = Date()
        runStatusMessage = "Run \(runID) live stream started..."
        do {
            try await streamRunUntilTerminal(runID: runID, timeoutSec: timeoutSec)
        } catch {
            runWatchSource = "fallback"
            runWatchLastEventAt = Date()
            runStatusMessage = "Run stream interrupted, switching to polling fallback..."
            await pollRunUntilTerminal(runID: runID, timeoutSec: timeoutSec)
        }
    }

    private func streamRunUntilTerminal(runID: String, timeoutSec: Double) async throws {
        let startedAt = Date()
        var fromIndex = 0

        while true {
            let elapsedSec = Date().timeIntervalSince(startedAt)
            let remainingSec = timeoutSec - elapsedSec
            if remainingSec <= 0 {
                runStatusMessage = "Run watch timeout reached. Use Refresh Runs."
                return
            }

            let streamTimeoutSec = min(30.0, max(2.0, remainingSec))
            let stream = appState.apiClient.streamAgentRunEvents(
                runId: runID,
                fromIndex: fromIndex,
                pollIntervalMs: 250,
                timeoutSec: streamTimeoutSec,
                includeSnapshot: true,
                includeHeartbeat: false
            )

            var reachedTerminal = false
            for try await event in stream {
                if let nextIndex = event.nextIndex {
                    fromIndex = max(fromIndex, nextIndex)
                }
                if let index = event.index {
                    fromIndex = max(fromIndex, index)
                }
                let done = try await handleRunStreamEvent(event, runID: runID)
                if done {
                    reachedTerminal = true
                    break
                }
            }

            if reachedTerminal {
                return
            }
        }
    }

    private func handleRunStreamEvent(_ event: APIAgentRunStreamEvent, runID: String) async throws -> Bool {
        let eventType = event.event.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        let status = event.status?.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() ?? "unknown"

        switch eventType {
        case "snapshot":
            let checkpoints = event.checkpointCount ?? event.nextIndex ?? 0
            markRunWatchEvent(source: "live")
            runStatusMessage = "Run \(runID) status: \(status) | checkpoints \(checkpoints)"
            _ = await fetchRunForWatch(runID: runID, silent: true, source: "live")
            return false
        case "checkpoint":
            let index = event.index ?? event.nextIndex ?? event.checkpointCount ?? 0
            markRunWatchEvent(source: "live")
            runStatusMessage = "Run \(runID) status: \(status) | event \(index)"
            if index > 0, index % 8 == 0 {
                _ = await fetchRunForWatch(runID: runID, silent: true, source: "live")
            }
            return false
        case "heartbeat":
            markRunWatchEvent(source: "live")
            runStatusMessage = "Run \(runID) status: \(status)"
            return false
        case "timeout":
            markRunWatchEvent(source: "live")
            runStatusMessage = "Run stream window elapsed, reconnecting..."
            return false
        case "done":
            markRunWatchEvent(source: "live")
            if let run = await fetchRunForWatch(runID: runID, silent: true) {
                await handleTerminalRun(run)
            } else {
                runStatusMessage = "Run \(runID) reached terminal status: \(status)"
                await loadReplay(runID: runID, silent: true)
                await loadDiagnostics(runID: runID, silent: true)
            }
            return true
        case "error":
            let message = event.message?.trimmingCharacters(in: .whitespacesAndNewlines)
            let text = (message?.isEmpty == false) ? message! : "Run stream error."
            throw NSError(
                domain: "amaryllis.run.stream",
                code: 1,
                userInfo: [NSLocalizedDescriptionKey: text]
            )
        default:
            return false
        }
    }

    private func fetchRunForWatch(runID: String, silent: Bool, source: String? = nil) async -> APIAgentRunRecord? {
        do {
            let run = try await appState.apiClient.getAgentRun(runId: runID)
            upsertRun(run)
            selectedRunID = run.id
            if let source {
                markRunWatchEvent(source: source)
            }
            return run
        } catch {
            if !silent {
                appState.lastError = error.localizedDescription
            }
            return nil
        }
    }

    private func pollRunUntilTerminal(runID: String, timeoutSec: Double) async {
        let terminalStates: Set<String> = ["succeeded", "failed", "canceled"]
        let maxTicks = max(1, Int(timeoutSec / 1.2))

        for _ in 0..<maxTicks {
            guard let run = await fetchRunForWatch(runID: runID, silent: false, source: "fallback") else {
                return
            }

            let status = run.status.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
            runWatchSource = "fallback"
            runStatusMessage = "Run \(run.id) status: \(status)"
            if terminalStates.contains(status) {
                await handleTerminalRun(run)
                return
            }

            do {
                try await Task.sleep(nanoseconds: 1_200_000_000)
            } catch {
                return
            }
        }

        runStatusMessage = "Run watch timeout reached. Use Refresh Runs."
    }

    private func handleTerminalRun(_ run: APIAgentRunRecord) async {
        let status = run.status.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        if status == "succeeded",
           !consumedRunResponses.contains(run.id),
           let response = runResponseText(from: run),
           !response.isEmpty {
            chatHistory.append("AGENT (run): \(response)")
            consumedRunResponses.insert(run.id)
        } else if status == "failed", let error = run.errorMessage, !error.isEmpty {
            chatHistory.append("RUN FAILED: \(error)")
        } else if status == "canceled" {
            chatHistory.append("RUN CANCELED")
        }

        runWatchSource = "terminal"
        runWatchLastEventAt = Date()
        runStatusMessage = "Run \(run.id) status: \(status)"
        await loadReplay(runID: run.id, silent: true)
        await loadDiagnostics(runID: run.id, silent: true)
        await loadAudit(runID: run.id, silent: true)
    }

    private func cancelRun(id: String) async {
        isRunActionLoading = true
        defer { isRunActionLoading = false }

        do {
            let run = try await appState.apiClient.cancelAgentRun(runId: id)
            upsertRun(run)
            selectedRunID = id
            runStatusMessage = "Run canceled: \(id)"
            await refreshRuns()
            appState.clearError()
        } catch {
            appState.lastError = error.localizedDescription
        }
    }

    private func resumeRun(id: String) async {
        isRunActionLoading = true
        defer { isRunActionLoading = false }

        do {
            let run = try await appState.apiClient.resumeAgentRun(runId: id)
            upsertRun(run)
            selectedRunID = id
            runStatusMessage = "Run resumed: \(id)"
            await refreshRuns()
            await watchRunUntilTerminal(runID: id, timeoutSec: 120)
            appState.clearError()
        } catch {
            appState.lastError = error.localizedDescription
        }
    }

    private func upsertRun(_ run: APIAgentRunRecord) {
        if let index = runs.firstIndex(where: { $0.id == run.id }) {
            runs[index] = run
        } else {
            runs.insert(run, at: 0)
        }
        runs.sort { $0.createdAt > $1.createdAt }
    }

    private func runResponseText(from run: APIAgentRunRecord) -> String? {
        guard let result = run.result else { return nil }
        if let direct = result["response"]?.stringValue {
            return direct
        }
        if let nested = result["result"]?.objectValue,
           let nestedResponse = nested["response"]?.stringValue {
            return nestedResponse
        }
        return nil
    }

    private func rawCheckpointCount(for run: APIAgentRunRecord) -> Int {
        if let replay = selectedRunReplay, replay.runId == run.id {
            return replay.checkpointCount
        }
        return run.checkpoints.count
    }

    private func rawCheckpointPreviewLines(for run: APIAgentRunRecord) -> [String] {
        if let replay = selectedRunReplay, replay.runId == run.id {
            let timeline = replay.timeline.suffix(20)
            return timeline.map { item in
                "[\(item.timestamp)] \(item.stage) \(item.message)"
            }
        }
        return run.checkpoints.suffix(20).map { checkpoint in
            let stage = checkpoint.stage ?? "-"
            let message = checkpoint.message ?? "-"
            return "[\(checkpoint.timestamp)] \(stage) \(message)"
        }
    }

    private func runStatusColor(_ status: String) -> Color {
        switch status {
        case "succeeded":
            return .green
        case "failed":
            return AmaryllisTheme.accent
        case "running":
            return .yellow
        case "queued":
            return .blue
        case "canceled":
            return .gray
        default:
            return AmaryllisTheme.textSecondary
        }
    }

    private func localReplayPreset(for serverPreset: String) -> String {
        switch serverPreset {
        case "errors":
            return "errors_only"
        case "tools":
            return "tool_calls"
        case "verify":
            return "verification_only"
        default:
            return "all"
        }
    }

    private func markRunWatchEvent(source: String) {
        runWatchSource = source
        runWatchLastEventAt = Date()
    }

    private func runWatchMetaLine() -> String {
        let sourceLabel: String
        switch runWatchSource {
        case "live":
            sourceLabel = "LIVE"
        case "fallback":
            sourceLabel = "FALLBACK"
        case "terminal":
            sourceLabel = "TERMINAL"
        default:
            sourceLabel = "IDLE"
        }
        let lastEvent = runWatchLastEventAt.map { runWatchTimeString($0) } ?? "-"
        return "watch: \(sourceLabel) | last event: \(lastEvent)"
    }

    private func runWatchTimeString(_ date: Date) -> String {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = TimeZone.current
        formatter.dateFormat = "HH:mm:ss"
        return formatter.string(from: date)
    }

    private func diagnosticsWarningLabel(_ warning: String) -> String {
        warning.replacingOccurrences(of: "_", with: " ")
    }

    private func diagnosticsWarningColor(_ warning: String) -> Color {
        switch warning {
        case "run_terminal_non_success", "budget_exceeded", "issues_blocked", "tool_failures_detected":
            return AmaryllisTheme.accent
        case "transient_infra_failures", "max_attempts_exhausted", "run_required_retries":
            return .orange
        default:
            return AmaryllisTheme.textSecondary
        }
    }

    private func diagnosticsSignalBadge(_ label: String, _ value: Int, tint: Color) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label)
                .font(AmaryllisTheme.monoFont(size: 10, weight: .regular))
                .foregroundStyle(AmaryllisTheme.textSecondary)
            Text(String(value))
                .font(AmaryllisTheme.bodyFont(size: 12, weight: .semibold))
                .foregroundStyle(tint)
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 5)
        .background(AmaryllisTheme.surfaceAlt)
        .clipShape(RoundedRectangle(cornerRadius: 4))
    }

    private func scheduleDebouncedInboxRefresh() {
        inboxRefreshDebounceTask?.cancel()
        inboxRefreshDebounceTask = Task {
            do {
                try await Task.sleep(nanoseconds: 400_000_000)
            } catch {
                return
            }
            if Task.isCancelled {
                return
            }
            await refreshInbox()
        }
    }

    private func replayStageColor(_ stage: String) -> Color {
        switch stage {
        case "succeeded", "verification_passed", "verification_repair_succeeded":
            return .green
        case "failed", "error", "tool_call_failed", "verification_warning":
            return AmaryllisTheme.accent
        case "running", "reasoning_started", "llm_response":
            return .yellow
        case "queued", "resumed", "retry_scheduled":
            return .blue
        case "canceled", "cancel_requested":
            return .gray
        default:
            return AmaryllisTheme.textSecondary
        }
    }

    private func renderStageCounts(_ stageCounts: [String: Int]) -> String {
        if stageCounts.isEmpty {
            return "no stages"
        }
        let parts = stageCounts
            .sorted { lhs, rhs in
                if lhs.value == rhs.value {
                    return lhs.key < rhs.key
                }
                return lhs.value > rhs.value
            }
            .prefix(3)
            .map { "\($0.key)=\($0.value)" }
        return parts.joined(separator: " | ")
    }

    private func auditChannelColor(_ channel: String) -> Color {
        switch channel {
        case "run_checkpoint":
            return .blue
        case "tool_call":
            return .orange
        case "security_audit":
            return .green
        default:
            return AmaryllisTheme.textSecondary
        }
    }

    private func renderAuditChannelCounts(_ counts: [String: Int]) -> String {
        if counts.isEmpty {
            return "none"
        }
        return counts
            .sorted { lhs, rhs in
                if lhs.value == rhs.value {
                    return lhs.key < rhs.key
                }
                return lhs.value > rhs.value
            }
            .prefix(4)
            .map { "\($0.key)=\($0.value)" }
            .joined(separator: " | ")
    }

    private func availableAuditChannels(_ audit: APIAgentRunAuditPayload) -> [String] {
        Array(Set(audit.timeline.map { $0.channel })).sorted()
    }

    private func filteredAuditTimeline(_ audit: APIAgentRunAuditPayload) -> [APIAgentRunAuditTimelineItem] {
        let query = auditSearchQuery.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        return audit.timeline.filter { item in
            if auditChannelFilter != "all", item.channel != auditChannelFilter {
                return false
            }
            if query.isEmpty {
                return true
            }
            let attemptText = item.attempt.map(String.init) ?? ""
            let haystack = "\(item.timestamp) \(item.channel) \(item.stage) \(item.action) \(item.status) \(attemptText) \(item.actor) \(item.message)".lowercased()
            return haystack.contains(query)
        }
    }

    private func visibleAuditTimeline(_ timeline: [APIAgentRunAuditTimelineItem]) -> [APIAgentRunAuditTimelineItem] {
        let safeLimit = max(auditTimelinePageSize, auditTimelineLimit)
        if timeline.count <= safeLimit {
            return timeline
        }
        return Array(timeline.suffix(safeLimit))
    }

    private func renderAuditEventLine(_ item: APIAgentRunAuditTimelineItem) -> String {
        var parts: [String] = ["[\(item.timestamp)]", item.channel, item.action]
        if !item.status.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            parts.append("status=\(item.status)")
        }
        if let attempt = item.attempt {
            parts.append("a\(attempt)")
        }
        if !item.actor.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            parts.append("actor=\(item.actor)")
        }
        if !item.message.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            parts.append(item.message)
        }
        return parts.joined(separator: " ")
    }

    private func availableReplayStages(_ replay: APIAgentRunReplayPayload) -> [String] {
        Array(Set(replay.timeline.map { $0.stage })).sorted()
    }

    private func availableReplayAttempts(_ replay: APIAgentRunReplayPayload) -> [Int] {
        Array(Set(replay.timeline.compactMap { $0.attempt })).sorted()
    }

    private func selectedReplayAttemptPair(
        _ replay: APIAgentRunReplayPayload
    ) -> (left: APIAgentRunReplayAttemptSummary, right: APIAgentRunReplayAttemptSummary)? {
        let sorted = replay.attemptSummary.sorted { $0.attempt < $1.attempt }
        guard sorted.count >= 2 else { return nil }

        let autoLeft = sorted[sorted.count - 2].attempt
        let autoRight = sorted[sorted.count - 1].attempt
        let leftAttempt = replayCompareLeftAttempt == "auto" ? autoLeft : Int(replayCompareLeftAttempt)
        let rightAttempt = replayCompareRightAttempt == "auto" ? autoRight : Int(replayCompareRightAttempt)
        guard let leftAttempt, let rightAttempt, leftAttempt != rightAttempt else {
            return nil
        }

        guard
            let left = sorted.first(where: { $0.attempt == leftAttempt }),
            let right = sorted.first(where: { $0.attempt == rightAttempt })
        else {
            return nil
        }
        return (left: left, right: right)
    }

    private func visibleReplayTimeline(_ timeline: [APIAgentRunReplayTimelineItem]) -> [APIAgentRunReplayTimelineItem] {
        let safeLimit = max(replayTimelinePageSize, replayTimelineLimit)
        if timeline.count <= safeLimit {
            return timeline
        }
        return Array(timeline.suffix(safeLimit))
    }

    private func filteredReplayTimeline(_ replay: APIAgentRunReplayPayload) -> [APIAgentRunReplayTimelineItem] {
        let query = replaySearchQuery.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        return replay.timeline.filter { event in
            if !replayPresetAllowsStage(event.stage) {
                return false
            }
            if replayStageFilter != "all", event.stage != replayStageFilter {
                return false
            }
            if replayAttemptFilter != "all" {
                guard let attempt = event.attempt, String(attempt) == replayAttemptFilter else {
                    return false
                }
            }
            if query.isEmpty {
                return true
            }
            let attemptText = event.attempt.map(String.init) ?? ""
            let haystack = "\(event.timestamp) \(event.stage) \(attemptText) \(event.message)".lowercased()
            return haystack.contains(query)
        }
    }

    private func replayPresetAllowsStage(_ stage: String) -> Bool {
        switch replayPreset {
        case "errors_only":
            return [
                "error",
                "failed",
                "tool_call_failed",
                "tool_call_invalid",
                "tool_call_blocked",
                "verification_warning",
            ].contains(stage)
        case "tool_calls":
            return stage.hasPrefix("tool_call_")
        case "verification_only":
            return stage.hasPrefix("verification_")
        default:
            return true
        }
    }

    private func renderAttemptComparisonLines(
        left: APIAgentRunReplayAttemptSummary,
        right: APIAgentRunReplayAttemptSummary
    ) -> [String] {
        var lines: [String] = []
        lines.append("tools: \(left.toolRounds) -> \(right.toolRounds) (delta \(signedDelta(right.toolRounds - left.toolRounds)))")
        lines.append(
            "repairs: \(left.verificationRepairs) -> \(right.verificationRepairs) (delta \(signedDelta(right.verificationRepairs - left.verificationRepairs)))"
        )
        lines.append("errors: \(left.errors.count) -> \(right.errors.count)")

        let stageKeys = Set(left.stageCounts.keys).union(Set(right.stageCounts.keys))
        let stageDeltas = stageKeys
            .sorted()
            .compactMap { key -> String? in
                let oldValue = left.stageCounts[key, default: 0]
                let newValue = right.stageCounts[key, default: 0]
                let delta = newValue - oldValue
                if delta == 0 {
                    return nil
                }
                return "\(key): \(oldValue)->\(newValue) (\(signedDelta(delta)))"
            }
        if stageDeltas.isEmpty {
            lines.append("stage delta: no changes")
        } else {
            lines.append("stage delta: " + stageDeltas.prefix(3).joined(separator: " | "))
        }
        return lines
    }

    private func signedDelta(_ value: Int) -> String {
        if value > 0 {
            return "+\(value)"
        }
        return "\(value)"
    }

    private func renderReplayAttempt(_ attempt: Int?) -> String {
        guard let attempt else { return "-" }
        return String(attempt)
    }

    @MainActor
    private func exportDiagnosticPackage(run: APIAgentRunRecord) async {
        isLoadingReplay = true
        defer { isLoadingReplay = false }

        do {
            let replay: APIAgentRunReplayPayload
            if let cached = selectedRunReplay, cached.runId == run.id {
                replay = cached
            } else {
                replay = try await appState.apiClient.getAgentRunReplay(runId: run.id)
                selectedRunReplay = replay
            }

            let filtered = filteredReplayTimeline(replay)
            let visible = visibleReplayTimeline(filtered)
            let payload = RunDiagnosticExport(
                schemaVersion: "agent_run_diagnostic_v1",
                exportedAt: ISO8601DateFormatter().string(from: Date()),
                app: "Amaryllis",
                run: run,
                replay: replay,
                replayFilters: ReplayFilterSnapshot(
                    search: replaySearchQuery,
                    stage: replayStageFilter,
                    attempt: replayAttemptFilter,
                    preset: replayPreset,
                    compareLeftAttempt: replayCompareLeftAttempt,
                    compareRightAttempt: replayCompareRightAttempt,
                    timelineLimit: replayTimelineLimit,
                    filteredCount: filtered.count,
                    visibleCount: visible.count
                )
            )

            let encoder = JSONEncoder()
            encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
            let data = try encoder.encode(payload)

            let panel = NSSavePanel()
            panel.title = "Export Run Diagnostics"
            panel.nameFieldStringValue = "amaryllis-run-\(run.id)-diagnostics.json"
            panel.allowedContentTypes = [UTType.json]
            panel.canCreateDirectories = true
            panel.isExtensionHidden = false

            let result = panel.runModal()
            guard result == .OK, let destination = panel.url else {
                replayStatusMessage = "Diagnostics export canceled."
                return
            }

            try data.write(to: destination, options: .atomic)
            replayStatusMessage = "Diagnostics exported: \(destination.lastPathComponent)"
            appState.clearError()
        } catch {
            replayStatusMessage = "Diagnostics export failed."
            appState.lastError = error.localizedDescription
        }
    }

    @MainActor
    private func exportAuditTimeline(run: APIAgentRunRecord, format: String) async {
        isLoadingAudit = true
        defer { isLoadingAudit = false }

        let normalized = format.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        do {
            let fileName: String
            let data: Data
            let panelTitle: String
            let allowedContentTypes: [UTType]

            if normalized == "json" {
                let response = try await appState.apiClient.exportAgentRunAuditJSON(
                    runId: run.id,
                    includeToolCalls: true,
                    includeSecurityActions: true,
                    limit: 5_000
                )
                selectedRunAudit = response.audit
                let encoder = JSONEncoder()
                encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
                data = try encoder.encode(response.audit)
                fileName = response.export.filename
                panelTitle = "Export Mission Audit (JSON)"
                allowedContentTypes = [UTType.json]
            } else {
                let exported = try await appState.apiClient.exportAgentRunAuditCSV(
                    runId: run.id,
                    includeToolCalls: true,
                    includeSecurityActions: true,
                    limit: 5_000
                )
                data = Data(exported.content.utf8)
                fileName = exported.filename
                panelTitle = "Export Mission Audit (CSV)"
                allowedContentTypes = [UTType.commaSeparatedText, UTType.plainText]
            }

            let panel = NSSavePanel()
            panel.title = panelTitle
            panel.nameFieldStringValue = fileName
            panel.allowedContentTypes = allowedContentTypes
            panel.canCreateDirectories = true
            panel.isExtensionHidden = false

            let result = panel.runModal()
            guard result == .OK, let destination = panel.url else {
                auditStatusMessage = "Audit export canceled."
                return
            }

            try data.write(to: destination, options: .atomic)
            auditStatusMessage = "Audit exported: \(destination.lastPathComponent)"
            appState.clearError()
        } catch {
            auditStatusMessage = "Audit export failed."
            appState.lastError = error.localizedDescription
        }
    }

    private struct ReplayFilterSnapshot: Encodable {
        let search: String
        let stage: String
        let attempt: String
        let preset: String
        let compareLeftAttempt: String
        let compareRightAttempt: String
        let timelineLimit: Int
        let filteredCount: Int
        let visibleCount: Int

        enum CodingKeys: String, CodingKey {
            case search
            case stage
            case attempt
            case preset
            case compareLeftAttempt = "compare_left_attempt"
            case compareRightAttempt = "compare_right_attempt"
            case timelineLimit = "timeline_limit"
            case filteredCount = "filtered_count"
            case visibleCount = "visible_count"
        }
    }

    private struct RunDiagnosticExport: Encodable {
        let schemaVersion: String
        let exportedAt: String
        let app: String
        let run: APIAgentRunRecord
        let replay: APIAgentRunReplayPayload
        let replayFilters: ReplayFilterSnapshot

        enum CodingKeys: String, CodingKey {
            case schemaVersion = "schema_version"
            case exportedAt = "exported_at"
            case app
            case run
            case replay
            case replayFilters = "replay_filters"
        }
    }

    private func createAutomation() async {
        guard let agent = selectedAgent else { return }
        let message = newAutomationMessage.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !message.isEmpty else { return }

        isCreatingAutomation = true
        defer { isCreatingAutomation = false }

        do {
            let schedulePayload = buildSchedulePayload()
            let fallbackInterval = clampInt(newAutomationIntervalSec, fallback: 300, min: 10, max: 86_400)
            _ = try await appState.apiClient.createAutomation(
                agentId: agent.id,
                userId: userID,
                message: message,
                sessionId: sessionID.isEmpty ? nil : sessionID,
                intervalSec: fallbackInterval,
                scheduleType: newAutomationScheduleType,
                schedule: schedulePayload,
                timezone: newAutomationTimezone.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                    ? TimeZone.current.identifier
                    : newAutomationTimezone.trimmingCharacters(in: .whitespacesAndNewlines),
                startImmediately: automationStartImmediately
            )
            await refreshAutomations()
            await refreshInbox()
            appState.clearError()
        } catch {
            appState.lastError = error.localizedDescription
        }
    }

    private func refreshAutomations() async {
        guard let agent = selectedAgent else {
            automations = []
            selectedAutomationID = nil
            automationEvents = []
            return
        }

        isLoadingAutomations = true
        defer { isLoadingAutomations = false }

        do {
            let response = try await appState.apiClient.listAutomations(
                userId: userID,
                agentId: agent.id,
                enabled: nil,
                limit: 200
            )
            automations = response.items
            if selectedAutomationID == nil || !automations.contains(where: { $0.id == selectedAutomationID }) {
                selectedAutomationID = automations.first?.id
            }
            if let selectedAutomationID {
                await loadAutomationEvents(automationID: selectedAutomationID)
            } else {
                automationEvents = []
            }
            appState.clearError()
        } catch {
            appState.lastError = error.localizedDescription
        }
    }

    private func loadAutomationEvents(automationID: String) async {
        do {
            let response = try await appState.apiClient.listAutomationEvents(automationId: automationID, limit: 100)
            automationEvents = response.items
        } catch {
            appState.lastError = error.localizedDescription
        }
    }

    private func refreshInbox() async {
        isLoadingInbox = true
        defer { isLoadingInbox = false }

        do {
            let category = inboxCategory.trimmingCharacters(in: .whitespacesAndNewlines)
            let response = try await appState.apiClient.listInbox(
                userId: userID.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? nil : userID,
                unreadOnly: inboxUnreadOnly,
                category: category.isEmpty ? nil : category,
                limit: 200
            )
            inboxItems = response.items
            appState.clearError()
        } catch {
            appState.lastError = error.localizedDescription
        }
    }

    private func setInboxItemRead(itemID: String, isRead: Bool) async {
        isInboxActionLoading = true
        defer { isInboxActionLoading = false }

        do {
            let updated = try await appState.apiClient.setInboxItemRead(itemId: itemID, isRead: isRead)
            if let index = inboxItems.firstIndex(where: { $0.id == updated.id }) {
                inboxItems[index] = updated
            }
            if inboxUnreadOnly && isRead {
                inboxItems.removeAll { $0.id == itemID }
            }
            appState.clearError()
        } catch {
            appState.lastError = error.localizedDescription
        }
    }

    private func pauseAutomation(id: String) async {
        isAutomationActionLoading = true
        defer { isAutomationActionLoading = false }
        do {
            _ = try await appState.apiClient.pauseAutomation(automationId: id)
            selectedAutomationID = id
            await refreshAutomations()
            await refreshInbox()
            appState.clearError()
        } catch {
            appState.lastError = error.localizedDescription
        }
    }

    private func resumeAutomation(id: String) async {
        isAutomationActionLoading = true
        defer { isAutomationActionLoading = false }
        do {
            _ = try await appState.apiClient.resumeAutomation(automationId: id)
            selectedAutomationID = id
            await refreshAutomations()
            await refreshInbox()
            appState.clearError()
        } catch {
            appState.lastError = error.localizedDescription
        }
    }

    private func runAutomationNow(id: String) async {
        isAutomationActionLoading = true
        defer { isAutomationActionLoading = false }
        do {
            _ = try await appState.apiClient.runAutomationNow(automationId: id)
            selectedAutomationID = id
            await refreshAutomations()
            await refreshInbox()
            appState.clearError()
        } catch {
            appState.lastError = error.localizedDescription
        }
    }

    private func deleteAutomation(id: String) async {
        isAutomationActionLoading = true
        defer { isAutomationActionLoading = false }
        do {
            _ = try await appState.apiClient.deleteAutomation(automationId: id)
            if selectedAutomationID == id {
                selectedAutomationID = nil
            }
            await refreshAutomations()
            await refreshInbox()
            appState.clearError()
        } catch {
            appState.lastError = error.localizedDescription
        }
    }

    private func buildSchedulePayload() -> [String: JSONValue] {
        switch newAutomationScheduleType {
        case "watch_fs":
            let pollSec = clampInt(newAutomationWatchPollSec, fallback: 10, min: 2, max: 3_600)
            let maxChangedFiles = clampInt(newAutomationWatchMaxChangedFiles, fallback: 20, min: 1, max: 500)
            let path = newAutomationWatchPath.trimmingCharacters(in: .whitespacesAndNewlines)
            let glob = newAutomationWatchGlob.trimmingCharacters(in: .whitespacesAndNewlines)
            return [
                "path": .string(path),
                "poll_sec": .number(Double(pollSec)),
                "recursive": .bool(newAutomationWatchRecursive),
                "glob": .string(glob.isEmpty ? "*" : glob),
                "max_changed_files": .number(Double(maxChangedFiles)),
            ]
        case "hourly":
            let intervalHours = clampInt(newAutomationIntervalHours, fallback: 1, min: 1, max: 24)
            let minute = clampInt(newAutomationMinute, fallback: 0, min: 0, max: 59)
            return [
                "interval_hours": .number(Double(intervalHours)),
                "minute": .number(Double(minute)),
            ]
        case "weekly":
            let hour = clampInt(newAutomationHour, fallback: 9, min: 0, max: 23)
            let minute = clampInt(newAutomationMinute, fallback: 0, min: 0, max: 59)
            return [
                "byday": .array(parseWeekdaysInput(newAutomationWeekdays).map { .string($0) }),
                "hour": .number(Double(hour)),
                "minute": .number(Double(minute)),
            ]
        default:
            let interval = clampInt(newAutomationIntervalSec, fallback: 300, min: 10, max: 86_400)
            return ["interval_sec": .number(Double(interval))]
        }
    }

    private func applyAutomationScheduleUpdate() async {
        guard let automationID = selectedAutomationID else { return }
        isAutomationActionLoading = true
        defer { isAutomationActionLoading = false }

        do {
            let message = newAutomationMessage.trimmingCharacters(in: .whitespacesAndNewlines)
            let timezone = newAutomationTimezone.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                ? TimeZone.current.identifier
                : newAutomationTimezone.trimmingCharacters(in: .whitespacesAndNewlines)
            let intervalOverride: Int? = newAutomationScheduleType == "interval"
                ? clampInt(newAutomationIntervalSec, fallback: 300, min: 10, max: 86_400)
                : nil

            _ = try await appState.apiClient.updateAutomation(
                automationId: automationID,
                message: message.isEmpty ? nil : message,
                sessionId: sessionID.isEmpty ? nil : sessionID,
                intervalSec: intervalOverride,
                scheduleType: newAutomationScheduleType,
                schedule: buildSchedulePayload(),
                timezone: timezone
            )

            await refreshAutomations()
            await refreshInbox()
            if let refreshed = automations.first(where: { $0.id == automationID }) {
                applyAutomationToForm(refreshed)
            }
            appState.clearError()
        } catch {
            appState.lastError = error.localizedDescription
        }
    }

    private func applyAutomationToForm(_ automation: APIAutomationRecord) {
        selectedAutomationID = automation.id
        newAutomationMessage = automation.message
        newAutomationScheduleType = automation.scheduleType
        newAutomationTimezone = automation.timezone

        if let interval = jsonIntValue(automation.schedule["interval_sec"]) {
            newAutomationIntervalSec = String(interval)
        } else {
            newAutomationIntervalSec = String(automation.intervalSec)
        }
        if let hours = jsonIntValue(automation.schedule["interval_hours"]) {
            newAutomationIntervalHours = String(hours)
        }
        if let minute = jsonIntValue(automation.schedule["minute"]) {
            newAutomationMinute = String(minute)
        }
        if let hour = jsonIntValue(automation.schedule["hour"]) {
            newAutomationHour = String(hour)
        }
        if let weekdays = jsonStringArrayValue(automation.schedule["byday"]), !weekdays.isEmpty {
            newAutomationWeekdays = weekdays.joined(separator: ",")
        }
        if let path = automation.schedule["path"]?.stringValue {
            newAutomationWatchPath = path
        }
        if let pollSec = jsonIntValue(automation.schedule["poll_sec"]) {
            newAutomationWatchPollSec = String(pollSec)
        }
        if let recursive = jsonBoolValue(automation.schedule["recursive"]) {
            newAutomationWatchRecursive = recursive
        }
        if let glob = automation.schedule["glob"]?.stringValue, !glob.isEmpty {
            newAutomationWatchGlob = glob
        }
        if let maxChangedFiles = jsonIntValue(automation.schedule["max_changed_files"]) {
            newAutomationWatchMaxChangedFiles = String(maxChangedFiles)
        }
    }

    private func scheduleSummary(for automation: APIAutomationRecord) -> String {
        switch automation.scheduleType {
        case "watch_fs":
            let path = automation.schedule["path"]?.stringValue ?? "-"
            let poll = jsonIntValue(automation.schedule["poll_sec"]) ?? 10
            let glob = automation.schedule["glob"]?.stringValue ?? "*"
            return "watch \(poll)s \(glob) \(path)"
        case "hourly":
            let hours = jsonIntValue(automation.schedule["interval_hours"]) ?? max(1, automation.intervalSec / 3600)
            let minute = jsonIntValue(automation.schedule["minute"]) ?? 0
            return "hourly/\(hours)h @:\(String(format: "%02d", minute))"
        case "weekly":
            let byday = jsonStringArrayValue(automation.schedule["byday"]) ?? ["MO"]
            let hour = jsonIntValue(automation.schedule["hour"]) ?? 9
            let minute = jsonIntValue(automation.schedule["minute"]) ?? 0
            return "weekly \(byday.joined(separator: ",")) \(String(format: "%02d:%02d", hour, minute))"
        default:
            return "interval \(automation.intervalSec)s"
        }
    }

    private func parseWeekdaysInput(_ raw: String) -> [String] {
        let allowed = Set(["MO", "TU", "WE", "TH", "FR", "SA", "SU"])
        var seen = Set<String>()
        var result: [String] = []
        for token in raw.split(separator: ",") {
            let value = token.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
            guard allowed.contains(value), !seen.contains(value) else { continue }
            seen.insert(value)
            result.append(value)
        }
        return result.isEmpty ? ["MO"] : result
    }

    private func jsonIntValue(_ value: JSONValue?) -> Int? {
        guard let value else { return nil }
        switch value {
        case .number(let number):
            return Int(number)
        case .string(let string):
            return Int(string.trimmingCharacters(in: .whitespacesAndNewlines))
        default:
            return nil
        }
    }

    private func jsonBoolValue(_ value: JSONValue?) -> Bool? {
        guard let value else { return nil }
        return value.boolValue
    }

    private func jsonStringArrayValue(_ value: JSONValue?) -> [String]? {
        guard let value else { return nil }
        switch value {
        case .array(let items):
            let result = items.compactMap { item -> String? in
                if case .string(let string) = item {
                    return string
                }
                return nil
            }
            return result.isEmpty ? nil : result
        case .string(let raw):
            let parsed = raw
                .split(separator: ",")
                .map { String($0).trimmingCharacters(in: .whitespacesAndNewlines) }
                .filter { !$0.isEmpty }
            return parsed.isEmpty ? nil : parsed
        default:
            return nil
        }
    }

    private func clampInt(_ raw: String, fallback: Int, min: Int, max: Int) -> Int {
        let parsed = Int(raw.trimmingCharacters(in: .whitespacesAndNewlines)) ?? fallback
        return Swift.max(min, Swift.min(max, parsed))
    }

    private func escalationColor(level: String) -> Color {
        switch level.lowercased() {
        case "critical":
            return AmaryllisTheme.accent
        case "warning":
            return Color.orange
        default:
            return AmaryllisTheme.textSecondary
        }
    }

    private func inboxSeverityColor(_ severity: String) -> Color {
        switch severity.lowercased() {
        case "error":
            return AmaryllisTheme.accent
        case "warning":
            return Color.orange
        default:
            return Color.green
        }
    }
}
