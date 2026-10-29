#!/usr/bin/env bash
# Start Mininet UI as a desktop app.
#
#   ./run.sh              install what's missing, then open the app window
#   ./run.sh --browser    use a browser tab instead of the app window
#   ./run.sh --server     only start the server (then open http://localhost:8080)
#   PORT=9000 ./run.sh    other settings (HOST, ONOS_URL, ODL_URL, ...) too
#
# Run it as your normal user, NOT with sudo. The first time, run it from a
# terminal: it installs what's missing and adds "Mininet UI" to the app menu.
# After that you can also start it from the menu; it then asks for your
# password in a dialog. Closing the window stops everything.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
PORT="${PORT:-8080}"
URL="http://localhost:${PORT}"

MODE=window
case "${1:-}" in
    --browser) MODE=browser ;;
    --server|--no-browser) MODE=server ;;
esac

# The server's messages always go to this file (see step 3).
LOG="$HOME/.cache/mininet-ui.log"
mkdir -p "$HOME/.cache"

# Started from the app menu there is no terminal: log everything to the file,
# and ask for the password with a dialog (pkexec) instead of sudo.
if [[ -t 0 ]]; then
    IN_TERMINAL=1
else
    IN_TERMINAL=0
    exec >>"$LOG" 2>&1
fi
echo "---- $(date)" >>"$LOG"

info() { echo -e "\e[1;34m==>\e[0m $*"; }
warn() { echo -e "\e[1;33mWARNING:\e[0m $*"; }
fail() {
    echo "ERROR: $*" >&2
    if [[ $IN_TERMINAL -eq 0 ]]; then        # show it, there's no terminal
        zenity --error --title="Mininet UI" --text="$*" 2>/dev/null ||
            notify-send "Mininet UI" "$*" 2>/dev/null || true
    fi
    exit 1
}

[[ $EUID -eq 0 ]] && fail "Run ./run.sh as your normal user, not with sudo."

server_up() {
    python3 - "$URL/api/status" <<'EOF' 2>/dev/null
import sys, urllib.request
urllib.request.urlopen(sys.argv[1], timeout=1)
EOF
}

shutdown_server() {
    python3 - "$URL/api/shutdown" <<'EOF' 2>/dev/null || true
import sys, urllib.request
req = urllib.request.Request(sys.argv[1], data=b'{"confirm": true}',
                             headers={'Content-Type': 'application/json'})
urllib.request.urlopen(req, timeout=5)
EOF
}

open_browser() {
    xdg-open "$URL" >/dev/null 2>&1 || echo "Open $URL in your browser."
}

# ---- already running? just open another window -------------------------------

if server_up; then
    info "Mininet UI is already running at $URL"
    case $MODE in
        window) exec python3 desktop/window.py "$URL" --keep-server ;;
        browser) open_browser ;;
    esac
    exit 0
fi

# ---- 1. dependencies -----------------------------------------------------------

info "Checking dependencies"
WEBKIT_PKG=gir1.2-webkit2-4.0                       # Ubuntu 22.04
if apt-cache show gir1.2-webkit2-4.1 >/dev/null 2>&1; then
    WEBKIT_PKG=gir1.2-webkit2-4.1                   # Ubuntu 24.04
fi

missing=()
python3 -c 'import flask' 2>/dev/null   || missing+=(python3-flask)
python3 -c 'import mininet' 2>/dev/null || missing+=(mininet)
command -v ovs-vsctl >/dev/null         || missing+=(openvswitch-switch)
command -v iperf >/dev/null             || missing+=(iperf)
if [[ $MODE == window ]]; then
    python3 -c 'import gi' 2>/dev/null  || missing+=(python3-gi)
    python3 - <<'EOF' 2>/dev/null       || missing+=("$WEBKIT_PKG")
import gi
for version in ('4.1', '4.0'):
    try:
        gi.require_version('WebKit2', version)
        break
    except ValueError:
        pass
else:
    raise SystemExit(1)
EOF
fi

if [[ ${#missing[@]} -gt 0 ]]; then
    if [[ $IN_TERMINAL -eq 0 ]]; then
        fail "Some parts are missing (${missing[*]}). Open a terminal, go to $DIR and run ./run.sh once to install them."
    fi
    info "Installing: ${missing[*]}"
    sudo apt-get update
    sudo apt-get install -y "${missing[@]}"
else
    info "All dependencies present"
fi

# ---- 2. app menu entry ------------------------------------------------------------

# Refreshed on every terminal start, so it follows the folder if it moves.
if [[ $IN_TERMINAL -eq 1 ]]; then
    mkdir -p "$HOME/.local/share/applications"
    cat > "$HOME/.local/share/applications/mininet-ui.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Mininet UI
Comment=Build and run Mininet networks, Packet Tracer style
Exec="$DIR/run.sh"
Icon=$DIR/frontend/icon.svg
Terminal=false
Categories=Network;Education;Development;
StartupWMClass=mininet-ui
EOF
fi

# ---- 3. server (as root) ------------------------------------------------------------

# Pass the settings through sudo/pkexec, which otherwise drop them.
envs=("PORT=$PORT")
for var in HOST ONOS_URL ONOS_USER ONOS_PASS ODL_URL ODL_USER ODL_PASS; do
    if [[ -n "${!var:-}" ]]; then envs+=("$var=${!var}"); fi
done

if [[ $IN_TERMINAL -eq 1 ]]; then
    sudo -v                                 # ask for the password now
    ROOT=(sudo)
else
    ROOT=(pkexec)                           # graphical password dialog
fi
info "Starting the server (its messages go to $LOG)"
# The server must not touch the terminal: sudo would otherwise relay it to
# the background server, which swallows your typing and can leave the
# terminal unusable after the app closes. So: no input, output to the log.
"${ROOT[@]}" /usr/bin/env "${envs[@]}" /bin/bash "$DIR/backend/start-server.sh" \
    </dev/null >>"$LOG" 2>&1 &
SERVER_PID=$!

# Whatever happens: stop the server, wait for it to finish, and give the
# terminal back in a normal state.
finish() {
    local began=$SECONDS
    # The window already asked the server to quit; only ask if it's still up.
    if server_up; then shutdown_server; fi
    for _ in $(seq 1 100); do               # wait up to 20 s, checking 5x a second
        [[ -d /proc/$SERVER_PID ]] || break
        sleep 0.2
    done
    if [[ -d /proc/$SERVER_PID ]]; then
        warn "the server is still stopping; see $LOG"
    else
        info "Stopped ($((SECONDS - began)) s). Details in $LOG"
    fi
    if [[ $IN_TERMINAL -eq 1 ]]; then stty sane 2>/dev/null || true; fi
}
trap finish EXIT

for _ in $(seq 1 60); do
    server_up && break
    [[ -d /proc/$SERVER_PID ]] || fail "The server did not start (password dialog cancelled?). Details: $LOG"
    sleep 1
done
server_up || fail "The server did not answer on $URL within 60 seconds."

# ---- 4. user interface ----------------------------------------------------------------

case $MODE in
    window)
        info "Opening the Mininet UI window"
        python3 desktop/window.py "$URL" </dev/null
        info "Window closed, stopping the network and server" ;;
    browser)
        info "Mininet UI is running at $URL (Ctrl+C to stop)"
        open_browser
        wait "$SERVER_PID" ;;
    server)
        info "Mininet UI is running at $URL (Ctrl+C to stop)"
        wait "$SERVER_PID" ;;
esac
