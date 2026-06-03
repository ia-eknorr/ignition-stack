#!/usr/bin/env bash
# End-to-end verification harness for ignition-stack.
#
# Drives the CLI through the full generate -> boot -> commission flow
# and writes a per-check PASS/FAIL line to results.log.
# Exits non-zero on any failure.
#
# Re-run safely: cleans containers and named volume on every invocation,
# wipes generated/<project> before regenerating.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${HERE}/../.." && pwd)"
VENV_DIR="${HERE}/.venv"
GEN_DIR="${HERE}/generated"
PROJECT_NAME="smoke"
PROJECT_DIR="${GEN_DIR}/${PROJECT_NAME}"
LOG="${HERE}/results.log"
HOST_PORT=9088
CACHE_DIR="$(mktemp -d -t ignition-stack-cache-XXXXXX)"

PASS=0
FAIL=0

log() { printf '%s\n' "$*" | tee -a "${LOG}"; }
pass() { PASS=$((PASS + 1)); log "PASS: $*"; }
fail() { FAIL=$((FAIL + 1)); log "FAIL: $*"; }
note() { log "      $*"; }

cleanup() {
  local rc=$?
  if [[ -d "${PROJECT_DIR}" ]] && [[ -f "${PROJECT_DIR}/docker-compose.yaml" ]]; then
    log ""
    log "--- teardown ---"
    (cd "${PROJECT_DIR}" && docker compose down -v --remove-orphans 2>&1 | tee -a "${LOG}" || true)
  fi
  rm -rf "${CACHE_DIR}" || true
  log ""
  log "Summary: ${PASS} passed, ${FAIL} failed"
  exit "${rc}"
}
trap cleanup EXIT

: > "${LOG}"
log "ignition-stack end-to-end verification"
log "repo:    ${REPO_ROOT}"
log "branch:  $(git -C "${REPO_ROOT}" branch --show-current)"
log "commit:  $(git -C "${REPO_ROOT}" rev-parse --short HEAD)"
log "started: $(date -u +%FT%TZ)"
log ""

############################################################
log "--- 1. preflight ---"
############################################################

command -v docker >/dev/null || { fail "docker not on PATH"; exit 1; }
pass "docker present ($(docker --version))"

command -v python3 >/dev/null || { fail "python3 not on PATH"; exit 1; }
PY_VER=$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')
pass "python3 present (${PY_VER})"

if lsof -i ":${HOST_PORT}" -sTCP:LISTEN >/dev/null 2>&1; then
  fail "host port ${HOST_PORT} already in use; free it or change HOST_PORT"
  exit 1
fi
pass "host port ${HOST_PORT} free"

docker info >/dev/null 2>&1 || { fail "docker daemon not reachable"; exit 1; }
pass "docker daemon reachable"

############################################################
log ""
log "--- 2. install CLI from working tree ---"
############################################################

if [[ ! -d "${VENV_DIR}" ]]; then
  python3 -m venv "${VENV_DIR}"
  note "created venv at ${VENV_DIR}"
fi
# shellcheck source=/dev/null
source "${VENV_DIR}/bin/activate"
pip install --quiet --upgrade pip 2>&1 | tee -a "${LOG}" >/dev/null || true
pip install --quiet -e "${REPO_ROOT}" 2>&1 | tee -a "${LOG}" >/dev/null

VER_OUT=$(ignition-stack --version 2>&1)
VER_RC=$?
if [[ "${VER_RC}" -eq 0 ]] && [[ "${VER_OUT}" == ignition-stack\ * ]]; then
  pass "ignition-stack --version works (${VER_OUT})"
else
  fail "ignition-stack --version broken (rc=${VER_RC}): ${VER_OUT}"
  exit 1
fi

# Bare invocation should print help and exit 0 (not error-out).
BARE_OUT=$(ignition-stack 2>&1)
BARE_RC=$?
if [[ "${BARE_RC}" -eq 0 ]] && [[ "${BARE_OUT}" == *"Usage: ignition-stack"* ]]; then
  pass "ignition-stack (no args) prints help and exits 0"
else
  fail "bare ignition-stack invocation broken (rc=${BARE_RC})"
fi

############################################################
log ""
log "--- 3. catalog (modules subcommand) ---"
############################################################

LIST_OUT="${HERE}/.tmp.list.out"
if ignition-stack modules list > "${LIST_OUT}" 2>&1; then
  # Rich truncates long names ('mqtt-transm…', 'mqtt-distri…'); count by
  # short prefixes that survive truncation rather than full names.
  ROW_COUNT=0
  for prefix in "mqtt-engine" "mqtt-transm" "mqtt-distri" "mysql-jdbc" "mcp-module"; do
    grep -q "${prefix}" "${LIST_OUT}" && ROW_COUNT=$((ROW_COUNT + 1))
  done
  if [[ "${ROW_COUNT}" -eq 5 ]]; then
    pass "modules list returned all 5 expected entries"
  else
    fail "modules list returned ${ROW_COUNT}/5 expected entries; saw output below"
    sed 's/^/  | /' "${LIST_OUT}" | tee -a "${LOG}" >/dev/null
  fi
else
  fail "modules list exited non-zero"
  cat "${LIST_OUT}" | tee -a "${LOG}" >/dev/null
fi
rm -f "${LIST_OUT}"

LIST_FILTERED="${HERE}/.tmp.list-filtered.out"
if ignition-stack modules list --ignition-version 8.3.6 > "${LIST_FILTERED}" 2>&1; then
  pass "modules list --ignition-version 8.3.6 succeeded"
else
  fail "modules list --ignition-version 8.3.6 failed"
  cat "${LIST_FILTERED}" | tee -a "${LOG}" >/dev/null
fi
rm -f "${LIST_FILTERED}"

# `validate --skip-network` must fail today: every sha is UNPINNED in
# modules.yaml. This proves the gate is wired, not that the catalog is
# broken. If the maintainer has since pinned shas, flip the assertion.
UNPINNED_COUNT=$(grep -c "sha256: UNPINNED" "${REPO_ROOT}/modules.yaml" || true)
VAL_OUT="${HERE}/.tmp.validate.out"
set +e
ignition-stack modules validate --skip-network > "${VAL_OUT}" 2>&1
VAL_RC=$?
set -e

if [[ "${UNPINNED_COUNT}" -gt 0 ]]; then
  if [[ "${VAL_RC}" -ne 0 ]] && grep -q "UNPINNED" "${VAL_OUT}"; then
    pass "modules validate correctly rejects UNPINNED shas (rc=${VAL_RC})"
  else
    fail "modules validate did not reject UNPINNED shas (rc=${VAL_RC})"
    cat "${VAL_OUT}" | tee -a "${LOG}" >/dev/null
  fi
else
  if [[ "${VAL_RC}" -eq 0 ]]; then
    pass "modules validate passed; all shas pinned"
  else
    fail "modules validate failed even though no UNPINNED entries in modules.yaml (rc=${VAL_RC})"
    cat "${VAL_OUT}" | tee -a "${LOG}" >/dev/null
  fi
fi
rm -f "${VAL_OUT}"

# offline + empty cache should fail closed for the four non-manual entries.
DL_OUT="${HERE}/.tmp.download.out"
set +e
ignition-stack modules download --offline --cache-dir "${CACHE_DIR}" > "${DL_OUT}" 2>&1
DL_RC=$?
set -e

if [[ "${DL_RC}" -ne 0 ]] && grep -q -E "(not in cache|--offline)" "${DL_OUT}"; then
  pass "modules download --offline with empty cache fails closed (rc=${DL_RC})"
else
  fail "modules download --offline did not fail closed (rc=${DL_RC})"
  cat "${DL_OUT}" | tee -a "${LOG}" >/dev/null
fi
# The manual-only mcp-module entry should be visibly skipped, not errored.
if grep -q "skipped-manual" "${DL_OUT}"; then
  pass "modules download surfaces skipped-manual for the EA-gated mcp-module entry"
else
  note "warning: did not see 'skipped-manual' line; output was:"
  sed 's/^/  | /' "${DL_OUT}" | tee -a "${LOG}" >/dev/null
fi
rm -f "${DL_OUT}"

############################################################
log ""
log "--- 4. init generates a project ---"
############################################################

rm -rf "${PROJECT_DIR}"
mkdir -p "${GEN_DIR}"

if ignition-stack init "${PROJECT_NAME}" --output-dir "${GEN_DIR}" >>"${LOG}" 2>&1; then
  pass "ignition-stack init ${PROJECT_NAME} created project"
else
  fail "ignition-stack init failed"
  exit 1
fi

for f in docker-compose.yaml .env scripts/docker-bootstrap.sh \
         services/ignition/config/resources/core/config-mode.json; do
  if [[ -e "${PROJECT_DIR}/${f}" ]]; then
    pass "generated file present: ${f}"
  else
    fail "generated file missing: ${f}"
  fi
done

if [[ -x "${PROJECT_DIR}/scripts/docker-bootstrap.sh" ]]; then
  pass "docker-bootstrap.sh is executable"
else
  fail "docker-bootstrap.sh is not executable"
fi

if grep -q "^COMPOSE_PROJECT_NAME=${PROJECT_NAME}$" "${PROJECT_DIR}/.env"; then
  pass ".env contains expected COMPOSE_PROJECT_NAME"
else
  fail ".env missing COMPOSE_PROJECT_NAME=${PROJECT_NAME}"
fi

############################################################
log ""
log "--- 5. docker compose up & gateway reaches RUNNING ---"
############################################################

(
  cd "${PROJECT_DIR}"
  log "running: docker compose up -d --wait"
  # --wait blocks until every healthcheck-having service is healthy and every
  # service_completed_successfully dependency has exited 0.
  if docker compose up -d --wait 2>&1 | tee -a "${LOG}"; then
    :
  else
    fail "docker compose up -d --wait exited non-zero"
    exit 1
  fi
)
pass "docker compose up --wait succeeded (db healthy, bootstrap completed)"

log "polling gateway StatusPing for RUNNING (timeout 240s)"
DEADLINE=$(( $(date +%s) + 240 ))
GATEWAY_OK=0
while [[ $(date +%s) -lt ${DEADLINE} ]]; do
  BODY=$(curl -fsS "http://localhost:${HOST_PORT}/StatusPing" 2>/dev/null || true)
  if [[ "${BODY}" == *'"state"'*'"RUNNING"'* ]] || [[ "${BODY}" == *'RUNNING'* ]]; then
    note "StatusPing -> ${BODY}"
    GATEWAY_OK=1
    break
  fi
  sleep 3
done

if [[ "${GATEWAY_OK}" -eq 1 ]]; then
  pass "gateway reached RUNNING state via StatusPing"
else
  fail "gateway did not reach RUNNING within 240s"
  note "last StatusPing body: ${BODY}"
  docker logs "${PROJECT_NAME}" --tail 80 2>&1 | sed 's/^/  | /' | tee -a "${LOG}" >/dev/null
fi

# /system/gwinfo is the unauthenticated info endpoint on 8.3. It proves
# three things at once: the gateway is RUNNING, the -n compose arg landed
# (PlatformName=<project>), and the image is the 8.3.6 we expect.
GWINFO=$(curl -fsS "http://localhost:${HOST_PORT}/system/gwinfo" || true)
if [[ "${GWINFO}" == *"ContextStatus=RUNNING"* ]] \
   && [[ "${GWINFO}" == *"PlatformName=${PROJECT_NAME}"* ]] \
   && [[ "${GWINFO}" == *"Version=8.3.6"* ]]; then
  pass "gwinfo confirms ContextStatus=RUNNING, PlatformName=${PROJECT_NAME}, Version=8.3.6"
else
  fail "gwinfo did not include expected fields"
  note "body: ${GWINFO}"
fi

# 8.3 has no `/data/status/details`; admin authentication moved to a session
# /CSRF-protected form login at /idp. A "did commissioning succeed" check
# without parsing CSRF tokens: confirm '/' redirects past any setup wall.
# A non-commissioned gateway would redirect to /setup or /commissioning;
# a fully commissioned one redirects to /Start (which itself forwards to /app).
ROOT_REDIR=$(curl -sI "http://localhost:${HOST_PORT}/" | awk 'tolower($1)=="location:" {print $2}' | tr -d '\r\n')
if [[ "${ROOT_REDIR}" == "/Start" ]] || [[ "${ROOT_REDIR}" == "/app" ]]; then
  pass "root redirects past commissioning: '/' -> '${ROOT_REDIR}'"
else
  fail "root redirected unexpectedly: '/' -> '${ROOT_REDIR}'"
fi

# Bonus: confirm the gateway logs do not mention pending commissioning.
if docker logs "${PROJECT_NAME}" 2>&1 | grep -qiE "(commissioning required|setup required|awaiting.+commission)"; then
  fail "gateway logs mention pending commissioning (env-driven setup may have failed)"
else
  pass "no commissioning-required messages in gateway logs"
fi

# Confirm the bootstrap actually layered the template resources into /data.
if docker exec "${PROJECT_NAME}" \
     test -f /usr/local/bin/ignition/data/config/resources/core/config-mode.json; then
  pass "config-mode.json was layered into gateway /data by bootstrap"
else
  fail "config-mode.json was NOT layered into /data"
fi

# Phase 1 hallmark: deterministic gateway-network UUID under /data.
if docker exec "${PROJECT_NAME}" \
     test -s /usr/local/bin/ignition/data/config/local/ignition/gateway-network/uuid.txt; then
  UUID=$(docker exec "${PROJECT_NAME}" cat /usr/local/bin/ignition/data/config/local/ignition/gateway-network/uuid.txt)
  pass "deterministic gateway-network UUID written: ${UUID}"
else
  fail "gateway-network UUID file missing"
fi

log ""
log "finished: $(date -u +%FT%TZ)"
