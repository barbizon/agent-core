import XCTest
@testable import OrwickApp

final class ThemeTests: XCTestCase {
    /// WCAG contrast ratio between two 0xRRGGBB colors (4.5 is the bar for normal text).
    private func contrast(_ a: UInt32, _ b: UInt32) -> Double {
        func luminance(_ c: UInt32) -> Double {
            func channel(_ v: UInt32) -> Double {
                let x = Double(v & 0xFF) / 255
                return x <= 0.03928 ? x / 12.92 : pow((x + 0.055) / 1.055, 2.4)
            }
            return 0.2126 * channel(c >> 16) + 0.7152 * channel(c >> 8) + 0.0722 * channel(c)
        }
        let (l1, l2) = (luminance(a), luminance(b))
        return (max(l1, l2) + 0.05) / (min(l1, l2) + 0.05)
    }

    private func hex(_ color: NSColor) -> UInt32 {
        let c = color.usingColorSpace(.sRGB)!
        return UInt32(round(c.redComponent * 255)) << 16 | UInt32(round(c.greenComponent * 255)) << 8
            | UInt32(round(c.blueComponent * 255))
    }

    /// Orwick prints red, green, yellow, blue, magenta, cyan and gray (bright black).
    private let usedByOrwick = [1, 2, 3, 4, 5, 6, 8]

    func testEveryColorOrwickUsesIsReadableInBothThemes() {
        for theme in [TerminalTheme.light, TerminalTheme.dark] {
            XCTAssertEqual(theme.ansi.count, 16)
            let background = hex(theme.background)
            XCTAssertGreaterThan(contrast(hex(theme.foreground), background), 7)
            for index in usedByOrwick {
                let ratio = contrast(theme.ansi[index], background)
                XCTAssertGreaterThanOrEqual(ratio, 4.5, "color \(index) in \(theme.isDark ? "dark" : "light"): \(ratio)")
            }
        }
    }

    func testAppearanceChoices() {
        XCTAssertNil(Appearance.system.nsAppearance)
        XCTAssertEqual(Appearance.light.nsAppearance?.name, .aqua)
        XCTAssertEqual(Appearance.dark.nsAppearance?.name, .darkAqua)
    }
}
