# Chess Weekly Report

Genera automàticament un informe setmanal (HTML amb gràfics) de les teves partides
de chess.com, analitzades amb Stockfish (depth 18), i te l'envia per correu.

## Configuració (un cop)

1. Crea un repositori privat nou a GitHub i puja aquest contingut.
2. Ves a **Settings → Secrets and variables → Actions → New repository secret**
   i afegeix:
   - `SMTP_SERVER` — p. ex. `smtp.gmail.com`
   - `SMTP_PORT` — p. ex. `465`
   - `SMTP_USERNAME` — el teu correu
   - `SMTP_PASSWORD` — contrasenya d'aplicació (NO la contrasenya normal;
     a Gmail: Compte Google → Seguretat → Contrasenyes d'aplicació)
   - `REPORT_EMAIL` — a quina adreça vols rebre l'avís

3. Si el teu usuari de chess.com no és `baikthemaik`, edita la línia
   `--username baikthemaik` a `.github/workflows/weekly-chess-report.yml`.

4. Per provar-ho sense esperar al dilluns: pestanya **Actions** → *Weekly Chess
   Report* → **Run workflow** (botó a la dreta).

5. Activa **GitHub Pages**: Settings → Pages → Source: *Deploy from a branch*
   → branch `main`, carpeta `/docs`. Guarda. Al cap d'un minut tindràs la teva
   PWA a `https://<el-teu-usuari>.github.io/<nom-del-repo>/`.

## Instal·lar-la al mòbil (PWA)

1. Obre l'URL de GitHub Pages des del navegador del mòbil (Chrome a Android,
   Safari a iOS).
2. **Android (Chrome)**: menú (⋮) → "Afegir a la pantalla d'inici" / "Instal·lar app".
3. **iOS (Safari)**: botó de compartir → "Afegeix a la pantalla d'inici".
4. Ja tens una icona pròpia a la pantalla d'inici que obre la llista d'informes
   a pantalla completa, sense barra de navegador.

Important: la PWA només **mostra** informes ja generats — no fa cap anàlisi.
Cada dilluns GitHub Actions genera l'informe nou i, en obrir la PWA, apareix
automàticament a la llista (prem el botó ⟳ si no es refresca sol).

## Estructura

- `scripts/weekly_report.py` — descàrrega + anàlisi Stockfish + generació HTML
  + actualització de l'índex de la PWA.
- `.github/workflows/weekly-chess-report.yml` — cron setmanal (dilluns 07:00 UTC).
- `docs/` — la PWA (arrel servida per GitHub Pages):
  - `index.html`, `manifest.json`, `sw.js`, icones — l'app instal·lable.
  - `reports_index.json` — llista d'informes generats (es va acumulant).
  - `reports/` — historial d'informes HTML autocontinguts.

## Notes

- L'"accuracy" és una aproximació pròpia (mètode estil Lichess basat en la
  pèrdua de win% per jugada), no la fórmula exacta de chess.com, que és
  propietària i no publicada.
- Cada execució analitza totes les partides classificades dels últims 7 dies
  (ràpides/blitz incloses). Amb ~15-20 partides/setmana a depth 18, el job
  triga uns 5-15 minuts — dins dels límits gratuïts d'Actions (2000 min/mes
  en repos privats).
- Si canvies de nom d'usuari a chess.com, actualitza el `--username`.

## Anàlisi de patrons (manual)

Cada informe inclou un botó **"📋 Copiar dades"** que copia al porta-retalls
un JSON amb totes les dades ja calculades per Stockfish d'aquella setmana
(jugades marcades com a error, fase de joc, obertures, resultats — mai el PGN
sencer ni cap altra dada personal).

Quan vulguis una lectura de patrons (obertures que et van pitjor, tendències
per color, fases de joc on falles més sovint), enganxa aquest JSON en una
conversa amb Claude (claude.ai) o un altre assistent, i demana't l'anàlisi.

Aquest pas és manual expressament — vam provar d'automatitzar-lo amb l'API
de Gemini, però els canvis freqüents de models/API d'aquell servei ho van fer
poc fiable per a un ús personal. El JSON queda desat igualment
(`docs/reports/payload_YYYY-MM-DD.json`), així que sempre el tens disponible.
