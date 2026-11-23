import SwiftUI

/// The ⓘ side panel: this session's tokens, limits, activity, tools and skills,
/// read from the status.json Orwick keeps in the session's folder,
/// `<folder>/.orwick/sessions/<id>/`.
struct InfoPanel: View {
    @ObservedObject var session: Session
    private let ticker = Timer.publish(every: 1, on: .main, in: .common).autoconnect()
    @State private var now = Date()

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                if let status = session.status {
                    header(status)
                    if let usage = status.current {
                        tokens(usage, backend: status.backend)
                        limits(usage, backend: status.backend)
                    } else {
                        card("🔢 Tokens") { dim("No AI requests yet. Send a message to see tokens.") }
                    }
                    activity(status.session)
                    skills(status)
                    tools(status.tools)
                    ownTools(status)
                    docs(status.docs ?? [])
                    otherBackends(status)
                } else {
                    VStack(spacing: 10) {
                        ProgressView()
                        dim(session.running ? "Waiting for Orwick…" : "Orwick isn't running. Restart it (⌘R) to see its info.")
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.top, 40)
                }
            }
            .padding(14)
        }
        .onAppear { session.refreshStatus(); now = Date() }
        .onReceive(ticker) { date in
            now = date
            session.refreshStatus()
        }
    }

    // MARK: Sections

    private func header(_ status: OrwickStatus) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            SessionNameField(session: session)
                .padding(.bottom, 4)
            HStack(spacing: 8) {
                Text(backendIcon(status.backend)).font(.title2)
                VStack(alignment: .leading, spacing: 1) {
                    Text(backendName(status.backend)).font(.headline)
                    Text(status.current?.model ?? status.model)
                        .font(.caption.monospaced())
                        .foregroundStyle(.secondary)
                }
            }
            if session.running, !status.isWaiting {
                HStack(spacing: 6) {
                    ProgressView().controlSize(.mini)
                    Text(status.activity ?? "Working…")
                        .font(.caption)
                        .lineLimit(2)
                        .truncationMode(.middle)
                }
                .help(status.activity ?? "")
            }
            Text(session.running
                 ? "Updated \(Format.duration(now.timeIntervalSince1970 - status.updatedAt)) ago · after each step"
                 : "Orwick has ended. These are its last numbers.")
                .font(.caption2)
                .foregroundStyle(.tertiary)
            Button {
                NSWorkspace.shared.activateFileViewerSelecting([session.sessionFolder])
            } label: {
                Label("Session \(session.sessionID)", systemImage: "folder")
                    .font(.caption.monospaced())
            }
            .buttonStyle(.link)
            .help("Show this session's folder (status.json, audit.jsonl) in Finder")
        }
    }

    private func tokens(_ usage: OrwickStatus.Usage, backend: String) -> some View {
        card("🔢 Tokens this session") {
            HStack(alignment: .top, spacing: 0) {
                bigNumber(Format.tokens(usage.newInputTokens), "new in")
                bigNumber(Format.tokens(usage.outputTokens), "out")
                bigNumber(Format.tokens(usage.cachedTokens), "re-read")
            }
            .help("New in: input the requests really added (on Claude, written to the cache). "
                  + "Out: the AI's replies. Re-read: the conversation sent again with each request "
                  + "and read back from the cache, the cheapest kind of token.")
            Text("Total \(Format.tokens(usage.inputTokens + usage.outputTokens)) tokens")
                .font(.callout.weight(.medium).monospacedDigit())
                .help("Everything sent and received in this session, re-reads included (new in + re-read + out): the figure Claude Code shows.")
            if let written = usage.cacheWriteTokens, written > 0 {
                dim("\(Format.tokens(written)) of the new input written to the cache · re-reads are cheap")
            }
            dim("\(usage.requests) request\(usage.requests == 1 ? "" : "s") · "
                + (usage.requests > 0 ? String(format: "%.1fs average", usage.seconds / Double(usage.requests)) : "none yet"))
            if let used = usage.contextTokens, let window = usage.contextWindow, window > 0 {
                meter("Context", fraction: Double(used) / Double(window),
                      detail: "\(Format.tokens(used)) of \(Format.tokens(window))")
            }
            if let cost = usage.costUsd, backend == "claude" {
                dim(String(format: "$%.2f at API prices · covered by your subscription, not charged", cost))
            }
            if backend == "codex" {
                dim("Codex adds about 25k tokens of its own instructions to each request.")
            }
        }
    }

    @ViewBuilder
    private func limits(_ usage: OrwickStatus.Usage, backend: String) -> some View {
        if !usage.limits.isEmpty {
            card("⏳ Subscription limits") {
                ForEach(usage.limits) { limit in
                    VStack(alignment: .leading, spacing: 3) {
                        meter(limit.name, fraction: limit.usedPercent / 100, detail: "\(Int(limit.usedPercent.rounded()))%")
                        if let resets = limit.resetsAt {
                            dim(Format.resets(resets, now: now))
                        }
                    }
                }
                dim(usage.limitsNote == "last known"
                    ? "Last known, from your own Codex sessions\(usage.limitsSeenAt.map { " \(Format.duration(now.timeIntervalSince1970 - $0)) ago" } ?? ""). For your whole account."
                    : "Live from \(backendName(backend)). For your whole account, not just Orwick.")
            }
        } else if backend == "claude" {
            card("⏳ Subscription limits") { dim("Shown after your first message: Claude reports them with each reply.") }
        }
    }

    private func activity(_ counts: OrwickStatus.Counts) -> some View {
        card("📊 Session") {
            link("⏱", "Running", Format.duration(now.timeIntervalSince1970 - counts.startedAt), .console,
                 help: "Show the console")
            link("💬", "Messages", "\(counts.messages)", .messages, help: "Show every message, its answer and what it took")
            link("🔁", "AI steps", "\(counts.steps)", .steps, help: "Show each AI step: how long it took and which tools it asked for")
            let callsLabel = counts.toolCalls == 0 ? "0" : "\(counts.toolCalls)  (✓ \(counts.toolsOk) · ✗ \(counts.toolsFailed))"
            link("🧰", "Tool calls", callsLabel, .calls, help: "Show each tool call: arguments, result, time, approvals")
            if counts.approvalsAsked > 0 {
                row("✋", "Approvals", "\(counts.approvalsYes) yes · \(counts.approvalsNo) no")
            }
            row("🧠", "History", "\(counts.historyMessages) messages · ~\(Format.tokens(counts.historyChars / 4)) tokens")
            if let level = session.status?.conversation {
                VStack(alignment: .leading, spacing: 3) {
                    HStack {
                        Text("📏 Conversation").font(.callout)
                        Spacer()
                        Text("\(level.percent)% of budget")
                            .font(.callout.monospacedDigit().weight(.medium))
                            .foregroundStyle(fillColor(level))
                    }
                    ProgressView(value: min(Double(level.percent) / 100, 1)).tint(fillColor(level))
                    dim("~\(Format.tokens(level.tokens)) of \(Format.tokens(level.budget)) tokens · warns at \(level.warn)%, trims at \(level.trimPercent)%, summarizes at \(level.summarizePercent)%")
                }
            }
        }
    }

    private func tools(_ tools: [OrwickStatus.Tool]) -> some View {
        let changed = !session.toolOverrides.isEmpty
        return card("🧰 Tools (\(tools.filter { isOn($0) }.count) of \(tools.count) on)") {
            if tools.isEmpty {
                dim("None in this folder. Orwick's tools live in .orwick/tools; without them it can only chat.")
            }
            ForEach(tools) { tool in
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Image(systemName: riskIcon(tool.risk))
                        .foregroundStyle(riskColor(tool.risk))
                        .frame(width: 16)
                        .help(riskHelp(tool.risk))
                    VStack(alignment: .leading, spacing: 2) {
                        HStack {
                            Text(tool.name).font(.callout.monospaced().weight(.medium))
                            if let origin = tool.origin, origin != "built-in" {
                                Text(origin.hasPrefix("mcp:") ? "MCP · \(origin.dropFirst(4))"
                                     : origin.hasPrefix("~") ? "your tool" : "project tool")
                                    .font(.caption2).foregroundStyle(.secondary)
                                    .padding(.horizontal, 5).background(.quaternary, in: Capsule())
                                    .help("From \(origin)")
                            }
                            if session.toolOverrides[tool.name] != nil {
                                Text("this session").font(.caption2.weight(.semibold)).foregroundStyle(.purple)
                                    .padding(.horizontal, 5).background(.purple.opacity(0.12), in: Capsule())
                                    .help("Switched for this session only; the main tool list says \(tool.settingsValue ? "on" : "off")")
                            }
                            if tool.uses > 0 {
                                Text("×\(tool.uses)")
                                    .font(.caption.monospacedDigit())
                                    .padding(.horizontal, 6)
                                    .padding(.vertical, 1)
                                    .background(.quaternary, in: Capsule())
                            }
                            Spacer()
                            Toggle("", isOn: Binding(
                                get: { isOn(tool) },
                                set: { session.setTool(tool.name, on: $0, settingsValue: tool.settingsValue) }))
                                .toggleStyle(.switch)
                                .controlSize(.mini)
                                .labelsHidden()
                                .disabled(!session.running)
                                .help("On or off for this session only")
                        }
                        Text(tool.description)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .lineLimit(2)
                            .help(tool.description)
                    }
                }
                .opacity(isOn(tool) ? 1 : 0.45)
            }
            if !tools.isEmpty {
                dim("Switches here change this session only; it applies with your next message. The main list is in Settings → Tools.")
                if changed {
                    Button("Use the tool list from Settings") { session.resetTools() }
                        .buttonStyle(.link)
                        .font(.caption)
                }
            }
        }
    }

    /// What this session will use: its own switch if set, otherwise the settings' list.
    private func isOn(_ tool: OrwickStatus.Tool) -> Bool {
        session.toolOverrides[tool.name] ?? tool.settingsValue
    }

    @ViewBuilder
    private func ownTools(_ status: OrwickStatus) -> some View {
        let own = status.ownTools ?? []
        card("\(backendIcon(status.backend)) \(status.backend.capitalized)'s own tools") {
            if own.isEmpty {
                dim("All off: it can only use Orwick's tools above.")
            }
            ForEach(own) { tool in
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Image(systemName: tool.risk == "dangerous" ? "exclamationmark.triangle.fill" : "globe")
                        .foregroundStyle(tool.risk == "dangerous" ? .red : .green)
                        .frame(width: 16)
                    VStack(alignment: .leading, spacing: 1) {
                        Text(tool.name).font(.callout.monospaced().weight(.medium))
                        Text(tool.what).font(.caption).foregroundStyle(.secondary)
                    }
                }
            }
            dim("These run inside the AI, not through Orwick. Change them in Settings → Tools.")
        }
    }

    private func docs(_ names: [String]) -> some View {
        card("📄 Session docs") {
            if names.isEmpty {
                dim("None yet. Skills such as feature-brief write them (you approve each write).")
            } else {
                ForEach(names, id: \.self) { Text($0).font(.callout.monospaced()) }
                Button("Show docs") {
                    SessionStore.shared.showDocs(session)
                }
                .buttonStyle(.link)
                .font(.caption)
            }
        }
    }

    @ViewBuilder
    private func skills(_ status: OrwickStatus) -> some View {
        let all = status.skillsAll ?? (status.skills ?? []).map {   // older Orwick: names of the ones on
            SkillInfo(name: $0, origin: "", source: nil, title: nil, description: "", default: true, actions: nil,
                      on: true, setting: nil, session: nil)
        }
        let on = all.filter { isOn($0) }
        card("🎓 Skills (\(on.count) of \(all.count) on)") {
            if all.isEmpty {
                dim("None found. Built-in skills ship with Orwick; add yours in ~/.orwick/skills/<name>/SKILL.md.")
            }
            ForEach(SkillInfo.grouped(all), id: \.origin) { group in
                let groupOn = group.skills.filter { isOn($0) }
                DisclosureGroup {
                    VStack(alignment: .leading, spacing: 6) {
                        ForEach(group.skills) { skill in
                            Toggle(isOn: skillBinding(skill)) {
                                VStack(alignment: .leading, spacing: 1) {
                                    Text(skill.name)
                                        .font(.callout.monospaced())
                                        .foregroundStyle(isOn(skill) ? .primary : .secondary)
                                    if !skill.description.isEmpty {
                                        Text(skill.description)
                                            .font(.caption)
                                            .foregroundStyle(.secondary)
                                            .lineLimit(2)
                                            .fixedSize(horizontal: false, vertical: true)
                                    }
                                }
                            }
                            .toggleStyle(.checkbox)
                            .disabled(!session.running || skill.origin.isEmpty)
                            .help(skill.description + (skill.setting.map { "\n\nIn the settings: \($0 ? "on" : "off")." } ?? ""))
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.leading, 14)
                    .padding(.top, 2)
                } label: {
                    Text("\(group.title)  \(groupOn.count)/\(group.skills.count)")
                        .font(.callout.weight(.medium))
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
            if !all.isEmpty {
                dim(session.skillOverrides.isEmpty
                    ? "Tick one to use it in this session only; the AI is told with your next message. Settings → Skills sets them for every session."
                    : "Changed for this session: \(session.skillOverrides.keys.sorted().joined(separator: ", ")). The AI is told with your next message.")
            }
        }
    }

    /// On for this session: its own switch if it has one (even before Orwick read it), else Orwick's view.
    private func isOn(_ skill: SkillInfo) -> Bool {
        session.skillOverrides[skill.name] ?? (skill.session == nil ? skill.on : skill.setting) ?? false
    }

    private func skillBinding(_ skill: SkillInfo) -> Binding<Bool> {
        Binding(get: { isOn(skill) },
                set: { on in session.setSkill(skill.name, on: on, settingsValue: skill.setting ?? skill.default) })
    }

    @ViewBuilder
    private func otherBackends(_ status: OrwickStatus) -> some View {
        let others = status.usage.filter { $0.key != status.backend && $0.value.requests > 0 }.sorted { $0.key < $1.key }
        if !others.isEmpty {
            card("🔀 Also used this session") {
                ForEach(others, id: \.key) { name, usage in
                    row(backendIcon(name), backendName(name),
                        "\(usage.requests) req · new in \(Format.tokens(usage.newInputTokens)) · out \(Format.tokens(usage.outputTokens))")
                }
            }
        }
    }

    // MARK: Pieces

    private func card<Content: View>(_ title: String, @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title).font(.subheadline.weight(.semibold))
            content()
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.background.secondary, in: RoundedRectangle(cornerRadius: 10))
    }

    private func bigNumber(_ value: String, _ label: String) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            Text(value).font(.title3.weight(.semibold).monospacedDigit())
            Text(label).font(.caption).foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func meter(_ label: String, fraction: Double, detail: String) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack {
                Text(label).font(.callout)
                Spacer()
                Text(detail).font(.callout.monospacedDigit().weight(.medium)).foregroundStyle(levelColor(fraction))
            }
            ProgressView(value: min(max(fraction, 0), 1))
                .tint(levelColor(fraction))
        }
    }

    /// A row that opens a screen in the middle of the window (highlighted while shown).
    private func link(_ icon: String, _ label: String, _ value: String, _ screen: SessionScreen, help: String) -> some View {
        Button { session.screen = screen } label: {
            HStack(alignment: .firstTextBaseline) {
                Text(icon).frame(width: 20, alignment: .leading)
                Text(label).foregroundStyle(session.screen == screen ? Color.accentColor : .secondary)
                Spacer()
                Text(value).monospacedDigit()
                Image(systemName: "chevron.right").font(.caption2).foregroundStyle(.tertiary)
            }
            .font(.callout)
            .padding(.vertical, 2)
            .padding(.horizontal, 4)
            .background(session.screen == screen ? Color.accentColor.opacity(0.12) : .clear, in: RoundedRectangle(cornerRadius: 5))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .padding(.horizontal, -4)
        .help(help)
    }

    private func row(_ icon: String, _ label: String, _ value: String) -> some View {
        HStack(alignment: .firstTextBaseline) {
            Text(icon).frame(width: 20, alignment: .leading)
            Text(label).foregroundStyle(.secondary)
            Spacer()
            Text(value).monospacedDigit()
        }
        .font(.callout)
    }

    private func dim(_ text: String) -> some View {
        Text(text)
            .font(.caption)
            .foregroundStyle(.secondary)
            .fixedSize(horizontal: false, vertical: true)
    }

    private func fillColor(_ level: OrwickStatus.Conversation) -> Color {
        level.isSummarizing ? .red : level.isWarning ? .orange : .green
    }

    private func levelColor(_ fraction: Double) -> Color {
        fraction < 0.5 ? .green : fraction < 0.8 ? .orange : .red
    }

    private func backendIcon(_ backend: String) -> String {
        ["claude": "🟠", "codex": "🟢", "openai": "🔵"][backend] ?? "⚪"
    }

    private func backendName(_ backend: String) -> String {
        ["claude": "Claude · subscription", "codex": "Codex · ChatGPT subscription", "openai": "OpenAI API · pay per token"][backend] ?? backend
    }

    private func riskIcon(_ risk: String) -> String {
        ["read": "eye", "write": "pencil", "dangerous": "exclamationmark.triangle.fill"][risk] ?? "questionmark.circle"
    }

    private func riskColor(_ risk: String) -> Color {
        ["read": .green, "write": .orange, "dangerous": .red][risk] ?? .secondary
    }

    private func riskHelp(_ risk: String) -> String {
        ["read": "Read: runs without asking", "write": "Write: asks you every time",
         "dangerous": "Dangerous: asks you every time"][risk] ?? risk
    }
}

/// The session's name, edited in place: Return (or leaving the field) saves it, Esc puts
/// it back. It's shown in the sidebar, the window title, the docs and Orwick's /sessions.
private struct SessionNameField: View {
    @ObservedObject var session: Session
    @State private var draft = ""
    @State private var error: String?
    @FocusState private var editing: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack(spacing: 6) {
                Image(systemName: "tag").foregroundStyle(.secondary)
                TextField("Name this session", text: $draft)
                    .textFieldStyle(.plain)
                    .font(.title3.weight(.semibold))
                    .focused($editing)
                    .onSubmit(save)
                    .onExitCommand { draft = session.customName ?? ""; editing = false }
                    .help("The session's name, shown in the sidebar, the window title, the docs and /sessions. "
                          + "Return saves it; empty removes it.")
            }
            if let error {
                Text(error).font(.caption).foregroundStyle(.red)
            }
        }
        .onAppear { draft = session.customName ?? "" }
        .onChange(of: session.customName) { _, saved in
            if !editing { draft = saved ?? "" }   // renamed elsewhere (/name in the chat)
        }
        .onChange(of: session.sessionID) { _, _ in draft = session.customName ?? "" }
        .onChange(of: editing) { _, now in
            if !now { save() }
        }
    }

    private func save() {
        guard SessionName.clean(draft) != (session.customName ?? "") else {
            draft = session.customName ?? ""
            return
        }
        do {
            try session.rename(draft)
            draft = session.customName ?? ""
            error = nil
        } catch {
            self.error = "Couldn't save the name: \(error.localizedDescription)"
        }
    }
}

