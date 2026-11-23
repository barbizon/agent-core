import SwiftUI
import WebKit

/// Mermaid diagrams drawn as pictures (boxes and arrows) in docs and service maps.
/// Mermaid itself (mermaid.min.js, pinned) ships inside the app, so drawing works offline;
/// the page loads nothing from the network.
enum Mermaid {
    /// The bundled script: in Orwick.app's Resources, or next to the sources (swift run, tests).
    static let script: String? = {
        let candidates = [
            Bundle.main.url(forResource: "mermaid.min", withExtension: "js"),
            URL(fileURLWithPath: #filePath).deletingLastPathComponent()
                .appendingPathComponent("../../Resources/mermaid.min.js").standardizedFileURL,
        ]
        for case let url? in candidates {
            if let text = try? String(contentsOf: url, encoding: .utf8) { return text }
        }
        return nil
    }()

    /// The page that draws one diagram and reports its height (or the error) back.
    static func page(_ source: String, dark: Bool) -> String {
        let escaped = source
            .replacingOccurrences(of: "&", with: "&amp;")
            .replacingOccurrences(of: "<", with: "&lt;")
            .replacingOccurrences(of: ">", with: "&gt;")
        return """
        <!doctype html><html><head><meta charset="utf-8">
        <meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; font-src data:">
        <style>
          html, body { margin: 0; padding: 0; background: transparent; }
          body { font: 13px -apple-system, sans-serif; color: \(dark ? "#ddd" : "#222"); }
          #d { padding: 8px 4px; }
          #d svg { max-width: 100%; height: auto; display: block; margin: 0 auto; }
          pre { white-space: pre-wrap; color: #c33; }
        </style></head><body>
        <div id="d" class="mermaid">\(escaped)</div>
        <script>\(script ?? "")</script>
        <script>
          function report() {
            const h = Math.ceil(document.getElementById('d').getBoundingClientRect().height);
            window.webkit.messageHandlers.size.postMessage(h);
          }
          if (!window.mermaid) {
            document.getElementById('d').outerHTML = '<pre>The diagram drawer is missing from this build.</pre>';
            report();
          } else {
            mermaid.initialize({ startOnLoad: false, securityLevel: 'strict',
                                 theme: '\(dark ? "dark" : "default")', flowchart: { useMaxWidth: true },
                                 sequence: { useMaxWidth: true } });
            mermaid.run({ querySelector: '#d' })
              .catch(e => { document.getElementById('d').outerHTML =
                             '<pre>Diagram error: ' + String(e.message || e).replace(/</g, '&lt;') + '</pre>'; })
              .finally(() => { report(); new ResizeObserver(report).observe(document.body); });
          }
        </script></body></html>
        """
    }
}

/// One drawn diagram; its height follows the picture. "Source" shows the Mermaid text.
struct MermaidView: View {
    let source: String
    let tint: Color
    @State private var height: CGFloat = 80
    @State private var showSource = false
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Label("Diagram", systemImage: "point.3.connected.trianglepath.dotted")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(tint)
                Spacer()
                Button(showSource ? "Picture" : "Source") { showSource.toggle() }
                    .buttonStyle(.borderless)
                    .font(.caption)
            }
            if showSource {
                Text(source)
                    .font(.callout.monospaced())
                    .textSelection(.enabled)
                    .padding(10)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(.quaternary, in: RoundedRectangle(cornerRadius: 8))
            } else {
                DiagramWebView(html: Mermaid.page(source, dark: colorScheme == .dark), height: $height)
                    .frame(height: height)
                    .frame(maxWidth: .infinity)
                    .background(.quaternary.opacity(0.4), in: RoundedRectangle(cornerRadius: 8))
            }
        }
    }
}

private struct DiagramWebView: NSViewRepresentable {
    let html: String
    @Binding var height: CGFloat

    func makeCoordinator() -> Coordinator { Coordinator(height: $height) }

    func makeNSView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        config.userContentController.add(context.coordinator, name: "size")
        config.websiteDataStore = .nonPersistent()
        let view = WKWebView(frame: .zero, configuration: config)
        view.setValue(false, forKey: "drawsBackground")   // the doc's background shows through
        view.navigationDelegate = context.coordinator
        return view
    }

    func updateNSView(_ view: WKWebView, context: Context) {
        guard context.coordinator.loaded != html else { return }
        context.coordinator.loaded = html
        view.loadHTMLString(html, baseURL: nil)
    }

    static func dismantleNSView(_ view: WKWebView, coordinator: Coordinator) {
        view.configuration.userContentController.removeScriptMessageHandler(forName: "size")
    }

    final class Coordinator: NSObject, WKScriptMessageHandler, WKNavigationDelegate {
        var height: Binding<CGFloat>
        var loaded: String?

        init(height: Binding<CGFloat>) { self.height = height }

        func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
            guard let value = message.body as? Double ?? (message.body as? Int).map(Double.init), value > 0 else { return }
            let next = CGFloat(min(value + 4, 4000))
            if abs(next - height.wrappedValue) > 1 { height.wrappedValue = next }
        }

        /// Links in a diagram never open inside the app.
        func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                     decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
            decisionHandler(action.navigationType == .other ? .allow : .cancel)
        }
    }
}
