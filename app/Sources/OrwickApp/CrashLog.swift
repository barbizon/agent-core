import SwiftUI
import Darwin

/// The app's own log (~/Library/Logs/Orwick/app.log) and the check, at launch, whether the
/// last run ended without quitting. Then a window shows what happened: the last run's log and,
/// when macOS wrote one, the crash report's summary.
enum AppLog {
    static let folder = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent("Library/Logs/Orwick", isDirectory: true)
    static let file = folder.appendingPathComponent("app.log")
    /// There while the app runs; removed on a normal quit. Found at launch = the last run crashed
    /// or was killed.
    static let marker = folder.appendingPathComponent("running.json")
    static let runStart = "──── Orwick started"
    private static let queue = DispatchQueue(label: "orwick.applog")
    private static let maxBytes = 1_000_000
    private static let stamp: DateFormatter = {   // your local time
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyy-MM-dd HH:mm:ss.SSS"
        return formatter
    }()
    /// Kept open for the crash handlers, which may only call write(2).
    private static var crashFD: Int32 = -1

    static func log(_ text: String) {
        let line = "\(stamp.string(from: Date())) \(text)\n"
        queue.async {
            try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
            if let handle = try? FileHandle(forWritingTo: file) {
                handle.seekToEndOfFile()
                handle.write(Data(line.utf8))
                try? handle.close()
            } else {
                try? Data(line.utf8).write(to: file)
            }
        }
    }

    /// At launch: the last run's report when it didn't quit normally (nil otherwise); then starts
    /// this run's part of the log and installs the crash handlers.
    static func startRun() -> CrashReport? {
        try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let report = FileManager.default.fileExists(atPath: marker.path) ? CrashReport.lastRun() : nil
        rotate()
        let version = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "dev"
        let started = Date()
        let info: [String: Any] = ["pid": ProcessInfo.processInfo.processIdentifier,
                                   "started": started.timeIntervalSince1970]
        try? JSONSerialization.data(withJSONObject: info).write(to: marker, options: .atomic)
        queue.sync {}   // the rotation is done before this run writes
        log("\(runStart) \(started.formatted(date: .abbreviated, time: .standard)) · version \(version) · pid \(ProcessInfo.processInfo.processIdentifier) ────")
        installCrashHandlers()
        return report
    }

    /// A normal quit: the marker goes, so the next launch shows nothing.
    static func endRun() {
        log("Quit normally")
        queue.sync {}
        try? FileManager.default.removeItem(at: marker)
    }

    private static func rotate() {
        guard let size = (try? FileManager.default.attributesOfItem(atPath: file.path))?[.size] as? Int,
              size > maxBytes else { return }
        let old = folder.appendingPathComponent("app.1.log")
        try? FileManager.default.removeItem(at: old)
        try? FileManager.default.moveItem(at: file, to: old)
    }

    private static func installCrashHandlers() {
        crashFD = open(file.path, O_WRONLY | O_APPEND | O_CREAT, 0o644)
        NSSetUncaughtExceptionHandler { exception in
            let text = "CRASH: uncaught exception \(exception.name.rawValue): \(exception.reason ?? "")\n"
                + exception.callStackSymbols.prefix(25).joined(separator: "\n") + "\n"
            AppLog.writeNow(text)
        }
        for signo in [SIGABRT, SIGSEGV, SIGBUS, SIGILL, SIGTRAP, SIGFPE] {
            signal(signo) { signo in
                // Only async-signal-safe calls here: write(2) of a fixed text, then the default action.
                let text: StaticString
                switch signo {
                case SIGTRAP: text = "CRASH: SIGTRAP (a Swift or SwiftUI check failed)\n"
                case SIGSEGV: text = "CRASH: SIGSEGV (bad memory access)\n"
                case SIGBUS: text = "CRASH: SIGBUS (bad memory access)\n"
                case SIGABRT: text = "CRASH: SIGABRT (aborted)\n"
                case SIGILL: text = "CRASH: SIGILL (illegal instruction)\n"
                default: text = "CRASH: fatal signal\n"
                }
                text.withUTF8Buffer { bytes in _ = write(AppLog.crashFD, bytes.baseAddress, bytes.count) }
                signal(signo, SIG_DFL)
                raise(signo)
            }
        }
    }

    fileprivate static func writeNow(_ text: String) {
        guard crashFD >= 0 else { return }
        var text = text
        text.withUTF8 { bytes in _ = write(crashFD, bytes.baseAddress, bytes.count) }
    }
}

/// What the last run left behind: its log lines and macOS's crash report, if any.
struct CrashReport: Identifiable {
    let id = UUID()
    let started: Date?
    let log: String
    let crashFile: URL?
    let crashSummary: String?

    var text: String {
        var parts: [String] = []
        if let crashSummary { parts.append("macOS crash report (\(crashFile?.lastPathComponent ?? "")):\n" + crashSummary) }
        parts.append("App log of that run (\(AppLog.file.path)):\n" + (log.isEmpty ? "(empty)" : log))
        return parts.joined(separator: "\n\n")
    }

    static func lastRun() -> CrashReport {
        let info = (try? Data(contentsOf: AppLog.marker))
            .flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] }
        let started = (info?["started"] as? Double).map(Date.init(timeIntervalSince1970:))
        let all = (try? String(contentsOf: AppLog.file, encoding: .utf8)) ?? ""
        // The last run's part of the log: from its start line on, at most 300 lines.
        let lines = all.components(separatedBy: "\n")
        let start = lines.lastIndex { $0.contains(AppLog.runStart) } ?? 0
        let log = lines[start...].suffix(300).joined(separator: "\n").trimmingCharacters(in: .whitespacesAndNewlines)
        let crash = newestCrashFile(since: started)
        return CrashReport(started: started, log: log, crashFile: crash, crashSummary: crash.flatMap(summary))
    }

    /// macOS's report for Orwick written since the run started (it may take a moment to appear).
    static func newestCrashFile(since start: Date?) -> URL? {
        let folder = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Logs/DiagnosticReports")
        let files = (try? FileManager.default.contentsOfDirectory(at: folder, includingPropertiesForKeys: [.contentModificationDateKey])) ?? []
        return files
            .filter { $0.lastPathComponent.hasPrefix("Orwick") && ["ips", "crash"].contains($0.pathExtension) }
            .compactMap { url -> (URL, Date)? in
                guard let date = try? url.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate,
                      date >= (start ?? .distantPast) else { return nil }
                return (url, date)
            }
            .max { $0.1 < $1.1 }?.0
    }

    /// The crash in a few lines: what happened and the top of the crashed thread.
    static func summary(of url: URL) -> String? {
        guard let text = try? String(contentsOf: url, encoding: .utf8) else { return nil }
        let body = text.split(separator: "\n", maxSplits: 1).last.map(String.init) ?? text
        guard let json = (try? JSONSerialization.jsonObject(with: Data(body.utf8))) as? [String: Any] else {
            return text.components(separatedBy: "\n").prefix(40).joined(separator: "\n")
        }
        var out: [String] = []
        if let exception = json["exception"] as? [String: Any] {
            out.append("\(exception["type"] ?? "") \(exception["signal"] ?? "")")
        }
        if let termination = json["termination"] as? [String: Any], let indicator = termination["indicator"] {
            out.append("\(indicator)")
        }
        if let asi = json["asi"] as? [String: [String]] {
            out += asi.values.flatMap { $0 }
        }
        if let threads = json["threads"] as? [[String: Any]], let images = json["usedImages"] as? [[String: Any]] {
            let index = json["faultingThread"] as? Int ?? 0
            if threads.indices.contains(index), let frames = threads[index]["frames"] as? [[String: Any]] {
                out.append("Crashed thread:")
                for frame in frames.prefix(15) {
                    let image = (frame["imageIndex"] as? Int).flatMap { images.indices.contains($0) ? images[$0]["name"] as? String : nil } ?? "?"
                    out.append("  \(image)  \(frame["symbol"] as? String ?? "?")")
                }
            }
        }
        return out.joined(separator: "\n")
    }
}

/// Shown at launch after a run that didn't quit normally.
struct CrashReportView: View {
    let report: CrashReport
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .top, spacing: 12) {
                Image(systemName: "exclamationmark.triangle.fill")
                    .font(.largeTitle).foregroundStyle(.orange)
                VStack(alignment: .leading, spacing: 4) {
                    Text("Orwick closed unexpectedly last time").font(.title3.weight(.semibold))
                    Text(subtitle).font(.callout).foregroundStyle(.secondary)
                }
            }
            ScrollView {
                Text(report.text)
                    .font(.caption.monospaced())
                    .textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(10)
            }
            .background(.quaternary.opacity(0.5), in: RoundedRectangle(cornerRadius: 8))
            HStack {
                Button("Copy") {
                    NSPasteboard.general.clearContents()
                    NSPasteboard.general.setString(report.text, forType: .string)
                }
                Button("Show Log in Finder") { NSWorkspace.shared.activateFileViewerSelecting([report.crashFile ?? AppLog.file]) }
                Spacer()
                Button("Close") { dismiss() }
                    .keyboardShortcut(.defaultAction)
            }
        }
        .padding(18)
        .frame(width: 720, height: 520)
    }

    private var subtitle: String {
        let when = report.started.map { "The run started \($0.formatted(date: .abbreviated, time: .shortened)). " } ?? ""
        return when + (report.crashSummary != nil
            ? "The app crashed; macOS's crash report and the app's log are below."
            : "It was stopped without quitting (crash, Force Quit, or the Mac shut down). The app's log is below.")
    }
}
