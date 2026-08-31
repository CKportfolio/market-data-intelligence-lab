# Architektura streaming research

## Zasada nadrzedna

Kosztowne tick/orderbook RAW jest archiwum zrodlowym. Nie jest materializowane do wielkiego pliku roboczego.

## Pass 1 — RAW -> micro 1m

Kazdy `.tar.gz` jest otwierany sekwencyjnie. `market.jsonl` jest czytany linia po linii bez extract do filesystemu. Ticki i L50 sa redukowane do minutowych statystyk. Na granicy dni trzymamy tylko maly bufor.

## Enrichment

REST nie jest osobnym etapem przygotowujacym pelny dataset. `RestDayCache` zapewnia serie dla konkretnego dnia w momencie, gdy pipeline ich potrzebuje. Cache jest persistent i wspolny dla kolejnych research runs.

## Geometry / feature extraction

Dzien jest jednostka robocza. Dla kazdego dnia ladowany jest tylko potrzebny left-context. Triple-test moze wymagac dwoch maksymalnych gapow, dlatego context jest liczony z konfiguracji, a nie wpisany na sztywno.

## Labeling

Dla kandydatow danego dnia pobierana jest tylko potrzebna przyszla cena do maksymalnego horyzontu. Brak pelnej przyszlosci oznacza `censored`, nie `timeout`.

## ML

Do pandas trafia finalnie compact candidate table. To jest celowy punkt redukcji: model uczy sie na przypadkach/epizodach, nie na miliardach raw messages.
