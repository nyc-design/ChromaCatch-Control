import CoreLocation
import Foundation

/// Lightweight CLLocationManager wrapper that reads what iOS *actually* reports
/// as the device location, for verifying GPS spoofing accuracy.
///
/// Uses `requestLocation()` on a timer (not `startUpdatingLocation()`) with
/// `kCLLocationAccuracyHundredMeters` to avoid interfering with the iTools
/// dongle's spoofed location feed.
class LocationMonitor: NSObject, ObservableObject, CLLocationManagerDelegate {
    private let manager = CLLocationManager()
    private var pollTimer: Timer?
    private var consecutiveDriftCount = 0
    private let log: (String) -> Void

    /// What iOS actually reports as the device location.
    @Published var iosReportedLat: Double = 0
    @Published var iosReportedLon: Double = 0

    /// True when iOS-reported location is within threshold of spoofed target.
    @Published var isAccurate: Bool = false

    /// Haversine distance in meters between iOS-reported and target location.
    @Published var driftMeters: Double = 0

    /// The spoofed target coordinates (set by AppCoordinator when dongle coords change).
    var targetLat: Double = 0
    var targetLon: Double = 0

    /// Distance threshold: under this = accurate (green), over = drifted (red).
    let accuracyThresholdMeters: Double = 100

    /// How many consecutive drifted updates before triggering auto-recovery.
    private let driftRecoveryThreshold = 3

    init(log: @escaping (String) -> Void = { NSLog("[LocationMonitor] %@", $0) }) {
        self.log = log
        super.init()
        manager.delegate = self
        manager.desiredAccuracy = kCLLocationAccuracyHundredMeters
    }

    /// Request when-in-use authorization and start polling every 5 seconds.
    func startMonitoring() {
        let status = manager.authorizationStatus
        if status == .notDetermined {
            manager.requestWhenInUseAuthorization()
        }

        guard pollTimer == nil else { return }
        log("Starting location polling (5s interval)")

        // Request immediately, then every 5 seconds
        requestOnce()
        pollTimer = Timer.scheduledTimer(withTimeInterval: 5.0, repeats: true) { [weak self] _ in
            self?.requestOnce()
        }
    }

    /// Stop polling.
    func stopMonitoring() {
        pollTimer?.invalidate()
        pollTimer = nil
        log("Location polling stopped")
    }

    /// Force iOS to re-acquire location from the dongle by stopping and
    /// restarting the location manager. This is the programmatic equivalent
    /// of toggling Location Services.
    func forceRefresh() {
        log("Force-refreshing location (stop + restart CLLocationManager)")
        manager.stopUpdatingLocation()
        consecutiveDriftCount = 0
        // Brief delay before re-requesting
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { [weak self] in
            self?.requestOnce()
        }
    }

    // MARK: - Private

    private func requestOnce() {
        let status = manager.authorizationStatus
        guard status == .authorizedWhenInUse || status == .authorizedAlways else {
            return
        }
        manager.requestLocation()
    }

    // MARK: - CLLocationManagerDelegate

    func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        guard let location = locations.last else { return }

        DispatchQueue.main.async { [self] in
            iosReportedLat = location.coordinate.latitude
            iosReportedLon = location.coordinate.longitude

            // Only compute drift if we have a target set
            if targetLat != 0 || targetLon != 0 {
                driftMeters = haversine(
                    lat1: iosReportedLat, lon1: iosReportedLon,
                    lat2: targetLat, lon2: targetLon
                )
                isAccurate = driftMeters <= accuracyThresholdMeters

                if isAccurate {
                    consecutiveDriftCount = 0
                } else {
                    consecutiveDriftCount += 1
                    log("Drift detected: \(String(format: "%.0f", driftMeters))m (consecutive: \(consecutiveDriftCount))")

                    if consecutiveDriftCount >= driftRecoveryThreshold {
                        log("Auto-recovery: \(consecutiveDriftCount) consecutive drifts, forcing refresh")
                        forceRefresh()
                    }
                }
            } else {
                // No target set yet — can't compute drift
                driftMeters = 0
                isAccurate = false
            }
        }
    }

    func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) {
        // Suppress kCLErrorLocationUnknown silently — it's transient and expected
        if let clError = error as? CLError, clError.code == .locationUnknown {
            return
        }
        log("Location error: \(error.localizedDescription)")
    }

    func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        let status = manager.authorizationStatus
        log("Location auth changed: \(status.rawValue)")
        if status == .authorizedWhenInUse || status == .authorizedAlways {
            requestOnce()
        }
    }

    // MARK: - Haversine

    /// Returns distance in meters between two WGS84 coordinates.
    private func haversine(lat1: Double, lon1: Double, lat2: Double, lon2: Double) -> Double {
        let R = 6_371_000.0 // Earth radius in meters
        let dLat = (lat2 - lat1) * .pi / 180.0
        let dLon = (lon2 - lon1) * .pi / 180.0
        let a = sin(dLat / 2) * sin(dLat / 2) +
                cos(lat1 * .pi / 180.0) * cos(lat2 * .pi / 180.0) *
                sin(dLon / 2) * sin(dLon / 2)
        let c = 2 * atan2(sqrt(a), sqrt(1 - a))
        return R * c
    }
}
