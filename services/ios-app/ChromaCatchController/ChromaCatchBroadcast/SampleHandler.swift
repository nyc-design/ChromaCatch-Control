import ReplayKit
import UIKit

/// ReplayKit Broadcast Upload Extension sample handler.
/// Captures the iPhone screen and sends H.264 frames to the ChromaCatch backend
/// using either RTP+FEC (UDP, lowest latency) or WebSocket (TCP fallback).
///
/// Transport mode is read from App Group UserDefaults:
///   "transportMode" = "rtp-fec" | "websocket" (default: "rtp-fec")
///   "rtpFecHost" = backend UDP host (required for rtp-fec)
///   "rtpFecPort" = backend UDP port (default: 7000)
///
/// Runs in a separate process with a 50MB memory limit.
class SampleHandler: RPBroadcastSampleHandler {
    private var encoder: H264Encoder?
    private var wsClient: BroadcastWSClient?
    private var rtpClient: BroadcastRTPClient?
    private var sampleCount: Int = 0

    override func broadcastStarted(withSetupInfo setupInfo: [String: NSObject]?) {
        // Read configuration from App Group shared defaults
        let defaults = UserDefaults(suiteName: "group.com.chromacatch")
        let backendURL = defaults?.string(forKey: "backendURL") ?? "wss://localhost:8000/ws/client"
        let apiKey = defaults?.string(forKey: "apiKey") ?? ""
        let clientId = defaults?.string(forKey: "clientId") ?? "ios-broadcast"
        let transportMode = defaults?.string(forKey: "transportMode") ?? "rtp-fec"
        let rtpHost = defaults?.string(forKey: "rtpFecHost") ?? ""
        let rtpPort = defaults?.integer(forKey: "rtpFecPort") ?? 7000

        // Detect actual screen dimensions (portrait-native on iPhone)
        let screenSize = UIScreen.main.nativeBounds.size
        let (encWidth, encHeight) = H264Encoder.scaledDimensions(
            screenWidth: Int(screenSize.width),
            screenHeight: Int(screenSize.height),
            maxDimension: 1280
        )
        NSLog("[SampleHandler] Screen native: %.0fx%.0f → Encode: %dx%d, transport=%@",
              screenSize.width, screenSize.height, encWidth, encHeight, transportMode)

        // Initialize H.264 encoder at detected dimensions
        encoder = H264Encoder(bitrate: 2_000_000, keyframeInterval: 60)

        // Initialize transport based on mode
        if transportMode == "rtp-fec" {
            let host = rtpHost.isEmpty ? Self.extractHost(from: backendURL) : rtpHost
            guard !host.isEmpty else {
                finishBroadcastWithError(NSError(domain: "ChromaCatch", code: 1,
                    userInfo: [NSLocalizedDescriptionKey: "RTP+FEC requires a host (set rtpFecHost or backendURL)"]))
                return
            }
            NSLog("[SampleHandler] RTP+FEC → %@:%d", host, rtpPort)
            rtpClient = BroadcastRTPClient(host: host, port: UInt16(rtpPort))
            rtpClient?.connect()
        } else {
            // WebSocket fallback
            guard let url = URL(string: backendURL.replacingOccurrences(of: "/ws/control", with: "/ws/client")) else {
                finishBroadcastWithError(NSError(domain: "ChromaCatch", code: 1,
                    userInfo: [NSLocalizedDescriptionKey: "Invalid backend URL"]))
                return
            }
            wsClient = BroadcastWSClient(url: url, apiKey: apiKey, clientId: clientId)
            wsClient?.connect()
        }

        // Wire encoder output to active transport
        encoder?.onEncodedAU = { [weak self] data, isKeyframe, captureTimestamp in
            if let rtpClient = self?.rtpClient {
                rtpClient.sendH264AU(data, isKeyframe: isKeyframe, captureTimestamp: captureTimestamp)
            } else {
                self?.wsClient?.sendH264AU(data, isKeyframe: isKeyframe, captureTimestamp: captureTimestamp)
            }
        }

        guard encoder?.start(width: encWidth, height: encHeight) == true else {
            finishBroadcastWithError(NSError(domain: "ChromaCatch", code: 2,
                userInfo: [NSLocalizedDescriptionKey: "Failed to start H.264 encoder at \(encWidth)x\(encHeight)"]))
            return
        }
    }

    override func broadcastPaused() {
        // No action needed — ReplayKit pauses sample delivery automatically
    }

    override func broadcastResumed() {
        // No action needed — samples resume automatically
    }

    override func broadcastFinished() {
        NSLog("[SampleHandler] broadcastFinished — stopping encoder and disconnecting transport")
        encoder?.stop()
        wsClient?.disconnect()
        rtpClient?.disconnect()
        encoder = nil
        wsClient = nil
        rtpClient = nil
    }

    override func processSampleBuffer(_ sampleBuffer: CMSampleBuffer, with sampleBufferType: RPSampleBufferType) {
        switch sampleBufferType {
        case .video:
            sampleCount += 1
            if sampleCount <= 3 || sampleCount % 300 == 0 {
                let transport = rtpClient != nil ? "rtp-fec" : "ws"
                let connected = (rtpClient?.isConnected ?? wsClient?.isConnected) == true
                NSLog("[SampleHandler] video sample #%d, encoder=%@, transport=%@(connected=%d)",
                      sampleCount,
                      encoder != nil ? "yes" : "nil",
                      transport,
                      connected ? 1 : 0)
            }
            encoder?.encode(sampleBuffer)

        case .audioApp:
            // Future: extract PCM from sampleBuffer and send via transport
            break

        case .audioMic:
            // We don't capture microphone audio
            break

        @unknown default:
            break
        }
    }

    // MARK: - Helpers

    /// Extract hostname from a WebSocket URL for RTP+FEC UDP target.
    private static func extractHost(from urlString: String) -> String {
        // Convert ws:// or wss:// to http:// for URL parsing
        let httpURL = urlString
            .replacingOccurrences(of: "wss://", with: "https://")
            .replacingOccurrences(of: "ws://", with: "http://")
        guard let url = URL(string: httpURL), let host = url.host else {
            return ""
        }
        return host
    }
}
