# Security and data policy

- No private API keys are required for the public market-data collectors.
- Do not commit `.env`, raw market archives, local caches, logs or model outputs containing private/local data.
- The repository contains public Bybit endpoints only.
- CI runs offline tests and does not call live exchange endpoints.
- This is a research/data project, not an order-execution bot.

If adapting this repository to private APIs, keep credentials in environment variables or a secret store and never in committed configuration.
