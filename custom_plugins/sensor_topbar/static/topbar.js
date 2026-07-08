/* Sensor Top Bar - front-end.
 *
 * Injects a compact telemetry bar at the top of every RotorHazard page and
 * keeps it updated from the server's `topbar_data` Socket.IO broadcasts.
 *
 * Groups (separated by dividers): Network | Core temperature | Battery | Climate.
 * Network and Battery expose extra detail via a hover/click popover.
 */
(function () {
	'use strict';

	if (window.__rhSensorTopbar) { return; }       // load-once guard (script may be injected per panel)
	window.__rhSensorTopbar = true;
	if (typeof io === 'undefined') { return; }     // socket.io not loaded on this page

	// Load our stylesheet ourselves so no core template edit is needed.
	function ensureCss() {
		if (document.getElementById('rh-topbar-css')) { return; }
		var l = document.createElement('link');
		l.id = 'rh-topbar-css';
		l.rel = 'stylesheet';
		l.href = '/sensor_topbar/static/topbar.css';
		(document.head || document.documentElement).appendChild(l);
	}

	var ICONS = {
		temp: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 14.76V5a2 2 0 0 0-4 0v9.76a4 4 0 1 0 4 0z"/></svg>',
		cpu: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="7" y="7" width="10" height="10" rx="1.5"/><path d="M9 2v3M15 2v3M9 19v3M15 19v3M2 9h3M2 15h3M19 9h3M19 15h3"/></svg>',
		pressure: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 21a9 9 0 1 0-9-9"/><line x1="12" y1="12" x2="16" y2="8"/></svg>',
		humidity: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2.7s6 5.5 6 10a6 6 0 0 1-12 0c0-4.5 6-10 6-10z"/></svg>',
		voltage: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M13 2L3 14h7l-1 8 10-12h-7l1-8z"/></svg>',
		current: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 12 8 12 11 4 15 20 18 12 21 12"/></svg>',
		power: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18.36 6.64a9 9 0 1 1-12.73 0"/><line x1="12" y1="2" x2="12" y2="12"/></svg>',
		gauge: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="1.5" fill="currentColor"/><line x1="12" y1="12" x2="16" y2="9"/></svg>',
		wifi: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M2 8.8a15 15 0 0 1 20 0"/><path d="M5 12.5a10 10 0 0 1 14 0"/><path d="M8.5 16a5 5 0 0 1 7 0"/><circle cx="12" cy="19.5" r="1" fill="currentColor" stroke="none"/></svg>',
		ethernet: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="8" width="18" height="9" rx="1"/><path d="M7 8V6M12 8V6M17 8V6"/></svg>',
		both: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/></svg>',
		ram: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="7" width="20" height="10" rx="1.5"/><path d="M6 17v3M10 17v3M14 17v3M18 17v3M7 10.5v3M12 10.5v3M17 10.5v3"/></svg>',
		disk: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="6" width="20" height="5" rx="1.5"/><rect x="2" y="13" width="20" height="5" rx="1.5"/><circle cx="6" cy="8.5" r="0.8" fill="currentColor" stroke="none"/><circle cx="6" cy="15.5" r="0.8" fill="currentColor" stroke="none"/></svg>',
		more: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="6 9 12 15 18 9"/></svg>'
	};

	// Single shared detail popover; `pinnedKey` survives the periodic re-render.
	var pinnedKey = null;
	var popEl = null;

	function categorize(reading, units) {
		var r = String(reading || '').toLowerCase();
		var u = String(units || '').toLowerCase();
		if (u.indexOf('°c') >= 0 || u === 'c' || u === '°f' || r.indexOf('temp') >= 0) return 'temp';
		if (u.indexOf('pa') >= 0 || u.indexOf('bar') >= 0 || r.indexOf('press') >= 0) return 'pressure';
		if (u.indexOf('rh') >= 0 || u.indexOf('humid') >= 0 || r.indexOf('humid') >= 0) return 'humidity';
		if (u === 'v' || r.indexOf('volt') >= 0) return 'voltage';
		if (u === 'a' || u === 'ma' || r.indexOf('current') >= 0) return 'current';
		if (u === 'w' || u === 'mw' || u === 'ah' || r.indexOf('power') >= 0) return 'power';
		return 'gauge';
	}

	function isCoreTemp(m) {
		return categorize(m.reading, m.units) === 'temp' &&
			String(m.sensor || '').toLowerCase().indexOf('core') >= 0;
	}

	// Order within the climate group: temperature, humidity, pressure, then rest.
	function climateRank(m) {
		var cat = categorize(m.reading, m.units);
		if (cat === 'temp') return 0;
		if (cat === 'humidity') return 1;
		if (cat === 'pressure') return 2;
		return 3;
	}

	function shortReading(reading) {
		var r = String(reading || '');
		var map = { temperature: 'Temp', pressure: 'Pressure', humidity: 'Humidity',
			voltage: 'Voltage', current: 'Current', power: 'Power', capacity: 'Capacity' };
		var key = r.toLowerCase();
		return map[key] || (r.charAt(0).toUpperCase() + r.slice(1));
	}

	// Avoid repeating the sensor name (e.g. "Climate") on well-known readings.
	function metricLabel(m) {
		if (isCoreTemp(m)) return 'Core';
		var cat = categorize(m.reading, m.units);
		if (cat === 'temp' || cat === 'humidity' || cat === 'pressure') return shortReading(m.reading);
		return (m.sensor ? m.sensor + ' ' : '') + shortReading(m.reading);
	}

	function fmt(value, units) {
		var n = parseFloat(value);
		if (!isFinite(n)) return String(value);
		var u = String(units || '').toLowerCase();
		var dec;
		if (u === 'v') dec = 2;
		else if (u.indexOf('rh') >= 0 || u.indexOf('pa') >= 0 || u === 'mw' || u === 'ma' || Math.abs(n) >= 100) dec = 0;
		else dec = 1;
		return n.toFixed(dec);
	}

	function batColor(pct) {
		if (pct <= 20) return '#ff5252';
		if (pct <= 50) return '#f5a623';
		return '#3ddc84';
	}

	// Load colour: green normally, amber past `warn`, red past `crit`.
	function loadColor(pct, warn, crit) {
		if (pct >= crit) return '#ff5252';
		if (pct >= warn) return '#f5a623';
		return '#3ddc84';
	}

	function el(tag, cls, html) {
		var e = document.createElement(tag);
		if (cls) e.className = cls;
		if (html != null) e.innerHTML = html;
		return e;
	}

	function ensureBar() {
		var bar = document.getElementById('rh-topbar');
		if (bar) return bar;
		bar = el('div', null);
		bar.id = 'rh-topbar';
		if (document.body.firstChild) {
			document.body.insertBefore(bar, document.body.firstChild);
		} else {
			document.body.appendChild(bar);
		}
		return bar;
	}

	// ------------------------------------------------------------- popover core

	function hidePopover() {
		if (popEl) { popEl.parentNode && popEl.parentNode.removeChild(popEl); popEl = null; }
	}

	function showPopover(anchor, contentNode) {
		hidePopover();
		popEl = el('div', 'rh-tb-popover');
		popEl.appendChild(contentNode);
		document.body.appendChild(popEl);
		var r = anchor.getBoundingClientRect();
		var left = Math.min(r.left, window.innerWidth - popEl.offsetWidth - 8);
		popEl.style.top = (r.bottom + 6) + 'px';
		popEl.style.left = Math.max(8, left) + 'px';
	}

	// rows: array of [name, valueHtml]; left-aligned, every value labelled.
	function popContent(title, rows) {
		var box = el('div');
		box.appendChild(el('div', 'rh-tb-pop-title', title));
		rows.forEach(function (row) {
			var r = el('div', 'rh-tb-pop-row');
			r.appendChild(el('span', 'rh-tb-pop-key', row[0]));
			r.appendChild(el('span', 'rh-tb-pop-val', row[1]));
			box.appendChild(r);
		});
		return box;
	}

	// Adds a "show more" chevron + hover/click popover to a tile.
	function attachPopover(tileEl, key, buildFn) {
		var more = el('span', 'rh-tb-more', ICONS.more);
		more.setAttribute('title', 'Show details');
		tileEl.appendChild(more);

		tileEl.addEventListener('mouseenter', function () { showPopover(tileEl, buildFn()); });
		tileEl.addEventListener('mouseleave', function () { if (pinnedKey !== key) hidePopover(); });
		more.addEventListener('click', function (e) {
			e.stopPropagation();
			if (pinnedKey === key) { pinnedKey = null; hidePopover(); }
			else { pinnedKey = key; showPopover(tileEl, buildFn()); }
		});

		// Re-open after a re-render so a click-pinned popover stays visible.
		if (pinnedKey === key) {
			setTimeout(function () { showPopover(tileEl, buildFn()); }, 0);
		}
	}

	// ------------------------------------------------------------------- tiles

	function tile(iconKey, label, valueHtml, extraClass) {
		var t = el('div', 'rh-tb-tile' + (extraClass ? ' ' + extraClass : ''));
		t.appendChild(el('div', 'rh-tb-icon', ICONS[iconKey] || ICONS.gauge));
		var text = el('div', 'rh-tb-text');
		if (label) text.appendChild(el('div', 'rh-tb-label', label));
		text.appendChild(el('div', 'rh-tb-value', valueHtml));
		t.appendChild(text);
		return t;
	}

	function metricTile(m) {
		var cat = categorize(m.reading, m.units);
		var iconKey = isCoreTemp(m) ? 'cpu' : cat;
		var valueHtml = fmt(m.value, m.units) +
			(m.units ? '<span class="rh-tb-unit">' + m.units + '</span>' : '');
		return tile(iconKey, metricLabel(m), valueHtml, isCoreTemp(m) ? 'rh-tb-core' : null);
	}

	function batteryTile(b) {
		var color = batColor(b.percent);
		var t = el('div', 'rh-tb-tile rh-tb-battery');

		var glyph = el('div', 'rh-tb-batglyph');
		var fill = el('div', 'rh-tb-batfill');
		fill.style.width = Math.max(0, Math.min(100, b.percent)) + '%';
		fill.style.background = color;
		glyph.appendChild(fill);
		t.appendChild(glyph);

		var text = el('div', 'rh-tb-text');
		text.appendChild(el('div', 'rh-tb-value',
			'<span style="color:' + color + '">' + b.percent + '%</span> ' +
			b.voltage + '<span class="rh-tb-unit">V</span>'));
		t.appendChild(text);

		attachPopover(t, 'bat', function () {
			var rows = [['Charge', b.percent + '%'], ['Voltage', b.voltage + ' V']];
			if (b.cells) rows.push(['Cells', b.cells + 'S']);
			if (b.per_cell) rows.push(['Per cell', b.per_cell.toFixed(2) + ' V']);
			if (b.current != null) rows.push(['Current', b.current.toFixed(2) + ' A']);
			if (b.power != null) rows.push(['Power', b.power.toFixed(0) + ' W']);
			if (b.mah_remaining != null && b.capacity_mah)
				rows.push(['Capacity', b.mah_remaining + ' / ' + b.capacity_mah + ' mAh']);
			return popContent('Battery', rows);
		});
		return t;
	}

	function networkTile(net) {
		var type = net.type || 'ethernet';
		var iconKey = type === 'wifi' ? 'wifi' : (type === 'both' ? 'both' : 'ethernet');
		var label = type === 'wifi' ? 'Wi-Fi' : (type === 'both' ? 'Eth + Wi-Fi' : 'Ethernet');

		var t = el('div', 'rh-tb-tile rh-tb-net');
		t.appendChild(el('div', 'rh-tb-icon', ICONS[iconKey]));
		var text = el('div', 'rh-tb-text');
		text.appendChild(el('div', 'rh-tb-label', 'Network'));
		text.appendChild(el('div', 'rh-tb-value', label));
		t.appendChild(text);

		attachPopover(t, 'net', function () {
			var ifaces = net.ifaces || [];
			var rows = ifaces.map(function (i) {
				var kind = i.type === 'wifi' ? 'Wi-Fi' : 'Ethernet';
				return [kind, i.ip + '<span class="rh-tb-pop-sub">' + i.name + '</span>'];
			});
			if (!rows.length) rows = [['Status', 'No address']];
			return popContent('Network', rows);
		});
		return t;
	}

	// Raspberry Pi load: CPU %, RAM %, free disk. Each tile carries a detail
	// popover and is only built when the server supplied that reading.
	function systemTiles(sys) {
		var tiles = [];

		if (sys.cpu_percent != null) {
			var c = sys.cpu_percent;
			var ct = tile('cpu', 'CPU',
				'<span style="color:' + loadColor(c, 70, 90) + '">' +
				Math.round(c) + '</span><span class="rh-tb-unit">%</span>');
			attachPopover(ct, 'sys-cpu', function () {
				var rows = [['Usage', Math.round(c) + ' %']];
				if (sys.load && sys.load.length) {
					rows.push(['Load avg', sys.load.map(function (n) {
						return Number(n).toFixed(2); }).join('  ')]);
				}
				return popContent('CPU', rows);
			});
			tiles.push(ct);
		}

		if (sys.mem && sys.mem.percent != null) {
			var m = sys.mem;
			var mt = tile('ram', 'RAM',
				'<span style="color:' + loadColor(m.percent, 75, 90) + '">' +
				Math.round(m.percent) + '</span><span class="rh-tb-unit">%</span>');
			attachPopover(mt, 'sys-mem', function () {
				return popContent('Memory', [
					['Used', m.used_mb + ' MB'],
					['Total', m.total_mb + ' MB'],
					['Usage', Math.round(m.percent) + ' %']
				]);
			});
			tiles.push(mt);
		}

		if (sys.disk && sys.disk.free_gb != null) {
			var d = sys.disk;
			var dt = tile('disk', 'Disk',
				'<span style="color:' + loadColor(d.percent, 80, 92) + '">' +
				d.free_gb + '</span><span class="rh-tb-unit">GB free</span>');
			attachPopover(dt, 'sys-disk', function () {
				return popContent('Disk', [
					['Free', d.free_gb + ' GB'],
					['Used', d.used_gb + ' GB'],
					['Total', d.total_gb + ' GB'],
					['Usage', Math.round(d.percent) + ' %']
				]);
			});
			tiles.push(dt);
		}

		return tiles;
	}

	// ------------------------------------------------------------------- render

	function render(bar, data) {
		bar.innerHTML = '';

		bar.appendChild(el('div', 'rh-tb-brand', ICONS.gauge + '<span>Telemetry</span>'));
		if (data && data.demo) bar.appendChild(el('div', 'rh-tb-demo', 'Demo'));

		// Partition metrics into core temperature vs climate.
		var metrics = (data && data.metrics) || [];
		var core = [], climate = [];
		for (var i = 0; i < metrics.length; i++) {
			if (metrics[i].value == null) continue;
			if (isCoreTemp(metrics[i])) core.push(metrics[i]);
			else climate.push(metrics[i]);
		}
		climate.sort(function (a, b) { return climateRank(a) - climateRank(b); });

		// Build the four groups in order.
		var groups = [];
		if (data && data.network && (data.network.type || (data.network.ifaces || []).length)) {
			groups.push([networkTile(data.network)]);
		}
		if (core.length) groups.push(core.map(metricTile));
		if (data && data.system) {
			var sysTiles = systemTiles(data.system);
			if (sysTiles.length) groups.push(sysTiles);
		}
		if (data && data.battery && data.battery.present) groups.push([batteryTile(data.battery)]);
		if (climate.length) groups.push(climate.map(metricTile));

		if (!groups.length) {
			bar.appendChild(el('div', 'rh-tb-empty', 'No sensors detected — enable Demo mode in Settings to preview.'));
			return;
		}

		groups.forEach(function (tiles, gi) {
			if (gi > 0) bar.appendChild(el('div', 'rh-tb-sep'));
			tiles.forEach(function (tl) { bar.appendChild(tl); });
		});
	}

	function start() {
		ensureCss();
		var bar = ensureBar();
		var socket = io.connect(location.protocol + '//' + document.domain + ':' + location.port);
		socket.on('connect', function () { socket.emit('topbar_request', {}); });
		socket.on('topbar_data', function (data) { render(bar, data); });
		// React to the core's periodic environmental broadcast for freshness.
		socket.on('environmental_data', function () { socket.emit('topbar_request', {}); });
		// Click anywhere else closes a pinned popover.
		document.addEventListener('click', function () {
			if (pinnedKey !== null) { pinnedKey = null; hidePopover(); }
		});
	}

	if (document.readyState === 'loading') {
		document.addEventListener('DOMContentLoaded', start);
	} else {
		start();
	}
})();
