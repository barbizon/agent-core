import SwiftUI

/// Orwick the robo-viking: a horned helmet with a shield badge (the protection),
/// a terminal prompt for a mouth, and a braided beard. Drawn in code on a
/// 100×100 grid, so it is sharp at any size and also becomes the app icon.
struct AvatarView: View {
    var winking = false
    var blinking = false

    private let navy = Color(red: 0.12, green: 0.14, blue: 0.25)

    var body: some View {
        Canvas { context, size in
            let scale = min(size.width, size.height) / 100
            context.translateBy(x: (size.width - 100 * scale) / 2, y: (size.height - 100 * scale) / 2)
            context.scaleBy(x: scale, y: scale)
            draw(in: &context)
        }
        .aspectRatio(1, contentMode: .fit)
        .accessibilityLabel("Orwick, a small robot wearing a viking helmet")
    }

    private func draw(in context: inout GraphicsContext) {
        let line = StrokeStyle(lineWidth: 2.2, lineCap: .round, lineJoin: .round)

        // Horns
        for side in [false, true] {
            var horn = Path()
            horn.move(to: p(24, 46, side))
            horn.addQuadCurve(to: p(9, 13, side), control: p(5, 36, side))
            horn.addQuadCurve(to: p(34, 38, side), control: p(19, 31, side))
            horn.closeSubpath()
            context.fill(horn, with: .linearGradient(
                Gradient(colors: [Color(red: 1, green: 0.97, blue: 0.88), Color(red: 0.93, green: 0.84, blue: 0.64)]),
                startPoint: p(9, 13, side), endPoint: p(30, 44, side)))
            context.stroke(horn, with: .color(navy), style: line)
            var ring = Path()
            ring.move(to: p(10.8, 32.8, side))
            ring.addLine(to: p(20.2, 28.3, side))
            ring.move(to: p(14.5, 39.5, side))
            ring.addLine(to: p(26.5, 33.2, side))
            context.stroke(ring, with: .color(navy.opacity(0.55)), style: StrokeStyle(lineWidth: 1.4, lineCap: .round))
        }

        // Side bolts (ears)
        for x in [22.5, 77.5] {
            let bolt = circle(x, 66, 3.6)
            context.fill(bolt, with: .color(Color(red: 0.62, green: 0.67, blue: 0.76)))
            context.stroke(bolt, with: .color(navy), style: line)
        }

        // Head
        let head = Path(roundedRect: CGRect(x: 25, y: 40, width: 50, height: 46), cornerRadius: 15)
        context.fill(head, with: .linearGradient(
            Gradient(colors: [Color(red: 0.96, green: 0.97, blue: 1.0), Color(red: 0.82, green: 0.86, blue: 0.93)]),
            startPoint: CGPoint(x: 50, y: 40), endPoint: CGPoint(x: 50, y: 86)))
        context.stroke(head, with: .color(navy), style: StrokeStyle(lineWidth: 2.5, lineJoin: .round))

        // Helmet dome
        var dome = Path()
        dome.move(to: CGPoint(x: 21, y: 50))
        dome.addCurve(to: CGPoint(x: 50, y: 19), control1: CGPoint(x: 21, y: 32), control2: CGPoint(x: 34, y: 19))
        dome.addCurve(to: CGPoint(x: 79, y: 50), control1: CGPoint(x: 66, y: 19), control2: CGPoint(x: 79, y: 32))
        dome.closeSubpath()
        context.fill(dome, with: .linearGradient(
            Gradient(colors: [Color(red: 0.70, green: 0.76, blue: 0.85), Color(red: 0.42, green: 0.48, blue: 0.60)]),
            startPoint: CGPoint(x: 35, y: 20), endPoint: CGPoint(x: 65, y: 50)))
        context.stroke(dome, with: .color(navy), style: line)
        var shine = Path()
        shine.move(to: CGPoint(x: 30, y: 38))
        shine.addQuadCurve(to: CGPoint(x: 42, y: 25), control: CGPoint(x: 32, y: 28))
        context.stroke(shine, with: .color(.white.opacity(0.75)), style: StrokeStyle(lineWidth: 2.4, lineCap: .round))

        // Gold band with rivets
        let band = Path(roundedRect: CGRect(x: 18.5, y: 45, width: 63, height: 8), cornerRadius: 3)
        context.fill(band, with: .color(Color(red: 0.97, green: 0.72, blue: 0.25)))
        context.stroke(band, with: .color(navy), style: line)
        for x in [25.0, 34.0, 66.0, 75.0] {
            context.fill(circle(x, 49, 1.3), with: .color(Color(red: 0.66, green: 0.43, blue: 0.08)))
        }

        // Shield badge with a check mark
        var shield = Path()
        shield.move(to: CGPoint(x: 43, y: 41))
        shield.addLine(to: CGPoint(x: 57, y: 41))
        shield.addQuadCurve(to: CGPoint(x: 50, y: 58), control: CGPoint(x: 58, y: 53))
        shield.addQuadCurve(to: CGPoint(x: 43, y: 41), control: CGPoint(x: 42, y: 53))
        shield.closeSubpath()
        context.fill(shield, with: .color(Color(red: 0.17, green: 0.70, blue: 0.64)))
        context.stroke(shield, with: .color(navy), style: line)
        var check = Path()
        check.move(to: CGPoint(x: 46.5, y: 48))
        check.addLine(to: CGPoint(x: 49.3, y: 51))
        check.addLine(to: CGPoint(x: 54, y: 45.5))
        context.stroke(check, with: .color(.white), style: StrokeStyle(lineWidth: 1.8, lineCap: .round, lineJoin: .round))

        // Eyes
        drawEye(in: &context, x: 39, closed: blinking, line: line)
        drawEye(in: &context, x: 61, closed: blinking || winking, line: line)

        // Cheeks
        for x in [31.5, 68.5] {
            context.fill(Path(ellipseIn: CGRect(x: x - 4, y: 70.5, width: 8, height: 4.5)),
                         with: .color(Color(red: 1, green: 0.55, blue: 0.64).opacity(0.65)))
        }

        // Beard with a wavy edge and two braids
        let ginger = Color(red: 0.91, green: 0.50, blue: 0.20)
        var beard = Path()
        beard.move(to: CGPoint(x: 27, y: 75))
        beard.addQuadCurve(to: CGPoint(x: 36, y: 90), control: CGPoint(x: 26, y: 87))
        beard.addQuadCurve(to: CGPoint(x: 50, y: 94), control: CGPoint(x: 42, y: 96))
        beard.addQuadCurve(to: CGPoint(x: 64, y: 90), control: CGPoint(x: 58, y: 96))
        beard.addQuadCurve(to: CGPoint(x: 73, y: 75), control: CGPoint(x: 74, y: 87))
        beard.addQuadCurve(to: CGPoint(x: 27, y: 75), control: CGPoint(x: 50, y: 88))
        beard.closeSubpath()
        context.fill(beard, with: .linearGradient(
            Gradient(colors: [Color(red: 0.97, green: 0.62, blue: 0.29), ginger]),
            startPoint: CGPoint(x: 50, y: 78), endPoint: CGPoint(x: 50, y: 95)))
        context.stroke(beard, with: .color(navy), style: line)
        for x in [42.0, 58.0] {
            let braid = Path(roundedRect: CGRect(x: x - 2, y: 91, width: 4, height: 7.5), cornerRadius: 2)
            context.fill(braid, with: .color(ginger))
            context.stroke(braid, with: .color(navy), style: StrokeStyle(lineWidth: 1.5))
            context.fill(Path(roundedRect: CGRect(x: x - 2.6, y: 93.5, width: 5.2, height: 2), cornerRadius: 1),
                         with: .color(Color(red: 0.97, green: 0.72, blue: 0.25)))
        }

        // Mouth: a tiny terminal with a prompt
        let screen = Path(roundedRect: CGRect(x: 41.5, y: 74, width: 17, height: 10), cornerRadius: 3)
        context.fill(screen, with: .color(Color(red: 0.07, green: 0.09, blue: 0.12)))
        context.stroke(screen, with: .color(navy), style: StrokeStyle(lineWidth: 1.6))
        var prompt = Path()
        prompt.move(to: CGPoint(x: 44.5, y: 76.8))
        prompt.addLine(to: CGPoint(x: 47.5, y: 79))
        prompt.addLine(to: CGPoint(x: 44.5, y: 81.2))
        prompt.move(to: CGPoint(x: 49.5, y: 81.2))
        prompt.addLine(to: CGPoint(x: 54.5, y: 81.2))
        context.stroke(prompt, with: .color(Color(red: 0.30, green: 0.90, blue: 0.54)),
                       style: StrokeStyle(lineWidth: 1.5, lineCap: .round, lineJoin: .round))
        // Mustache curls, on top of the mouth
        for side in [false, true] {
            var curl = Path()
            curl.move(to: p(50, 71.5, side))
            curl.addQuadCurve(to: p(32.5, 72, side), control: p(41, 65.5, side))
            curl.addQuadCurve(to: p(36, 75.5, side), control: p(30.5, 76, side))
            curl.addQuadCurve(to: p(50, 74, side), control: p(43, 77, side))
            curl.closeSubpath()
            context.fill(curl, with: .color(ginger))
            context.stroke(curl, with: .color(navy), style: StrokeStyle(lineWidth: 1.6, lineJoin: .round))
        }
    }

    private func drawEye(in context: inout GraphicsContext, x: Double, closed: Bool, line: StrokeStyle) {
        if closed {
            var lid = Path()
            lid.move(to: CGPoint(x: x - 6, y: 65))
            lid.addQuadCurve(to: CGPoint(x: x + 6, y: 65), control: CGPoint(x: x, y: 58.5))
            context.stroke(lid, with: .color(navy), style: StrokeStyle(lineWidth: 2.6, lineCap: .round))
            return
        }
        let eye = circle(x, 64, 7.5)
        context.fill(eye, with: .color(.white))
        context.stroke(eye, with: .color(navy), style: line)
        context.fill(circle(x + 1, 65, 3.9), with: .color(navy))
        context.fill(circle(x + 2.4, 63.3, 1.4), with: .color(.white))
    }

    /// A point on the left half, or mirrored to the right half.
    private func p(_ x: Double, _ y: Double, _ mirrored: Bool) -> CGPoint {
        CGPoint(x: mirrored ? 100 - x : x, y: y)
    }

    private func circle(_ x: Double, _ y: Double, _ r: Double) -> Path {
        Path(ellipseIn: CGRect(x: x - r, y: y - r, width: 2 * r, height: 2 * r))
    }
}

/// The app icon: the avatar on a rounded square, following the macOS icon grid.
struct AppIconView: View {
    var body: some View {
        ZStack {
            RoundedRectangle(cornerRadius: 185, style: .continuous)
                .fill(LinearGradient(colors: [Color(red: 0.20, green: 0.78, blue: 0.74), Color(red: 0.25, green: 0.27, blue: 0.62)],
                                     startPoint: .topLeading, endPoint: .bottomTrailing))
                .frame(width: 824, height: 824)
                .shadow(color: .black.opacity(0.3), radius: 18, y: 10)
            AvatarView()
                .frame(width: 700, height: 700)
                .offset(y: 8)
        }
        .frame(width: 1024, height: 1024)
    }
}

enum IconRenderer {
    @MainActor
    static func write(to url: URL) -> Bool {
        let renderer = ImageRenderer(content: AppIconView())
        renderer.scale = 1
        guard let image = renderer.cgImage,
              let png = NSBitmapImageRep(cgImage: image).representation(using: .png, properties: [:]) else {
            return false
        }
        return (try? png.write(to: url)) != nil
    }
}
