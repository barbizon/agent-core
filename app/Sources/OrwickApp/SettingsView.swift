import SwiftUI

/// Edits the main settings, `~/.orwick/config.json`, used by every session.
/// Running sessions re-read it before your next message, so there is no need
/// to restart them.
struct SettingsView: View {
    /// The selected session's folder, to say which settings it overrides.
    let folder: URL?
    /// Folders whose tools the Tools tab lists (recent and open ones).
    var toolFolders: [URL] = []
    @State private var tab = "general"
    @State private var orwickTools: [ToolInfo] = []
    @State private var ownTools: [String: [ToolInfo.OwnTool]] = [:]
    @State private var mcpServers: [String: ToolInfo.MCPServer] = [:]
    @State private var skillList: [SkillInfo] = []
    @State private var skillFilter = ""
    @State private var mcpFilter = ""
    @State private var mcpExpanded: Set<String> = []   // servers whose full tool list is shown
    @State private var askedOrwick = false
    @State private var asking = false
    @EnvironmentObject private var store: SessionStore
    @Environment(\.dismiss) private var dismiss

    @State private var saved = OrwickConfig()
    @State private var config = OrwickConfig()
    @State private var loadError: String?
    @State private var saveError: String?

    private var fileURL: URL { OrwickConfig.mainFile }
    private var fileExists: Bool { FileManager.default.fileExists(atPath: fileURL.path) }
    private var overrides: [String] { folder.map(OrwickConfig.folderOverrides(in:)) ?? [] }
    private var problems: [String: String] { config.problems }

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider()
            if let loadError {
                brokenFile(loadError)
            } else {
                Picker("", selection: $tab) {
                    Label("General", systemImage: "gearshape").tag("general")
                    Label("Skills", systemImage: "graduationcap").tag("skills")
                    Label("Tools", systemImage: "wrench.and.screwdriver").tag("tools")
                }
                .pickerStyle(.segmented)
                .labelsHidden()
                .frame(width: 360)
                .padding(.vertical, 10)
                Divider()
                switch tab {
                case "skills": skillsTab
                case "tools": toolsTab
                default: form
                }
            }
            Divider()
            footer
        }
        .frame(width: 580, height: 680)
        .onAppear(perform: load)
    }

    // MARK: Parts

    private var header: some View {
        HStack(spacing: 12) {
            Image(systemName: "gearshape.fill")
                .font(.title)
                .foregroundStyle(.teal)
            VStack(alignment: .leading, spacing: 2) {
                Text("Settings")
                    .font(.headline)
                Text((fileURL.path as NSString).abbreviatingWithTildeInPath)
                    .font(.caption.monospaced())
                    .foregroundStyle(.secondary)
                    .textSelection(.enabled)
            }
            Spacer()
        }
        .padding(16)
    }

    private var form: some View {
        Form {
            Section("🖥 App") {
                Picker("Appearance", selection: $store.appearance) {
                    ForEach(Appearance.allCases) { Text($0.title).tag($0) }
                }
                .pickerStyle(.segmented)
                note("This app's look, terminal included. Applies right away; System follows macOS light and dark mode.")
            }

            Section {
                Text("The settings below are Orwick's main settings, used by every session.")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }

            if !fileExists {
                Section {
                    Label("No main settings yet, so Orwick uses its built-in defaults. Saving creates ~/.orwick/config.json.",
                          systemImage: "info.circle")
                        .font(.callout)
                        .foregroundStyle(.secondary)
                }
            }
            if let folder, !overrides.isEmpty {
                Section {
                    Label("\(folder.lastPathComponent) has its own .orwick/config.json that sets \(overrides.joined(separator: ", ")). In sessions there, those values win over these.",
                          systemImage: "exclamationmark.circle")
                        .font(.callout)
                        .foregroundStyle(.orange)
                }
            }

            Section("🤖 AI") {
                Picker("Backend", selection: $config.backend) {
                    Text("Not set (\(OrwickConfig.Default.backend))").tag("")
                    Text("Codex · ChatGPT subscription").tag("codex")
                    Text("Claude · Claude subscription").tag("claude")
                    Text("OpenAI API · pay per token").tag("openai")
                }
                if config.backend == "openai" {
                    note("Needs OPENAI_API_KEY in .orwick/.env. Orwick refuses to start this backend without it.", warning: true)
                }
                let backend = config.backend.isEmpty ? OrwickConfig.Default.backend : config.backend
                let offered = store.models[backend] ?? []
                let custom = !config.model.isEmpty && !offered.contains { $0.id == config.model }
                Picker("Model", selection: $config.model) {
                    Text(ModelCatalog.defaultLabel(backend: backend, in: store.models).map { "Default (\($0))" }
                         ?? "Default (the backend picks)").tag("")
                    ForEach(offered) { model in
                        Text(model.note.isEmpty ? model.label : "\(model.label) — \(model.note)").tag(model.id)
                    }
                    if custom { Text("\(config.model) (typed)").tag(config.model) }
                }
                DisclosureGroup("Another model name") {
                    TextField("Model name", text: $config.model, prompt: Text("e.g. claude-opus-5-5"))
                }
                .font(.callout)
                note("The list comes from Orwick (for Codex, from Codex's own model list). Each session can switch with the model menu in its toolbar.")
            }

            Section("🔁 Agent loop") {
                numberField("Max steps per message", text: $config.maxSteps,
                            placeholder: OrwickConfig.Default.maxSteps, key: "max_steps",
                            range: "0 = no limit · otherwise 1 or more", unit: "steps")
                numberField("Request timeout", text: $config.requestTimeoutSeconds,
                            placeholder: OrwickConfig.Default.requestTimeoutSeconds, key: "request_timeout_seconds",
                            range: "0 = wait as long as it takes · otherwise seconds", unit: "s")
                numberField("Max tool result", text: $config.maxToolResultChars,
                            placeholder: OrwickConfig.Default.maxToolResultChars, key: "max_tool_result_chars",
                            range: "0 = never cut · otherwise 100 or more", unit: "characters")
                numberField("Conversation budget", text: $config.historyBudgetTokens,
                            placeholder: OrwickConfig.Default.historyBudgetTokens, key: "history_budget_tokens",
                            range: "0 = never shorten · otherwise 8,000 or more", unit: "tokens")
                numberField("Warn at", text: $config.historyWarnPercent,
                            placeholder: OrwickConfig.Default.historyWarnPercent, key: "history_warn_percent",
                            range: "0 = never warn · otherwise 1–100", unit: "% of the budget")
                note("When a conversation gets this big, Orwick shortens what it sends: from 80% old tool output is trimmed, from 90% the AI summarizes the older part. At the warning percent you get a notice (in the terminal, a badge in the sidebar and a banner here), so you can /compact or /clear first. /history still shows everything.")
            }

            Section("🐞 Debug") {
                Toggle("Debug output", isOn: Binding(get: { config.debug ?? false },
                                                     set: { config.debug = $0 }))
                    .toggleStyle(.switch)
                Toggle("Show tool calls in the terminal", isOn: Binding(get: { config.trace ?? true },
                                                                          set: { config.trace = $0 }))
                    .toggleStyle(.switch)
                note("One short title per tool call (▸ Read Jira issue PROJ-1); errors get a line too. Off: just how many calls were made. Info lists every call with its tool; /debug shows full calls and results. /trace on|off switches it for one session.")
                note("Shows each AI request and tool call in the terminal. /debug on|off in the chat overrides this.")
            }

            Section("🗺 Code root") {
                HStack {
                    TextField("Code root", text: $config.codeRoot,
                              prompt: Text("Found (the folder around a session's folder, when it holds other repos)"))
                    Button("Choose…") {
                        let panel = NSOpenPanel()
                        panel.canChooseDirectories = true
                        panel.canChooseFiles = false
                        panel.prompt = "Choose"
                        if panel.runModal() == .OK, let url = panel.url { config.codeRoot = FolderTree.short(url) }
                    }
                }
                note("The folder that holds all your repos, e.g. ~/Documents/go. Service maps read it (never change it) to follow a flow into the other repos. Empty: the folder around the session's folder, when it holds other git repos.")
            }

            Section("📄 Advanced") {
                TextField("Audit log", text: $config.auditLog, prompt: Text("Not set (\(OrwickConfig.Default.auditLog))"))
                note("Empty: each session logs to .orwick/sessions/<id>/audit.jsonl. A path puts every session in that one file (relative to the session's folder). The workspace is always the session's folder and isn't set here.")
            }

            Section {
                note("Empty means Orwick's built-in default. Running sessions pick up saved changes with your next message. A folder's own .orwick/config.json or .orwick/.env, and /backend in the chat, win over these.")
            }
        }
        .formStyle(.grouped)
    }

    // MARK: Tools tab

    private var catalog: [ToolInfo] {
        ToolInfo.catalog(in: toolFolders, alsoNamed: Array(config.tools.keys), fromOrwick: orwickTools)
    }

    private var toolsTab: some View {
        Form {
            Section {
                Text("The main tool list: which tools every new session starts with. A switched-off tool is never offered to the AI, so it can't be used. Running sessions pick up changes with your next message. To change tools for one session only, use the Info panel (ⓘ) or /tools off <name> in the chat.")
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                if let folder, overrides.contains("tools") {
                    Label("\(folder.lastPathComponent) has its own \"tools\" in .orwick/config.json; in sessions there, its choices win per tool.",
                          systemImage: "exclamationmark.circle")
                        .font(.callout)
                        .foregroundStyle(.orange)
                }
            }
            Section("🧰 Tools") {
                if !askedOrwick {
                    // Until Orwick answers, only tools named in your settings or found in folders
                    // are listed: the built-in ones, the AIs' own and MCP tools come with the answer.
                    HStack(spacing: 8) {
                        ProgressView().controlSize(.small)
                        Text("Loading tools from Orwick… Starting the MCP servers can take a few seconds.")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                } else if catalog.isEmpty {
                    note("No tools found. Is the orwick command installed?")
                }
                ForEach(catalog) { tool in
                    Toggle(isOn: toolBinding(tool.name)) {
                        HStack(alignment: .firstTextBaseline, spacing: 8) {
                            Image(systemName: riskIcon(tool.risk))
                                .foregroundStyle(riskColor(tool.risk))
                                .frame(width: 16)
                            VStack(alignment: .leading, spacing: 2) {
                                HStack {
                                    Text(tool.name).font(.callout.monospaced().weight(.medium))
                                    Text(riskText(tool.risk)).font(.caption).foregroundStyle(riskColor(tool.risk))
                                    if tool.origin != "built-in" {
                                        Text(tool.origin == "folder" ? "project tool" : "your tool")
                                            .font(.caption2).foregroundStyle(.secondary)
                                            .padding(.horizontal, 5).background(.quaternary, in: Capsule())
                                    }
                                }
                                Text(tool.description)
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                                    .lineLimit(2)
                                    .help(tool.description)
                                if !tool.folders.isEmpty {
                                    Text("In: " + tool.folders.joined(separator: ", "))
                                        .font(.caption2)
                                        .foregroundStyle(.tertiary)
                                }
                            }
                        }
                    }
                    .toggleStyle(.switch)
                }
            }
            ownToolsSection("claude", title: "🟠 Claude's own tools", list: $config.claudeTools)
            ownToolsSection("codex", title: "🟢 Codex's own tools", list: $config.codexTools)
            ForEach(mcpServers.keys.sorted(), id: \.self) { server in
                mcpSection(server, mcpServers[server]!)
            }
        }
        .formStyle(.grouped)
        .onAppear {
            askOrwick()
        }
    }

    /// Asks Orwick once for its tools, MCP tools and skills (the Skills and Tools tabs share it).
    private func askOrwick() {
        guard !askedOrwick, !asking else { return }
        asking = true
        ToolInfo.fromOrwickWithOwn { result in
            orwickTools = result.tools
            ownTools = result.own
            mcpServers = result.mcp
            skillList = result.skills
            askedOrwick = true
            asking = false
        }
    }

    // MARK: Skills tab

    private var skillsTab: some View {
        Form {
            Section {
                Text("Which skills every new session can use. The AI sees only the names and short descriptions of the skills that are on, and loads a skill's instructions when a task needs it, so every skill you switch on adds a line to each request. Running sessions pick up changes with your next message.")
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                if let folder, overrides.contains("skills") {
                    Label("\(folder.lastPathComponent) has its own \"skills\" in .orwick/config.json; in sessions there, its choices win per skill.",
                          systemImage: "exclamationmark.circle")
                        .font(.callout)
                        .foregroundStyle(.orange)
                }
                if !askedOrwick {
                    HStack(spacing: 8) {
                        ProgressView().controlSize(.small)
                        Text("Loading skills from Orwick…").font(.caption).foregroundStyle(.secondary)
                    }
                } else if skillList.isEmpty {
                    note("No skills found. Is the orwick command installed?")
                } else {
                    TextField("Filter skills", text: $skillFilter)
                }
            }
            ForEach(SkillInfo.grouped(filteredSkills), id: \.origin) { group in
                Section {
                    note(group.note)
                    ForEach(group.skills) { skill in
                        Toggle(isOn: skillBinding(skill)) {
                            VStack(alignment: .leading, spacing: 2) {
                                HStack(spacing: 6) {
                                    Text(skill.name).font(.callout.monospaced().weight(.medium))
                                    if let title = skill.title {
                                        Text(title).font(.caption2).foregroundStyle(.secondary)
                                            .padding(.horizontal, 5).background(.quaternary, in: Capsule())
                                    }
                                    if (skill.actions ?? 0) > 0 {
                                        Text("\(skill.actions!) button\(skill.actions! == 1 ? "" : "s")")
                                            .font(.caption2).foregroundStyle(.teal)
                                    }
                                }
                                Text(skill.description)
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                                    .lineLimit(2)
                                    .help(skill.description)
                            }
                        }
                        .toggleStyle(.switch)
                    }
                } header: {
                    Text("\(group.title)  ·  \(group.skills.filter { config.skills[$0.name] ?? $0.default }.count) of \(group.skills.count) on")
                }
            }
        }
        .formStyle(.grouped)
        .onAppear(perform: askOrwick)
    }

    private var filteredSkills: [SkillInfo] {
        let query = skillFilter.trimmingCharacters(in: .whitespaces).lowercased()
        guard !query.isEmpty else { return skillList }
        return skillList.filter { $0.name.contains(query) || $0.description.lowercased().contains(query) }
    }

    /// On/off for one skill; matching the skill's default removes it from the file.
    private func skillBinding(_ skill: SkillInfo) -> Binding<Bool> {
        Binding(get: { config.skills[skill.name] ?? skill.default },
                set: { on in
                    if on == skill.default { config.skills.removeValue(forKey: skill.name) }
                    else { config.skills[skill.name] = on }
                })
    }

    /// One AI's own built-in tools: safe ones on by default, dangerous ones only if you switch them on.
    @ViewBuilder
    private func ownToolsSection(_ backend: String, title: String, list: Binding<[String]?>) -> some View {
        let tools = ownTools[backend] ?? []
        if !tools.isEmpty {
            Section {
                ForEach(tools) { tool in
                    Toggle(isOn: Binding(
                        get: { (list.wrappedValue ?? defaults(tools)).contains(tool.name) },
                        set: { on in
                            var names = list.wrappedValue ?? defaults(tools)
                            names.removeAll { $0 == tool.name }
                            if on { names.append(tool.name) }
                            let ordered = tools.map(\.name).filter(names.contains)
                            list.wrappedValue = ordered == defaults(tools) ? nil : ordered
                        })) {
                        VStack(alignment: .leading, spacing: 2) {
                            HStack {
                                Text(tool.name).font(.callout.monospaced().weight(.medium))
                                Text(tool.isDangerous ? "⚠ dangerous" : "safe")
                                    .font(.caption).foregroundStyle(tool.isDangerous ? .red : .green)
                            }
                            Text(tool.what.prefix(1).uppercased() + tool.what.dropFirst())
                                .font(.caption).foregroundStyle(.secondary)
                        }
                    }
                    .toggleStyle(.switch)
                }
                HStack {
                    Button("All off") { list.wrappedValue = [] }
                    Button("Safe defaults") { list.wrappedValue = nil }
                    Spacer()
                }
                .controlSize(.small)
                if (list.wrappedValue ?? []).contains(where: { name in tools.first { $0.name == name }?.isDangerous == true }) {
                    note("Dangerous tools work in your session's folder WITHOUT asking you, outside Orwick's sandbox and checks.", warning: true)
                }
            } header: {
                Text(title)
            } footer: {
                Text("Used only when this AI answers. They run inside the AI, not through Orwick.")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
    }

    /// One MCP server's tools: off until you switch them on (a server with dozens of tools
    /// would otherwise fill every request). Read-only ones first.
    private func mcpSection(_ server: String, _ info: ToolInfo.MCPServer) -> some View {
        let all = info.tools ?? []
        let onCount = all.filter { config.tools[$0.name] == true }.count
        let expanded = mcpExpanded.contains(server)
        // Collapsed: only the tools that are on. Expanded: all of them, with a filter.
        let shown = expanded ? mcpTools(all) : mcpTools(all.filter { config.tools[$0.name] == true })
        return Section {
            if let error = info.error {
                note("Couldn't start it: \(error)", warning: true)
            }
            if expanded && all.count > 8 {
                TextField("Filter tools", text: $mcpFilter, prompt: Text("Filter \(all.count) tools"))
            }
            if !expanded && onCount == 0 && info.error == nil {
                note("No tools on yet.")
            }
            ForEach(shown) { tool in
                MCPToolRow(tool: tool, isOn: mcpBinding(tool.name))
            }
            if !all.isEmpty {
                Button(expanded ? "Show less" : "Show all \(all.count) tools") {
                    if expanded { mcpExpanded.remove(server); mcpFilter = "" } else { mcpExpanded.insert(server) }
                }
                .buttonStyle(.link)
                .font(.callout)
            }
        } header: {
            Button {
                if expanded { mcpExpanded.remove(server) } else { mcpExpanded.insert(server) }
            } label: {
                HStack(spacing: 6) {
                    Image(systemName: expanded ? "chevron.down" : "chevron.right")
                        .font(.caption.weight(.semibold))
                    Text("🔌 MCP · \(server) (\(onCount) of \(all.count) on)")
                }
            }
            .buttonStyle(.plain)
        } footer: {
            Text("Servers are set in \"mcpServers\" in ~/.orwick/config.json. Their tools are off until you switch them on; read-only ones run without asking, others ask you every time.")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }

    private func mcpTools(_ all: [ToolInfo.MCPServer.Tool]) -> [ToolInfo.MCPServer.Tool] {
        let filtered = mcpFilter.isEmpty ? all : all.filter { $0.name.localizedCaseInsensitiveContains(mcpFilter) }
        return filtered.sorted { a, b in
            if (a.risk == "read") != (b.risk == "read") { return a.risk == "read" }
            return a.name < b.name
        }
    }

    /// MCP tools: on = listed as true; off = not listed (they're off by default).
    private func mcpBinding(_ name: String) -> Binding<Bool> {
        Binding(get: { config.tools[name] == true },
                set: { on in if on { config.tools[name] = true } else { config.tools.removeValue(forKey: name) } })
    }

    private func defaults(_ tools: [ToolInfo.OwnTool]) -> [String] {
        tools.filter(\.default).map(\.name)
    }

    /// On = not listed (the default); off = listed as false.
    private func toolBinding(_ name: String) -> Binding<Bool> {
        Binding(get: { config.tools[name] ?? true },
                set: { on in if on { config.tools.removeValue(forKey: name) } else { config.tools[name] = false } })
    }

    private func riskIcon(_ risk: String) -> String {
        ["read": "eye", "write": "pencil", "dangerous": "exclamationmark.triangle.fill"][risk] ?? "questionmark.circle"
    }

    private func riskColor(_ risk: String) -> Color {
        ["read": .green, "write": .orange, "dangerous": .red][risk] ?? .secondary
    }

    private func riskText(_ risk: String) -> String {
        ["read": "read · runs without asking", "write": "write · asks you", "dangerous": "dangerous · asks you"][risk] ?? ""
    }

    private func brokenFile(_ reason: String) -> some View {
        VStack(spacing: 14) {
            Image(systemName: "exclamationmark.triangle.fill")
                .font(.system(size: 36))
                .foregroundStyle(.orange)
            Text("Orwick can't use this config file")
                .font(.headline)
            Text(reason)
                .multilineTextAlignment(.center)
                .foregroundStyle(.secondary)
            Text("Fix it in a text editor, then reload. The app won't overwrite it.")
                .font(.callout)
                .foregroundStyle(.secondary)
            HStack {
                Button("Open in Editor", action: openInEditor)
                Button("Reload", action: load)
            }
        }
        .padding(30)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private var footer: some View {
        HStack {
            Button("Open in Editor", action: openInEditor)
                .disabled(!fileExists)
                .help("Open config.json in your default editor")
            Button("Revert") { config = saved }
                .disabled(config == saved)
            if let saveError {
                Text(saveError)
                    .font(.caption)
                    .foregroundStyle(.red)
                    .lineLimit(2)
            }
            Spacer()
            Button("Cancel") { dismiss() }
                .keyboardShortcut(.cancelAction)
            Button("Save", action: save)
                .keyboardShortcut(.defaultAction)
                .disabled(loadError != nil || !problems.isEmpty || (config == saved && fileExists))
        }
        .padding(14)
    }

    /// A limit: its field, the allowed range, and the value in effect now (yours or the default).
    private func numberField(_ title: String, text: Binding<String>, placeholder: String, key: String,
                             range: String = "", unit: String = "") -> some View {
        let value = text.wrappedValue.trimmingCharacters(in: .whitespaces)
        let effective = value.isEmpty ? placeholder : value
        let now = effective == "0" ? "no limit" : "\(effective) \(unit)".trimmingCharacters(in: .whitespaces)
        return VStack(alignment: .leading, spacing: 3) {
            TextField(title, text: text, prompt: Text("Not set (\(placeholder))"))
            HStack(spacing: 6) {
                Text("Now: \(now)\(value.isEmpty ? " (default)" : "")")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(effective == "0" ? Color.orange : Color.teal)
                if !range.isEmpty {
                    Text("· \(range)").font(.caption).foregroundStyle(.secondary)
                }
            }
            if let problem = problems[key] {
                Text(problem)
                    .font(.caption)
                    .foregroundStyle(.red)
            }
        }
    }

    private func note(_ text: String, warning: Bool = false) -> some View {
        Text(text)
            .font(.caption)
            .foregroundStyle(warning ? AnyShapeStyle(.orange) : AnyShapeStyle(.secondary))
            .fixedSize(horizontal: false, vertical: true)
    }

    // MARK: Actions

    private func load() {
        do {
            saved = try OrwickConfig.load(from: fileURL)
            config = saved
            loadError = nil
        } catch {
            loadError = error.localizedDescription
        }
    }

    private func save() {
        do {
            try config.save(to: fileURL)
            saved = config
            dismiss()
        } catch {
            saveError = "Couldn't save: \(error.localizedDescription)"
        }
    }

    private func openInEditor() {
        NSWorkspace.shared.open(fileURL)
    }
}

private struct MCPToolRow: View {
    let tool: ToolInfo.MCPServer.Tool
    @Binding var isOn: Bool

    var body: some View {
        Toggle(isOn: $isOn) {
            VStack(alignment: .leading, spacing: 2) {
                HStack {
                    Text(tool.shortName).font(.callout.monospaced().weight(.medium))
                    Text(tool.risk == "read" ? "read · runs without asking" : "asks you")
                        .font(.caption)
                        .foregroundStyle(tool.risk == "read" ? Color.green : Color.orange)
                }
                Text(tool.description)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
                    .help(tool.description)
            }
        }
        .toggleStyle(.switch)
    }
}
