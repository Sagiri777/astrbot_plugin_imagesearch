#!/bin/sh
set -eu
export DISPLAY=:99
export SOUTUBOT_HEADLESS=false
Xvfb "$DISPLAY" -screen 0 1280x900x24 -nolisten tcp &
xvfb_pid=$!
attempt=0
until xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; do
    attempt=$((attempt + 1))
    if [ "$attempt" -ge 50 ]; then kill "$xvfb_pid"; exit 1; fi
    sleep 0.1
done
fluxbox -display "$DISPLAY" &
wm_pid=$!
x11vnc -display "$DISPLAY" -localhost -forever -shared -nopw -rfbport 5900 &
vnc_pid=$!
websockify --web=/usr/share/novnc/ 6080 localhost:5900 &
web_pid=$!
python -m uvicorn proxy.app:app --host 0.0.0.0 --port 8787 &
api_pid=$!
trap 'kill "$api_pid" "$web_pid" "$vnc_pid" "$wm_pid" "$xvfb_pid" 2>/dev/null || true' EXIT
trap 'exit 0' INT TERM
wait "$api_pid"
