// UI glue: toolbar, properties panel, console, and calls to the backend.

const $ = id => document.getElementById(id);
const POLL_MS = 3000;

let isRoot = true;      // backend runs as root (needed for Run)
let busy = false;       // a network request to Mininet is in flight

// ---- console -------------------------------------------------------------

function notify(message, cls) {
  const out = $('console-out');
  const line = document.createElement('div');
  if (cls) line.className = cls;
  line.textContent = message;
  out.appendChild(line);
  out.scrollTop = out.scrollHeight;
}

// Call the backend. GET when body is undefined, otherwise POST JSON.
// Throws an Error (with .errors for topology problems) on failure.
async function api(path, body) {
  const opts = body === undefined ? {} : {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  };
  const res = await fetch(path, opts);
  const data = await res.json().catch(() => ({ error: res.status + ' ' + res.statusText }));
  if (!res.ok || data.error) {
    const err = new Error(data.error || 'Request failed');
    err.errors = data.errors || [];
    throw err;
  }
  return data;
}

function showError(e) {
  notify('Error: ' + e.message, 'err');
  (e.errors || []).forEach(msg => notify('  - ' + msg, 'err'));
}

function showWarnings(warnings) {
  (warnings || []).forEach(w => notify('Warning: ' + w, 'warn'));
}

// Run a backend action that uses Mininet, with the UI marked busy.
async function mininetAction(label, path, body) {
  if (busy) return notify('Busy, wait for the previous command to finish', 'warn');
  busy = true;
  updateControls();
  notify('$ ' + label, 'cmd');
  try {
    const data = await api(path, body);
    if (data.output !== undefined) notify(data.output.trimEnd() || '(no output)');
    return data;
  } catch (e) {
    showError(e);
  } finally {
    busy = false;
    updateControls();
  }
}

// ---- toolbar ---------------------------------------------------------------

function setTool(tool) {
  state.tool = tool;
  state.linkStart = null;
  $('rubber-band').setAttribute('visibility', 'hidden');
  document.querySelectorAll('#sidebar [data-tool]').forEach(b =>
    b.classList.toggle('active', b.dataset.tool === tool));
  render();
}

document.querySelectorAll('#sidebar [data-tool]').forEach(b =>
  b.addEventListener('click', () => setTool(b.dataset.tool)));

// File name shown in the title bar and used for Save/Export, without extension.
let fileName = 'topology';

function setFileName(name) {
  fileName = name.replace(/\.(mn|json|py)$/i, '') || 'topology';
  $('file-name').textContent = fileName;
  document.title = fileName + ' - Mininet UI';
}

function download(filename, content, type) {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 60000);  // after the download started
}

$('btn-new').addEventListener('click', () => {
  if (state.running) return notify('Stop the network first', 'warn');
  if (state.topo.nodes.length && !confirm('Discard the current topology?')) return;
  setTopology({ version: TOPOLOGY_VERSION, nodes: [], links: [] });
  setFileName('topology');
  onTopologyChanged();
});

$('btn-open').addEventListener('click', () => $('file-input').click());

// Network settings live in the Properties panel when nothing is selected.
$('btn-settings').addEventListener('click', () => {
  setTool('select');
  select(null);
  $('props').scrollTop = 0;
});

$('file-input').addEventListener('change', async evt => {
  const file = evt.target.files[0];
  evt.target.value = '';          // allow re-opening the same file
  if (!file) return;
  if (state.running) return notify('Stop the network first', 'warn');
  try {
    const notes = [];
    setTopology(parseTopologyFile(await file.text(), notes));
    setFileName(file.name);
    onTopologyChanged();
    notify('Opened ' + file.name, 'ok');
    notes.forEach(n => notify('Note: ' + n, 'warn'));
  } catch (e) {
    showError(e);
  }
});

// Save as .mn (MiniEdit's format, opens in MiniEdit too) unless the user
// types a name ending in .json.
// In the desktop app (desktop/window.py) files are saved through a native
// Save dialog: we send it the content for each allowed extension, and it
// calls appSaveDone(path) back (path is null if the user cancelled).
const desktopApp = window.webkit && window.webkit.messageHandlers &&
                   window.webkit.messageHandlers.mininetui;
let onAppSaved = null;

window.appSaveDone = path => {
  if (path && onAppSaved) onAppSaved(path);
  onAppSaved = null;
};

function saveInApp(name, files, done) {
  onAppSaved = done;
  desktopApp.postMessage(JSON.stringify({ action: 'save', name, files }));
}

$('btn-save').addEventListener('click', () => {
  const mn = JSON.stringify(toMiniEdit(state.topo), null, 2);
  const json = JSON.stringify(state.topo, null, 2);
  if (desktopApp) {
    return saveInApp(fileName + '.mn', { '.mn': mn, '.json': json }, path => {
      setFileName(path.split('/').pop());
      notify('Saved ' + path, 'ok');
    });
  }
  const name = prompt('Save as (.mn = MiniEdit format, or .json):', fileName + '.mn');
  if (!name) return;
  const file = /\.(mn|json)$/i.test(name) ? name : name + '.mn';
  const isJson = file.toLowerCase().endsWith('.json');
  download(file, isJson ? json : mn, 'application/json');
  setFileName(file);
  notify('Saved ' + file, 'ok');
});

$('btn-export').addEventListener('click', async () => {
  try {
    const data = await api('/api/export', state.topo);
    if (desktopApp) {
      return saveInApp(fileName + '.py', { '.py': data.script }, path =>
        notify(`Exported ${path} (run it with: sudo python3 ${path})`, 'ok'));
    }
    download(fileName + '.py', data.script, 'text/x-python');
    notify(`Exported ${fileName}.py (run it with: sudo python3 ${fileName}.py)`, 'ok');
  } catch (e) {
    showError(e);
  }
});

$('btn-run').addEventListener('click', async () => {
  notify('Starting network (this takes a few seconds)...');
  const data = await mininetAction('start network', '/api/start', state.topo);
  if (data) {
    state.running = true;
    showWarnings(data.warnings);
    notify('Network running: ' + data.hosts.length + ' hosts, ' +
           data.switches.length + ' switches', 'ok');
    pollStatus();       // show the controller connection status right away
  }
  updateControls();
});

$('btn-stop').addEventListener('click', async () => {
  notify('Stopping the network (Mininet removes every link, switch and PC)...');
  const data = await mininetAction('stop network', '/api/stop', {});
  if (data) {
    state.running = false;
    notify(`Network stopped (${data.seconds} s)`, 'ok');
  }
  updateControls();
});

function updateControls() {
  $('btn-run').disabled = state.running || busy || !isRoot;
  $('btn-run').title = isRoot ? 'Start the network in Mininet'
                              : 'Backend is not running as root (sudo)';
  $('btn-stop').disabled = !state.running || busy;
  const badge = $('net-status');
  badge.textContent = busy ? 'working...' : state.running ? 'running' : 'stopped';
  badge.className = 'badge' + (busy ? ' busy' : state.running ? ' running' : '');
  updateControllerBadge();
  ['btn-cmd', 'btn-ping', 'btn-iperf', 'btn-pingall'].forEach(id => {
    $(id).disabled = !state.running || busy;
  });
  renderProps();
}

// ---- properties panel ------------------------------------------------------

// Settings shown in the Properties panel for each kind of item. Each field:
//   [key, label, options]   options: { type: 'select' | 'check', choices,
//                                      placeholder, more: true = under "More options" }
// The keys are Mininet's own parameter names (see backend/topology.py).
const FIELDS = {
  host: [
    ['name', 'Name'],
    ['ip', 'IP address (CIDR)'],
    ['mac', 'MAC address'],
    ['defaultRoute', 'Default route', { more: true, placeholder: 'e.g. via 10.0.0.254' }],
    ['cpu', 'CPU limit (fraction of total, 0.01 - 1)', { more: true, placeholder: 'blank = no limit' }],
    ['sched', 'CPU scheduler (with a CPU limit)', { more: true, type: 'select',
      choices: [['cfs', 'cfs (fair share)'], ['rt', 'rt (real-time)']] }],
    ['cores', 'Run only on CPU cores', { more: true, placeholder: 'e.g. 0 or 0-1' }],
    ['startCommand', 'Start command (runs after Run)', { more: true,
      placeholder: 'e.g. python3 -m http.server 80 &' }],
    ['stopCommand', 'Stop command (runs before Stop)', { more: true }],
  ],
  switch: [
    ['name', 'Name'],
    ['dpid', 'DPID (16 hex digits)'],
    ['protocols', 'OpenFlow version', { more: true, type: 'select',
      choices: [['', 'Network default (see Network settings)'], 'OpenFlow10', 'OpenFlow11',
                'OpenFlow12', 'OpenFlow13', 'OpenFlow14', 'OpenFlow15'] }],
    ['failMode', 'Fail mode (when the controller is unreachable)', { more: true, type: 'select',
      choices: [['secure', 'secure: drop traffic'], ['standalone', 'standalone: act as a normal switch']] }],
    ['datapath', 'Datapath', { more: true, type: 'select',
      choices: [['kernel', 'kernel (fast, default)'], ['user', 'user space']] }],
    ['stp', 'Spanning Tree Protocol (for loops)', { more: true, type: 'check' }],
    ['listenPort', 'Listen port for ovs-ofctl', { more: true, placeholder: 'e.g. 6654, blank = none' }],
  ],
  controller: [
    ['name', 'Name'],
    ['ip', 'Controller IP'],
    ['port', 'OpenFlow port'],
    ['protocol', 'Connection', { more: true, type: 'select',
      choices: [['tcp', 'tcp'], ['ssl', 'ssl (needs certificates)']] }],
  ],
  // Whole-network settings (like MiniEdit's Preferences), shown when nothing
  // is selected. ['-', text] is a section heading.
  network: [
    ['-', 'OpenFlow'],
    ['openFlowVersions', 'OpenFlow versions the switches speak', { type: 'checks',
      choices: ['OpenFlow10', 'OpenFlow11', 'OpenFlow12', 'OpenFlow13', 'OpenFlow14', 'OpenFlow15'],
      fallback: ['OpenFlow13'] }],
    ['listenPort', 'dpctl listen port (first switch; the next ones +1)',
      { placeholder: 'e.g. 6654, blank = none' }],
    ['-', 'Addressing'],
    ['ipBase', 'IP base (network for the PCs)', { placeholder: '10.0.0.0/8' }],
    ['autoStaticArp', 'Static ARP (PCs know each other’s MAC address from the start)',
      { type: 'check' }],
    ['-', 'sFlow (traffic sampling, e.g. for sFlow-RT)'],
    ['sflowTarget', 'sFlow collector (ip:port, blank = off)', { placeholder: 'e.g. 127.0.0.1:6343' }],
    ['sflowSampling', 'Sampling rate (1 in N packets)', { placeholder: '400' }],
    ['sflowHeader', 'Header bytes', { placeholder: '128' }],
    ['sflowPolling', 'Counter polling interval (seconds)', { placeholder: '30' }],
    ['sflowAgent', 'Agent interface', { placeholder: 'lo' }],
    ['-', 'NetFlow'],
    ['netflowTarget', 'NetFlow collector (ip:port, blank = off)', { placeholder: 'e.g. 127.0.0.1:2055' }],
    ['netflowTimeout', 'Active timeout (seconds)', { placeholder: '600' }],
    ['netflowAddId', 'Add switch ID to interface', { type: 'check' }],
  ],
  link: [
    ['bw', 'Bandwidth (Mbit/s, blank = unlimited)'],
    ['delay', 'Delay (e.g. 5ms, blank = none)'],
    ['loss', 'Packet loss (%)'],
    ['jitter', 'Jitter (delay variation, e.g. 1ms)', { more: true }],
    ['max_queue_size', 'Queue size (packets)', { more: true, placeholder: 'blank = default' }],
  ],
};

let moreOpen = false;     // keep "More options" open while clicking around

const KIND_TITLES = { host: 'PC', switch: 'Switch', controller: 'Controller' };

// Multi-line text box for a note's text.
function addNoteText(body, note) {
  const wrap = document.createElement('label');
  wrap.textContent = 'Text';
  const area = document.createElement('textarea');
  area.id = 'note-text';
  area.rows = 6;
  area.value = note.text;
  area.addEventListener('input', () => {
    note.text = area.value;
    render();
    persist();
  });
  wrap.appendChild(area);
  body.appendChild(wrap);
}

// Put the cursor in the selected note's text box, with the text selected.
function focusNoteText() {
  const area = $('note-text');
  if (area) { area.focus(); area.select(); }
}

function addField(body, obj, key, label, opts = {}) {
  const wrap = document.createElement('label');
  const changed = value => {
    obj[key] = value;
    render();
    persist();
    if (key === 'name') updateHostLists();
  };
  let input;
  if (opts.type === 'checks') {
    // Several checkboxes; the value is the list of ticked choices. A <div>,
    // not a <label>: clicking a label's caption would tick the first box.
    const group = document.createElement('div');
    group.className = 'field';
    group.textContent = label;
    const box = document.createElement('div');
    box.className = 'checks';
    const ticked = new Set(obj[key] || opts.fallback || []);
    const boxes = opts.choices.map(choice => {
      const item = document.createElement('label');
      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.checked = ticked.has(choice);
      cb.disabled = state.running;
      cb.addEventListener('change', () =>
        changed(boxes.filter(b => b.checked).map(b => b.value)));
      cb.value = choice;
      item.append(cb, ' ' + choice.replace('OpenFlow', ''));
      box.appendChild(item);
      return cb;
    });
    group.appendChild(box);
    body.appendChild(group);
    return;
  }
  if (opts.type === 'check') {
    wrap.className = 'check';
    input = document.createElement('input');
    input.type = 'checkbox';
    input.checked = !!obj[key];
    input.addEventListener('change', () => changed(input.checked));
    wrap.append(input, ' ' + label);
  } else {
    wrap.textContent = label;
    if (opts.type === 'select') {
      input = document.createElement('select');
      for (const c of opts.choices) {
        const [value, text] = Array.isArray(c) ? c : [c, c];
        input.add(new Option(text, value));
      }
      input.value = obj[key] || input.options[0].value;     // blank = the default
      input.addEventListener('change', () => changed(input.value));
    } else {
      input = document.createElement('input');
      input.value = obj[key] == null ? '' : obj[key];
      if (opts.placeholder) input.placeholder = opts.placeholder;
      input.addEventListener('input', () => changed(input.value.trim()));
    }
    wrap.appendChild(input);
  }
  input.disabled = state.running;
  body.appendChild(wrap);
}

// Basic fields, then the rest in a collapsible "More options" section.
function addFields(body, obj, kind) {
  const fields = FIELDS[kind];
  fields.filter(f => !(f[2] || {}).more).forEach(([key, label, opts]) => {
    if (key !== '-') return addField(body, obj, key, label, opts);
    const h = document.createElement('h3');
    h.textContent = label;
    body.appendChild(h);
  });
  const more = fields.filter(f => (f[2] || {}).more);
  if (!more.length) return;
  const details = document.createElement('details');
  details.open = moreOpen;
  details.addEventListener('toggle', () => { moreOpen = details.open; });
  const summary = document.createElement('summary');
  summary.textContent = 'More options';
  details.appendChild(summary);
  more.forEach(([key, label, opts]) => addField(details, obj, key, label, opts));
  body.appendChild(details);
}

function addInfo(body, cls, lines) {
  const div = document.createElement('div');
  div.className = cls;
  div.innerText = lines.join('\n');
  body.appendChild(div);
  return div;
}

// Extra details from the controller for the selected switch or host.
function liveInfo(node) {
  const o = state.live;
  if (node.type === 'controller') {
    const names = switchesOf(node).map(s => s.name);
    if (!names.length) return ['No switches linked to this controller yet.'];
    if (!state.running) return ['Linked switches: ' + names.join(', ')];
    if (!names.some(n => n in state.connected)) return ['Checking connections...'];
    const up = names.filter(n => state.connected[n]), down = names.filter(n => !state.connected[n]);
    const lines = [down.length ? `Connected: ${up.length} of ${names.length} switches`
                               : 'Connected to all its switches'];
    if (up.length) lines.push('Connected: ' + up.join(', '));
    if (down.length) lines.push('Not connected: ' + down.join(', '));
    return lines;
  }
  if (!o || !o.ok) {
    if (node.type !== 'switch' || !state.running) return null;
    if (!(node.name in state.connected)) return ['Not linked to a controller'];
    return [state.connected[node.name] ? 'Connected to the controller'
                                       : 'Not connected to the controller'];
  }
  if (node.type === 'switch') {
    const dpid = dpidOf(node);
    const dev = o.devices.find(d => d.dpid === dpid);
    if (!dev) return [o.label + ': not connected', 'dpid ' + dpid];
    return [o.label + ': ' + (dev.available ? 'available' : 'unavailable'), dev.id,
            'Flow rules: ' + (o.flows[dpid] || 0)];
  }
  if (node.type === 'host') {
    const h = liveHost(node);
    if (!h) return [o.label + ': host not discovered yet', '(send some traffic, e.g. ping)'];
    const sw = dpid => (nodesOfType('switch').find(s => dpidOf(s) === dpid) || {}).name || dpid;
    return [o.label + ': discovered', 'IPs: ' + h.ips.join(', ')]
      .concat(h.locations.map(l => 'at ' + sw(l.dpid) + ' port ' + l.port));
  }
  return null;
}

// Fill the live info box of the properties panel (if there is one).
function renderLive() {
  const box = $('props-live');
  if (!box) return;
  const node = state.selected && findNode(state.selected.id);
  const lines = node && liveInfo(node);
  box.hidden = !lines;
  box.innerText = lines ? lines.join('\n') : '';
}

// The form is only rebuilt when the selection or run state changes, so the
// 3-second polls don't wipe an input while the user is typing in it.
let propsKey = null;

function renderProps() {
  const key = JSON.stringify([state.selected, state.running, busy]);
  if (key === propsKey) return renderLive();
  propsKey = key;
  const body = $('props-body');
  body.replaceChildren();
  const sel = state.selected;
  const find = { node: findNode, link: findLink, note: findNote };
  const obj = sel && find[sel.kind](sel.id);
  const title = $('props').querySelector('h2');
  if (!obj) {
    // Nothing selected: settings for the whole network (MiniEdit's Preferences).
    title.textContent = 'Network settings';
    addInfo(body, 'hint', [
      'Pick a device on the left, then click the workspace to place it. ' +
        'Click a device or link to see its own settings.',
    ]);
    addFields(body, state.topo.settings, 'network');
    if (state.running) addInfo(body, 'hint', ['Stop the network to change settings.']);
    return;
  }
  if (sel.kind === 'note') {
    title.textContent = 'Note';
    addNoteText(body, obj);
    addInfo(body, 'hint', ['Notes are only for people reading the drawing; ' +
                           'Mininet ignores them.']);
    return;
  }
  const kind = sel.kind === 'node' ? obj.type : 'link';
  if (kind === 'link') {
    const name = id => (findNode(id) || { name: '?' }).name;
    title.textContent = 'Link ' + name(obj.src) + ' ↔ ' + name(obj.dst);
    if (isControlLink(obj)) {
      const sw = switchOf(obj);
      title.textContent = 'Control link ' + name(obj.src) + ' ↔ ' + name(obj.dst);
      addInfo(body, 'hint', [
        `${sw ? sw.name : 'The switch'} connects to this controller over OpenFlow ` +
          'when the network runs. Control links have no settings.',
        'Delete the link to disconnect them.',
      ]);
      return;
    }
  } else {
    title.textContent = KIND_TITLES[kind] + ' ' + obj.name;
  }
  addFields(body, obj, kind);
  if (state.running) addInfo(body, 'hint', ['Stop the network to edit properties.']);

  addInfo(body, 'live', []).id = 'props-live';
  renderLive();

  if (kind === 'host' && state.running) {
    const actions = document.createElement('div');
    actions.className = 'actions';
    for (const [label, cmd] of [['ifconfig', 'ifconfig'], ['route', 'route -n'],
                                ['arp', 'arp -n']]) {
      const b = document.createElement('button');
      b.textContent = label;
      b.disabled = busy;
      b.addEventListener('click', () => runCommand(obj.name, cmd));
      actions.appendChild(b);
    }
    body.appendChild(actions);
  }
}

// Called by the canvas when the selection changes.
function onSelectionChanged() {
  const node = state.selected && findNode(state.selected.id);
  if (node && node.type === 'host') $('cmd-node').value = node.name;
  render();
  renderProps();
}

// Called whenever nodes or links are added/removed.
function onTopologyChanged() {
  propsKey = null;        // node/link objects may have been replaced
  persist();
  render();
  renderProps();
  updateHostLists();
}

// ---- console bar: commands on hosts ----------------------------------------

function updateHostLists() {
  const names = nodesOfType('host').map(h => h.name);
  for (const id of ['cmd-node', 'cmd-target']) {
    const select = $(id);
    const old = select.value;
    select.replaceChildren(...names.map(n => new Option(n, n)));
    if (names.includes(old)) select.value = old;
  }
  // Default the target to a different host than the source.
  if ($('cmd-target').value === $('cmd-node').value && names.length > 1) {
    $('cmd-target').value = names.find(n => n !== $('cmd-node').value);
  }
}

function runCommand(node, cmd) {
  return mininetAction(node + '> ' + cmd, '/api/cmd', { node, cmd });
}

$('btn-cmd').addEventListener('click', () => {
  const cmd = $('cmd-input').value.trim();
  if (cmd) runCommand($('cmd-node').value, cmd);
});

$('cmd-input').addEventListener('keydown', evt => {
  if (evt.key === 'Enter' && !$('btn-cmd').disabled) $('btn-cmd').click();
});

$('btn-ping').addEventListener('click', () => {
  const src = $('cmd-node').value, dst = $('cmd-target').value;
  mininetAction(`ping ${src} -> ${dst}`, '/api/ping', { src, dst });
});

$('btn-iperf').addEventListener('click', () => {
  const src = $('cmd-node').value, dst = $('cmd-target').value;
  mininetAction(`iperf ${src} -> ${dst}`, '/api/iperf', { src, dst });
});

$('btn-pingall').addEventListener('click', () => {
  mininetAction('pingall', '/api/pingall', {});
});

$('btn-clear').addEventListener('click', () => $('console-out').replaceChildren());

// ---- keyboard shortcuts ---------------------------------------------------------

document.addEventListener('keydown', evt => {
  if (['INPUT', 'SELECT', 'TEXTAREA'].includes(evt.target.tagName)) return;
  if (evt.key === 'Escape') {
    setTool('select');
  } else if ((evt.key === 'Delete' || evt.key === 'Backspace') && state.selected) {
    evt.preventDefault();     // e.g. Backspace = "go back" in some web views
    const { kind, id } = state.selected;
    if (kind === 'note') deleteNote(id);          // notes are never locked
    else if (editingLocked()) return;
    else if (kind === 'node') deleteNode(id);
    else deleteLink(id);
    onTopologyChanged();
  }
});

// ---- polling: backend status and ONOS live view ----------------------------------

// Toolbar badge: are the switches connected to the controller?
function updateControllerBadge() {
  const el = $('ctl-status');
  const { up, total } = controllerConnections();
  el.hidden = !state.running || !total;
  if (el.hidden) return;
  if (up === total) {
    el.textContent = 'Controller connected';
    el.className = 'badge running';
  } else if (up === 0) {
    el.textContent = 'Controller not connected';
    el.className = 'badge down';
  } else {
    el.textContent = `Controller: ${up}/${total} switches`;
    el.className = 'badge busy';
  }
}

// Tell the user in the console when switches connect or disconnect.
function reportConnectionChanges(before, after) {
  const changed = Object.keys(after).filter(n => before[n] !== after[n]);
  const joined = changed.filter(n => after[n]), left = changed.filter(n => !after[n]);
  const names = Object.keys(after);
  if (joined.length && names.every(n => after[n])) {
    notify('Controller connected to all switches (' + names.join(', ') + ')', 'ok');
  } else if (joined.length) {
    notify('Connected to the controller: ' + joined.join(', '), 'ok');
  }
  // On the first report after Run, "not connected" is not news yet.
  if (left.length && Object.keys(before).length) {
    notify('Lost connection to the controller: ' + left.join(', '), 'err');
  }
}

async function pollStatus() {
  try {
    const data = await api('/api/status');
    isRoot = data.root;
    if (!busy) state.running = data.running;
    const connected = data.connected || {};
    if (JSON.stringify(connected) !== JSON.stringify(state.connected)) {
      reportConnectionChanges(state.connected, connected);
      state.connected = connected;
      render();
    }
  } catch (e) {
    // backend restarting; try again next time
  }
  updateControls();
}

async function pollLive() {
  const source = $('live-source').value;      // '', 'onos' or 'odl'
  if (!source) return;
  let data = null, status;
  try {
    data = await api('/api/live?controller=' + source);
    status = data.ok
      ? `${data.label}: ${data.devices.length} switches, ${data.links.length} links, ` +
        `${data.hosts.length} hosts`
      : data.error;
  } catch (e) {
    status = 'backend unreachable';
  }
  if ($('live-source').value !== source) return;   // user switched meanwhile
  state.live = data;
  $('live-status').textContent = status;
  render();
  renderProps();
}

$('live-source').addEventListener('change', () => {
  state.live = null;
  $('live-status').textContent = $('live-source').value ? 'connecting...' : '';
  render();
  renderProps();
  pollLive();
});

// ---- start up ----------------------------------------------------------------

inlineIcons();
restore();
onTopologyChanged();
pollStatus();
setInterval(pollStatus, POLL_MS);
setInterval(pollLive, POLL_MS);
notify('Ready. Place nodes with the toolbar, then press Run.');
