# Mininet UI

A browser-based, Packet Tracer–style network builder for Mininet + ONOS
(replacement for MiniEdit). Draw hosts, OpenFlow 1.3 switches and a remote
controller, run the network, ping/iperf between hosts, and watch what ONOS
sees, all from the browser.

## Setup (Ubuntu 22.04 VM)

```bash
sudo apt install python3-flask      # system package, so `sudo python3` sees it
```

Mininet 2.3.0, Open vSwitch and ONOS (Docker container `onos`) are assumed to
already be installed, as in the Task 1 setup.

## Run

```bash
docker start onos                   # if it isn't running already
cd mininet-ui
sudo python3 backend/app.py
```

Open <http://localhost:8080> in the VM's browser (works on Wayland, unlike MiniEdit).

Without `sudo` the editor and **Export .py** still work; **Run** is disabled.

Settings via environment variables (use `sudo -E` or `sudo VAR=... python3 ...`):

| Variable    | Default                          |
|-------------|----------------------------------|
| `PORT`      | `8080`                           |
| `HOST`      | `127.0.0.1` (keep it: the server runs commands as root) |
| `ONOS_URL`  | `http://localhost:8181/onos/v1`  |
| `ONOS_USER` / `ONOS_PASS` | `onos` / `rocks`   |

## Using it

1. **Build**: pick Host / Switch / Controller and click the canvas. Pick
   **Link** and click two nodes. Drag to move. `Del` deletes, `Esc` cancels.
   Every switch is connected to every controller automatically (dashed lines).
2. **Edit**: click a node or link and change it in the Properties panel
   (host IP/MAC, switch DPID, controller IP/port, link bandwidth/delay/loss).
   New nodes get unique defaults (h1 = 10.0.0.1, 00:00:00:00:00:01, ...).
3. **Save / Open**: download or load the topology as JSON. The current
   drawing is also kept in the browser between reloads. Try
   `examples/two-switches.json`.
4. **Export .py**: downloads a standalone Mininet script
   (`RemoteController`, `OpenFlow13`, `TCLink`). Run it with `sudo python3 topology.py`.
5. **Run / Stop**: starts the network inside the backend (it runs the
   equivalent of `sudo mn -c` first). The topology is locked while running.
6. **Console**: pick a host and type a command, or use Ping / iperf / Ping all.
   Clicking a host on the canvas selects it in the console. Commands time out after 30 s.
7. **ONOS live view**: tick the checkbox. Every 3 s the backend fetches
   devices/links/hosts/flows from ONOS and the canvas shows:
   - green dot on a switch = connected to ONOS, red = not;
   - green link = ONOS discovered it (LLDP for switch-switch links, host
     location for host links); red dashed = not (yet);
   - green dot on a host = ONOS has seen its MAC. Hosts appear only after
     they send traffic, so run **Ping all** once.
   - select a switch to see its ONOS id and number of flow rules.

## How it works

```
browser (frontend/)            backend/ (Flask, runs as root)
  state.js   topology model  ──JSON──▶ app.py      HTTP API
  canvas.js  SVG drawing                topology.py validate + export script
  ui.js      panels, console            runner.py   Mininet API (start/stop/cmd)
                                        onos.py     ONOS REST client ──▶ :8181
```

- The topology is a JSON object with `nodes` and `links` (format in
  [backend/topology.py](backend/topology.py)). The same file is saved, loaded,
  exported and run.
- `topology.normalize()` validates everything (names, unique IP/MAC/DPID,
  link values) and produces one "spec" used both by the script exporter and
  by `runner.py`, so the exported script and the Run button build the same network.
- `runner.py` uses the Mininet Python API directly. A lock serialises access
  because each Mininet host is a single shell.
- `onos.py` reads ONOS REST on the server side, so the browser never needs
  the ONOS password and there are no CORS issues.

### API

| Method | Path            | Body                 | Result |
|--------|-----------------|----------------------|--------|
| GET    | `/api/status`   |                      | `{root, running, hosts, switches}` |
| POST   | `/api/validate` | topology             | `{warnings}` or `{error, errors}` |
| POST   | `/api/export`   | topology             | `{script}` |
| POST   | `/api/start`    | topology             | `{warnings, hosts, switches}` |
| POST   | `/api/stop`     |                      | `{ok}` |
| POST   | `/api/cmd`      | `{node, cmd}`        | `{output}` |
| POST   | `/api/ping`     | `{src, dst}`         | `{output}` |
| POST   | `/api/iperf`    | `{src, dst}`         | `{output}` |
| POST   | `/api/pingall`  |                      | `{output}` |
| GET    | `/api/onos`     |                      | `{ok, devices, links, hosts, flows}` |

## Tests

```bash
python3 -m unittest discover tests     # no root or Mininet needed
```

## Troubleshooting

- **Switches stay red**: check ONOS is up (`docker ps`) and the
  `org.onosproject.openflow` app is active; controller IP/port must be
  `127.0.0.1:6653`.
- **Ping fails**: activate `org.onosproject.fwd` in ONOS.
- **Run says "already running" / leftovers after a crash**: Stop, or restart
  the backend and run `sudo mn -c`.
