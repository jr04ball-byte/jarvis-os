# iPhone Companion

The fastest way to use Jarvis on iPhone is the responsive `/companion` web app served by the gateway. Open it from Safari on the trusted home LAN, enter `AI_API_TOKEN`, and choose **Share → Add to Home Screen**. The companion provides chat, approvals, push-to-talk navigation, multi-file upload, and a list of recent phone uploads stored on the PC.

For a native iOS build, `AICompanion.swift` is a SwiftUI client with chat and the Apple Files picker. Enter the gateway's HTTPS/LAN URL in the app. The sample must still be placed in an Xcode iOS project and signed with an Apple developer identity before device installation.

Uploads default to `<ai-system>/phone-uploads/Inbox`. Override this with `JARVIS_PHONE_UPLOAD_DIR`, and set the maximum individual file size with `JARVIS_PHONE_UPLOAD_MAX_MB`.

Security: do not expose the gateway directly to the public internet. For remote access, put it behind a VPN such as Tailscale or another authenticated private network.
