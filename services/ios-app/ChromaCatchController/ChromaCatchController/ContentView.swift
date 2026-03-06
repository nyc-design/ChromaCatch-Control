import SwiftUI

struct ContentView: View {
    @EnvironmentObject var coordinator: AppCoordinator

    var body: some View {
        TabView {
            DashboardTab()
                .tabItem {
                    Label("Dashboard", systemImage: "gauge.with.dots.needle.67percent")
                }

            VideoTab()
                .tabItem {
                    Label("Video", systemImage: "video.fill")
                }

            InputTab()
                .tabItem {
                    Label("Input", systemImage: "gamecontroller.fill")
                }

            SettingsTab()
                .tabItem {
                    Label("Settings", systemImage: "gearshape.fill")
                }
        }
        .tint(.green)
    }
}
