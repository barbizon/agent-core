import SwiftUI

struct OrwickApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @StateObject private var store = SessionStore.shared

    var body: some Scene {
        Window("Orwick", id: "main") {
            ContentView()
                .environmentObject(store)
                .frame(minWidth: 820, minHeight: 520)
        }
        .commands {
            CommandGroup(replacing: .appInfo) { AboutMenuItem() }
            CommandGroup(replacing: .appSettings) {
                Button("Settings…") { store.showSettings() }
                    .keyboardShortcut(",")
            }
            CommandGroup(replacing: .newItem) {
                Button("New Session…") { store.chooseFolderAndStart() }
                    .keyboardShortcut("n")
            }
            CommandMenu("Session") {
                Button("Home") { store.goHome() }
                    .keyboardShortcut("h", modifiers: [.command, .shift])
                    .disabled(store.isHome)
                Divider()
                Button(store.showingInfo ? "Hide Info" : "Show Info") { store.showingInfo.toggle() }
                    .keyboardShortcut("i")
                    .disabled(store.selected == nil)
                Divider()
                Button("Restart Orwick") { store.selected.map(store.restart) }
                    .keyboardShortcut("r")
                    .disabled(store.selected == nil)
                Button("Close Session") { store.selected.map(store.close) }
                    .keyboardShortcut("w", modifiers: [.command, .shift])
                    .disabled(store.selected == nil)
            }
        }

        Window("About Orwick", id: "about") {
            AboutView()
        }
        .windowResizability(.contentSize)
        .windowStyle(.hiddenTitleBar)
    }
}

private struct AboutMenuItem: View {
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        Button("About Orwick") { openWindow(id: "about") }
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        let report = AppLog.startRun()
        MainActor.assumeIsolated {
            SessionStore.shared.startAppearance()
            SessionStore.shared.crashReport = report
        }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    /// Quitting stops every open Orwick session, so ask first. Each session saves its
    /// conversation on the way out (like /exit) and can be resumed from Recent sessions. The app
    /// comes to the front before asking: after the red X the window is gone, and an alert of an
    /// app in the background only bounces in the Dock.
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        let (running, answering) = MainActor.assumeIsolated { () -> (Int, Int) in
            let open = SessionStore.shared.sessions.filter(\.running)
            return (open.count, open.filter { $0.status?.isWaiting == false }.count)
        }
        guard running > 0 else { return .terminateNow }
        NSApp.activate(ignoringOtherApps: true)
        let alert = NSAlert()
        alert.messageText = running == 1 ? "An Orwick session is open" : "\(running) Orwick sessions are open"
        alert.informativeText = (answering > 0 ? "Orwick is still answering; quitting stops the answer. " : "")
            + "Quitting closes the sessions. Their conversations are saved, and you can resume them from Recent sessions."
        alert.addButton(withTitle: "Quit")
        alert.addButton(withTitle: "Cancel")
        return alert.runModal() == .alertFirstButtonReturn ? .terminateNow : .terminateCancel
    }

    func applicationWillTerminate(_ notification: Notification) {
        MainActor.assumeIsolated { SessionStore.shared.closeAll() }
        AppLog.endRun()
    }
}
