import ExternalAccessory
import Foundation

/// Manages the External Accessory session with the iTools dongle (BT-01414-CORE).
///
/// Opening an EA session triggers the iAP2/MFi authentication handshake,
/// which activates the dongle's GPS forwarding mode. The session must stay
/// open for the dongle to relay NMEA coordinates to iPhone's Core Location.
class EAManager: NSObject, ObservableObject, StreamDelegate {
    static let protocolString = "com.feasycom.BLEAssistant"

    private var accessory: EAAccessory?
    private var session: EASession?
    private var inputStream: InputStream?
    private var outputStream: OutputStream?
    private let log: (String) -> Void
    private var lastRetryTime: Date = .distantPast

    @Published var isConnected = false

    init(log: @escaping (String) -> Void = { print("EA: \($0)") }) {
        self.log = log
        super.init()

        NotificationCenter.default.addObserver(
            self,
            selector: #selector(accessoryDidConnect(_:)),
            name: .EAAccessoryDidConnect,
            object: nil
        )
        NotificationCenter.default.addObserver(
            self,
            selector: #selector(accessoryDidDisconnect(_:)),
            name: .EAAccessoryDidDisconnect,
            object: nil
        )
        EAAccessoryManager.shared().registerForLocalNotifications()

        // Check if dongle is already paired and connected
        findAndOpenSession()
    }

    deinit {
        NotificationCenter.default.removeObserver(self)
        closeSession()
    }

    /// Called periodically from AppCoordinator to retry if dongle wasn't found at init time.
    /// Throttled to avoid log spam (retries at most every 5 seconds).
    func retryConnection() {
        guard !isConnected else { return }
        let now = Date()
        guard now.timeIntervalSince(lastRetryTime) >= 5.0 else { return }
        lastRetryTime = now
        findAndOpenSession()
    }

    // MARK: - Session Management

    private var hasLoggedInitialDiagnostics = false

    private func findAndOpenSession() {
        let accessories = EAAccessoryManager.shared().connectedAccessories

        // Full diagnostics on first call only
        if !hasLoggedInitialDiagnostics {
            hasLoggedInitialDiagnostics = true
            let declared = Bundle.main.object(forInfoDictionaryKey: "UISupportedExternalAccessoryProtocols") as? [String]
            print("[EA] Info.plist EA protocols: \(declared ?? ["MISSING!"])")
            log("Info.plist EA protocols: \(declared ?? ["MISSING!"])")

            let bgModes = Bundle.main.object(forInfoDictionaryKey: "UIBackgroundModes") as? [String]
            print("[EA] Info.plist UIBackgroundModes: \(bgModes ?? ["MISSING!"])")
            log("Info.plist UIBackgroundModes: \(bgModes ?? ["MISSING!"])")

            let btDesc = Bundle.main.object(forInfoDictionaryKey: "NSBluetoothAlwaysUsageDescription") as? String
            print("[EA] Info.plist NSBluetoothAlwaysUsageDescription: \(btDesc != nil ? "present" : "MISSING!")")
        }

        print("[EA] Found \(accessories.count) connected accessories")

        for acc in accessories {
            print("[EA]   - \(acc.name) protocols: \(acc.protocolStrings)")
            log("  - \(acc.name) protocols: \(acc.protocolStrings)")
        }

        guard let target = accessories.first(where: {
            $0.protocolStrings.contains(Self.protocolString)
        }) else {
            if accessories.isEmpty {
                log("No EA accessories found — pair BT-01414-CORE in iPhone Settings > Bluetooth")
            } else {
                log("Dongle not in \(accessories.count) accessories — need protocol '\(Self.protocolString)'")
            }
            return
        }
        openSession(with: target)
    }

    private func openSession(with accessory: EAAccessory) {
        guard let session = EASession(
            accessory: accessory,
            forProtocol: Self.protocolString
        ) else {
            log("Failed to open EASession — is '\(Self.protocolString)' in Info.plist?")
            return
        }

        self.accessory = accessory
        self.session = session

        // Open input stream (read dongle data to prevent buffer pressure)
        if let input = session.inputStream {
            self.inputStream = input
            input.delegate = self
            input.schedule(in: .main, forMode: .default)
            input.open()
        }

        // Open output stream (needed to keep session active)
        if let output = session.outputStream {
            self.outputStream = output
            output.delegate = self
            output.schedule(in: .main, forMode: .default)
            output.open()
        }

        DispatchQueue.main.async { self.isConnected = true }
        log("EASession opened with \(accessory.name) (ID: \(accessory.connectionID))")
    }

    private func closeSession() {
        inputStream?.close()
        inputStream?.remove(from: .main, forMode: .default)
        inputStream = nil

        outputStream?.close()
        outputStream?.remove(from: .main, forMode: .default)
        outputStream = nil

        session = nil
        accessory = nil
        DispatchQueue.main.async { self.isConnected = false }
    }

    // MARK: - Notifications

    @objc private func accessoryDidConnect(_ notification: Notification) {
        guard let accessory = notification.userInfo?[EAAccessoryKey] as? EAAccessory,
              accessory.protocolStrings.contains(Self.protocolString) else { return }
        log("Dongle connected — opening EA session")
        openSession(with: accessory)
    }

    @objc private func accessoryDidDisconnect(_ notification: Notification) {
        guard let disconnected = notification.userInfo?[EAAccessoryKey] as? EAAccessory,
              disconnected.connectionID == accessory?.connectionID else { return }
        log("Dongle disconnected")
        closeSession()
    }

    // MARK: - StreamDelegate

    func stream(_ aStream: Stream, handle eventCode: Stream.Event) {
        switch eventCode {
        case .hasBytesAvailable:
            guard let input = aStream as? InputStream else { return }
            var buffer = [UInt8](repeating: 0, count: 1024)
            let bytesRead = input.read(&buffer, maxLength: buffer.count)
            if bytesRead > 0 {
                let data = Data(buffer[0..<bytesRead])
                log("Received \(bytesRead) bytes: \(data.map { String(format: "%02x", $0) }.joined())")
            }

        case .hasSpaceAvailable:
            break // Output stream ready

        case .errorOccurred:
            log("Stream error: \(aStream.streamError?.localizedDescription ?? "unknown")")
            closeSession()
            // Try to reconnect
            DispatchQueue.main.asyncAfter(deadline: .now() + 2.0) { [weak self] in
                self?.findAndOpenSession()
            }

        case .endEncountered:
            log("Stream ended")
            closeSession()

        default:
            break
        }
    }
}
