# FLAC-Serv Versionsuppdateringar & Projektlogg 📝

Här loggas alla versionsförändringar, optimeringar och buggfixar som gjorts under tidens gång i DCCore-projektet.

---

## 🟦 v1.4.5-BETA (2026-07-31) - "The Multi-Character Regex Sanitizer Update"
### 🚀 Nya funktioner
- **🧹 Universell Sök-Sanering (`@find`):** Integrerat en kraftfull regex-tvätt via `re.sub(r'[-*_.]', ' ', search_term)` inuti sökfunktionen i `list.py`. Boten översätter nu omedelbart alla mIRC-stjärnor (`*`), understreck (`_`), punkter (`.`) och bindestreck (`-`) till rena mellanslag innan sökorden splittas. Detta eliminerar problemet med missade träffar när användare söker med råa specialtecken (t.ex. `metallica*red*alert`).

---
## 🟦 v1.4.4-BETA (2026-07-30) - "The External Indexer & Micro-Read Update"
### 🚀 Nya funktioner
- **🎛️ Helautomatisk Listuppdatering (`!update`):** Byggt ett avancerat administrationsverktyg i `commands.py` som exekverar ditt externa skript `update_list.py` i en stängd bakgrundstråd via `subprocess.run`. Detta gör att du kan indexera om hela din NFS-musikdisk live direkt inifrån mIRC utan Proxmox CLI-access.
- **⚡ Blixtsnabb Mikro-Read Optimering:** Skapat en högpresterande beräkningsmotor (`get_count_from_list`) som enbart läser den absolut första raden ur din gigantiska masterlista (`f.readline()`). Den använder ett strikt regex-mönster (`List of X Files`) för att suga ut det sanna filantalet på 0ms helt utan att belasta disk-I/O eller CPU.
- **🧮 Matematisk Realtids-skanning:** Boten sparar och jämför nu dina exakta filsiffror live före och efter skript-exekveringen, vilket gör att den stolt kan annonsera exakt hur många nya flac-låtar som har lagts till sedan din förra sökning.

### 🐛 Buggfixar & Optimeringar
- **🧟 Eliminering av Zombie-processer:** Genom att migrera från asynkron `Popen` till synkroniserad tråd-hantering via `subprocess.run` garanteras det nu att Linux-kärnan städar bort barn-processen omedelbart vid slutförd skanning, vilket lämnar din `ps aux`-lista 100 % ren från fula `defunct`-rader.
- **🧬 Cirkulär Namnkonflikts-säkring:** Isolerat system-importen av `list` lokalt inuti funktionen via Pythons levande modulstruktur (`sys.modules.get('list')`), vilket helt eliminerade en tyst krasch orsakad av att Python blandade ihop din fil `list.py` med det inbyggda array-objektet `list`.

---

## 🟦 v1.4.3-BETA (2026-07-30) - "The Clean Config & Security Sync Update"
### 🚀 Nya funktioner
- **🧼 100% Import-Fri `config.py`:** Sanerat och städat ur hela din centrala konfigurationsfil från all funktionell källkod, dolda `import os`-satser samt dynamiska `BASE_DIR`-beräkningar. Alla sökvägar (till `stats.txt`, `bans.txt` och `hard_bans.txt`) är nu helt normaliserade, rena och kraschsäkra textsträngar.
- **🛡️ Live Anti-Flood & Mute-Spårning:** Integrerat din asynkrona VIP-motor `announce.send_debug` djupt inuti flood-skyddet `is_flooding` i `security.py`. Boten strömmar nu färgkodade, lila **`[TEMPBAN]`**-notiser live till mIRC på 0ms så fort en användare rör sig för snabbt, rensar deras fildelningskö och loggar om de uppgraderas till en hård dags-ban fram till midnatt.
- **🚨 Centraliserat Säkerhetsgränssnitt:** Uppgraderat användarkontrollen `check_user_status` i `security.py` med din blixtsnabba socket-send. Systemet dundrar nu upp mörkröda **`[HARDBAN]`**-notiser i din dolda `#flac-debug`-kanal i samma mikrosekund som en spambot som matchar dina permanenta wildcards försöker hamra på sökkommandona.

### 🐛 Buggfixar & Optimeringar
- **⛓️ Trådsäker Fil-Normalisering:** Justerat filhanteringen inuti administratörsverktygen i `commands.py` (`!ban` och `!unban`) till att läsa direkt från din rena config-sträng, vilket eliminerade ett dolt `NameError` vid boot och ser till att trådarna alltid hittar spikrakt in i din undermapp `data/`.

---

## 🟦 v1.4.2-BETA (2026-07-30) - "The Hard Ban & Admin Category Update"
### 🚀 Nya funktioner
- **🛡️ Permanent Wildcard-skydd (`hard_bans.txt`):** Skapat ett isolerat säkerhetslager för fasta spambot-mönster (t.ex. `lidx_*`) djupt inbäddat i undermappen `data/`. Denna fil är helt fredad från systemets helautomatiska midnatts-rensning av vanliga flood-spärrar.
- **🛠️ Live Admin-kommandon (`!ban` / `!unban`):** Monterat två nya administrationsverktyg i `commands.py` som låter dig skriva till och städa i din permanenta ban-fil direkt inifrån mIRC via din asynkrona socket-send, helt utan behov av CLI-access eller manuell `!rehash`.
- **🎨 Dedikerade Säkerhets-färgblock:** Utökat färgblocks-motorn i `announce.py` med två helt egna, solida mIRC-etiketter: Mörkröd **`[HARDBAN]`** för permanenta wildcards och Lila **`[TEMPBAN]`** för rörliga dags-bans, vilket skapar total linjär struktur på den kritvita debug-linjen.
- **🧭 Absolut Trådsynk & Sökvägslås:** Integrerat `os.path.normpath` och absoluta sökvägar baserat på botens hjärta (`BASE_DIR`) för att garantera att de trådade fil-kommandona alltid hittar djupt in i din `data/`-mapp, samt stensäkrat lowercase-formatering (`.lower()`) genom hela kedjan för att stänga alla case-sensitive kryphål för spambottar.

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

---

## 🟨 v1.2.0-BETA (2026-07-27) - "The Database & Index Sync"
### 🚀 Nya funktioner
- **📉 Live 7-Kolonnsstatistik:** Integrerat en automatisk uppräkning av totalt skickade filer, totalt skickade bytes samt dagens och gårdagens mätare vid varje slutförd filöverföring.
- **💾 Hård Disk-flush (`fsync`):** Uppgraderat `db.save_advanced_stats` med `f.flush()` och `os.fsync()` för att tvinga Linux/Proxmox att skriva ändringarna direkt på disken i stället för att ligga kvar i operativsystemets buffert.

### 🐛 Buggfixar & Optimeringar
- **🔢 Index-synkronisering:** Korrigerat databasindexen (`stats` för gårdagen och `stats` för idag) inuti `announce.py` så att de matchar 7-kolonnsformatet från `stats.txt` i stället för att läsa av listdatumet och krascha.
- **🧮 Datumsäkrad matematik:** Fixat ett kritiskt `ValueError` i `dcc.py` genom att isolera listdatumet (index 6) som en rå sträng, vilket hindrar matteloopen från att försöka göra om bindestreck till heltal.

---

## 🟩 v1.1.0-BETA (2026-07-26) - "The VIP Express & Architecture Update"
### 🚀 Nya funktioner
- **🚅 Isolerad VIP-sluss:** Skapat en ny flagga `is_vip=False` i huvudfunktionen `oserve.queue_message`. De nya kommandona skickar nu med `is_vip=True`, vilket gör att de flyger spikrakt förbi det vanliga flood-skyddet.
- **⛓️ Kedjad Kommandotolk:** Byggt om hela kommandotolken i `irc.py` till en stängd `if / elif`-kedja samt ändrat `continue` till `return` i CTCP-filtret, vilket helt eliminerade problemet med dolda dubbelpostningar i kanalerna.

### 🐛 Buggfixar & Optimeringar
- **🧬 Cirkulär import-spärr:** Ersatt den vanliga topp-importen i `commands.py` med en live-avläsning via `sys.modules.get('oserve')` ur RAM-minnet, vilket förhindrar att boten låser sig vid boot.
- **🧼 Cache-rensning:** Rensat ut gamla överflödiga och dubblerade definitioner av `def queue_message` ur `oserve.py` som låg och skrev över den nya källkoden vid uppstart.
