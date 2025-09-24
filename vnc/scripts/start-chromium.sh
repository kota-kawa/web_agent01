#!/usr/bin/env bash
set -euo pipefail

log() {
  printf '[chromium] %s\n' "$*"
}

export DISPLAY="${DISPLAY:-:0}"
export LANG="${LANG:-ja_JP.UTF-8}"

REMOTE_PORT="${CHROMIUM_REMOTE_DEBUG_PORT:-9222}"
REMOTE_ADDRESS="${CHROMIUM_REMOTE_DEBUG_ADDRESS:-0.0.0.0}"
START_URL="${START_URL:-https://www.yahoo.co.jp/}"
PROFILE_DIR="${CHROMIUM_PROFILE_DIR:-/tmp/chromium-profile}"
X_WAIT_ATTEMPTS="${XSET_WAIT_ATTEMPTS:-45}"
REMOTE_READY_TIMEOUT="${CHROMIUM_REMOTE_DEBUG_TIMEOUT:-90}"
REMOTE_READY_INTERVAL="${CHROMIUM_REMOTE_DEBUG_INTERVAL:-1}"

if ! [[ "$X_WAIT_ATTEMPTS" =~ ^[0-9]+$ ]] || (( X_WAIT_ATTEMPTS < 1 )); then
  X_WAIT_ATTEMPTS=45
fi

if ! [[ "$REMOTE_READY_TIMEOUT" =~ ^[0-9]+$ ]] || (( REMOTE_READY_TIMEOUT < 10 )); then
  REMOTE_READY_TIMEOUT=90
fi

if ! [[ "$REMOTE_READY_INTERVAL" =~ ^[0-9]+$ ]] || (( REMOTE_READY_INTERVAL < 1 )); then
  REMOTE_READY_INTERVAL=1
fi

mkdir -p "$PROFILE_DIR"

IFS=' ' read -r -a EXTRA_ARGS <<< "${CHROMIUM_ADDITIONAL_ARGS:-}"
if (( ${#EXTRA_ARGS[@]} == 1 )) && [[ -z "${EXTRA_ARGS[0]}" ]]; then
  EXTRA_ARGS=()
fi

wait_for_x() {
  for (( attempt = 1; attempt <= X_WAIT_ATTEMPTS; attempt++ )); do
    if xset q >/dev/null 2>&1; then
      log "X server ready after ${attempt} attempt(s)"
      return 0
    fi
    log "waiting for X server... (${attempt}/${X_WAIT_ATTEMPTS})"
    sleep 1
  done
  return 1
}

configure_display() {
  if wait_for_x; then
    xset s off >/dev/null 2>&1 || true
    xset s noblank >/dev/null 2>&1 || true
    xset -dpms >/dev/null 2>&1 || true
  else
    log "warning: could not configure xset (X server not ready)"
  fi
}

wait_for_cdp() {
  local chromium_pid="$1"
  local deadline=$((SECONDS + REMOTE_READY_TIMEOUT))
  local local_url="http://127.0.0.1:${REMOTE_PORT}/json/version"
  local remote_url="http://${REMOTE_ADDRESS}:${REMOTE_PORT}/json/version"

  while (( SECONDS < deadline )); do
    if ! kill -0 "$chromium_pid" >/dev/null 2>&1; then
      return 1
    fi

    if curl -fsS --max-time 2 "$local_url" >/dev/null 2>&1 || \
       curl -fsS --max-time 2 "$remote_url" >/dev/null 2>&1; then
      log "remote debugging endpoint available on port ${REMOTE_PORT}"
      return 0
    fi

    sleep "$REMOTE_READY_INTERVAL"
  done

  log "timed out waiting for remote debugging endpoint on port ${REMOTE_PORT}"
  return 1
}

launch_chromium() {
  log "launching chromium (remote-debugging-port=${REMOTE_PORT})"
  chromium \
    --no-sandbox \
    --disable-dev-shm-usage \
    --disable-background-networking \
    --disable-background-timer-throttling \
    --disable-renderer-backgrounding \
    --disable-client-side-phishing-detection \
    --disable-default-apps \
    --no-first-run \
    --no-default-browser-check \
    --disable-features=Translate,BackForwardCache \
    --remote-debugging-address="${REMOTE_ADDRESS}" \
    --remote-debugging-port="${REMOTE_PORT}" \
    --remote-allow-origins="*" \
    --remote-allow-ips="*" \
    --user-data-dir="${PROFILE_DIR}" \
    --window-size=1280,800 \
    "${EXTRA_ARGS[@]}" \
    "${START_URL}" &
  echo $!
}

configure_display

chromium_pid=0
stop_requested=0

cleanup() {
  stop_requested=1
  if (( chromium_pid > 0 )); then
    log "terminating chromium (pid=${chromium_pid})"
    kill "$chromium_pid" >/dev/null 2>&1 || true
    wait "$chromium_pid" 2>/dev/null || true
  fi
}

trap cleanup INT TERM

while (( stop_requested == 0 )); do
  chromium_pid=$(launch_chromium)

  if ! wait_for_cdp "$chromium_pid"; then
    log "remote debugging endpoint not ready; restarting browser"
    kill "$chromium_pid" >/dev/null 2>&1 || true
    wait "$chromium_pid" 2>/dev/null || true
    (( stop_requested == 0 )) && sleep 2
    continue
  fi

  wait "$chromium_pid"
  exit_code=$?

  if (( stop_requested )); then
    break
  fi

  log "chromium exited with code ${exit_code}; restarting in 2s"
  sleep 2
done

log "chromium supervisor exiting"
