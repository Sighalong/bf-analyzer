# Prisjakt Black Friday pris-agent (Python)

Denne agenten finner produkter på prisjakt.no og leser **tekstlige felter** som
**«Laveste pris 3 mnd …»** (med dato) og **«Laveste pris nå»**, beregner forskjellen, flagger mistenkelige tilfeller, og lager **CSV + Markdown** med **topplister**.

> Skriptet bruker **Playwright** for å rendre JavaScript og en **robust regex-tilnærming** for å plukke ut feltene fra side-teksten. Ingen uoffisielle API-er.

## Kom i gang

1. Lag et Python-venv (anbefalt) og installer pakkene:
   ```bash
   pip install -r requirements.txt
   playwright install chromium
   ```

2. Kjør agenten med standardkategorier:
   ```bash
   python prisjakt_agent.py --categories "TV" "Mobiltelefoner" "Bærbare PC-er" "Hodetelefoner" "Robotstøvsugere" "Skjermer" "Smartklokker" --max-per-category 20
   ```

   Eller bruk egne kategorier:
   ```bash
   python prisjakt_agent.py --categories "RTX 4070" "OLED TV" --max-per-category 15
   ```

3. (Valgfritt) Har du en egen liste med produkt-URL-er?
   ```bash
   python prisjakt_agent.py --product-urls urls.txt
   ```

4. Resultater:
   - `prisjakt_output.csv`
   - `prisjakt_output.md` (inkl. topplister)

## Parametre

- `--categories` – søkeord/temaer som agenten bruker for å finne produkter.
- `--max-per-category` – maks antall produkter per kategori/tema (default 20).
- `--product-urls` – fil med ferdige produktlenker (en per linje); brukes i tillegg til funn fra søk.
- `--min-price-nok` – filtrer bort produkter med nå-pris under denne grensen (default 500).
- `--out-prefix` – prefiks for utfilene (default `prisjakt_output`).

## Metode

- **Oppdagelse:** Skriptet går til forsiden, søker på hvert kategoriord, og samler produktlenker fra resultatsiden (fornuftig skrolling + deduplisering).
- **Uthenting:** På produktsiden rendres innholdet, og skriptet matcher følgende mønstre i synlig tekst:
  - `Laveste pris 3 mnd` / `Laveste pris siste 3 mnd` / `Laveste pris 90 dager` *(pris og valgfri dato)*
  - `Laveste pris nå` / `Dagens laveste pris` / `Den billigste prisen … (nå)` / `Nå`
  - (Hvis synlig) `Laveste pris 30 dager` / `Laveste pris 1 mnd`
- **Beregning:** Δ3m, %Δ3m og – hvis 30-dagers-feltet finnes – Δ30d, %Δ30d.
- **Flagging:** Konservativt mistenkelig hvis `%Δ3m ≥ 15 %`, eller hvis `nå` ligger ≥10 % over `Min30` (når tilgjengelig).
- **Rapport:** CSV + Markdown-tabell og to topplister (absolutt/prosentvis økning siste 3 mnd).

## Tips

- Prisjakt kan endre markup. Regex-tilnærmingen tåler variasjoner i teksten, men hvis noe feiler, bruk `--product-urls` med ferdige lenker.
- Kjør med lavt volum (f.eks. 10–50 produkter) og øk gradvis.
- Respekter robots/terms og sett opp kjøring utenfor travle perioder.

## Feilsøking

- Finner ingen produkter ved søk? Forsiden/markup kan ha endret seg. Legg inn produkt-URL-er via `--product-urls`.
- Får lite treff på `Laveste pris 3 mnd`? Noen sider viser kun graf. Øk ventetid/scroll eller prøv flere produkter.
