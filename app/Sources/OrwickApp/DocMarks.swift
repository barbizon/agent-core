import SwiftUI

/// The marks the feature docs put on components, events, APIs, tables, config keys and
/// permissions: ✅ exists (used as is) · ✏️ exists, must change · 🆕 new / missing.
/// The app shows them as colored badges, tints the rows that need work, and colors the
/// marked labels in diagrams, so new and changed parts stand out from what already exists.
enum DocMark: CaseIterable {
    case exists, change, new

    var symbol: String {
        switch self {
        case .exists: return "✅"
        case .change: return "✏️"
        case .new: return "🆕"
        }
    }

    var label: String {
        switch self {
        case .exists: return "EXISTS"
        case .change: return "CHANGE"
        case .new: return "NEW"
        }
    }

    var color: Color {
        switch self {
        case .exists: return .green
        case .change: return .orange
        case .new: return .blue
        }
    }

    /// The mark a table cell starts with, and the text after it ("✏️ add level_id" →
    /// (.change, "add level_id")). The pencil may come without its emoji selector.
    static func leading(_ cell: String) -> (mark: DocMark, rest: String)? {
        let trimmed = cell.trimmingCharacters(in: .whitespaces)
        for mark in allCases {
            for symbol in mark.symbols where trimmed.hasPrefix(symbol) {
                return (mark, String(trimmed.dropFirst(symbol.count)).trimmingCharacters(in: .whitespaces))
            }
        }
        return nil
    }

    /// The row's state: the first cell that starts with a mark.
    static func ofRow(_ cells: [String]) -> DocMark? {
        cells.lazy.compactMap { leading($0)?.mark }.first
    }

    var symbols: [String] { self == .change ? ["✏️", "✏"] : [symbol] }

    /// The rows that need work get a light tint; existing ones stay plain.
    var rowTint: Color? {
        switch self {
        case .exists: return nil
        case .change, .new: return color.opacity(0.09)
        }
    }

    /// Where a marked label ends in a line: at a quote, bracket, pipe, `·` or the line's end
    /// (`JOB["✏️ orders RefundJob"]` → `✏️ orders RefundJob`).
    static let labelEnd: Set<Character> = ["\"", "]", ")", "|", "·", "\n"]

    /// Colors every marked label in `attributed`: the mark and the words after it.
    static func colorLabels(in attributed: inout AttributedString) {
        let text = String(attributed.characters)
        for mark in allCases {
            for symbol in mark.symbols {
                var from = text.startIndex
                while let found = text.range(of: symbol, range: from..<text.endIndex) {
                    let end = text[found.upperBound...].firstIndex { labelEnd.contains($0) } ?? text.endIndex
                    if let lower = AttributedString.Index(found.lowerBound, within: attributed),
                       let upper = AttributedString.Index(end, within: attributed), lower < upper {
                        attributed[lower..<upper].foregroundColor = mark.color
                        attributed[lower..<upper].inlinePresentationIntent = .stronglyEmphasized
                    }
                    from = found.upperBound
                }
            }
        }
    }
}

/// A small colored capsule: EXISTS / CHANGE / NEW.
struct DocMarkBadge: View {
    let mark: DocMark

    var body: some View {
        Text(mark.label)
            .font(.caption2.weight(.bold))
            .foregroundStyle(mark.color)
            .padding(.horizontal, 6)
            .padding(.vertical, 2)
            .background(mark.color.opacity(0.15), in: Capsule())
            .overlay(Capsule().stroke(mark.color.opacity(0.4)))
            .fixedSize()
    }
}
