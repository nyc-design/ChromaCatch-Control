import Foundation
import Network

/// RTP+FEC UDP sender for the Broadcast Upload Extension.
///
/// Implements the same wire format as the Python `UnifiedRTPFECTransport`:
///   [RTP Header (12B)] [ChromaCatch Header (8B)] [Payload]
///
/// Current implementation sends data shards without parity (data_shards == total_shards).
/// This is fully compatible with the backend receiver — FEC recovery is only used when
/// packets are lost, and without parity shards, the receiver simply reassembles directly.
///
/// If packet loss becomes an issue, Reed-Solomon FEC can be added here using
/// a Swift zfec implementation while keeping the same wire format.
class BroadcastRTPClient {
    private var connection: NWConnection?
    private let host: NWEndpoint.Host
    private let port: NWEndpoint.Port
    private let queue = DispatchQueue(label: "com.chromacatch.rtp-fec", qos: .userInitiated)

    private(set) var isConnected = false
    private var intentionalDisconnect = false

    // RTP state
    private var videoSeq: UInt16 = 0
    private var frameId: UInt16 = 0
    private var ssrc: UInt32
    private var videoTimestamp: UInt32 = 0

    // Audio RTP state
    private var audioSeq: UInt16 = 0
    private var audioSsrc: UInt32
    private var audioTimestamp: UInt32 = 0

    // Counters
    private(set) var framesSent: Int = 0

    // Protocol constants (must match shared/rtp_fec_protocol.py)
    private static let rtpHeaderSize = 12
    private static let ccHeaderSize = 8
    private static let headerSize = rtpHeaderSize + ccHeaderSize
    private static let maxMTU = 1500
    private static let ipUdpOverhead = 28
    private static let payloadSize = maxMTU - ipUdpOverhead - headerSize  // ~1452
    private static let fecDataShards = 10
    private static let rtpVersion: UInt8 = 2
    private static let rtpPayloadTypeVideo: UInt8 = 96
    private static let rtpPayloadTypeAudio: UInt8 = 97
    private static let rtpClockRate: UInt32 = 90000
    private static let rtpAudioClockRate: UInt32 = 48000
    private static let flagKeyframe: UInt8 = 0x01
    private static let flagLastBlock: UInt8 = 0x02

    /// Buffers the most recent keyframe so it can be sent on (re)connect.
    private var pendingKeyframe: (data: Data, timestamp: Double)?

    init(host: String, port: UInt16) {
        self.host = NWEndpoint.Host(host)
        self.port = NWEndpoint.Port(rawValue: port) ?? .init(integerLiteral: 7000)
        self.ssrc = UInt32.random(in: 0...UInt32.max)
        self.audioSsrc = UInt32.random(in: 0...UInt32.max)
    }

    func connect() {
        guard !intentionalDisconnect else {
            NSLog("[BroadcastRTP] connect() ignored — already disconnected")
            return
        }

        let params = NWParameters.udp
        params.allowLocalEndpointReuse = true

        connection = NWConnection(host: host, port: port, using: params)
        connection?.stateUpdateHandler = { [weak self] state in
            switch state {
            case .ready:
                self?.isConnected = true
                NSLog("[BroadcastRTP] Connected to %@:%d", "\(self?.host ?? "")", self?.port.rawValue ?? 0)
                // Flush buffered keyframe
                if let kf = self?.pendingKeyframe {
                    self?.pendingKeyframe = nil
                    self?.sendH264AU(kf.data, isKeyframe: true, captureTimestamp: kf.timestamp)
                }
            case .failed(let error):
                self?.isConnected = false
                NSLog("[BroadcastRTP] Failed: %@, reconnecting in 3s", error.localizedDescription)
                DispatchQueue.global().asyncAfter(deadline: .now() + 3.0) { [weak self] in
                    guard let self = self, !self.intentionalDisconnect else { return }
                    self.connect()
                }
            case .cancelled:
                self?.isConnected = false
            default:
                break
            }
        }
        connection?.start(queue: queue)
    }

    func disconnect() {
        NSLog("[BroadcastRTP] disconnect() called")
        intentionalDisconnect = true
        connection?.cancel()
        connection = nil
        isConnected = false
        pendingKeyframe = nil
    }

    /// Send an H.264 Access Unit as RTP+FEC packets.
    func sendH264AU(_ auData: Data, isKeyframe: Bool, captureTimestamp: Double) {
        if isKeyframe {
            pendingKeyframe = (data: auData, timestamp: captureTimestamp)
        }
        guard isConnected, let connection = connection else { return }

        frameId = frameId &+ 1

        // Split AU into payload-sized chunks
        var chunks: [Data] = []
        var offset = 0
        while offset < auData.count {
            let end = min(offset + Self.payloadSize, auData.count)
            chunks.append(auData[offset..<end])
            offset = end
        }
        guard !chunks.isEmpty else { return }

        // Group chunks into FEC blocks (no parity — data_shards == total_shards)
        let numBlocks = (chunks.count + Self.fecDataShards - 1) / Self.fecDataShards

        for blockIdx in 0..<numBlocks {
            let start = blockIdx * Self.fecDataShards
            let end = min(start + Self.fecDataShards, chunks.count)
            let blockChunks = Array(chunks[start..<end])
            let actualDataCount = blockChunks.count
            let isLastBlock = blockIdx == numBlocks - 1
            let lastOrigLen = blockChunks.last?.count ?? 0

            for shardIdx in 0..<actualDataCount {
                var flags: UInt8 = 0
                if isKeyframe { flags |= Self.flagKeyframe }
                if isLastBlock { flags |= Self.flagLastBlock }

                let origLen: UInt8 = (shardIdx == actualDataCount - 1) ? UInt8(lastOrigLen & 0xFF) : 0
                let marker = (shardIdx == actualDataCount - 1) && isLastBlock

                let rtpHdr = Self.buildRTPHeader(
                    seq: videoSeq, timestamp: videoTimestamp, ssrc: ssrc,
                    marker: marker, pt: Self.rtpPayloadTypeVideo
                )
                let ccHdr = Self.buildCCHeader(
                    frameId: frameId, blockId: UInt8(blockIdx),
                    shardIndex: UInt8(shardIdx),
                    dataShards: UInt8(actualDataCount),
                    totalShards: UInt8(actualDataCount),  // No parity shards
                    flags: flags, origLen: origLen
                )

                // Pad payload to payloadSize for protocol consistency
                var payload = blockChunks[shardIdx]
                if payload.count < Self.payloadSize {
                    payload.append(Data(count: Self.payloadSize - payload.count))
                }

                var packet = Data()
                packet.append(rtpHdr)
                packet.append(ccHdr)
                packet.append(payload)

                connection.send(content: packet, completion: .contentProcessed { error in
                    if let error = error {
                        NSLog("[BroadcastRTP] Send error: %@", error.localizedDescription)
                    }
                })

                videoSeq = videoSeq &+ 1
            }
        }

        videoTimestamp = videoTimestamp &+ (Self.rtpClockRate / 30)
        framesSent += 1

        if framesSent <= 3 || framesSent % 300 == 0 {
            NSLog("[BroadcastRTP] sent frame #%d, kf=%@, %d bytes, %d chunks",
                  framesSent, isKeyframe ? "YES" : "no", auData.count, chunks.count)
        }
    }

    // MARK: - Protocol Helpers

    /// Build a 12-byte RTP header (RFC 3550).
    private static func buildRTPHeader(
        seq: UInt16, timestamp: UInt32, ssrc: UInt32,
        marker: Bool, pt: UInt8
    ) -> Data {
        var data = Data(count: rtpHeaderSize)
        data[0] = (rtpVersion << 6)  // V=2, P=0, X=0, CC=0
        data[1] = pt | (marker ? 0x80 : 0x00)
        // Sequence number (big-endian)
        data[2] = UInt8((seq >> 8) & 0xFF)
        data[3] = UInt8(seq & 0xFF)
        // Timestamp (big-endian)
        data[4] = UInt8((timestamp >> 24) & 0xFF)
        data[5] = UInt8((timestamp >> 16) & 0xFF)
        data[6] = UInt8((timestamp >> 8) & 0xFF)
        data[7] = UInt8(timestamp & 0xFF)
        // SSRC (big-endian)
        data[8] = UInt8((ssrc >> 24) & 0xFF)
        data[9] = UInt8((ssrc >> 16) & 0xFF)
        data[10] = UInt8((ssrc >> 8) & 0xFF)
        data[11] = UInt8(ssrc & 0xFF)
        return data
    }

    /// Build an 8-byte ChromaCatch FEC header.
    private static func buildCCHeader(
        frameId: UInt16, blockId: UInt8, shardIndex: UInt8,
        dataShards: UInt8, totalShards: UInt8,
        flags: UInt8, origLen: UInt8
    ) -> Data {
        var data = Data(count: ccHeaderSize)
        // frame_id (big-endian u16)
        data[0] = UInt8((frameId >> 8) & 0xFF)
        data[1] = UInt8(frameId & 0xFF)
        data[2] = blockId
        data[3] = shardIndex
        data[4] = dataShards
        data[5] = totalShards
        data[6] = flags
        data[7] = origLen
        return data
    }
}
