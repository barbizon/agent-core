import SwiftUI

/// A session's docs below its terminal: the Markdown notes Orwick keeps in
/// `.orwick/sessions/<id>/docs/` (written by skills such as feature-docs, with your
/// approval), as tabs. Read only here; refreshed while Orwick writes them.
struct DocsPane: View {
    let title: String
    let folder: URL               // the session's folder
    let close: () -> Void
    @State private var selected: String?
    @State private var docs: [URL] = []
    @State private var text = ""
    private let ticker = Timer.publish(every: 2, on: .main, in: .common).autoconnect()

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 8) {
                Image(systemName: "doc.text.fill").foregroundStyle(.teal)
                Text("Docs").font(.headline)
                Spacer()
                Button {
                    NSWorkspace.shared.activateFileViewerSelecting([folder.appendingPathComponent("docs")])
                } label: {
                    Image(systemName: "folder")
                }
                .buttonStyle(.borderless)
                .help("Show the docs folder in Finder")
                Button(action: close) {
                    Image(systemName: "xmark")
                }
                .buttonStyle(.borderless)
                .help("Hide the docs")
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
            if docs.isEmpty {
                Divider()
                Text("No docs yet. Click Read ticket, or ask Orwick to make the feature docs.")
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                    .padding(20)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 2) {
                        ForEach(docs, id: \.lastPathComponent) { doc in
                            tab(doc)
                        }
                    }
                    .padding(.horizontal, 8)
                }
                Divider()
                ScrollView {
                    MarkdownText(text: text)
                        .padding(18)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
        }
        .background(Color(nsColor: .textBackgroundColor))
        .onAppear(perform: reload)
        .onReceive(ticker) { _ in reload() }
    }

    private func tab(_ doc: URL) -> some View {
        let name = doc.lastPathComponent
        let isCurrent = current == name
        return Button {
            selected = name
            reload()
        } label: {
            Text(SessionDocs.label(doc))
                .font(.callout.weight(isCurrent ? .semibold : .regular))
                .padding(.horizontal, 10)
                .padding(.vertical, 6)
                .foregroundStyle(isCurrent ? Color.primary : Color.secondary)
                .overlay(alignment: .bottom) {
                    Rectangle().fill(isCurrent ? Color.teal : .clear).frame(height: 2)
                }
        }
        .buttonStyle(.plain)
    }

    private var current: String? {
        if let selected, docs.contains(where: { $0.lastPathComponent == selected }) { return selected }
        return docs.first?.lastPathComponent
    }

    private func reload() {
        let fresh = SessionDocs.ordered(SessionDocs.list(in: folder))
        if fresh != docs { docs = fresh }
        let newText = docs.first { $0.lastPathComponent == current }.map(SessionDocs.read) ?? ""
        if newText != text { text = newText }
    }
}

enum SessionDocs {
    static func list(in sessionFolder: URL) -> [URL] {
        let folder = sessionFolder.appendingPathComponent("docs", isDirectory: true)
        let files = (try? FileManager.default.contentsOfDirectory(at: folder, includingPropertiesForKeys: nil)) ?? []
        return files.filter { $0.pathExtension == "md" && (try? $0.resourceValues(forKeys: [.isSymbolicLinkKey]).isSymbolicLink) != true }
            .sorted { $0.lastPathComponent < $1.lastPathComponent }
    }

    static func hasDocs(_ sessionFolder: URL) -> Bool { !list(in: sessionFolder).isEmpty }

    /// The known tabs, in order: feature-docs' five, then code-check's two.
    static let tabs: [(name: String, label: String)] = [
        ("product-requirement", "Product Requirement"), ("flows", "Flows"), ("technical-design", "Technical Design"),
        ("todo", "To Do"), ("code-review", "Code Review"),
        ("review-findings", "Review Findings"), ("grade", "Grade"),
    ]

    /// The known tabs first (in their order), then any other docs by name.
    static func ordered(_ docs: [URL]) -> [URL] {
        let rank = Dictionary(uniqueKeysWithValues: tabs.enumerated().map { ($1.name, $0) })
        return docs.sorted {
            let (a, b) = ($0.deletingPathExtension().lastPathComponent, $1.deletingPathExtension().lastPathComponent)
            return (rank[a] ?? 99, a) < (rank[b] ?? 99, b)
        }
    }

    static func label(_ doc: URL) -> String {
        let name = doc.deletingPathExtension().lastPathComponent
        return tabs.first { $0.name == name }?.label ?? name
    }

    static func read(_ url: URL) -> String {
        (try? String(contentsOf: url, encoding: .utf8)) ?? "(couldn't read \(url.lastPathComponent))"
    }
}

/// Small Markdown renderer for docs: headings, bullets, code blocks, and inline
/// styles (bold, italic, code, links) within lines.
struct MarkdownText: View {
    let text: String

    private enum Block: Hashable {
        case heading(Int, String), bullet(String), code(String, String), paragraph(String), rule, table([[String]])
    }

    private var blocks: [Block] {
        var result: [Block] = []
        var code: [String]? = nil
        var codeKind = ""
        var paragraph: [String] = []
        var table: [[String]] = []
        func flushTable() {
            if !table.isEmpty { result.append(.table(table)); table = [] }
        }
        func flush() {
            // Lines inside a paragraph keep their breaks (Epic / Story / PM lines, ticket text).
            if !paragraph.isEmpty { result.append(.paragraph(paragraph.joined(separator: "\n"))); paragraph = [] }
        }
        for line in text.components(separatedBy: "\n") {
            if line.hasPrefix("```") {
                if let lines = code { result.append(.code(codeKind, lines.joined(separator: "\n"))); code = nil }
                else { flush(); flushTable(); code = []; codeKind = String(line.dropFirst(3)).trimmingCharacters(in: .whitespaces) }
                continue
            }
            if code != nil { code!.append(line); continue }
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            if trimmed.hasPrefix("|") && trimmed.hasSuffix("|") && trimmed.count > 1 {
                flush()
                let cells = trimmed.dropFirst().dropLast().components(separatedBy: "|")
                    .map { $0.trimmingCharacters(in: .whitespaces) }
                if !cells.allSatisfy({ $0.allSatisfy { "-: ".contains($0) } }) { table.append(cells) }   // skip |---|
                continue
            }
            flushTable()
            if trimmed.isEmpty { flush(); continue }
            if trimmed == "---" { flush(); result.append(.rule); continue }
            if let hashes = trimmed.prefix(while: { $0 == "#" }).count as Int?, hashes > 0, hashes <= 4,
               trimmed.dropFirst(hashes).hasPrefix(" ") {
                flush(); result.append(.heading(hashes, String(trimmed.dropFirst(hashes + 1)))); continue
            }
            if trimmed.hasPrefix("- ") || trimmed.hasPrefix("* ") {
                flush(); result.append(.bullet(String(trimmed.dropFirst(2)))); continue
            }
            paragraph.append(trimmed)
        }
        if let lines = code { result.append(.code(codeKind, lines.joined(separator: "\n"))) }
        flush()
        flushTable()
        return result
    }

    /// Section colors, one per `##` heading in turn (like colored stage labels).
    static let palette: [Color] = [.teal, .indigo, .orange, .pink, .green, .blue, .purple, .brown, .cyan, .mint]

    /// Status words and marks that get a color wherever they appear.
    static let highlights: [(String, Color)] = [
        ("Merged", .green), ("Done", .green), ("Closed", .green), ("✅", .green),
        ("In review", .purple), ("In progress", .orange), ("To Do", .blue),
        ("(guess)", .purple), ("unknown", .gray), ("Not found", .gray),
    ]

    var body: some View {
        let items = Array(blocks.enumerated())
        let sectionColor = sectionColors(items.map(\.element))
        VStack(alignment: .leading, spacing: 8) {
            ForEach(items, id: \.offset) { index, block in
                switch block {
                case .heading(let level, let text):
                    heading(level, text, color: sectionColor[index] ?? .teal)
                case .bullet(let text):
                    HStack(alignment: .firstTextBaseline, spacing: 7) {
                        Circle().fill(sectionColor[index] ?? .secondary).frame(width: 5, height: 5)
                            .alignmentGuide(.firstTextBaseline) { $0[.bottom] - 1 }
                        inline(text)
                    }
                case .code(let kind, let text) where kind == "mermaid":
                    MermaidView(source: text, tint: sectionColor[index] ?? .teal)
                case .code(_, let text):
                    VStack(alignment: .leading, spacing: 4) {
                        codeText(text)
                            .font(.callout.monospaced())
                            .padding(10)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .background(.quaternary, in: RoundedRectangle(cornerRadius: 8))
                    }
                case .table(let rows):
                    MarkdownTable(rows: rows, tint: sectionColor[index] ?? .teal, inline: inline)
                case .paragraph(let text):
                    inline(text)
                case .rule:
                    Divider()
                }
            }
        }
        .textSelection(.enabled)
    }

    @ViewBuilder
    private func heading(_ level: Int, _ text: String, color: Color) -> some View {
        switch level {
        case 1:
            inline(text)
                .font(.title2.weight(.bold))
                .foregroundStyle(color)
        case 2:
            HStack(spacing: 8) {
                RoundedRectangle(cornerRadius: 2).fill(color).frame(width: 4, height: 18)
                inline(text).font(.title3.weight(.semibold))
            }
            .padding(.top, 8)
        default:
            inline(text)
                .font(.headline)
                .foregroundStyle(color)
                .padding(.top, 4)
        }
    }

    /// Each block's section color: the color of the `##` heading above it.
    private func sectionColors(_ blocks: [Block]) -> [Int: Color] {
        var result: [Int: Color] = [:]
        var section = -1
        for (index, block) in blocks.enumerated() {
            if case .heading(let level, _) = block, level <= 2 { section += level == 2 ? 1 : 0 }
            result[index] = Self.palette[max(section, 0) % Self.palette.count]
        }
        return result
    }

    /// A code block as is, except that ✅ / ✏️ / 🆕 labels (in diagrams) get their color.
    private func codeText(_ text: String) -> Text {
        var attributed = AttributedString(text)
        DocMark.colorLabels(in: &attributed)
        return Text(attributed)
    }

    private func inline(_ text: String) -> Text {
        let options = AttributedString.MarkdownParsingOptions(interpretedSyntax: .inlineOnlyPreservingWhitespace)
        guard var attributed = try? AttributedString(markdown: text, options: options) else { return Text(text) }
        for (word, color) in Self.highlights {
            var searchFrom = attributed.startIndex
            while searchFrom < attributed.endIndex,
                  let range = attributed[searchFrom..<attributed.endIndex].range(of: word) {
                attributed[range].foregroundColor = color
                attributed[range].inlinePresentationIntent = .stronglyEmphasized
                searchFrom = range.upperBound
            }
        }
        for run in attributed.runs where run.inlinePresentationIntent?.contains(.code) == true {
            attributed[run.range].foregroundColor = .pink
        }
        DocMark.colorLabels(in: &attributed)
        return Text(attributed)
    }
}

/// A Markdown table: the first row is the header.
private struct MarkdownTable: View {
    let rows: [[String]]
    let tint: Color
    let inline: (String) -> Text

    var body: some View {
        let columns = rows.map(\.count).max() ?? 0
        Grid(alignment: .leading, horizontalSpacing: 0, verticalSpacing: 0) {
            ForEach(Array(rows.enumerated()), id: \.offset) { index, row in
                let rowMark = index == 0 ? nil : DocMark.ofRow(row)
                GridRow {
                    ForEach(0..<columns, id: \.self) { column in
                        cell(column < row.count ? row[column] : "", header: index == 0)
                            .font(index == 0 ? .caption.weight(.semibold) : .callout)
                            .foregroundStyle(index == 0 ? tint : .primary)
                            .textCase(index == 0 ? .uppercase : nil)
                            .fixedSize(horizontal: false, vertical: true)
                            .padding(.horizontal, 10)
                            .padding(.vertical, 6)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .background(index == 0 ? tint.opacity(0.12)
                                        : rowMark?.rowTint ?? (index % 2 == 0 ? Color.secondary.opacity(0.05) : .clear))
                    }
                }
            }
        }
        .clipShape(RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(tint.opacity(0.25)))
    }

    /// A cell that starts with ✅ / ✏️ / 🆕 shows a colored badge, then the rest of its text.
    @ViewBuilder
    private func cell(_ text: String, header: Bool) -> some View {
        if !header, let (mark, rest) = DocMark.leading(text) {
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                DocMarkBadge(mark: mark)
                if !rest.isEmpty { inline(rest) }
            }
        } else {
            inline(text)
        }
    }
}
