import AppKit
import SwiftTerm
import SwiftUI

/// One conversation: a terminal running the `orwick` command in a chosen folder.
/// The app never talks to Orwick directly; it only hosts the CLI, like iTerm would.
final class Session: NSObject, ObservableObject, Identifiable, LocalProcessTerminalViewDelegate {
    let id = UUID()
    let folder: URL
    let started = Date()
    let terminal: LocalProcessTerminalView
    @Published private(set) var running = false
    @Published private(set) var exitCode: Int32?
    /// The middle of the window: the console, or messages / AI steps / tool calls.
    @Published var screen: SessionScreen = .console
    /// Top / Bottom in the toolbar, for the messages, steps and calls screens (the console
    /// scrolls its own terminal). The count makes each press a new request.
    @Published private(set) var scrollRequest: (toTop: Bool, count: Int) = (false, 0)

    /// Scrolls what the middle of the window shows to its very top or bottom.
    func scroll(toTop: Bool) {
        if screen == .console {
            terminal.scroll(toPosition: toTop ? 0 : 1)
        } else {
            scrollRequest = (toTop, scrollRequest.count + 1)
        }
    }
    @Published private(set) var theme = TerminalTheme.dark
    @Published private(set) var status: OrwickStatus?
    /// The name you gave this session (name.txt), or nil.
    @Published private(set) var customName: String?
    /// This session's own tool switches, from `.orwick/sessions/<id>/tools.json` (Orwick reads
    /// it before each message; `/tools on|off` in the chat writes the same file).
    @Published private(set) var toolOverrides: [String: Bool] = [:]

    var toolsURL: URL { sessionFolder.appendingPathComponent("tools.json") }
    var skillsURL: URL { sessionFolder.appendingPathComponent("skills.json") }

    /// This session's own skill switches (skills.json). Orwick reads them before your next
    /// message and tells the AI then; `/skills on|off` in the chat writes the same file.
    @Published private(set) var skillOverrides: [String: Bool] = [:]

    /// Switches a skill for this session only. The same as the settings' value: no switch.
    func setSkill(_ name: String, on: Bool, settingsValue: Bool) {
        var changed = Session.readToolOverrides(from: skillsURL)
        if on == settingsValue { changed.removeValue(forKey: name) } else { changed[name] = on }
        Session.writeToolOverrides(changed, to: skillsURL)
        skillOverrides = changed
    }

    /// Switches a tool for this session only. The same as the settings' value: no override.
    func setTool(_ name: String, on: Bool, settingsValue: Bool) {
        var changed = readToolOverrides()
        if on == settingsValue { changed.removeValue(forKey: name) } else { changed[name] = on }
        writeToolOverrides(changed)
    }

    func resetTools() { writeToolOverrides([:]) }

    private func readToolOverrides() -> [String: Bool] { Session.readToolOverrides(from: toolsURL) }

    private func writeToolOverrides(_ map: [String: Bool]) {
        Session.writeToolOverrides(map, to: toolsURL)
        toolOverrides = map
    }

    static func readToolOverrides(from url: URL) -> [String: Bool] {
        guard let data = try? Data(contentsOf: url),
              let map = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else { return [:] }
        var result: [String: Bool] = [:]
        for (name, value) in map {
            if let flag = value as? NSNumber, CFGetTypeID(flag) == CFBooleanGetTypeID() { result[name] = flag.boolValue }
        }
        return result
    }

    /// No file when nothing is switched (the session follows the settings).
    static func writeToolOverrides(_ map: [String: Bool], to url: URL) {
        if map.isEmpty {
            try? FileManager.default.removeItem(at: url)
        } else if let data = try? JSONSerialization.data(withJSONObject: map, options: [.sortedKeys]) {
            try? data.write(to: url, options: .atomic)
        }
    }
    /// The fill percent at which you closed the banner; it comes back 10 points later.
    @Published var bannerDismissedAt: Int?

    /// The conversation-fill warning to show, if any.
    var fillWarning: OrwickStatus.Conversation? {
        guard let level = status?.conversation, level.isWarning, running else { return nil }
        if let dismissed = bannerDismissedAt, level.percent < dismissed + 10 { return nil }
        return level
    }
    private var statusStamp: Date?

    /// This Orwick run's id. Orwick keeps the run's records in
    /// `<folder>/.orwick/sessions/<id>/` (status.json, audit.jsonl); the app only
    /// chooses the id so it knows where to look. Each restart is a new run.
    @Published private(set) var sessionID = Session.newID()
    private var pendingResume: String?
    /// Other projects this session may read (`orwick --related`), given when it starts.
    private var relatedFolders: [URL] = []
    /// A command typed for you once Orwick first waits at its prompt (e.g. `/map …`).
    private var firstCommand: String?

    /// True when Orwick has saved a conversation for this session (something was said).
    var hasSavedConversation: Bool {
        FileManager.default.fileExists(atPath: sessionFolder.appendingPathComponent("conversation.json").path)
    }

    var sessionFolder: URL { folder.appendingPathComponent(".orwick/sessions/\(sessionID)", isDirectory: true) }
    var statusURL: URL { sessionFolder.appendingPathComponent("status.json") }

    /// The same shape as Orwick's own ids: 2026-11-12_114105_a3f9.
    static func newID(now: Date = Date()) -> String {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "yyyy-MM-dd_HHmmss"
        return "\(formatter.string(from: now))_\(String(format: "%04x", UInt16.random(in: .min ... .max)))"
    }

    /// What the app calls this session: the name you gave it, else its folder's name.
    var name: String { customName ?? folder.lastPathComponent }

    /// Names (or with "" un-names) the session; Orwick and the sidebar pick it up.
    func rename(_ text: String) throws {
        customName = try SessionName.write(text, to: sessionFolder)
    }
    var shortPath: String { (folder.path as NSString).abbreviatingWithTildeInPath }

    /// `resume`: continue that saved session (`orwick --resume <id>`) instead of starting a new one.
    init(folder: URL, resume: String? = nil, related: [URL] = [], firstCommand: String? = nil) {
        self.folder = folder
        if let resume, Session.isSessionID(resume) { pendingResume = resume }
        relatedFolders = related
        self.firstCommand = firstCommand
        terminal = LocalProcessTerminalView(frame: NSRect(x: 0, y: 0, width: 900, height: 600))
        super.init()
        terminal.processDelegate = self
        terminal.font = NSFont.monospacedSystemFont(ofSize: 13, weight: .regular)
        terminal.optionAsMetaKey = true
        terminal.changeScrollback(Session.scrollbackLines)  // room for a resumed conversation
        theme = MainActor.assumeIsolated { TerminalTheme.current }  // sessions are created on the main thread
        theme.apply(to: terminal)
        start()
    }

    func start() {
        exitCode = nil
        running = true
        // A login + interactive shell gives Orwick the same environment as your terminal
        // (~/.zprofile and ~/.zshrc: PATH, and variables such as tokens that MCP servers
        // use); `exec` then replaces the shell with Orwick.
        let command: String
        if let resume = pendingResume {
            sessionID = resume
            command = "exec orwick --resume \(Session.shellQuoted(resume))"
        } else {
            sessionID = Session.newID()
            command = "exec orwick --session-id \(Session.shellQuoted(sessionID))"
                + relatedFolders.map { " --related " + Session.shellQuoted($0.path) }.joined()
        }
        relatedFolders = []   // kept in the session from now on (related.json)
        pendingResume = nil
        status = nil
        statusStamp = nil
        AppLog.log("Session \(sessionID) started in \(folder.path): \(command)")
        terminal.startProcess(executable: Session.loginShell,
                              args: ["-l", "-i", "-c", command],
                              environment: Session.environment(),
                              currentDirectory: folder.path)
    }

    func apply(_ newTheme: TerminalTheme) {
        guard newTheme != theme else { return }
        theme = newTheme
        newTheme.apply(to: terminal)
    }

    /// Starts Orwick again. If something was said, it continues the same conversation.
    /// Starts Orwick again. If something was said, it continues the same conversation.
    /// Waits for the old Orwick to finish first: it saves the session and frees its lock.
    func restart() {
        let wasRunning = running
        running = false
        endProcess { [weak self] in
            guard let self else { return }
            if self.hasSavedConversation { self.pendingResume = self.sessionID }
            if wasRunning { self.terminal.feed(text: "\r\n") }
            self.terminal.getTerminal().resetToInitialState()
            self.start()
        }
    }

    /// Types a command into Orwick's prompt (e.g. a skill action's `/do ...`), only while
    /// Orwick is waiting for you, so it never lands in the middle of an answer.
    func run(_ command: String) {
        guard running, status?.isWaiting ?? false,
              command.hasPrefix("/do ") || command.hasPrefix("/backend ") || command.hasPrefix("/map") else { return }
        terminal.send(txt: command + "\r")
        terminal.window?.makeFirstResponder(terminal)
    }

    func stop() {
        running = false
        endProcess {}
    }

    /// Asks Orwick to end (it saves and frees its session), collects the finished
    /// process so none is left behind as a zombie, and forces it after 5 seconds.
    private func endProcess(then done: @escaping () -> Void) {
        let pid = terminal.process?.shellPid ?? 0
        terminal.terminate()                       // closes the terminal and sends SIGTERM
        guard pid > 0 else { return done() }
        DispatchQueue.global(qos: .userInitiated).async {
            Session.reap(pid)
            DispatchQueue.main.async(execute: done)
        }
    }

    /// Waits for a child process to end and collects it; SIGKILL after `seconds`.
    static func reap(_ pid: pid_t, seconds: Double = 5) {
        var status: Int32 = 0
        let deadline = Date().addingTimeInterval(seconds)
        while Date() < deadline {
            let result = waitpid(pid, &status, WNOHANG)
            if result == pid || (result == -1 && errno == ECHILD) { return }
            usleep(50_000)
        }
        kill(pid, SIGKILL)
        waitpid(pid, &status, 0)
    }

    /// Re-reads the status file if Orwick changed it. After /clear, Orwick's last
    /// update of the old session names the new one, and the app follows it.
    func refreshStatus() {
        defer {   // also named from Orwick's /name, or after /clear moved to a new session
            let fresh = SessionName.read(sessionFolder)
            if fresh != customName { customName = fresh }
        }
        for _ in 0..<3 {  // follow at most a few /clear hops at once
            let modified = (try? statusURL.resourceValues(forKeys: [.contentModificationDateKey]))?.contentModificationDate
            guard let modified, modified != statusStamp else { return }
            statusStamp = modified
            toolOverrides = readToolOverrides()
            skillOverrides = Session.readToolOverrides(from: skillsURL)
            guard let fresh = OrwickStatus.read(from: statusURL) else { return }
            if fresh != status { status = fresh }
            if let command = firstCommand, running, fresh.isWaiting {
                firstCommand = nil
                terminal.send(txt: command + "\r")
            }
            guard let next = fresh.continuedIn, Session.isSessionID(next), next != sessionID else { return }
            sessionID = next
            statusStamp = nil
        }
    }

    static func isSessionID(_ text: String) -> Bool {
        text.range(of: #"^\d{4}-\d{2}-\d{2}_\d{6}_[0-9a-f]{4,8}$"#, options: .regularExpression) != nil
    }

    static func shellQuoted(_ text: String) -> String {
        "'" + text.replacingOccurrences(of: "'", with: "'\\''") + "'"
    }

    // MARK: LocalProcessTerminalViewDelegate

    func processTerminated(source: TerminalView, exitCode: Int32?) {
        DispatchQueue.main.async {
            guard self.running else { return }
            self.running = false
            self.exitCode = exitCode
            let code = exitCode.map { " (exit code \($0))" } ?? ""
            AppLog.log("Session \(self.sessionID) in \(self.folder.path) ended\(code)")
            self.terminal.feed(text: "\r\n\u{1b}[2m── Orwick ended\(code). Press ⌘R to start it again. ──\u{1b}[0m\r\n")
        }
    }

    func sizeChanged(source: LocalProcessTerminalView, newCols: Int, newRows: Int) {}
    func setTerminalTitle(source: LocalProcessTerminalView, title: String) {}
    func hostCurrentDirectoryUpdate(source: TerminalView, directory: String?) {}

    // MARK: Process setup

    /// Lines you can scroll back (SwiftTerm keeps 500 by default).
    static let scrollbackLines = 10_000

    static var loginShell: String {
        if let entry = getpwuid(getuid()), let shell = entry.pointee.pw_shell,
           FileManager.default.isExecutableFile(atPath: String(cString: shell)) {
            return String(cString: shell)
        }
        return "/bin/zsh"
    }

    static func environment() -> [String] {
        var values = Terminal.getEnvironmentVariables(termName: "xterm-256color", trueColor: true)
        let current = ProcessInfo.processInfo.environment
        for key in ["TMPDIR", "SSH_AUTH_SOCK"] {
            if let value = current[key] { values.append("\(key)=\(value)") }
        }
        values.append("SHELL=\(loginShell)")
        values.append("TERM_PROGRAM=OrwickApp")
        return values
    }
}

enum FolderPanelMode { case session, map }

@MainActor
final class SessionStore: ObservableObject {
    static let shared = SessionStore()

    @Published private(set) var sessions: [Session] = []
    @Published var selection: Session.ID? {
        didSet { if selection != nil { openMap = nil; showingAnalytics = false } }
    }
    /// The Analytics screen (common review issues) is shown in the detail area.
    @Published var showingAnalytics = false
    /// What the last run left behind when it didn't quit normally; shown once at launch.
    @Published var crashReport: CrashReport?
    /// The service map shown in the detail area instead of a session.
    @Published var openMap: String?
    /// The service map library (~/.orwick/maps), the newest first.
    @Published private(set) var maps: [ServiceMap] = []
    /// What the New Session panel is for: a session, or a service map.
    @Published var folderPanelMode: FolderPanelMode = .session
    @Published var showingSettings = false
    @Published var showingInfo = false
    /// The models each backend offers (from `orwick --list-models`), for the model menus.
    @Published var models: [String: [ModelInfo]] = [:]

    func loadModels() {
        ModelCatalog.load { [weak self] models in self?.models = models }
    }
    /// Sessions whose docs are shown beside their terminal (a resizable split).
    @Published var docsOpen: Set<Session.ID> = []

    func toggleDocs(_ session: Session) {
        selection = session.id
        if docsOpen.contains(session.id) { docsOpen.remove(session.id) } else { docsOpen.insert(session.id) }
    }

    func showDocs(_ session: Session) {
        selection = session.id
        docsOpen.insert(session.id)
    }

    /// A saved session's docs: open (resume) the session, with its docs beside it.
    func showDocs(_ recent: RecentSession) {
        open(recent)
        if let session = sessions.first(where: { $0.folder == recent.folder && $0.sessionID == recent.id }) {
            docsOpen.insert(session.id)
        }
    }
    @Published var appearance: Appearance {
        didSet {
            UserDefaults.standard.set(appearance.rawValue, forKey: Self.appearanceKey)
            applyAppearance()
        }
    }
    private var appearanceObservation: NSKeyValueObservation?
    private static let appearanceKey = "appearance"
    @Published private(set) var recentFolders: [String]
    @Published private(set) var recentSessions: [RecentSession] = []
    /// Goes up when saved sessions change on disk by the app's own doing (one deleted, one
    /// closed), so the dashboard re-reads at once.
    @Published private(set) var savedSessionsVersion = 0
    static let recentSessionLimit = 15

    private static let recentKey = "recentFolders"
    private static let recentLimit = 8

    private init() {
        recentFolders = UserDefaults.standard.stringArray(forKey: Self.recentKey) ?? []
        appearance = Appearance(rawValue: UserDefaults.standard.string(forKey: Self.appearanceKey) ?? "") ?? .system
    }

    /// Called once the app has launched: applies the saved appearance and follows
    /// macOS light/dark changes while "System" is chosen.
    func startAppearance() {
        loadModels()
        applyAppearance()
        appearanceObservation = NSApp.observe(\.effectiveAppearance) { _, _ in
            DispatchQueue.main.async { SessionStore.shared.refreshTerminalThemes() }
        }
    }

    private func applyAppearance() {
        NSApp?.appearance = appearance.nsAppearance
        refreshTerminalThemes()
    }

    func refreshTerminalThemes() {
        let theme = TerminalTheme.current
        sessions.forEach { $0.apply(theme) }
    }

    /// `open -a Orwick --args --open <folder>` starts a session there right away.
    func startFromLaunchArguments() {
        let arguments = CommandLine.arguments
        guard sessions.isEmpty, let index = arguments.firstIndex(of: "--open"), index + 1 < arguments.count else { return }
        var isFolder: ObjCBool = false
        let path = (arguments[index + 1] as NSString).expandingTildeInPath
        if FileManager.default.fileExists(atPath: path, isDirectory: &isFolder), isFolder.boolValue {
            start(in: URL(fileURLWithPath: path))
        }
    }

    var selected: Session? { sessions.first { $0.id == selection } }

    /// Home is the dashboard: shown when the app opens, and whenever no session or map is.
    var isHome: Bool { selected == nil && openMap == nil && !showingAnalytics }

    func goHome() {
        selection = nil
        openMap = nil
        showingAnalytics = false
    }

    func showAnalytics() {
        selection = nil
        openMap = nil
        showingAnalytics = true
    }

    /// Runs an analytics (`/do <skill> <action>`): in an open session that waits and knows
    /// that skill, else in a new one (a skill created after a session started is unknown to
    /// it). Its questions are asked in that session.
    func runAnalytics(_ entry: AnalyticsEntry) {
        send(entry.command, needs: entry.name)
    }

    private func send(_ command: String, needs skill: String) {
        if let waiting = sessions.first(where: { $0.running && $0.status?.isWaiting == true
                                                  && ($0.status?.skills ?? []).contains(skill) }) {
            selection = waiting.id
            waiting.run(command)
            return
        }
        guard let folder = sessionFolders.first else {
            chooseFolderAndStart()
            return
        }
        let session = Session(folder: folder, firstCommand: command)
        sessions.append(session)
        selection = session.id
    }

    /// Recent folders that still exist, newest first.
    var availableRecentFolders: [String] {
        recentFolders.filter { FileManager.default.fileExists(atPath: $0) }
    }

    /// The New Session panel (FolderPanel) is out.
    @Published var showingFolderPanel = false

    /// New Session: slides the folder panel out next to the sidebar (or back in).
    func chooseFolderAndStart() {
        folderPanelMode = .session
        withAnimation(.easeOut(duration: 0.18)) { showingFolderPanel.toggle() }
    }

    /// The macOS folder dialog, for what the panel can't do (a new folder, another disk).
    func chooseFolderInFinder() {
        let panel = NSOpenPanel()
        panel.title = "Choose a folder for Orwick"
        panel.message = "Orwick works only inside this folder. Every change still needs your approval."
        panel.prompt = "Start Orwick Here"
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.canCreateDirectories = true
        panel.allowsMultipleSelection = false
        if panel.runModal() == .OK, let url = panel.url {
            start(in: url)
        }
    }

    func refreshMaps() {
        let fresh = ServiceMap.all()
        if fresh != maps { maps = fresh }
    }

    func showMap(_ name: String) {
        selection = nil
        showingAnalytics = false
        openMap = name
    }

    /// Rebuilds `map` (same name): Orwick scans the service again from scratch and replaces the
    /// map's docs. The repos around it are found again; only folders elsewhere are passed.
    func rebuildMap(_ map: ServiceMap) {
        guard let folder = map.folder else { return }
        let around = folder.deletingLastPathComponent().path
        AppLog.log("Rebuild service map \(map.name)")
        startMap(in: folder, related: map.related.filter { $0.deletingLastPathComponent().path != around },
                 focus: "", name: map.name, rebuild: true)
    }

    /// Renames a service map: asks for the new name (lowercase, words joined by -) and moves
    /// its folder in the library. A name already in use is refused.
    func renameMap(_ map: ServiceMap) {
        NSApp.activate(ignoringOtherApps: true)
        let field = NSTextField(string: map.name)
        field.frame = NSRect(x: 0, y: 0, width: 280, height: 24)
        let alert = NSAlert()
        alert.messageText = "Rename the service map “\(map.name)”"
        alert.informativeText = "Lowercase, words joined by - (e.g. orders-refund)."
        alert.accessoryView = field
        alert.addButton(withTitle: "Rename")
        alert.addButton(withTitle: "Cancel")
        alert.window.initialFirstResponder = field
        guard alert.runModal() == .alertFirstButtonReturn else { return }
        if let problem = renameMap(map, to: field.stringValue) {
            let failed = NSAlert()
            failed.messageText = "The map could not be renamed"
            failed.informativeText = problem
            failed.runModal()
        }
    }

    /// Renames a service map to `newName` (made lowercase, words joined by -): moves its folder
    /// in the library. Returns the problem, or nil when renamed (or nothing to change).
    @discardableResult
    func renameMap(_ map: ServiceMap, to newName: String) -> String? {
        let name = ServiceMap.slug(newName)
        guard !name.isEmpty else { return "The name is empty." }
        guard name != map.name else { return nil }
        let target = map.library.deletingLastPathComponent().appendingPathComponent(name, isDirectory: true)
        if FileManager.default.fileExists(atPath: target.path) {
            return "A service map named “\(name)” already exists."
        }
        do {
            try FileManager.default.moveItem(at: map.library, to: target)
        } catch {
            return error.localizedDescription
        }
        if openMap == map.name { openMap = name }
        refreshMaps()
        return nil
    }

    /// Deletes a service map after asking: its folder goes to the Trash (so it can be put back).
    func deleteMap(_ map: ServiceMap) {
        NSApp.activate(ignoringOtherApps: true)
        let alert = NSAlert()
        alert.messageText = "Delete the service map “\(map.name)”?"
        alert.informativeText = "Its docs and diagrams go to the Trash. The service's code is not touched."
        alert.addButton(withTitle: "Delete")
        alert.addButton(withTitle: "Cancel")
        alert.buttons.first?.hasDestructiveAction = true
        guard alert.runModal() == .alertFirstButtonReturn else { return }
        do {
            try FileManager.default.trashItem(at: map.library, resultingItemURL: nil)
        } catch {
            let failed = NSAlert()
            failed.messageText = "The map could not be deleted"
            failed.informativeText = error.localizedDescription
            failed.runModal()
            return
        }
        if openMap == map.name { openMap = nil }
        refreshMaps()
    }

    /// The panel, opened for a new service map.
    func chooseFolderForMap() {
        folderPanelMode = .map
        withAnimation(.easeOut(duration: 0.18)) { showingFolderPanel = true }
    }

    /// A service map of `folder` named `name` (empty: Orwick asks for one), reading `related`
    /// too: in the session already open there (when it waits), else in a new session that runs
    /// `/map` as soon as Orwick is ready.
    func startMap(in folder: URL, related: [URL], focus: String, name: String = "", rebuild: Bool = false) {
        let folder = folder.standardizedFileURL
        let focus = focus.trimmingCharacters(in: .whitespacesAndNewlines)
        let name = ServiceMap.slug(name)
        let command = ("/map \(focus)" + (name.isEmpty ? "" : " --name \(name)") + (rebuild ? " --rebuild" : ""))
            .replacingOccurrences(of: "  ", with: " ")
        if let existing = sessions.first(where: { $0.folder.path == folder.path }) {
            let with = related.map { Session.shellQuoted($0.path).replacingOccurrences(of: "'", with: "") }
            existing.run(command + (with.isEmpty ? "" : " --with " + with.joined(separator: " ")))
            selection = existing.id
        } else {
            let session = Session(folder: folder, related: related, firstCommand: command)
            sessions.append(session)
            selection = session.id
        }
        remember(folder.path)
    }

    /// Switches to the session already open in this folder, or starts a new one.
    func start(in folder: URL) {
        let folder = folder.standardizedFileURL
        if let existing = sessions.first(where: { $0.folder.path == folder.path }) {
            selection = existing.id
            remember(folder.path)
            return
        }
        let session = Session(folder: folder)
        sessions.append(session)
        selection = session.id
        remember(folder.path)
    }

    func showSettings() {
        showingSettings = true
    }

    /// Recent folders and folders with open sessions: where the Tools tab looks for tools.
    var toolFolders: [URL] {
        var folders = sessions.map(\.folder)
        for path in availableRecentFolders where !folders.contains(URL(fileURLWithPath: path)) {
            folders.append(URL(fileURLWithPath: path))
        }
        return folders
    }

    /// Where saved sessions are looked for, by the sidebar and the dashboard alike: recent
    /// folders, folders with open sessions, the code root (Settings) and the folders in it.
    var sessionFolders: [URL] {
        var folders = availableRecentFolders.map { URL(fileURLWithPath: $0) }
        for session in sessions where !folders.contains(session.folder) { folders.append(session.folder) }
        let root = (try? OrwickConfig.load())?.codeRoot ?? ""
        let codeRoot = root.isEmpty ? nil : URL(fileURLWithPath: (root as NSString).expandingTildeInPath)
        return DashboardData.folders(known: folders, codeRoot: codeRoot)
    }

    /// Re-reads saved sessions in every folder Orwick is known to have been used in.
    func refreshRecentSessions() {
        let found = sessionFolders.flatMap(RecentSession.saved(in:))
            .sorted { $0.updatedAt > $1.updatedAt }
        let top = Array(found.prefix(Self.recentSessionLimit))
        if top != recentSessions { recentSessions = top }
    }

    /// Switches to the session if it's open here; otherwise opens it (`orwick --resume`).
    func open(_ recent: RecentSession) {
        if let open = sessions.first(where: { $0.folder == recent.folder && $0.sessionID == recent.id }) {
            selection = open.id
            if !open.running { open.restart() }
            return
        }
        let session = Session(folder: recent.folder, resume: recent.id)
        sessions.append(session)
        selection = session.id
        remember(recent.folder.path)
    }

    /// Deletes a saved session after you confirm: Orwick removes everything in it
    /// (`orwick --delete-session`). An open one is closed first.
    func delete(folder: URL, id: String, title: String, messages: Int, feature: String?) {
        let docs = SessionDocs.list(in: folder.appendingPathComponent(".orwick/sessions/\(id)"))
            .map { SessionDocs.label($0) }
        let alert = NSAlert()
        alert.alertStyle = .warning
        alert.messageText = "Delete the session “\(title)”?"
        alert.informativeText = [
            "\(folder.lastPathComponent) · \(messages) message\(messages == 1 ? "" : "s")" + (feature.map { " · \($0)" } ?? ""),
            docs.isEmpty ? nil : "Docs: \(docs.joined(separator: ", "))",
            "Its conversation, docs and logs are deleted. This can't be undone.",
        ].compactMap { $0 }.joined(separator: "\n")
        let delete = alert.addButton(withTitle: "Delete")
        delete.hasDestructiveAction = true
        alert.addButton(withTitle: "Cancel")
        guard alert.runModal() == .alertFirstButtonReturn else { return }

        if let open = sessions.first(where: { $0.folder == folder && $0.sessionID == id }) {
            close(open)            // Orwick saves and lets go of it; deleting waits for that
        }
        DispatchQueue.global(qos: .userInitiated).async {
            let process = Process()
            process.executableURL = URL(fileURLWithPath: Session.loginShell)
            process.arguments = ["-l", "-i", "-c", "orwick --delete-session \(Session.shellQuoted(id))"]
            process.currentDirectoryURL = folder
            let errors = Pipe()
            process.standardError = errors
            process.standardOutput = FileHandle.nullDevice
            try? process.run()
            process.waitUntilExit()
            let message = String(decoding: errors.fileHandleForReading.readDataToEndOfFile(), as: UTF8.self)
                .split(separator: "\n").last { $0.hasPrefix("Not deleted") }.map(String.init)
            DispatchQueue.main.async {
                self.refreshRecentSessions()
                self.savedSessionsVersion += 1
                if process.terminationStatus != 0 {
                    let failed = NSAlert()
                    failed.messageText = "The session wasn't deleted"
                    failed.informativeText = message ?? "Orwick couldn't delete it."
                    failed.runModal()
                }
            }
        }
    }

    func restart(_ session: Session) {
        session.restart()
        selection = session.id
    }

    func close(_ session: Session) {
        session.stop()
        guard let index = sessions.firstIndex(where: { $0.id == session.id }) else { return }
        sessions.remove(at: index)
        if selection == session.id {
            selection = sessions.isEmpty ? nil : sessions[min(index, sessions.count - 1)].id
        }
    }

    func closeAll() {
        sessions.forEach { $0.stop() }
    }

    func forget(_ path: String) {
        recentFolders.removeAll { $0 == path }
        UserDefaults.standard.set(recentFolders, forKey: Self.recentKey)
    }

    private func remember(_ path: String) {
        recentFolders.removeAll { $0 == path }
        recentFolders.insert(path, at: 0)
        recentFolders = Array(recentFolders.prefix(Self.recentLimit))
        UserDefaults.standard.set(recentFolders, forKey: Self.recentKey)
    }
}
