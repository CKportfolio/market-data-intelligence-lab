const path = require("node:path");

module.exports = {
  apps: [
    {
      name: "ml-market-collector-adaptive",
      script: "collector.mjs",
      interpreter: "node",
      cwd: __dirname,
      watch: false,
      autorestart: true,
      restart_delay: 5000,
      max_restarts: 100,
      min_uptime: "30s",
      max_memory_restart: "700M",
      out_file: "./logs/out.log",
      error_file: "./logs/err.log",
      merge_logs: true,
      log_date_format: "YYYY-MM-DD HH:mm:ss",
      env: {
        NODE_ENV: "production",
        STATUS_PORT: process.env.ADAPTIVE_STATUS_PORT || "3043",
        SPOT_SYMBOL: process.env.SPOT_SYMBOL || "BTCUSDT",
        PERP_SYMBOL: process.env.PERP_SYMBOL || "BTCUSDT",
        DATA_DIR: process.env.ML_COLLECTOR_DATA_DIR || path.join(__dirname, "data"),
        ORDERBOOK_DEPTH: "50",
        SAVE_ORDERBOOK_DELTAS: "false",
        ORDERBOOK_WRITE_UNCHANGED: "true",
        RAW_BATCH_MAX_MB: "900",
        RAW_LIVE_NAME: "current-adaptive",
        ADAPTIVE_ENABLED: "true",
        ADAPTIVE_TICK_MS: "250",
        ADAPTIVE_WARMUP_MINUTES: process.env.ADAPTIVE_WARMUP_MINUTES || "30",
        ADAPTIVE_USE_BUNDLED_RESEARCH_CALIBRATION: process.env.ADAPTIVE_USE_BUNDLED_RESEARCH_CALIBRATION || "true"
      }
    }
  ]
};
