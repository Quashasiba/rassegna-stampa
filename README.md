# La Rassegna

Rassegna stampa automatica, aggiornata **tre volte al giorno** (circa alle 7:00, 13:00 e 19:00 ora italiana) tramite GitHub Actions e pubblicata con GitHub Pages.

## Temi seguiti

- Espansione dei robotaxi nel mondo
- Nuove funzionalità ed espansione di Revolut
- Dati sul mercato delle auto elettriche
- Nuove ZTL e pedonalizzazioni a Milano
- Uber e Bolt in Italia

## Come funziona

- `topics.json` — definisce i temi e le query sui feed RSS di Google News (italiano e inglese). Per aggiungere o modificare un tema basta modificare questo file.
- `generate.py` — legge i feed, elimina i duplicati, tiene le notizie delle ultime 72 ore e genera `index.html`. Usa solo la libreria standard di Python.
- `.github/workflows/rassegna.yml` — esegue lo script tre volte al giorno e pubblica la pagina aggiornata.

## Aggiornamento manuale

Dalla scheda **Actions** del repository: `Aggiorna rassegna stampa` → **Run workflow**.
