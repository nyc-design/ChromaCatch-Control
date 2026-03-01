import CoreLocation
import Foundation

/// Uses Core Location background updates to keep the app alive when backgrounded.
/// This is the most reliable background keepalive mechanism on iOS.
class LocationKeepAlive: NSObject, CLLocationManagerDelegate {
    private let locationManager = CLLocationManager()
    private let log: (String) -> Void

    init(log: @escaping (String) -> Void = { print("Location: \($0)") }) {
        self.log = log
        super.init()
        locationManager.delegate = self
    }

    func startBackgroundUpdates() {
        // iOS requires WhenInUse first, then escalate to Always
        let status = locationManager.authorizationStatus
        if status == .notDetermined {
            locationManager.requestWhenInUseAuthorization()
            return // Will continue in delegate callback
        }
        beginUpdates()
    }

    private func beginUpdates() {
        let status = locationManager.authorizationStatus
        if status == .authorizedWhenInUse {
            // Escalate to Always for background support
            locationManager.requestAlwaysAuthorization()
        }
        locationManager.allowsBackgroundLocationUpdates = true
        locationManager.showsBackgroundLocationIndicator = true
        locationManager.pausesLocationUpdatesAutomatically = false
        locationManager.desiredAccuracy = kCLLocationAccuracyReduced
        locationManager.distanceFilter = kCLDistanceFilterNone
        locationManager.startUpdatingLocation()
        log("Background location updates started (auth: \(status.rawValue))")
    }

    func stop() {
        locationManager.stopUpdatingLocation()
        log("Location updates stopped")
    }

    // MARK: - CLLocationManagerDelegate

    func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        // Location updates keep the app alive — no action needed
    }

    func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        switch manager.authorizationStatus {
        case .authorizedAlways:
            log("Authorization: Always")
        case .authorizedWhenInUse:
            log("Authorization: When In Use — request Always for background support")
        case .denied, .restricted:
            log("Authorization denied/restricted — background mode will not work")
        case .notDetermined:
            log("Authorization not determined — requesting...")
        @unknown default:
            break
        }
    }

    func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) {
        log("Error: \(error.localizedDescription)")
    }
}
