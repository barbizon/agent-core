import AppKit
import SwiftTerm

/// The app's light/dark choice. It's an app preference (stored by the app),
/// not an Orwick setting: Orwick only prints basic colors, and the terminal theme
/// below decides how they look.
enum Appearance: String, CaseIterable, Identifiable {
    case system, light, dark

    var id: String { rawValue }
    var title: String { rawValue.capitalized }

    /// What AppKit should use; nil follows macOS.
    var nsAppearance: NSAppearance? {
        switch self {
        case .system: return nil
        case .light: return NSAppearance(named: .aqua)
        case .dark: return NSAppearance(named: .darkAqua)
        }
    }
}

/// Terminal colors for one appearance: background, text, cursor and the 16
/// basic colors Orwick uses (cyan, magenta, yellow, gray, ...). The light palette
/// uses darker shades so Orwick's yellow and gray stay readable on white.
struct TerminalTheme: Equatable {
    let isDark: Bool
    let background: NSColor
    let foreground: NSColor
    let caret: NSColor
    let ansi: [UInt32]  // 0xRRGGBB: black, red, green, yellow, blue, magenta, cyan, white, then the bright ones

    static let dark = TerminalTheme(
        isDark: true,
        background: rgb(0x17191F), foreground: rgb(0xE6E6E6), caret: rgb(0x56D4DD),
        ansi: [0x3B4048, 0xFF6B6B, 0x5FD68A, 0xF2C55C, 0x6CB6FF, 0xD59BF6, 0x56D4DD, 0xC9D1D9,
               0x7D8590, 0xFF8E8E, 0x7EE2A0, 0xF7D77E, 0x91CBFF, 0xE2B6FA, 0x7FE3EA, 0xF0F3F6])

    static let light = TerminalTheme(
        isDark: false,
        background: rgb(0xFBFBF8), foreground: rgb(0x1F2328), caret: rgb(0x0E7490),
        ansi: [0x24292F, 0xCF222E, 0x116329, 0x8A6100, 0x0550AE, 0x8250DF, 0x0E7490, 0x6E7781,
               0x57606A, 0xA40E26, 0x1A7F37, 0x7D4E00, 0x0969DA, 0x6639BA, 0x0B5C73, 0x8C959F])

    /// The theme matching what the app currently shows (its own choice, or macOS's).
    @MainActor
    static var current: TerminalTheme {
        let appearance = NSApp?.effectiveAppearance ?? NSAppearance.currentDrawing()
        return appearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua ? .dark : .light
    }

    func apply(to view: LocalProcessTerminalView) {
        view.nativeBackgroundColor = background
        view.nativeForegroundColor = foreground
        view.caretColor = caret
        view.installColors(ansi.map { value in
            SwiftTerm.Color(red: UInt16((value >> 16) & 0xFF) * 257,
                            green: UInt16((value >> 8) & 0xFF) * 257,
                            blue: UInt16(value & 0xFF) * 257)
        })
        view.needsDisplay = true
    }

    private static func rgb(_ value: UInt32) -> NSColor {
        NSColor(srgbRed: CGFloat((value >> 16) & 0xFF) / 255, green: CGFloat((value >> 8) & 0xFF) / 255,
                blue: CGFloat(value & 0xFF) / 255, alpha: 1)
    }
}
