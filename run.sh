#!/usr/bin/env bash
# Set up and start mininet-ui, then open it in the browser.
#
#   ./run.sh                 install missing dependencies, start, open browser
#   ./run.sh --no-browser    same, but don't open the browser
#   PORT=9000 ./run.sh       other settings (HOST, ONOS_URL, ODL_URL, ...) too
#
# Run it as your normal user, NOT with sudo: it asks for your password when
# it needs root (apt, starting Mininet), and the browser must not run as root.
# Stop the server with Ctrl+C.
set -euo pipefail

cd "$(dirname "$0")"
PORT="${PORT:-8080}"
URL="http://localhost:${PORT}"
OPEN_BROWSER=1
[[ "${1:-}" == "--no-browser" ]] && OPEN_BROWSER=0

info() { echo -e "\e[1;34m==>\e[0m $*"; }
warn() { echo -e "\e[1;33mWARNING:\e[0m $*"; }

if [[ $EUID -eq 0 ]]; then
    warn "run this as your normal user (./run.sh), not with sudo;"
    warn "the browser will not be opened automatically."
    OPEN_BROWSER=0
fi

# Does something answer on our port already? (python3 is always on Ubuntu)
server_up() {
    python3 - "$URL/api/status" <<'EOF' 2>/dev/null
import sys, urllib.request
urllib.request.urlopen(sys.argv[1], timeout=1)
EOF
}

open_browser() {
    if [[ $OPEN_BROWSER -eq 1 ]]; then
        xdg-open "$URL" >/dev/null 2>&1 || echo "Open $URL in your browser."
    fi
}

if server_up; then
    info "mininet-ui is already running at $URL"
    open_browser
    exit 0
fi

# ---- 1. dependencies ---------------------------------------------------------

info "Checking dependencies"
sudo -v     # ask for the password once, up front

missing=()
python3 -c 'import flask' 2>/dev/null   || missing+=(python3-flask)
python3 -c 'import mininet' 2>/dev/null || missing+=(mininet)
command -v ovs-vsctl >/dev/null         || missing+=(openvswitch-switch)
command -v iperf >/dev/null             || missing+=(iperf)

if [[ ${#missing[@]} -gt 0 ]]; then
    info "Installing: ${missing[*]}"
    sudo apt-get update
    sudo apt-get install -y "${missing[@]}"
else
    info "All dependencies present"
fi

if ! systemctl is-active --quiet openvswitch-switch; then
    info "Starting Open vSwitch"
    sudo systemctl start openvswitch-switch
fi

# ---- 2. controller -----------------------------------------------------------

# Start the ONOS container from the Task 1 setup if it exists but is stopped.
# (docker may need sudo if you're not in the docker group.)
DOCKER=""
if command -v docker >/dev/null; then
    if docker info >/dev/null 2>&1; then DOCKER="docker"; else DOCKER="sudo docker"; fi
fi
if [[ -n "$DOCKER" ]] && $DOCKER inspect onos >/dev/null 2>&1; then
    if [[ "$($DOCKER inspect -f '{{.State.Running}}' onos)" != "true" ]]; then
        info "Starting ONOS container (takes ~1 min to come up fully)"
        $DOCKER start onos >/dev/null
    fi
fi

# ONOS and OpenDaylight both serve REST on 8181 by default.
if ! python3 -c 'import socket; socket.create_connection(("127.0.0.1", 8181), 1)' 2>/dev/null; then
    warn "no controller is answering on port 8181 yet (ONOS or OpenDaylight)."
    warn "You can still build topologies; switches won't connect until it is up."
fi

# ---- 3. server + browser -------------------------------------------------------

# Open the browser once the server answers (in the background, as this user).
(
    for _ in $(seq 1 30); do
        if server_up; then open_browser; exit 0; fi
        sleep 1
    done
    warn "server did not answer on $URL within 30 s"
) &

info "Starting mininet-ui on $URL (Ctrl+C to stop)"
exec sudo --preserve-env=PORT,HOST,ONOS_URL,ONOS_USER,ONOS_PASS,ODL_URL,ODL_USER,ODL_PASS \
    python3 backend/app.py
