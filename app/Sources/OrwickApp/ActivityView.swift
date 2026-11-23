import SwiftUI

/// What the middle of a session shows: its console, or one of the activity screens (read from
/// the session's files, no AI). The console keeps running underneath.
enum SessionScreen: String, CaseIterable, Identifiable {
    case console, messages, steps, calls
    var id: String { rawValue }
    var title: String {
        switch self {
        case .console: return "Console"
        case .messages: return "Messages"
        case .steps: return "AI steps"
        case .calls: return "Tool calls"
        }
    }
}

/// Messages, AI steps or tool calls of one session, with a bar to switch between them and back
/// to the console.
struct ActivityView: View {
    @ObservedObject var session: Session
    @State private var log = SessionLog()
    @State private var stamp: Date?
    @State private var open: Set<String> = []
    @State private var failedOnly = false
    @State private var filter = ""
    private let ticker = Timer.publish(every: 2, on: .main, in: .common).autoconnect()

    var body: some View {
        VStack(spacing: 0) {
            bar
            Divider()
            ScrollViewReader { proxy in
            ScrollView {
                Color.clear.frame(height: 0).id("top")
                LazyVStack(alignment: .leading, spacing: 12) {
                    if log.turns.isEmpty {
                        Text("Nothing yet: send a message in the console.")
                            .foregroundStyle(.secondary)
                            .padding(.top, 30)
                            .frame(maxWidth: .infinity)
                    }
                    switch session.screen {
                    case .messages: messages
                    case .steps: steps
                    default: calls
                    }
                }
                .padding(16)
                // Each screen is a new list: the three show the same messages (same ids), and a
                // lazy list would otherwise keep showing the rows of the screen before.
                .id(session.screen)
                Color.clear.frame(height: 0).id("bottom")
            }
            .onChange(of: session.scrollRequest.count) {
                let top = session.scrollRequest.toTop
                proxy.scrollTo(top ? "top" : "bottom", anchor: top ? .top : .bottom)
            }
            }
        }
        .background(Color(nsColor: .textBackgroundColor))
        .onAppear(perform: load)
        .onReceive(ticker) { _ in load() }
        .onExitCommand { session.screen = .console }
    }

    private var bar: some View {
        HStack(spacing: 10) {
            Button { session.screen = .console } label: { Label("Console", systemImage: "chevron.left") }
                .help("Back to the console (Esc)")
            Picker("", selection: $session.screen) {
                Text("Messages \(log.turns.count)").tag(SessionScreen.messages)
                Text("AI steps \(log.stepCount)").tag(SessionScreen.steps)
                Text("Tool calls \(log.callCount)").tag(SessionScreen.calls)
            }
            .pickerStyle(.segmented)
            .fixedSize()
            Spacer()
            if session.screen == .calls {
                Toggle("Failed only", isOn: $failedOnly).toggleStyle(.checkbox)
                TextField("Filter tool or argument", text: $filter)
                    .textFieldStyle(.roundedBorder)
                    .frame(width: 200)
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
    }

    // MARK: Messages

    @ViewBuilder private var messages: some View {
        ForEach(log.turns.reversed()) { turn in
            VStack(alignment: .leading, spacing: 8) {
                turnHeader(turn)
                expandable(id: "msg-\(turn.id)", text: turn.message, lines: 4)
                    .padding(10)
                    .background(Color.accentColor.opacity(0.08), in: RoundedRectangle(cornerRadius: 8))
                if let answer = turn.answer {
                    MarkdownText(text: answer)
                        .padding(.horizontal, 4)
                } else if turn.running {
                    Label("Working… step \(turn.steps.count)", systemImage: "hourglass").foregroundStyle(.secondary)
                }
                if let error = turn.error {
                    Label(error, systemImage: "exclamationmark.triangle").foregroundStyle(.orange)
                }
                HStack(spacing: 14) {
                    Button("\(turn.steps.count) AI steps") { show(.steps, turn) }
                    Button("\(turn.calls.count) tool calls" + (turn.failedCalls > 0 ? " (✗ \(turn.failedCalls))" : "")) { show(.calls, turn) }
                }
                .buttonStyle(.link)
                .font(.caption)
            }
            .padding(12)
            .background(.background, in: RoundedRectangle(cornerRadius: 10))
            .overlay(RoundedRectangle(cornerRadius: 10).stroke(.quaternary))
            .id("msg-" + turn.id)
        }
    }

    private func turnHeader(_ turn: SessionLog.Turn) -> some View {
        HStack {
            Text("Message \(turn.number)").font(.headline)
            Text(turn.started.formatted(date: .abbreviated, time: .shortened)).foregroundStyle(.secondary)
            Spacer()
            Text(turn.running ? "running \(Format.duration(Date().timeIntervalSince(turn.started)))"
                 : turn.finished.map { Format.duration($0.timeIntervalSince(turn.started)) } ?? "")
                .font(.callout.monospacedDigit()).foregroundStyle(.secondary)
        }
    }

    // MARK: AI steps

    @ViewBuilder private var steps: some View {
        ForEach(log.turns.reversed()) { turn in
            VStack(alignment: .leading, spacing: 6) {
                turnHeader(turn)
                Text(firstLine(turn.message)).font(.callout).foregroundStyle(.secondary).lineLimit(1)
                ForEach(turn.steps) { step in
                    let calls = turn.calls.filter { $0.step == step.number }
                    VStack(alignment: .leading, spacing: 3) {
                        HStack(alignment: .firstTextBaseline, spacing: 8) {
                            Text("\(step.number)").font(.callout.monospacedDigit().weight(.semibold)).frame(width: 26, alignment: .trailing)
                            Image(systemName: step.tools.isEmpty ? "text.bubble" : "wrench.and.screwdriver")
                                .foregroundStyle(step.tools.isEmpty ? Color.green : Color.accentColor)
                            Text(step.tools.isEmpty ? (step.responded == nil ? "thinking…" : "answered")
                                 : "asked for " + toolList(step.tools))
                                .font(.callout)
                            Spacer()
                            if let thinking = step.thinking {
                                Text("AI \(seconds(thinking))").help("How long the AI took for this step")
                            }
                            if let tools = step.toolsTime, !step.tools.isEmpty {
                                Text("tools \(seconds(tools))").help("From the AI's answer to the next step: tools, approvals")
                            }
                        }
                        .font(.caption.monospacedDigit())
                        .foregroundStyle(.secondary)
                        if !calls.isEmpty {
                            Text(calls.map { "\($0.ok == false ? "✗" : "·") \($0.tool) \($0.summary)" }.joined(separator: "\n"))
                                .font(.caption.monospaced())
                                .foregroundStyle(.secondary)
                                .lineLimit(open.contains(step.id) ? nil : 3)
                                .padding(.leading, 60)
                                .onTapGesture { toggle(step.id) }
                        }
                    }
                }
            }
            .padding(12)
            .background(.background, in: RoundedRectangle(cornerRadius: 10))
            .overlay(RoundedRectangle(cornerRadius: 10).stroke(.quaternary))
            .id("steps-" + turn.id)
        }
    }

    // MARK: Tool calls

    @ViewBuilder private var calls: some View {
        ForEach(log.turns.reversed()) { turn in
            let shown = turn.calls.filter(matches)
            if !shown.isEmpty {
                VStack(alignment: .leading, spacing: 4) {
                    turnHeader(turn)
                    Text(firstLine(turn.message)).font(.callout).foregroundStyle(.secondary).lineLimit(1)
                    ForEach(shown) { call in callRow(call) }
                }
                .padding(12)
                .background(.background, in: RoundedRectangle(cornerRadius: 10))
                .overlay(RoundedRectangle(cornerRadius: 10).stroke(.quaternary))
                .id("calls-" + turn.id)
            }
        }
    }

    private func callRow(_ call: SessionLog.Call) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Image(systemName: call.ok == nil ? "clock" : call.ok! ? "checkmark.circle.fill" : "xmark.circle.fill")
                    .foregroundStyle(call.ok == nil ? Color.secondary : call.ok! ? Color.green : Color.red)
                Text(call.tool).font(.callout.monospaced().weight(.medium))
                Text(call.summary).font(.caption.monospaced()).foregroundStyle(.secondary).lineLimit(1).truncationMode(.middle)
                Spacer()
                if let approval = call.approval { approvalBadge(approval) }
                Text("step \(call.step)").font(.caption2).foregroundStyle(.tertiary)
                if let ms = call.durationMs { Text(seconds(ms / 1000)).font(.caption.monospacedDigit()).foregroundStyle(.secondary) }
                Image(systemName: "chevron.right")
                    .font(.caption2)
                    .rotationEffect(.degrees(open.contains(call.id) ? 90 : 0))
                    .foregroundStyle(.tertiary)
            }
            .contentShape(Rectangle())
            .onTapGesture { toggle(call.id) }
            if open.contains(call.id) {
                VStack(alignment: .leading, spacing: 6) {
                    Text(call.at.formatted(date: .omitted, time: .standard)).font(.caption).foregroundStyle(.secondary)
                    if let error = call.error {
                        Text(error).font(.caption).foregroundStyle(.red).textSelection(.enabled)
                    }
                    block("Arguments", call.arguments)
                    if let result = call.result { block("Result", result.count > 6000 ? String(result.prefix(6000)) + "\n… (\(result.count - 6000) more characters)" : result) }
                }
                .padding(.leading, 24)
            }
        }
        .padding(.vertical, 3)
    }

    private func approvalBadge(_ approval: SessionLog.Call.Approval) -> some View {
        let (text, color): (String, Color) = {
            switch approval {
            case .asked: return ("waiting for you", .orange)
            case .granted(let after): return ("approved after \(seconds(after))", .secondary)
            case .denied: return ("denied", .red)
            }
        }()
        return Text(text).font(.caption2).foregroundStyle(color)
            .padding(.horizontal, 5).background(.quaternary.opacity(0.6), in: Capsule())
    }

    private func block(_ title: String, _ text: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title.uppercased()).font(.caption2.weight(.semibold)).foregroundStyle(.secondary)
            Text(text)
                .font(.caption.monospaced())
                .textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(8)
                .background(.quaternary.opacity(0.5), in: RoundedRectangle(cornerRadius: 6))
        }
    }

    private func matches(_ call: SessionLog.Call) -> Bool {
        if failedOnly && call.ok != false { return false }
        let wanted = filter.trimmingCharacters(in: .whitespaces).lowercased()
        return wanted.isEmpty || call.tool.lowercased().contains(wanted) || call.arguments.lowercased().contains(wanted)
    }

    // MARK: Helpers

    private func expandable(id: String, text: String, lines: Int) -> some View {
        Text(text)
            .font(.callout)
            .textSelection(.enabled)
            .lineLimit(open.contains(id) ? nil : lines)
            .frame(maxWidth: .infinity, alignment: .leading)
            .contentShape(Rectangle())
            .onTapGesture { toggle(id) }
            .help(open.contains(id) ? "Click to shorten" : "Click to show all")
    }

    private func show(_ screen: SessionScreen, _ turn: SessionLog.Turn) {
        session.screen = screen
    }

    private func toggle(_ id: String) {
        if open.contains(id) { open.remove(id) } else { open.insert(id) }
    }

    private func toolList(_ tools: [String]) -> String {
        var counts: [(String, Int)] = []
        for tool in tools {
            if let i = counts.firstIndex(where: { $0.0 == tool }) { counts[i].1 += 1 } else { counts.append((tool, 1)) }
        }
        return counts.map { $0.1 > 1 ? "\($0.0) ×\($0.1)" : $0.0 }.joined(separator: ", ")
    }

    private func firstLine(_ text: String) -> String {
        text.split(separator: "\n").first.map(String.init) ?? text
    }

    private func seconds(_ value: Double) -> String {
        value < 10 ? String(format: "%.1fs", value) : Format.duration(value)
    }

    /// Reads the files again only when they changed.
    private func load() {
        let audit = session.sessionFolder.appendingPathComponent("audit.jsonl")
        let conversation = session.sessionFolder.appendingPathComponent("conversation.json")
        let changed = [audit, conversation]
            .compactMap { try? $0.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate }
            .max()
        guard changed != stamp || log.turns.isEmpty else { return }
        stamp = changed
        let fresh = SessionLog.load(session.sessionFolder)
        if fresh != log { log = fresh }
    }
}
