import CoreLocation
import Foundation

/// Uses Core Location significant change monitoring to keep the app alive when backgrounded.
/// Uses significantLocationChanges (not startUpdatingLocation) to avoid competing with
/// the iTools dongle's spoofed GPS coordinates.
class LocationKeepAlive: NSObject, CLLocationManagerDelegate {
    private let locationManager = CLLocationManager()
    private let log: (String) -> Void
    private var isStarted = false

    init(log: @escaping (String) -> Void = { print("Location: \($0)") }) {
        self.log = log
        super.init()
        locationManager.delegate = self
    }

    func startBackgroundUpdates() {
        isStarted = true
        let status = locationManager.authorizationStatus
        if status == .notDetermined {
            locationManager.requestWhenInUseAuthorization()
            return // Will continue in delegate callback
        }
        if status == .authorizedWhenInUse {
            locationManager.requestAlwaysAuthorization()
            return // Will continue in delegate callback
        }
        if status == .authorizedAlways {
            beginMonitoring()
        }
    }

    private func beginMonitoring() {
        locationManager.allowsBackgroundLocationUpdates = true
        locationManager.showsBackgroundLocationIndicator = true
        // Use significant change monitoring — keeps app alive in background
        // without actively polling GPS (which would override dongle's spoofed location)
        locationManager.startMonitoringSignificantLocationChanges()
        log("Background keepalive started (significant changes)")
    }

    func stop() {
        isStarted = false
        locationManager.stopMonitoringSignificantLocationChanges()
        log("Location monitoring stopped")
    }

    // MARK: - CLLocationManagerDelegate

    func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        // Significant change events keep the app alive — no action needed
    }

    func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        let status = manager.authorizationStatus
        switch status {
        case .authorizedAlways:
            log("Authorization: Always")
            if isStarted { beginMonitoring() }
        case .authorizedWhenInUse:
            log("Authorization: When In Use — requesting Always for background")
            if isStarted { manager.requestAlwaysAuthorization() }
        case .denied, .restricted:
            log("Authorization denied — enable Location in Settings > Privacy > Location Services")
        case .notDetermined:
            break
        @unknown default:
            break
        }
    }

    func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) {
        log("Location error: \(error.localizedDescription)")
    }
}
