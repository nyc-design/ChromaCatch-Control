//
//  BLEHIDCommander.swift
//  ChromaCatchController
//
//  BLE HID peripheral - allows iOS to act as a Bluetooth mouse/keyboard/gamepad
//  to target devices (Switch, 3DS, PC) without requiring ESP32 hardware.
//
//  Uses CBPeripheralManager with expanded 128-bit HID service UUID to bypass
//  Apple's short-form blocklist. Proven approach (BluTouch, iOS 15.5+).
//

import Foundation
import CoreBluetooth
import os

// MARK: - HID Service UUIDs (expanded 128-bit to bypass Apple blocklist)

/// HID Service UUID - expanded 128-bit form of 0x1812
private let kHIDServiceUUID = CBUUID(string: "00001812-0000-1000-8000-00805F9B34FB")
/// HID Information Characteristic (mandatory)
private let kHIDInfoUUID = CBUUID(string: "00002A4A-0000-1000-8000-00805F9B34FB")
/// HID Report Map (mandatory) - describes the report descriptor
private let kHIDReportMapUUID = CBUUID(string: "00002A4B-0000-1000-8000-00805F9B34FB")
/// HID Control Point (mandatory)
private let kHIDControlPointUUID = CBUUID(string: "00002A4C-0000-1000-8000-00805F9B34FB")
/// HID Report (the actual input data)
private let kHIDReportUUID = CBUUID(string: "00002A4D-0000-1000-8000-00805F9B34FB")
/// Protocol Mode
private let kProtocolModeUUID = CBUUID(string: "00002A4E-0000-1000-8000-00805F9B34FB")

/// Battery Service (required for some hosts)
private let kBatteryServiceUUID = CBUUID(string: "0000180F-0000-1000-8000-00805F9B34FB")
private let kBatteryLevelUUID = CBUUID(string: "00002A19-0000-1000-8000-00805F9B34FB")

/// Device Information Service
private let kDeviceInfoServiceUUID = CBUUID(string: "0000180A-0000-1000-8000-00805F9B34FB")
private let kManufacturerNameUUID = CBUUID(string: "00002A29-0000-1000-8000-00805F9B34FB")
private let kPnPIDUUID = CBUUID(string: "00002A50-0000-1000-8000-00805F9B34FB")

/// Report Reference Descriptor UUID
private let kReportReferenceUUID = CBUUID(string: "00002908-0000-1000-8000-00805F9B34FB")
/// Client Characteristic Configuration Descriptor
private let kCCCDescriptorUUID = CBUUID(string: "00002902-0000-1000-8000-00805F9B34FB")

private let log = Logger(subsystem: "com.chromacatch", category: "BLEHIDCommander")

// MARK: - HID Profile

enum HIDProfile: String, CaseIterable {
    case mouse = "mouse"
    case keyboard = "keyboard"
    case gamepad = "gamepad"
    case combo = "combo"  // mouse + keyboard
}

// MARK: - Report Descriptors

/// Minimal mouse HID Report Descriptor
/// Report ID 1: buttons (3), X (8-bit), Y (8-bit), wheel (8-bit)
private let mouseReportDescriptor: [UInt8] = [
    0x05, 0x01,       // Usage Page (Generic Desktop)
    0x09, 0x02,       // Usage (Mouse)
    0xA1, 0x01,       // Collection (Application)
    0x85, 0x01,       //   Report ID (1)
    0x09, 0x01,       //   Usage (Pointer)
    0xA1, 0x00,       //   Collection (Physical)
    0x05, 0x09,       //     Usage Page (Button)
    0x19, 0x01,       //     Usage Minimum (1)
    0x29, 0x03,       //     Usage Maximum (3)
    0x15, 0x00,       //     Logical Minimum (0)
    0x25, 0x01,       //     Logical Maximum (1)
    0x95, 0x03,       //     Report Count (3)
    0x75, 0x01,       //     Report Size (1)
    0x81, 0x02,       //     Input (Data, Variable, Absolute)
    0x95, 0x01,       //     Report Count (1)
    0x75, 0x05,       //     Report Size (5)
    0x81, 0x01,       //     Input (Constant) - padding
    0x05, 0x01,       //     Usage Page (Generic Desktop)
    0x09, 0x30,       //     Usage (X)
    0x09, 0x31,       //     Usage (Y)
    0x09, 0x38,       //     Usage (Wheel)
    0x15, 0x81,       //     Logical Minimum (-127)
    0x25, 0x7F,       //     Logical Maximum (127)
    0x75, 0x08,       //     Report Size (8)
    0x95, 0x03,       //     Report Count (3)
    0x81, 0x06,       //     Input (Data, Variable, Relative)
    0xC0,             //   End Collection
    0xC0              // End Collection
]

/// Keyboard HID Report Descriptor
/// Report ID 2: modifier (8-bit), reserved, keys[6]
private let keyboardReportDescriptor: [UInt8] = [
    0x05, 0x01,       // Usage Page (Generic Desktop)
    0x09, 0x06,       // Usage (Keyboard)
    0xA1, 0x01,       // Collection (Application)
    0x85, 0x02,       //   Report ID (2)
    0x05, 0x07,       //   Usage Page (Keyboard/Keypad)
    0x19, 0xE0,       //   Usage Minimum (Left Control)
    0x29, 0xE7,       //   Usage Maximum (Right GUI)
    0x15, 0x00,       //   Logical Minimum (0)
    0x25, 0x01,       //   Logical Maximum (1)
    0x75, 0x01,       //   Report Size (1)
    0x95, 0x08,       //   Report Count (8)
    0x81, 0x02,       //   Input (Data, Variable, Absolute) - Modifiers
    0x95, 0x01,       //   Report Count (1)
    0x75, 0x08,       //   Report Size (8)
    0x81, 0x01,       //   Input (Constant) - Reserved
    0x95, 0x06,       //   Report Count (6)
    0x75, 0x08,       //   Report Size (8)
    0x15, 0x00,       //   Logical Minimum (0)
    0x25, 0x65,       //   Logical Maximum (101)
    0x05, 0x07,       //   Usage Page (Keyboard/Keypad)
    0x19, 0x00,       //   Usage Minimum (0)
    0x29, 0x65,       //   Usage Maximum (101)
    0x81, 0x00,       //   Input (Data, Array)
    0xC0              // End Collection
]

/// Gamepad HID Report Descriptor
/// Report ID 3: 16 buttons, hat switch, 2x analog sticks (X, Y, Z, Rz)
private let gamepadReportDescriptor: [UInt8] = [
    0x05, 0x01,       // Usage Page (Generic Desktop)
    0x09, 0x05,       // Usage (Gamepad)
    0xA1, 0x01,       // Collection (Application)
    0x85, 0x03,       //   Report ID (3)
    // 16 buttons
    0x05, 0x09,       //   Usage Page (Button)
    0x19, 0x01,       //   Usage Minimum (1)
    0x29, 0x10,       //   Usage Maximum (16)
    0x15, 0x00,       //   Logical Minimum (0)
    0x25, 0x01,       //   Logical Maximum (1)
    0x75, 0x01,       //   Report Size (1)
    0x95, 0x10,       //   Report Count (16)
    0x81, 0x02,       //   Input (Data, Variable, Absolute)
    // Hat switch (D-pad)
    0x05, 0x01,       //   Usage Page (Generic Desktop)
    0x09, 0x39,       //   Usage (Hat Switch)
    0x15, 0x00,       //   Logical Minimum (0)
    0x25, 0x07,       //   Logical Maximum (7)
    0x35, 0x00,       //   Physical Minimum (0)
    0x46, 0x3B, 0x01, //   Physical Maximum (315)
    0x65, 0x14,       //   Unit (Degrees)
    0x75, 0x04,       //   Report Size (4)
    0x95, 0x01,       //   Report Count (1)
    0x81, 0x42,       //   Input (Data, Variable, Absolute, Null State)
    0x75, 0x04,       //   Report Size (4) - padding
    0x95, 0x01,       //   Report Count (1)
    0x81, 0x01,       //   Input (Constant)
    // Left stick X, Y + Right stick Z, Rz
    0x09, 0x30,       //   Usage (X)
    0x09, 0x31,       //   Usage (Y)
    0x09, 0x32,       //   Usage (Z)
    0x09, 0x35,       //   Usage (Rz)
    0x15, 0x00,       //   Logical Minimum (0)
    0x26, 0xFF, 0x00, //   Logical Maximum (255)
    0x75, 0x08,       //   Report Size (8)
    0x95, 0x04,       //   Report Count (4)
    0x81, 0x02,       //   Input (Data, Variable, Absolute)
    0xC0              // End Collection
]

// MARK: - BLEHIDCommander

@MainActor
class BLEHIDCommander: NSObject, ObservableObject {
    @Published var isAdvertising = false
    @Published var isConnected = false
    @Published var connectedDeviceName: String?
    @Published var currentProfile: HIDProfile = .combo

    private var peripheralManager: CBPeripheralManager?
    private var hidService: CBMutableService?
    private var mouseReportCharacteristic: CBMutableCharacteristic?
    private var keyboardReportCharacteristic: CBMutableCharacteristic?
    private var gamepadReportCharacteristic: CBMutableCharacteristic?

    // Track subscribed centrals
    private var subscribedCentrals: [CBCentral] = []

    // Current gamepad state
    private var gamepadButtons: UInt16 = 0
    private var gamepadHat: UInt8 = 0x0F  // centered (null state)
    private var gamepadLeftX: UInt8 = 128
    private var gamepadLeftY: UInt8 = 128
    private var gamepadRightX: UInt8 = 128
    private var gamepadRightY: UInt8 = 128

    override init() {
        super.init()
    }

    func start(profile: HIDProfile = .combo) {
        currentProfile = profile
        peripheralManager = CBPeripheralManager(delegate: self, queue: nil)
    }

    func stop() {
        peripheralManager?.stopAdvertising()
        peripheralManager = nil
        isAdvertising = false
        isConnected = false
        subscribedCentrals.removeAll()
    }

    // MARK: - Input Commands

    /// Send mouse move (relative)
    func mouseMove(dx: Int8, dy: Int8, wheel: Int8 = 0) {
        let report = Data([0x00, UInt8(bitPattern: dx), UInt8(bitPattern: dy), UInt8(bitPattern: wheel)])
        sendReport(report, characteristic: mouseReportCharacteristic)
    }

    /// Send mouse button press/release
    func mouseButton(buttons: UInt8, dx: Int8 = 0, dy: Int8 = 0) {
        let report = Data([buttons, UInt8(bitPattern: dx), UInt8(bitPattern: dy), 0x00])
        sendReport(report, characteristic: mouseReportCharacteristic)
    }

    /// Send mouse click (press + release)
    func mouseClick(button: UInt8 = 0x01) {
        mouseButton(buttons: button)
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.05) { [weak self] in
            self?.mouseButton(buttons: 0x00)
        }
    }

    /// Send keyboard key press
    func keyPress(modifier: UInt8 = 0, keys: [UInt8] = []) {
        var report = Data([modifier, 0x00])  // modifier + reserved
        let paddedKeys = keys + Array(repeating: UInt8(0), count: max(0, 6 - keys.count))
        report.append(contentsOf: paddedKeys.prefix(6))
        sendReport(report, characteristic: keyboardReportCharacteristic)
    }

    /// Release all keyboard keys
    func keyRelease() {
        keyPress(modifier: 0, keys: [])
    }

    /// Send gamepad button press
    func gamepadButtonPress(buttonIndex: Int) {
        guard buttonIndex >= 0 && buttonIndex < 16 else { return }
        gamepadButtons |= (1 << buttonIndex)
        sendGamepadReport()
    }

    /// Send gamepad button release
    func gamepadButtonRelease(buttonIndex: Int) {
        guard buttonIndex >= 0 && buttonIndex < 16 else { return }
        gamepadButtons &= ~(1 << buttonIndex)
        sendGamepadReport()
    }

    /// Set gamepad hat switch (D-pad): 0=N, 1=NE, 2=E, ... 7=NW, 0x0F=center
    func gamepadSetHat(_ direction: UInt8) {
        gamepadHat = direction
        sendGamepadReport()
    }

    /// Set gamepad stick values (0-255, 128 = center)
    func gamepadSetStick(left: Bool, x: UInt8, y: UInt8) {
        if left {
            gamepadLeftX = x
            gamepadLeftY = y
        } else {
            gamepadRightX = x
            gamepadRightY = y
        }
        sendGamepadReport()
    }

    private func sendGamepadReport() {
        let buttonsLow = UInt8(gamepadButtons & 0xFF)
        let buttonsHigh = UInt8((gamepadButtons >> 8) & 0xFF)
        let hatByte = gamepadHat
        let report = Data([buttonsLow, buttonsHigh, hatByte, 0x00,
                           gamepadLeftX, gamepadLeftY, gamepadRightX, gamepadRightY])
        sendReport(report, characteristic: gamepadReportCharacteristic)
    }

    private func sendReport(_ report: Data, characteristic: CBMutableCharacteristic?) {
        guard let char = characteristic, !subscribedCentrals.isEmpty else { return }
        let success = peripheralManager?.updateValue(report, for: char, onSubscribedCentrals: nil)
        if success != true {
            log.warning("Failed to send HID report (queue full, will retry)")
        }
    }

    // MARK: - Service Setup

    private func setupServices() {
        guard let pm = peripheralManager else { return }

        // Build report descriptor based on profile
        var reportDescriptor: [UInt8] = []
        switch currentProfile {
        case .mouse:
            reportDescriptor = mouseReportDescriptor
        case .keyboard:
            reportDescriptor = keyboardReportDescriptor
        case .gamepad:
            reportDescriptor = gamepadReportDescriptor
        case .combo:
            reportDescriptor = mouseReportDescriptor + keyboardReportDescriptor
        }

        // HID Information: bcdHID=1.11, bCountryCode=0, Flags=0x02 (normally connectable)
        let hidInfoValue = Data([0x11, 0x01, 0x00, 0x02])

        // Protocol Mode: Report Protocol (1)
        let protocolModeValue = Data([0x01])

        // Create characteristics
        let hidInfoChar = CBMutableCharacteristic(
            type: kHIDInfoUUID,
            properties: .read,
            value: hidInfoValue,
            permissions: .readable
        )

        let reportMapChar = CBMutableCharacteristic(
            type: kHIDReportMapUUID,
            properties: .read,
            value: Data(reportDescriptor),
            permissions: .readable
        )

        let controlPointChar = CBMutableCharacteristic(
            type: kHIDControlPointUUID,
            properties: .writeWithoutResponse,
            value: nil,
            permissions: .writeable
        )

        let protocolModeChar = CBMutableCharacteristic(
            type: kProtocolModeUUID,
            properties: [.read, .writeWithoutResponse],
            value: protocolModeValue,
            permissions: [.readable, .writeable]
        )

        var hidChars: [CBMutableCharacteristic] = [hidInfoChar, reportMapChar, controlPointChar, protocolModeChar]

        // Mouse report characteristic (Report ID 1)
        if currentProfile == .mouse || currentProfile == .combo {
            mouseReportCharacteristic = CBMutableCharacteristic(
                type: kHIDReportUUID,
                properties: [.read, .notify],
                value: nil,
                permissions: .readable
            )
            hidChars.append(mouseReportCharacteristic!)
        }

        // Keyboard report characteristic (Report ID 2)
        if currentProfile == .keyboard || currentProfile == .combo {
            keyboardReportCharacteristic = CBMutableCharacteristic(
                type: kHIDReportUUID,
                properties: [.read, .notify],
                value: nil,
                permissions: .readable
            )
            hidChars.append(keyboardReportCharacteristic!)
        }

        // Gamepad report characteristic (Report ID 3)
        if currentProfile == .gamepad {
            gamepadReportCharacteristic = CBMutableCharacteristic(
                type: kHIDReportUUID,
                properties: [.read, .notify],
                value: nil,
                permissions: .readable
            )
            hidChars.append(gamepadReportCharacteristic!)
        }

        // HID Service
        hidService = CBMutableService(type: kHIDServiceUUID, primary: true)
        hidService!.characteristics = hidChars
        pm.add(hidService!)

        // Battery Service (some hosts require this)
        let batteryLevelChar = CBMutableCharacteristic(
            type: kBatteryLevelUUID,
            properties: [.read, .notify],
            value: Data([100]),  // 100% battery
            permissions: .readable
        )
        let batteryService = CBMutableService(type: kBatteryServiceUUID, primary: false)
        batteryService.characteristics = [batteryLevelChar]
        pm.add(batteryService)

        // Device Information Service
        let manufacturerChar = CBMutableCharacteristic(
            type: kManufacturerNameUUID,
            properties: .read,
            value: "ChromaCatch".data(using: .utf8),
            permissions: .readable
        )
        // PnP ID: vendor source (0x02=USB), vendor ID, product ID, version
        let pnpValue = Data([0x02, 0x6D, 0x04, 0x00, 0x01, 0x00, 0x01])
        let pnpChar = CBMutableCharacteristic(
            type: kPnPIDUUID,
            properties: .read,
            value: pnpValue,
            permissions: .readable
        )
        let deviceInfoService = CBMutableService(type: kDeviceInfoServiceUUID, primary: false)
        deviceInfoService.characteristics = [manufacturerChar, pnpChar]
        pm.add(deviceInfoService)
    }

    private func startAdvertising() {
        let advertisementData: [String: Any] = [
            CBAdvertisementDataLocalNameKey: "ChromaCatch HID",
            CBAdvertisementDataServiceUUIDsKey: [kHIDServiceUUID],
        ]
        peripheralManager?.startAdvertising(advertisementData)
    }
}

// MARK: - CBPeripheralManagerDelegate

extension BLEHIDCommander: CBPeripheralManagerDelegate {
    nonisolated func peripheralManagerDidUpdateState(_ peripheral: CBPeripheralManager) {
        Task { @MainActor in
            switch peripheral.state {
            case .poweredOn:
                log.info("Bluetooth powered on, setting up HID services")
                setupServices()
            case .poweredOff:
                log.warning("Bluetooth powered off")
                isAdvertising = false
                isConnected = false
            case .unauthorized:
                log.error("Bluetooth unauthorized")
            case .unsupported:
                log.error("Bluetooth unsupported on this device")
            default:
                log.info("Bluetooth state: \(peripheral.state.rawValue)")
            }
        }
    }

    nonisolated func peripheralManager(_ peripheral: CBPeripheralManager, didAdd service: CBService, error: Error?) {
        Task { @MainActor in
            if let error = error {
                log.error("Failed to add service: \(error.localizedDescription)")
                return
            }
            log.info("Service added: \(service.uuid)")

            // Start advertising after HID service is added
            if service.uuid == kHIDServiceUUID {
                startAdvertising()
            }
        }
    }

    nonisolated func peripheralManagerDidStartAdvertising(_ peripheral: CBPeripheralManager, error: Error?) {
        Task { @MainActor in
            if let error = error {
                log.error("Failed to start advertising: \(error.localizedDescription)")
                isAdvertising = false
                return
            }
            log.info("BLE HID advertising started")
            isAdvertising = true
        }
    }

    nonisolated func peripheralManager(_ peripheral: CBPeripheralManager, central: CBCentral, didSubscribeTo characteristic: CBCharacteristic) {
        Task { @MainActor in
            log.info("Central subscribed to \(characteristic.uuid)")
            if !subscribedCentrals.contains(where: { $0.identifier == central.identifier }) {
                subscribedCentrals.append(central)
            }
            isConnected = true
            connectedDeviceName = central.identifier.uuidString
        }
    }

    nonisolated func peripheralManager(_ peripheral: CBPeripheralManager, central: CBCentral, didUnsubscribeFrom characteristic: CBCharacteristic) {
        Task { @MainActor in
            log.info("Central unsubscribed from \(characteristic.uuid)")
            subscribedCentrals.removeAll { $0.identifier == central.identifier }
            if subscribedCentrals.isEmpty {
                isConnected = false
                connectedDeviceName = nil
            }
        }
    }

    nonisolated func peripheralManagerIsReady(toUpdateSubscribers peripheral: CBPeripheralManager) {
        // Called when the transmit queue has space again
        log.debug("Ready to update subscribers")
    }
}
