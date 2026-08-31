# Market ML Collector v1.1

Jeden collector 24/7 + osobny lokalny enrichment pod dataset ML.

## Co collector zapisuje na żywo

Z Bybit public WebSocket:

1. `spot_trades` — wszystkie publiczne transakcje spot tick-by-tick.
2. `spot_orderbook` — odtworzony L50 spot i pełny snapshot co 1 s, jeśli książka się zmieniła.
3. `perp_trades` — wszystkie publiczne transakcje perpetual tick-by-tick.
4. `perp_orderbook` — odtworzony L50 perpetual i pełny snapshot co 1 s.
5. `liquidations` — wszystkie publiczne likwidacje BTC perpetual.
6. `system` — reconnecty/subskrypcje/błędy socketów.

Opcjonalnie `SAVE_ORDERBOOK_DELTAS=true` zapisuje również każdą deltę orderbooka. Domyślnie jest wyłączone, bo ilość danych rośnie bardzo szybko.

Każdy rekord w surowym pliku dostaje dodatkowo:

- `_channel` — rodzaj strumienia (`spot_trades`, `perp_orderbook`, itd.),
- `tsRecordMs` — ujednolicony główny timestamp rekordu.

Nie zapisujemy RSI/MACD/CCI/ATR/regime/sygnałów — to cechy pochodne, które policzymy później z danych źródłowych.

## Rotacja i kompresja 150 MB

Domyślny limit aktywnej paczki to:

```text
RAW_BATCH_MAX_MB=150
```

Collector zapisuje wszystko do:

```text
data/raw/live/current/market.jsonl
```

Gdy kolejny zapis spowodowałby przekroczenie limitu:

1. aktualna paczka zostaje zamknięta,
2. jest atomowo przenoszona do `data/raw/staging/`,
3. collector natychmiast otwiera nową `live/current` i dalej zbiera dane,
4. zamknięta paczka jest kompresowana do `tar.gz`,
5. archiwum jest sprawdzane przez `tar -tzf`,
6. dopiero po poprawnej weryfikacji nieskompresowany staging jest kasowany.

Jeśli VPS padnie podczas kompresji, staging pozostaje. Przy kolejnym starcie collector spróbuje go spakować ponownie.

Archiwa trafiają do:

```text
data/raw/archives/
```

Przykładowa nazwa:

```text
27.08.2026_12-10 - 27.08.2026_12-46.tar.gz
```

Zakres w nazwie jest w UTC. Wewnątrz `manifest.json` znajduje się również czytelna etykieta z dwukropkiem, dokładne `startMs`/`endMs`, liczba rekordów i liczba rekordów per kanał.

W nazwie pliku używamy `12-10` zamiast `12:10`, ponieważ Windows nie dopuszcza dwukropka w nazwach plików.

## Co robi lokalny `enrich.mjs`

Po skopiowaniu katalogu `data/raw` z VPS uruchamiasz:

```bash
npm run enrich
```

Skrypt najpierw:

1. czyta manifesty wszystkich `.tar.gz`,
2. szereguje paczki po dokładnym `startMs`,
3. uwzględnia także ewentualny `staging` po awarii,
4. na końcu dokłada aktualną, jeszcze niezarchiwizowaną paczkę,
5. każde archiwum rozpakowuje tylko tymczasowo,
6. składa wszystkie raw records do jednego:

```text
collected/market_merged.jsonl
```

7. usuwa tymczasowo rozpakowane dane,
8. dopiero potem pobiera dane historyczne REST.

Jeżeli po awarii istnieje jednocześnie archiwum i staging tej samej paczki, deduplikacja odbywa się po `batchId` i archiwum ma pierwszeństwo.

## Co dociąga enrichment

Dla zakresu czasowego wykrytego z collected data:

- spot OHLCV 1m,
- perpetual OHLCV 1m,
- mark price 1m,
- index price 1m,
- premium index 1m,
- open interest 5m,
- long/short ratio 5m,
- funding history,
- instrument metadata.

Domyślnie REST backfill zaczyna się 14 dni przed pierwszym collected tickiem i próbuje sięgać 24 h za ostatni tick (nie dalej niż dostępne „teraz”).

## Foldery

```text
data/
  raw/
    archives/
      27.08.2026_12-10 - 27.08.2026_12-46.tar.gz
      27.08.2026_12-46 - 27.08.2026_13-22.tar.gz
    staging/                 # normalnie pusty; recovery po awarii
    live/
      current/
        batch.json
        market.jsonl         # aktualna paczka < 150 MB
  complete/
    complete_<FROM>__<TO>/
      collected/
        market_merged.jsonl  # wszystkie archiwa + current
      rest/
        spot_1m.jsonl
        perp_1m.jsonl
        mark_1m.jsonl
        index_1m.jsonl
        premium_1m.jsonl
        open_interest_5m.jsonl
        long_short_5m.jsonl
        funding.jsonl
        spot_instrument.json
        perp_instrument.json
      manifest.json
```

## Test lokalny

Wymagany Node.js >= 18.18 oraz polecenie `tar`. Windows 10/11 ma `tar.exe` standardowo; na typowym Linux/VPS również jest dostępny.

```bash
npm install
npm test
npm run check
npm start
```

W zestawie testów znajduje się test mikro-rotacji: limit około 1.8 KiB wymusza kilka archiwów, po czym program odtwarza pełny `market_merged.jsonl` z archiwów + current i porównuje wszystkie rekordy 1:1.

Status collectora:

```bash
curl http://127.0.0.1:3042/status
```

Status pokazuje m.in. bieżący rozmiar paczki, procent wypełnienia i limit 150 MiB.

Po kilku minutach zatrzymaj `Ctrl+C` i sprawdź zapis:

```bash
npm run validate
```

Następnie zbuduj komplet:

```bash
npm run enrich
```

Możesz jawnie wskazać katalog:

```bash
npm run enrich -- --input ./data/raw --output ./data/complete
```

Albo podać zakres REST:

```bash
npm run enrich -- --from 2026-08-20T00:00:00Z --to 2026-08-27T00:00:00Z
```

## Test rotacji z małym limitem ręcznie

Jeśli chcesz zobaczyć rotację bez czekania na 150 MB:

Linux / VPS:

```bash
RAW_BATCH_MAX_MB=1 npm start
```

PowerShell:

```powershell
$env:RAW_BATCH_MAX_MB="1"
npm start
```

Po teście usuń zmienną albo ustaw z powrotem `150`.

## Para spot

Domyślne ustawienie to `BTCUSDT`. Jeśli chcesz jako główny spot obserwować BTCUSDC:

Windows CMD:

```bat
set SPOT_SYMBOL=BTCUSDC
npm start
```

PowerShell:

```powershell
$env:SPOT_SYMBOL="BTCUSDC"
npm start
```

Linux/VPS:

```bash
SPOT_SYMBOL=BTCUSDC npm start
```

Perpetual może nadal zostać `BTCUSDT` jako niezależny sensor rynku.

## PM2 / VPS

Po skopiowaniu folderu na VPS:

```bash
cd /srv/market-ml-collector
npm install --omit=dev
pm2 start ecosystem.config.cjs
pm2 save
pm2 status
pm2 logs ml-market-collector
curl http://127.0.0.1:3042/status
```

`ecosystem.config.cjs` ma już:

```text
RAW_BATCH_MAX_MB=150
```

## Najważniejsze ustawienia

- `SPOT_SYMBOL=BTCUSDT`
- `PERP_SYMBOL=BTCUSDT`
- `ORDERBOOK_DEPTH=50`
- `ORDERBOOK_SAMPLE_MS=1000`
- `SAVE_ORDERBOOK_DELTAS=false`
- `RAW_BATCH_MAX_MB=150`
- `DATA_DIR=./data`
- `STATUS_PORT=3042`
- `BYBIT_REST_BASE=https://api.bybit.com`
- `BYBIT_WS_SPOT_URL=wss://stream.bybit.com/v5/public/spot`
- `BYBIT_WS_LINEAR_URL=wss://stream.bybit.com/v5/public/linear`

## Dlaczego tak

Na VPS trzymamy tylko dane, których później nie da się dobrze odtworzyć historycznie. Rotacja ogranicza rozmiar pojedynczego pliku i zabezpiecza przed jednym wielkim, trudnym do przenoszenia plikiem. Lokalny enrichment odtwarza jednolity raw dataset i dociąga wszystkie serie, które można odzyskać z REST.
