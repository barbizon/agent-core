import SwiftUI

/// Review analytics, as Orwick keeps it in `~/.orwick/analytics/` (Orwick's
/// src/agent_core/tools/analytics.py): the issues reviewers raise again and again in
/// pull requests. Orwick writes the patterns and their counts; the app only writes
/// `decisions.json`: which patterns count ("on") and which don't ("muted").
struct ReviewAnalytics: Equatable {
    struct Pattern: Identifiable, Equatable {
        let id: String
        let name: String
        let rule: String
        let example: String
        let detect: String
        let item: String          // the code-check item it belongs to, or ""
        let count: Int            // comments
        let prs: Int              // pull requests
        let repos: [String]
        let reviewers: Int
        let lastSeen: String      // 2026-10-05, or ""
    }

    enum Decision: String, CaseIterable {
        case suggested, on, muted

        var title: String {
            switch self {
            case .suggested: return "Suggested"
            case .on: return "On"
            case .muted: return "Muted"
            }
        }
    }

    var patterns: [Pattern] = []
    var decisions: [String: String] = [:]
    var repos: [String] = []
    var comments = 0
    var waiting = 0               // comments read but not grouped yet

    static var folder: URL {
        FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".orwick/analytics", isDirectory: true)
    }

    func decision(_ pattern: Pattern) -> Decision { Decision(rawValue: decisions[pattern.id] ?? "") ?? .suggested }

    var on: Int { patterns.filter { decision($0) == .on }.count }

    private static func json(_ name: String, in folder: URL) -> [String: Any] {
        guard let data = try? Data(contentsOf: folder.appendingPathComponent(name)),
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return [:] }
        return object
    }

    static func load(from folder: URL = folder) -> ReviewAnalytics {
        let rows = json("patterns.json", in: folder)["patterns"] as? [[String: Any]] ?? []
        let comments = json("comments.json", in: folder)["comments"] as? [[String: Any]] ?? []
        return ReviewAnalytics(
            patterns: rows.compactMap { row in
                guard let id = row["id"] as? String, let name = row["name"] as? String else { return nil }
                return Pattern(id: id, name: name, rule: row["rule"] as? String ?? "",
                               example: row["example"] as? String ?? "", detect: row["detect"] as? String ?? "",
                               item: row["item"] as? String ?? "", count: row["count"] as? Int ?? 0,
                               prs: row["prs"] as? Int ?? 0, repos: row["repos"] as? [String] ?? [],
                               reviewers: row["reviewers"] as? Int ?? 0, lastSeen: row["last_seen"] as? String ?? "")
            },
            decisions: json("decisions.json", in: folder) as? [String: String] ?? [:],
            repos: ((json("state.json", in: folder)["repos"] as? [String: Any]) ?? [:]).keys.sorted(),
            comments: comments.count,
            waiting: comments.filter { $0["pattern"] is NSNull }.count)
    }

    /// Saves your decision on one pattern ("suggested" removes it). Orwick reads the file
    /// when a session starts and before each button, and rewrites the known-issues skill.
    static func decide(_ id: String, _ decision: Decision, in folder: URL = folder) {
        var decisions = json("decisions.json", in: folder) as? [String: String] ?? [:]
        decisions[id] = decision == .suggested ? nil : decision.rawValue
        try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        if let data = try? JSONSerialization.data(withJSONObject: decisions, options: [.prettyPrinted, .sortedKeys]) {
            try? data.write(to: folder.appendingPathComponent("decisions.json"), options: .atomic)
        }
    }
}

/// One analytics: a skill with `kind: analytics` in its header (Orwick's own "Common review
/// issues", or one you created: `~/.orwick/skills/<name>/`), and its latest report
/// (`~/.orwick/analytics/<name>/report.md`), written at the end of a run.
struct AnalyticsEntry: Identifiable, Equatable {
    let name: String              // the skill's name
    let title: String
    let description: String
    let action: String            // the button that runs it: /do <name> <action>
    let isYours: Bool             // one you created: it can be edited and deleted
    var wanted = ""               // what you asked for, in your words (analytics.json)
    var report = ""
    var updatedAt: Date?
    var runs = 0

    var id: String { name }
    var command: String { "/do \(name) \(action)" }

    static let reviewIssues = AnalyticsEntry(
        name: "review-analytics", title: "Common review issues",
        description: "The issues reviewers raise again and again in pull requests.", action: "learn", isYours: false)

    static var skillsFolder: URL {
        FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".orwick/skills", isDirectory: true)
    }

    /// Orwick's own first, then yours by title.
    static func all(skills: URL = skillsFolder, store: URL = ReviewAnalytics.folder) -> [AnalyticsEntry] {
        let folders = (try? FileManager.default.contentsOfDirectory(at: skills, includingPropertiesForKeys: nil)) ?? []
        let yours: [AnalyticsEntry] = folders.compactMap { folder in
            guard let text = try? String(contentsOf: folder.appendingPathComponent("SKILL.md"), encoding: .utf8),
                  text.hasPrefix("---") else { return nil }
            var header: [String: String] = [:]
            for line in text.components(separatedBy: "\n").dropFirst() {
                if line.trimmingCharacters(in: .whitespaces) == "---" { break }
                guard let colon = line.firstIndex(of: ":") else { continue }
                header[String(line[..<colon]).trimmingCharacters(in: .whitespaces)] =
                    String(line[line.index(after: colon)...]).trimmingCharacters(in: .whitespaces)
            }
            guard header["kind"] == "analytics", let name = header["name"], name == folder.lastPathComponent else { return nil }
            var entry = AnalyticsEntry(name: name, title: header["title"] ?? name,
                                       description: header["description"] ?? "", action: "run", isYours: true)
            if let data = try? Data(contentsOf: folder.appendingPathComponent("analytics.json")),
               let definition = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
                entry.wanted = definition["wanted"] as? String ?? ""
            }
            let home = store.appendingPathComponent(name, isDirectory: true)
            entry.report = (try? String(contentsOf: home.appendingPathComponent("report.md"), encoding: .utf8)) ?? ""
            if let data = try? Data(contentsOf: home.appendingPathComponent("meta.json")),
               let meta = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
                entry.updatedAt = (meta["updated_at"] as? Double).map(Date.init(timeIntervalSince1970:))
                entry.runs = meta["runs"] as? Int ?? 0
            }
            return entry
        }
        return [reviewIssues] + yours.sorted { $0.title.localizedCaseInsensitiveCompare($1.title) == .orderedAscending }
    }

    /// Saves an analytics from its title and what you want to get (`orwick
    /// --create-analytics`: Orwick writes the skill; nothing is worked out yet). `name`: the
    /// one being edited. Calls back on the main thread with its name, or the problem.
    static func save(title: String, wanted: String, name: String? = nil,
                     done: @escaping (_ name: String?, _ problem: String?) -> Void) {
        DispatchQueue.global(qos: .userInitiated).async {
            let process = Process()
            process.executableURL = URL(fileURLWithPath: Session.loginShell)
            process.arguments = ["-l", "-i", "-c",
                                 "orwick --create-analytics \(Session.shellQuoted(title)) --wanted \(Session.shellQuoted(wanted))"
                                 + (name.map { " --analytics-name \(Session.shellQuoted($0))" } ?? "")]
            process.currentDirectoryURL = FileManager.default.homeDirectoryForCurrentUser
            let output = Pipe(), errors = Pipe()
            process.standardOutput = output
            process.standardError = errors
            try? process.run()
            let out = output.fileHandleForReading.readDataToEndOfFile()
            let problem = String(decoding: errors.fileHandleForReading.readDataToEndOfFile(), as: UTF8.self)
                .split(separator: "\n").last { $0.hasPrefix("Not saved") }.map(String.init)
            process.waitUntilExit()
            let saved = String(decoding: out, as: UTF8.self).split(separator: "\n").reversed().lazy
                .compactMap { try? JSONSerialization.jsonObject(with: Data($0.utf8)) as? [String: Any] }
                .compactMap { $0["saved"] as? String }.first
            DispatchQueue.main.async {
                done(saved, saved == nil ? (problem ?? "Orwick couldn't save it. Is the orwick command installed?") : nil)
            }
        }
    }

    /// Deletes an analytics of yours: its skill and its reports go to the Trash.
    static func delete(_ entry: AnalyticsEntry, skills: URL = skillsFolder, store: URL = ReviewAnalytics.folder) {
        guard entry.isYours else { return }
        try? FileManager.default.trashItem(at: skills.appendingPathComponent(entry.name), resultingItemURL: nil)
        try? FileManager.default.trashItem(at: store.appendingPathComponent(entry.name), resultingItemURL: nil)
    }
}

/// The Analytics screen: your analytics by title, each with a Run button and its latest
/// report, and Create analytics for a new one. "Common review issues" is Orwick's own: there
/// you switch on the issues that count, and Review code and Code Scoring look for them.
struct AnalyticsView: View {
    @EnvironmentObject private var store: SessionStore
    @State private var entries: [AnalyticsEntry] = [AnalyticsEntry.reviewIssues]
    @State private var selected = AnalyticsEntry.reviewIssues.name
    @State private var creating = false
    @State private var editing: String?          // the analytics being edited in the sheet; nil: a new one
    @State private var newTitle = ""
    @State private var newWanted = ""
    @State private var saving = false
    @State private var problem: String?

    private var current: AnalyticsEntry { entries.first { $0.name == selected } ?? entries[0] }

    var body: some View {
        VStack(spacing: 0) {
            bar
            Divider()
            if current.isYours {
                ReportView(entry: current) { edit(current) }
            } else {
                ReviewIssuesView()
            }
        }
        .background(Color(nsColor: .windowBackgroundColor))
        .navigationTitle("Orwick")
        .navigationSubtitle("Analytics")
        .onAppear(perform: reload)
        .onReceive(Timer.publish(every: 2, on: .main, in: .common).autoconnect()) { _ in reload() }
        .sheet(isPresented: $creating) { createSheet }
    }

    private func reload() {
        DispatchQueue.global(qos: .userInitiated).async {
            let fresh = AnalyticsEntry.all()
            DispatchQueue.main.async { if fresh != entries { entries = fresh } }
        }
    }

    /// The analytics by title, and Create analytics.
    private var bar: some View {
        HStack(spacing: 8) {
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 4) {
                    ForEach(entries) { entry in
                        Button {
                            selected = entry.name
                        } label: {
                            Text(entry.title)
                                .font(.callout.weight(current.name == entry.name ? .semibold : .regular))
                                .padding(.horizontal, 12)
                                .padding(.vertical, 6)
                                .background(current.name == entry.name ? Color.accentColor.opacity(0.18) : Color.secondary.opacity(0.08),
                                            in: RoundedRectangle(cornerRadius: 7))
                        }
                        .buttonStyle(.plain)
                        .help(entry.description)
                        .contextMenu {
                            if entry.isYours {
                                Button("Edit…") { edit(entry) }
                                Button("Show Its Skill in Finder") {
                                    NSWorkspace.shared.activateFileViewerSelecting(
                                        [AnalyticsEntry.skillsFolder.appendingPathComponent("\(entry.name)/SKILL.md")])
                                }
                                Divider()
                                Button("Delete Analytics…", role: .destructive) { delete(entry) }
                            }
                        }
                    }
                }
            }
            Spacer(minLength: 8)
            Button {
                editing = nil
                newTitle = ""
                newWanted = ""
                problem = nil
                creating = true
            } label: {
                Label("Create analytics", systemImage: "plus")
            }
            .help("A new analytics: give it a title and say what you want to get")
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
    }

    private func edit(_ entry: AnalyticsEntry) {
        editing = entry.name
        newTitle = entry.title
        newWanted = entry.wanted
        problem = nil
        creating = true
    }

    private func save() {
        saving = true
        AnalyticsEntry.save(title: newTitle, wanted: newWanted, name: editing) { name, trouble in
            saving = false
            problem = trouble
            guard let name else { return }
            creating = false
            entries = AnalyticsEntry.all()
            selected = name
        }
    }

    private func delete(_ entry: AnalyticsEntry) {
        let alert = NSAlert()
        alert.messageText = "Delete the analytics “\(entry.title)”?"
        alert.informativeText = "Its skill and its reports go to the Trash."
        alert.addButton(withTitle: "Delete").hasDestructiveAction = true
        alert.addButton(withTitle: "Cancel")
        guard alert.runModal() == .alertFirstButtonReturn else { return }
        AnalyticsEntry.delete(entry)
        selected = AnalyticsEntry.reviewIssues.name
        reload()
    }

    private var createSheet: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(editing == nil ? "Create analytics" : "Edit analytics").font(.title2.weight(.bold))
            Text("It is saved to your list at once. Nothing runs now: press Run on it when you want the result.")
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            Text("Title").font(.callout.weight(.semibold))
            TextField("e.g. Slow pull requests", text: $newTitle)
                .textFieldStyle(.roundedBorder)
            Text("What do you want to get?").font(.callout.weight(.semibold))
            TextEditor(text: $newWanted)
                .font(.body)
                .frame(height: 150)
                .overlay(RoundedRectangle(cornerRadius: 6).strokeBorder(Color.secondary.opacity(0.3), lineWidth: 1))
            Text("In your own words: what to look at, what to count, how you want to see it. Orwick follows this text each time it runs.")
                .font(.caption).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            if let problem {
                Text(problem).font(.callout).foregroundStyle(.red).fixedSize(horizontal: false, vertical: true)
            }
            HStack {
                if saving { ProgressView().controlSize(.small) }
                Spacer()
                Button("Cancel") { creating = false }
                    .keyboardShortcut(.cancelAction)
                Button("Save") { save() }
                    .buttonStyle(.borderedProminent)
                    .keyboardShortcut(.defaultAction)
                    .disabled(saving || newTitle.trimmingCharacters(in: .whitespaces).count < 2
                              || newWanted.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }
        .padding(22)
        .frame(width: 540)
    }
}

/// One of your analytics: what it is, Run, and its latest report.
private struct ReportView: View {
    @EnvironmentObject private var store: SessionStore
    let entry: AnalyticsEntry
    let edit: () -> Void

    private var runButton: some View {
        Button {
            store.runAnalytics(entry)
        } label: {
            Label(entry.report.isEmpty ? "Run" : "Run again", systemImage: "play.fill")
        }
        .buttonStyle(.borderedProminent)
        .controlSize(.large)
        .help("Orwick works it out in a session (it asks its questions there) and saves the report here")
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                HStack(alignment: .top, spacing: 14) {
                    VStack(alignment: .leading, spacing: 4) {
                        Text(entry.title).font(.largeTitle.weight(.bold))
                        if let updated = entry.updatedAt {
                            Text("Last run \(updated.formatted(.relative(presentation: .named))) · \(entry.runs) run\(entry.runs == 1 ? "" : "s")")
                                .font(.callout).foregroundStyle(.secondary)
                        }
                    }
                    Spacer()
                    Button("Edit…", action: edit)
                        .controlSize(.large)
                    runButton
                }
                if !entry.wanted.isEmpty {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("WHAT YOU ASKED FOR").font(.caption2.weight(.bold)).foregroundStyle(.secondary)
                        Text(entry.wanted).textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
                    }
                    .padding(12)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(Color.accentColor.opacity(0.07), in: RoundedRectangle(cornerRadius: 10))
                }
                if entry.report.isEmpty {
                    VStack(spacing: 10) {
                        Image(systemName: "chart.bar.doc.horizontal").font(.system(size: 48)).foregroundStyle(.secondary)
                        Text("Not run yet").font(.title2.weight(.semibold))
                        Text("Press Run. Orwick works it out in a session and the report appears here.")
                            .foregroundStyle(.secondary)
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 60)
                } else {
                    MarkdownText(text: entry.report)
                        .padding(18)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(Color(nsColor: .textBackgroundColor))
                        .clipShape(RoundedRectangle(cornerRadius: 10))
                        .overlay(RoundedRectangle(cornerRadius: 10).strokeBorder(Color.secondary.opacity(0.2), lineWidth: 1))
                }
            }
            .padding(24)
            .frame(maxWidth: 1100, alignment: .leading)
            .frame(maxWidth: .infinity)
        }
    }
}

/// "Common review issues": the patterns, the most widespread first. You switch on the ones
/// that count; Review code and Code Scoring then look for them.
struct ReviewIssuesView: View {
    @EnvironmentObject private var store: SessionStore
    var folder = ReviewAnalytics.folder
    @State private var data = ReviewAnalytics()
    @State private var loaded = false
    @State private var open: Set<String> = []
    @State private var show: ReviewAnalytics.Decision?
    private let ticker = Timer.publish(every: 2, on: .main, in: .common).autoconnect()

    private var shown: [ReviewAnalytics.Pattern] {
        guard let show else { return data.patterns }
        return data.patterns.filter { data.decision($0) == show }
    }

    var body: some View {
        Group {
            if !loaded {
                Color.clear
            } else if data.patterns.isEmpty {
                empty
            } else {
                list
            }
        }
        .onAppear(perform: reload)
        .onReceive(ticker) { _ in reload() }
    }

    private func reload() {
        DispatchQueue.global(qos: .userInitiated).async {
            let fresh = ReviewAnalytics.load(from: folder)
            DispatchQueue.main.async {
                if fresh != data { data = fresh }
                loaded = true
            }
        }
    }

    private var learnButton: some View {
        Button {
            store.runAnalytics(AnalyticsEntry.reviewIssues)
        } label: {
            Label(data.repos.isEmpty ? "Learn from PRs" : "Refresh from PRs", systemImage: "arrow.triangle.2.circlepath")
        }
        .buttonStyle(.borderedProminent)
        .controlSize(.large)
        .help("Orwick reads the review comments of pull requests changed since last time and groups them. "
              + "It asks which repos the first time.")
    }

    private var empty: some View {
        VStack(spacing: 14) {
            Image(systemName: "chart.bar.xaxis")
                .font(.system(size: 64))
                .foregroundStyle(.secondary)
            Text(data.waiting > 0 ? "Comments are in, not grouped yet" : "No review analytics yet")
                .font(.system(size: 26, weight: .bold, design: .rounded))
            Text(data.waiting > 0
                 ? "\(data.waiting) review comments wait to be grouped. Press the button to finish."
                 : "Orwick can read the review comments of your past pull requests\nand find the issues reviewers raise again and again.")
                .font(.title3)
                .multilineTextAlignment(.center)
                .foregroundStyle(.secondary)
            learnButton.padding(.top, 6)
        }
        .padding(40)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private var list: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                HStack(alignment: .top, spacing: 14) {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("Common review issues").font(.largeTitle.weight(.bold))
                        Text("\(data.patterns.count) issues from \(data.comments) review comments in "
                             + (data.repos.isEmpty ? "your repos" : data.repos.map { $0.components(separatedBy: "/").last ?? $0 }.joined(separator: ", "))
                             + ". \(data.on) switched on: Review code and Code Scoring look for those.")
                            .foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                        if data.waiting > 0 {
                            Text("\(data.waiting) comment\(data.waiting == 1 ? " is" : "s are") read but not grouped yet: press Refresh from PRs.")
                                .font(.callout).foregroundStyle(.orange)
                        }
                    }
                    Spacer()
                    learnButton
                }
                Picker("Show", selection: $show) {
                    Text("All (\(data.patterns.count))").tag(ReviewAnalytics.Decision?.none)
                    ForEach(ReviewAnalytics.Decision.allCases, id: \.self) { decision in
                        Text("\(decision.title) (\(data.patterns.filter { data.decision($0) == decision }.count))")
                            .tag(ReviewAnalytics.Decision?.some(decision))
                    }
                }
                .pickerStyle(.segmented)
                .labelsHidden()
                .frame(maxWidth: 520)

                VStack(spacing: 0) {
                    if shown.isEmpty {
                        Text("None here.").foregroundStyle(.secondary)
                            .frame(maxWidth: .infinity, alignment: .leading).padding(12)
                    }
                    ForEach(Array(shown.enumerated()), id: \.element.id) { index, pattern in
                        row(pattern, rank: (data.patterns.firstIndex(of: pattern) ?? index) + 1)
                    }
                }
                .background(Color(nsColor: .controlBackgroundColor))
                .clipShape(RoundedRectangle(cornerRadius: 10))
                .overlay(RoundedRectangle(cornerRadius: 10).strokeBorder(Color.secondary.opacity(0.2), lineWidth: 1))
            }
            .padding(24)
            .frame(maxWidth: 1100, alignment: .leading)
            .frame(maxWidth: .infinity)
        }
    }

    private func row(_ pattern: ReviewAnalytics.Pattern, rank: Int) -> some View {
        let decision = data.decision(pattern)
        let top = max(data.patterns.first?.prs ?? 1, 1)
        return VStack(spacing: 0) {
            HStack(alignment: .top, spacing: 12) {
                Text("\(rank)").font(.callout.monospacedDigit()).foregroundStyle(.secondary).frame(width: 22, alignment: .trailing)
                VStack(alignment: .leading, spacing: 5) {
                    HStack(spacing: 6) {
                        Text(pattern.name).fontWeight(.semibold)
                            .foregroundStyle(decision == .muted ? .secondary : .primary)
                        if !pattern.item.isEmpty {
                            Text(pattern.item).font(.caption2.weight(.medium)).foregroundStyle(.indigo)
                                .padding(.horizontal, 6).padding(.vertical, 2)
                                .background(Color.indigo.opacity(0.12), in: Capsule())
                                .help("The code-check item this belongs to")
                        }
                    }
                    Text(pattern.rule).font(.callout).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                    HStack(spacing: 8) {
                        // How widespread, against the most widespread one.
                        GeometryReader { geometry in
                            ZStack(alignment: .leading) {
                                Capsule().fill(Color.secondary.opacity(0.15))
                                Capsule().fill(decision == .on ? Color.green : Color.accentColor.opacity(0.7))
                                    .frame(width: geometry.size.width * CGFloat(pattern.prs) / CGFloat(top))
                            }
                        }
                        .frame(width: 120, height: 6)
                        Text("in \(pattern.prs) PR\(pattern.prs == 1 ? "" : "s") · \(pattern.count) comment\(pattern.count == 1 ? "" : "s")"
                             + (pattern.repos.isEmpty ? "" : " · \(pattern.repos.joined(separator: ", "))")
                             + (pattern.lastSeen.isEmpty ? "" : " · last \(pattern.lastSeen)"))
                            .font(.caption).foregroundStyle(.secondary).lineLimit(1)
                    }
                    if open.contains(pattern.id) {
                        VStack(alignment: .leading, spacing: 4) {
                            if !pattern.detect.isEmpty { detail("How to spot it", pattern.detect) }
                            if !pattern.example.isEmpty { detail("Example", pattern.example) }
                        }
                        .padding(.top, 2)
                    }
                }
                Spacer(minLength: 8)
                Picker("", selection: Binding(get: { decision }, set: { new in
                    ReviewAnalytics.decide(pattern.id, new, in: folder)
                    data.decisions[pattern.id] = new == .suggested ? nil : new.rawValue
                })) {
                    ForEach(ReviewAnalytics.Decision.allCases, id: \.self) { Text($0.title).tag($0) }
                }
                .pickerStyle(.segmented)
                .labelsHidden()
                .frame(width: 230)
                .help("On: reviews look for it. Muted: ignored. Suggested: not decided yet, ignored for now.")
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 10)
            .contentShape(Rectangle())
            .onTapGesture {
                if open.contains(pattern.id) { open.remove(pattern.id) } else { open.insert(pattern.id) }
            }
            Divider().padding(.leading, 46)
        }
    }

    private func detail(_ title: String, _ text: String) -> some View {
        VStack(alignment: .leading, spacing: 1) {
            Text(title.uppercased()).font(.caption2.weight(.bold)).foregroundStyle(.secondary)
            Text(text).font(.callout.monospaced()).textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}
