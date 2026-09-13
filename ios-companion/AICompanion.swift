import SwiftUI
import UniformTypeIdentifiers

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
    @State private var uploading = false
    @State private var showingImporter = false
    @State private var uploadStatus = ""
    @AppStorage("aiConversationId") private var conversationId: Int = 0
    @AppStorage("jarvisGatewayURL") private var gatewayURL = "http://YOUR-PC-IP:8000"
    @State private var apiToken: String = ""

    private var baseURL: URL? { URL(string: gatewayURL.trimmingCharacters(in: .whitespacesAndNewlines)) }

    var body: some View {
        NavigationStack {
            VStack(spacing: 16) {
                ScrollView { Text(reply.isEmpty ? "Ask your AI anything." : reply).frame(maxWidth: .infinity, alignment: .leading) }
                SecureField("Required API token", text: $apiToken)
                    .textFieldStyle(.roundedBorder)
                TextField("Jarvis gateway URL", text: $gatewayURL)
                    .textFieldStyle(.roundedBorder)
                    .textInputAutocapitalization(.never)
                    .keyboardType(.URL)
                Button(uploading ? "Uploading…" : "Send Files to Jarvis") {
                    showingImporter = true
                }
                .disabled(uploading || apiToken.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                if !uploadStatus.isEmpty {
                    Text(uploadStatus).font(.footnote).frame(maxWidth: .infinity, alignment: .leading)
                }
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
            .fileImporter(isPresented: $showingImporter, allowedContentTypes: [.item], allowsMultipleSelection: true) { result in
                switch result {
                case .success(let files): Task { await upload(files) }
                case .failure(let error): uploadStatus = "File selection failed: \(error.localizedDescription)"
                }
            }
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
        guard let baseURL else { throw URLError(.badURL) }
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
            guard let baseURL else { throw URLError(.badURL) }
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

    func upload(_ urls: [URL]) async {
        guard let baseURL else { uploadStatus = "Enter a valid Jarvis gateway URL."; return }
        uploading = true; defer { uploading = false }
        var completed = 0
        for url in urls {
            let access = url.startAccessingSecurityScopedResource()
            do {
                let values = try url.resourceValues(forKeys: [.fileSizeKey])
                if (values.fileSize ?? 0) > 250 * 1024 * 1024 {
                    throw NSError(domain: "JarvisUpload", code: 413,
                                  userInfo: [NSLocalizedDescriptionKey: "\(url.lastPathComponent) exceeds 250 MB"])
                }
                let data = try Data(contentsOf: url, options: .mappedIfSafe)
                if access { url.stopAccessingSecurityScopedResource() }
                let boundary = "Jarvis-\(UUID().uuidString)"
                var body = Data()
                body.append("--\(boundary)\r\nContent-Disposition: form-data; name=\"folder\"\r\n\r\nInbox\r\n".data(using: .utf8)!)
                body.append("--\(boundary)\r\nContent-Disposition: form-data; name=\"file\"; filename=\"\(url.lastPathComponent.replacingOccurrences(of: "\"", with: "_"))\"\r\nContent-Type: application/octet-stream\r\n\r\n".data(using: .utf8)!)
                body.append(data)
                body.append("\r\n--\(boundary)--\r\n".data(using: .utf8)!)
                var request = URLRequest(url: baseURL.appendingPathComponent("v1/companion/upload"))
                request.httpMethod = "POST"
                headers().forEach { request.setValue($1, forHTTPHeaderField: $0) }
                request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
                let (_, response) = try await URLSession.shared.upload(for: request, from: body)
                guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
                    throw URLError(.badServerResponse)
                }
                completed += 1
                uploadStatus = "Uploaded \(completed) of \(urls.count): \(url.lastPathComponent)"
            } catch {
                if access { url.stopAccessingSecurityScopedResource() }
                uploadStatus = "Uploaded \(completed) of \(urls.count). Failed: \(error.localizedDescription)"
                return
            }
        }
        uploadStatus = "Uploaded \(completed) file\(completed == 1 ? "" : "s") to your Jarvis PC."
    }
}

