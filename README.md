# FLAC-Serv IRC DCCore Daemon 🚀

En extremt snabb, stabil och skräddarsydd IRC DCC-fildelningsmotor (OmenServe-arkitektur) byggd i Python 3.10 för Undernet. Scriptet är optimerat för Proxmox LXC-containrar och levererar fildelningsnotiser, avancerad databasstatistik samt realtidsövervakning med mIRC-färger i absolut guldstandard.

## ✨ Nyckelfunktioner

- **⚡ VIP Express-kö:** Isolerad högprioriterad kö (`vip_queue`) som skjuter ut privata kö-kontroller (`@bot-name-que`) och sök-headers på under 1 millisekund, helt opåverkad av vanliga flood-skydd eller kanalreklam.
- **❄️ Smart Frysbox & Realtime-väckning:** Om en användare med filer i kön råkar göra `PART` eller tappar nätverket (`QUIT`), fryses kön automatiskt i en bakgrundstråd under 5 minuter. Om användaren gör `JOIN` tinar kön upp direkt på 0ms och fortsätter skicka.
- **🎨 Centralt mIRC Block-tema:** Fullständigt temasatt via `announce.py` med tunga färgblock (Turkos/Mörkröd) och kritvita bakgrundsplattor, kliniskt rensad från färgspill och klientspecifika cache-rutor.
- **📊 7-Kolonns Avancerad Databas:** Skottsäker live-statistik (`stats.txt`) som mäter totalt skickade filer/bytes, gårdagens och dagens aktivitet samt synkade listdatum i realtid med tvingad disk-flush (`fsync`).
- **🛠️ Dedikerad VIP Debug-kanal:** Helautomatisk nätverkssluss som strömmar tidstämplade och färgkodade CLI-loggar (`[SENT]`, `[PART]`, `[QUIT]`, `[JOIN]`) live till kanalen `#flac-debug` via VIP-expressen.

## 📁 Filstruktur

- `oserve.py` — Centrala motorn, trådhanteraren och flood-skyddsköerna.
- `irc.py` — Dedikerad nätverksmodul, asynkron loop och kedjad kommandotolk.
- `dcc.py` — DCC-handskakningar, socket-sändare och smarta frysbox-timers.
- `announce.py` — Centrala mIRC-temat, kanalannonseringar och VIP-debugmotorn.
- `commands.py` — Användarkommandon (`que`, `remove`) isolerade från nätverksloopen.
- `stats_mgr.py` — Dum datamodul för storleks- och hastighetsberäkningar.
- `db.py` — I/O-gränssnitt för databasen med tvingat skrivskydd vid filslut.
- `config.py` — Central konfigurationsfil för nätverk, slots, timers och färgkoder.

## 🚀 Installation & Uppstart

### Förutsättningar
Scriptet är utvecklat och testat för **Python 3.10** i en Linuxmiljö (t.ex. Debian/Ubuntu LXC i Proxmox).

### Starta Daemonen
För att starta om boten helt rent, rensa dolda cache-filer och tvinga fram en ny inläsning av kodändringar i RAM-minnet, kör du:
```bash
pkill -f oserve.py && rm -rf __pycache__ */__pycache__ && python3 oserve.py
```
