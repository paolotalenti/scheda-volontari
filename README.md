# Scheda Volontari — Stato del progetto

App web per la gestione delle visite dei volontari agli assistiti. Usata da "Tempo Insieme".

## Stack tecnico

- **Backend**: Python / Flask
- **Database**: PostgreSQL su Render
- **Hosting**: Render Web Service (piano Free) — URL: `https://scheda-volontari2.onrender.com`
- **Repository GitHub**: `https://github.com/paolotalenti/scheda-volontari`
- **Deploy**: automatico da GitHub (branch `main`)

## Funzionalità

- Inserimento visite da parte dei volontari (senza login)
- Area admin protetta da password: report, filtri per volontario e data
- Gestione volontari: aggiunta, modifica, eliminazione
- Gestione assistiti
- Export CSV e PDF del report
- Backup e ripristino dati

## Backup — architettura (aggiornato 31 maggio 2026)

I backup vengono salvati nella tabella `backups` del database PostgreSQL. Non vengono scritti su disco (il filesystem di Render Free è effimero e si azzera ad ogni restart).

### Struttura tabella `backups`
```
id          SERIAL PRIMARY KEY
filename    TEXT               — es. backup_dati_20260531_020000.csv
created_at  TIMESTAMP WITH TIME ZONE
csv_content TEXT               — contenuto completo del CSV
```

I backup più vecchi di **30 giorni** vengono eliminati automaticamente ad ogni nuovo backup.

### Come funziona il backup automatico

Due job su **cron-job.org** gestiscono il servizio:

| Job | URL | Frequenza | Scopo |
|---|---|---|---|
| Keepalive | `https://scheda-volontari2.onrender.com` | Ogni 5 min | Impedisce il sleep su Render Free |
| Backup notturno | `https://scheda-volontari2.onrender.com/run_backup?token=…` | Ogni giorno alle 02:00 | Sveglia il servizio ed esegue il backup |

L'endpoint `/run_backup` è protetto da token. Il token è nella variabile d'ambiente `BACKUP_TOKEN` su Render.

### Backup manuale

Dall'area admin → pulsante **Backup** → salva immediatamente nel DB.

### Ripristino

Dall'area admin → **Ripristino** → lista di tutti i backup disponibili (ultimi 30 giorni) con data e ora. Seleziona il backup, inserisci la password admin, clicca Ripristina. È anche possibile scaricare qualsiasi backup come file CSV.

## Variabili d'ambiente su Render

| Variabile | Descrizione |
|---|---|
| `DATABASE_URL` | URL connessione PostgreSQL |
| `SECRET_KEY` | Chiave segreta Flask per le sessioni |
| `ADMIN_PASSWORD` | Password area admin |
| `BACKUP_TOKEN` | Token per l'endpoint `/run_backup` |

## Deploy

Ogni push su `main` avvia il deploy automatico su Render. Per forzare un deploy manuale: Dashboard Render → `scheda-volontari2` → Manual Deploy.

## Note operative

- **Cold start**: il piano Free di Render va in sleep dopo 15 minuti di inattività. Il keepalive su cron-job.org lo mantiene sveglio. Se il servizio è addormentato la prima richiesta può richiedere 30-60 secondi.
- **Upgrade a Starter ($7/mese)**: elimina il cold start completamente — da valutare se il cold start diventa un problema per gli utenti.
