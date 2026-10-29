#!/usr/bin/env bash
# Runs as root (run.sh starts it with sudo, or pkexec from the app menu):
# makes sure Open vSwitch and the ONOS container are up, then starts the
# mininet-ui server. Everything that needs root is in this one script, so
# the password is asked only once.
set -u

if ! systemctl is-active --quiet openvswitch-switch; then
    echo "Starting Open vSwitch"
    systemctl start openvswitch-switch
fi

# The ONOS container from the Task 1 setup, if it exists but is stopped.
if command -v docker >/dev/null && docker inspect onos >/dev/null 2>&1; then
    if [ "$(docker inspect -f '{{.State.Running}}' onos)" != "true" ]; then
        echo "Starting ONOS container (takes about a minute to come up)"
        docker start onos >/dev/null
    fi
fi

exec python3 "$(dirname "$0")/app.py"
