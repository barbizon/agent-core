import SwiftUI

/// Reading the folder tree: only folders, no hidden ones, no packages (.app), sorted by name.
enum FolderTree {
    static func subfolders(of folder: URL) -> [URL] {
        let keys: [URLResourceKey] = [.isDirectoryKey, .isPackageKey]
        let items = (try? FileManager.default.contentsOfDirectory(
            at: folder, includingPropertiesForKeys: keys, options: [.skipsHiddenFiles])) ?? []
        return items.filter {
            let values = try? $0.resourceValues(forKeys: Set(keys))
            return values?.isDirectory == true && values?.isPackage != true
        }
        .sorted { $0.lastPathComponent.localizedStandardCompare($1.lastPathComponent) == .orderedAscending }
    }

    /// The folders to open so `folder` shows in a tree that starts at `root`: root and every
    /// folder between it and `folder` (not `folder` itself). Empty when `folder` isn't under root.
    static func pathToShow(_ folder: URL, under root: URL) -> Set<String> {
        let rootPath = root.standardizedFileURL.path
        var current = folder.standardizedFileURL.deletingLastPathComponent()
        guard folder.standardizedFileURL.path.hasPrefix(rootPath + "/") else { return [] }
        var result: Set<String> = []
        while current.path.count >= rootPath.count {
            result.insert(current.path)
            if current.path == rootPath { break }
            current = current.deletingLastPathComponent()
        }
        return result
    }

    /// The folder already has Orwick sessions (a .orwick/sessions folder).
    static func hasSessions(_ folder: URL) -> Bool {
        FileManager.default.fileExists(atPath: folder.appendingPathComponent(".orwick/sessions").path)
    }

    /// `~/Documents/go` instead of `/Users/me/Documents/go`.
    static func short(_ folder: URL) -> String {
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        let path = folder.path
        return path == home ? "~" : path.hasPrefix(home + "/") ? "~" + path.dropFirst(home.count) : path
    }
}

/// New Session: a panel that slides out next to the sidebar, with recent folders and a folder
/// tree to pick from. In the tree a click opens a folder; a double-click or Start starts Orwick
/// there and closes the panel. A recent folder starts on a click.
struct FolderPanel: View {
    @EnvironmentObject private var store: SessionStore
    @State private var selection: URL?
    @State private var expanded: Set<String> = []
    // Service map mode: the chosen service, folders elsewhere to read too, an optional flow.
    @State private var mapFolder: URL?
    @State private var related: [URL] = []
    @State private var focus = ""
    @State private var mapName = ""
    private let root = FileManager.default.homeDirectoryForCurrentUser

    private var forMap: Bool { store.folderPanelMode == .map }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            Divider()
            ScrollViewReader { scroller in
                ScrollView {
                    VStack(alignment: .leading, spacing: 0) {
                        if !store.availableRecentFolders.isEmpty {
                            sectionTitle("Recent")
                            ForEach(store.availableRecentFolders, id: \.self) { path in
                                let url = URL(fileURLWithPath: path)
                                FolderRow(url: url, title: FolderTree.short(url), depth: 0, expandable: false,
                                          expanded: $expanded, selection: $selection, start: start,
                                          actionLabel: forMap ? "Choose" : "Start")
                            }
                            sectionTitle("Folders")
                        }
                        FolderRow(url: root, title: "~ " + root.lastPathComponent, depth: 0, expandable: true,
                                  expanded: $expanded, selection: $selection, start: start,
                                          actionLabel: forMap ? "Choose" : "Start")
                    }
                    .padding(.vertical, 6)
                }
                .onAppear {
                    if let first = store.availableRecentFolders.first.map({ URL(fileURLWithPath: $0) }) {
                        selection = first
                        expanded = FolderTree.pathToShow(first, under: root)
                    } else {
                        expanded = [root.path, root.appendingPathComponent("Documents").path]
                    }
                    if let selection { DispatchQueue.main.async { scroller.scrollTo("tree:" + selection.path, anchor: .center) } }
                }
            }
            Divider()
            if forMap, let mapFolder {
                mapForm(mapFolder)
            } else {
                footer
            }
        }
        .frame(width: 340)
        .frame(maxHeight: .infinity)
        .background(.regularMaterial)
        .overlay(alignment: .trailing) { Divider() }
        .shadow(color: .black.opacity(0.18), radius: 12, x: 4)
        .onExitCommand { close() }
    }

    private var header: some View {
        HStack(alignment: .top) {
            VStack(alignment: .leading, spacing: 2) {
                Text(forMap ? "New Service Map" : "New Session").font(.headline)
                Text(forMap ? "Pick the service. Orwick finds what it talks to."
                            : "Pick a folder. Orwick works only inside it.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            Button { close() } label: { Image(systemName: "xmark") }
                .buttonStyle(.borderless)
                .help("Close (Esc)")
        }
        .padding(12)
    }

    private var footer: some View {
        HStack {
            Text(forMap ? "Click opens · double-click or Choose picks" : "Click opens · double-click or Start begins")
                .font(.caption)
                .foregroundStyle(.secondary)
            Spacer()
            Button("Finder…") {
                close()
                store.chooseFolderInFinder()
            }
            .help("Choose with the macOS folder dialog (to create a new folder, for example)")
        }
        .padding(12)
    }

    private func sectionTitle(_ text: String) -> some View {
        Text(text.uppercased())
            .font(.caption2.weight(.semibold))
            .foregroundStyle(.secondary)
            .padding(.horizontal, 12)
            .padding(.top, 8)
            .padding(.bottom, 4)
    }

    private func start(_ folder: URL) {
        if forMap {   // a map needs more: the related services and an optional flow
            mapFolder = folder
            selection = folder
            related = []
            mapName = ""
            return
        }
        store.start(in: folder)
        closeAfterward()
    }

    /// Service map mode, once the service is chosen: an optional flow or topic, and Create
    /// Map. Orwick finds the services that take part itself, among the folders beside it
    /// (read, never changed); a folder elsewhere can be added.
    private func mapForm(_ service: URL) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Image(systemName: "point.3.connected.trianglepath.dotted").foregroundStyle(.orange)
                Text(service.lastPathComponent).font(.callout.weight(.semibold))
                Spacer()
                Button("Change") { mapFolder = nil }
                    .buttonStyle(.borderless).font(.caption)
            }
            Text("Orwick finds the services it talks to by itself, in \(FolderTree.short(service.deletingLastPathComponent())) (read only).")
                .font(.caption).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            TextField("Flow or topic (empty: the whole service)", text: $focus)
                .textFieldStyle(.roundedBorder)
            TextField("Map name (empty: automatic, <session>_map1, 2, …)", text: $mapName)
                .textFieldStyle(.roundedBorder)
                .help("The name in Service maps. Empty: chosen for you; click the map's title later to rename it. A map with this name is updated.")
            if !mapName.isEmpty, ServiceMap.all().contains(where: { $0.name == ServiceMap.slug(mapName) }) {
                Text("A map with this name exists; it will be updated.")
                    .font(.caption).foregroundStyle(.orange)
            }
            ForEach(related, id: \.path) { folder in
                HStack {
                    Text("+ " + FolderTree.short(folder)).font(.caption).lineLimit(1).truncationMode(.middle)
                    Spacer()
                    Button { related.removeAll { $0.path == folder.path } } label: { Image(systemName: "xmark.circle.fill") }
                        .buttonStyle(.borderless).foregroundStyle(.secondary)
                }
            }
            HStack {
                Button("Also read a folder elsewhere…") {
                    let panel = NSOpenPanel()
                    panel.canChooseDirectories = true
                    panel.canChooseFiles = false
                    panel.allowsMultipleSelection = true
                    panel.prompt = "Add"
                    if panel.runModal() == .OK {
                        related += panel.urls.filter { url in !related.contains { $0.path == url.path } }
                    }
                }
                .buttonStyle(.borderless).font(.caption)
                .help("Only for services that are not in the same folder as this one")
                Spacer()
                Button("Create Map") {
                    store.startMap(in: service, related: related, focus: focus,
                                   name: mapName)
                    closeAfterward()
                }
                .buttonStyle(.borderedProminent)
                .keyboardShortcut(.defaultAction)
            }
        }
        .padding(12)
    }

    /// After a session was added: the sidebar takes the new row first, then the panel slides
    /// away (a row inserted during the slide can crash SwiftUI's sidebar list).
    private func closeAfterward() {
        DispatchQueue.main.async { close() }
    }

    private func close() {
        withAnimation(.easeOut(duration: 0.18)) { store.showingFolderPanel = false }
    }
}

/// One folder in the panel; an expanded one lists its subfolders below it (read when opened).
struct FolderRow: View {
    let url: URL
    let title: String
    let depth: Int
    let expandable: Bool
    @Binding var expanded: Set<String>
    @Binding var selection: URL?
    let start: (URL) -> Void
    var actionLabel = "Start"

    private var isOpen: Bool { expandable && expanded.contains(url.path) }
    private var isSelected: Bool { selection?.path == url.path }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 4) {
                Image(systemName: "chevron.right")
                    .font(.caption2.weight(.semibold))
                    .rotationEffect(.degrees(isOpen ? 90 : 0))
                    .frame(width: 14)
                    .opacity(expandable ? 0.7 : 0)
                    .contentShape(Rectangle())
                    .onTapGesture { toggle() }
                Image(systemName: "folder.fill")
                    .foregroundStyle(isSelected ? Color.white : Color.accentColor)
                Text(title)
                    .lineLimit(1)
                    .truncationMode(.middle)
                Spacer(minLength: 4)
                if FolderTree.hasSessions(url) {
                    Text("sessions")
                        .font(.caption2)
                        .foregroundStyle(isSelected ? Color.white : .secondary)
                        .help("Orwick has worked here before")
                }
                if isSelected && expandable {
                    Button { start(url) } label: {
                        Label(actionLabel, systemImage: actionLabel == "Start" ? "play.fill" : "checkmark")
                            .font(.caption.weight(.semibold))
                            .labelStyle(.titleAndIcon)
                            .padding(.horizontal, 7)
                            .padding(.vertical, 2)
                            .foregroundStyle(Color.accentColor)
                            .background(Color.white, in: Capsule())
                    }
                    .buttonStyle(.plain)
                    .help("\(actionLabel) \(FolderTree.short(url))")
                }
            }
            .font(.callout)
            .foregroundStyle(isSelected ? Color.white : .primary)
            .padding(.vertical, 3)
            .padding(.leading, 8 + CGFloat(depth) * 14)
            .padding(.trailing, 10)
            .background(isSelected ? Color.accentColor : .clear, in: RoundedRectangle(cornerRadius: 5))
            .padding(.horizontal, 6)
            .contentShape(Rectangle())
            // In the tree a click selects the folder and opens or closes it; a double-click (or
            // Start) starts the session there. A recent folder can't open, so a click starts it.
            .onTapGesture(count: 2) { start(url) }
            .onTapGesture {
                if expandable {
                    selection = url
                    toggle()
                } else {
                    start(url)
                }
            }
            .id((expandable ? "tree:" : "recent:") + url.path)
            .help(FolderTree.short(url))

            if isOpen {
                ForEach(FolderTree.subfolders(of: url), id: \.path) { child in
                    FolderRow(url: child, title: child.lastPathComponent, depth: depth + 1, expandable: true,
                              expanded: $expanded, selection: $selection, start: start,
                              actionLabel: actionLabel)
                }
            }
        }
    }

    private func toggle() {
        if expanded.contains(url.path) { expanded.remove(url.path) } else { expanded.insert(url.path) }
    }
}
