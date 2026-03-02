import CoreLocation
import Foundation

/// Monitors the iOS-reported device location and compares it against the target
/// spoofed coordinates to detect GPS drift. Provides a red/green accuracy indicator
/// and auto-recovery via CLLocationManager restart when drift persists.
class LocationMonitor: NSObject, ObservableObject, CLLocationManagerDelegate {
    @Published var iosReportedLat: Double = 0
    @Published var iosReportedLon: Double = 0
    @Published var isAccurate: Bool = false
    @Published var driftMeters: Double = 0

    var targetLat: Double = 0 {
        didSet { updateAccuracy() }
    }
    var targetLon: Double = 0 {
        didSet { updateAccuracy() }
    }

    let accuracyThresholdMeters: Double = 100

    private let locationManager = CLLocationManager()
    private var consecutiveDriftCount = 0
    private let driftRecoveryThreshold = 3
    private var isMonitoring = false
    private let log: (String) -> Void

    init(log: @escaping (String) -> Void = { _ in }) {
        self.log = log
        super.init()
        locationManager.delegate = self
        locationManager.desiredAccuracy = kCLLocationAccuracyBest
        locationManager.distanceFilter = kCLDistanceFilterNone // Continuous updates
    }

    func startMonitoring() {
        guard !isMonitoring else { return }
        isMonitoring = true
        locationManager.requestWhenInUseAuthorization()
        locationManager.startUpdatingLocation()
        log("GPS monitor started")
    }

    func stopMonitoring() {
        guard isMonitoring else { return }
        isMonitoring = false
        locationManager.stopUpdatingLocation()
        log("GPS monitor stopped")
    }

    /// Force iOS to re-acquire location from dongle by restarting CLLocationManager.
    /// This is the programmatic equivalent of toggling Location Services.
    func forceRefresh() {
        guard isMonitoring else { return }
        log("GPS force refresh — restarting CLLocationManager")
        locationManager.stopUpdatingLocation()
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { [weak self] in
            self?.locationManager.startUpdatingLocation()
        }
        consecutiveDriftCount = 0
    }

    // MARK: - CLLocationManagerDelegate

    func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        guard let location = locations.last else { return }

        DispatchQueue.main.async { [weak self] in
            guard let self = self else { return }
            self.iosReportedLat = location.coordinate.latitude
            self.iosReportedLon = location.coordinate.longitude
            self.updateAccuracy()
        }
    }

    func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) {
        // kCLErrorLocationUnknown (error 0) is transient — fires during startup before lock
        let clError = error as? CLError
        if clError?.code == .locationUnknown { return }
        log("GPS error: \(error.localizedDescription)")
    }

    func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        switch manager.authorizationStatus {
        case .authorizedWhenInUse, .authorizedAlways:
            if isMonitoring {
                manager.startUpdatingLocation()
            }
        case .denied, .restricted:
            log("GPS permission denied")
        default:
            break
        }
    }

    // MARK: - Private

    private func updateAccuracy() {
        let hasTarget = targetLat != 0 || targetLon != 0
        let hasReport = iosReportedLat != 0 || iosReportedLon != 0

        guard hasTarget && hasReport else {
            isAccurate = false
            driftMeters = 0
            return
        }

        driftMeters = haversine(
            lat1: targetLat, lon1: targetLon,
            lat2: iosReportedLat, lon2: iosReportedLon
        )
        isAccurate = driftMeters <= accuracyThresholdMeters

        if !isAccurate {
            consecutiveDriftCount += 1
            if consecutiveDriftCount >= driftRecoveryThreshold {
                log("GPS drift detected (\(Int(driftMeters))m) for \(consecutiveDriftCount) updates — auto-refreshing")
                forceRefresh()
            }
        } else {
            consecutiveDriftCount = 0
        }
    }

    /// Haversine formula — returns distance in meters between two coordinates.
    private func haversine(lat1: Double, lon1: Double, lat2: Double, lon2: Double) -> Double {
        let R = 6_371_000.0 // Earth radius in meters
        let dLat = (lat2 - lat1) * .pi / 180
        let dLon = (lon2 - lon1) * .pi / 180
        let a = sin(dLat / 2) * sin(dLat / 2) +
            cos(lat1 * .pi / 180) * cos(lat2 * .pi / 180) *
            sin(dLon / 2) * sin(dLon / 2)
        let c = 2 * atan2(sqrt(a), sqrt(1 - a))
        return R * c
    }
}
