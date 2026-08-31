# Market ML Research Engine — STREAMING v0.2

To jest przebudowa `market-ml-research-v0.1` pod dlugoterminowy korpus z collectora bez tworzenia gigantycznego `market_merged.jsonl`.

## Najwazniejsza zmiana

Stary pipeline:

```text
setki .tar.gz -> rozpakowanie/scalenie -> market_merged.jsonl -> enrichment -> research
```

Nowy pipeline:

```text
kolejny .tar.gz
    ↓  (tarfile stream, bez extract)
RAW JSONL czytany linia po linii
    ↓
agregacja mikrostruktury do 1 min
    ↓
dzienna partycja work/.../micro_1m/YYYY-MM-DD.csv.gz
    ↓
REST enrichment tylko dla potrzebnego dnia
    ↓
cache/rest/<series>/YYYY-MM-DD.jsonl.gz
    ↓
dzienna partycja enriched_1m
    ↓
rolling context ~1–2 dni
    ↓
double/triple candidates + features
    ↓
future price z dziennego REST cache -> barrier labels
    ↓
compact candidate table
    ↓
walk-forward ML + embargo + nietykalny holdout
```

**RAW nigdy nie jest scalany do jednego pliku.** Program nie rozpakowuje calego archiwum na dysk. Otwiera kolejne `.tar.gz`, czyta `market.jsonl` strumieniowo i od razu redukuje dane.

## Input

Wskazujesz jeden z tych katalogow:

```text
D:\marketdata\data\raw
D:\marketdata\data\raw\archives
```

Program wykryje `*.tar.gz`, odczyta manifesty, posortuje paczki po czasie i sprawdzi przerwy/nakladanie.

Formatem produkcyjnym jest `Market ML Collector v1.1+`:

```text
archive.tar.gz
  batch_.../
    market.jsonl
    manifest.json
```

Archiwa z `dense recorder` do badania adaptive sampling sa innym datasetem. Domyslnie program je odrzuci, aby przypadkiem nie pomieszac dwoch eksperymentow.

## Live enrichment / dzienny cache

Program pobiera publiczne dane historyczne Bybit dopiero wtedy, gdy potrzebuje konkretnego dnia. Cache jest wspolny miedzy badaniami, wiec ponowne uruchomienie nie pobiera zamknietych dni drugi raz.

Serie:

- spot OHLCV 1m,
- perpetual OHLCV 1m,
- mark price 1m,
- index price 1m,
- premium index 1m,
- open interest 5m,
- long/short ratio 5m,
- funding history.

Cache:

```text
cache/rest/
  BTCUSDC__BTCUSDT/
    spot_1m/2026-08-27.jsonl.gz
    perp_1m/2026-08-27.jsonl.gz
    mark_1m/2026-08-27.jsonl.gz
    ...
```

Zamkniete dni sa traktowane jako immutable. Biezacy dzien moze byc odswiezony.

Domyslny endpoint:

```text
https://api.bybit.com
```

Mozna zmienic:

```cmd
set BYBIT_REST_BASE=https://...
```

Symbole sa automatycznie wykrywane z RAW. Mozna je nadpisac zmiennymi `SPOT_SYMBOL` i `PERP_SYMBOL`.

## Co zostaje z v0.1

Zachowane zostaly najwazniejsze zasady badania:

- multi-scale double/triple bottom/top,
- `pivot_time` osobno od chwili, kiedy pivot zostal potwierdzony,
- brak backdatingu,
- features tylko z przeszlosci i zamknietej aktualnej minuty,
- `signal_available_ms = signal_ms + 60000`,
- barrier outcomes TP-before-SL,
- jesli TP i SL sa w tej samej swiecy 1m -> konserwatywnie SL-first,
- MFE / MAE,
- walk-forward,
- embargo rowne maksymalnemu horyzontowi labela,
- osobny final holdout,
- replay zawierajacy OOS/holdout, bez in-sample backpaint,
- grupowanie nakladajacych sie sygnalow w niezalezne epizody; do model selection/evaluation uzywany jest pierwszy sygnal epizodu (bez wybierania najlepszego z przyszlosci),
- ablation `historical_backfillable_only` vs `full_microstructure`, aby sprawdzic, czy kosztowne RAW trade/orderbook faktycznie wnosi wartosc ponad tanie dane historyczne.

## Partycje zamiast gigantycznych plikow

`work/<dataset-id>/` jest technicznym cache badania:

```text
micro_1m/
  2026-08-27.csv.gz
  2026-08-28.csv.gz

enriched_1m/
  2026-08-13.csv.gz
  ...

candidate_parts/
  2026-08-27.csv.gz
  ...
```

Mikro/agregaty minutowe sa o kilka rzedow wielkosci mniejsze od tickowego RAW. Kandydaci sa jeszcze mniejsi.

Finalny `candidates_with_features_outcomes.csv.gz` jest jednym plikiem celowo — jest to juz kompaktowy zbior przypadkow ML, a nie surowy korpus rynku.

## RAM i dysk

Podczas skanowania RAW program trzyma w pamieci tylko niewielki slownik agregatow minutowych (maksymalnie okolo dwoch dni, aby tolerowac drobne przestawienia timestampow na granicy UTC).

Przy wykrywaniu geometrii ladowany jest tylko dzien + rolling left-context wystarczajacy do najdluzszej potrojnej formacji i feature lookbacku.

Nie ma etapu wymagajacego jednoczesnego rozpakowania dziesiatek GB RAW.

## Uruchomienie Windows

Najprosciej:

```text
start.bat
```

Jesli Windows blokuje `.bat`, z CMD w katalogu programu — pierwszy start, jedna linia:

```cmd
py -3 -m venv .venv && .venv\Scripts\python.exe -m pip install --upgrade pip && .venv\Scripts\python.exe -m pip install -r requirements.txt && .venv\Scripts\python.exe main.py
```

Kolejne uruchomienia:

```cmd
.venv\Scripts\python.exe main.py
```

Szybki research/pipeline check (wszystkie depth=10):

```cmd
.venv\Scripts\python.exe main.py --quick
```

Ze sciezka input od razu:

```cmd
.venv\Scripts\python.exe main.py --input "D:\marketdata\data\raw"
```

Wylacz internet i korzystaj tylko z juz zbudowanego cache:

```cmd
.venv\Scripts\python.exe main.py --input "D:\marketdata\data\raw" --offline
```

Wymus nowe przeliczenie `work/`:

```cmd
.venv\Scripts\python.exe main.py --input "D:\marketdata\data\raw" --rebuild
```

## Glebokosc badania

Tak jak w v0.1 program pyta o szesc warstw:

```text
10 = lekkie
20 = srednie / rekomendowane
40 = mocne
```

Warstwy: geometria, features, outcomes, validation, model search, stability.

## Output

```text
output/YYYY-MM-DD__YYYY-MM-DD_streaming/
  manifest.json
  research_config.json
  dataset_summary.json

  candidates/
    candidates_with_features_outcomes.csv.gz

  reports/
    SUMMARY.txt
    target_metrics.csv
    hyperparameter_search.csv
    thresholds.csv
    calibration_bins.csv
    champion_holdout_predictions.csv
    feature_importance.csv
    range_regression_holdout.csv
    feature_group_ablation.csv

  preset/
    preset.json
    feature_schema.json
    champion_model.joblib
    mfe_regressor.joblib
    mae_regressor.joblib
    price_1m.csv.gz
    replay_predictions.csv.gz
    replay_timeline_1m.csv.gz
```

## Wznawianie

Po przerwaniu `Ctrl+C` zostaja:

- `cache/rest` — pobrany enrichment,
- `work/<dataset-id>/micro_1m` — juz zagregowany RAW,
- pozostale male partycje.

Jesli skan RAW zakonczyl sie poprawnie, `micro_done.json` pozwala nie skanowac ponownie wszystkich tarow przy kolejnym starcie.

## Testy

```cmd
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Pelny syntetyczny smoke test bez internetu:

```cmd
.venv\Scripts\python.exe smoke_streaming.py
```

Smoke test tworzy syntetyczne archiwa collectora, czyta je bez extract/merge, buduje dzienne partycje, korzysta z dziennego enrichment cache, wykrywa kandydatow, labeluje ich i wykonuje maly walk-forward ML.

## Ważne ograniczenie

To nadal jest **research engine**, nie bot transakcyjny. `preset.json` ma sluzyc do pozniejszego forward-testu na danych, ktorych model nie widzial.

Najbogatsze mikro-cechy orderbooka z normalnego collectora sa ograniczone przez cadence snapshotow zapisanych w RAW. Gdy adaptive collector zostanie zatwierdzony, ten sam streaming research engine moze konsumowac jego archiwa bez zmiany modelowej architektury — trzeba jedynie zachowac kompatybilny schemat snapshotow i metadata samplingu.
