import Foundation
import SwiftUI

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

    var body: some View {
        HStack(spacing: 12) {
            leftPanel
                .frame(minWidth: 320, idealWidth: 360, maxWidth: 420)

            rightPanel
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .task {
            await refreshAgents()
            await refreshRuns()
            await refreshAutomations()
            await refreshInbox()
        }
        .onChange(of: selectedAgentID ?? "") { _ in
            Task {
                await refreshRuns()
                await refreshAutomations()
                await refreshInbox()
            }
        }
        .onChange(of: selectedRunID ?? "") { _ in
            Task { await loadReplayForSelectedRun(silent: true) }
        }
        .onChange(of: inboxUnreadOnly) { _ in
            Task { await refreshInbox() }
        }
        .onChange(of: userID) { _ in
            Task { await refreshInbox() }
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
                        HStack(spacing: 8) {
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

                        if let replay = selectedRunReplay, replay.runId == run.id {
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

                            Text("Replay Timeline (\(replay.timeline.count))")
                                .font(AmaryllisTheme.bodyFont(size: 11, weight: .semibold))
                                .foregroundStyle(AmaryllisTheme.textSecondary)
                            ScrollView {
                                LazyVStack(alignment: .leading, spacing: 4) {
                                    ForEach(Array(replay.timeline.suffix(40))) { event in
                                        HStack(spacing: 6) {
                                            Circle()
                                                .fill(replayStageColor(event.stage))
                                                .frame(width: 6, height: 6)
                                            Text("[\(event.timestamp)] \(event.stage) \(event.message)")
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

                        Text("Raw Checkpoints (\(run.checkpoints.count))")
                            .font(AmaryllisTheme.bodyFont(size: 11, weight: .semibold))
                            .foregroundStyle(AmaryllisTheme.textSecondary)
                        ScrollView {
                            LazyVStack(alignment: .leading, spacing: 4) {
                                ForEach(Array(run.checkpoints.suffix(20))) { checkpoint in
                                    let stage = checkpoint.stage ?? "-"
                                    let message = checkpoint.message ?? "-"
                                    Text("[\(checkpoint.timestamp)] \(stage) \(message)")
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
            await pollRunUntilTerminal(runID: run.id, timeoutSec: 120)
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
            return
        }

        isLoadingRuns = true
        defer { isLoadingRuns = false }

        do {
            let response = try await appState.apiClient.listAgentRuns(
                agentId: agent.id,
                userId: userID,
                status: nil,
                limit: 100
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

    private func loadReplayForSelectedRun(silent: Bool) async {
        guard let run = selectedRun else {
            if !silent {
                replayStatusMessage = "Select a run to load replay."
            }
            selectedRunReplay = nil
            return
        }
        await loadReplay(runID: run.id, silent: silent)
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

    private func pollRunUntilTerminal(runID: String, timeoutSec: Double) async {
        let terminalStates: Set<String> = ["succeeded", "failed", "canceled"]
        let maxTicks = max(1, Int(timeoutSec / 1.2))

        for _ in 0..<maxTicks {
            do {
                let run = try await appState.apiClient.getAgentRun(runId: runID)
                upsertRun(run)
                selectedRunID = run.id
                runStatusMessage = "Run \(run.id) status: \(run.status)"

                if terminalStates.contains(run.status) {
                    if run.status == "succeeded",
                       !consumedRunResponses.contains(run.id),
                       let response = runResponseText(from: run),
                       !response.isEmpty {
                        chatHistory.append("AGENT (run): \(response)")
                        consumedRunResponses.insert(run.id)
                    } else if run.status == "failed", let error = run.errorMessage, !error.isEmpty {
                        chatHistory.append("RUN FAILED: \(error)")
                    } else if run.status == "canceled" {
                        chatHistory.append("RUN CANCELED")
                    }
                    await loadReplay(runID: run.id, silent: true)
                    return
                }
            } catch {
                appState.lastError = error.localizedDescription
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
            await pollRunUntilTerminal(runID: id, timeoutSec: 120)
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
