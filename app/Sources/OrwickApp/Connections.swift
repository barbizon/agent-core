import SwiftUI

/// Connecting Jira from Settings, without editing files by hand.
///
/// Orwick reads tickets through an MCP server (see src/agent_core/mcp.py). This writes what
/// that needs: the login into `~/.orwick/.env` (only you can read it), the `mcp-atlassian`
/// server into `mcpServers` with `${NAME}` placeholders, so the settings file holds no
/// token, and the read-only ticket and page tools switched on.
enum JiraConnection {
    static let server = "mcp-atlassian"
    static let envFile = OrwickConfig.mainFile.deletingLastPathComponent().appendingPathComponent(".env")
    static let tokenPage = URL(string: "https://id.atlassian.com/manage-profile/security/api-tokens")!
    /// Read-only tools: the ticket, a search (a parent's other children), a linked page.
    static let tools = ["jira_get_issue", "jira_search", "confluence_get_page", "confluence_search"]

    static func isConnected(_ config: OrwickConfig) -> Bool {
        servers(config)[server] != nil
    }

    /// "yourteam.atlassian.net/" -> "https://yourteam.atlassian.net"
    static func site(_ typed: String) -> String {
        var site = typed.trimmingCharacters(in: .whitespacesAndNewlines)
        while site.hasSuffix("/") { site.removeLast() }
        return site.contains("://") ? site : "https://" + site
    }

    /// The settings with the server defined and its read tools on; other servers are kept.
    static func connected(_ config: OrwickConfig) -> OrwickConfig {
        var config = config
        var all = servers(config)
        all[server] = [
            "command": "uvx", "args": [server],
            "env": ["JIRA_URL": "${JIRA_URL}", "JIRA_USERNAME": "${JIRA_EMAIL}", "JIRA_API_TOKEN": "${JIRA_TOKEN}",
                    "CONFLUENCE_URL": "${JIRA_URL}/wiki", "CONFLUENCE_USERNAME": "${JIRA_EMAIL}",
                    "CONFLUENCE_API_TOKEN": "${JIRA_TOKEN}"],
        ]
        let data = try! JSONSerialization.data(withJSONObject: all,
                                               options: [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes])
        config.mcpServersJSON = String(decoding: data, as: UTF8.self)
            .split(separator: "\n").joined(separator: "\n  ")   // indented like the rest of the file
        for tool in tools {
            config.tools["mcp__\(server)__\(tool)"] = true
        }
        return config
    }

    /// The `.env` text with these values set: a line that already sets a name is replaced,
    /// the rest of the file is kept.
    static func env(_ text: String, setting values: [(String, String)]) -> String {
        var lines = text.isEmpty ? [] : text.trimmingCharacters(in: .newlines).components(separatedBy: "\n")
        for (name, value) in values {
            let line = "\(name)=\(value)"
            if let index = lines.firstIndex(where: { $0.hasPrefix("\(name)=") }) {
                lines[index] = line
            } else {
                lines.append(line)
            }
        }
        return lines.joined(separator: "\n") + "\n"
    }

    static func saveLogin(site: String, email: String, token: String, to url: URL = envFile) throws {
        let old = (try? String(contentsOf: url, encoding: .utf8)) ?? ""
        let text = env(old, setting: [("JIRA_URL", Self.site(site)),
                                      ("JIRA_EMAIL", email.trimmingCharacters(in: .whitespaces)),
                                      ("JIRA_TOKEN", token.trimmingCharacters(in: .whitespacesAndNewlines))])
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try Data(text.utf8).write(to: url, options: .atomic)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
    }

    private static func servers(_ config: OrwickConfig) -> [String: Any] {
        guard let json = config.mcpServersJSON,
              let all = (try? JSONSerialization.jsonObject(with: Data(json.utf8))) as? [String: Any] else { return [:] }
        return all
    }
}

/// Asks for the Jira login. `connect` saves it and returns an error to show, or nil when done.
struct ConnectJiraSheet: View {
    let connect: (_ site: String, _ email: String, _ token: String) -> String?
    @Environment(\.dismiss) private var dismiss
    @State private var site = ""
    @State private var email = ""
    @State private var token = ""
    @State private var error: String?

    private var ready: Bool {
        ![site, email, token].contains { $0.trimmingCharacters(in: .whitespaces).isEmpty }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Connect Jira").font(.title3.weight(.semibold))
            Text("Orwick will read tickets, and the Confluence pages they link to. It only reads; it can't change anything there.")
                .font(.callout)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            Form {
                TextField("Site", text: $site, prompt: Text("yourteam.atlassian.net"))
                TextField("Email", text: $email, prompt: Text("The email you sign in to Jira with"))
                SecureField("API token", text: $token, prompt: Text("Paste the token"))
            }
            Link("Create an API token on Atlassian's site", destination: JiraConnection.tokenPage)
                .font(.callout)
            Text("The login is saved on this Mac in ~/.orwick/.env, which only you can read. It goes to the Jira connection only, never to the AI.")
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            if let error {
                Text(error).font(.caption).foregroundStyle(.red)
            }
            HStack {
                Spacer()
                Button("Cancel") { dismiss() }
                    .keyboardShortcut(.cancelAction)
                Button("Connect") {
                    error = connect(site, email, token)
                    if error == nil { dismiss() }
                }
                .keyboardShortcut(.defaultAction)
                .disabled(!ready)
            }
        }
        .padding(20)
        .frame(width: 460)
    }
}
