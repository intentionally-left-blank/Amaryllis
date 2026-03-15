import Foundation

@MainActor
final class RuntimeProcessManager: ObservableObject {
    enum ProcessState: String {
        case stopped
        case starting
        case running
        case failed
    }

    enum ConnectionState: String {
        case unknown
        case online
        case offline
    }

    @Published var processState: ProcessState = .stopped
    @Published var connectionState: ConnectionState = .unknown
    @Published var logs: [String] = []
    @Published var logCaptureEnabled: Bool = false

    private var process: Process?
    private var outputPipe: Pipe?
    private var pendingLogs: [String] = []
    private var logFlushTask: Task<Void, Never>?
    private var trailingPartialLine: String = ""
    private let maxLogLines: Int = 120
    private let logFlushDelayNanos: UInt64 = 120_000_000

    var isRunning: Bool {
        processState == .running
    }

    func start(
        runtimeDirectory: String,
        host: String,
        port: Int,
        additionalEnvironment: [String: String] = [:]
    ) {
        guard process == nil else { return }

        processState = .starting
        connectionState = .unknown

        let runtimeURL = URL(fileURLWithPath: runtimeDirectory, isDirectory: true)
        let runtimeServerPath = runtimeURL.appendingPathComponent("runtime/server.py").path
        guard FileManager.default.fileExists(atPath: runtimeServerPath) else {
            appendLog("Runtime not found at \(runtimeDirectory). Expected runtime/server.py", force: true)
            processState = .failed
            connectionState = .offline
            return
        }

        let venvPython = runtimeURL.appendingPathComponent(".venv/bin/python").path
        let pythonCommand = FileManager.default.fileExists(atPath: venvPython) ? venvPython : "python3"
        let uvicornArgs = [
            pythonCommand,
            "-m",
            "uvicorn",
            "runtime.server:app",
            "--host",
            host,
            "--port",
            String(port),
            "--no-access-log"
        ]

        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        proc.arguments = uvicornArgs
        proc.currentDirectoryURL = runtimeURL
        proc.environment = mergedEnvironment(additional: additionalEnvironment)

        let pipe = Pipe()
        outputPipe = pipe
        proc.standardOutput = pipe
        proc.standardError = pipe

        pipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty else { return }
            guard let text = String(data: data, encoding: .utf8) else { return }
            Task { @MainActor in
                self?.appendLog(text)
            }
        }

        proc.terminationHandler = { [weak self] process in
            Task { @MainActor in
                self?.appendLog("Runtime terminated with code \(process.terminationStatus)", force: true)
                self?.processState = .stopped
                self?.connectionState = .offline
                self?.cleanup()
            }
        }

        do {
            try proc.run()
            process = proc
            processState = .running
            appendLog("Runtime started at http://\(host):\(port) using \(pythonCommand)", force: true)
        } catch {
            appendLog("Failed to start runtime: \(error.localizedDescription)", force: true)
            processState = .failed
            connectionState = .offline
            cleanup()
        }
    }

    func stop() {
        guard let process else { return }
        appendLog("Stopping runtime...", force: true)
        process.terminate()
        processState = .stopped
        connectionState = .offline
        cleanup()
    }

    func setLogCaptureEnabled(_ enabled: Bool) {
        logCaptureEnabled = enabled
    }

    private func cleanup() {
        flushPendingLogs()
        logFlushTask?.cancel()
        logFlushTask = nil
        trailingPartialLine = ""
        outputPipe?.fileHandleForReading.readabilityHandler = nil
        outputPipe = nil
        process = nil
    }

    private func appendLog(_ text: String, force: Bool = false) {
        let lines = normalizedLogLines(from: text, force: force)
        guard !lines.isEmpty else { return }
        pendingLogs.append(contentsOf: lines)
        scheduleLogFlushIfNeeded()
    }

    private func normalizedLogLines(from text: String, force: Bool) -> [String] {
        var combined = text
        if !trailingPartialLine.isEmpty {
            combined = trailingPartialLine + combined
            trailingPartialLine = ""
        }

        let endsWithNewline = combined.hasSuffix("\n")
        var parts = combined.split(separator: "\n", omittingEmptySubsequences: false).map(String.init)

        if !endsWithNewline, let last = parts.popLast() {
            trailingPartialLine = last
        }

        let normalized = parts
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
            .filter { line in
                // Drop high-volume per-request noise that makes the UI laggy.
                if line.contains("request_start request_id=") || line.contains("request_done request_id=") {
                    return false
                }
                if line.contains(" HTTP/1.1\" ") && line.contains("INFO:") {
                    return false
                }
                if line.contains("\"GET /health HTTP/1.1\"") || line.contains("\"GET /models HTTP/1.1\"") {
                    return false
                }
                return true
            }
        if force || logCaptureEnabled {
            return normalized
        }
        return normalized.filter { isCriticalRuntimeLine($0) }
    }

    private func isCriticalRuntimeLine(_ line: String) -> Bool {
        let lower = line.lowercased()
        if lower.contains("error")
            || lower.contains("failed")
            || lower.contains("exception")
            || lower.contains("traceback")
            || lower.contains("terminated")
            || lower.contains("shutdown")
            || lower.contains("critical")
            || lower.contains("runtime started")
            || lower.contains("runtime not found") {
            return true
        }
        return false
    }

    private func scheduleLogFlushIfNeeded() {
        guard logFlushTask == nil else { return }
        logFlushTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: self?.logFlushDelayNanos ?? 120_000_000)
            await self?.flushPendingLogsAsync()
        }
    }

    private func flushPendingLogsAsync() async {
        flushPendingLogs()
    }

    private func flushPendingLogs() {
        guard !pendingLogs.isEmpty else {
            logFlushTask = nil
            return
        }
        logs.append(contentsOf: pendingLogs)
        pendingLogs.removeAll(keepingCapacity: true)
        if logs.count > maxLogLines {
            logs.removeFirst(logs.count - maxLogLines)
        }
        logFlushTask = nil
        if !pendingLogs.isEmpty {
            scheduleLogFlushIfNeeded()
        }
    }

    private func mergedEnvironment(additional: [String: String]) -> [String: String] {
        var env = ProcessInfo.processInfo.environment
        env["PYTHONUNBUFFERED"] = "1"
        for (key, value) in additional {
            env[key] = value
        }
        return env
    }
}
