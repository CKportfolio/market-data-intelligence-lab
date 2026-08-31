# Market Data Intelligence Lab

**Od prostego grid bota do infrastruktury badawczej dla osobnego krótkoterminowego bota sygnałowego.**

Punktem wyjścia nie było „zróbmy ML”. Punktem wyjścia były próby uczynienia grid bota coraz bardziej „przebiegłym” przez dokładanie kolejnych sygnałów rynkowych. W praktyce zwiększało to złożoność infrastruktury, ale nie dawało proporcjonalnej korzyści.

Zamiast dalej komplikować grid, rozdzieliłem problem na dwa systemy:

- **grid bot** pozostaje prostszą strategią o własnej logice,
- **predykcja krótkoterminowa** staje się osobnym kierunkiem R&D dla przyszłego bota sygnałowego.

Główne pytanie badawcze brzmi:

> **Czy mikrostruktura orderbooka i inne dane rynkowe bezpośrednio przed potwierdzeniem formacji mogą dostarczać sygnału pozwalającego ocenić krótkoterminowe wejście?**

Żeby to w ogóle sprawdzić, potrzebowałem własnego datasetu o odpowiedniej rozdzielczości. Stąd powstał cały ciąg:

```text
stały collector 1 s
→ dense recorder
→ adaptive sampling research
→ walidacja 480 polityk
→ adaptive collector
→ streaming ML research
→ badanie predyktora formacji
→ przyszły signal bot
```

## Wynik adaptacyjnego collectora

Na chronologicznym holdoucie:

- **72,73% mniej** pełnych snapshotów L50 względem baseline 1 s,
- **100%** Tier A event recall,
- **100%** Tier B event recall,
- **100%** Tier A snapshot recall,
- **94,44%** Tier B snapshot recall,
- reaction p95: **250 ms**,
- **9/9** bramek jakości zaliczonych.

To wynik jednego zamrożonego okresu rynku, więc nie jest przedstawiany jako uniwersalna gwarancja dla wszystkich reżimów.

## Co jest ML, a co nim nie jest

Adaptive sampler **nie jest modelem ML**. To deterministyczna polityka wybrana eksperymentalnie na danych kalibracyjnych i sprawdzona na chronologicznym holdoucie.

Warstwa ML znajduje się w `05-ml-research-engine` i obejmuje m.in.:

- HistGradientBoostingClassifier,
- kalibrację prawdopodobieństw,
- regresję MFE/MAE,
- walk-forward validation,
- embargo względem horyzontu labela,
- untouched holdout,
- Brier score / log loss / ROC AUC / Average Precision,
- permutation importance,
- porównanie `historical_backfillable_only` vs `full_microstructure`.

Repo **nie twierdzi**, że istnieje już dochodowy predyktor ani gotowy live signal bot. Pokazuje natomiast pełną drogę badawczą: od problemu architektonicznego, przez własne dane i eksperyment, aż po infrastrukturę potrzebną do uczciwego testowania hipotezy ML.

Pełna wersja README: [README.md](README.md).
