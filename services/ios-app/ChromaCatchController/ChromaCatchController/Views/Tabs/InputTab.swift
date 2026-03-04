import SwiftUI

struct InputTab: View {
    @EnvironmentObject var coordinator: AppCoordinator

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 16) {
                    // ESP32 Mode
                    CardView("ESP32 Mode", icon: "cpu") {
                        if coordinator.esp32Mode == ESP32Mode.unknown {
                            Text("Not connected \u{2014} tap Refresh to query ESP32 mode.")
                                .font(.caption)
                                .foregroundColor(.secondary)
                        } else {
                            InfoRow(label: "Input", value: coordinator.esp32Mode.inputMode)
                            InfoRow(label: "Output", value: coordinator.esp32Mode.outputDelivery)
                            InfoRow(label: "Mode", value: coordinator.esp32Mode.outputMode)
                        }

                        Button {
                            Task { await coordinator.queryESP32Mode() }
                        } label: {
                            HStack {
                                Image(systemName: "arrow.clockwise")
                                Text("Refresh Mode")
                            }
                            .frame(maxWidth: .infinity)
                        }
                        .buttonStyle(.bordered)
                    }

                    // BLE HID Commander
                    CardView("BLE HID Commander", icon: "gamecontroller.fill") {
                        Text("Route commands directly via Bluetooth HID instead of ESP32. Works with Switch, 3DS, PC.")
                            .font(.caption)
                            .foregroundColor(.secondary)

                        Toggle("Use BLE HID", isOn: Binding(
                            get: { coordinator.useBLEHID },
                            set: { newValue in coordinator.setBLEHIDEnabled(newValue) }
                        ))

                        if coordinator.useBLEHID {
                            ConnectionRow(
                                label: "Advertising", icon: "antenna.radiowaves.left.and.right",
                                isConnected: coordinator.bleHIDCommander.isAdvertising,
                                activeColor: .mint
                            )
                            ConnectionRow(
                                label: "Connected", icon: "link",
                                isConnected: coordinator.bleHIDCommander.isConnected,
                                detail: coordinator.bleHIDCommander.connectedDeviceName,
                                activeColor: .mint
                            )
                        }
                    }

                    // Command Routing
                    CardView("Command Routing", icon: "arrow.triangle.branch") {
                        InfoRow(label: "Target", value: coordinator.useBLEHID ? "BLE HID" : "ESP32")
                        InfoRow(label: "Commands Sent", value: "\(coordinator.commandsSent)")
                        InfoRow(label: "Commands Acked", value: "\(coordinator.commandsAcked)")
                        if coordinator.esp32Mode != ESP32Mode.unknown {
                            InfoRow(label: "ESP32 Mode", value: coordinator.esp32Mode.outputMode)
                        }
                    }
                }
                .padding()
            }
            .background(Color(.systemGroupedBackground))
            .navigationTitle("Input")
        }
    }
}
