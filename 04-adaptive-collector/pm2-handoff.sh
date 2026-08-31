#!/usr/bin/env bash
set -euo pipefail
OLD_NAME="${1:-ml-market-collector}"
DATA_DIR="${2:-}"
OLD_STATUS_PORT="${OLD_STATUS_PORT:-3042}"
export ADAPTIVE_STATUS_PORT="${ADAPTIVE_STATUS_PORT:-3043}"

if [[ -z "$DATA_DIR" ]]; then
  echo "Usage: bash pm2-handoff.sh OLD_PM2_NAME /absolute/path/to/existing/data"
  echo "Example: bash pm2-handoff.sh ml-market-collector /srv/market-ml-collector/data"
  exit 2
fi
if [[ ! -d "$DATA_DIR" ]]; then
  echo "ERROR: data dir does not exist: $DATA_DIR"
  exit 2
fi
export ML_COLLECTOR_DATA_DIR="$(cd "$DATA_DIR" && pwd)"

# Preserve the exact symbols of the running old collector whenever its status endpoint is available.
OLD_JSON="$(curl -fsS "http://127.0.0.1:${OLD_STATUS_PORT}/status" 2>/dev/null || true)"
if [[ -n "$OLD_JSON" ]]; then
  OLD_SYMBOLS="$(printf '%s' "$OLD_JSON" | node -e 'let s="";process.stdin.on("data",d=>s+=d).on("end",()=>{try{const j=JSON.parse(s); console.log(`${j.config?.spotSymbol||""} ${j.config?.perpSymbol||""}`)}catch{process.exit(1)}})' || true)"
  read -r OLD_SPOT OLD_PERP <<< "$OLD_SYMBOLS"
  if [[ -n "${OLD_SPOT:-}" ]]; then export SPOT_SYMBOL="$OLD_SPOT"; fi
  if [[ -n "${OLD_PERP:-}" ]]; then export PERP_SYMBOL="$OLD_PERP"; fi
  echo "[handoff] old symbols: spot=${SPOT_SYMBOL:-?} perp=${PERP_SYMBOL:-?}"
else
  echo "[handoff] WARNING: old status endpoint unavailable on port ${OLD_STATUS_PORT}; using env/default symbols."
fi

echo "[handoff] starting ml-market-collector-adaptive against shared data: $ML_COLLECTOR_DATA_DIR"
pm2 start ecosystem.config.cjs --only ml-market-collector-adaptive --update-env

echo "[handoff] waiting for both WS feeds, both books and first trades..."
READY=0
NEW_JSON=""
for _ in $(seq 1 90); do
  if NEW_JSON="$(curl -fsS "http://127.0.0.1:${ADAPTIVE_STATUS_PORT}/status" 2>/dev/null)"; then
    if printf '%s' "$NEW_JSON" | node -e 'let s="";process.stdin.on("data",d=>s+=d).on("end",()=>{try{process.exit(JSON.parse(s).handoffReady?0:1)}catch{process.exit(1)}})'; then
      READY=1
      break
    fi
  fi
  sleep 1
done
if [[ "$READY" != "1" ]]; then
  echo "ERROR: new collector did not become handoffReady; OLD collector was NOT stopped."
  pm2 logs ml-market-collector-adaptive --lines 40 --nostream || true
  exit 1
fi

if [[ -n "$OLD_JSON" ]]; then
  if ! OLD_JSON="$OLD_JSON" NEW_JSON="$NEW_JSON" node -e 'const o=JSON.parse(process.env.OLD_JSON),n=JSON.parse(process.env.NEW_JSON); if(o.config?.spotSymbol!==n.config?.spotSymbol||o.config?.perpSymbol!==n.config?.perpSymbol){console.error(`symbol mismatch old=${o.config?.spotSymbol}/${o.config?.perpSymbol} new=${n.config?.spotSymbol}/${n.config?.perpSymbol}`);process.exit(1)}'; then
    echo "ERROR: symbol mismatch. OLD collector was NOT stopped."
    exit 1
  fi
fi

# If the exact research sensor universe is in use, require the bundled calibration to have loaded.
if [[ "${SPOT_SYMBOL:-}" == "BTCUSDC" && "${PERP_SYMBOL:-}" == "BTCUSDT" ]]; then
  if ! NEW_JSON="$NEW_JSON" node -e 'const n=JSON.parse(process.env.NEW_JSON); const ok=n.adaptive?.calibrated===true && String(n.adaptive?.calibrationSource||"").includes("research_v04"); process.exit(ok?0:1)'; then
    echo "ERROR: validated BTCUSDC/BTCUSDT universe is active but bundled research calibration was not loaded. OLD collector was NOT stopped."
    exit 1
  fi
  echo "[handoff] exact v0.4 research calibration loaded; adaptive starts immediately."
else
  echo "[handoff] preserving non-research symbol universe ${SPOT_SYMBOL:-?}/${PERP_SYMBOL:-?}; bundled BTCUSDC calibration is NOT applied."
  echo "[handoff] collector will reuse a compatible data/adaptive/calibration.json or safely warm up at 1s."
fi

echo "[handoff] new collector is READY; stopping old collector: $OLD_NAME"
pm2 stop "$OLD_NAME"
pm2 save

echo "[handoff] DONE. New collector keeps recording in live/current-adaptive."
curl -fsS "http://127.0.0.1:${ADAPTIVE_STATUS_PORT}/status" || true
echo
