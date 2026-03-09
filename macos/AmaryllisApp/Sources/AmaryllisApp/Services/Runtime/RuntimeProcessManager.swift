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

    private var process: Process?
    private var outputPipe: Pipe?

    var isRunning: Bool {
        processState == .running
    }

    func start(runtimeDirectory: String, host: String, port: Int) {
        guard process == nil else { return }

        processState = .starting
        connectionState = .unknown

        let runtimeURL = URL(fileURLWithPath: runtimeDirectory, isDirectory: true)
        let runtimeServerPath = runtimeURL.appendingPathComponent("runtime/server.py").path
        guard FileManager.default.fileExists(atPath: runtimeServerPath) else {
            appendLog("Runtime not found at \(runtimeDirectory). Expected runtime/server.py")
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
            String(port)
        ]

        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        proc.arguments = uvicornArgs
        proc.currentDirectoryURL = runtimeURL
        proc.environment = mergedEnvironment()

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
                self?.appendLog("Runtime terminated with code \(process.terminationStatus)")
                self?.processState = .stopped
                self?.connectionState = .offline
                self?.cleanup()
            }
        }

        do {
            try proc.run()
            process = proc
            processState = .running
            appendLog("Runtime started at http://\(host):\(port) using \(pythonCommand)")
        } catch {
            appendLog("Failed to start runtime: \(error.localizedDescription)")
            processState = .failed
            connectionState = .offline
            cleanup()
        }
    }

    func stop() {
        guard let process else { return }
        appendLog("Stopping runtime...")
        process.terminate()
        processState = .stopped
        connectionState = .offline
        cleanup()
    }

    private func cleanup() {
        outputPipe?.fileHandleForReading.readabilityHandler = nil
        outputPipe = nil
        process = nil
    }

    private func appendLog(_ text: String) {
        let lines = text
            .split(separator: "\n", omittingEmptySubsequences: false)
            .map(String.init)
            .filter { !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }

        logs.append(contentsOf: lines)
        if logs.count > 400 {
            logs.removeFirst(logs.count - 400)
        }
    }

    private func mergedEnvironment() -> [String: String] {
        var env = ProcessInfo.processInfo.environment
        env["PYTHONUNBUFFERED"] = "1"
        return env
    }
}
