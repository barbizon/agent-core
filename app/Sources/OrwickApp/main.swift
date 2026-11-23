import AppKit

// `OrwickApp --render-icon <file.png>` draws the app icon and exits (used by build-app.sh).
let arguments = CommandLine.arguments
if let index = arguments.firstIndex(of: "--render-icon"), index + 1 < arguments.count {
    MainActor.assumeIsolated {
        exit(IconRenderer.write(to: URL(fileURLWithPath: arguments[index + 1])) ? 0 : 1)
    }
}

OrwickApp.main()
