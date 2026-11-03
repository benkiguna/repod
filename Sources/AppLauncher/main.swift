import AppKit

// The double-clickable app. It holds no features: it starts the page's server, opens the page in the
// browser and stays in the Dock while the server runs. Quitting stops the server the way Ctrl-C does,
// so a write in progress finishes first.

final class Launcher: NSObject, NSApplicationDelegate {
    private var server: Process?
    private var port: UInt16 = 8765
    private var quitting = false
    private var termination: DispatchSourceSignal?

    /// Everything the server needs, beside this app's executable.
    private var resources: URL { Bundle.main.resourceURL ?? Bundle.main.bundleURL }

    func applicationDidFinishLaunching(_ notification: Notification) {
        buildMenu()
        // `kill` and a log-out should stop the server as Quit does.
        signal(SIGTERM, SIG_IGN)
        let source = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .main)
        source.setEventHandler { NSApp.terminate(nil) }
        source.resume()
        termination = source
        do {
            try startServer()
        } catch {
            fail("The server could not start: \(error.localizedDescription)")
        }
    }

    /// Double-clicking the app again, or its Dock icon, brings the page back.
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        openPage()
        return false
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard let server, server.isRunning else { return .terminateNow }
        guard !quitting else { return .terminateCancel }
        quitting = true
        server.interrupt()  // as Ctrl-C: the server waits for a write to finish
        for _ in 0..<15 where server.isRunning { Thread.sleep(forTimeInterval: 0.1) }
        guard server.isRunning else { return .terminateNow }  // the usual case: nothing was being written
        // A write is under way. The app leaves when the server has: `.terminateLater` holds the run loop in a mode where word of that never arrives.
        DispatchQueue.global().async {
            server.waitUntilExit()
            exit(0)
        }
        return .terminateCancel
    }

    private func buildMenu() {
        let main = NSMenu()
        let item = NSMenuItem()
        main.addItem(item)
        let menu = NSMenu()
        menu.addItem(withTitle: "Open Page", action: #selector(openPageAction), keyEquivalent: "o").target = self
        menu.addItem(.separator())
        menu.addItem(withTitle: "Quit", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        item.submenu = menu
        NSApp.mainMenu = main
    }

    @objc private func openPageAction() { openPage() }

    private func openPage() {
        if let url = URL(string: "http://127.0.0.1:\(port)") { NSWorkspace.shared.open(url) }
    }

    /// The runtime inside the app, and without one the Mac's own, which Apple's command-line tools provide.
    private func python() -> URL? {
        let bundled = resources.appendingPathComponent("python/\(machine() == "arm64" ? "arm64" : "x86_64")/bin/python3")
        if FileManager.default.isExecutableFile(atPath: bundled.path) { return bundled }
        let tools = Process()
        tools.executableURL = URL(fileURLWithPath: "/usr/bin/xcode-select")
        tools.arguments = ["-p"]
        tools.standardOutput = FileHandle.nullDevice
        tools.standardError = FileHandle.nullDevice
        guard (try? tools.run()) != nil else { return nil }
        tools.waitUntilExit()
        return tools.terminationStatus == 0 ? URL(fileURLWithPath: "/usr/bin/python3") : nil
    }

    private func startServer() throws {
        guard let free = freePort(from: 8765) else { return fail("No free port was found on this Mac.") }
        guard let python = python() else { return fail("This needs Python. In Terminal, run: xcode-select --install") }
        port = free
        let environment = ProcessInfo.processInfo.environment
        let script = environment["REPOD_SERVER"] ?? resources.appendingPathComponent("web/web.py").path

        let process = Process()
        process.executableURL = python
        process.arguments = [script, "--port", String(free)]
        process.environment = environment.merging([
            "IPODCTL": environment["IPODCTL"] ?? resources.appendingPathComponent("bin/ipodctl").path,
            "SSL_CERT_FILE": resources.appendingPathComponent("cacert.pem").path,
            "PYTHONDONTWRITEBYTECODE": "1",
        ]) { _, new in new }
        let log = FileHandle.logFile()
        process.standardOutput = log
        process.standardError = log
        process.terminationHandler = { [weak self] _ in DispatchQueue.main.async { self?.serverEnded() } }
        try process.run()
        server = process

        DispatchQueue.global().async { [weak self] in
            guard let self else { return }
            let deadline = Date().addingTimeInterval(20)
            while Date() < deadline, process.isRunning {
                if self.answers(free) {
                    if environment["REPOD_NO_OPEN"] == nil { DispatchQueue.main.async { self.openPage() } }
                    return
                }
                Thread.sleep(forTimeInterval: 0.1)
            }
        }
    }

    private func serverEnded() {
        if quitting { return }
        fail("The server stopped unexpectedly. Its log is in ~/Library/Logs/RePod.log.")
    }

    private func fail(_ message: String) {
        NSApp.activate(ignoringOtherApps: true)
        let alert = NSAlert()
        alert.messageText = "RePod can't continue"
        alert.informativeText = message
        alert.runModal()
        NSApp.terminate(nil)
    }

    private func machine() -> String {
        var size = 0
        sysctlbyname("hw.machine", nil, &size, nil, 0)
        var name = [CChar](repeating: 0, count: size)
        sysctlbyname("hw.machine", &name, &size, nil, 0)
        return String(cString: name)
    }

    private func freePort(from first: UInt16) -> UInt16? {
        (first..<first + 20).first { !answers($0) && canBind($0) }
    }

    private func address(_ port: UInt16) -> sockaddr_in {
        var address = sockaddr_in()
        address.sin_family = sa_family_t(AF_INET)
        address.sin_port = port.bigEndian
        address.sin_addr.s_addr = inet_addr("127.0.0.1")
        return address
    }

    private func canBind(_ port: UInt16) -> Bool {
        let socket = Darwin.socket(AF_INET, SOCK_STREAM, 0)
        defer { close(socket) }
        var yes: Int32 = 1  // as the server does: a port that was just closed is free, and the page's address stays the same
        setsockopt(socket, SOL_SOCKET, SO_REUSEADDR, &yes, socklen_t(MemoryLayout<Int32>.size))
        var place = address(port)
        return withUnsafePointer(to: &place) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { Darwin.bind(socket, $0, socklen_t(MemoryLayout<sockaddr_in>.size)) == 0 }
        }
    }

    private func answers(_ port: UInt16) -> Bool {
        let socket = Darwin.socket(AF_INET, SOCK_STREAM, 0)
        defer { close(socket) }
        var place = address(port)
        return withUnsafePointer(to: &place) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { Darwin.connect(socket, $0, socklen_t(MemoryLayout<sockaddr_in>.size)) == 0 }
        }
    }
}

private extension FileHandle {
    static func logFile() -> FileHandle {
        let url = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Logs/RePod.log")
        FileManager.default.createFile(atPath: url.path, contents: nil)
        return (try? FileHandle(forWritingTo: url)) ?? .nullDevice
    }
}

let application = NSApplication.shared
let launcher = Launcher()
application.delegate = launcher
application.setActivationPolicy(.regular)
application.run()
