import SwiftUI

/// Everything the dashboard shows that comes from disk: every saved session in the folders
/// Orwick knows (recent folders and folders with an open session), with its docs and what it
/// has used so far. Read off the main thread; open sessions and service maps come live from
/// the store.
struct DashboardData: Equatable {
    struct Item: Identifiable, Equatable {
        let recent: RecentSession
        let docs: [String]        // the docs' labels, in tab order
        let tokens: Int           // new input + output, every backend
        let cost: Double          // at API prices
        var id: String { recent.key }
    }

    var items: [Item] = []

    var docs: Int { items.reduce(0) { $0 + $1.docs.count } }
    var tokens: Int { items.reduce(0) { $0 + $1.tokens } }
    var cost: Double { items.reduce(0) { $0 + $1.cost } }
    var tickets: [String] { Array(Set(items.compactMap(\.recent.feature))).sorted() }
    var folders: Int { Set(items.map(\.recent.folder)).count }

    /// Where sessions are looked for: the folders the app knows, plus the code root (Settings)
    /// and every folder in it that Orwick has been used in.
    static func folders(known: [URL], codeRoot: URL?) -> [URL] {
        var folders = known.map(\.standardizedFileURL)
        if let codeRoot {
            let inside = (try? FileManager.default.contentsOfDirectory(at: codeRoot, includingPropertiesForKeys: nil)) ?? []
            for folder in [codeRoot] + inside.sorted(by: { $0.path < $1.path })
            where FileManager.default.fileExists(atPath: folder.appendingPathComponent(".orwick/sessions").path)
                && !folders.contains(folder.standardizedFileURL) {
                folders.append(folder.standardizedFileURL)
            }
        }
        return folders
    }

    /// Saved sessions of `folders`, the most recently used first.
    static func load(folders: [URL]) -> DashboardData {
        let items = folders.flatMap(RecentSession.saved(in:)).sorted { $0.updatedAt > $1.updatedAt }.map { recent in
            let usage = OrwickStatus.read(from: recent.sessionFolder.appendingPathComponent("status.json"))?.usage.values
            return Item(recent: recent,
                        docs: SessionDocs.ordered(SessionDocs.list(in: recent.sessionFolder)).map(SessionDocs.label),
                        tokens: usage?.reduce(0) { $0 + $1.newInputTokens + $1.outputTokens } ?? 0,
                        cost: usage?.reduce(0) { $0 + ($1.costUsd ?? 0) } ?? 0)
        }
        return DashboardData(items: items)
    }
}

/// Home: the whole of Orwick on one screen. What is open now, every saved session with its
/// ticket and docs, the service maps, and what it all used. Shown when the app opens and
/// from the Home button; a click opens the session, its docs or the map.
struct DashboardView: View {
    @EnvironmentObject private var store: SessionStore
    @State private var data = DashboardData()
    @State private var loaded = false
    @State private var filter = ""

    private var shown: [DashboardData.Item] {
        let words = filter.lowercased().split(separator: " ")
        guard !words.isEmpty else { return data.items }
        return data.items.filter { item in
            let text = ([item.recent.title, item.recent.feature ?? "", item.recent.folder.lastPathComponent] + item.docs)
                .joined(separator: " ").lowercased()
            return words.allSatisfy { text.contains($0) }
        }
    }

    private var isEmpty: Bool { data.items.isEmpty && store.sessions.isEmpty && store.maps.isEmpty }

    var body: some View {
        Group {
            if !loaded {
                Color.clear
            } else if isEmpty {
                empty
            } else {
                filled
            }
        }
        .background(Color(nsColor: .windowBackgroundColor))
        .navigationTitle("Orwick")
        .navigationSubtitle("Home")
        .onAppear(perform: reload)
        .onReceive(Timer.publish(every: 2, on: .main, in: .common).autoconnect()) { _ in reload() }
        // At once when the app itself changed something: a session deleted, opened or closed.
        .onChange(of: store.savedSessionsVersion) { reload() }
        .onChange(of: store.recentSessions) { reload() }
        .onChange(of: store.sessions.count) { reload() }
    }

    private var filled: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 22) {
                header
                tiles
                if !store.sessions.isEmpty {
                    section("Open now", count: store.sessions.count) {
                        ForEach(store.sessions) { OpenSessionRow(session: $0) }
                    }
                }
                sessions
                if !store.maps.isEmpty {
                    section("Service maps", count: store.maps.count) {
                        ForEach(store.maps) { mapRow($0) }
                    }
                }
            }
            .padding(24)
            .frame(maxWidth: 1100, alignment: .leading)
            .frame(maxWidth: .infinity)
        }
    }

    private func reload() {
        let folders = store.sessionFolders
        DispatchQueue.global(qos: .userInitiated).async {
            let fresh = DashboardData.load(folders: folders)
            DispatchQueue.main.async {
                if fresh != data { data = fresh }
                loaded = true
            }
        }
    }

    // MARK: Parts

    private var header: some View {
        HStack(spacing: 14) {
            AvatarView().frame(width: 56, height: 56)
            VStack(alignment: .leading, spacing: 2) {
                Text("Orwick").font(.largeTitle.weight(.bold))
                Text("Everything Orwick is working on: your recent folders and the code root.")
                    .foregroundStyle(.secondary)
            }
            Spacer()
            Button {
                store.showAnalytics()
            } label: {
                Label("Analytics", systemImage: "chart.bar")
            }
            .controlSize(.large)
            .help("The issues reviewers raise again and again in pull requests")
            Button {
                store.chooseFolderForMap()
            } label: {
                Label("New Service Map", systemImage: "point.3.connected.trianglepath.dotted")
            }
            .controlSize(.large)
            Button {
                store.chooseFolderAndStart()
            } label: {
                Label("New Session", systemImage: "plus")
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.large)
        }
    }

    /// Nothing yet: no sessions, no docs, no maps. Orwick alone in the middle, with a nudge.
    private var empty: some View {
        VStack(spacing: 14) {
            AvatarView(winking: true)
                .frame(width: 200, height: 200)
            Text("Oops, nothing to show yet!")
                .font(.system(size: 30, weight: .bold, design: .rounded))
            Text("Orwick is polishing his helmet and waiting for work.\nStart a session, and your sessions, docs and service maps will gather here.")
                .font(.title3)
                .multilineTextAlignment(.center)
                .foregroundStyle(.secondary)
            Button {
                store.chooseFolderAndStart()
            } label: {
                Label("Start working", systemImage: "plus")
                    .padding(.horizontal, 8)
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.large)
            .keyboardShortcut(.defaultAction)
            .padding(.top, 6)
        }
        .padding(40)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private var tiles: some View {
        let working = store.sessions.filter { $0.running && $0.status?.isWaiting == false }.count
        return LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 12), count: 3), spacing: 12) {
            tile("Open now", "\(store.sessions.count)", working > 0 ? "\(working) working" : "none working", "bubble.left.and.bubble.right", .green)
            tile("Saved sessions", "\(data.items.count)", "in \(data.folders) folder\(data.folders == 1 ? "" : "s")", "tray.full", .blue)
            tile("Tickets", "\(data.tickets.count)", data.tickets.prefix(2).joined(separator: ", "), "ticket", .indigo)
            let withDocs = data.items.filter { !$0.docs.isEmpty }.count
            tile("Docs", "\(data.docs)", "in \(withDocs) session\(withDocs == 1 ? "" : "s")", "doc.text", .teal)
            tile("Service maps", "\(store.maps.count)", store.maps.first.map { "latest: \($0.name)" } ?? "", "point.3.connected.trianglepath.dotted", .orange)
            tile("Tokens used", Format.tokens(data.tokens),
                 data.cost > 0 ? String(format: "$%.2f at API prices", data.cost) : "new input + output", "sum", .pink)
        }
    }

    private func tile(_ title: String, _ value: String, _ detail: String, _ icon: String, _ color: Color) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Label(title, systemImage: icon)
                .font(.caption.weight(.semibold))
                .foregroundStyle(color)
            Text(value).font(.system(size: 28, weight: .bold, design: .rounded)).monospacedDigit()
            Text(detail.isEmpty ? " " : detail)
                .font(.caption).foregroundStyle(.secondary).lineLimit(1).truncationMode(.middle)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(12)
        .background(color.opacity(0.08), in: RoundedRectangle(cornerRadius: 10))
        .overlay(RoundedRectangle(cornerRadius: 10).strokeBorder(color.opacity(0.25), lineWidth: 1))
    }

    private func section<Content: View>(_ title: String, count: Int, @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 6) {
                Text(title).font(.title3.weight(.semibold))
                Text("\(count)").font(.callout.monospacedDigit()).foregroundStyle(.secondary)
            }
            VStack(spacing: 0) { content() }
                .background(Color(nsColor: .controlBackgroundColor))
                .clipShape(RoundedRectangle(cornerRadius: 10))
                .overlay(RoundedRectangle(cornerRadius: 10).strokeBorder(Color.secondary.opacity(0.2), lineWidth: 1))
        }
    }

    private var sessions: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 6) {
                Text("Sessions").font(.title3.weight(.semibold))
                Text(filter.isEmpty ? "\(data.items.count)" : "\(shown.count) of \(data.items.count)")
                    .font(.callout.monospacedDigit()).foregroundStyle(.secondary)
                Spacer()
                TextField("Filter by title, ticket, folder or doc", text: $filter)
                    .textFieldStyle(.roundedBorder)
                    .frame(width: 280)
            }
            VStack(spacing: 0) {
                if shown.isEmpty {
                    Text(data.items.isEmpty ? "No saved sessions yet." : "No session matches.")
                        .foregroundStyle(.secondary)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(12)
                }
                ForEach(shown) { sessionRow($0) }
            }
            .background(Color(nsColor: .controlBackgroundColor))
            .clipShape(RoundedRectangle(cornerRadius: 10))
            .overlay(RoundedRectangle(cornerRadius: 10).strokeBorder(Color.secondary.opacity(0.2), lineWidth: 1))
        }
    }

    private func sessionRow(_ item: DashboardData.Item) -> some View {
        let recent = item.recent
        let isOpen = store.sessions.contains { $0.folder == recent.folder && $0.sessionID == recent.id }
        return VStack(spacing: 0) {
            HStack(alignment: .firstTextBaseline, spacing: 10) {
                Circle().fill(isOpen ? Color.green : Color.secondary.opacity(0.3)).frame(width: 7, height: 7)
                    .help(isOpen ? "Open now" : "Saved")
                VStack(alignment: .leading, spacing: 4) {
                    HStack(spacing: 6) {
                        Text(recent.title).fontWeight(.medium).lineLimit(1)
                        if let feature = recent.feature { Badge(text: feature, color: .indigo) }
                    }
                    Text("\(recent.folder.lastPathComponent) · \(recent.messages) message\(recent.messages == 1 ? "" : "s")"
                         + (item.tokens > 0 ? " · \(Format.tokens(item.tokens)) tokens" : ""))
                        .font(.caption).foregroundStyle(.secondary).lineLimit(1)
                    if !item.docs.isEmpty {
                        HStack(spacing: 4) {
                            ForEach(item.docs, id: \.self) { doc in
                                Button { store.showDocs(recent) } label: { Badge(text: doc, color: .teal, icon: "doc.text") }
                                    .buttonStyle(.plain)
                                    .help("Open this session with its docs")
                            }
                        }
                    }
                }
                Spacer(minLength: 8)
                Text(recent.updatedAt.formatted(.relative(presentation: .named)))
                    .font(.caption).foregroundStyle(.secondary)
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
            .contentShape(Rectangle())
            .onTapGesture { store.open(recent) }
            .contextMenu {
                Button("Continue Session") { store.open(recent) }
                Button("Show in Finder") { NSWorkspace.shared.activateFileViewerSelecting([recent.sessionFolder]) }
                Divider()
                Button("Delete Session…", role: .destructive) {
                    store.delete(folder: recent.folder, id: recent.id, title: recent.title,
                                 messages: recent.messages, feature: recent.feature)
                }
            }
            .help("Continue “\(recent.title)” in \((recent.folder.path as NSString).abbreviatingWithTildeInPath)")
            Divider().padding(.leading, 29)
        }
    }

    private func mapRow(_ map: ServiceMap) -> some View {
        VStack(spacing: 0) {
            HStack(spacing: 10) {
                Image(systemName: "point.3.connected.trianglepath.dotted").foregroundStyle(.orange)
                VStack(alignment: .leading, spacing: 2) {
                    Text(map.name).fontWeight(.medium).lineLimit(1)
                    Text(([map.question.map { "“\($0)”" }, "\(map.docs.count) docs",
                           map.related.isEmpty ? nil : "with " + map.related.map(\.lastPathComponent).joined(separator: ", ")]
                        .compactMap { $0 }).joined(separator: " · "))
                        .font(.caption).foregroundStyle(.secondary).lineLimit(1)
                }
                Spacer(minLength: 8)
                Text(map.updatedAt.formatted(.relative(presentation: .named)))
                    .font(.caption).foregroundStyle(.secondary)
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
            .contentShape(Rectangle())
            .onTapGesture { store.showMap(map.name) }
            .contextMenu {
                Button("Open Map") { store.showMap(map.name) }
                Button("Rename Map…") { store.renameMap(map) }
                Button("Show in Finder") { NSWorkspace.shared.activateFileViewerSelecting([map.library]) }
                Divider()
                Button("Delete Map…", role: .destructive) { store.deleteMap(map) }
            }
            .help("Open the service map \(map.name)")
            Divider().padding(.leading, 38)
        }
    }
}

/// An open session on the dashboard: what Orwick is doing in it right now.
private struct OpenSessionRow: View {
    @EnvironmentObject private var store: SessionStore
    @ObservedObject var session: Session

    private var state: (text: String, color: Color) {
        if !session.running { return ("Ended", .secondary) }
        if session.status?.isWaiting == false { return ("Working: \(session.status?.activity ?? "answering")", .orange) }
        return ("Waiting for you", .green)
    }

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 10) {
                Circle().fill(state.color).frame(width: 8, height: 8)
                VStack(alignment: .leading, spacing: 2) {
                    HStack(spacing: 6) {
                        Text(session.name).fontWeight(.medium).lineLimit(1)
                        if let feature = session.status?.feature { Badge(text: feature, color: .indigo) }
                    }
                    Text("\(session.shortPath) · \(state.text)")
                        .font(.caption).foregroundStyle(.secondary).lineLimit(1).truncationMode(.middle)
                }
                Spacer(minLength: 8)
                if let docs = session.status?.docs, !docs.isEmpty {
                    Button { store.showDocs(session) } label: { Badge(text: "\(docs.count) docs", color: .teal, icon: "doc.text") }
                        .buttonStyle(.plain)
                        .help("Open this session with its docs")
                }
                if let level = session.status?.conversation, session.running {
                    Badge(text: "\(level.percent)% full", color: level.isSummarizing ? .red : level.isWarning ? .orange : .secondary)
                        .help("This conversation is at \(level.percent)% of its budget")
                }
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
            .contentShape(Rectangle())
            .onTapGesture { store.selection = session.id }
            .help("Go to this session")
            Divider().padding(.leading, 30)
        }
    }
}

private struct Badge: View {
    let text: String
    let color: Color
    var icon: String?

    var body: some View {
        HStack(spacing: 3) {
            if let icon { Image(systemName: icon) }
            Text(text).lineLimit(1)
        }
        .font(.caption2.weight(.medium))
        .foregroundStyle(color)
        .padding(.horizontal, 6)
        .padding(.vertical, 2)
        .background(color.opacity(0.12), in: Capsule())
    }
}
