'''
Controller for the Sensor Top Bar plugin.

Responsibilities:
  * Register a settings panel + configuration options (battery pack setup, demo
    mode, refresh rate).
  * Serve the front-end assets (topbar.js / topbar.css) through a blueprint.
  * Run a background loop that reads every installed sensor, computes the
    battery charge %, and broadcasts a compact payload to the browser over the
    existing Socket.IO connection.
'''

import logging
import math
import os
import socket
from time import monotonic

import gevent
from flask import Blueprint

from RHUI import UIField, UIFieldType, UIFieldSelectOption

logger = logging.getLogger(__name__)

PLUGIN_ID = 'sensor_topbar'

# Option keys (kept together so the reader helpers and registration agree).
OPT_SOURCE = 'topbar_batt_source'
OPT_CELLS = 'topbar_batt_cells'
OPT_CELL_MIN = 'topbar_batt_cell_min'
OPT_CELL_MAX = 'topbar_batt_cell_max'
OPT_CAPACITY = 'topbar_batt_capacity'
OPT_METHOD = 'topbar_batt_method'
OPT_INTERVAL = 'topbar_interval'
OPT_DEMO = 'topbar_demo'
OPT_THEME = 'topbar_theme'

# Standard resting per-cell LiPo discharge curve (voltage -> percent), used as
# the shape of the estimate. It is rescaled to the user's configured per-cell
# min/max so custom chemistries/ranges still follow a sensible curve.
_LIPO_STD_MAX = 4.20
_LIPO_STD_MIN = 3.27
_LIPO_CURVE = [
    (4.20, 100), (4.15, 95), (4.11, 90), (4.08, 85), (4.02, 80),
    (3.98, 75), (3.95, 70), (3.91, 65), (3.87, 60), (3.85, 55),
    (3.84, 50), (3.82, 45), (3.80, 40), (3.79, 35), (3.77, 30),
    (3.75, 25), (3.73, 20), (3.71, 15), (3.69, 10), (3.61, 5), (3.27, 0),
]


class TopBarController:
    def __init__(self, rhapi):
        self._rhapi = rhapi
        self._task = None
        self._started_at = monotonic()
        self._net_cache = None
        self._net_cache_at = 0.0
        # Previous /proc/stat snapshot (total, idle) for CPU-usage deltas.
        self._cpu_prev = None

    # ------------------------------------------------------------------ setup

    def register_blueprint(self):
        bp = Blueprint(
            PLUGIN_ID,
            __name__,
            static_folder='static',
            static_url_path='/sensor_topbar/static',
        )
        self._rhapi.ui.blueprint_add(bp)

    def on_startup(self, _args=None):
        self._register_ui()
        if self._task is None:
            self._task = gevent.spawn(self._loop)
            logger.info('Sensor Top Bar telemetry loop started')

    def _register_ui(self):
        ui = self._rhapi.ui
        fields = self._rhapi.fields

        ui.register_panel(PLUGIN_ID, 'Sensor Top Bar', 'settings', order=0)

        # Battery voltage source: auto-detect, none, or a specific reading.
        source_options = [
            UIFieldSelectOption('auto', 'Auto-detect (first voltage reading)'),
            UIFieldSelectOption('none', 'No battery'),
        ]
        for name, reading in self._voltage_readings():
            key = '{}:{}'.format(name, reading)
            source_options.append(UIFieldSelectOption(key, key))

        fields.register_option(UIField(
            name=OPT_SOURCE, label='Battery voltage source',
            field_type=UIFieldType.SELECT, options=source_options, value='auto',
            desc='Which sensor reading is the battery pack voltage.'),
            PLUGIN_ID)

        fields.register_option(UIField(
            name=OPT_CELLS, label='Battery cells (S)',
            field_type=UIFieldType.BASIC_INT, value=4,
            desc='Number of cells in series in the pack.'),
            PLUGIN_ID)

        fields.register_option(UIField(
            name=OPT_CELL_MIN, label='Empty voltage per cell (V)',
            field_type=UIFieldType.NUMBER, value=3.3,
            desc='Per-cell voltage treated as 0% charge (e.g. 3.3 for LiPo).'),
            PLUGIN_ID)

        fields.register_option(UIField(
            name=OPT_CELL_MAX, label='Full voltage per cell (V)',
            field_type=UIFieldType.NUMBER, value=4.2,
            desc='Per-cell voltage treated as 100% charge (e.g. 4.2 for LiPo).'),
            PLUGIN_ID)

        fields.register_option(UIField(
            name=OPT_CAPACITY, label='Pack capacity (mAh)',
            field_type=UIFieldType.BASIC_INT, value=1500,
            desc='Used to estimate remaining capacity in mAh.'),
            PLUGIN_ID)

        fields.register_option(UIField(
            name=OPT_METHOD, label='Charge % method',
            field_type=UIFieldType.SELECT, value='curve',
            options=[
                UIFieldSelectOption('curve', 'LiPo discharge curve'),
                UIFieldSelectOption('linear', 'Linear voltage'),
            ],
            desc='LiPo curve is more accurate for lithium packs; linear is a '
                 'straight voltage-to-percent map.'),
            PLUGIN_ID)

        fields.register_option(UIField(
            name=OPT_THEME, label='Theme',
            field_type=UIFieldType.SELECT, value='dark',
            options=[
                UIFieldSelectOption('dark', 'Dark'),
                UIFieldSelectOption('light', 'Light'),
                UIFieldSelectOption('auto', 'Auto (follow browser/OS)'),
            ],
            desc='Colour scheme of the top bar. Auto follows each viewer\'s '
                 'browser/OS light-dark preference.'),
            PLUGIN_ID)

        fields.register_option(UIField(
            name=OPT_INTERVAL, label='Refresh interval (seconds)',
            field_type=UIFieldType.BASIC_INT, value=2,
            desc='How often the top bar is updated.'),
            PLUGIN_ID)

        fields.register_option(UIField(
            name=OPT_DEMO, label='Demo mode (simulate sensors)',
            field_type=UIFieldType.CHECKBOX, value=False,
            desc='Generate simulated telemetry so the bar can be tested on a '
                 'PC with no sensors attached.'),
            PLUGIN_ID)

        self._register_loader(ui)

    def _register_loader(self, ui):
        '''Inject the top bar's front-end without editing any core template.

        RotorHazard has no hook to add global JS/CSS, but every page that shows
        plugin panels renders either a panel's *markdown* (via showdown +
        jQuery ``.html()``) or a field's *description* (via jQuery ``.append``)
        as raw HTML -- both of which execute embedded <script> tags. So a
        one-line <script> loader delivered through those channels pulls in
        topbar.js, which then builds the sticky bar and loads its own CSS.

        Pages differ in which channel they render (e.g. the Marshal page shows
        field descriptions but not markdown), so each hidden loader panel
        carries the loader through *both* channels. topbar.js has a load-once
        guard, and the loader panels are hidden by topbar.css.
        '''
        fields = self._rhapi.fields
        loader = '<script src="/sensor_topbar/static/topbar.js"></script>'

        # Settings page: attach the loader to the existing config panel.
        ui.register_markdown(PLUGIN_ID, 'sensor_topbar_boot', loader)

        # Other pages that render custom UI panels: hidden loader panels,
        # covered via markdown *and* a dummy field description.
        for page in ('run', 'marshal', 'results', 'format'):
            panel = 'sensor_topbar_load_' + page
            ui.register_panel(panel, 'Sensor Top Bar', page, order=0)
            ui.register_markdown(panel, 'sensor_topbar_boot_' + page, loader)
            fields.register_option(UIField(
                name='_sensor_topbar_boot_' + page, label='', value='',
                field_type=UIFieldType.TEXT, private=True, desc=loader),
                panel)

    # -------------------------------------------------------------- option io

    def _opt(self, name, default):
        try:
            val = self._rhapi.db.option(name)
        except Exception:
            return default
        if val is None or val == '':
            return default
        return val

    def _opt_float(self, name, default):
        try:
            return float(self._opt(name, default))
        except (TypeError, ValueError):
            return default

    def _opt_int(self, name, default):
        try:
            return int(float(self._opt(name, default)))
        except (TypeError, ValueError):
            return default

    def _opt_bool(self, name, default=False):
        val = self._opt(name, default)
        return val in (True, 1, '1', 'true', 'True')

    # ----------------------------------------------------------- sensor reads

    def _sensor_objs(self):
        try:
            return self._rhapi.sensors.sensor_objs
        except Exception:
            return []

    def _voltage_readings(self):
        '''All (sensor_name, reading_name) pairs whose units are volts.'''
        found = []
        for sensor in self._sensor_objs():
            try:
                readings = sensor.getReadings()
            except Exception:
                continue
            for rname, data in readings.items():
                if str(data.get('units', '')).strip().upper() == 'V':
                    found.append((sensor.name, rname))
        return found

    def _read_all(self):
        '''Return a flat list of reading dicts from real hardware.'''
        out = []
        for sensor in self._sensor_objs():
            try:
                sensor.update()
            except Exception:
                pass  # keep last-known values if a read fails
            try:
                readings = sensor.getReadings()
            except Exception:
                continue
            for rname, data in readings.items():
                out.append({
                    'sensor': sensor.name,
                    'reading': rname,
                    'value': data.get('value'),
                    'units': data.get('units', ''),
                })
        return out

    def _demo_readings(self):
        '''Simulated telemetry for testing without hardware.'''
        t = monotonic() - self._started_at
        # Slow discharge that recovers, cycling roughly every 4 minutes.
        frac = (t % 240.0) / 240.0
        cell_min = self._opt_float(OPT_CELL_MIN, 3.3)
        cell_max = self._opt_float(OPT_CELL_MAX, 4.2)
        cells = max(1, self._opt_int(OPT_CELLS, 4))
        per_cell = cell_max - (cell_max - cell_min) * frac
        pack_v = round(per_cell * cells, 2)
        wob = math.sin(t / 7.0)
        return [
            {'sensor': 'Battery', 'reading': 'voltage', 'value': pack_v, 'units': 'V'},
            {'sensor': 'Battery', 'reading': 'current',
             'value': round(8.5 + wob * 1.5, 2), 'units': 'A'},
            {'sensor': 'Battery', 'reading': 'power',
             'value': round(pack_v * (8.5 + wob * 1.5), 1), 'units': 'W'},
            {'sensor': 'Core', 'reading': 'temperature',
             'value': round(48.0 + wob * 4.0, 1), 'units': '°C'},
            {'sensor': 'Outside', 'reading': 'temperature',
             'value': round(23.0 + math.sin(t / 30.0) * 1.5, 1), 'units': '°C'},
            {'sensor': 'Outside', 'reading': 'pressure',
             'value': round(1013.0 + math.sin(t / 45.0) * 3.0, 1), 'units': 'hPa'},
            {'sensor': 'Outside', 'reading': 'humidity',
             'value': round(46.0 + math.sin(t / 25.0) * 5.0, 0), 'units': '%rH'},
        ]

    # ------------------------------------------------------------- battery calc

    def _lipo_percent(self, per_cell, cell_min, cell_max):
        span = (cell_max - cell_min)
        if span <= 0:
            return 0.0
        # Map the measured per-cell voltage onto the standard curve's scale.
        v_std = _LIPO_STD_MIN + (per_cell - cell_min) / span * (_LIPO_STD_MAX - _LIPO_STD_MIN)
        if v_std >= _LIPO_CURVE[0][0]:
            return 100.0
        if v_std <= _LIPO_CURVE[-1][0]:
            return 0.0
        for i in range(len(_LIPO_CURVE) - 1):
            v_hi, p_hi = _LIPO_CURVE[i]
            v_lo, p_lo = _LIPO_CURVE[i + 1]
            if v_lo <= v_std <= v_hi:
                return p_lo + (p_hi - p_lo) * (v_std - v_lo) / (v_hi - v_lo)
        return 0.0

    def _compute_battery(self, readings):
        source = self._opt(OPT_SOURCE, 'auto')
        if source == 'none':
            return {'present': False}

        voltage = None
        used_source = None
        current = None

        # Resolve the pack voltage reading.
        if source and source != 'auto' and ':' in source:
            want_name, want_reading = source.split(':', 1)
            for r in readings:
                if r['sensor'] == want_name and r['reading'] == want_reading:
                    voltage = r['value']
                    used_source = source
                    break

        if voltage is None:  # auto-detect, or configured source missing
            for r in readings:
                if str(r['units']).strip().upper() == 'V':
                    voltage = r['value']
                    used_source = '{}:{}'.format(r['sensor'], r['reading'])
                    break

        if voltage is None:
            return {'present': False}

        # Grab current/power from the same sensor if present, and remember which
        # readings we consumed so they are not duplicated as standalone tiles.
        src_sensor = used_source.split(':', 1)[0] if used_source else None
        power = None
        consumed = [used_source] if used_source else []
        for r in readings:
            if r['sensor'] != src_sensor:
                continue
            u = str(r['units']).strip().upper()
            key = '{}:{}'.format(r['sensor'], r['reading'])
            if u in ('A', 'MA') and current is None:
                current = r['value'] if u == 'A' else (r['value'] or 0) / 1000.0
                consumed.append(key)
            elif u in ('W', 'MW') and power is None:
                power = r['value'] if u == 'W' else (r['value'] or 0) / 1000.0
                consumed.append(key)

        cells = max(1, self._opt_int(OPT_CELLS, 4))
        cell_min = self._opt_float(OPT_CELL_MIN, 3.3)
        cell_max = self._opt_float(OPT_CELL_MAX, 4.2)
        capacity = self._opt_int(OPT_CAPACITY, 1500)
        method = self._opt(OPT_METHOD, 'curve')

        try:
            voltage = float(voltage)
        except (TypeError, ValueError):
            return {'present': False}

        per_cell = voltage / cells if cells else voltage
        if method == 'linear':
            span = (cell_max - cell_min)
            pct = ((per_cell - cell_min) / span * 100.0) if span > 0 else 0.0
        else:
            pct = self._lipo_percent(per_cell, cell_min, cell_max)
        pct = max(0.0, min(100.0, pct))

        return {
            'present': True,
            'voltage': round(voltage, 2),
            'per_cell': round(per_cell, 2),
            'cells': cells,
            'percent': int(round(pct)),
            'capacity_mah': capacity,
            'mah_remaining': int(round(capacity * pct / 100.0)),
            'current': round(current, 2) if current is not None else None,
            'power': round(power, 1) if power is not None else None,
            'source': used_source,
            'consumed': consumed,
        }

    # ------------------------------------------------------------- payload/loop

    def _build_payload(self):
        demo = self._opt_bool(OPT_DEMO)
        readings = self._demo_readings() if demo else self._read_all()
        battery = self._compute_battery(readings)

        # Metrics = every reading except those already shown in the battery tile
        # (its voltage source plus that sensor's current/power).
        consumed = set(battery.pop('consumed', []) or [])
        metrics = [r for r in readings
                   if '{}:{}'.format(r['sensor'], r['reading']) not in consumed]

        return {
            'battery': battery,
            'metrics': metrics,
            'network': self._network_info(),
            'system': self._demo_system() if demo else self._system_info(),
            'demo': demo,
            'theme': self._opt(OPT_THEME, 'dark'),
        }

    # ---------------------------------------------------------------- system

    def _system_info(self):
        '''Raspberry Pi load: CPU %, RAM usage, and free disk space.

        Pure-stdlib readers (/proc + os.statvfs) so no extra dependency such as
        psutil is needed. Each sub-reader degrades gracefully: a field is simply
        omitted (and its tile hidden) when it cannot be read on this platform.
        '''
        info = {}
        cpu = self._cpu_percent()
        if cpu is not None:
            info['cpu_percent'] = cpu
        try:
            la1, la5, la15 = os.getloadavg()
            info['load'] = [round(la1, 2), round(la5, 2), round(la15, 2)]
        except (OSError, AttributeError):
            pass  # not available on Windows dev machine
        mem = self._mem_info()
        if mem:
            info['mem'] = mem
        disk = self._disk_info()
        if disk:
            info['disk'] = disk
        return info

    def _cpu_percent(self):
        '''Whole-CPU busy % measured between successive calls via /proc/stat.

        Returns None on the very first call (no previous sample to diff) and on
        platforms without /proc; the tile stays hidden until a delta exists.
        '''
        try:
            with open('/proc/stat') as f:
                line = f.readline()
        except Exception:
            return None
        parts = line.split()
        if not parts or parts[0] != 'cpu':
            return None
        try:
            vals = [float(x) for x in parts[1:]]
        except ValueError:
            return None
        idle = vals[3] + (vals[4] if len(vals) > 4 else 0.0)  # idle + iowait
        total = sum(vals)
        prev = self._cpu_prev
        self._cpu_prev = (total, idle)
        if prev is None:
            return None
        d_total = total - prev[0]
        d_idle = idle - prev[1]
        if d_total <= 0:
            return None
        return round((1.0 - d_idle / d_total) * 100.0, 1)

    def _mem_info(self):
        '''RAM usage from /proc/meminfo (uses MemAvailable when present).'''
        try:
            info = {}
            with open('/proc/meminfo') as f:
                for line in f:
                    key, _, rest = line.partition(':')
                    info[key.strip()] = rest
            total_kb = int(info['MemTotal'].split()[0])
            if 'MemAvailable' in info:
                avail_kb = int(info['MemAvailable'].split()[0])
            else:  # older kernels: approximate available memory
                avail_kb = sum(int(info[k].split()[0])
                               for k in ('MemFree', 'Buffers', 'Cached')
                               if k in info)
            used_kb = max(0, total_kb - avail_kb)
            return {
                'total_mb': int(round(total_kb / 1024.0)),
                'used_mb': int(round(used_kb / 1024.0)),
                'percent': round(used_kb / total_kb * 100.0, 1) if total_kb else 0.0,
            }
        except Exception:
            return None

    def _disk_info(self, path='/'):
        '''Free/used disk space for the filesystem holding `path`.'''
        try:
            st = os.statvfs(path)
            total = st.f_blocks * st.f_frsize
            free = st.f_bavail * st.f_frsize
            used = total - free
        except (AttributeError, OSError):
            # Windows dev machine (no statvfs): fall back to shutil.
            try:
                import shutil
                target = path if os.path.exists(path) else 'C:\\'
                total, used, free = shutil.disk_usage(target)
            except Exception:
                return None
        if not total:
            return None
        gb = 1024.0 ** 3
        return {
            'total_gb': round(total / gb, 1),
            'free_gb': round(free / gb, 1),
            'used_gb': round(used / gb, 1),
            'percent': round(used / total * 100.0, 1),
        }

    def _demo_system(self):
        '''Simulated Pi load for previewing the bar without hardware.'''
        t = monotonic() - self._started_at
        wob = math.sin(t / 5.0)
        cpu = max(2.0, min(99.0, 38.0 + wob * 22.0))
        used_mb = int(round(430 + wob * 90))
        total_mb = 1024
        return {
            'cpu_percent': round(cpu, 1),
            'load': [round(1.1 + wob * 0.4, 2), 1.05, 0.92],
            'mem': {
                'total_mb': total_mb,
                'used_mb': used_mb,
                'percent': round(used_mb / total_mb * 100.0, 1),
            },
            'disk': {'total_gb': 29.7, 'free_gb': 18.4,
                     'used_gb': 11.3, 'percent': 38.0},
        }

    # --------------------------------------------------------------- network

    def _network_info(self):
        '''Active interfaces + IPs. Cached briefly since it rarely changes.'''
        now = monotonic()
        if self._net_cache is not None and (now - self._net_cache_at) < 15:
            return self._net_cache
        info = self._detect_network()
        self._net_cache = info
        self._net_cache_at = now
        return info

    def _iface_ip(self, iface):
        '''IPv4 for a named interface via SIOCGIFADDR (Linux only).'''
        try:
            import fcntl
            import struct
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                packed = struct.pack('256s', iface[:15].encode('utf-8'))
                return socket.inet_ntoa(fcntl.ioctl(s.fileno(), 0x8915, packed)[20:24])
            finally:
                s.close()
        except Exception:
            return None

    def _primary_ip(self):
        '''Best-guess primary IP (works cross-platform, incl. dev machine).'''
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                s.connect(('8.8.8.8', 80))
                return s.getsockname()[0]
            finally:
                s.close()
        except Exception:
            return None

    def _detect_network(self):
        base = '/sys/class/net'
        try:
            if os.path.isdir(base):
                info = {'type': None, 'ifaces': [], 'primary_ip': None}
                wifi = eth = False
                for iface in sorted(os.listdir(base)):
                    if iface == 'lo':
                        continue
                    try:
                        with open(os.path.join(base, iface, 'operstate')) as f:
                            if f.read().strip() != 'up':
                                continue
                    except Exception:
                        continue
                    is_wifi = os.path.exists(os.path.join(base, iface, 'wireless')) \
                        or os.path.exists(os.path.join(base, iface, 'phy80211'))
                    ip = self._iface_ip(iface)
                    if not ip:
                        continue
                    info['ifaces'].append({
                        'name': iface,
                        'type': 'wifi' if is_wifi else 'ethernet',
                        'ip': ip,
                    })
                    if is_wifi:
                        wifi = True
                    else:
                        eth = True
                if wifi and eth:
                    info['type'] = 'both'
                elif wifi:
                    info['type'] = 'wifi'
                elif eth:
                    info['type'] = 'ethernet'
                if info['ifaces']:
                    info['primary_ip'] = info['ifaces'][0]['ip']
                    return info
        except Exception:
            logger.debug('Network detection via sysfs failed', exc_info=True)

        # Fallback (e.g. Windows dev machine): single primary IP.
        ip = self._primary_ip()
        if ip:
            return {'type': 'ethernet',
                    'ifaces': [{'name': 'net', 'type': 'ethernet', 'ip': ip}],
                    'primary_ip': ip}
        return {'type': None, 'ifaces': [], 'primary_ip': None}

    def on_request(self, _data=None):
        try:
            self._rhapi.ui.socket_send('topbar_data', self._build_payload())
        except Exception:
            logger.exception('Failed to answer topbar_request')

    def _loop(self):
        while True:
            interval = max(1, self._opt_int(OPT_INTERVAL, 2))
            try:
                self._rhapi.ui.socket_broadcast('topbar_data', self._build_payload())
            except Exception:
                logger.exception('Sensor Top Bar broadcast failed')
            gevent.sleep(interval)
