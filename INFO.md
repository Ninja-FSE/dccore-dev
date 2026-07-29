# FLAC-Serv Versionsuppdateringar & Projektlogg 📝

Här loggas alla versionsförändringar, optimeringar och buggfixar som gjorts under tidens gång i DCCore-projektet.

---

## 🟥 v1.3.0-BETA (2026-07-28) - "The Debug & Theme Sync Update"
### 🚀 Nya funktioner
- **🛠️ VIP Debug-kanal:** Byggt en helautomatisk nätverkssluss som skickar tidstämplade och färgkodade CLI-loggar direkt live in i mIRC.
- **🏎️ Express-logg via VIP:** Kopplat om funktionen `send_debug` till att använda `is_vip=True` så att systemloggarna skjuts ut på 0ms utan att störa den vanliga kön.
- **🏷️ Kategori-taggar för Debug:** Skapat dynamiska och färgkodade etiketter till vänster i loggen: `[SENT]` (Grön), `[PART]` (Röd), `[QUIT]` (Lila) och `[JOIN]` (Turkos) inramade av solida färgstolpar.

### 🐛 Buggfixar & Optimeringar
- **📦 Eliminering av svarta rutor:** Strukturerat om blankstegsmatningen i `send_debug` och bakat in en fast `{BG_TEXT_BOX}` (kritvit bakgrund) för att förhindra mIRC från att rita fula svarta cache-boxar runt texten.
- **📋 Garanterad text-formatering:** Justerat `announce.py` med en lokal strängkontroll (`str()`) för att förhindra att heltal (`integers`) från databasen misstolkas som råa mIRC-färgnummer på skärmen.
- **🧩 Namnkonflikts-säkring:** Ändrat `isinstance(stats, list)` till en ren `type()`-kontroll för att helt eliminera den dolda namnkonflikten med fildelningsmodulen `list.py`.

---

## 🟨 v1.2.0-BETA (2026-07-27) - "The Database & Index Sync"
### 🚀 Nya funktioner
- **📉 Live 7-Kolonnsstatistik:** Integrerat en automatisk uppräkning av totalt skickade filer, totalt skickade bytes samt dagens och gårdagens mätare vid varje slutförd filöverföring.
- **💾 Hård Disk-flush (`fsync`):** Uppgraderat `db.save_advanced_stats` med `f.flush()` och `os.fsync()` för att tvinga Linux/Proxmox att skriva ändringarna direkt på disken i stället för att ligga kvar i operativsystemets buffert.

### 🐛 Buggfixar & Optimeringar
- **🔢 Index-synkronisering:** Korrigerat databasindexen (`stats[2]` för gårdagen och `stats[4]` för idag) inuti `announce.py` så att de matchar 7-kolonnsformatet från `stats.txt` i stället för att läsa av listdatumet och krascha.
- **🧮 Datumsäkrad matematik:** Fixat ett kritiskt `ValueError` i `dcc.py` genom att isolera listdatumet (index 6) som en rå sträng, vilket hindrar matteloopen från att försöka göra om bindestreck till heltal.

---

## 🟩 v1.1.0-BETA (2026-07-26) - "The VIP Express & Architecture Update"
### 🚀 Nya funktioner
- **🚅 Isolerad VIP-sluss:** Skapat en ny flagga `is_vip=False` i huvudfunktionen `oserve.queue_message`. De nya kommandona skickar nu med `is_vip=True`, vilket gör att de flyger spikrakt förbi det vanliga flood-skyddet.
- **⛓️ Kedjad Kommandotolk:** Byggt om hela kommandotolken i `irc.py` till en stängd `if / elif`-kedja samt ändrat `continue` till `return` i CTCP-filtret, vilket helt eliminerade problemet med dolda dubbelpostningar i kanalerna.

### 🐛 Buggfixar & Optimeringar
- **🧬 Cirkulär import-spärr:** Ersatt den vanliga topp-importen i `commands.py` med en live-avläsning via `sys.modules.get('oserve')` ur RAM-minnet, vilket förhindrar att boten låser sig vid boot.
- **🧼 Cache-rensning:** Rensat ut gamla överflödiga och dubblerade definitioner av `def queue_message` ur `oserve.py` som låg och skrev över den nya källkoden vid uppstart.
