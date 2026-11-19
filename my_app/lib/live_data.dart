// Data from GET /api/esp32/live/<device_id>: live per-axis vibration,
// temperature, the overall verdict against the healthy pump and the timed test.

double? _toDouble(dynamic value) {
  if (value is num) return value.toDouble();
  return double.tryParse(value?.toString() ?? '');
}

Map<String, dynamic> _map(dynamic value) =>
    value is Map ? Map<String, dynamic>.from(value) : <String, dynamic>{};

List<Map<String, dynamic>> _list(dynamic value) => value is List
    ? value
          .whereType<Map>()
          .map((item) => Map<String, dynamic>.from(item))
          .toList()
    : const [];

/// How long a pump counts as online after its last data when the app works
/// it out itself, and how long the app keeps showing its last live data while
/// the server is slow to answer: the backend's 35 s plus the 5 s between
/// updates of the device list.
const Duration kOnlineWindow = Duration(seconds: 40);

/// Age of a backend time (ISO 8601); without a time zone it is UTC, as on Render.
Duration? ageOfServerTime(String? value) {
  if (value == null || value.isEmpty) return null;
  final hasTimezone = RegExp(
    r'(Z|[+-]\d{2}:?\d{2})$',
    caseSensitive: false,
  ).hasMatch(value);
  final time = DateTime.tryParse(hasTimezone ? value : '${value}Z');
  if (time == null) return null;
  return DateTime.now().toUtc().difference(time.toUtc());
}

/// Whether a pump's ESP32 is sending data right now (e.g. connected to the
/// phone's hotspot), from a device-list or status entry: the backend's
/// "online" when it reports one, otherwise from its last update.
bool isDeviceOnline(Map<String, dynamic> device) {
  final online = device['online'];
  if (online is bool) return online;
  final age = ageOfServerTime(device['last_update']?.toString());
  return age != null &&
      age >= const Duration(seconds: -10) && // phone clock slightly behind
      age <= kOnlineWindow;
}

/// One axis of the vibration bars.
class AxisLevel {
  final String axis;
  final String pumpPart;
  final double? rmsG;
  final double? healthyRmsG;
  final double? healthyLimitG;
  final double? timesHealthy;

  /// healthy, warning, faulty or off.
  final String status;

  const AxisLevel({
    required this.axis,
    required this.pumpPart,
    required this.rmsG,
    required this.healthyRmsG,
    required this.healthyLimitG,
    required this.timesHealthy,
    required this.status,
  });

  factory AxisLevel.fromJson(Map<String, dynamic> json) => AxisLevel(
    axis: json['axis']?.toString() ?? '?',
    pumpPart: json['pump_part']?.toString() ?? '',
    rmsG: _toDouble(json['rms_g']),
    healthyRmsG: _toDouble(json['healthy_rms_g']),
    healthyLimitG: _toDouble(json['healthy_limit_g']),
    timesHealthy: _toDouble(json['times_healthy']),
    status: json['status']?.toString() ?? 'off',
  );
}

class PumpProblem {
  final String title;
  final String severity;
  final String evidence;
  final String check;

  const PumpProblem({
    required this.title,
    required this.severity,
    required this.evidence,
    required this.check,
  });

  factory PumpProblem.fromJson(Map<String, dynamic> json) => PumpProblem(
    title: json['title']?.toString() ?? '',
    severity: json['severity']?.toString() ?? 'medium',
    evidence: json['evidence']?.toString() ?? '',
    check: json['check']?.toString() ?? '',
  );
}

/// Overall judgement of a stretch of running data against the healthy pump.
class Assessment {
  final int windows;
  final double runningSeconds;
  final List<AxisLevel> axes;
  final double? totalRmsG;
  final double? healthyTotalRmsG;
  final double? deviationPercent;
  final double? anomalyWindowPercent;
  final double? modelUnusualPercent;
  final Map<String, double> partFlagsPercent;
  final double? temperatureMaxC;
  final double? temperatureMeanC;
  final double? healthyTemperatureMeanC;
  final double? healthyTemperatureMaxC;
  final double? riskScore;

  /// Risk (0-100) from each check: vibration_deviation, unusual_windows, temperature.
  final Map<String, double> riskParts;
  final double? healthPercent;

  /// HEALTHY, MONITOR, WARNING, FAULTY or NO_DATA.
  final String verdict;
  final String verdictText;
  final List<PumpProblem> problems;

  const Assessment({
    required this.windows,
    required this.runningSeconds,
    required this.axes,
    required this.totalRmsG,
    required this.healthyTotalRmsG,
    required this.deviationPercent,
    required this.anomalyWindowPercent,
    required this.modelUnusualPercent,
    required this.partFlagsPercent,
    required this.temperatureMaxC,
    required this.temperatureMeanC,
    required this.healthyTemperatureMeanC,
    required this.healthyTemperatureMaxC,
    required this.riskScore,
    this.riskParts = const {},
    required this.healthPercent,
    required this.verdict,
    required this.verdictText,
    required this.problems,
  });

  bool get hasData => verdict != 'NO_DATA';

  /// How far the pump is toward faulty: 0% = healthy, 100% = faulty
  /// (100 - machine health, the same as the risk score).
  double? get faultyPercent =>
      healthPercent == null ? null : (100 - healthPercent!).clamp(0.0, 100.0);

  /// No running data: the result is "No data", the healthy values still show.
  factory Assessment.empty() => Assessment(
    windows: 0,
    runningSeconds: 0,
    axes: kDefaultAxes,
    totalRmsG: null,
    healthyTotalRmsG: kDefaultHealthyReference.totalRmsG,
    deviationPercent: null,
    anomalyWindowPercent: null,
    modelUnusualPercent: null,
    partFlagsPercent: const {},
    temperatureMaxC: null,
    temperatureMeanC: null,
    healthyTemperatureMeanC: kDefaultHealthyReference.temperatureMeanC,
    healthyTemperatureMaxC: kDefaultHealthyReference.temperatureMaxC,
    riskScore: null,
    healthPercent: null,
    verdict: 'NO_DATA',
    verdictText: 'No running data yet',
    problems: const [],
  );

  factory Assessment.fromJson(Map<String, dynamic> json) => Assessment(
    windows: (_toDouble(json['windows']) ?? 0).toInt(),
    runningSeconds: _toDouble(json['running_seconds']) ?? 0,
    axes: _list(json['axes']).map(AxisLevel.fromJson).toList(),
    totalRmsG: _toDouble(json['total_rms_g']),
    healthyTotalRmsG: _toDouble(json['healthy_total_rms_g']),
    deviationPercent: _toDouble(json['deviation_percent']),
    anomalyWindowPercent: _toDouble(json['anomaly_window_percent']),
    modelUnusualPercent: _toDouble(json['model_unusual_percent']),
    partFlagsPercent: _map(
      json['part_flags_percent'],
    ).map((key, value) => MapEntry(key, _toDouble(value) ?? 0)),
    temperatureMaxC: _toDouble(json['temperature_max_c']),
    temperatureMeanC: _toDouble(json['temperature_mean_c']),
    healthyTemperatureMeanC: _toDouble(json['healthy_temperature_mean_c']),
    healthyTemperatureMaxC: _toDouble(json['healthy_temperature_max_c']),
    riskScore: _toDouble(json['risk_score']),
    riskParts: _map(
      json['risk_parts'],
    ).map((key, value) => MapEntry(key, _toDouble(value) ?? 0)),
    healthPercent: _toDouble(json['health_percent']),
    verdict: json['verdict']?.toString() ?? 'NO_DATA',
    verdictText: json['verdict_text']?.toString() ?? '',
    problems: _list(json['problems']).map(PumpProblem.fromJson).toList(),
  );
}

/// A timed test started from the app.
class PumpTest {
  /// none, running, finished or stopped.
  final String state;
  final double minutes;
  final DateTime? startedAt;
  final DateTime? endsAt;
  final double elapsedSeconds;
  final double remainingSeconds;
  final String? recordingFile;
  final String? summaryFile;
  final Assessment? assessment;

  const PumpTest({
    required this.state,
    required this.minutes,
    required this.startedAt,
    required this.endsAt,
    required this.elapsedSeconds,
    required this.remainingSeconds,
    required this.recordingFile,
    required this.summaryFile,
    required this.assessment,
  });

  bool get isRunning => state == 'running';
  bool get exists => state != 'none';

  factory PumpTest.fromJson(Map<String, dynamic> json) => PumpTest(
    state: json['state']?.toString() ?? 'none',
    minutes: _toDouble(json['minutes']) ?? 0,
    startedAt: DateTime.tryParse(json['started_at']?.toString() ?? ''),
    endsAt: DateTime.tryParse(json['ends_at']?.toString() ?? ''),
    elapsedSeconds: _toDouble(json['elapsed_seconds']) ?? 0,
    remainingSeconds: _toDouble(json['remaining_seconds']) ?? 0,
    recordingFile: json['recording_file']?.toString(),
    summaryFile: json['summary_file']?.toString(),
    assessment: json['assessment'] is Map
        ? Assessment.fromJson(_map(json['assessment']))
        : null,
  );

  static const none = PumpTest(
    state: 'none',
    minutes: 0,
    startedAt: null,
    endsAt: null,
    elapsedSeconds: 0,
    remainingSeconds: 0,
    recordingFile: null,
    summaryFile: null,
    assessment: null,
  );
}

/// The healthy pump's per-axis values (data/healthy_reference/
/// ESP32-002_healthy_reference.json, limits from models/pump_part_thresholds.json),
/// built in so the bars show the healthy levels even before the server answers.
/// X and Y were set by hand to 0.50 g and 0.15 g (limits moved with them).
const List<AxisLevel> kDefaultAxes = [
  AxisLevel(
    axis: 'X',
    pumpPart: 'Lateral Bearing/Shaft Alignment',
    rmsG: null,
    healthyRmsG: 0.50,
    healthyLimitG: 0.7424,
    timesHealthy: null,
    status: 'off',
  ),
  AxisLevel(
    axis: 'Y',
    pumpPart: 'Radial Bearing/Motor Housing',
    rmsG: null,
    healthyRmsG: 0.15,
    healthyLimitG: 0.3053,
    timesHealthy: null,
    status: 'off',
  ),
  AxisLevel(
    axis: 'Z',
    pumpPart: 'Impeller/Pump Head/Axial Load',
    rmsG: null,
    healthyRmsG: 0.6654,
    healthyLimitG: 1.0246,
    timesHealthy: null,
    status: 'off',
  ),
];

const kDefaultHealthyReference = HealthyReference(
  available: true,
  totalRmsG: 0.8524,
  axisRmsG: {'x': 0.50, 'y': 0.15, 'z': 0.6654},
  axisLimitG: {'x': 0.7424, 'y': 0.3053, 'z': 1.0246},
  temperatureMeanC: 55.37,
  temperatureMaxC: 65.25,
  recordedFrom: null,
);

/// Server axes, with the built-in healthy values filled in where missing.
List<AxisLevel> axesWithDefaults(List<AxisLevel> axes) {
  if (axes.isEmpty) return kDefaultAxes;
  return [
    for (final axis in axes)
      () {
        final fallback = kDefaultAxes.firstWhere(
          (d) => d.axis == axis.axis,
          orElse: () => axis,
        );
        return AxisLevel(
          axis: axis.axis,
          pumpPart: axis.pumpPart.isEmpty ? fallback.pumpPart : axis.pumpPart,
          rmsG: axis.rmsG,
          healthyRmsG: axis.healthyRmsG ?? fallback.healthyRmsG,
          healthyLimitG: axis.healthyLimitG ?? fallback.healthyLimitG,
          timesHealthy: axis.timesHealthy,
          status: axis.status,
        );
      }(),
  ];
}

/// The healthy pump every pump is compared with.
class HealthyReference {
  final bool available;
  final double? totalRmsG;
  final Map<String, double> axisRmsG;
  final Map<String, double> axisLimitG;
  final double? temperatureMeanC;
  final double? temperatureMaxC;
  final DateTime? recordedFrom;

  const HealthyReference({
    required this.available,
    required this.totalRmsG,
    required this.axisRmsG,
    required this.axisLimitG,
    required this.temperatureMeanC,
    required this.temperatureMaxC,
    required this.recordedFrom,
  });

  factory HealthyReference.fromJson(Map<String, dynamic> json) =>
      HealthyReference(
        available: json['available'] == true,
        totalRmsG: _toDouble(json['total_rms_g']),
        axisRmsG: _map(
          json['axis_rms_g'],
        ).map((key, value) => MapEntry(key, _toDouble(value) ?? 0)),
        axisLimitG: _map(
          json['axis_limit_g'],
        ).map((key, value) => MapEntry(key, _toDouble(value) ?? 0)),
        temperatureMeanC: _toDouble(json['temperature_mean_c']),
        temperatureMaxC: _toDouble(json['temperature_max_c']),
        recordedFrom: DateTime.tryParse(
          json['recorded_from']?.toString() ?? '',
        ),
      );
}

class LiveData {
  final String deviceId;
  final String deviceName;
  final String location;
  final bool online;
  final double? secondsSinceUpdate;
  final bool pumpRunning;
  final double? temperatureC;
  final double? sensorVibrationRmsG;
  final List<AxisLevel> axes;
  final Assessment liveAssessment;
  final HealthyReference healthyReference;
  final PumpTest test;
  final bool recording;
  final String? recordingFile;

  /// Nothing received (no pump selected, ESP32 not connected or server not
  /// reachable): every section still shows, with empty values.
  factory LiveData.empty({
    required String deviceId,
    required String deviceName,
  }) => LiveData(
    deviceId: deviceId,
    deviceName: deviceName,
    location: '—',
    online: false,
    secondsSinceUpdate: null,
    pumpRunning: false,
    temperatureC: null,
    sensorVibrationRmsG: null,
    axes: kDefaultAxes,
    liveAssessment: Assessment.empty(),
    healthyReference: kDefaultHealthyReference,
    test: PumpTest.none,
    recording: false,
    recordingFile: null,
  );

  const LiveData({
    required this.deviceId,
    required this.deviceName,
    required this.location,
    required this.online,
    required this.secondsSinceUpdate,
    required this.pumpRunning,
    required this.temperatureC,
    required this.sensorVibrationRmsG,
    required this.axes,
    required this.liveAssessment,
    required this.healthyReference,
    required this.test,
    required this.recording,
    required this.recordingFile,
  });

  factory LiveData.fromJson(Map<String, dynamic> json) {
    final recording = _map(json['recording']);
    return LiveData(
      deviceId: json['device_id']?.toString() ?? '',
      deviceName: json['device_name']?.toString() ?? '',
      location: json['location']?.toString() ?? 'Unknown',
      online: json['online'] == true,
      secondsSinceUpdate: _toDouble(json['seconds_since_update']),
      pumpRunning: json['pump_running'] == true,
      temperatureC: _toDouble(json['latest_temperature_c']),
      sensorVibrationRmsG: _toDouble(json['sensor_vibration_rms_g']),
      axes: axesWithDefaults(
        _list(json['axes']).map(AxisLevel.fromJson).toList(),
      ),
      liveAssessment: Assessment.fromJson(_map(json['live_assessment'])),
      healthyReference: () {
        final reference = HealthyReference.fromJson(
          _map(json['healthy_reference']),
        );
        return reference.available ? reference : kDefaultHealthyReference;
      }(),
      test: json['test'] is Map
          ? PumpTest.fromJson(_map(json['test']))
          : PumpTest.none,
      recording: recording['recording'] == true,
      recordingFile: recording['file']?.toString(),
    );
  }

  /// The same data with [test], e.g. the server's answer to starting a test.
  LiveData withTest(PumpTest test) => LiveData(
    deviceId: deviceId,
    deviceName: deviceName,
    location: location,
    online: online,
    secondsSinceUpdate: secondsSinceUpdate,
    pumpRunning: pumpRunning,
    temperatureC: temperatureC,
    sensorVibrationRmsG: sensorVibrationRmsG,
    axes: axes,
    liveAssessment: liveAssessment,
    healthyReference: healthyReference,
    test: test,
    recording: recording,
    recordingFile: recordingFile,
  );

  /// From `GET /api/esp32/status/<device_id>`, for a backend deployed before
  /// the live view: whether the ESP32 is online, the temperature and whether
  /// the pump runs. The per-axis levels and the verdict need the live view,
  /// so they show "No data".
  factory LiveData.fromStatusJson(Map<String, dynamic> json) {
    final latest = _map(json['latest_sample']);
    final deviceId = json['device_id']?.toString() ?? '';
    final age = ageOfServerTime(json['last_update']?.toString());
    return LiveData(
      deviceId: deviceId,
      deviceName: json['device_name']?.toString() ?? deviceId,
      location: json['location']?.toString() ?? 'Unknown',
      online: isDeviceOnline(json),
      secondsSinceUpdate: age == null ? null : age.inMilliseconds / 1000.0,
      pumpRunning: latest['pump_running'] == true,
      temperatureC: _toDouble(json['latest_temperature_c']),
      sensorVibrationRmsG: _toDouble(latest['vibration_rms_g']),
      axes: kDefaultAxes,
      liveAssessment: Assessment.empty(),
      healthyReference: kDefaultHealthyReference,
      test: PumpTest.none,
      recording: false,
      recordingFile: null,
    );
  }
}
