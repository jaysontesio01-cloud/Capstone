import 'dart:async';
import 'dart:convert';
import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';

import 'dashboard_widgets.dart';
import 'live_data.dart';

// Public backend URL so the phone does not depend on the laptop or local network.
// For a backend on this computer, build or run with:
//   --dart-define=SERVER_BASE_URL=http://localhost:5000
const String serverBaseUrl = String.fromEnvironment(
  'SERVER_BASE_URL',
  defaultValue: 'https://capstone-1-z9k0.onrender.com',
);
const Duration apiTimeout = Duration(seconds: 10);

void main() {
  runApp(const MyApp());
}

class MyApp extends StatelessWidget {
  const MyApp({super.key});

  @override
  Widget build(BuildContext context) {
    const seed = Color(0xFF0F766E);
    return MaterialApp(
      debugShowCheckedModeBanner: false,
      title: 'Water Pump Maintenance',
      theme: ThemeData(
        useMaterial3: true,
        colorScheme: ColorScheme.fromSeed(
          seedColor: seed,
          brightness: Brightness.light,
        ),
        scaffoldBackgroundColor: const Color(0xFFF5F7FA),
        appBarTheme: const AppBarTheme(
          centerTitle: false,
          elevation: 0,
          backgroundColor: Color(0xFFF5F7FA),
          foregroundColor: Color(0xFF102A43),
        ),
        cardTheme: CardThemeData(
          elevation: 0,
          color: Colors.white,
          margin: EdgeInsets.zero,
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(8),
            side: const BorderSide(color: Color(0xFFE1E8F0)),
          ),
        ),
        textTheme: Theme.of(context).textTheme.apply(
          bodyColor: const Color(0xFF102A43),
          displayColor: const Color(0xFF102A43),
        ),
      ),
      home: const MyHomePage(title: 'Water Pump Maintenance'),
    );
  }
}

class MyHomePage extends StatefulWidget {
  const MyHomePage({super.key, required this.title});

  final String title;

  @override
  State<MyHomePage> createState() => _MyHomePageState();
}

class _MyHomePageState extends State<MyHomePage> {
  // Latest maintenance record (GET /api/esp32/maintenance/<id>?limit=1).
  Map<String, dynamic>? _latestMaintenance;

  // Previous tests of the shown pump (GET /api/esp32/tests/<id>), newest first.
  List<PumpTest>? _previousTests;
  bool _testsLoading = false;

  // Live dashboard (GET /api/esp32/live/<id>), compared with the healthy pump.
  LiveData? _live;
  DateTime? _liveFetchedAt;
  bool _liveRequestActive = false;
  bool _liveRefreshQueued = false;
  int _liveGeneration = 0; // +1 when a test starts or stops
  static const _testMinutesKey = 'test_minutes';
  int _testMinutes = TestSettingsDialog.defaultMinutes;
  bool _testBusy = false;
  String? _lastVerdictAlertKey;
  String? _lastTestFinishedKey;
  bool _dialogOpen = false;

  Timer? _deviceTimer;
  Timer? _liveTimer;
  Timer? _tickTimer;
  final String _deviceId = 'Flutter-App-001';
  String _selectedDeviceId = '';
  List<Map<String, dynamic>> _registeredDevices = const [];
  Future<void>? _deviceRefreshFuture;
  bool _isRestoringSelectedDevice = true;

  @override
  void initState() {
    super.initState();
    _restoreSelectedDevice();
    // The pump list (with each pump's Online/Offline) every 5 seconds.
    _deviceTimer = Timer.periodic(const Duration(seconds: 5), (timer) {
      _fetchRegisteredDevices();
      if (_selectedDeviceId.isNotEmpty) _fetchLatestMaintenance();
    });
    _liveTimer = Timer.periodic(const Duration(seconds: 2), (_) {
      if (_selectedDeviceId.isNotEmpty) _fetchLive();
    });
    // Smooth countdown between the 2-second updates.
    _tickTimer = Timer.periodic(const Duration(seconds: 1), (_) {
      if (mounted && (_live?.test.isRunning ?? false)) setState(() {});
    });
  }

  @override
  void dispose() {
    _deviceTimer?.cancel();
    _liveTimer?.cancel();
    _tickTimer?.cancel();
    super.dispose();
  }

  Future<void> _refreshDashboard() async {
    await Future.wait([
      _fetchRegisteredDevices(),
      _fetchLive(),
      _fetchLatestMaintenance(),
      _fetchPreviousTests(),
    ]);
  }

  /// The shown pump's previous (finished or stopped) tests.
  Future<void> _fetchPreviousTests() async {
    final deviceId = _selectedDeviceId;
    if (deviceId.isEmpty) return;
    setState(() => _testsLoading = true);
    try {
      final response = await http
          .get(
            Uri.parse(
              '$serverBaseUrl/api/esp32/tests/${Uri.encodeComponent(deviceId)}',
            ),
          )
          .timeout(apiTimeout);
      if (!mounted || deviceId != _selectedDeviceId) return;
      if (response.statusCode == 200) {
        final tests = jsonDecode(response.body)['tests'];
        setState(() {
          _previousTests = tests is List
              ? tests
                    .whereType<Map>()
                    .map((t) => PumpTest.fromJson(Map<String, dynamic>.from(t)))
                    .toList()
              : const [];
        });
      }
    } catch (e) {
      debugPrint('Previous tests unavailable: $e');
    } finally {
      if (mounted) setState(() => _testsLoading = false);
    }
  }

  Future<void> _confirmDeleteAllTests() async {
    final count = _previousTests?.length ?? 0;
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        icon: const Icon(Icons.delete_outline, color: kFaulty, size: 32),
        title: const Text('Delete all previous tests?'),
        content: Text(
          'All $count previous test${count == 1 ? '' : 's'} of '
          '${_deviceNameFor(_selectedDeviceId)} will be deleted. '
          'This cannot be undone.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(dialogContext).pop(false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            style: FilledButton.styleFrom(backgroundColor: kFaulty),
            onPressed: () => Navigator.of(dialogContext).pop(true),
            child: const Text('Delete all'),
          ),
        ],
      ),
    );
    if (confirmed == true) await _deleteAllTests();
  }

  Future<void> _deleteAllTests() async {
    final deviceId = _selectedDeviceId;
    if (deviceId.isEmpty) return;
    try {
      final response = await http
          .delete(
            Uri.parse(
              '$serverBaseUrl/api/esp32/tests/${Uri.encodeComponent(deviceId)}',
            ),
          )
          .timeout(apiTimeout);
      if (response.statusCode != 200) {
        throw Exception('HTTP ${response.statusCode}');
      }
      final deleted = jsonDecode(response.body)['deleted'];
      if (mounted) setState(() => _previousTests = const []);
      _showSnack('Deleted $deleted previous test${deleted == 1 ? '' : 's'}.');
    } catch (e) {
      debugPrint('Deleting the previous tests failed: $e');
      _showSnack('Could not delete the previous tests. Try again.');
    }
    await Future.wait([_fetchPreviousTests(), _fetchLive(soon: true)]);
  }

  Future<void> _restoreSelectedDevice() async {
    try {
      final preferences = await SharedPreferences.getInstance();
      final savedDeviceId = preferences.getString('selected_device_id');
      final savedMinutes = preferences.getInt(_testMinutesKey);
      // Web: ...?device=ESP32-001 opens that pump directly (e.g. a bookmark).
      final deviceFromUrl = Uri.base.queryParameters['device']?.trim();
      if (!mounted) return;

      setState(() {
        _selectedDeviceId = (deviceFromUrl != null && deviceFromUrl.isNotEmpty)
            ? deviceFromUrl
            : (savedDeviceId ?? '');
        if (savedMinutes != null) {
          _testMinutes = savedMinutes.clamp(
            TestSettingsDialog.minMinutes,
            TestSettingsDialog.maxMinutes,
          );
        }
        _isRestoringSelectedDevice = false;
      });

      if (_selectedDeviceId.isNotEmpty) {
        _refreshDashboard();
      }
    } catch (e) {
      debugPrint('Failed to restore selected device: $e');
      if (mounted) {
        setState(() {
          _isRestoringSelectedDevice = false;
        });
      }
    }
    _fetchRegisteredDevices();
  }

  Future<void> _persistSelectedDevice(String deviceId) async {
    try {
      final preferences = await SharedPreferences.getInstance();
      await preferences.setString('selected_device_id', deviceId);
    } catch (e) {
      debugPrint('Failed to save selected device: $e');
    }
  }

  Future<void> _fetchRegisteredDevices({VoidCallback? onComplete}) async {
    final activeRefresh = _deviceRefreshFuture;
    if (activeRefresh != null) {
      await activeRefresh;
      onComplete?.call();
      return;
    }

    final refresh = _refreshRegisteredDevices();
    _deviceRefreshFuture = refresh;
    try {
      await refresh;
    } finally {
      if (identical(_deviceRefreshFuture, refresh)) {
        _deviceRefreshFuture = null;
      }
      onComplete?.call();
    }
  }

  Future<void> _refreshRegisteredDevices() async {
    try {
      final response = await http
          .get(Uri.parse('$serverBaseUrl/api/esp32/devices'))
          .timeout(apiTimeout);
      if (response.statusCode != 200) {
        throw Exception('Device search failed: ${response.statusCode}');
      }

      final data = jsonDecode(response.body);
      final devices = data['devices'] is List
          ? data['devices'] as List
          : const [];
      if (!mounted) return;

      setState(() {
        _registeredDevices = devices
            .map((device) => Map<String, dynamic>.from(device as Map))
            .toList();
      });
      if (_selectedDeviceId.isEmpty && !_isRestoringSelectedDevice) {
        final online = _onlineDevices;
        final deviceId = online.isEmpty
            ? ''
            : online.first['device_id']?.toString() ?? '';
        if (deviceId.isNotEmpty) _selectDevice(deviceId);
      }
    } catch (e) {
      debugPrint('Registered device list unavailable: $e');
    }
  }

  List<Map<String, dynamic>> get _onlineDevices =>
      _registeredDevices.where(isDeviceOnline).toList();

  /// Whether a pump's sensors are online. The pump on the dashboard uses its
  /// live data (updated every 2 s), so the list and its sections agree.
  bool _pumpOnline(String deviceId, Map<String, dynamic>? device) {
    final live = _live;
    if (deviceId == _selectedDeviceId && live != null) return live.online;
    return device != null && isDeviceOnline(device);
  }

  /// Whether the shown pump's sensors are online.
  bool get _selectedPumpOnline {
    if (_selectedDeviceId.isEmpty) return false;
    Map<String, dynamic>? device;
    for (final entry in _registeredDevices) {
      if (entry['device_id']?.toString() == _selectedDeviceId) device = entry;
    }
    return _pumpOnline(_selectedDeviceId, device);
  }

  Future<void> _selectDevice(String deviceId) async {
    if (deviceId.isEmpty) return;
    setState(() {
      _selectedDeviceId = deviceId;
      _live = null;
      _latestMaintenance = null;
      _previousTests = null;
      _lastVerdictAlertKey = null;
    });
    await _persistSelectedDevice(deviceId);
    _refreshDashboard();
  }

  Future<void> _showAddDeviceDialog() async {
    await showDialog<void>(
      context: context,
      builder: (dialogContext) {
        var isScanning = false;
        var initialScanStarted = false;

        return StatefulBuilder(
          builder: (context, setDialogState) {
            Future<void> scan() async {
              if (!dialogContext.mounted || isScanning) return;
              setDialogState(() => isScanning = true);
              await _fetchRegisteredDevices(
                onComplete: () {
                  if (dialogContext.mounted) {
                    setDialogState(() => isScanning = false);
                  }
                },
              );
            }

            if (!initialScanStarted) {
              initialScanStarted = true;
              WidgetsBinding.instance.addPostFrameCallback((_) => scan());
            }

            final devices = _onlineDevices;
            return AlertDialog(
              title: const Text('Find nearby pumps'),
              content: SizedBox(
                width: double.maxFinite,
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    const Text(
                      'Pumps connected to the internet will appear here. '
                      'Select a pump to connect.',
                    ),
                    const SizedBox(height: 12),
                    if (isScanning && devices.isEmpty)
                      const Padding(
                        padding: EdgeInsets.all(20),
                        child: CircularProgressIndicator(),
                      )
                    else if (devices.isEmpty)
                      const Padding(
                        padding: EdgeInsets.all(16),
                        child: Text('No online pumps found.'),
                      )
                    else
                      Flexible(
                        child: ListView.separated(
                          shrinkWrap: true,
                          itemCount: devices.length,
                          separatorBuilder: (_, _) => const Divider(height: 1),
                          itemBuilder: (context, index) {
                            final device = devices[index];
                            final deviceId =
                                device['device_id']?.toString() ?? '';
                            final deviceName =
                                device['device_name']?.toString() ?? deviceId;
                            final location =
                                device['location']?.toString() ?? 'Unknown';
                            return ListTile(
                              contentPadding: EdgeInsets.zero,
                              leading: const Icon(
                                Icons.sensors,
                                color: Color(0xFF0F766E),
                              ),
                              title: Text(deviceName),
                              subtitle: Text('$deviceId · $location'),
                              onTap: deviceId.isEmpty
                                  ? null
                                  : () async {
                                      await _selectDevice(deviceId);
                                      if (dialogContext.mounted) {
                                        Navigator.of(dialogContext).pop();
                                      }
                                    },
                            );
                          },
                        ),
                      ),
                  ],
                ),
              ),
              actions: [
                TextButton(
                  onPressed: () => Navigator.of(dialogContext).pop(),
                  child: const Text('Cancel'),
                ),
                OutlinedButton.icon(
                  onPressed: isScanning ? null : scan,
                  icon: const Icon(Icons.refresh),
                  label: Text(isScanning ? 'Scanning...' : 'Scan again'),
                ),
              ],
            );
          },
        );
      },
    );
  }

  Future<void> _confirmDeleteDevice(String deviceId, String deviceName) async {
    final shouldDelete = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: const Text('Remove device?'),
        content: Text(
          'Remove $deviceName from your registered devices? Its sensor history will be kept.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(dialogContext).pop(false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.of(dialogContext).pop(true),
            child: const Text('Remove'),
          ),
        ],
      ),
    );
    if (shouldDelete == true) {
      await _deleteDevice(deviceId, deviceName);
    }
  }

  Future<void> _deleteDevice(String deviceId, String deviceName) async {
    try {
      final response = await http
          .delete(
            Uri.parse(
              '$serverBaseUrl/api/esp32/devices/${Uri.encodeComponent(deviceId)}',
            ),
          )
          .timeout(apiTimeout);
      if (response.statusCode != 200) {
        throw Exception('Device removal failed: ${response.statusCode}');
      }

      final wasSelected = _selectedDeviceId == deviceId;
      var savedSelectionCleared = true;
      if (wasSelected) {
        try {
          final preferences = await SharedPreferences.getInstance();
          savedSelectionCleared = await preferences.remove(
            'selected_device_id',
          );
        } catch (e) {
          debugPrint('Failed to clear saved device selection: $e');
          savedSelectionCleared = false;
        }
      }
      if (!mounted) return;

      setState(() {
        _registeredDevices = _registeredDevices
            .where((device) => device['device_id']?.toString() != deviceId)
            .toList();
        if (wasSelected) {
          _selectedDeviceId = '';
        }
      });
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(
            savedSelectionCleared
                ? '$deviceName removed. Sensor history was kept.'
                : '$deviceName removed, but its saved selection could not be cleared.',
          ),
        ),
      );
      await _fetchRegisteredDevices();
    } catch (e) {
      debugPrint('Failed to remove device $deviceId: $e');
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Could not remove $deviceName. Try again.')),
        );
      }
    }
  }

  /// [soon]: if an update is already on its way, fetch again right after it
  /// (e.g. after starting a test, so its countdown shows at once).
  Future<void> _fetchLive({bool soon = false}) async {
    final deviceId = _selectedDeviceId;
    if (deviceId.isEmpty) return;
    if (_liveRequestActive) {
      if (soon) _liveRefreshQueued = true;
      return;
    }
    _liveRequestActive = true;
    final generation = _liveGeneration;
    try {
      final live = await _requestLive(deviceId);
      if (!mounted || deviceId != _selectedDeviceId) return;
      // Asked before a test started or stopped: its test state is outdated.
      if (generation != _liveGeneration) return;
      if (live == null) {
        _liveUpdateFailed();
        return;
      }
      final previousTest = _live?.deviceId == live.deviceId
          ? _live?.test
          : null;
      setState(() {
        _live = live;
        _liveFetchedAt = DateTime.now();
      });
      _checkAlerts(live, previousTest);
      if ((previousTest?.isRunning ?? false) && !live.test.isRunning) {
        _fetchPreviousTests(); // the test just ended: it is now a previous test
      }
    } catch (e) {
      debugPrint('Live data unavailable: $e');
      if (mounted) _liveUpdateFailed();
    } finally {
      _liveRequestActive = false;
      if (_liveRefreshQueued && mounted) {
        _liveRefreshQueued = false;
        _fetchLive();
      }
    }
  }

  /// The pump's live data, or null if the server has none.
  /// A backend deployed before the live view (no /live route) still has
  /// /status, which is enough to show the sensors online, the temperature and
  /// whether the pump runs.
  Future<LiveData?> _requestLive(String deviceId) async {
    final id = Uri.encodeComponent(deviceId);
    final live = await http
        .get(Uri.parse('$serverBaseUrl/api/esp32/live/$id'))
        .timeout(apiTimeout);
    if (live.statusCode == 200) {
      return LiveData.fromJson(
        Map<String, dynamic>.from(jsonDecode(live.body) as Map),
      );
    }
    final status = await http
        .get(Uri.parse('$serverBaseUrl/api/esp32/status/$id'))
        .timeout(apiTimeout);
    if (status.statusCode == 200) {
      return LiveData.fromStatusJson(
        Map<String, dynamic>.from(jsonDecode(status.body) as Map),
      );
    }
    return null;
  }

  /// A failed or slow update keeps the last data on screen; only when nothing
  /// arrived for longer than the online window (server or ESP32 really not
  /// reachable) show "no data" instead of old values that look live. A few
  /// slow answers in a row no longer make the pump flicker offline.
  void _liveUpdateFailed() {
    final fetchedAt = _liveFetchedAt;
    final stale =
        fetchedAt == null ||
        DateTime.now().difference(fetchedAt) > kOnlineWindow;
    if (stale && _live != null) {
      setState(() => _live = null);
    }
  }

  double get _testRemainingSeconds {
    final test = _live?.test;
    if (test == null || !test.isRunning) return 0;
    final since = _liveFetchedAt == null
        ? 0.0
        : DateTime.now().difference(_liveFetchedAt!).inMilliseconds / 1000.0;
    return (test.remainingSeconds - since).clamp(0.0, test.minutes * 60);
  }

  /// What the dashboard shows: the live data, or empty values (no pump
  /// selected, ESP32 not connected, server not reachable) so every section
  /// still shows.
  LiveData get _view =>
      _live ??
      LiveData.empty(
        deviceId: _selectedDeviceId,
        deviceName: _selectedDeviceId.isEmpty
            ? 'No pump selected'
            : _deviceNameFor(_selectedDeviceId),
      );

  /// The test Machine health and Machine Problems are calculated over: the
  /// running test, or the last one (null before the pump's first test).
  PumpTest? get _currentTest {
    final test = _view.test;
    return test.exists ? test : null;
  }

  /// The current or last test's result over its whole duration.
  Assessment get _testAssessment =>
      _currentTest?.assessment ?? Assessment.empty();

  /// Which test Machine health is calculated over.
  String get _machineHealthSubtitle {
    final test = _currentTest;
    if (test == null) {
      return 'No test yet: start a test to calculate the machine health.';
    }
    final minutes = '${test.minutes.toStringAsFixed(0)} min';
    if (test.isRunning) {
      return 'Test running: ${formatDuration(_testRemainingSeconds)} left of '
          '$minutes · calculated over the test so far';
    }
    final how = test.state == 'stopped' ? 'stopped after' : 'ran';
    return 'Last test: ${_formatDateTime(test.startedAt)} · $minutes test, '
        '$how ${formatDuration(test.elapsedSeconds)}';
  }

  static const _esp32OfflineMessage =
      'The ESP32 is not connected. Turn it on and connect it to your '
      "phone's hotspot, then start the test.";

  void _showSnack(String message) {
    if (!mounted) return;
    ScaffoldMessenger.of(
      context,
    ).showSnackBar(SnackBar(content: Text(message)));
  }

  /// Shows the test the server answered with at once (countdown included);
  /// live updates asked for before it are ignored.
  void _applyTestResponse(String deviceId, http.Response response) {
    final live = _live;
    if (!mounted || live == null || deviceId != _selectedDeviceId) return;
    final json = jsonDecode(response.body);
    if (json is! Map || json['test'] is! Map) return;
    setState(() {
      _liveGeneration++;
      _live = live.withTest(
        PumpTest.fromJson(Map<String, dynamic>.from(json['test'] as Map)),
      );
      _liveFetchedAt = DateTime.now();
    });
  }

  /// Starts a timed test on the server, only while the pump's ESP32 is
  /// connected (the server checks this too).
  Future<void> _startTest() async {
    final deviceId = _selectedDeviceId;
    if (deviceId.isEmpty) return;
    if (!(_live?.online ?? false)) {
      _showSnack(_esp32OfflineMessage);
      return;
    }
    setState(() => _testBusy = true);
    String message;
    try {
      final response = await http
          .post(
            Uri.parse(
              '$serverBaseUrl/api/esp32/test/${Uri.encodeComponent(deviceId)}/start',
            ),
            headers: {'Content-Type': 'application/json'},
            body: jsonEncode({'minutes': _testMinutes}),
          )
          .timeout(apiTimeout);
      switch (response.statusCode) {
        case 200:
          _lastVerdictAlertKey = null;
          _applyTestResponse(deviceId, response);
          message = 'Test started ($_testMinutes min). Switch the pump ON now.';
        case 409: // the server got no data from the ESP32 lately
          message = _esp32OfflineMessage;
        case 404: // a server deployed before timed tests were added
          message =
              'The server does not have timed tests yet. Redeploy the '
              'backend (python folder) to Render, then try again.';
        default:
          message =
              'Could not start the test (HTTP ${response.statusCode}). '
              'Try again.';
      }
    } catch (e) {
      debugPrint('Starting the test failed: $e');
      message =
          "Could not reach the server. Check the phone's internet and "
          'try again.';
    } finally {
      if (mounted) setState(() => _testBusy = false);
    }
    _showSnack(message);
    await _fetchLive(soon: true);
  }

  Future<void> _stopTest() async {
    final deviceId = _selectedDeviceId;
    if (deviceId.isEmpty) return;
    setState(() => _testBusy = true);
    try {
      final response = await http
          .post(
            Uri.parse(
              '$serverBaseUrl/api/esp32/test/${Uri.encodeComponent(deviceId)}/stop',
            ),
          )
          .timeout(apiTimeout);
      if (response.statusCode != 200) {
        throw Exception('HTTP ${response.statusCode}');
      }
      _applyTestResponse(deviceId, response);
      _showSnack('Test stopped. You can switch the pump off.');
    } catch (e) {
      debugPrint('Stopping the test failed: $e');
      _showSnack('Could not stop the test. Try again.');
    } finally {
      if (mounted) setState(() => _testBusy = false);
    }
    await _fetchLive(soon: true);
  }

  void _checkAlerts(LiveData live, PumpTest? previousTest) {
    final test = live.test;
    if (test.state == 'finished' &&
        test.startedAt != null &&
        previousTest != null &&
        previousTest.isRunning) {
      final key = '${live.deviceId}-${test.startedAt!.toIso8601String()}';
      if (_lastTestFinishedKey != key && !_dialogOpen) {
        _lastTestFinishedKey = key;
        _showTestFinishedAlert(test);
        return;
      }
    }

    // While a test runs: alert when its result turns Warning or Faulty (the
    // finished test has its own alert above).
    if (!test.isRunning || test.assessment == null) return;
    final assessment = test.assessment!;
    if (assessment.verdict == 'WARNING' || assessment.verdict == 'FAULTY') {
      final key =
          '${live.deviceId}-${test.startedAt?.toIso8601String()}-${assessment.verdict}';
      if (_lastVerdictAlertKey != key && !_dialogOpen) {
        _lastVerdictAlertKey = key;
        _showVerdictAlert(assessment);
      }
    } else if (assessment.verdict == 'HEALTHY') {
      _lastVerdictAlertKey = null; // alert again if it gets worse later
    }
  }

  Future<void> _showMessageDialog({
    required IconData icon,
    required Color color,
    required String title,
    required String message,
  }) async {
    if (_dialogOpen || !mounted) return;
    _dialogOpen = true;
    try {
      await showDialog<void>(
        context: context,
        builder: (dialogContext) => AlertDialog(
          icon: Icon(icon, color: color, size: 36),
          title: Text(title),
          content: SingleChildScrollView(child: Text(message)),
          actions: [
            FilledButton(
              onPressed: () => Navigator.of(dialogContext).pop(),
              child: const Text('OK'),
            ),
          ],
        ),
      );
    } finally {
      _dialogOpen = false;
    }
  }

  String _problemLines(Assessment assessment) {
    if (assessment.problems.isEmpty) return '';
    final lines = assessment.problems
        .take(3)
        .map((problem) => '• ${problem.title}')
        .join('\n');
    return '\n\nPossible problems:\n$lines';
  }

  void _showVerdictAlert(Assessment assessment) {
    final faulty = assessment.faultyPercent == null
        ? ''
        : '\nToward faulty: ${assessment.faultyPercent!.toStringAsFixed(0)}% '
              '(0% = healthy, 100% = faulty)';
    _showMessageDialog(
      icon: verdictIcon(assessment.verdict),
      color: levelColor(assessment.verdict),
      title: 'Pump check: ${assessment.verdict}',
      message:
          '${assessment.verdictText}.$faulty'
          '${_problemLines(assessment)}\n\n'
          'Compared with the healthy pump.',
    );
  }

  void _showTestFinishedAlert(PumpTest test) {
    final assessment = test.assessment;
    final result = assessment == null || !assessment.hasData
        ? 'No running data was collected.'
        : 'Result: ${assessment.verdict} '
              '(${assessment.faultyPercent?.toStringAsFixed(0) ?? '—'}% toward faulty).'
              '${_problemLines(assessment)}';
    _showMessageDialog(
      icon: Icons.timer_off_outlined,
      color: const Color(0xFF0F766E),
      title: 'Test finished',
      message: 'Switch the pump OFF now.\n\n$result',
    );
  }

  Future<void> _fetchLatestMaintenance() async {
    if (_selectedDeviceId.isEmpty) return;
    final deviceId = _selectedDeviceId;
    try {
      final response = await http
          .get(
            Uri.parse(
              '$serverBaseUrl/api/esp32/maintenance/${Uri.encodeComponent(deviceId)}?limit=1',
            ),
          )
          .timeout(apiTimeout);
      if (response.statusCode != 200 ||
          !mounted ||
          deviceId != _selectedDeviceId) {
        return;
      }
      final history = jsonDecode(response.body)['history'];
      setState(() {
        _latestMaintenance = history is List && history.isNotEmpty
            ? Map<String, dynamic>.from(history.first as Map)
            : null;
      });
    } catch (e) {
      debugPrint('Latest maintenance record unavailable: $e');
    }
  }

  Future<List<Map<String, dynamic>>> _fetchMaintenanceHistory() async {
    if (_selectedDeviceId.isEmpty) return const [];
    try {
      final response = await http
          .get(
            Uri.parse(
              '$serverBaseUrl/api/esp32/maintenance/$_selectedDeviceId?limit=20',
            ),
          )
          .timeout(apiTimeout);

      if (response.statusCode != 200) {
        return const [];
      }

      final data = jsonDecode(response.body);
      final history = data['history'];
      if (history is List) {
        return history
            .map((item) => Map<String, dynamic>.from(item as Map))
            .toList();
      }
      return const [];
    } catch (e) {
      debugPrint('Maintenance history unavailable: $e');
      return const [];
    }
  }

  String _formatDateTime(DateTime? value) {
    if (value == null) return 'No date recorded';
    final hour = value.hour % 12 == 0 ? 12 : value.hour % 12;
    final period = value.hour >= 12 ? 'PM' : 'AM';
    final minute = value.minute.toString().padLeft(2, '0');
    return '${value.month}/${value.day}/${value.year} $hour:$minute $period';
  }

  Widget _buildAppDrawer() {
    return Drawer(
      backgroundColor: Colors.white,
      child: SafeArea(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(20, 20, 20, 16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const SystemLogo(size: 44),
                  const SizedBox(height: 14),
                  const Text(
                    'Pump Monitor',
                    style: TextStyle(fontSize: 22, fontWeight: FontWeight.w700),
                  ),
                  const SizedBox(height: 4),
                  Text(
                    _deviceId,
                    style: const TextStyle(color: Color(0xFF64748B)),
                  ),
                ],
              ),
            ),
            const Divider(height: 1),
            ListTile(
              leading: const Icon(Icons.space_dashboard_outlined),
              title: const Text('Dashboard'),
              selected: true,
              onTap: () => Navigator.pop(context),
            ),
            ListTile(
              leading: const Icon(Icons.settings_outlined),
              title: const Text('Test settings'),
              subtitle: Text('Test duration: $_testMinutes min'),
              onTap: () {
                Navigator.pop(context);
                _openTestSettings();
              },
            ),
            ListTile(
              leading: const Icon(Icons.add_circle_outline),
              title: const Text('Add device'),
              onTap: () {
                Navigator.pop(context);
                _showAddDeviceDialog();
              },
            ),
            const Divider(height: 1),
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 12, 16, 8),
              child: Text(
                'Pumps',
                style: TextStyle(
                  color: const Color(0xFF64748B),
                  fontWeight: FontWeight.w700,
                  fontSize: 12,
                ),
              ),
            ),
            ..._registeredDevices.map((device) {
              final deviceId = device['device_id']?.toString() ?? 'Unknown';
              final deviceName = device['device_name']?.toString() ?? deviceId;
              final isSelected = deviceId == _selectedDeviceId;
              final online = _pumpOnline(deviceId, device);
              return ListTile(
                dense: true,
                leading: Icon(
                  isSelected ? Icons.check_circle : Icons.engineering,
                  color: isSelected
                      ? const Color(0xFF0F766E)
                      : const Color(0xFF64748B),
                ),
                title: Text(deviceName),
                subtitle: Text(deviceId),
                trailing: StatusChip(
                  label: online ? 'Online' : 'Offline',
                  color: online ? kHealthy : kOff,
                ),
                selected: isSelected,
                onLongPress: () => _confirmDeleteDevice(deviceId, deviceName),
                onTap: () {
                  Navigator.pop(context);
                  _selectDevice(deviceId);
                },
              );
            }),
            if (_registeredDevices.isEmpty)
              const Padding(
                padding: EdgeInsets.symmetric(horizontal: 16, vertical: 8),
                child: Text(
                  'No devices found yet',
                  style: TextStyle(color: Color(0xFF64748B)),
                ),
              ),
            const Divider(height: 1),
            ListTile(
              leading: const Icon(Icons.history),
              title: const Text('History'),
              onTap: () {
                Navigator.pop(context);
                Navigator.of(context).push(
                  MaterialPageRoute(
                    builder: (_) => HistoryPage(
                      fetchHistory: _fetchMaintenanceHistory,
                      formatDateTime: _formatDateTime,
                    ),
                  ),
                );
              },
            ),
            ListTile(
              leading: const Icon(Icons.build_circle_outlined),
              title: const Text('Maintenance history'),
              onTap: () {
                Navigator.pop(context);
                Navigator.of(context).push(
                  MaterialPageRoute(
                    builder: (_) => MaintenanceHistoryPage(
                      fetchMaintenanceHistory: _fetchMaintenanceHistory,
                      formatDateTime: _formatDateTime,
                    ),
                  ),
                );
              },
            ),
            const Spacer(),
            Padding(
              padding: const EdgeInsets.all(16),
              child: Text(
                serverBaseUrl,
                style: const TextStyle(fontSize: 12, color: Color(0xFF64748B)),
              ),
            ),
          ],
        ),
      ),
    );
  }

  String _deviceNameFor(String deviceId) {
    for (final device in _registeredDevices) {
      if (device['device_id']?.toString() == deviceId) {
        return device['device_name']?.toString() ?? deviceId;
      }
    }
    return deviceId;
  }

  double? _barsTotalRms(LiveData live) {
    final values = live.axes.map((axis) => axis.rmsG).toList();
    if (values.any((value) => value == null)) return null;
    return math.sqrt(values.fold<double>(0, (sum, v) => sum + v! * v));
  }

  List<Widget> _buildLiveSections() {
    final live = _view;
    final test = _currentTest;
    final testResult = _testAssessment;
    return [
      // Temperature and the test timer side by side, same height.
      IntrinsicHeight(
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Expanded(
              child: TemperatureCard(
                temperatureC: live.temperatureC,
                pumpRunning: live.pumpRunning,
                online: live.online,
                healthyMaxC: live.healthyReference.temperatureMaxC,
                healthyMeanC: live.healthyReference.temperatureMeanC,
              ),
            ),
            const SizedBox(width: 10),
            Expanded(
              child: TestTimerCard(
                test: live.test,
                remainingSeconds: _testRemainingSeconds,
                selectedMinutes: _testMinutes,
                pumpRunning: live.pumpRunning && live.online,
                busy: _testBusy,
                onStart: _startTest,
                onStop: _stopTest,
                onOpenSettings: _openTestSettings,
                canStart: _selectedDeviceId.isNotEmpty,
                connected: live.online,
              ),
            ),
          ],
        ),
      ),
      const SizedBox(height: 12),
      VibrationBarsCard(
        axes: live.axes,
        pumpRunning: live.pumpRunning,
        online: live.online,
        overall: live.liveAssessment,
        liveTotalRmsG: _barsTotalRms(live),
        healthyTotalRmsG: live.healthyReference.totalRmsG,
      ),
      const SizedBox(height: 12),
      // Calculated over the current or last test's whole duration.
      MachineHealthCard(
        assessment: testResult,
        subtitle: _machineHealthSubtitle,
      ),
      const SizedBox(height: 12),
      MachineProblemsCard(
        assessment: testResult,
        hasTest: test != null,
        testRunning: test?.isRunning ?? false,
      ),
      const SizedBox(height: 12),
      PreviousTestsCard(
        tests: _previousTests,
        loading: _testsLoading,
        onDeleteAll: _selectedDeviceId.isEmpty ? null : _confirmDeleteAllTests,
        formatDateTime: _formatDateTime,
      ),
    ];
  }

  /// Test settings (⚙ on the Test timer, or the menu): the test duration,
  /// saved on this device (default 20 minutes).
  Future<void> _openTestSettings() async {
    final minutes = await showDialog<int>(
      context: context,
      builder: (_) => TestSettingsDialog(
        minutes: _testMinutes,
        testRunning: _live?.test.isRunning ?? false,
      ),
    );
    if (minutes == null || !mounted) return;
    setState(() => _testMinutes = minutes);
    try {
      final preferences = await SharedPreferences.getInstance();
      await preferences.setInt(_testMinutesKey, minutes);
    } catch (e) {
      debugPrint('Saving the test duration failed: $e');
    }
  }

  Widget _buildRecentHistoryPreview() {
    final latest = _latestMaintenance;
    final risk = latest?['risk_score'] is num
        ? (latest!['risk_score'] as num).toDouble()
        : null;
    final when = DateTime.tryParse(latest?['timestamp']?.toString() ?? '');
    return Card(
      child: ListTile(
        leading: const Icon(Icons.history, color: Color(0xFF0F766E)),
        title: Text(
          latest == null
              ? 'No maintenance records yet'
              : 'Last record: risk ${risk?.toStringAsFixed(0) ?? '—'} / 100',
          style: const TextStyle(fontWeight: FontWeight.w800),
        ),
        subtitle: Text(
          latest == null
              ? 'Records are saved every few minutes while the pump runs.'
              : '${_formatDateTime(when?.toLocal())}\n'
                    '${latest['maintenance_action'] ?? ''}',
        ),
        isThreeLine: latest != null,
        trailing: TextButton(
          onPressed: () {
            Navigator.of(context).push(
              MaterialPageRoute(
                builder: (_) => MaintenanceHistoryPage(
                  fetchMaintenanceHistory: _fetchMaintenanceHistory,
                  formatDateTime: _formatDateTime,
                ),
              ),
            );
          },
          child: const Text('View'),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        automaticallyImplyLeading: false,
        titleSpacing: 12,
        title: Row(
          children: [
            const SystemLogo(size: 36),
            const SizedBox(width: 5),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    widget.title,
                    style: const TextStyle(fontWeight: FontWeight.w800),
                  ),
                  const Text(
                    'Water pump maintenance monitoring',
                    style: TextStyle(fontSize: 12, color: Color(0xFF64748B)),
                  ),
                ],
              ),
            ),
          ],
        ),
        actions: [
          // The menu opens from the right, next to its button.
          Builder(
            builder: (context) => IconButton(
              tooltip: 'Menu',
              icon: const Icon(Icons.menu),
              onPressed: () => Scaffold.of(context).openEndDrawer(),
            ),
          ),
        ],
      ),
      endDrawer: _buildAppDrawer(),
      body: _isRestoringSelectedDevice
          ? const Center(child: CircularProgressIndicator())
          : SafeArea(
              child: RefreshIndicator(
                onRefresh: _selectedDeviceId.isEmpty
                    ? _fetchRegisteredDevices
                    : _refreshDashboard,
                child: ListView(
                  padding: const EdgeInsets.fromLTRB(12, 8, 12, 24),
                  children: [
                    PumpSwitchCard(
                      selectedDeviceId: _selectedDeviceId,
                      online: _selectedPumpOnline,
                      onSelect: _selectDevice,
                    ),
                    const SizedBox(height: 12),
                    ..._buildLiveSections(),
                    const SizedBox(height: 12),
                    _buildRecentHistoryPreview(),
                  ],
                ),
              ),
            ),
    );
  }
}

class MaintenanceHistoryPage extends StatefulWidget {
  const MaintenanceHistoryPage({
    super.key,
    required this.fetchMaintenanceHistory,
    required this.formatDateTime,
  });

  final Future<List<Map<String, dynamic>>> Function() fetchMaintenanceHistory;
  final String Function(DateTime?) formatDateTime;

  @override
  State<MaintenanceHistoryPage> createState() => _MaintenanceHistoryPageState();
}

class _MaintenanceHistoryPageState extends State<MaintenanceHistoryPage> {
  late List<Map<String, dynamic>> _history;
  bool _isLoading = true;

  @override
  void initState() {
    super.initState();
    _history = const [];
    _refresh();
  }

  Future<void> _refresh() async {
    setState(() {
      _isLoading = true;
    });
    final result = await widget.fetchMaintenanceHistory();
    if (!mounted) return;
    setState(() {
      _history = result;
      _isLoading = false;
    });
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text(
          'Maintenance history',
          style: TextStyle(fontWeight: FontWeight.w800),
        ),
        actions: [
          IconButton(
            tooltip: 'Refresh',
            onPressed: _refresh,
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
      body: RefreshIndicator(
        onRefresh: _refresh,
        child: _isLoading
            ? const Center(child: CircularProgressIndicator())
            : _history.isEmpty
            ? const Center(
                child: Text('No maintenance recommendations recorded yet.'),
              )
            : ListView.separated(
                padding: const EdgeInsets.all(12),
                itemCount: _history.length,
                separatorBuilder: (_, _) => const SizedBox(height: 8),
                itemBuilder: (context, index) {
                  final item = _history[index];
                  final risk = (item['risk_score'] is num)
                      ? (item['risk_score'] as num).toDouble()
                      : 0.0;
                  final timeValue = item['timestamp']?.toString();
                  final timestamp = timeValue == null || timeValue.isEmpty
                      ? null
                      : DateTime.tryParse(timeValue);
                  final shutdown =
                      item['suggested_shutdown_date']?.toString() ?? '—';
                  final failure =
                      item['predicted_failure_date']?.toString() ?? '—';
                  final action =
                      item['maintenance_action']?.toString() ??
                      'Monitor the pump';
                  final failureBasis =
                      item['failure_basis']?.toString() ?? 'Not recorded';
                  final remainingLife = item['remaining_life_days'];
                  final remainingLifeText = remainingLife is num
                      ? '${remainingLife.toStringAsFixed(1)} days'
                      : 'Not recorded';
                  final runtimeHours = item['runtime_hours'];
                  final runtimeHoursText = runtimeHours is num
                      ? '${runtimeHours.toStringAsFixed(1)} hours'
                      : 'Not recorded';
                  final alertStatus = item['alert_active'] == true
                      ? 'Active'
                      : item['alert_active'] == false
                      ? 'Clear'
                      : 'Not recorded';

                  return Card(
                    child: ListTile(
                      leading: Container(
                        width: 44,
                        height: 44,
                        decoration: BoxDecoration(
                          color: risk >= 85
                              ? const Color(0xFFFFFBEB)
                              : const Color(0xFFECFDF5),
                          borderRadius: BorderRadius.circular(8),
                        ),
                        child: Icon(
                          risk >= 85
                              ? Icons.warning_amber_rounded
                              : Icons.build_circle_outlined,
                          color: risk >= 85
                              ? const Color(0xFFD97706)
                              : const Color(0xFF0F766E),
                        ),
                      ),
                      title: Text(
                        'Risk ${risk.toStringAsFixed(2)} / 100',
                        style: const TextStyle(fontWeight: FontWeight.w800),
                      ),
                      subtitle: Padding(
                        padding: const EdgeInsets.only(top: 4),
                        child: Text(
                          'Recorded: ${widget.formatDateTime(timestamp)}\n'
                          'Failure basis: $failureBasis\n'
                          'Remaining life: $remainingLifeText\n'
                          'Alert status: $alertStatus\n'
                          'Predicted failure: $failure\n'
                          'Suggested shutdown: $shutdown\n'
                          'Runtime: $runtimeHoursText\n'
                          'Action: $action',
                        ),
                      ),
                      isThreeLine: true,
                    ),
                  );
                },
              ),
      ),
    );
  }
}

class HistoryPage extends StatefulWidget {
  const HistoryPage({
    super.key,
    required this.fetchHistory,
    required this.formatDateTime,
  });

  final Future<List<Map<String, dynamic>>> Function() fetchHistory;
  final String Function(DateTime?) formatDateTime;

  @override
  State<HistoryPage> createState() => _HistoryPageState();
}

class _HistoryPageState extends State<HistoryPage> {
  List<Map<String, dynamic>> _history = const [];
  bool _isLoading = true;

  @override
  void initState() {
    super.initState();
    _refresh();
  }

  Future<void> _refresh() async {
    setState(() {
      _isLoading = true;
    });
    final result = await widget.fetchHistory();
    if (!mounted) return;
    setState(() {
      _history = result;
      _isLoading = false;
    });
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text(
          'History',
          style: TextStyle(fontWeight: FontWeight.w800),
        ),
        actions: [
          IconButton(
            tooltip: 'Refresh',
            onPressed: _refresh,
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
      body: RefreshIndicator(onRefresh: _refresh, child: _buildBody(context)),
    );
  }

  Widget _buildBody(BuildContext context) {
    if (_isLoading && _history.isEmpty) {
      return const Center(child: CircularProgressIndicator());
    }

    if (_history.isEmpty) {
      return ListView(
        padding: const EdgeInsets.all(24),
        children: [
          Icon(Icons.history, size: 48, color: Colors.grey[600]),
          const SizedBox(height: 12),
          Text(
            'No predictive maintenance records yet',
            textAlign: TextAlign.center,
            style: Theme.of(context).textTheme.titleLarge,
          ),
          const SizedBox(height: 8),
          const Text(
            'Records will appear after the system saves a pump assessment.',
            textAlign: TextAlign.center,
          ),
        ],
      );
    }

    return ListView.separated(
      padding: const EdgeInsets.all(12),
      itemCount: _history.length,
      separatorBuilder: (_, _) => const SizedBox(height: 8),
      itemBuilder: (context, index) {
        final item = _history[index];
        final risk = item['risk_score'] is num
            ? (item['risk_score'] as num).toDouble()
            : 0.0;
        final timestampValue = item['timestamp']?.toString();
        final timestamp = timestampValue == null || timestampValue.isEmpty
            ? null
            : DateTime.tryParse(timestampValue);
        final remainingLife = item['remaining_life_days'];
        final remainingLifeText = remainingLife is num
            ? '${remainingLife.toStringAsFixed(1)} days'
            : 'Not estimated';
        final runtime = item['runtime_hours'];
        final runtimeText = runtime is num
            ? '${runtime.toStringAsFixed(1)} hours'
            : 'Not recorded';
        final alertStatus = item['alert_active'] == true
            ? 'Active'
            : item['alert_active'] == false
            ? 'Normal'
            : 'Not recorded';
        final predictedFailure =
            item['predicted_failure_date']?.toString() ?? '—';
        final suggestedShutdown =
            item['suggested_shutdown_date']?.toString() ?? '—';
        final failureBasis =
            item['failure_basis']?.toString() ?? 'Not recorded';
        final severity =
            item['severity']?.toString() ??
            (risk >= 85
                ? 'CRITICAL'
                : risk >= 60
                ? 'WARNING'
                : 'MONITOR');
        final statusColor = severity == 'CRITICAL'
            ? const Color(0xFFDC2626)
            : severity == 'WARNING'
            ? const Color(0xFFD97706)
            : const Color(0xFF0891B2);
        final action =
            item['maintenance_action']?.toString() ??
            'Continue monitoring and collect more data.';

        return Card(
          child: Padding(
            padding: const EdgeInsets.all(14),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    const Icon(Icons.auto_awesome, color: Color(0xFF0F766E)),
                    const SizedBox(width: 8),
                    const Expanded(
                      child: Text(
                        'Predictive maintenance',
                        style: TextStyle(fontWeight: FontWeight.w800),
                      ),
                    ),
                    Container(
                      padding: const EdgeInsets.symmetric(
                        horizontal: 10,
                        vertical: 5,
                      ),
                      decoration: BoxDecoration(
                        color: statusColor.withAlpha(20),
                        borderRadius: BorderRadius.circular(16),
                      ),
                      child: Text(
                        severity,
                        style: TextStyle(
                          color: statusColor,
                          fontWeight: FontWeight.w800,
                        ),
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 10),
                Text(
                  'Recorded: ${widget.formatDateTime(timestamp)}',
                  style: const TextStyle(color: Color(0xFF64748B)),
                ),
                const SizedBox(height: 8),
                _historyInfoRow(
                  'Risk score',
                  '${risk.toStringAsFixed(2)} / 100',
                ),
                _historyInfoRow('Failure basis', failureBasis),
                _historyInfoRow('Remaining life', remainingLifeText),
                _historyInfoRow('Alert status', alertStatus),
                _historyInfoRow('Predicted failure', predictedFailure),
                _historyInfoRow('Suggested shutdown', suggestedShutdown),
                _historyInfoRow('Runtime hours', runtimeText),
                const SizedBox(height: 10),
                Container(
                  width: double.infinity,
                  padding: const EdgeInsets.all(10),
                  decoration: BoxDecoration(
                    color: statusColor.withAlpha(16),
                    borderRadius: BorderRadius.circular(8),
                    border: Border.all(color: statusColor.withAlpha(70)),
                  ),
                  child: Text(
                    action,
                    style: const TextStyle(fontWeight: FontWeight.w700),
                  ),
                ),
              ],
            ),
          ),
        );
      },
    );
  }

  Widget _historyInfoRow(String label, String value) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 3),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: 132,
            child: Text(
              label,
              style: const TextStyle(
                color: Color(0xFF64748B),
                fontWeight: FontWeight.w600,
              ),
            ),
          ),
          Expanded(
            child: Text(
              value,
              style: const TextStyle(fontWeight: FontWeight.w700),
            ),
          ),
        ],
      ),
    );
  }
}
