import SwiftUI

struct AboutView: View {
    @State private var winking = false
    @State private var blinking = false
    @State private var quip = AboutView.quips[0]

    private let blinkTimer = Timer.publish(every: 3.4, on: .main, in: .common).autoconnect()

    static let quips = [
        "I ask before I write. Every. Single. Time.",
        "No network in my sandbox. I've made peace with it.",
        "Your .env? Never heard of it.",
        "I raid codebases. Politely. With approval.",
        "The horns are for show. The sandbox is real.",
        "rm -rf? Not on my watch.",
        "Born in a terminal. Still prefer it.",
    ]

    private var version: String {
        Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "dev"
    }

    var body: some View {
        VStack(spacing: 12) {
            ZStack {
                Circle()
                    .fill(LinearGradient(colors: [Color(red: 0.20, green: 0.78, blue: 0.74), Color(red: 0.25, green: 0.27, blue: 0.62)],
                                         startPoint: .topLeading, endPoint: .bottomTrailing))
                AvatarView(winking: winking, blinking: blinking)
                    .padding(12)
            }
            .frame(width: 150, height: 150)
            .shadow(color: .black.opacity(0.2), radius: 8, y: 4)
            .onTapGesture(perform: wink)
            .help("Poke Orwick")

            Text(quip)
                .font(.callout.italic())
                .multilineTextAlignment(.center)
                .padding(.horizontal, 12)
                .padding(.vertical, 7)
                .background(.quaternary, in: RoundedRectangle(cornerRadius: 10))
                .id(quip)
                .transition(.opacity)

            Text("Orwick")
                .font(.system(size: 28, weight: .bold, design: .rounded))
                .padding(.top, 4)
            Text("Version \(version)")
                .font(.caption)
                .foregroundStyle(.secondary)

            Text("A window for the orwick command-line agent.\nEverything happens in the CLI: sandboxed,\nand every change waits for your OK.")
                .font(.callout)
                .multilineTextAlignment(.center)
                .foregroundStyle(.secondary)

            Divider().padding(.vertical, 4)

            Text("Terminal by SwiftTerm")
                .font(.caption2)
                .foregroundStyle(.tertiary)
        }
        .padding(.horizontal, 28)
        .padding(.top, 34)
        .padding(.bottom, 22)
        .frame(width: 340)
        .onReceive(blinkTimer) { _ in blink() }
    }

    private func wink() {
        withAnimation(.easeInOut(duration: 0.2)) {
            quip = (Self.quips.filter { $0 != quip }).randomElement() ?? quip
        }
        winking = true
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.6) { winking = false }
    }

    private func blink() {
        guard !winking else { return }
        blinking = true
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.14) { blinking = false }
    }
}
