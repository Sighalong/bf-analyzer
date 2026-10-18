# bf-analyzer – Black Friday-prisvakt

Denne lille verktøykassen følger prisene i norske nettbutikker dag for dag og
sier fra når noe lukter **falsk rabatt**: butikken som hever prisen i ukene før
Black Friday, for så å «sette den ned» igjen med store rabattskilt.

Den henter priser per butikk fra **Prisjakt.no** og **Klarna** (Prisguide.no
sender deg videre til Klarna), lagrer alt i en SQLite-database og lager rapporter
(CSV, Markdown og en enkel nettside). Den bruker bare Pythons standardbibliotek,
så den går fint på en gammel **Raspberry Pi 1B**. Black Friday 2026 er
**27. november**, så start innsamlingen tidlig – jo flere dager med historikk,
desto bedre treff.

## Kom i gang (Mac/Linux, uten installasjon)

Du trenger bare Python 3.7 eller nyere:

```bash
python3 -m bfa run --categories TV --max-per-category 5
```

Resultatet havner i `outputs/` (`bfa.sqlite`, `bfa_report.csv`, `bfa_report.md`).
Vil du se det i nettleseren?

```bash
python3 app.py     # åpne http://localhost:8080
```

## Raspberry Pi 1B

Testet som oppsett på papiret: Raspberry Pi OS Lite, ingen pip-pakker, ingen nettleser.

```bash
sudo apt update && sudo apt install -y git python3
git clone <repo-url> bf-analyzer
cd bf-analyzer
bash pi/install.sh
```

Skriptet sjekker Python (≥ 3.7) og SQLite (≥ 3.24), lager `outputs/`, installerer
og aktiverer systemd-enhetene og skriver ut adressen. Det kan kjøres på nytt uten
skade. Deretter:

- Åpne `http://<pi>:8080` i nettleseren for rapporten.
- Innsamlingen går automatisk hver dag ca. kl. 06:30 (tilfeldig forsinkelse på inntil 30 min).
- Se når neste kjøring er: `systemctl list-timers bf-analyzer-collect.timer`
- Se loggen: `journalctl -u bf-analyzer-collect`
- Start en innsamling med en gang: `sudo systemctl start bf-analyzer-collect.service`

Merk: minnegrensene (`MemoryMax`) i enhetene krever at cgroup-minne er slått på.
Hvis systemd klager, legg `cgroup_enable=memory cgroup_memory=1` til i `/boot/cmdline.txt`
(eller `/boot/firmware/cmdline.txt`) og start på nytt. En innsamling kan ta lang tid
på en Pi 1B fordi verktøyet venter minst 2 sekunder mellom hver forespørsel.

## Parametre

Kommandoer: `python3 -m bfa run` (samle + rapport, standard), `collect` (bare samle)
og `report` (bare rapport). `python prisjakt_agent.py ...` fungerer fortsatt og
betyr `run`.

| Flagg | Betydning |
|---|---|
| `--categories` | Standard er alle: `tv mobil laptop hodetelefoner skjerm smartklokke robotstovsuger nettbrett spillkonsoll kaffemaskin stovsuger soundbar hoyttaler kamera skjermkort lopesko jakke varmeovn parfyme`. Norske navn som «Bærbare PC-er» fungerer også. `kamera` finnes bare på Klarna; `skjermkort`, `lopesko`, `jakke`, `varmeovn` og `parfyme` bare på Prisjakt. |
| `--sources` | `prisjakt`, `klarna` eller begge (standard). |
| `--max-per-category` | Maks nye produkter per kategori (standard 8). |
| `--max-products` | Maks produkter per kilde per kjøring (standard 200; en full kjøring tar ca. 25 min per kilde på en Pi 1B). |
| `--product-urls FIL` | Fil med produkt-URL-er, én per linje. |
| `--min-price-nok` | Hopp over funn med pris under dette (standard 500). |
| `--db` | SQLite-fil (standard `$OUTPUT_DIR/bfa.sqlite`). |
| `--out-prefix` | Prefiks for rapportfilene (standard `$OUTPUT_DIR/bfa_report`). |
| `--today` | Analysedag `ÅÅÅÅ-MM-DD` (standard: i dag, Oslo-tid). |
| `--delay` | Sekunder mellom forespørsler per side (minst 2, standard 3). |
| `--baseline-end` | Hvor normalprisen slutter: `auto` (1. oktober i sesongen, ellers glidende), `rolling` eller en dato `ÅÅÅÅ-MM-DD`. |
| `--top` | Antall rader i toppliste (standard 25). |
| `--product kilde:id` | Vis prisserien per butikk for ett produkt. |

Miljøvariabler: `OUTPUT_DIR` (standard `./outputs`), og for nettsiden `PORT` (8080) og `HOST` (0.0.0.0).

Nettsiden har disse adressene: `/` (rapport), `/run` (POST eller GET, starter en
innsamling i bakgrunnen; svarer 409 hvis en allerede pågår), `/status` og `/files/<navn>`.

Produkter som er funnet én gang, sjekkes på nytt hver dag i 45 dager. Det er slik
prishistorikken per butikk bygges opp.

## Slik fungerer deteksjonen

For hver butikk og produkt sammenlignes en **normalpris (grunnlag)** med prisene etterpå.

- **I Black Friday-sesongen** (1. oktober til to uker etter Black Friday): grunnlaget er
  de 60 dagene *før 1. oktober*. Da kan ikke en butikk som hever prisen tidlig (f.eks. i
  september/oktober) «gjemme» den hevede prisen i sitt eget grunnlag.
- Har butikken for lite data før 1. oktober (f.eks. fordi Pi-en startet innsamlingen
  senere), brukes de første inntil 14 dagene med data før 1. november. Funnet merkes da
  med **«tidlig»** og datoene, så du ser at grunnlaget er svakere.
- **Resten av året:** glidende vindu, dag 31–90 tilbake.
- Velg selv med `--baseline-end` (`auto`, `rolling` eller en dato).

Støy filtreres bort før analysen: tilbud som er **utsolgt**, og tilbud som koster mer enn
**2 × medianprisen** hos de andre butikkene samme dag (feilkoblede pakker, gamle priser).

Flaggene:

- **hike (prishopp):** toppen etter grunnlaget er minst 10 % over normalprisen, og prisen ligger fortsatt nær toppen.
- **fake_discount (falsk rabatt):** prisen steg minst 10 %, falt så minst 8 % fra toppen, men er ikke nevneverdig under grunnlagets laveste pris (innenfor 3 %).
- **before_price (før-pris):** butikkens oppgitte «før-pris» er høyere enn butikkens egen laveste pris de 30 dagene *før den nåværende prisen startet*. Bare Prisjakt, og krever minst 14 dager med data.
- **deal (ekte tilbud):** prisen er minst 5 % under grunnlagets laveste.

Eksempel: en TV koster vanligvis 10 000 kr. I oktober heves prisen til 12 000 kr, og
så er den plutselig «BF-tilbud» til 10 000 kr. Det er ingen reell rabatt (prisen er
tilbake der den alltid var), og den flagges som **falsk rabatt**.

**Butikkrangering:** rapporten viser også hvilke butikker som har flest flaggede
produkter, som andel av produktene de selger blant dem vi følger. Webgrensesnittet
viser en liten prisgraf per funn: normalpris (stiplet), topp (oransje) og nå (grønn),
så du raskt kan se om flagget holder.

Prisjakts egen 3-måneders historikk («billigst blant alle butikker») går tilbake til
juli og gir treff fra dag én. Flaggene per butikk krever at Pi-en samler daglig. Starter
du 18. oktober, får butikkene et «tidlig» grunnlag fra 18.–31. oktober, og november
sammenlignes mot det. Jo tidligere du starter, jo bedre.

## Datakilder og begrensninger

- **Prisjakt.no:** kategorisider (`/c/...`) for å finne produkter, produktsider for priser per butikk og historikk. Søk (`/search`) er forbudt i robots.txt og brukes ikke.
- **Klarna:** kategori- og produktsider, priser per butikk fra sidens strukturerte data. Klarna har ingen før-pris, og prishistorikk-sidene er forbudt i robots.txt.
- Produkter kobles ikke på tvers av sidene.
- robots.txt respekteres, og det er minst 2 sekunders pause mellom forespørsler. Verktøyet forsøker aldri å omgå bot-beskyttelse.

## Viktig å vite

Rapporten viser bare hva prisdataene viser. Det er ikke en juridisk vurdering av
om en butikk har brutt markedsføringsloven eller noe annet. Sjekk alltid
funnene for hånd før du konkluderer med noe.

## Feilsøking

- **403 eller «stop» i loggen:** du er ratebegrenset eller blokkert. Senk volumet (`--max-products`, `--max-per-category`), øk `--delay`, og kjør senere.
- **Få flagg per butikk i starten:** helt normalt. Det trengs flere ukers daglig innsamling før grunnlaget er godt nok.
- **Tjenesten starter ikke:** `journalctl -u bf-analyzer-web` og `journalctl -u bf-analyzer-collect` viser feilen.

## Utvikling

Se `CLAUDE.md` for regler og bakgrunn (bare standardbibliotek, Python 3.7-syntaks,
robots.txt). Kjør testene med:

```bash
python3 -m unittest discover -s tests
```

Agenter og skills for videre arbeid ligger i `.claude/`.

Hosting på Render er også mulig (`render.yaml`, bruker Dockerfile). Historikken
krever den vedvarende disken, ellers forsvinner den ved hver redeploy.
