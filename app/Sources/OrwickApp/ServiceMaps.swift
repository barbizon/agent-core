import SwiftUI

/// One service map in the library, `~/.orwick/maps/<service>/` (written by Orwick's
/// service-map skill, checked against its contract): the docs, in the contract's order,
/// and what was scanned.
struct ServiceMap: Identifiable, Equatable {
    let name: String
    let library: URL              // ~/.orwick/maps/<name>
    let folder: URL?              // the service's own folder
    let related: [URL]
    let question: String?
    let updatedAt: Date
    let docs: [String]
    let titles: [String: String]

    var id: String { name }

    static var libraryURL: URL {
        FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".orwick/maps", isDirectory: true)
    }

    /// Every map in the library, the most recently changed first.
    static func all(in library: URL = libraryURL) -> [ServiceMap] {
        let folders = (try? FileManager.default.contentsOfDirectory(at: library, includingPropertiesForKeys: nil)) ?? []
        return folders.compactMap(read).sorted { $0.updatedAt > $1.updatedAt }
    }

    static func read(_ folder: URL) -> ServiceMap? {
        guard let data = try? Data(contentsOf: folder.appendingPathComponent("meta.json")),
              let meta = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return nil }
        let present = Set(((try? FileManager.default.contentsOfDirectory(at: folder, includingPropertiesForKeys: nil)) ?? [])
            .filter { $0.pathExtension == "md" }.map { $0.deletingPathExtension().lastPathComponent })
        let ordered = (meta["docs"] as? [String] ?? []).filter(present.contains)
        return ServiceMap(
            name: folder.lastPathComponent, library: folder,
            folder: (meta["folder"] as? String).map { URL(fileURLWithPath: $0) },
            related: (meta["related"] as? [String] ?? []).map { URL(fileURLWithPath: $0) },
            question: meta["question"] as? String,
            updatedAt: Date(timeIntervalSince1970: meta["updated_at"] as? Double ?? 0),
            docs: ordered + present.subtracting(ordered).sorted(),
            titles: meta["titles"] as? [String: String] ?? [:])
    }

    /// A map name as the library keeps it: lowercase, words joined by - ("Refund job" → refund-job).
    static func slug(_ text: String) -> String {
        let words = text.lowercased().components(separatedBy: CharacterSet(charactersIn: "abcdefghijklmnopqrstuvwxyz0123456789_").inverted)
            .filter { !$0.isEmpty }
        return String(words.joined(separator: "-").prefix(60)).trimmingCharacters(in: CharacterSet(charactersIn: "-_"))
    }

    func title(of doc: String) -> String { titles[doc] ?? doc }
    func url(of doc: String) -> URL { library.appendingPathComponent(doc + ".md") }
}

/// A service map in the detail area: what was scanned, then one tab per doc; diagrams are
/// drawn. Refreshed while Orwick updates it.
struct ServiceMapView: View {
    @EnvironmentObject private var store: SessionStore
    let map: ServiceMap
    @State private var selected: String?
    @State private var text = ""
    @State private var editingName = false
    @State private var newName = ""
    @State private var renameProblem: String?
    @FocusState private var nameFocused: Bool
    private let ticker = Timer.publish(every: 2, on: .main, in: .common).autoconnect()

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            Divider()
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 2) {
                    ForEach(map.docs, id: \.self) { doc in
                        Button {
                            selected = doc
                            load()
                        } label: {
                            Text(map.title(of: doc))
                                .font(.callout.weight(current == doc ? .semibold : .regular))
                                .padding(.horizontal, 10)
                                .padding(.vertical, 5)
                                .background(current == doc ? Color.accentColor.opacity(0.15) : .clear,
                                            in: RoundedRectangle(cornerRadius: 6))
                        }
                        .buttonStyle(.plain)
                    }
                }
                .padding(.horizontal, 10)
                .padding(.vertical, 6)
            }
            Divider()
            ScrollView {
                MarkdownText(text: text)
                    .padding(18)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .onAppear(perform: load)
        .onChange(of: map) { load() }
        .onChange(of: map.name) { editingName = false }
        .onReceive(ticker) { _ in load() }
    }

    private var current: String? { selected.flatMap { map.docs.contains($0) ? $0 : nil } ?? map.docs.first }

    private var header: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: "point.3.connected.trianglepath.dotted")
                .font(.title2)
                .foregroundStyle(.orange)
            VStack(alignment: .leading, spacing: 3) {
                if editingName {
                    TextField("Map name", text: $newName)
                        .textFieldStyle(.roundedBorder)
                        .font(.title3.weight(.semibold))
                        .frame(maxWidth: 360)
                        .focused($nameFocused)
                        .onSubmit { commitName() }
                        .onExitCommand { editingName = false; renameProblem = nil }
                        .onChange(of: nameFocused) { if !nameFocused && editingName { commitName(stayOnProblem: false) } }
                    if let renameProblem {
                        Text(renameProblem).font(.caption).foregroundStyle(.red)
                    }
                } else {
                    Text(map.name).font(.title3.weight(.semibold))
                        .contentShape(Rectangle())
                        .onTapGesture {
                            newName = map.name
                            renameProblem = nil
                            editingName = true
                            DispatchQueue.main.async { nameFocused = true }
                        }
                        .help("Click to rename")
                }
                if let folder = map.folder {
                    Text(FolderTree.short(folder)).font(.caption.monospaced()).foregroundStyle(.secondary)
                }
                if !map.related.isEmpty {
                    Text("Talks to: " + map.related.map { $0.lastPathComponent }.joined(separator: ", "))
                        .font(.caption).foregroundStyle(.secondary)
                }
                if let question = map.question, !question.isEmpty {
                    Text("“\(question)”").font(.caption).italic().foregroundStyle(.secondary)
                }
                Text("Updated " + map.updatedAt.formatted(.relative(presentation: .named)))
                    .font(.caption2).foregroundStyle(.tertiary)
            }
            Spacer()
            if let folder = map.folder {
                Button {
                    store.rebuildMap(map)
                } label: {
                    Label("Rebuild", systemImage: "arrow.triangle.2.circlepath")
                }
                .help("Scan \(FolderTree.short(folder)) again from scratch and replace this map (in its session, or a new one)")
            }
            Button {
                NSWorkspace.shared.activateFileViewerSelecting([map.library])
            } label: {
                Image(systemName: "folder")
            }
            .buttonStyle(.borderless)
            .help("Show the map's files in Finder")
            Button { store.deleteMap(map) } label: { Image(systemName: "trash") }
                .buttonStyle(.borderless)
                .help("Delete this service map (to the Trash)")
        }
        .padding(14)
    }

    /// Enter renames; a problem keeps the field open and says why. Clicking away renames
    /// too, or, on a problem, leaves the old name.
    private func commitName(stayOnProblem: Bool = true) {
        guard editingName else { return }
        if let problem = store.renameMap(map, to: newName) {
            if stayOnProblem {
                renameProblem = problem
                DispatchQueue.main.async { nameFocused = true }
            } else {
                editingName = false
                renameProblem = nil
            }
        } else {
            editingName = false
            renameProblem = nil
        }
    }

    private func load() {
        guard let doc = current else { text = ""; return }
        let fresh = (try? String(contentsOf: map.url(of: doc), encoding: .utf8)) ?? ""
        if fresh != text { text = fresh }
    }
}
