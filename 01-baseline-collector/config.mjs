import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

function envBool(name, fallback) {
  const raw = process.env[name];
  if (raw == null || raw === "") return fallback;
  return /^(1|true|yes|on)$/i.test(raw);
}

function envInt(name, fallback, min = Number.MIN_SAFE_INTEGER, max = Number.MAX_SAFE_INTEGER) {
  const n = Number(process.env[name]);
  if (!Number.isFinite(n)) return fallback;
  return Math.max(min, Math.min(max, Math.trunc(n)));
}

export const CONFIG = {
  // Publiczne feedy Bybit. Nie wymagają API key.
  REST_BASE: process.env.BYBIT_REST_BASE || "https://api.bybit.com",
  WS_SPOT_URL: process.env.BYBIT_WS_SPOT_URL || "wss://stream.bybit.com/v5/public/spot",
  WS_LINEAR_URL: process.env.BYBIT_WS_LINEAR_URL || "wss://stream.bybit.com/v5/public/linear",

  // Para obserwowana na rynku spot i perpetual może być różna.
  // Jeśli chcesz analizować BTCUSDC na spot, ustaw SPOT_SYMBOL=BTCUSDC.
  SPOT_SYMBOL: (process.env.SPOT_SYMBOL || "BTCUSDT").toUpperCase(),
  PERP_SYMBOL: (process.env.PERP_SYMBOL || "BTCUSDT").toUpperCase(),

  DATA_DIR: path.resolve(process.env.DATA_DIR || path.join(__dirname, "data")),
  STATUS_PORT: envInt("STATUS_PORT", 3042, 1, 65535),

  ORDERBOOK_DEPTH: envInt("ORDERBOOK_DEPTH", 50, 1, 1000),
  // Dla predyktora formacji minutowych/godzinowych 1 s zachowuje dużo mikrostruktury
  // i ogranicza rozmiar danych względem zapisu każdej delty 20 ms.
  ORDERBOOK_SAMPLE_MS: envInt("ORDERBOOK_SAMPLE_MS", 1000, 100, 60_000),
  SAVE_ORDERBOOK_DELTAS: envBool("SAVE_ORDERBOOK_DELTAS", false),

  // Surowe dane są rotowane paczkami. Po osiągnięciu limitu aktualna paczka
  // zostaje zamknięta, a collector natychmiast zaczyna następną; poprzednia
  // jest kompresowana do .tar.gz w tle.
  RAW_BATCH_MAX_MB: envInt("RAW_BATCH_MAX_MB", 150, 1, 10_240),

  COLLECT_SPOT_TRADES: envBool("COLLECT_SPOT_TRADES", true),
  COLLECT_SPOT_ORDERBOOK: envBool("COLLECT_SPOT_ORDERBOOK", true),
  COLLECT_PERP_TRADES: envBool("COLLECT_PERP_TRADES", true),
  COLLECT_PERP_ORDERBOOK: envBool("COLLECT_PERP_ORDERBOOK", true),
  COLLECT_LIQUIDATIONS: envBool("COLLECT_LIQUIDATIONS", true),

  // Enrichment: historyczny kontekst przed pierwszym tickiem i opcjonalny ogon
  // cenowy po ostatnim ticku (przydatny później do etykiet MFE/MAE).
  ENRICH_PREROLL_DAYS: envInt("ENRICH_PREROLL_DAYS", 14, 0, 3650),
  ENRICH_POSTROLL_HOURS: envInt("ENRICH_POSTROLL_HOURS", 24, 0, 24 * 365),
  REST_REQUEST_DELAY_MS: envInt("REST_REQUEST_DELAY_MS", 80, 0, 5000),
  REST_RETRIES: envInt("REST_RETRIES", 4, 0, 10),
};
