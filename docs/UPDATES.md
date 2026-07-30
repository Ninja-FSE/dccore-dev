# FLAC-Serv Versionsuppdateringar & Projektlogg 📝

Här loggas alla versionsförändringar, optimeringar och buggfixar som gjorts under tidens gång i DCCore-projektet.

---

## 🟦 v1.4.1-BETA (2026-07-30) - "The Intelligent Wildcard Search Update"
### 🚀 Nya funktioner
- **🔍 Dynamisk Wildcard-sökning (`@find`):** Skrivit om sökfunktionen `execute_search` i `list.py` till en asynkron ord-för-ord-skanning. Motorn splittar nu upp söksträngen i enskilda ord och rensar bort lösa bindestreck. Sökningen är helt oberoende av ordning och kräver bara att alla ord existerar på raden för att ge en träff (t.ex. matchar både `metallica red alert` och `red alert metallica`).

### 🐛 Buggfixar & Optimeringar
- **🧹 Diskutrymmes-optimering:** Genomfört en manuell djup-rensning av dolda system- och cachefiler (`/var/cache/apt/` index) i din Proxmox LXC-container, vilket frigjorde över 270 MB välbehövligt andrum på systemdisken inför GitHub-etableringen.

---

## 🟦 v1.4.0-BETA (2026-07-30) - "The Live Rehash & Channel Sync Update"
### 🚀 Nya funktioner
- **🔄 Live Modul-Rehash (`!rehash`):** Integrerat `importlib.reload()` i `commands.py` för att ladda om botens alla centrala moduler live i RAM-minnet helt utan att behöva stänga av eller döda processen i Proxmox-CLI.
- **🌐 Helautomatisk Kanalsynk:** Boten jämför nu dina kanaler live under en rehash. Den skickar automatiskt `JOIN` till nya kanaler som lagts till i `config.py` och kör `PART` i de kanaler som tagits bort.
- **⚡ Människovänlig Latensmätare (`!ping`):** Byggt ett administrationsverktyg isolerat i `commands.py` som mäter botens exakta svarstid till Undernet-servern i sekunder med 3 decimaler (t.ex. `0.129 sec`), helt rensat från dolda färgkrockar och textförvridningar.
- **🎯 Intelligent Färg- och Trådsäkring:** Rensat ut alla råa mIRC-färgkoder från textsträngarna inuti `commands.py` för att eliminera dolda färgkrockar på skärmen, samt synkat den levande reklam-timern så att den tvingas vänta fulla 5 minuter i stället for att starta krockande dubbeltrådar vid en rehash.

### 🐛 Buggfixar & Optimeringar
- **🎛️ Asynkron Rå Socket-sluss:** Skrivit om `send_debug` inuti `announce.py` till att använda en direkt, asynkron `irc_sock.send`-metod. Detta gör att alla loggar, latenssvar och rehash-bekräftelser bypassar botens interna 15-sekunders nätverksbuffert och poppar upp i mIRC på absolut 0.0 sekunder.
- **🧬 Dynamisk Tagg-interferens:** Uppdaterat `irc.py` Del 1 med en dedikerad `PONG`-sluss högst upp i huvudloopen för att fånga latens-svaret före alla andra PRIVMSG-filter.

---

## 🟥 v1.3.0-BETA (2026-07-28) - "The Debug & Theme Sync Update"
### 🚀 Nya funktioner
- **🛠️ VIP Debug-kanal (`#flac-debug`):** Byggt en helautomatisk nätverkssluss som skickar tidstämplade och färgkodade CLI-loggar direkt live in i mIRC.
- **🏎️ Express-logg via VIP:** Kopplat om funktionen `send_debug` till att använda `is_vip=True` så att systemloggarna skjuts ut på 0ms utan att störa den vanliga kön.
- **🏷️ Kategori-taggar för Debug:** Skapat dynamiska och färgkodade etiketter till vänster i loggen: `[SENT]` (Grön), `[PART]` (Röd), `[QUIT]` (Lila) och `[JOIN]` (Turkos) inramade av solida färgstolpar.

### 🐛 Buggfixar & Optimeringar
- **📦 Eliminering av svarta rutor:** Strukturerat om blankstegsmatningen i `send_debug` och bakat in en fast `{BG_TEXT_BOX}` (kritvit bakgrund) för att förhindra mIRC från att rita fula svarta cache-boxar runt texten.
- **📋 Garanterad text-formatering:** Justerat `announce.py` med en lokal strängkontroll (`str()`) för att förhindra att heltal (`integers`) från databasen misstolkas som råa mIRC-färgnummer på skärmen.
- **🧩 Namnkonflikts-säkring:** Ändrat `isinstance(stats, list)` till en ren `type()`-kontroll för att helt eliminera den dolda namnkonflikten med fildelningsmodulen `list.py`.
