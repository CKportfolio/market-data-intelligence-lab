module.exports = {
  apps: [
    {
      name: "ml-market-collector",
      script: "collector.mjs",
      interpreter: "node",
      cwd: __dirname,
      watch: false,
      autorestart: true,
      restart_delay: 5000,
      max_restarts: 100,
      min_uptime: "30s",
      max_memory_restart: "500M",
      out_file: "./logs/out.log",
      error_file: "./logs/err.log",
      merge_logs: true,
      log_date_format: "YYYY-MM-DD HH:mm:ss",
      env: {
        NODE_ENV: "production",
        STATUS_PORT: "3042",
        SPOT_SYMBOL: "BTCUSDT",
        PERP_SYMBOL: "BTCUSDT",
        ORDERBOOK_DEPTH: "50",
        ORDERBOOK_SAMPLE_MS: "1000",
        SAVE_ORDERBOOK_DELTAS: "false",
        RAW_BATCH_MAX_MB: "150"
      }
    }
  ]
};
