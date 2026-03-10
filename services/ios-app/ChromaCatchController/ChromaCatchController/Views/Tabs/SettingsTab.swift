import SwiftUI

struct SettingsTab: View {
    @EnvironmentObject var coordinator: AppCoordinator

    var body: some View {
        NavigationStack {
            Form {
                Section("Backend") {
                    TextField("Backend Control WS URL", text: $coordinator.backendURL)
                        .font(.system(.caption, design: .monospaced))
                        .autocapitalization(.none)
                        .disableAutocorrection(true)
                    TextField("API Key", text: $coordinator.apiKey)
                        .font(.system(.caption, design: .monospaced))
                        .autocapitalization(.none)
                        .disableAutocorrection(true)
                }

                Section("ESP32") {
                    HStack {
                        TextField("Host", text: $coordinator.esp32Host)
                            .font(.system(.caption, design: .monospaced))
                            .autocapitalization(.none)
                        TextField("Port", text: $coordinator.esp32Port)
                            .font(.system(.caption, design: .monospaced))
                            .frame(width: 60)
                            .keyboardType(.numberPad)
                        TextField("WS", text: $coordinator.esp32WSPort)
                            .font(.system(.caption, design: .monospaced))
                            .frame(width: 60)
                            .keyboardType(.numberPad)
                    }
                }

                Section("Client") {
                    TextField("Client ID", text: $coordinator.clientId)
                        .font(.system(.caption, design: .monospaced))
                        .autocapitalization(.none)
                        .disableAutocorrection(true)
                }

                Section {
                    NavigationLink {
                        LogView()
                    } label: {
                        HStack {
                            Image(systemName: "doc.text")
                            Text("Logs")
                            Spacer()
                            Text("\(coordinator.logs.count)")
                                .font(.system(.caption, design: .monospaced))
                                .foregroundColor(.secondary)
                        }
                    }
                }
            }
            .navigationTitle("Settings")
        }
    }
}
