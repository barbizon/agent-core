import SwiftUI

struct ContentView: View {
    @EnvironmentObject private var store: SessionStore

    var body: some View {
        NavigationSplitView {
            Sidebar()
                .navigationSplitViewColumnWidth(min: 200, ideal: 240, max: 360)
        } detail: {
            ZStack(alignment: .leading) {
                if store.showingAnalytics {
                    AnalyticsView()
                } else if let name = store.openMap, let map = store.maps.first(where: { $0.name == name }) {
                    ServiceMapView(map: map)
                } else if let session = store.selected {
                    SessionDetail(session: session)
                } else {
                    DashboardView()
                }
                if store.showingFolderPanel {
                    // A click beside the panel closes it.
                    Color.black.opacity(0.08)
                        .contentShape(Rectangle())
                        .onTapGesture { withAnimation(.easeOut(duration: 0.18)) { store.showingFolderPanel = false } }
                        .transition(.opacity)
                    FolderPanel()
                        .transition(.move(edge: .leading).combined(with: .opacity))
                }
            }
        }
        .onAppear { store.startFromLaunchArguments() }
        .sheet(item: $store.crashReport) { report in
            CrashReportView(report: report)
        }
        .sheet(isPresented: $store.showingSettings) {
            SettingsView(folder: store.selected?.folder, toolFolders: store.toolFolders)
                .environmentObject(store)
        }
    }
}

// MARK: Sidebar

private struct Sidebar: View {
    @EnvironmentObject private var store: SessionStore

    var body: some View {
        List(selection: $store.selection) {
            Section {
                ForEach(store.sessions) { session in
                    SessionRow(session: session) {
                        store.toggleDocs(session)
                    }
                        .tag(session.id)
                        .contextMenu {
                            Button("Restart Orwick") { store.restart(session) }
                            if session.hasSavedConversation {
                                Button("Close and Delete Session…", role: .destructive) {
                                    store.delete(folder: session.folder, id: session.sessionID, title: session.name,
                                                 messages: session.status?.session.messages ?? 0,
                                                 feature: session.status?.feature)
                                }
                            }
                            Button("Show in Finder") { NSWorkspace.shared.activateFileViewerSelecting([session.folder]) }
                            Divider()
                            Button("Close Session") { store.close(session) }
                        }
                }
            } header: {
                Text("Sessions")
            }

            if !store.maps.isEmpty {
                Section("Service maps") {
                    ForEach(store.maps) { map in
                        Button {
                            store.showMap(map.name)
                        } label: {
                            HStack(spacing: 8) {
                                Image(systemName: "point.3.connected.trianglepath.dotted")
                                    .foregroundStyle(.orange)
                                VStack(alignment: .leading, spacing: 1) {
                                    Text(map.name).lineLimit(1)
                                    Text(map.related.isEmpty ? "\(map.docs.count) docs"
                                         : "with " + map.related.map(\.lastPathComponent).joined(separator: ", "))
                                        .font(.caption).foregroundStyle(.secondary).lineLimit(1)
                                }
                                Spacer(minLength: 0)
                            }
                            .padding(.vertical, 2)
                            .padding(.horizontal, 4)
                            .background(store.openMap == map.name ? Color.accentColor.opacity(0.18) : .clear,
                                        in: RoundedRectangle(cornerRadius: 6))
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                        .help(map.question.map { "“\($0)”" } ?? "Service map of \(map.name)")
                        .contextMenu {
                            if map.folder != nil {
                                Button("Rebuild Map") { store.rebuildMap(map) }
                            }
                            Button("Rename Map…") { store.renameMap(map) }
                            Button("Show in Finder") { NSWorkspace.shared.activateFileViewerSelecting([map.library]) }
                            Divider()
                            Button("Delete Map…", role: .destructive) { store.deleteMap(map) }
                        }
                    }
                }
            }

            if !store.recentSessions.isEmpty {
                Section("Recent sessions") {
                    ForEach(store.recentSessions, id: \.key) { recent in
                        Button {
                            store.open(recent)
                        } label: {
                            RecentSessionRow(recent: recent,
                                             isOpen: store.sessions.contains { $0.folder == recent.folder && $0.sessionID == recent.id },
                                             openDocs: { store.showDocs(recent) })
                        }
                        .buttonStyle(.plain)
                        .help("Continue “\(recent.title)” in \((recent.folder.path as NSString).abbreviatingWithTildeInPath)")
                        .contextMenu {
                            Button("Show in Finder") {
                                NSWorkspace.shared.activateFileViewerSelecting(
                                    [recent.folder.appendingPathComponent(".orwick/sessions/\(recent.id)")])
                            }
                            Divider()
                            Button("Delete Session…", role: .destructive) {
                                store.delete(folder: recent.folder, id: recent.id, title: recent.title,
                                             messages: recent.messages, feature: recent.feature)
                            }
                        }
                    }
                }
            }

            if !store.availableRecentFolders.isEmpty {
                Section("Recent folders") {
                    ForEach(store.availableRecentFolders, id: \.self) { path in
                        Button {
                            store.start(in: URL(fileURLWithPath: path))
                        } label: {
                            Label((path as NSString).lastPathComponent, systemImage: "folder")
                        }
                        .buttonStyle(.plain)
                        .help("Open \((path as NSString).abbreviatingWithTildeInPath): switches to its session, or starts one")
                        .contextMenu {
                            Button("Remove from List") { store.forget(path) }
                        }
                    }
                }
            }
        }
        .listStyle(.sidebar)
        // Rows come and go without animation: SwiftUI's sidebar list can crash (an assertion
        // in its outline view) when rows are inserted while an animation runs.
        .transaction { $0.animation = nil }
        .toolbar {
            // The sidebar's part of the title bar: pushed to its right edge, away from the
            // sidebar button.
            ToolbarItem {
                Button {
                    store.goHome()
                } label: {
                    Label("Home", systemImage: store.isHome ? "house.fill" : "house")
                }
                .help("Home: the dashboard of all sessions, docs and service maps (⇧⌘H)")
            }
            ToolbarItem {
                Button {
                    store.showAnalytics()
                } label: {
                    Label("Analytics", systemImage: store.showingAnalytics ? "chart.bar.fill" : "chart.bar")
                }
                .help("Analytics: the issues reviewers raise again and again in pull requests")
            }
            ToolbarItem { Spacer() }
            ToolbarItem {
                Button {
                    store.chooseFolderForMap()
                } label: {
                    Label("New Service Map", systemImage: "point.3.connected.trianglepath.dotted")
                }
                .help("New service map: pick a service and the services it talks to")
            }
            ToolbarItem {
                Button {
                    store.chooseFolderAndStart()
                } label: {
                    Label("New Session", systemImage: "plus.bubble")
                }
                .help("New session: pick a folder (⌘N)")
            }
        }
        .onAppear { store.refreshRecentSessions(); store.refreshMaps() }
        .onReceive(Timer.publish(every: 2, on: .main, in: .common).autoconnect()) { _ in
            store.refreshRecentSessions()
            store.refreshMaps()
            store.sessions.forEach { $0.refreshStatus() }   // for the fill badges and banner
        }
        .safeAreaInset(edge: .bottom) {
            HStack(spacing: 8) {
                Button {
                    store.chooseFolderAndStart()
                } label: {
                    Label("New Session", systemImage: "plus")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                .help("Choose a folder and start Orwick there (⌘N)")

                Button {
                    store.showSettings()
                } label: {
                    Image(systemName: "gearshape")
                }
                .help("Settings, used by every session (⌘,)")
            }
            .controlSize(.large)
            .padding(12)
        }
    }
}

private struct RecentSessionRow: View {
    let recent: RecentSession
    let isOpen: Bool
    let openDocs: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 1) {
            HStack(spacing: 5) {
                if isOpen {
                    Circle().fill(Color.accentColor).frame(width: 6, height: 6)
                }
                Text(recent.title)
                    .lineLimit(1)
                    .truncationMode(.tail)
                Spacer(minLength: 4)
                if recent.hasDocs {
                    DocsIcon(action: openDocs)
                }
            }
            Text("\(recent.feature.map { "\($0) · " } ?? "")\(recent.folder.lastPathComponent) · \(recent.messages) msg · \(recent.updatedAt.formatted(.relative(presentation: .named)))")
                .font(.caption)
                .foregroundStyle(.secondary)
                .lineLimit(1)
        }
        .padding(.vertical, 1)
    }
}

private struct SessionRow: View {
    @ObservedObject var session: Session
    let openDocs: () -> Void

    var body: some View {
        HStack(spacing: 8) {
            Circle()
                .fill(session.running ? Color.green : Color.secondary.opacity(0.5))
                .frame(width: 8, height: 8)
                .help(session.running ? "Orwick is running" : "Orwick has ended")
            VStack(alignment: .leading, spacing: 1) {
                HStack(spacing: 6) {
                    Text(session.name)
                        .fontWeight(.medium)
                        .lineLimit(1)
                    if let level = session.status?.conversation, level.isWarning, session.running {
                        Text("\(level.percent)%")
                            .font(.caption2.monospacedDigit().weight(.semibold))
                            .foregroundStyle(.white)
                            .padding(.horizontal, 5)
                            .padding(.vertical, 1)
                            .background(level.isSummarizing ? Color.red : Color.orange, in: Capsule())
                            .help("This conversation is at \(level.percent)% of its budget")
                    }
                }
                Text((session.status?.feature.map { "\($0) · " } ?? "") + session.shortPath)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .truncationMode(.middle)
            }
            Spacer(minLength: 4)
            if !(session.status?.docs ?? []).isEmpty {
                DocsIcon(action: openDocs)
            }
        }
        .padding(.vertical, 2)
    }
}

/// Opens a session's docs.
private struct DocsIcon: View {
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Image(systemName: "doc.text")
                .foregroundStyle(.teal)
        }
        .buttonStyle(.borderless)
        .help("This session's docs")
    }
}

// MARK: Detail

private struct SessionDetail: View {
    @EnvironmentObject private var store: SessionStore
    @ObservedObject var session: Session
    @AppStorage("docsHeight") private var docsHeight: Double = 340

    var body: some View {
        // Terminal above, docs below, with a divider you drag up or down (so the Info
        // panel on the right keeps its width). The docs' height is remembered.
        GeometryReader { geometry in
            VStack(spacing: 0) {
                terminalColumn
                    .frame(maxHeight: .infinity)
                if store.docsOpen.contains(session.id) {
                    SplitHandle(height: $docsHeight, total: geometry.size.height)
                    DocsPane(title: session.name, folder: session.sessionFolder) { store.toggleDocs(session) }
                        .frame(height: min(max(docsHeight, 140), max(geometry.size.height - 200, 140)))
                }
            }
        }
            .inspector(isPresented: $store.showingInfo) {
                InfoPanel(session: session)
                    .inspectorColumnWidth(min: 280, ideal: 330, max: 460)
            }
            .navigationTitle(session.name)
            .navigationSubtitle(session.shortPath)
            .toolbar { toolbarItems }
    }

    private var terminalColumn: some View {
        VStack(spacing: 0) {
            if let actions = session.status?.actions?.filter({ session.skillOverrides[$0.skill] ?? $0.on ?? true }),
               !actions.isEmpty, session.running {
                // A skill ticked or unticked in the Info panel shows or hides its buttons at once.
                ActionBar(actions: actions, groups: session.status?.actionGroups ?? [],
                          enabled: session.status?.isWaiting ?? false,
                          activity: session.status?.activity) { session.run($0.command) }
            }
            if let level = session.fillWarning {
                FillBanner(level: level) { session.bannerDismissedAt = level.percent }
            }
            ZStack {
                // The console keeps running (and keeps its text) while another screen is shown.
                TerminalHost(session: session)
                    .padding(.leading, 8)
                    .padding(.top, 4)
                    .padding(.bottom, 8)   // the last line keeps a little room above the divider
                    .opacity(session.screen == .console ? 1 : 0)
                    .allowsHitTesting(session.screen == .console)
                if session.screen != .console {
                    ActivityView(session: session)
                }
            }
        }
            .background(Color(nsColor: session.theme.background))
    }

    @ToolbarContentBuilder
    private var toolbarItems: some ToolbarContent {
        ToolbarItem {
            ModelMenu(session: session)
        }
        ToolbarItemGroup {
            Button {
                session.scroll(toTop: true)
            } label: {
                Label("Top", systemImage: "arrow.up.to.line")
            }
            .help("Scroll to the very top")

            Button {
                session.scroll(toTop: false)
            } label: {
                Label("Bottom", systemImage: "arrow.down.to.line")
            }
            .help("Scroll to the very bottom")
        }
        ToolbarItemGroup {
            Button {
                store.toggleDocs(session)
            } label: {
                Label("Docs", systemImage: store.docsOpen.contains(session.id) ? "doc.text.fill" : "doc.text")
            }
            .help("Show this session's docs below the terminal")

            Button {
                store.showingInfo.toggle()
            } label: {
                Label("Info", systemImage: "info.circle")
            }
            .help("Tools, skills and tokens for this session (⌘I)")

            Button {
                store.showSettings()
            } label: {
                Label("Settings", systemImage: "gearshape")
            }
            .help("Orwick settings, used by every session (⌘,)")

            Button {
                store.restart(session)
            } label: {
                Label("Restart", systemImage: "arrow.clockwise")
            }
            .help("Restart Orwick in this folder (⌘R); it continues the same conversation.")

            Button {
                store.close(session)
            } label: {
                Label("Close", systemImage: "xmark")
            }
            .help("End this session (⇧⌘W)")
        }
    }
}

/// The divider between the terminal and the docs: a bar with a grip you drag up or down.
private struct SplitHandle: View {
    @Binding var height: Double     // the docs' height
    let total: CGFloat
    @State private var startHeight: Double?
    @State private var hovering = false

    var body: some View {
        ZStack {
            Rectangle().fill(Color(nsColor: .windowBackgroundColor))
            Rectangle().fill(Color.secondary.opacity(0.25)).frame(height: 1).frame(maxHeight: .infinity, alignment: .top)
            Rectangle().fill(Color.secondary.opacity(0.25)).frame(height: 1).frame(maxHeight: .infinity, alignment: .bottom)
            Capsule()
                .fill(hovering ? Color.accentColor : Color.secondary.opacity(0.5))
                .frame(width: 44, height: 4)
        }
        .frame(height: 10)
        .contentShape(Rectangle())
        .onHover { inside in
            hovering = inside
            if inside { NSCursor.resizeUpDown.push() } else { NSCursor.pop() }
        }
        .gesture(DragGesture(minimumDistance: 1, coordinateSpace: .global)
            .onChanged { drag in
                let start = startHeight ?? height
                if startHeight == nil { startHeight = height }
                height = min(max(start - drag.translation.height, 140), max(Double(total) - 200, 140))
            }
            .onEnded { _ in startHeight = nil })
        .help("Drag to resize the terminal and the docs")
    }
}

/// The AI and model this session uses, and a menu to switch: it types `/backend <ai> <model>`
/// into Orwick (only while Orwick waits), so the conversation is kept, as with the command.
private struct ModelMenu: View {
    @EnvironmentObject private var store: SessionStore
    @ObservedObject var session: Session

    private var backend: String { session.status?.backend ?? "?" }
    private var choice: String? { session.status?.modelChoice }
    /// The model that really answered (Claude reports it), e.g. what "Default" means right now.
    private var answering: String? { session.status?.current?.model }

    var body: some View {
        let icon = ModelCatalog.backends.first { $0.id == backend }?.icon ?? "⚪"
        Menu {
            ForEach(ModelCatalog.backends, id: \.id) { ai in
                Section("\(ai.icon) \(ai.label)" + (ai.id == backend ? " · in use" : "")) {
                    item(ai.id, nil, defaultTitle(ai.id))
                    ForEach(store.models[ai.id] ?? []) { model in
                        item(ai.id, model.id, "\(model.label)  (\(model.note))")
                    }
                }
            }
        } label: {
            Text("\(icon) \(backend) · \(currentLabel)")
        }
        .disabled(!session.running || !(session.status?.isWaiting ?? false))
        .help("The AI and model answering in this session (✓ in the list). Pick another to switch; the conversation is kept.")
        .onAppear { if store.models.isEmpty { store.loadModels() } }
    }

    /// "Claude Sonnet 5.5", or for the default what it really is: "Claude Opus 5.5 (default)".
    private var currentLabel: String {
        if let choice, !choice.isEmpty {
            return ModelCatalog.label(backend: backend, model: choice, in: store.models)
        }
        let model = (answering?.isEmpty == false ? answering : nil)
        if let named = model.map({ ModelCatalog.label(backend: backend, model: $0, in: store.models) })
            ?? ModelCatalog.defaultLabel(backend: backend, in: store.models) {
            return "\(named) (default)"
        }
        return "default"
    }

    /// "Default (Claude Opus 5.5)": the model the backend uses when none is chosen.
    private func defaultTitle(_ ai: String) -> String {
        if ai == backend, choice == nil, let answering, !answering.isEmpty {
            return "Default (\(ModelCatalog.label(backend: ai, model: answering, in: store.models)))"
        }
        return ModelCatalog.defaultLabel(backend: ai, in: store.models).map { "Default (\($0))" } ?? "Default"
    }

    /// A menu item with a checkmark on the one in use.
    private func item(_ ai: String, _ model: String?, _ title: String) -> some View {
        let inUse = ai == backend && (choice ?? nil) == model
        return Toggle(title, isOn: Binding(
            get: { inUse },
            set: { _ in if !inUse { session.run("/backend \(ai)" + (model.map { " \($0)" } ?? "")) } }))
    }
}

/// Buttons for the skills' UI actions (from their ui.json, as Orwick reports them).
/// A click types the action's `/do ...` command; Orwick then asks for its inputs.
private struct ActionBar: View {
    /// What the button does (the skill's hint), what Orwick will ask, and the command it types.
    static func tooltip(_ action: OrwickStatus.Action) -> String {
        var lines: [String] = []
        if let hint = action.hint, !hint.isEmpty { lines.append(hint) }
        if !action.inputs.isEmpty { lines.append("Orwick will ask: \(action.inputs.joined(separator: ", ")).") }
        lines.append("Types: \(action.command)")
        return lines.joined(separator: "\n\n")
    }

    let actions: [OrwickStatus.Action]
    let groups: [OrwickStatus.ActionGroup]
    let enabled: Bool
    let activity: String?
    let run: (OrwickStatus.Action) -> Void

    /// The buttons per skill, in Orwick's order, each with its group's title and hint.
    private var sections: [(skill: String, title: String, hint: String?, actions: [OrwickStatus.Action])] {
        var order: [String] = []
        var bySkill: [String: [OrwickStatus.Action]] = [:]
        for action in actions {
            if bySkill[action.skill] == nil { order.append(action.skill) }
            bySkill[action.skill, default: []].append(action)
        }
        return order.map { skill in
            let group = groups.first { $0.skill == skill }
            return (skill, group?.title ?? skill, group?.hint, bySkill[skill] ?? [])
        }
    }

    /// One color per group, in order, so neighbouring groups never look alike.
    static let groupColors: [Color] = [.teal, .indigo, .orange, .pink, .green, .purple]

    var body: some View {
        HStack(alignment: .center, spacing: 10) {
            ForEach(Array(sections.enumerated()), id: \.element.skill) { index, section in
                group(section, color: Self.groupColors[index % Self.groupColors.count])
            }
            Spacer()
            if !enabled {
                ProgressView().controlSize(.small)
                Text(activity.map { "Orwick is working: \($0)…" } ?? "Orwick is working…")
                    .font(.caption).foregroundStyle(.secondary)
                    .lineLimit(1).truncationMode(.middle)
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 6)
        .background(.bar)
    }

    /// A skill's buttons in a tinted frame, its title on top.
    private func group(_ section: (skill: String, title: String, hint: String?, actions: [OrwickStatus.Action]),
                       color: Color) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack(spacing: 4) {
                Circle().fill(color).frame(width: 6, height: 6)
                Text(section.title.uppercased())
                    .font(.caption2.weight(.bold))
                    .tracking(0.6)
                    .foregroundStyle(color)
            }
            HStack(spacing: 6) {
                ForEach(section.actions, id: \.key) { action in
                    Button {
                        run(action)
                    } label: {
                        Label(action.label, systemImage: action.icon)
                    }
                    .buttonStyle(.bordered)
                    .controlSize(.small)
                    .tint(color)
                    .disabled(!enabled)
                    .help(Self.tooltip(action))
                }
            }
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 5)
        .background(color.opacity(0.07), in: RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(color.opacity(0.35), lineWidth: 1))
        .help(section.hint ?? "")
    }
}

/// Shown above the terminal when the conversation crosses the warning percent.
private struct FillBanner: View {
    let level: OrwickStatus.Conversation
    let dismiss: () -> Void

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            Image(systemName: "gauge.with.dots.needle.67percent")
                .foregroundStyle(level.isSummarizing ? .red : .orange)
            VStack(alignment: .leading, spacing: 2) {
                Text("This conversation is at \(level.percent)% of its budget (~\(Format.tokens(level.tokens)) of \(Format.tokens(level.budget)) tokens)")
                    .font(.callout.weight(.semibold))
                Text("From \(level.trimPercent)% Orwick trims old tool output, from \(level.summarizePercent)% it summarizes the older part. Type /compact to do it now, or /clear to start a new session.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer()
            Button(action: dismiss) {
                Image(systemName: "xmark")
            }
            .buttonStyle(.borderless)
            .help("Hide until it grows another 10%")
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .background((level.isSummarizing ? Color.red : Color.orange).opacity(0.14))
    }
}

// MARK: Terminal

/// Shows the selected session's terminal. Each session keeps its own terminal
/// view, so switching sessions never stops or redraws a conversation.
private struct TerminalHost: NSViewRepresentable {
    let session: Session

    func makeNSView(context: Context) -> NSView { NSView() }

    func updateNSView(_ container: NSView, context: Context) {
        let terminal = session.terminal
        guard terminal.superview !== container else { return }
        container.subviews.forEach { $0.removeFromSuperview() }
        terminal.removeFromSuperview()
        terminal.translatesAutoresizingMaskIntoConstraints = false
        container.addSubview(terminal)
        NSLayoutConstraint.activate([
            terminal.leadingAnchor.constraint(equalTo: container.leadingAnchor),
            terminal.trailingAnchor.constraint(equalTo: container.trailingAnchor),
            terminal.topAnchor.constraint(equalTo: container.topAnchor),
            terminal.bottomAnchor.constraint(equalTo: container.bottomAnchor),
        ])
        DispatchQueue.main.async { terminal.window?.makeFirstResponder(terminal) }
    }
}
