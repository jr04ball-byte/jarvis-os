# iPhone Companion

The fastest way to use the AI System on iPhone today is the responsive `/companion` web app served by the gateway. Open it from Safari on the trusted home LAN and use it like a lightweight app.

For a native iOS build, `AICompanion.swift` is a minimal SwiftUI client. Set `baseURL` to your gateway's HTTPS/LAN URL before building in Xcode.

Security: do not expose the gateway directly to the public internet. For remote access, put it behind a VPN such as Tailscale or another authenticated private network.
