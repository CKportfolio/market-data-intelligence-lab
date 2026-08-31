# Market Data Intelligence Lab

Repozytorium dokumentuje rozwój systemu od prostego collectora danych rynkowych do eksperymentalnego pipeline'u machine learning przygotowanego pod przyszłego bota sygnałowego.

Projekt powstał jako kolejny etap prac nad [BOT_EU](https://github.com/CKportfolio/BOT_EU).

W grid bocie testowane były kolejne mechanizmy mające uczynić strategię bardziej „świadomą rynku”. W praktyce dokładanie następnych warstw informacji zwiększało głównie złożoność systemu, bez przekonującego dowodu, że daje proporcjonalną poprawę działania samego gridu.

Z tego powstała decyzja:

> **nie komplikować dalej strategii gridowej, tylko oddzielić od niej problem predykcji.**

Nowym celem stał się osobny bot sygnałowy, który w przyszłości mógłby wykorzystywać model ML do rozpoznawania sytuacji pojawiającej się bezpośrednio przed potwierdzeniem krótkoterminowej formacji cenowej.

Żeby taki pomysł można było w ogóle zbadać, najpierw potrzebne były odpowiednie dane.

---

## Co właściwie zbieramy?

Oprócz zwykłych danych o cenie projekt zapisuje także **orderbook**.

Orderbook można najprościej wyobrazić sobie jako bieżącą listę:

- „chcę kupić po tej cenie”,
- „chcę sprzedać po tej cenie”.

Na giełdzie takich zleceń są tysiące i cały czas się zmieniają.

Przykład:

```text
SPRZEDAŻ

101.00   2.1 BTC
100.90   0.8 BTC
100.80   1.4 BTC

----------------
aktualna cena
----------------

100.70   1.8 BTC
100.60   3.2 BTC
100.50   0.6 BTC

KUPNO
