import SwiftUI

@main
struct AICompanionApp: App {
    var body: some Scene { WindowGroup { ContentView() } }
}

struct ChatMessage: Codable {
    let role: String
    let content: String
}

struct AgentRequest: Codable {
    let model: String
    let messages: [ChatMessage]
    let assistant_profile: String
    let conversation_id: Int?
}

struct AgentResponse: Codable {
    struct Message: Codable { let role: String; let content: String }
    let message: Message?
    let conversation_id: Int?
}

struct ConversationResponse: Codable { let conversation_id: Int }

struct ContentView: View {
    @State private var text = ""
    @State private var reply = ""
    @State private var busy = false
    @AppStorage("aiConversationId") private var conversationId: Int = 0
    @State private var apiToken: String = ""
    private let baseURL = URL(string: "http://YOUR-PC-IP:8000")!

    var body: some View {
        NavigationStack {
            VStack(spacing: 16) {
                ScrollView { Text(reply.isEmpty ? "Ask your AI anything." : reply).frame(maxWidth: .infinity, alignment: .leading) }
                SecureField("Required API token", text: $apiToken)
                    .textFieldStyle(.roundedBorder)
                HStack {
                    TextField("Ask your AI…", text: $text, axis: .vertical)
                        .textFieldStyle(.roundedBorder)
                    Button(busy ? "…" : "Send") { Task { await send() } }
                        .disabled(busy || text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }
            }
            .padding()
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("New Chat") { conversationId = 0; reply = "" }
                }
            }
            .navigationTitle("AI System")
        }
    }

    private func headers(json: Bool = false) -> [String: String] {
        var h = [String: String]()
        if json { h["Content-Type"] = "application/json" }
        if !apiToken.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            h["Authorization"] = "Bearer \(apiToken.trimmingCharacters(in: .whitespacesAndNewlines))"
        }
        return h
    }

    private func ensureConversation() async throws -> Int {
        if conversationId > 0 { return conversationId }
        var req = URLRequest(url: baseURL.appendingPathComponent("v1/conversations"))
        req.httpMethod = "POST"
        headers(json: true).forEach { req.setValue($1, forHTTPHeaderField: $0) }
        req.httpBody = try JSONEncoder().encode([
            "title": "iPhone AI Chat",
            "model": "llama3.1:8b",
            "assistant_profile": "general"
        ])
        let (data, response) = try await URLSession.shared.data(for: req)
        guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
            throw URLError(.badServerResponse)
        }
        let created = try JSONDecoder().decode(ConversationResponse.self, from: data)
        conversationId = created.conversation_id
        return created.conversation_id
    }

    func send() async {
        busy = true; defer { busy = false }
        let prompt = text.trimmingCharacters(in: .whitespacesAndNewlines); text = ""
        do {
            let cid = try await ensureConversation()
            var req = URLRequest(url: baseURL.appendingPathComponent("v1/companion/chat"))
            req.httpMethod = "POST"
            headers(json: true).forEach { req.setValue($1, forHTTPHeaderField: $0) }
            let body = AgentRequest(model: "auto", messages: [ChatMessage(role: "user", content: prompt)], assistant_profile: "general", conversation_id: cid)
            req.httpBody = try JSONEncoder().encode(body)
            let (data, response) = try await URLSession.shared.data(for: req)
            guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
                reply = String(data: data, encoding: .utf8) ?? "Request failed"
                return
            }
            let decoded = try JSONDecoder().decode(AgentResponse.self, from: data)
            if let returnedId = decoded.conversation_id { conversationId = returnedId }
            reply = decoded.message?.content ?? String(data: data, encoding: .utf8) ?? "No response"
        } catch {
            reply = "Connection error: \(error.localizedDescription)"
        }
    }
}

