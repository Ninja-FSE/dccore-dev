/* DCCore Dashboard - vanilla JS, no build step, no framework.
 *
 * Talks to the three original read-only endpoints (/api/search, /api/queue,
 * /api/filelists) plus the cross-bot search/fetch endpoints added alongside
 * them: POST /api/search/broadcast, GET /api/search/broadcast/status,
 * POST /api/fetch/enqueue, GET /api/fetch/status, GET /api/fetch/<id>/download,
 * POST /api/filelists/fetch, GET /api/filelists/bots, GET /api/filelists/bot/<nick>.
 * See webserver.py's module docstring - EVERY route requires a login,
 * including static assets, shared with the DCC CHAT admin console's
 * password. This file only ever runs after that login has already
 * succeeded (index.html itself is behind require_login()).
 */
(function () {
  "use strict";

  var REFRESH_MS = 8000;
  var BROADCAST_POLL_MS = 2000;
  var UPDATE_LIST_POLL_MS = 3000;
  var DOWNLOADS_POLL_MS = 4000;
  var FILELISTS_BOTS_POLL_MS = 4000;
  var CONSOLE_LOG_POLL_MS = 2000;
  // Held so the poll can stop itself when the Console turns out to be off -
  // see disableConsoleUi().
  var consoleLogTimer = null;
  // Matches webserver.py's FILELISTS_DEFAULT_PAGE_SIZE - keep the two in
  // sync if either changes, so a page here always lines up with a page the
  // server actually hands back.
  var FILELISTS_PAGE_SIZE = 200;
  // Short, because the query behind it is measured in single-digit
  // milliseconds - the index exists so this can be a filter rather than a
  // search button. It is not zero: a keystroke still costs a round trip, and
  // a fast typist should not queue one per character.
  var FILELISTS_FILTER_DEBOUNCE_MS = 120;

  var views = {
    search:    { title: "Search",     sub: "Find a file across the current master list." },
    download:  { title: "Downloads",  sub: "What you have asked other bots for, and how it is going." },
    filelists: { title: "List Browser", sub: "Every file this bot - or a fetched bot's list - is currently offering." },
    tools:     { title: "Tools",      sub: "Checks you run on demand against the current master list." },
    settings:  { title: "Settings",   sub: "Every editable setting, grouped. Saving writes settings.conf and starts a rehash." },
    stats:     { title: "Stats",      sub: "Everything this bot knows about itself, including who is waiting." },
    console:   { title: "Console",    sub: "The DCC CHAT admin console's commands and live log, in the browser." }
  };

  var state = {
    // What the previewed OmenServe import would write, held between the
    // preview and the confirm so the button sends exactly what was shown -
    // not a second parse that could have moved on from it.
    importValues: null,
    // Served folders (#164 step 4). `foldersDraft` is what the rows are
    // showing and `folders` is what the server last confirmed - kept apart
    // so switching category and back does not silently discard an edit,
    // and so a failed save leaves the operator's rows exactly as typed.
    // The last checkbox the operator touched, as the anchor for a
    // shift-click range. A DOM node, so it is cleared whenever the
    // table is rebuilt.
    filelistsLastChecked: null,
    // One row per held list, keyed by bot, as /api/filelists/bots last
    // reported it - so the staleness banner can be rendered for whichever
    // source is selected without asking again.
    filelistsBots: {},
    folders: null, foldersSource: "", foldersDraft: null, foldersNote: null,
    downloads: [],
    lists: null, listsSource: "", listsDraft: null, listsNote: null,
    onConnect: null, onConnectNote: null,
    // The folder picker (#164 step 5). `browse` is null when the panel is
    // closed; open, it carries the row it will write back into.
    foldersBrowserEnabled: false, browse: null,
    active: "search", filelistsLoaded: false, filelistsSource: "__own__",
    // The live filter term, and the token that decides whether a reply is
    // still wanted. Typing fast enough puts several requests in flight, and
    // the slowest is not necessarily the oldest.
    filelistsFilter: "", filelistsFilterToken: 0,
    // Which LOAD is allowed to render. Separate from filelistsFilterToken,
    // which decides which debounced keystroke gets to send a request at all -
    // two different questions, and conflating them is what let a stale reply
    // through.
    filelistsLoadToken: 0,
    // Which bots the operator has switched OFF while filtering, and the last
    // answer the server gave. Toggling re-renders from that answer rather
    // than asking again: the rows are already here, and a round trip per
    // click would be slower than the search that produced them.
    filelistsExcluded: {}, filelistsFilterPayload: null,
    filelistsOffset: 0, filelistsTotal: 0, filelistsReturned: 0,
    filelistsHistory: [],
    settingsLoaded: false, settingsCategories: [], settingsActiveCategory: null,
    settingsBaseline: {}, settingsDirty: {}, settingsAdminPasswordSet: false,
    consoleCursor: 0
  };

  var el = {
    navItems:     document.querySelectorAll(".nav-item"),
    pageTitle:    document.getElementById("page-title"),
    pageSub:      document.getElementById("page-sub"),
    searchForm:   document.getElementById("search-form"),
    searchInput:  document.getElementById("search-input"),
    searchBody:   document.getElementById("search-body"),
    queueBody:    document.getElementById("queue-body"),
    filelistsBody:document.getElementById("filelists-body"),
    filelistsFilterInput: document.getElementById("filelists-filter-input"),
    filelistsFilterClear: document.getElementById("filelists-filter-clear"),
    filelistsFilterStatus: document.getElementById("filelists-filter-status"),
    filelistsFilterActions: document.getElementById("filelists-filter-actions"),
    filelistsFilterAll: document.getElementById("filelists-filter-all"),
    filelistsFilterNone: document.getElementById("filelists-filter-none"),
    statSlots:    document.getElementById("stat-slots"),
    statFiles:    document.getElementById("stat-files"),
    statUsers:    document.getElementById("stat-users"),
    connDot:      document.getElementById("conn-dot"),
    connText:     document.getElementById("conn-text"),
    statusSlots:  document.getElementById("status-slots"),
    statusQueued: document.getElementById("status-queued"),
    broadcastBtn:        document.getElementById("broadcast-btn"),
    broadcastStatus:     document.getElementById("broadcast-status"),
    broadcastWrap:       document.getElementById("broadcast-wrap"),
    broadcastBody:       document.getElementById("broadcast-body"),
    downloadSelectedBtn: document.getElementById("download-selected-btn"),
    downloadsBody:       document.getElementById("downloads-body"),
    bulkFetchForm:       document.getElementById("bulk-fetch-form"),
    bulkFetchTextarea:   document.getElementById("bulk-fetch-textarea"),
    bulkFetchErrors:     document.getElementById("bulk-fetch-errors"),
    filelistsFetchForm:   document.getElementById("filelists-fetch-form"),
    filelistsFetchInput:  document.getElementById("filelists-fetch-input"),
    filelistsFetchStatus: document.getElementById("filelists-fetch-status"),
    filelistsFreshness: document.getElementById("filelists-freshness"),
    filelistsBotList: document.getElementById("filelists-bot-list"),
    filelistsPrevBtn:     document.getElementById("filelists-prev-btn"),
    filelistsNextBtn:     document.getElementById("filelists-next-btn"),
    filelistsPageInfo:    document.getElementById("filelists-page-info"),
    filelistsExpandAll:   document.getElementById("filelists-expand-all"),
    filelistsCollapseAll: document.getElementById("filelists-collapse-all"),
    filelistsDownloadSelectedBtn: document.getElementById("filelists-download-selected-btn"),
    stSpeed:               document.getElementById("st-speed"),
    stRecord:              document.getElementById("st-record"),
    stSending:             document.getElementById("st-sending"),
    stQueued:              document.getElementById("st-queued"),
    stQueuedLabel:         document.getElementById("st-queued-label"),
    stUptime:              document.getElementById("st-uptime"),
    stSentTotal:           document.getElementById("st-sent-total"),
    stSentToday:           document.getElementById("st-sent-today"),
    stSentYesterday:       document.getElementById("st-sent-yesterday"),
    stSentTotalFiles:      document.getElementById("st-sent-total-files"),
    stSentTodayFiles:      document.getElementById("st-sent-today-files"),
    stSentYesterdayFiles:  document.getElementById("st-sent-yesterday-files"),
    stFiles:               document.getElementById("st-files"),
    stSize:                document.getElementById("st-size"),
    stAlbums:              document.getElementById("st-albums"),
    stBuilt:               document.getElementById("st-built"),
    stFoot:                document.getElementById("st-foot"),
    stTopFiles:            document.getElementById("st-top-files"),
    importFile:            document.getElementById("import-file"),
    importPasteToggle:     document.getElementById("import-paste-toggle"),
    importPaste:           document.getElementById("import-paste"),
    importPasteActions:    document.getElementById("import-paste-actions"),
    importPasteRead:       document.getElementById("import-paste-read"),
    importStatus:          document.getElementById("import-status"),
    importPreviewWrap:     document.getElementById("import-preview-wrap"),
    importPreview:         document.getElementById("import-preview"),
    importWarning:         document.getElementById("import-warning"),
    importConfirm:         document.getElementById("import-confirm"),
    importApply:           document.getElementById("import-apply"),
    importCancel:          document.getElementById("import-cancel"),
    stTopAlbums:           document.getElementById("st-top-albums"),
    stTopAlbumsWrap:       document.getElementById("st-top-albums-wrap"),
    stTopAlbumsLabel:      document.getElementById("st-top-albums-label"),
    stTopAlbumsOff:        document.getElementById("st-top-albums-off"),
    themeDark:    document.getElementById("theme-dark"),
    themeLight:   document.getElementById("theme-light"),
    updateListRunBtn:     document.getElementById("update-list-run-btn"),
    updateListStatus:     document.getElementById("update-list-status"),
    verifyRunBtn:         document.getElementById("verify-run-btn"),
    verifyStatus:         document.getElementById("verify-status"),
    verifyResults:        document.getElementById("verify-results"),
    settingsRail:         document.getElementById("settings-rail"),
    settingsFields:       document.getElementById("settings-fields"),
    settingsSaveBtn:      document.getElementById("settings-save-btn"),
    settingsSavebarText:  document.getElementById("settings-savebar-text"),
    settingsRestartNote:  document.getElementById("settings-restart-note"),
    settingsSaveStatus:   document.getElementById("settings-save-error"),
    consoleLog:     document.getElementById("console-log"),
    consoleForm:    document.getElementById("console-form"),
    consoleInput:   document.getElementById("console-input"),
    consoleRunBtn:  document.getElementById("console-run-btn")
  };

  function escapeHtml(value) {
    var div = document.createElement("div");
    div.textContent = value === undefined || value === null ? "" : String(value);
    return div.innerHTML;
  }

  function emptyRow(colspan, text) {
    return '<tr class="empty-row"><td colspan="' + colspan + '">' + escapeHtml(text) + "</td></tr>";
  }

  function fetchJson(url) {
    return fetch(url, { headers: { Accept: "application/json" } }).then(function (res) {
      // A 401 is not a failed request, it is an expired session, and every
      // caller here turned it into "HTTP 401" and rendered the daemon as
      // unreachable. app.secret_key is os.urandom(32) per process, so EVERY
      // restart invalidates every cookie - a perfectly healthy bot then reads
      // as down, for ever, until somebody thinks to reload the page.
      //
      // The returned promise is deliberately left pending: navigation is
      // already under way, and resolving or rejecting it would flash an error
      // into a page that is about to be replaced.
      if (res.status === 401) {
        window.location.href = "/login";
        return new Promise(function () {});
      }
      if (!res.ok) { throw new Error("HTTP " + res.status); }
      return res.json();
    });
  }

  // postJson deliberately does NOT throw on a non-2xx response - the mutating
  // routes (broadcast/enqueue) return a meaningful JSON error body (409
  // already-in-progress, 429 cooldown, 503 IRC down, 400 bad input) that the
  // caller wants to show the operator, not treat as a network failure.
  // Like fetchJson, but a non-2xx is DATA rather than a throw - the same
  // posture postJson already takes below, and for the same reason: these
  // routes answer a bad request with a JSON body that says what was wrong,
  // and fetchJson's `throw new Error("HTTP " + res.status)` discards it. The
  // folder browser's "... is not a folder on this machine" reached the
  // operator as "HTTP 400" until this existed.
  function fetchJsonAllowingError(url) {
    return fetch(url, { headers: { Accept: "application/json" } })
      .then(function (res) {
        if (res.status === 401) {
          window.location.href = "/login";
          return new Promise(function () {});
        }
        return res.json().then(function (data) {
          return { ok: res.ok, status: res.status, data: data };
        });
      });
  }

  function postJson(url, body) {
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(body)
    }).then(function (res) {
      return res.json().then(function (data) {
        return { ok: res.ok, status: res.status, data: data };
      });
    });
  }

  // -------------------------------------------------------------- Routing

  function activateView(name) {
    state.active = name;
    // Guarded, because one missing section must not take the whole navigation
    // with it. Unguarded, a null here threw before the per-view loaders at the
    // bottom of this function ran, so every OTHER view silently stopped
    // working too - one absent element disabling the entire dashboard.
    Object.keys(views).forEach(function (key) {
      var section = document.getElementById("view-" + key);
      if (section) { section.classList.toggle("is-active", key === name); }
    });
    el.navItems.forEach(function (btn) {
      btn.classList.toggle("is-active", btn.dataset.view === name);
    });
    el.pageTitle.textContent = views[name].title;
    el.pageSub.textContent = views[name].sub;

    if (name === "download") { loadDownloads(); }
    if (name === "filelists") {
      pollFilelistsBots();
      if (!state.filelistsLoaded) { loadFilelists(); }
    }
    if (name === "settings" && !state.settingsLoaded) { loadSettings(); }
    if (name === "stats") { loadStats(); }
  }

  el.navItems.forEach(function (btn) {
    btn.addEventListener("click", function () { activateView(btn.dataset.view); });
  });

  // --------------------------------------------------------------- Search

  el.searchForm.addEventListener("submit", function (evt) {
    evt.preventDefault();
    runSearch();
  });

  function runSearch() {
    var query = el.searchInput.value.trim();
    if (!query) {
      el.searchBody.innerHTML = emptyRow(4, "Type something and press Search.");
      return;
    }
    el.searchBody.innerHTML = emptyRow(4, "Searching…");
    fetchJson("/api/search?q=" + encodeURIComponent(query))
      .then(function (rows) {
        markConnection(true);
        if (!rows.length) {
          el.searchBody.innerHTML = emptyRow(4, "No matches for “" + query + "”.");
          return;
        }
        el.searchBody.innerHTML = rows.map(function (row) {
          return "<tr>" +
            "<td class=\"col-mono\">" + escapeHtml(row.title) + "</td>" +
            "<td class=\"col-dim col-mono\">" + escapeHtml(row.path) + "</td>" +
            "<td class=\"col-mono\">" + escapeHtml(row.size) + "</td>" +
            "<td class=\"col-dim\">" + escapeHtml(row.channel) + "</td>" +
            "</tr>";
        }).join("");
      })
      .catch(function (err) {
        markConnection(false);
        el.searchBody.innerHTML = emptyRow(4, "Search failed: " + err.message);
      });
  }

  // ------------------------------------------------------- Broadcast search

  var broadcast = { pollTimer: null, deadline: 0 };

  el.broadcastBtn.addEventListener("click", function () {
    var term = el.searchInput.value.trim();
    if (term.length < 3) {
      showBroadcastStatus("Type at least 3 characters first.", true);
      return;
    }
    el.broadcastBtn.disabled = true;
    postJson("/api/search/broadcast", { term: term }).then(function (res) {
      el.broadcastBtn.disabled = false;
      if (!res.ok) {
        showBroadcastStatus(res.data.error || ("HTTP " + res.status), true);
        return;
      }
      broadcast.deadline = res.data.deadline * 1000;
      el.broadcastWrap.style.display = "";
      el.broadcastBody.innerHTML = emptyRow(3, "Listening…");
      startBroadcastPolling();
    }).catch(function (err) {
      el.broadcastBtn.disabled = false;
      showBroadcastStatus("Request failed: " + err.message, true);
    });
  });

  function showBroadcastStatus(text, isError) {
    el.broadcastStatus.textContent = text;
    el.broadcastStatus.classList.toggle("is-error", !!isError);
  }

  function startBroadcastPolling() {
    if (broadcast.pollTimer) { clearInterval(broadcast.pollTimer); }
    pollBroadcastStatus();
    broadcast.pollTimer = setInterval(pollBroadcastStatus, BROADCAST_POLL_MS);
  }

  function pollBroadcastStatus() {
    fetchJson("/api/search/broadcast/status").then(function (payload) {
      markConnection(true);
      renderBroadcastResults(payload.results);
      if (payload.listening) {
        var remaining = Math.max(0, Math.ceil((payload.deadline * 1000 - Date.now()) / 1000));
        showBroadcastStatus("Listening… " + remaining + "s remaining, " +
          payload.results.length + " repl" + (payload.results.length === 1 ? "y" : "ies") + " so far.");
      } else {
        showBroadcastStatus(payload.results.length
          ? "Done. " + payload.results.length + " repl" + (payload.results.length === 1 ? "y" : "ies") + " received."
          : "Done. No replies received.");
        if (broadcast.pollTimer) {
          clearInterval(broadcast.pollTimer);
          broadcast.pollTimer = null;
        }
      }
    }).catch(function (err) {
      markConnection(false);
      showBroadcastStatus("Could not poll broadcast status: " + err.message, true);
    });
  }

  // Built via DOM APIs, not string-concatenated innerHTML, and this is load
  // bearing rather than a style choice: entry.bot/entry.filename come from
  // irc.py's best-effort "!<bot> <filename>" extraction against arbitrary
  // text ANY IRC user can PM/NOTICE to the bot during an open broadcast
  // window - i.e. attacker-controlled, unauthenticated text. Building the
  // bot/filename data attributes by hand from that text (even through
  // escapeHtml(), which never encodes the double-quote character) lets a
  // filename like: x[quote] autofocus onfocus=[quote]alert(1) - break out of
  // the attribute and inject a live event handler. Assigning through
  // .dataset instead has no such hazard: the browser sets the attribute
  // value directly, with no HTML parsing step for the attacker's text to
  // escape through. entry.from/entry.text are plain text-node content
  // (.textContent), which was never the vulnerable pattern - only
  // attribute-position values are.
  // Replies arrive interleaved - every bot's header, then its matches, in
  // whatever order they answer - which is unreadable once a dozen bots reply.
  // Grouped by sender instead: one heading per bot carrying what it told us
  // about itself, then its matches beneath it as the exact line you would
  // paste into the channel.
  function groupBroadcastResults(results) {
    var order = [];
    var groups = {};
    results.forEach(function (entry) {
      var who = entry.from || "?";
      if (!groups[who]) {
        groups[who] = { from: who, header: null, files: [], other: [] };
        order.push(who);
      }
      var g = groups[who];
      if (entry.header) {
        // Keep the first header. A bot that answers twice in one window is
        // reporting the same slot counts; the later one is not more true.
        g.header = g.header || entry.header;
      } else if (entry.bot && entry.filename) {
        g.files.push(entry);
      } else {
        g.other.push(entry);
      }
    });
    return order.map(function (who) { return groups[who]; })
                .sort(function (a, b) { return b.files.length - a.files.length; });
  }

  // What the bot said about itself, as one line. Every field is optional -
  // a missing one means "it did not say", never zero, so nothing is invented
  // to fill the gap.
  function describeBot(group) {
    var h = group.header || {};
    var bits = [];

    if (group.files.length) {
      // The header's count is what it FOUND; the rows are what it SENT.
      // Beezer finds 12 and sends 5, and saying only "5" would hide that
      // refining the search is worth doing.
      if (typeof h.matches === "number" && h.matches > group.files.length) {
        bits.push(h.matches + " matches, showing " + group.files.length);
      } else {
        bits.push(group.files.length + (group.files.length === 1 ? " match" : " matches"));
      }
    } else if (typeof h.matches === "number") {
      bits.push(h.matches + " matches, none sent");
    }

    if (typeof h.slots_free === "number" && typeof h.slots_total === "number") {
      bits.push(h.slots_free + "/" + h.slots_total + " slots free");
    } else if (typeof h.slots_in_use === "number") {
      // SPQR reports slots IN USE, the opposite sense - label it as such
      // rather than silently showing it where "free" would go.
      bits.push(h.slots_in_use + " slot" + (h.slots_in_use === 1 ? "" : "s") + " in use");
    }

    if (typeof h.queued === "number") {
      bits.push(typeof h.queue_total === "number"
        ? "queue " + h.queued + "/" + h.queue_total
        : h.queued + " queued");
    }

    if (typeof h.list_size === "number") {
      bits.push("list " + h.list_size.toLocaleString() + " files");
    }

    if (h.server) {
      bits.push(h.server);
    } else if (h.family === "spqr") {
      bits.push("SPQR");
    }

    return bits.join("  ·  ");
  }

  function renderBroadcastResults(results) {
    el.broadcastBody.innerHTML = "";
    if (!results.length) {
      el.broadcastBody.innerHTML = emptyRow(3, "No replies yet.");
      updateDownloadSelectedState();
      return;
    }

    groupBroadcastResults(results).forEach(function (group) {
      var headRow = document.createElement("tr");
      headRow.className = "bot-group";

      var headCell = document.createElement("td");
      headCell.colSpan = 3;

      var name = document.createElement("span");
      name.className = "bot-group-name";
      name.textContent = group.from;
      headCell.appendChild(name);

      var summary = describeBot(group);
      if (summary) {
        var meta = document.createElement("span");
        meta.className = "bot-group-meta";
        meta.textContent = summary;
        headCell.appendChild(meta);
      }

      headRow.appendChild(headCell);
      el.broadcastBody.appendChild(headRow);

      group.files.forEach(function (entry) {
        var tr = document.createElement("tr");

        var checkTd = document.createElement("td");
        checkTd.className = "col-check";
        var box = document.createElement("input");
        box.type = "checkbox";
        box.className = "broadcast-check";
        box.dataset.bot = entry.bot;
        box.dataset.filename = entry.filename;
        box.addEventListener("change", updateDownloadSelectedState);
        checkTd.appendChild(box);
        tr.appendChild(checkTd);

        // The exact line you would paste into the channel yourself, which is
        // also what the checkbox fetches - so what you read is what happens.
        var cmdTd = document.createElement("td");
        cmdTd.className = "col-mono";
        cmdTd.colSpan = 2;
        cmdTd.textContent = "!" + entry.bot + " " + entry.filename;
        tr.appendChild(cmdTd);

        el.broadcastBody.appendChild(tr);
      });

      // Anything that was neither a header nor a match still gets shown -
      // a bot saying it found nothing, or unrelated chatter that landed in
      // the window. Dimmed, and never with a checkbox, but never hidden:
      // silently dropping a reply would be worse than showing a stray line.
      group.other.forEach(function (entry) {
        var tr = document.createElement("tr");
        tr.appendChild(document.createElement("td"));
        var td = document.createElement("td");
        td.className = "col-dim col-mono";
        td.colSpan = 2;
        td.textContent = entry.text;
        tr.appendChild(td);
        el.broadcastBody.appendChild(tr);
      });
    });

    updateDownloadSelectedState();
  }

  function updateDownloadSelectedState() {
    var checked = el.broadcastBody.querySelectorAll(".broadcast-check:checked");
    el.downloadSelectedBtn.disabled = checked.length === 0;
  }

  el.downloadSelectedBtn.addEventListener("click", function () {
    var checked = el.broadcastBody.querySelectorAll(".broadcast-check:checked");
    var items = Array.prototype.map.call(checked, function (box) {
      return { bot: box.dataset.bot, filename: box.dataset.filename };
    });
    if (!items.length) { return; }
    el.downloadSelectedBtn.disabled = true;
    postJson("/api/fetch/enqueue", items).then(function (res) {
      if (!res.ok && !(res.data && res.data.created && res.data.created.length)) {
        showBroadcastStatus("Could not queue the download: " +
          (res.data.error || (res.data.errors && res.data.errors[0] && res.data.errors[0].error) || ("HTTP " + res.status)), true);
      } else {
        showBroadcastStatus("Queued " + res.data.created.length + " file(s) for fetch - see Queue → Downloads.");
        Array.prototype.forEach.call(checked, function (box) { box.checked = false; });
      }
      updateDownloadSelectedState();
      loadDownloads();
    }).catch(function (err) {
      showBroadcastStatus("Request failed: " + err.message, true);
      el.downloadSelectedBtn.disabled = false;
    });
  });

  // -------------------------------------------------------- Bulk fetch (Download tab)

  // Same tolerant pattern already used to build the Search view's broadcast
  // "Download" buttons under the hood (irc.py's own cross-bot capture uses
  // the equivalent on the IRC side) - reused here rather than reinvented, per
  // the brief: "!(\S+)\s+(.+)$".
  var BULK_FETCH_LINE_RE = /^!(\S+)\s+(.+)$/;

  el.bulkFetchForm.addEventListener("submit", function (evt) {
    evt.preventDefault();
    submitBulkFetch();
  });

  function submitBulkFetch() {
    var lines = el.bulkFetchTextarea.value.split(/\r?\n/);
    var items = [];
    var messages = [];

    lines.forEach(function (rawLine, idx) {
      var line = rawLine.trim();
      if (!line) { return; }
      var match = BULK_FETCH_LINE_RE.exec(line);
      if (!match) {
        messages.push({
          text: "Line " + (idx + 1) + ": could not parse “" + line +
                "” - expected \"!<bot> <filename>\".",
          isError: true
        });
        return;
      }
      items.push({ bot: match[1], filename: match[2] });
    });

    if (!items.length) {
      if (!messages.length) {
        messages.push({ text: "Nothing to queue - paste at least one \"!<bot> <filename>\" line.", isError: true });
      }
      renderBulkFetchMessages(messages);
      return;
    }

    postJson("/api/fetch/enqueue", items).then(function (res) {
      var createdCount = (res.data.created || []).length;
      (res.data.errors || []).forEach(function (err) {
        var item = err.item || {};
        messages.push({
          text: "\"!" + (item.bot != null ? item.bot : "?") + " " +
                (item.filename != null ? item.filename : "?") + "\": " + err.error,
          isError: true
        });
      });
      if (createdCount) {
        messages.unshift({ text: "Queued " + createdCount + " request(s) - see the Downloads table below.", isError: false });
        el.bulkFetchTextarea.value = "";
        loadDownloads();
      } else if (!messages.length) {
        messages.push({ text: res.data.error || "Nothing was queued.", isError: true });
      }
      renderBulkFetchMessages(messages);
    }).catch(function (err) {
      messages.push({ text: "Request failed: " + err.message, isError: true });
      renderBulkFetchMessages(messages);
    });
  }

  // Built via textContent, one <li> per message - never string-concatenated
  // innerHTML - even though this box is fed by the operator's own paste
  // rather than remote IRC text, the same "untrusted text never goes through
  // an HTML-parsing step" discipline applies everywhere else in this file.
  function renderBulkFetchMessages(messages) {
    el.bulkFetchErrors.innerHTML = "";
    if (!messages || !messages.length) { return; }
    var ul = document.createElement("ul");
    ul.className = "bulk-fetch-message-list";
    messages.forEach(function (msg) {
      var li = document.createElement("li");
      li.textContent = msg.text;
      li.className = msg.isError ? "is-error" : "is-ok";
      ul.appendChild(li);
    });
    el.bulkFetchErrors.appendChild(ul);
  }

  // -------------------------------------------------------------- Downloads

  // "rejected" is not a state dcc_fetch.py ever writes. A list archive whose
  // bytes arrived intact but which the extraction guard refused keeps
  // state === "complete", because the transfer really did succeed - the
  // reason it was refused is carried separately, in list_processing_error.
  // This is the display-side name for that combination.
  var DOWNLOAD_STATE_LABELS = {
    pending: "Pending", offered: "Offered", listening: "Listening",
    receiving: "Receiving", complete: "Complete", failed: "Failed",
    rejected: "Rejected"
  };

  function loadDownloads() {
    fetchJson("/api/fetch/status").then(function (rows) {
      markConnection(true);
      // Held so a row's own details can be looked up by id. The Redownload
      // button needs the bot and the filename, and neither may go into a data
      // attribute - escapeHtml() is textContent -> innerHTML and leaves a
      // double quote alone, and both of those come off the wire. The id does
      // go in one: it is ours, and it is hex.
      state.downloads = rows || [];
      renderDownloads(rows);
    }).catch(function () { markConnection(false); });
  }

  function redownloadFetchRow(button) {
    var requestId = decodeURIComponent(button.dataset.requestId);
    var row = (state.downloads || []).filter(function (candidate) {
      return String(candidate.id) === requestId;
    })[0];
    if (!row) { return; }

    button.disabled = true;

    // A LIST row and a FILE row are asked for in different ways, because they
    // always were: a list is "@<bot>" and we cannot know what the bot will
    // call its archive, while a file is named outright. Re-asking has to use
    // the same route the original request came through, or the retry would
    // create a row of a different kind from the one it is retrying.
    var again;
    if (row.request_type === "list") {
      again = postJson("/api/filelists/fetch", { bot: row.bot });
    } else {
      // requested_filename, not filename: for a folder row the second is the
      // name the OTHER bot eventually advertised, and for a failed one it may
      // never have been set at all. The first is what we asked for.
      var wanted = row.requested_filename || row.filename;
      if (!wanted) { button.disabled = false; return; }
      again = postJson("/api/fetch/enqueue", [{ bot: row.bot, filename: wanted }]);
    }

    again.then(function (res) {
      if (!res.ok) {
        window.alert("Could not ask again: " +
          ((res.data && res.data.error) || ("HTTP " + res.status)));
        button.disabled = false;
        return;
      }
      // The old row is left alone deliberately. It is the record of what
      // happened, and deleting it as a side effect of retrying would throw
      // away the reason the retry was needed.
      loadDownloads();
    }).catch(function (err) {
      window.alert("Could not ask again: " + err.message);
      button.disabled = false;
    });
  }

  // Delegated: renderDownloads() rebuilds the table's innerHTML on every
  // poll, which would silently drop a listener attached to any one row.
  el.downloadsBody.addEventListener("click", function (evt) {
    var retry = evt.target.closest ? evt.target.closest(".fetch-retry-btn") : null;
    if (retry) {
      redownloadFetchRow(retry);
      return;
    }

    var btn = evt.target.closest ? evt.target.closest(".fetch-delete-btn") : null;
    if (!btn) { return; }
    var requestId = decodeURIComponent(btn.dataset.requestId);
    // A queued row has no file yet and can simply be re-queued, so the
    // finished-row warning would be both wrong and needlessly alarming.
    var prompt = btn.dataset.pending
      ? "Remove this queued request? Nothing has been downloaded yet."
      : "Delete this fetched file? This cannot be undone.";
    if (!window.confirm(prompt)) { return; }
    btn.disabled = true;
    postJson("/api/fetch/" + encodeURIComponent(requestId) + "/delete", {}).then(function (res) {
      if (!res.ok) {
        window.alert("Could not delete: " + (res.data && res.data.error || ("HTTP " + res.status)));
        btn.disabled = false;
        return;
      }
      loadDownloads();
    }).catch(function (err) {
      window.alert("Could not delete: " + err.message);
      btn.disabled = false;
    });
  });

  function renderDownloads(rows) {
    if (!rows.length) {
      el.downloadsBody.innerHTML = emptyRow(5, "Nothing queued yet.");
      return;
    }
    el.downloadsBody.innerHTML = rows.map(function (row) {
      var state = row.state || "pending";
      // dcc_fetch.py records a refused list archive on the row explicitly
      // "for the dashboard", and /api/fetch/status serves it - but nothing
      // here read it, so the field was served and discarded. A zip-slip
      // attempt from a foreign bot rendered as "Complete" with a working
      // Download button, indistinguishable from a list that fetched
      // perfectly, and the only record of the attempt was one stdout line.
      var rejected = !!row.list_processing_error;
      var displayState = rejected ? "rejected" : state;
      var label = DOWNLOAD_STATE_LABELS[displayState] || displayState;
      var progress = row.total_size
        ? Math.round(100 * (row.bytes_received || 0) / row.total_size) + "%"
        : (row.bytes_received ? row.bytes_received + " B" : "—");
      // Order matters: a rejected row is also state === "complete", so it has
      // to be tested first or it gets the Download button anyway.
      // A finished row (complete or failed) is deletable, and so is a pending
      // one: nothing has been dispatched for it yet, so there is nothing to
      // cancel - only a row to forget. The three genuinely in-flight states
      // (offered/listening/receiving) still get no button, because there is no
      // cancellation path for a transfer thread already running.
      // Without this the server-side fix would be invisible: the queue would
      // be clearable by API and not by the dashboard that filled it.
      var deletable = (state === "complete" || state === "failed" || state === "pending");
      // "Cancel" for a row that has not started - calling it Delete would
      // suggest a downloaded file is being thrown away when none exists.
      var deleteBtn = deletable
        ? "<button type=\"button\" class=\"btn btn-small btn-danger fetch-delete-btn\" data-request-id=\"" +
          encodeURIComponent(row.id) + "\" data-pending=\"" + (state === "pending" ? "1" : "") + "\">" +
          (state === "pending" ? "Cancel" : "Delete") + "</button>"
        : "";
      // ASK AGAIN, for a row that did not arrive. Neo's: a failed or rejected
      // fetch is the one an operator most wants to retry, and the only way to
      // do it was to go back to the List Browser and retype the nick.
      //
      // Not offered on a row that succeeded: there is a Download button there,
      // and re-fetching a list already held is what the Refresh in the List
      // Browser is for.
      //
      // NOTHING IN AN ATTRIBUTE that a nick or a filename could break out of.
      // escapeHtml() is textContent -> innerHTML and leaves a double quote
      // alone, and both of these come off the wire - so the row's id goes in
      // (it is ours, and hex) and the handler looks the rest up from state.
      var retryBtn = (rejected || state === "failed")
        ? "<button type=\"button\" class=\"btn btn-small fetch-retry-btn\" data-request-id=\"" +
          encodeURIComponent(row.id) + "\">Redownload</button> "
        : "";
      var action;
      if (rejected) {
        action = "<span class=\"col-dim\">" + escapeHtml(row.list_processing_error) + "</span> " + retryBtn + deleteBtn;
      } else if (state === "complete") {
        action = "<a class=\"btn btn-small\" href=\"/api/fetch/" + encodeURIComponent(row.id) + "/download\">Download</a> " + deleteBtn;
      } else if (state === "failed") {
        action = "<span class=\"col-dim\">" + escapeHtml(row.reason || "") + "</span> " + retryBtn + deleteBtn;
      } else if (state === "pending") {
        action = deleteBtn;
      } else {
        action = "";
      }
      // A "list" row (see dcc_fetch.py's request_type) starts with no
      // filename at all - we sent a bare "@<bot>" and cannot know what the
      // target bot will name its list zip until it actually answers. Show
      // something sensible instead of a blank cell until it does.
      var filenameDisplay = row.filename;
      if (!filenameDisplay && row.request_type === "list") {
        filenameDisplay = row.bot + "’s file list";
      } else if (row.request_type === "folder" && row.filename &&
                 row.filename === row.requested_filename) {
        // Not yet claimed: dcc_fetch.py's "folder" rows start with
        // filename === the literal "!rar <folder>" request text
        // (requested_filename, set once at creation) and only get the real
        // advertised .rar name once the other bot answers - show something
        // more readable than the raw wire command until then.
        filenameDisplay = row.filename.replace(/^!rar\s+/, "") + " (packing…)";
      }
      return "<tr>" +
        "<td class=\"col-mono\">" + escapeHtml(row.bot) + "</td>" +
        "<td class=\"col-dim col-mono\">" + escapeHtml(filenameDisplay) + "</td>" +
        "<td><span class=\"status-pill status-" + escapeHtml(displayState) + "\">" + escapeHtml(label) + "</span></td>" +
        "<td class=\"col-mono\">" + escapeHtml(progress) + "</td>" +
        "<td>" + action + "</td>" +
        "</tr>";
    }).join("");
  }

  // ---------------------------------------------------------------- Queue

  var STATUS_LABELS = { sending: "Sending", frozen: "Frozen", queued: "Queued", empty: "Empty" };

  function loadQueue() {
    fetchJson("/api/queue")
      .then(function (rows) {
        markConnection(true);
        renderQueueStats(rows);
        renderQueueTable(rows);
        renderSidebarStatus(rows);
      })
      .catch(function (err) {
        markConnection(false);
        el.queueBody.innerHTML = emptyRow(4, "Could not load the queue: " + err.message);
      });
  }

  function renderQueueTable(rows) {
    if (!rows.length) {
      el.queueBody.innerHTML = emptyRow(4, "The queue is empty.");
      return;
    }
    el.queueBody.innerHTML = rows.map(function (row) {
      var status = row.status || "queued";
      var label = STATUS_LABELS[status] || status;
      return "<tr>" +
        "<td class=\"col-mono\">" + escapeHtml(row.user) + "</td>" +
        "<td class=\"col-dim col-mono\">" + escapeHtml(row.preview) + "</td>" +
        "<td class=\"col-mono\">" + escapeHtml(row.count) + "</td>" +
        "<td><span class=\"status-pill status-" + escapeHtml(status) + "\">" + escapeHtml(label) + "</span></td>" +
        "</tr>";
    }).join("");
  }

  function renderQueueStats(rows) {
    var sending = rows.filter(function (r) { return r.status === "sending"; }).length;
    var totalFiles = rows.reduce(function (sum, r) { return sum + (r.count || 0); }, 0);
    el.statSlots.textContent = sending;
    el.statFiles.textContent = totalFiles;
    el.statUsers.textContent = rows.length;
  }

  function renderSidebarStatus(rows) {
    var sending = rows.filter(function (r) { return r.status === "sending"; }).length;
    var totalFiles = rows.reduce(function (sum, r) { return sum + (r.count || 0); }, 0);
    el.statusSlots.textContent = sending;
    el.statusQueued.textContent = totalFiles;
  }

  function markConnection(ok) {
    el.connDot.classList.toggle("is-live", ok);
    el.connDot.classList.toggle("is-down", !ok);
    el.connText.textContent = ok ? "live" : "unreachable";
  }

  // ------------------------------------------------------------ File Lists

  // Fetching another bot's list ------------------------------------------

  el.filelistsFetchForm.addEventListener("submit", function (evt) {
    evt.preventDefault();
    var bot = el.filelistsFetchInput.value.trim();
    if (!bot) { return; }
    postJson("/api/filelists/fetch", { bot: bot }).then(function (res) {
      if (!res.ok) {
        showFilelistsFetchStatus(res.data.error || ("HTTP " + res.status), true);
        return;
      }
      showFilelistsFetchStatus(
        "Requested " + bot + "’s list - track its progress on the Download tab; " +
        "it will appear in the switcher below once fetched.");
      el.filelistsFetchInput.value = "";
      pollFilelistsBots();
    }).catch(function (err) {
      showFilelistsFetchStatus("Request failed: " + err.message, true);
    });
  });

  function showFilelistsFetchStatus(text, isError) {
    el.filelistsFetchStatus.textContent = text;
    el.filelistsFetchStatus.classList.toggle("is-error", !!isError);
  }

  // Switching which bot's list is shown ------------------------------------

  // Delegated, because the rows are rebuilt on every poll - a listener per
  // row would be re-attached each time, and the row the operator is aiming
  // at can be replaced between the mousedown and the click.
  el.filelistsBotList.addEventListener("click", function (evt) {
    var row = evt.target.closest ? evt.target.closest(".bot-row") : null;
    if (!row) { return; }

    // A bot we have only seen advertising has no list to page through. Rather
    // than switching to a source that would come back empty, put its nick
    // where fetching one starts.
    if (row.dataset.held === "no") {
      el.filelistsFetchInput.value = row.dataset.bot;
      el.filelistsFetchInput.focus();
      showFilelistsFetchStatus("No list held for " + row.dataset.bot
        + " yet. Press Fetch to ask for it.");
      return;
    }

    // WHILE FILTERING the sidebar answers a different question, so the click
    // does a different thing: the table is showing every list at once, so
    // "switch to this one" has nothing to mean. Clicking toggles whether
    // that bot's matches are on screen instead.
    if ((state.filelistsFilter || "").trim() && row.dataset.bot !== "__own__") {
      var key = String(row.dataset.bot || "").toLowerCase();
      if (state.filelistsExcluded[key]) {
        delete state.filelistsExcluded[key];
      } else {
        state.filelistsExcluded[key] = true;
      }
      rerenderFromFilterPayload();
      return;
    }

    if (row.dataset.bot === state.filelistsSource) { return; }
    state.filelistsSource = row.dataset.bot;
    markFilelistsActiveBot();
    state.filelistsOffset = 0;
    state.filelistsHistory = [];
    loadFilelists();
  });

  el.filelistsFilterInput.addEventListener("input", function () {
    state.filelistsFilter = el.filelistsFilterInput.value;
    runFilelistsFilter();
  });

  el.filelistsFilterAll.addEventListener("click", function () {
    setEveryListShown(true);
  });

  el.filelistsFilterNone.addEventListener("click", function () {
    setEveryListShown(false);
  });

  el.filelistsFilterClear.addEventListener("click", function () {
    el.filelistsFilterInput.value = "";
    state.filelistsFilter = "";
    runFilelistsFilter();
    el.filelistsFilterInput.focus();
  });

  el.filelistsPrevBtn.addEventListener("click", function () {
    if (state.filelistsOffset <= 0) { return; }
    // Pop the offset this page was reached FROM rather than subtracting a
    // page size. Forward steps are not a fixed width (see Next below), so
    // arithmetic backwards would land somewhere no page ever started.
    state.filelistsOffset = state.filelistsHistory.length
      ? state.filelistsHistory.pop()
      : Math.max(0, state.filelistsOffset - FILELISTS_PAGE_SIZE);
    loadFilelists();
  });

  el.filelistsNextBtn.addEventListener("click", function () {
    // Advance by the number of folders the server actually RETURNED, not by
    // the number asked for. A page is also capped by a row ceiling, so a
    // request for 200 folders can come back with 33 - and stepping by 200
    // would skip the 167 in between without a word.
    var step = state.filelistsReturned || FILELISTS_PAGE_SIZE;
    var next = state.filelistsOffset + step;
    if (next >= state.filelistsTotal) { return; }
    state.filelistsHistory.push(state.filelistsOffset);
    state.filelistsOffset = next;
    loadFilelists();
  });

  function pollFilelistsBots() {
    fetchJson("/api/filelists/bots").then(function (rows) {
      markConnection(true);
      renderFilelistsSwitcher(rows);
      renderFilelistsFreshness();
    }).catch(function () { markConnection(false); });
  }

  // BUILT WITH DOM APIs, not concatenated markup. A bot nick is remote input
  // - it is whatever that bot called itself in a channel - and escapeHtml()
  // does not encode a quote, so a nick in an attribute is the break-out this
  // file already carries several comments about. createElement/textContent
  // has no HTML-parsing step at all, and the nick is kept in .dataset rather
  // than in the markup, the same way the file checkboxes carry theirs.
  function renderFilelistsSwitcher(rows) {
    // The bot list is rebuilt from scratch every FILELISTS_BOTS_POLL_MS, and
    // everything the filter put on it lives in classes on those rows - so
    // without the restore at the end of this function the greying, the
    // crossed-out names and the operator's own switched-off choices all
    // vanished four seconds after they appeared, repeatedly. The scroll
    // position went with them, which on a channel with thirty-odd
    // advertisers means the list jumps back to the top while being read.
    var keptScroll = el.filelistsBotList.scrollTop;
    var keptFocus = document.activeElement;
    var refocusBot = (keptFocus && keptFocus.classList
      && keptFocus.classList.contains("bot-row"))
      ? keptFocus.dataset.bot : null;

    var list = el.filelistsBotList;
    var previous = state.filelistsSource || "__own__";

    state.filelistsBots = {};
    list.innerHTML = "";
    list.appendChild(botRow({ bot: "__own__", label: "Our own list",
                              held: true, freshness: "own" }));

    rows.forEach(function (row) {
      state.filelistsBots[row.bot] = row;
      list.appendChild(botRow(row));
    });

    // A source that has gone - the bot dropped out of the registry, or its
    // list was removed - falls back to our own rather than leaving the view
    // pointed at nothing.
    var stillThere = previous === "__own__" ||
      rows.some(function (row) { return row.bot === previous && row.held; });
    state.filelistsSource = stillThere ? previous : "__own__";
    markFilelistsActiveBot();

    // Put back what the rebuild just discarded.
    if (state.filelistsFilterPayload) {
      applyFilterHighlight(state.filelistsFilterPayload);
    }
    el.filelistsBotList.scrollTop = keptScroll;
    if (refocusBot) {
      // Found by WALKING the rows and comparing .dataset, not by building a
      // selector with the nick in it. A nick is remote input, and this file's
      // rule is that one never gets concatenated into anything that is then
      // parsed. The XSS guards in test_webserver.py scan for a data attribute
      // being opened in a concatenation and do not care whether the result is
      // markup or a selector - which is the right amount of strict, and is
      // why this walks instead.
      var candidates = el.filelistsBotList.querySelectorAll(".bot-row");
      for (var r = 0; r < candidates.length; r++) {
        if (candidates[r].dataset.bot === refocusBot) {
          candidates[r].focus();
          break;
        }
      }
    }
  }

  function botRow(row) {
    var button = document.createElement("button");
    button.type = "button";
    button.className = "bot-row";
    button.dataset.bot = row.bot;
    button.dataset.held = row.held ? "yes" : "no";

    var led = document.createElement("span");
    led.className = "led " + ledClass(row.freshness);
    led.title = ledTitle(row.freshness);
    button.appendChild(led);

    var name = document.createElement("span");
    name.className = "bot-row-name";
    name.textContent = row.label || row.bot;
    button.appendChild(name);

    var count = document.createElement("span");
    count.className = "bot-row-count";
    // An em dash, not 0: a bot that published no count did not say, and
    // saying zero would be a claim they never made.
    count.textContent = row.count === undefined || row.count === null
      ? "\u2014" : Number(row.count).toLocaleString();
    button.appendChild(count);

    if (!row.held && row.bot !== "__own__") {
      button.title = "You have not downloaded this bot's list. " +
        "Click to put its nick in the fetch box.";
    }
    return button;
  }

  function ledClass(freshness) {
    if (freshness === "current" || freshness === "own") { return "is-current"; }
    if (freshness === "changed") { return "is-changed"; }
    if (freshness === "not_held") { return "is-not-held"; }
    return "is-unknown";
  }

  function ledTitle(freshness) {
    if (freshness === "changed") {
      return "Their list has changed since you downloaded it";
    }
    if (freshness === "not_held") { return "Not downloaded"; }
    if (freshness === "unknown") {
      return "Cannot tell - we have not seen what they advertise, " +
             "or they publish no date or count";
    }
    return "Current";
  }

  function markFilelistsActiveBot() {
    var rows = el.filelistsBotList.querySelectorAll(".bot-row");
    for (var i = 0; i < rows.length; i++) {
      var active = rows[i].dataset.bot === state.filelistsSource;
      rows[i].classList.toggle("is-active", active);
      // aria-current, not aria-selected: these are ordinary buttons the
      // operator tabs through, not the options of a listbox, and claiming
      // the listbox role would promise arrow-key navigation we do not
      // implement.
      if (active) {
        rows[i].setAttribute("aria-current", "true");
      } else {
        rows[i].removeAttribute("aria-current");
      }
    }
  }

  // Loading the table itself ------------------------------------------------

    // Both /api/filelists and /api/filelists/bot/<nick> return a page of
    // FOLDERS - `{"folders","total","total_files","offset","limit"}` - rather
    // than a page of loose rows. Paging by the folder keeps a folder whole:
    // the folder is the unit an operator browses, and paging by row split
    // large ones across a page boundary, so page two opened mid-folder under
    // no heading at all.
    //
    // `total` counts folders, which is what the pager steps through;
    // `total_files` counts the rows inside them, which is what the operator
    // actually wants to know about the list.
    function renderFilelistsPager(totalFiles) {
      var total = state.filelistsTotal || 0;
      var offset = state.filelistsOffset || 0;
      var shown = state.filelistsReturned || 0;
      var start = shown === 0 ? 0 : offset + 1;
      var end = offset + shown;
      var files = totalFiles || 0;
      el.filelistsPageInfo.textContent =
        "Folders " + start.toLocaleString() + "–" + end.toLocaleString() +
        " of " + total.toLocaleString() +
        " (" + files.toLocaleString() + (files === 1 ? " file)" : " files)");
      el.filelistsPrevBtn.disabled = offset <= 0;
      el.filelistsNextBtn.disabled = (offset + shown) >= total;
    }

    // A file that reached us with no folder heading above it still has to be
    // shown under something.
    function folderLabel(name) {
      return name ? name : "(no folder)";
    }

    // The count is the point of a collapsed folder: it says how much is inside
    // before the operator spends a click finding out. `count` is the folder's
    // TRUE size, so a folder that arrived truncated still reports what it
    // holds rather than only what fitted on the page.
    function folderHeadingHtml(group, index) {
      var count = group.count || 0;
      // Packing a whole folder as .rar only makes sense against another
      // bot's list - browsing our own is direct filesystem access already,
      // same gate folderFilesHtml() already applies to the per-file
      // checkbox column.
      // A group with no folder name has nothing to pack: requestFolderRar()
      // drops the click on `if (!bot || !folder)`, so the button was there,
      // clickable, and silently did nothing - one click, no request, no
      // message. Found by audit. A foreign list always has this group when
      // any of its rows sat above the first folder heading.
      // Same rule as the file rows below, and for the same reason: while
      // FILTERING there is no single source, so asking whether the SELECTED
      // one is another bot's list answers the wrong question. Every group in
      // a filter result belongs to another bot by definition, and this
      // suppressed the folder button on all of them.
      var fetchable = ((state.filelistsFilter || "").trim()
        ? true
        : (state.filelistsSource || "__own__") !== "__own__")
        && !!group.folder;
      // data-folder-index is safe to string-concatenate: it is this group's
      // own position in the internal `groups` array (an internal loop
      // index), not untrusted content - unlike the bot/folder values
      // attachFilelistsFolderRarData() sets below via .dataset assignment.
      var rarButton = fetchable
        ? "<button type=\"button\" class=\"btn btn-small folder-rar-btn\"" +
          " data-folder-index=\"" + index + "\">Get folder as .rar</button>"
        : "";
      return "<tr class=\"folder-row\">" +
        "<td colspan=\"5\">" +
          "<button type=\"button\" class=\"folder-toggle\" aria-expanded=\"false\"" +
                 " data-folder-index=\"" + index + "\">" +
            "<span class=\"folder-caret\" aria-hidden=\"true\"></span>" +
            "<span class=\"folder-name\">" +
              escapeHtml(folderLabel(group.folder)) + "</span>" +
            "<span class=\"folder-count\">" + count.toLocaleString() +
              (count === 1 ? " file" : " files") + "</span>" +
          "</button>" +
          rarButton +
        "</td></tr>";
    }

    function folderFilesHtml(group, index) {
      var entries = group.entries || [];
      // A file is only fetchable when it belongs to someone ELSE's list -
      // browsing our own is direct filesystem access already, and
      // /api/fetch/enqueue exists to reach another bot over IRC, not this one.
      //
      // While FILTERING there is no single source: the rows come from every
      // list held, and every one of them is another bot's by definition. This
      // asked only about the selected source, which defaults to our own list -
      // so filtering before picking a bot rendered every result with no
      // checkbox and no way to queue any of it.
      var fetchable = (state.filelistsFilter || "").trim()
        ? true
        : (state.filelistsSource || "__own__") !== "__own__";
      var rows = entries.map(function (row) {
        // No data-bot/data-filename attribute here, and no bot/filename text
        // anywhere in this markup fragment: `row.source`/`row.title` come
        // from another bot's fetched list file - attacker-controlled the
        // same way the broadcast table's entry.bot/entry.filename were (see
        // BroadcastRenderingXssRegressionTests) - and escapeHtml() does not
        // encode `"`, so string-concatenating either into an HTML attribute
        // value is exactly BUG 2 again. attachFilelistsCheckboxData(), called
        // right after this HTML lands in the DOM, sets both via .dataset
        // instead, which the browser assigns as a property with no
        // HTML-parsing step for the value to break out through.
        var checkCell = fetchable
          ? "<td class=\"col-check\"><input type=\"checkbox\" class=\"filelists-check\"></td>"
          : "<td class=\"col-check\"></td>";
        // Two states, never three: "requested" while it is still in
        // flight, "received" once it has arrived, and nothing at all for a
        // failed one - a failure is not a thing you have, and marking it
        // would discourage the one useful action left, which is to ask
        // again. The server decides which; this only renders it.
        //
        // escapeHtml() on the mark as well, even though it comes from a
        // fixed set: the day it does not, this line should already be safe.
        var mark = row.mark === "received" || row.mark === "requested"
          ? " <span class=\"file-mark is-" + row.mark + "\">" +
            escapeHtml(row.mark === "received" ? "have it" : "asked") +
            "</span>"
          : "";
        return "<tr class=\"file-row is-hidden\" data-folder-index=\"" + index + "\">" +
          checkCell +
          "<td class=\"col-mono col-indent\">" + escapeHtml(row.title) + mark + "</td>" +
          "<td class=\"col-mono\">" + escapeHtml(row.size) + "</td>" +
          "<td class=\"col-dim\">" + escapeHtml(row.format) + "</td>" +
          "<td class=\"col-dim col-mono\">" + escapeHtml(row.source) + "</td>" +
          "</tr>";
      });
      // A folder bigger than the page's row ceiling arrives cut short. Say so
      // in place, so the rows on screen cannot quietly disagree with the count
      // in the heading right above them.
      if (group.truncated) {
        rows.push(
          "<tr class=\"file-row folder-truncated is-hidden\" data-folder-index=\"" +
          index + "\"><td colspan=\"5\">Showing the first " +
          entries.length.toLocaleString() + " of " +
          (group.count || 0).toLocaleString() +
          " files in this folder.</td></tr>");
      }
      return rows.join("");
    }

    function setFolderExpanded(index, expanded) {
      var rows = el.filelistsBody.querySelectorAll(
        ".file-row[data-folder-index=\"" + index + "\"]");
      for (var i = 0; i < rows.length; i++) {
        rows[i].classList.toggle("is-hidden", !expanded);
      }
    }

    function setAllFolders(expanded) {
      var toggles = el.filelistsBody.querySelectorAll(".folder-toggle");
      for (var i = 0; i < toggles.length; i++) {
        toggles[i].setAttribute("aria-expanded", expanded ? "true" : "false");
      }
      var rows = el.filelistsBody.querySelectorAll(".file-row");
      for (var j = 0; j < rows.length; j++) {
        rows[j].classList.toggle("is-hidden", !expanded);
      }
    }

    // Delegated once here rather than bound per heading: the table is rebuilt
    // on every page change and every source change, and a per-heading listener
    // would have to be re-attached each time.
    el.filelistsBody.addEventListener("click", function (evt) {
      var toggle = evt.target.closest ? evt.target.closest(".folder-toggle") : null;
      if (toggle) {
        var expanded = toggle.getAttribute("aria-expanded") === "true";
        toggle.setAttribute("aria-expanded", expanded ? "false" : "true");
        setFolderExpanded(toggle.getAttribute("data-folder-index"), !expanded);
        return;
      }
      var rarBtn = evt.target.closest ? evt.target.closest(".folder-rar-btn") : null;
      if (rarBtn) { requestFolderRar(rarBtn); }
    });

    el.filelistsExpandAll.addEventListener("click", function () { setAllFolders(true); });
    el.filelistsCollapseAll.addEventListener("click", function () { setAllFolders(false); });

    // Delegated for the same reason as the folder-toggle listener above: the
    // checkboxes are rebuilt from scratch on every page/source change, so a
    // listener attached per-checkbox would need re-attaching every time.
    el.filelistsBody.addEventListener("change", function (evt) {
      if (evt.target.classList && evt.target.classList.contains("filelists-check")) {
        state.filelistsLastChecked = evt.target;
        updateFilelistsDownloadSelectedState();
      }
    });

    // SHIFT EXTENDS A RANGE (#133). Handled on click rather than change,
    // because the range has to be computed against the state BEFORE the
    // browser toggles the clicked box - and because shift-clicking a checkbox
    // also selects text across the rows it spans, which looks broken.
    //
    // The anchor is the last box the operator actually touched, which is what
    // every file manager means by it. Ctrl needs no code at all: a checkbox
    // toggles one box on its own, which is already ctrl's behaviour.
    //
    // A rebuild of the table drops the anchor (the element is gone), so
    // renderFilelists clears it rather than leaving a reference to a node
    // that is no longer in the document.
    el.filelistsBody.addEventListener("click", function (evt) {
      var box = evt.target;
      if (!box.classList || !box.classList.contains("filelists-check")) { return; }
      var anchor = state.filelistsLastChecked;
      if (!evt.shiftKey || !anchor || anchor === box) { return; }

      var boxes = Array.prototype.slice.call(
        el.filelistsBody.querySelectorAll(".filelists-check"));
      var from = boxes.indexOf(anchor);
      var to = boxes.indexOf(box);
      if (from === -1 || to === -1) { return; }

      evt.preventDefault();
      window.getSelection && window.getSelection().removeAllRanges();

      // The clicked box takes the anchor's state, and so does everything
      // between them - "extend the selection to here", not "toggle each".
      var wanted = anchor.checked;
      if (from > to) { var swap = from; from = to; to = swap; }
      for (var i = from; i <= to; i++) {
        boxes[i].checked = wanted;
      }
      updateFilelistsDownloadSelectedState();
    });

    function updateFilelistsDownloadSelectedState() {
      var checked = el.filelistsBody.querySelectorAll(".filelists-check:checked");
      el.filelistsDownloadSelectedBtn.disabled = checked.length === 0;
    }

    // Sets each checkbox's bot/filename via .dataset - a property assignment
    // the browser stores as-is, with no HTML-attribute-parsing step for
    // either value to break out through - rather than string-concatenating
    // them into the markup folderFilesHtml() already built (see that
    // function's comment, and BroadcastRenderingXssRegressionTests, for why
    // that would reopen BUG 2). Walks `groups` in the exact order
    // folderFilesHtml() rendered it in, so the Nth checkbox in the DOM lines
    // up with the Nth fetchable row here - true because every row in one
    // render shares the same `fetchable` value, so a render either produces
    // zero checkboxes or exactly one per row, never a mix to line up against.
    function attachFilelistsCheckboxData(groups) {
      // The old anchor pointed into the table that has just been replaced.
      state.filelistsLastChecked = null;
      var boxes = el.filelistsBody.querySelectorAll(".filelists-check");
      if (!boxes.length) { return; }
      var i = 0;
      groups.forEach(function (group) {
        (group.entries || []).forEach(function (row) {
          if (i >= boxes.length) { return; }
          boxes[i].dataset.bot = row.source;
          boxes[i].dataset.filename = row.title;
          i += 1;
        });
      });
    }

    // Same reasoning and same pattern as attachFilelistsCheckboxData() right
    // above: state.filelistsSource/group.folder must NEVER be
    // string-concatenated into an HTML attribute (escapeHtml() does not
    // encode `"`, so a malicious folder name could break out of one - see
    // BroadcastRenderingXssRegressionTests for the bug class this avoids).
    // Each button already carries its own group's index via
    // data-folder-index (safe - an internal loop index, set in
    // folderHeadingHtml() above), so this just looks that index back up in
    // `groups` and sets the real bot/folder via .dataset property
    // assignment instead.
    function attachFilelistsFolderRarData(groups) {
      var buttons = el.filelistsBody.querySelectorAll(".folder-rar-btn");
      for (var i = 0; i < buttons.length; i++) {
        var button = buttons[i];
        var index = parseInt(button.dataset.folderIndex, 10);
        var group = groups[index];
        if (!group) { continue; }
        // The GROUP's bot when it has one. A cross-list filter shows
        // groups from several bots at once, and state.filelistsSource is
        // whichever list the sidebar has selected - not the one this folder
        // came from. Requesting the right folder from the wrong bot is a
        // request that cannot succeed.
        button.dataset.bot = group.bot || state.filelistsSource;
        button.dataset.folder = group.folder;
      }
    }

    function requestFolderRar(button) {
      var bot = button.dataset.bot;
      var folder = button.dataset.folder;
      if (!bot || !folder) { return; }
      button.disabled = true;
      postJson("/api/filelists/fetch-folder-rar", { bot: bot, folder: folder }).then(function (res) {
        if (!res.ok) {
          showFilelistsFetchStatus(res.data.error || ("HTTP " + res.status), true);
          button.disabled = false;
          return;
        }
        showFilelistsFetchStatus(
          "Requested " + bot + "’s folder as .rar - track its progress on the Download tab.");
        loadDownloads();
      }).catch(function (err) {
        showFilelistsFetchStatus("Request failed: " + err.message, true);
        button.disabled = false;
      });
    }

    el.filelistsDownloadSelectedBtn.addEventListener("click", function () {
      var checked = el.filelistsBody.querySelectorAll(".filelists-check:checked");
      var items = Array.prototype.map.call(checked, function (box) {
        return { bot: box.dataset.bot, filename: box.dataset.filename };
      });
      if (!items.length) { return; }
      el.filelistsDownloadSelectedBtn.disabled = true;
      postJson("/api/fetch/enqueue", items).then(function (res) {
        if (!res.ok && !(res.data && res.data.created && res.data.created.length)) {
          showFilelistsFetchStatus("Could not queue the download: " +
            (res.data.error || (res.data.errors && res.data.errors[0] && res.data.errors[0].error) || ("HTTP " + res.status)), true);
        } else {
          showFilelistsFetchStatus(
            "Queued " + res.data.created.length + " file(s) for fetch - see Queue → Downloads.", false);
          Array.prototype.forEach.call(checked, function (box) { box.checked = false; });
          // Re-read the page so the rows just queued say so. The marks are
          // stamped server-side when a page is built, so without this they
          // would not appear until something else caused a reload - and the
          // one moment an operator most wants to see "asked" is immediately
          // after asking.
          loadFilelists();
        }
        updateFilelistsDownloadSelectedState();
        loadDownloads();
      }).catch(function (err) {
        showFilelistsFetchStatus("Could not queue the download: " + err.message, true);
        updateFilelistsDownloadSelectedState();
      });
    });

    // Accepts the older flat shapes as well as the folder one: a bare array,
    // or a payload carrying `entries`, both become a single unnamed folder
    // rather than a blank table. That is the same defensive unwrap this did
    // before paging existed, extended to cover the shape that replaced it.
    function folderGroupsFrom(payload) {
      if (Array.isArray(payload)) {
        return payload.length
          ? [{ folder: "", count: payload.length, entries: payload }]
          : [];
      }
      if (payload.folders) { return payload.folders; }
      var rows = payload.entries || [];
      return rows.length ? [{ folder: "", count: rows.length, entries: rows }] : [];
    }

    // Their advert THEN against their advert NOW - never against our own
  // parsed row count, which is a different thing counted a different way (see
  // webserver._freshness). "unknown" renders as nothing at all: a bot that
  // publishes no date, or one whose advert we have not seen, should show no
  // freshness claim rather than an invented one.
  // Greys out the bots with nothing to show for the current term, and
  // clears every mark again when the term goes. Driven by what the server
  // said rather than by what came back in the page: the page is capped, so a
  // bot whose matches all fall past the cap would look empty when it is not.
  function applyFilterHighlight(payload) {
    var rows = el.filelistsBotList.querySelectorAll(".bot-row");
    var filtering = !!(state.filelistsFilter || "").trim();
    var empty = {};
    if (payload && Array.isArray(payload.empty)) {
      payload.empty.forEach(function (name) { empty[String(name).toLowerCase()] = true; });
    }
    for (var i = 0; i < rows.length; i++) {
      var bot = String(rows[i].dataset.bot || "").toLowerCase();
      // Our own list is not one of the lists the filter searches - it covers
      // lists FETCHED from other bots - so it is never greyed by it.
      var dim = filtering && bot !== "__own__" && empty[bot] === true;
      rows[i].classList.toggle("is-filtered-out", dim);
      // Switched off BY THE OPERATOR, which is a different thing from having
      // nothing to show and reads differently: one is an answer, the other is
      // a choice, and the choice is reversible by clicking again.
      rows[i].classList.toggle(
        "is-excluded",
        filtering && !!state.filelistsExcluded[bot]);
    }

    if (!filtering) {
      el.filelistsFilterStatus.hidden = true;
      el.filelistsFilterStatus.textContent = "";
      return;
    }
    var files = (payload && payload.total_files) || 0;
    var matched = (payload && payload.matched && payload.matched.length) || 0;
    var text = files
      ? files.toLocaleString() + (payload && payload.truncated ? "+" : "") +
        " match" + (files === 1 ? "" : "es") + " in " + matched +
        " list" + (matched === 1 ? "" : "s")
      : "No matches in any list you hold";
    if (payload && payload.truncated) {
      text += " \u2014 showing the first " + files + ", narrow the term to see the rest";
    }
    el.filelistsFilterStatus.hidden = false;
    el.filelistsFilterStatus.textContent = text;
  }

  // Every group the current answer holds, minus the bots switched off.
  // Client-side on purpose: #133 calls the toggling trivial precisely because
  // the rows are already in the browser, and re-asking the server for a
  // narrower set would be slower than the search that fetched them.
  function visibleFilterGroups(groups) {
    if (!(state.filelistsFilter || "").trim()) { return groups; }
    return groups.filter(function (group) {
      return !state.filelistsExcluded[String(group.bot || "").toLowerCase()];
    });
  }

  function renderFilelistGroups(allGroups, filtering) {
    var groups = visibleFilterGroups(allGroups);
    if (!groups.length) {
      el.filelistsBody.innerHTML = emptyRow(5, filtering
        ? (allGroups.length
            ? "Every list with a match is switched off."
            : "Nothing in any list you hold matches that.")
        : "No files published yet.");
      updateFilelistsDownloadSelectedState();
      return;
    }
    el.filelistsBody.innerHTML = groups.map(function (group, index) {
      return folderHeadingHtml(group, index) + folderFilesHtml(group, index);
    }).join("");
    attachFilelistsCheckboxData(groups);
    attachFilelistsFolderRarData(groups);
    updateFilelistsDownloadSelectedState();
  }

  // Re-renders from the answer already held. Used by the sidebar toggle and
  // by the two buttons, none of which change what MATCHES - only which of
  // the matching lists is on screen.
  function rerenderFromFilterPayload() {
    var payload = state.filelistsFilterPayload;
    if (!payload) { return; }
    applyFilterHighlight(payload);
    renderFilelistGroups(folderGroupsFrom(payload), true);
  }

  function setEveryListShown(shown) {
    state.filelistsExcluded = {};
    if (!shown) {
      var payload = state.filelistsFilterPayload;
      var names = (payload && payload.matched) || [];
      names.forEach(function (name) {
        state.filelistsExcluded[String(name).toLowerCase()] = true;
      });
    }
    rerenderFromFilterPayload();
  }

  function runFilelistsFilter() {
    // Every reply carries the token it was issued with, and only the newest
    // is allowed to render. Typing fast puts several requests in flight and
    // the slowest is not necessarily the oldest - without this, stopping
    // typing can leave an earlier term's results on screen under the later
    // term in the box.
    state.filelistsFilterToken += 1;
    var token = state.filelistsFilterToken;
    var term = (state.filelistsFilter || "").trim();

    el.filelistsFilterClear.hidden = !term;
    el.filelistsFilterActions.hidden = !term;
    // A new term is a new question, so nothing carries over: a bot switched
    // off while looking for one thing should not be silently switched off
    // while looking for the next.
    state.filelistsExcluded = {};
    state.filelistsOffset = 0;
    state.filelistsHistory = [];

    window.setTimeout(function () {
      if (token !== state.filelistsFilterToken) { return; }
      loadFilelists();
    }, term ? FILELISTS_FILTER_DEBOUNCE_MS : 0);
  }

  function renderFilelistsFreshness() {
    var banner = el.filelistsFreshness;
    if (!banner) { return; }
    var row = state.filelistsBots[state.filelistsSource];
    if (!row || row.freshness !== "changed") {
      banner.hidden = true;
      banner.textContent = "";
      return;
    }

    var then = row.advert_then || {};
    var now = row.advert_now || {};
    banner.hidden = false;
    banner.textContent =
      "Their list has changed since you downloaded it \u2014 they advertised " +
      describeAdvert(then) + ", and now advertise " + describeAdvert(now) +
      ". Fetch it again to see what they are offering now.";
  }

  function describeAdvert(advert) {
    var parts = [];
    if (advert.files) { parts.push(Number(advert.files).toLocaleString() + " files"); }
    if (advert.list_date) { parts.push("built " + advert.list_date); }
    return parts.length ? parts.join(", ") : "nothing we could read";
  }

  function loadFilelists() {
      // THE REPLY's token, not the timer's. runFilelistsFilter() already had
      // one and its comment claimed "only the newest is allowed to render" -
      // but it was compared inside the debounce callback, BEFORE this
      // function was even called, so it decided which request to send and
      // nothing decided which reply to draw.
      //
      // Two requests are easily in flight at 120ms of debounce: a broad term
      // is slow, one more character is narrow and fast, and the narrow reply
      // arrives first. The broad one then lands and repaints the table and
      // the sidebar under the later term still in the box - and because it
      // also caches itself as filelistsFilterPayload, every later re-render
      // keeps serving it until the next keystroke.
      state.filelistsLoadToken += 1;
      var loadToken = state.filelistsLoadToken;

      el.filelistsBody.innerHTML = emptyRow(5, "Loading…");
      var source = state.filelistsSource || "__own__";
      var offset = state.filelistsOffset || 0;
      var filter = (state.filelistsFilter || "").trim();
      var url;
      if (filter) {
        // The filter replaces the browse view rather than narrowing it: it
        // spans every list held, so "which bot am I looking at" stops being
        // the question while a term is set. The sidebar still shows which
        // bots have matches - see applyFilterHighlight().
        url = "/api/filelists/search?q=" + encodeURIComponent(filter);
      } else {
        var base = (source === "__own__")
          ? "/api/filelists"
          : "/api/filelists/bot/" + encodeURIComponent(source);
        url = base + "?offset=" + offset + "&limit=" + FILELISTS_PAGE_SIZE;
      }

      fetchJson(url)
        .then(function (payload) {
          markConnection(true);
          if (loadToken !== state.filelistsLoadToken) { return; }
          state.filelistsLoaded = true;
          var groups = folderGroupsFrom(payload);
          applyFilterHighlight(payload);
          state.filelistsTotal = Array.isArray(payload)
            ? groups.length : (payload.total || 0);
          state.filelistsReturned = groups.length;
          renderFilelistsPager(Array.isArray(payload)
            ? payload.length : (payload.total_files || 0));
          state.filelistsFilterPayload = filter ? payload : null;
          renderFilelistGroups(groups, !!filter);
        })
        .catch(function (err) {
          markConnection(false);
          // A superseded request failing is not this view's problem: the
          // newer one is what the operator is waiting for, and painting an
          // error over its results would be the same staleness bug wearing
          // an error message.
          if (loadToken !== state.filelistsLoadToken) { return; }
          el.filelistsBody.innerHTML = emptyRow(5, "Could not load file lists: " + err.message);
          updateFilelistsDownloadSelectedState();
        });
    }

  // ------------------------------------------- Coming from OmenServe (#69)

  // What the server says it recognises. Fetched once and cached: the filter
  // below has to keep exactly the lines the parser knows about, and hard-
  // coding that list here is how the two would drift - a field added to
  // omenserve_import.FIELDS would then be stripped out before it arrived.
  var importVariableNames = null;

  function importVariables() {
    if (importVariableNames) { return Promise.resolve(importVariableNames); }
    return fetchJson("/api/stats/import/variables").then(function (payload) {
      importVariableNames = (payload && payload.variables) || [];
      return importVariableNames;
    });
  }

  // KEEP ONLY THE COUNTER LINES. A real vars.ini on the install #69 was
  // written from held 280 variables: nicks, channel names, paths, add-on
  // settings and passwords among them. None of that is any of the bot's
  // business, and none of it needs to cross the network for this to work.
  //
  // Done here rather than server-side for exactly that reason: the filtering
  // has to happen before the upload, or it is not filtering.
  function keepOnlyCounterLines(text, names) {
    var wanted = {};
    names.forEach(function (name) { wanted[String(name).toLowerCase()] = true; });
    return String(text || "").split(/\r?\n/).filter(function (line) {
      var match = /^\s*n\d+\s*=\s*(%\S+)/i.exec(line);
      return !!match && wanted[match[1].toLowerCase()] === true;
    }).join("\n");
  }

  function showImportStatus(text, isError) {
    el.importStatus.hidden = !text;
    el.importStatus.textContent = text || "";
    el.importStatus.classList.toggle("is-error", !!isError);
  }

  function resetImportPreview() {
    state.importValues = null;
    el.importPreviewWrap.hidden = true;
    el.importPreview.innerHTML = "";
    el.importWarning.hidden = true;
    el.importWarning.textContent = "";
    el.importConfirm.hidden = true;
  }

  function renderImportPreview(payload) {
    var current = payload.current || {};
    var values = payload.values || {};
    var byTarget = {
      "Files sent": "total_files",
      "Bytes sent": "total_bytes",
      "Speed record": "speed_record"
    };

    var body = "";
    (payload.rows || []).forEach(function (row) {
      if (row.value === null || row.value === undefined) { return; }
      var target = byTarget[row.label];
      var now = target ? (current[target] || 0) : null;
      var after = target && values[target] !== undefined ? values[target] : null;
      body += "<tr>" +
        "<td>" + escapeHtml(row.label) +
          (target ? "" : " <span class=\"import-skipped\">not imported</span>") +
        "</td>" +
        "<td class=\"col-num col-mono\">" +
          (now === null ? "&mdash;" : Number(now).toLocaleString()) + "</td>" +
        "<td class=\"col-num col-mono\">" +
          (after === null ? "&mdash;" : Number(after).toLocaleString()) + "</td>" +
        "</tr>";
    });

    if (!body) {
      resetImportPreview();
      showImportStatus((payload.notes || []).join(" ") ||
        "Nothing recognisable was found in that file.", true);
      return;
    }

    el.importPreview.innerHTML = body;
    el.importPreviewWrap.hidden = false;
    state.importValues = values;

    // OVERWRITTEN, NOT COMBINED - and said only when it matters. On a fresh
    // install nobody reads that sentence; on a used one it is the only thing
    // that does. `replaces` is the server's answer to "which of these already
    // has a non-zero value", so the warning appears exactly then.
    var replacing = Object.keys(payload.replaces || {});
    var notes = (payload.notes || []).slice();
    if (replacing.length) {
      notes.unshift("Your current figures will be REPLACED, not added to.");
    }
    el.importWarning.hidden = !notes.length;
    el.importWarning.textContent = notes.join(" ");
    el.importConfirm.hidden = false;
    showImportStatus("");
  }

  function previewImportText(text) {
    if (!String(text || "").trim()) {
      resetImportPreview();
      showImportStatus("That file was empty.", true);
      return;
    }
    importVariables().then(function (names) {
      var kept = keepOnlyCounterLines(text, names);
      if (!kept.trim()) {
        resetImportPreview();
        showImportStatus("No OmenServe counters were found in that file. The " +
          "totals come from the add-ons (mxrarserver, OS-Limits), so an " +
          "install without them has nothing to bring across.", true);
        return;
      }
      return postJson("/api/stats/import/preview", { text: kept })
        .then(function (res) {
          if (!res.ok) {
            resetImportPreview();
            showImportStatus(res.data.error || ("HTTP " + res.status), true);
            return;
          }
          renderImportPreview(res.data);
        });
    }).catch(function (err) {
      resetImportPreview();
      showImportStatus("Could not read that: " + err.message, true);
    });
  }

  el.importFile.addEventListener("change", function () {
    var file = el.importFile.files && el.importFile.files[0];
    if (!file) { return; }
    var reader = new FileReader();
    reader.onload = function () { previewImportText(reader.result); };
    reader.onerror = function () {
      showImportStatus("Could not read that file.", true);
    };
    reader.readAsText(file);
    // Cleared so choosing the SAME file again still fires a change event -
    // otherwise a second attempt after an error looks like a dead button.
    el.importFile.value = "";
  });

  el.importPasteToggle.addEventListener("click", function () {
    var showing = el.importPaste.hidden;
    el.importPaste.hidden = !showing;
    el.importPasteActions.hidden = !showing;
    if (showing) { el.importPaste.focus(); }
  });

  el.importPasteRead.addEventListener("click", function () {
    previewImportText(el.importPaste.value);
  });

  el.importCancel.addEventListener("click", function () {
    resetImportPreview();
    showImportStatus("");
  });

  el.importApply.addEventListener("click", function () {
    if (!state.importValues) { return; }
    el.importApply.disabled = true;
    postJson("/api/stats/import", state.importValues).then(function (res) {
      el.importApply.disabled = false;
      if (!res.ok) {
        showImportStatus(res.data.error || ("HTTP " + res.status), true);
        return;
      }
      resetImportPreview();
      // What ACTUALLY happened, from the server's own before/after, rather
      // than what the page asked for.
      var after = res.data.after || {};
      showImportStatus("Imported. Files sent is now " +
        Number(after.total_files || 0).toLocaleString() + ", and the speed " +
        "record " + Number(after.speed_record || 0).toLocaleString() + " B/s.");
      loadStats();
    }).catch(function (err) {
      el.importApply.disabled = false;
      showImportStatus("Import failed: " + err.message, true);
    });
  });

  // ---------------------------------------------------------------- Tools

  // Rebuilding the list is the dashboard's own equivalent of !update -
  // added because FILE_DIRECTORY is deliberately not in
  // settings_file.REQUIRED (see settings_file.py's own comment): an
  // operator who sets it for the first time from the Settings page had no
  // way at all to then build the list it enables, short of a real IRC
  // client or a CLI already running. A rebuild can take minutes on a real
  // library, so this polls /status rather than waiting on the POST itself -
  // the same shape as broadcast search above.
  var updateList = { pollTimer: null };

  el.updateListRunBtn.addEventListener("click", function () {
    el.updateListRunBtn.disabled = true;
    showUpdateListStatus("Starting…", false);
    postJson("/api/tools/update-list", {}).then(function (res) {
      if (!res.ok) {
        el.updateListRunBtn.disabled = false;
        showUpdateListStatus(res.data.error || ("HTTP " + res.status), true);
        return;
      }
      startUpdateListPolling();
    }).catch(function (err) {
      el.updateListRunBtn.disabled = false;
      showUpdateListStatus("Request failed: " + err.message, true);
    });
  });

  function showUpdateListStatus(text, isError) {
    el.updateListStatus.textContent = text;
    el.updateListStatus.classList.toggle("is-error", !!isError);
  }

  function startUpdateListPolling() {
    if (updateList.pollTimer) { clearInterval(updateList.pollTimer); }
    pollUpdateListStatus();
    updateList.pollTimer = setInterval(pollUpdateListStatus, UPDATE_LIST_POLL_MS);
  }

  function pollUpdateListStatus() {
    fetchJson("/api/tools/update-list/status").then(function (payload) {
      markConnection(true);
      if (payload.running) {
        showUpdateListStatus("Rebuilding the master list…", false);
        return;
      }
      clearInterval(updateList.pollTimer);
      updateList.pollTimer = null;
      el.updateListRunBtn.disabled = false;
      // #224: "running" alone cannot tell a rebuild that worked from one
      // that failed - this used to say "Done" unconditionally the moment
      // running flipped false, whichever it was.
      if (payload.ok === false) {
        showUpdateListStatus("Failed: " + (payload.error || "unknown error"), true);
      } else {
        showUpdateListStatus("Done. Check Stats for the new file count.", false);
      }
    }).catch(function (err) {
      markConnection(false);
      clearInterval(updateList.pollTimer);
      updateList.pollTimer = null;
      el.updateListRunBtn.disabled = false;
      showUpdateListStatus("Lost track of the update: " + err.message, true);
    });
  }

  // The Tools view runs nothing on its own beyond the update above.
  // Verifying the list re-reads and re-parses the whole master list, which
  // is work worth doing when the operator asks for it and not on every tab
  // switch.
  function renderVerifyResults(payload) {
    var duplicates = payload.duplicates || [];
    var checked = payload.checked || 0;

    if (!duplicates.length) {
      el.verifyStatus.textContent =
        "No duplicates. All " + checked.toLocaleString() +
        " filenames in the list are unique.";
      el.verifyStatus.classList.remove("is-error");
      el.verifyResults.innerHTML = "";
      return;
    }

    // "shadowed", not "unreachable" - see webserver.build_verify_list_payload().
    // Since #128 a requester pasting a search result's whole line reaches the
    // copy its size names; it is the bare-name request that only ever gets the
    // first one listed.
    var shadowed = payload.shadowed || 0;
    el.verifyStatus.textContent =
      duplicates.length.toLocaleString() +
      (duplicates.length === 1 ? " filename appears" : " filenames appear") +
      " under more than one folder, out of " + checked.toLocaleString() +
      " checked. " + shadowed.toLocaleString() +
      (shadowed === 1 ? " copy is" : " copies are") +
      " reachable only by pasting a search result's whole line.";
    el.verifyStatus.classList.remove("is-error");

    el.verifyResults.innerHTML = duplicates.map(function (item) {
      // The FIRST folder is the one a request for this name actually
      // reaches - dcc.py resolves the name against this same list and takes
      // the first match. Marking it is the whole point of showing the
      // folders in list order rather than sorted.
      var folders = (item.folders || []).map(function (folder, index) {
        return "<li class=\"verify-folder" + (index === 0 ? " is-served" : "") + "\">" +
          "<span class=\"verify-path\">" +
            escapeHtml(folder || "(library root)") + "</span>" +
          (index === 0 ? "<span class=\"verify-tag\">served</span>"
                       : "<span class=\"verify-tag is-dim\">shadowed</span>") +
          "</li>";
      }).join("");
      return "<div class=\"verify-group\">" +
        "<div class=\"verify-name\">" +
          "<span class=\"verify-file\">" + escapeHtml(item.filename) + "</span>" +
          "<span class=\"verify-count\">" + (item.count || 0) + " folders</span>" +
        "</div>" +
        "<ul class=\"verify-folders\">" + folders + "</ul>" +
      "</div>";
    }).join("");
  }

  el.verifyRunBtn.addEventListener("click", function () {
    el.verifyRunBtn.disabled = true;
    el.verifyStatus.classList.remove("is-error");
    el.verifyStatus.textContent = "Reading the master list…";
    el.verifyResults.innerHTML = "";

    fetchJson("/api/tools/verify-list")
      .then(function (payload) {
        markConnection(true);
        renderVerifyResults(payload);
      })
      .catch(function (err) {
        markConnection(false);
        el.verifyStatus.textContent = "Could not verify the list: " + err.message;
        el.verifyStatus.classList.add("is-error");
      })
      .then(function () {
        el.verifyRunBtn.disabled = false;
      });
  });

  // ------------------------------------------------------------- Settings

  // Values arrive from GET /api/settings already in their real Python types
  // (bool/int/float/list/str/null); every value POSTed back must be a
  // string (settings_file.save() coerces it the same way settings.conf
  // itself would be read) - this turns a loaded value, or nothing at all if
  // the field is still dirty, into the string a fresh baseline comparison
  // needs.
  function settingsValueToString(value) {
    if (typeof value === "boolean") { return value ? "true" : "false"; }
    if (Array.isArray(value)) { return value.join(", "); }
    if (value === null || value === undefined) { return ""; }
    return String(value);
  }

  function settingsFieldHtml(field) {
    var isDirty = Object.prototype.hasOwnProperty.call(state.settingsDirty, field.name);
    var nameClass = "settings-field-name" + (isDirty ? " is-dirty" : "");
    var control;

    if (field.choices) {
      // A fixed few, not free text. The three list formats are the first: a
      // typed "ZIP" or "tar" would be refused by the save with a reason, but
      // being refused is a worse way to find out than never being offered it.
      var options = field.choices.map(function (choice) {
        return '<option value="' + escapeHtml(choice) + '">' + escapeHtml(choice) + "</option>";
      }).join("");
      control = '<select data-setting="' + escapeHtml(field.name) + '">' + options + "</select>";
    } else if (field.type === "bool") {
      var checked = isDirty ? state.settingsDirty[field.name] === "true" : !!field.value;
      control = '<input type="checkbox" data-setting="' + escapeHtml(field.name) + '"' +
        (checked ? " checked" : "") + ">";
    } else if (field.type === "int" || field.type === "float") {
      control = '<input type="number" step="' + (field.type === "float" ? "any" : "1") +
        '" data-setting="' + escapeHtml(field.name) + '">';
    } else {
      control = '<input type="text" autocomplete="off" data-setting="' +
        escapeHtml(field.name) + '">';
    }

    return '<div class="settings-field-row">' +
      '<span class="' + nameClass + '">' + escapeHtml(field.label || field.name) + '</span>' +
      '<span class="settings-field-control">' + control + '</span>' +
      "</div>";
  }

  // Classes, not ids, for everything below - this markup is injected by
  // renderSettingsCategory() every time the operator switches category, and
  // the two listeners it is wired to (added once, further down, on the
  // static el.settingsFields container) are event-delegated for exactly that
  // reason: an id-based document.getElementById() lookup would have nothing
  // to find until the injection has happened at least once, and re-wiring
  // fresh listeners after every re-render would leak the old ones. Same
  // pattern el.filelistsBody's folder-toggle delegation already uses.
  function settingsPasswordSectionHtml() {
    var statusText = "Admin password: " + (state.settingsAdminPasswordSet ? "set" : "not set");
    return (
      '<div class="settings-password-row">' +
        '<span class="settings-password-status">' + escapeHtml(statusText) + "</span>" +
        '<button type="button" class="btn btn-small settings-password-toggle">Change password</button>' +
      "</div>" +
      '<form class="settings-password-form" style="display:none;">' +
        '<input type="password" class="settings-new-password" placeholder="New password" autocomplete="new-password">' +
        '<input type="password" class="settings-confirm-password" placeholder="Confirm new password" autocomplete="new-password">' +
        '<button type="submit" class="btn btn-accent btn-small">Set password</button>' +
      "</form>" +
      '<p class="settings-note settings-password-note" style="display:none;"></p>'
    );
  }

  function onSettingsFieldChange(evt) {
    var input = evt.target;
    var name = input.dataset.setting;
    if (!name) { return; }

    var newValue = (input.type === "checkbox") ? (input.checked ? "true" : "false") : input.value;
    var baselineStr = settingsValueToString(state.settingsBaseline[name]);

    if (newValue === baselineStr) {
      delete state.settingsDirty[name];
    } else {
      state.settingsDirty[name] = newValue;
    }

    var row = input.closest(".settings-field-row");
    var label = row && row.querySelector(".settings-field-name");
    if (label) {
      label.classList.toggle("is-dirty", Object.prototype.hasOwnProperty.call(state.settingsDirty, name));
    }
    updateSettingsSaveBar();
  }

  function updateSettingsSaveBar() {
    var count = Object.keys(state.settingsDirty).length;
    el.settingsSaveBtn.disabled = count === 0;
    el.settingsSavebarText.classList.toggle("is-dirty", count > 0);
    el.settingsSavebarText.textContent = count === 0
      ? "All changes saved"
      : (count + (count === 1 ? " unsaved change" : " unsaved changes"));
  }

  // The served-folder editor. Markup only - NO VALUES - for the reason the
  // whole of this file repeats: escapeHtml() is textContent -> innerHTML,
  // which leaves a double quote alone, so a path containing one would close
  // value="…" and everything after it would be parsed as markup. Every value
  // here is assigned as a .value PROPERTY by attachFolderRows() below.
  // MORE THAN ONE LIST (#26). This replaces the single folders editor rather
  // than sitting beside it: two places to edit folders, one of which quietly
  // does nothing, is worse than either on its own. With one list the familiar
  // editor stays exactly where it was; the button below is what moves it.
  // Commands sent once the server has registered us and BEFORE we join.
  // The ordering is the whole point - see on_connect.py.
  function onConnectSectionHtml() {
    var data = state.onConnect || { commands: [], delay_seconds: 2 };

    var note = "";
    if (state.onConnectNote) {
      note = '<p class="served-folder-note ' +
        (state.onConnectNote.ok ? "is-ok" : "is-error") + '">' +
        escapeHtml(state.onConnectNote.text) +
        (state.onConnectNote.problems || []).map(function (line) {
          return "<br>\u2022 " + escapeHtml(line);
        }).join("") + "</p>";
    }

    // NO VALUE IN AN ATTRIBUTE. These lines hold an X password and
    // escapeHtml() leaves a double quote alone - the textarea's contents are
    // assigned as a property in attachOnConnectRows(), like every other value
    // on this page.
    return '<div class="served-folders">' +
      "<h3>On connect</h3>" +
      '<p class="served-folder-summary">' +
        "Sent once the server has registered you and <strong>before</strong> " +
        "joining - one command per line, exactly as you would type it into a " +
        "client. On Undernet that ordering matters: logging in to X takes " +
        "<code>+x</code>, and joining first shows your real host to everyone " +
        "already in the channel. Use <code>%nick%</code> for the nickname the " +
        "server actually gave you." +
      "</p>" +
      '<textarea class="on-connect-commands" rows="5" spellcheck="false" ' +
        'placeholder="PRIVMSG X@channels.undernet.org :LOGIN yourname yourpass' +
        '&#10;MODE %nick% +x" aria-label="Commands to send on connect"></textarea>' +
      '<div class="on-connect-delay">' +
        '<label for="on-connect-delay-input">Seconds between commands</label>' +
        '<input type="number" id="on-connect-delay-input" ' +
          'class="on-connect-delay-input" min="0" max="' +
          escapeHtml(String(data.max_delay_seconds || 60)) + '" step="1">' +
      "</div>" +
      '<div class="served-folder-actions">' +
        '<button type="button" class="btn btn-accent on-connect-save">' +
        "Save on-connect commands</button>" +
      "</div>" + note + "</div>";
  }

  function attachOnConnectRows() {
    var data = state.onConnect || { commands: [], delay_seconds: 2 };
    var box = el.settingsFields.querySelector(".on-connect-commands");
    var delay = el.settingsFields.querySelector(".on-connect-delay-input");
    if (!box || !delay) { return; }

    box.value = (data.commands || []).join("\n");
    delay.value = data.delay_seconds === undefined ? 2 : data.delay_seconds;
  }

  function loadOnConnect() {
    return fetchJson("/api/on-connect")
      .then(function (payload) {
        state.onConnect = payload;
        if (state.active === "settings" && state.settingsActiveCategory === "paths") {
          renderSettingsFields();
        }
      })
      .catch(function () { /* the settings page still works without it */ });
  }

  function saveOnConnect() {
    var box = el.settingsFields.querySelector(".on-connect-commands");
    var delay = el.settingsFields.querySelector(".on-connect-delay-input");
    if (!box || !delay) { return; }

    return postJson("/api/on-connect", {
      commands: box.value,
      delay_seconds: Number(delay.value)
    }).then(function (res) {
      if (res.ok) {
        state.onConnect = {
          commands: res.data.commands || [],
          delay_seconds: res.data.delay_seconds,
          max_delay_seconds: (state.onConnect || {}).max_delay_seconds
        };
        state.onConnectNote = { ok: true, text: res.data.message || "Saved." };
      } else {
        // Every fault at once, newline separated, the same way the folder and
        // list endpoints answer.
        var message = (res.data && res.data.error) || "Could not save.";
        var parts = message.split("\n");
        state.onConnectNote = {
          ok: false,
          text: parts.length > 1 ? "Could not save:" : message,
          problems: parts.length > 1 ? parts : []
        };
      }
      renderSettingsFields();
    });
  }

  function listsSectionHtml() {
    var draft = state.listsDraft || [];

    var blocks = draft.map(function (entry, index) {
      var folderRows = (entry.folders || []).map(function (_f, fIndex) {
        return '<div class="list-folder-row" data-list-index="' + index +
          '" data-folder-index="' + fIndex + '">' +
          '<input type="text" class="list-folder-name" placeholder="Label" aria-label="Folder label">' +
          '<input type="text" class="list-folder-path" placeholder="Full path to the folder" aria-label="Folder path">' +
          '<button type="button" class="served-folder-btn list-folder-remove" title="Remove folder" aria-label="Remove folder">\u00d7</button>' +
          "</div>";
      }).join("");

      return '<div class="served-list-block" data-list-index="' + index + '">' +
        '<div class="served-list-head">' +
          '<input type="text" class="served-list-name" placeholder="List name" aria-label="List name">' +
          '<label class="served-list-primary-label">' +
            '<input type="radio" name="served-list-primary" class="served-list-primary">' +
            " Primary</label>" +
          '<button type="button" class="served-folder-btn served-list-remove" title="Remove list" aria-label="Remove list">\u00d7</button>' +
        "</div>" +
        '<input type="text" class="served-list-channels" placeholder="#channel, #other (blank = everywhere)" aria-label="Channels this list serves">' +
        '<div class="served-list-folders">' + folderRows + "</div>" +
        '<button type="button" class="btn btn-small list-folder-add">Add folder</button>' +
        "</div>";
    }).join("");

    var note = "";
    if (state.listsNote) {
      note = '<p class="served-folder-note ' + (state.listsNote.ok ? "is-ok" : "is-error") + '">' +
        escapeHtml(state.listsNote.text) +
        (state.listsNote.problems || []).map(function (line) {
          return "<br>\u2022 " + escapeHtml(line);
        }).join("") + "</p>";
    }

    return '<div class="served-folders">' +
      "<h3>Served lists</h3>" +
      '<p class="served-folder-summary">' +
        "Each list is built from its own folders and answered in its own " +
        "channels. A channel named by no list is not served at all; a list " +
        "naming no channels answers everywhere, which only makes sense for " +
        "one of them. The primary is what a private message means." +
      "</p>" +
      blocks +
      '<div class="served-folder-actions">' +
        '<button type="button" class="btn served-list-add">Add list</button>' +
        '<button type="button" class="btn btn-accent served-list-save">Save lists</button>' +
      "</div>" + note + "</div>";
  }

  // Values as PROPERTIES, never concatenated into value="…": escapeHtml() is
  // textContent -> innerHTML and leaves a double quote alone, so a path
  // containing one would close the attribute. Same rule the folder rows
  // follow. Typing does not re-render, so the caret stays where it is.
  function attachListRows() {
    var draft = state.listsDraft || [];

    el.settingsFields.querySelectorAll(".served-list-block").forEach(function (block) {
      var index = parseInt(block.dataset.listIndex, 10);
      var entry = draft[index];
      if (!entry) { return; }

      var nameInput = block.querySelector(".served-list-name");
      var channelsInput = block.querySelector(".served-list-channels");
      var primaryInput = block.querySelector(".served-list-primary");

      nameInput.value = entry.name || "";
      channelsInput.value = (entry.channels || []).join(", ");
      primaryInput.checked = !!entry.primary;

      nameInput.addEventListener("input", function () {
        draft[index].name = nameInput.value;
      });
      channelsInput.addEventListener("input", function () {
        draft[index].channels = channelsInput.value.split(",")
          .map(function (part) { return part.trim(); })
          .filter(function (part) { return part.length > 0; });
      });
      primaryInput.addEventListener("change", function () {
        // Exactly one, enforced here as well as on the server: a radio group
        // already allows only one, but the draft is what gets sent.
        draft.forEach(function (other, otherIndex) {
          other.primary = otherIndex === index;
        });
      });
    });

    el.settingsFields.querySelectorAll(".list-folder-row").forEach(function (row) {
      var listIndex = parseInt(row.dataset.listIndex, 10);
      var folderIndex = parseInt(row.dataset.folderIndex, 10);
      var entry = draft[listIndex];
      if (!entry || !entry.folders || !entry.folders[folderIndex]) { return; }
      var folder = entry.folders[folderIndex];

      var nameInput = row.querySelector(".list-folder-name");
      var pathInput = row.querySelector(".list-folder-path");
      nameInput.value = folder.name || "";
      pathInput.value = folder.path || "";
      nameInput.addEventListener("input", function () {
        folder.name = nameInput.value;
      });
      pathInput.addEventListener("input", function () {
        folder.path = pathInput.value;
      });
    });
  }

  function handleListButton(button) {
    var draft = state.listsDraft || (state.listsDraft = []);

    if (button.classList.contains("served-list-start")) {
      // Seeded from what is already served, so "serve more than one list"
      // does not begin by throwing away the folders already configured. It
      // is not saved until the operator saves it.
      state.listsDraft = [{
        name: "Main",
        primary: true,
        channels: [],
        folders: (state.foldersDraft || state.folders || []).map(function (f) {
          return { name: f.name, path: f.path };
        })
      }];
      state.listsSource = "file";
      state.listsNote = {
        ok: true,
        text: "Add a second list, name the channels each one serves, then " +
              "save. Nothing changes until you do."
      };
      renderSettingsCategory();
      return;
    }

    if (button.classList.contains("served-list-save")) {
      saveLists();
      return;
    }

    if (button.classList.contains("served-list-add")) {
      draft.push({ name: "", primary: draft.length === 0, channels: [], folders: [] });
    } else if (button.classList.contains("served-list-remove")) {
      var block = button.closest(".served-list-block");
      var removeIndex = parseInt(block.dataset.listIndex, 10);
      var wasPrimary = draft[removeIndex] && draft[removeIndex].primary;
      draft.splice(removeIndex, 1);
      // Removing the primary must leave one behind, or the save is refused
      // for a reason the operator did not choose.
      if (wasPrimary && draft.length) { draft[0].primary = true; }
    } else if (button.classList.contains("list-folder-add")) {
      var addBlock = button.closest(".served-list-block");
      var addIndex = parseInt(addBlock.dataset.listIndex, 10);
      if (draft[addIndex]) {
        draft[addIndex].folders = draft[addIndex].folders || [];
        draft[addIndex].folders.push({ name: "", path: "" });
      }
    } else if (button.classList.contains("list-folder-remove")) {
      var row = button.closest(".list-folder-row");
      var listIndex = parseInt(row.dataset.listIndex, 10);
      var folderIndex = parseInt(row.dataset.folderIndex, 10);
      if (draft[listIndex] && draft[listIndex].folders) {
        draft[listIndex].folders.splice(folderIndex, 1);
      }
    }
    state.listsNote = null;
    renderSettingsCategory();
  }

  function loadLists() {
    return fetchJson("/api/lists")
      .then(function (payload) {
        state.lists = payload.lists || [];
        state.listsSource = payload.source || "";
        // Only seeded from the server when there is no edit in progress -
        // a poll landing mid-edit must not throw away what was typed.
        if (state.listsDraft === null) {
          state.listsDraft = state.lists.map(function (entry) {
            return {
              name: entry.name,
              primary: !!entry.primary,
              channels: (entry.channels || []).slice(),
              folders: (entry.folders || []).map(function (f) {
                return { name: f.name, path: f.path };
              })
            };
          });
        }
        if (state.active === "settings" && state.settingsActiveCategory === "paths") {
          renderSettingsFields();
        }
      })
      .catch(function () { /* the settings page still works without it */ });
  }

  function saveLists() {
    var draft = state.listsDraft || [];
    return postJson("/api/lists", { lists: draft })
      .then(function (res) {
        if (res.ok) {
          state.listsNote = {
            ok: true,
            text: res.data.message || "Saved."
          };
          // Dropped so the reload seeds it from what the server actually
          // wrote, rather than from what was typed - a name the server
          // trimmed would otherwise stay untrimmed on screen.
          state.listsDraft = null;
          state.lists = res.data.lists || [];
          return loadLists();
        }
        // The server returns every fault at once, newline separated, so an
        // operator fixing three things is told about three things.
        var message = (res.data && res.data.error) || "Could not save.";
        var parts = message.split("\n");
        state.listsNote = {
          ok: false,
          text: parts.length > 1 ? "Could not save:" : message,
          problems: parts.length > 1 ? parts : []
        };
        renderSettingsFields();
      });
  }

  function foldersSectionHtml() {
    var draft = state.foldersDraft || [];
    var rows = draft.map(function (_row, index) {
      return '<div class="served-folder-row" data-served-folder-index="' + index + '">' +
        '<input type="text" class="served-folder-name" placeholder="Label" aria-label="Folder label">' +
        '<input type="text" class="served-folder-path" placeholder="Full path to the folder" aria-label="Folder path">' +
        (state.foldersBrowserEnabled
          ? '<button type="button" class="served-folder-btn served-folder-browse" title="Browse for a folder">Browse</button>'
          : "") +
        '<button type="button" class="served-folder-btn served-folder-up" title="Move up" aria-label="Move up">\u2191</button>' +
        '<button type="button" class="served-folder-btn served-folder-down" title="Move down" aria-label="Move down">\u2193</button>' +
        '<button type="button" class="served-folder-btn served-folder-remove" title="Remove" aria-label="Remove">\u00d7</button>' +
        "</div>";
    }).join("");

    var note = "";
    if (state.foldersNote) {
      note = '<p class="served-folder-note ' + (state.foldersNote.ok ? "is-ok" : "is-error") + '">' +
        escapeHtml(state.foldersNote.text) +
        (state.foldersNote.problems || []).map(function (line) {
          return "<br>\u2022 " + escapeHtml(line);
        }).join("") + "</p>";
    }

    var summary;
    if (state.foldersSource === "file") {
      summary = "Serving " + draft.length + " folder" + (draft.length === 1 ? "" : "s") +
        ", in this order. The label is what users see as the first part of every path.";
    } else if (state.foldersSource === "file_directory") {
      summary = "No folder list yet \u2014 serving the single Music directory below. " +
        "Add a folder here to serve more than one.";
    } else {
      summary = "Nothing is being served yet. Add a folder here, or set Music directory below.";
    }

    // NO PATH IN ANY ATTRIBUTE. An entry is addressed by its INDEX into
    // state.browse.entries, and the handler looks the path up from there.
    // escapeHtml() is textContent -> innerHTML and leaves a double quote
    // alone, and a directory name on Linux may contain one - so a path
    // concatenated into data-path="…" would break out of the attribute. Same
    // rule the folder rows and the file lists already follow.
    var browsePanel = "";
    if (state.browse) {
      var b = state.browse;
      var list;
      if (b.error) {
        list = '<p class="served-folder-note is-error">' + escapeHtml(b.error) + "</p>";
      } else if (!b.entries.length) {
        list = '<p class="browse-empty">No folders in here.</p>';
      } else {
        list = '<ul class="browse-list">' + b.entries.map(function (entry, index) {
          return '<li><button type="button" class="browse-entry" data-browse-index="' +
            index + '">' + escapeHtml(entry.name) + "</button></li>";
        }).join("") + "</ul>";
      }

      browsePanel = '<div class="browse-panel">' +
        '<div class="browse-head">' +
          '<span class="browse-where">' +
            escapeHtml(b.at_root ? "This machine" : b.path) + "</span>" +
          '<button type="button" class="btn btn-small browse-close">Close</button>' +
        "</div>" +
        (b.at_root ? "" :
          '<button type="button" class="btn btn-small browse-up">\u2191 Up</button>') +
        list +
        (b.truncated
          ? '<p class="browse-empty">Only the first ' + b.entries.length +
            " folders are shown.</p>"
          : "") +
        (b.at_root ? "" :
          '<div class="browse-actions">' +
            '<button type="button" class="btn btn-accent browse-use">Use this folder</button>' +
          "</div>") +
        "</div>";
    }

    var offNote = "";
    if (!state.foldersBrowserEnabled) {
      offNote = '<p class="served-folder-summary">Type the full path to each folder. ' +
        "A folder picker is available if you turn on \u201cFolder picker on the " +
        "Settings page\u201d under Web dashboard.</p>";
    }

    return '<div class="served-folder-section">' +
      '<h2 class="settings-category-title">Served folders</h2>' +
      '<p class="served-folder-summary">' + escapeHtml(summary) + "</p>" +
      offNote +
      '<div class="served-served-folder-rows">' + rows + "</div>" +
      browsePanel +
      '<div class="served-folder-actions">' +
        '<button type="button" class="btn served-folder-add">Add folder</button>' +
        '<button type="button" class="btn btn-accent served-folder-save">Save folders</button>' +
      "</div>" + note + "</div>";
  }

  // Assigns every row's values as properties and keeps the draft in step with
  // typing. Typing deliberately does NOT re-render: rebuilding the rows on
  // each keystroke would take the caret with it.
  function attachFolderRows() {
    var draft = state.foldersDraft || [];
    el.settingsFields.querySelectorAll(".served-folder-row").forEach(function (row) {
      var index = parseInt(row.dataset.servedFolderIndex, 10);
      var entry = draft[index] || { name: "", path: "" };
      var nameInput = row.querySelector(".served-folder-name");
      var pathInput = row.querySelector(".served-folder-path");
      nameInput.value = entry.name || "";
      pathInput.value = entry.path || "";
      nameInput.addEventListener("input", function () {
        draft[index].name = nameInput.value;
      });
      pathInput.addEventListener("input", function () {
        draft[index].path = pathInput.value;
      });
    });
  }

  function loadFolders() {
    return fetchJson("/api/folders")
      .then(function (payload) {
        state.folders = payload.folders || [];
        state.foldersSource = payload.source || "";
        state.foldersBrowserEnabled = !!payload.browser_enabled;
        // Only seeded from the server when there is no edit in progress.
        if (state.foldersDraft === null) {
          state.foldersDraft = state.folders.map(function (f) {
            return { name: f.name, path: f.path };
          });
        }
        if (state.active === "settings" && state.settingsActiveCategory === "paths") {
          renderSettingsCategory();
        }
      })
      .catch(function () { /* the settings page still works without it */ });
  }

  // Addressed by the ROW OBJECT, not its index.
  //
  // The picker is a panel that stays open while the rows behind it can still
  // be added to, removed and reordered - every one of those renumbers the
  // draft. An index captured when the panel opened therefore points at
  // whichever row happens to sit there by the time "Use this folder" is
  // pressed, and the chosen path lands in the wrong one. Found by audit.
  //
  // The draft entries are objects, so holding the reference survives any
  // amount of reordering. The indexOf() check before writing covers the one
  // case a reference cannot survive: the row having been removed.
  function openBrowse(row, path) {
    fetchJsonAllowingError("/api/folders/browse?path=" + encodeURIComponent(path || ""))
      .then(function (res) {
        var payload = res.data || {};
        state.browse = {
          row: row,
          path: payload.path || "",
          parent: payload.parent,
          at_root: !!payload.at_root,
          entries: payload.entries || [],
          truncated: !!payload.truncated,
          error: payload.error || null
        };
        renderSettingsCategory();
      })
      .catch(function (err) {
        state.browse = { row: row, path: path || "", parent: "",
                         at_root: false, entries: [], truncated: false,
                         error: err.message };
        renderSettingsCategory();
      });
  }

  function saveFolders() {
    var rows = (state.foldersDraft || []).filter(function (entry) {
      return String(entry.path || "").trim() !== "";
    });
    postJson("/api/folders", { folders: rows }).then(function (res) {
      if (res.ok) {
        state.folders = res.data.folders || [];
        state.foldersSource = res.data.source || "";
        state.foldersDraft = state.folders.map(function (f) {
          return { name: f.name, path: f.path };
        });
        state.foldersNote = {
          ok: true,
          text: res.data.written
            ? "Saved " + res.data.written + " folder" +
              (res.data.written === 1 ? "" : "s") +
              ". Rebuild the list from Tools before they appear in it."
            : "Folder list cleared \u2014 back to the single Music directory."
        };
      } else {
        state.foldersNote = {
          ok: false,
          text: (res.data && res.data.error) || "Could not save the folders.",
          problems: (res.data && res.data.problems) || []
        };
      }
      renderSettingsCategory();
    });
  }

  function renderSettingsCategory() {
    var category = (state.settingsCategories || []).filter(function (c) {
      return c.id === state.settingsActiveCategory;
    })[0];

    if (!category) {
      el.settingsFields.innerHTML = '<p class="tool-status">No settings to show.</p>';
      return;
    }

    var html = '<h2 class="settings-category-title">' + escapeHtml(category.label) + "</h2>" +
      category.fields.map(settingsFieldHtml).join("");
    if (category.id === "admin-console") {
      html += settingsPasswordSectionHtml();
    }
    if (category.id === "paths") {
      // ABOVE the fields, because the served library is what an operator comes
      // to this category for and Music directory is now only the fallback
      // used when nothing else is configured.
      //
      // ONE editor, not two. With more than one list configured the folders
      // live inside the lists, so showing the single-list folder editor as
      // well would be a second place to edit folders that quietly does
      // nothing. The button below is what moves an operator between them.
      if (state.listsSource === "file") {
        html = listsSectionHtml() + html;
      } else {
        html = foldersSectionHtml() +
          '<div class="served-folder-actions">' +
            '<button type="button" class="btn served-list-start">' +
            "Serve more than one list\u2026</button>" +
          "</div>" + html;
      }
    }
    if (category.id === "paths") {
      html += onConnectSectionHtml();
    }
    el.settingsFields.innerHTML = html;
    if (category.id === "paths") {
      if (state.listsSource === "file") { attachListRows(); } else { attachFolderRows(); }
      attachOnConnectRows();
    }

    // Values are assigned as PROPERTIES here, not concatenated into value="…"
    // in the markup above. escapeHtml() is textContent -> innerHTML, which
    // encodes & < > and leaves a double quote alone - so a value containing
    // one closed the attribute early and everything after it became markup.
    // That is BUG 2 exactly, the shape four other render functions in this
    // file carry long comments about avoiding, and the same fix they use:
    // attachFilelistsCheckboxData() assigns via .dataset for the same reason.
    //
    // Self-XSS only today - every settings writer is an authenticated
    // operator and no IRC input reaches these fields - which is an argument
    // about blast radius, not about the code being right.
    var byName = {};
    category.fields.forEach(function (field) { byName[field.name] = field; });

    el.settingsFields.querySelectorAll("[data-setting]").forEach(function (input) {
      var field = byName[input.dataset.setting];
      if (field && input.type !== "checkbox") {
        var dirty = Object.prototype.hasOwnProperty.call(state.settingsDirty, field.name);
        input.value = dirty ? state.settingsDirty[field.name]
                            : settingsValueToString(field.value);
      }
      // "input" for anything typed into, so the save bar tracks a keystroke at
      // a time; "change" for the controls that have no intermediate state.
      var discrete = input.type === "checkbox" || input.tagName === "SELECT";
      input.addEventListener(discrete ? "change" : "input", onSettingsFieldChange);
    });
  }

  function renderSettingsRail() {
    var categories = state.settingsCategories || [];
    if (!categories.length) {
      el.settingsRail.innerHTML = '<div class="settings-rail-empty">No settings available.</div>';
      return;
    }
    el.settingsRail.innerHTML = categories.map(function (category) {
      var active = category.id === state.settingsActiveCategory;
      return '<button type="button" class="settings-rail-item' + (active ? " is-active" : "") +
        '" data-category="' + escapeHtml(category.id) + '">' + escapeHtml(category.label) + "</button>";
    }).join("");

    el.settingsRail.querySelectorAll("[data-category]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        state.settingsActiveCategory = btn.dataset.category;
        renderSettingsRail();
        renderSettingsCategory();
      });
    });
  }

  function showSettingsStatus(el_, message, isError) {
    el_.textContent = message;
    el_.className = "settings-note " + (isError ? "is-error" : "is-success");
    el_.style.display = "block";
  }

  // `preserveDirty` is true on the reload that follows a successful save:
  // the just-written values become the new baseline, but any OTHER field the
  // operator was mid-edit on (a save sends only the dirty set, not the whole
  // form) must not be reloaded out from under them.
  function loadSettings(preserveDirty) {
    if (!preserveDirty) {
      el.settingsFields.innerHTML = '<p class="tool-status">Loading…</p>';
    }
    fetchJson("/api/settings")
      .then(function (payload) {
        markConnection(true);
        state.settingsLoaded = true;
        state.settingsCategories = payload.categories || [];
        state.settingsAdminPasswordSet = !!payload.admin_password_set;
        loadFolders();
        loadLists();
        loadOnConnect();

        var baseline = {};
        state.settingsCategories.forEach(function (category) {
          category.fields.forEach(function (field) { baseline[field.name] = field.value; });
        });
        state.settingsBaseline = baseline;
        if (!preserveDirty) { state.settingsDirty = {}; }

        var stillValid = state.settingsCategories.some(function (c) {
          return c.id === state.settingsActiveCategory;
        });
        if (!stillValid) {
          state.settingsActiveCategory = state.settingsCategories.length
            ? state.settingsCategories[0].id : null;
        }

        renderSettingsRail();
        renderSettingsCategory();
        updateSettingsSaveBar();
      })
      .catch(function (err) {
        markConnection(false);
        el.settingsFields.innerHTML =
          '<p class="tool-status is-error">Could not load settings: ' + escapeHtml(err.message) + "</p>";
      });
  }

  // Delegated on the static container, once - see settingsPasswordSectionHtml()'s
  // comment for why (the toggle/form/note only exist after the admin-console
  // category has actually been rendered at least once).
  // Delegated for the same reason as the password toggle below: the folder
  // rows only exist after the paths category has been rendered, and they are
  // rebuilt from scratch every time it is.
  el.settingsFields.addEventListener("click", function (evt) {
    var browseButton = evt.target.closest(
      ".served-folder-browse, .browse-entry, .browse-up, .browse-use, .browse-close");
    if (browseButton) {
      var open = state.browse;
      if (browseButton.classList.contains("served-folder-browse")) {
        var rowEl = browseButton.closest(".served-folder-row");
        var index = parseInt(rowEl.dataset.servedFolderIndex, 10);
        var entryRow = state.foldersDraft[index];
        if (entryRow) { openBrowse(entryRow, entryRow.path || ""); }
      } else if (browseButton.classList.contains("browse-close")) {
        state.browse = null;
        renderSettingsCategory();
      } else if (browseButton.classList.contains("browse-up")) {
        openBrowse(open.row, open.parent || "");
      } else if (browseButton.classList.contains("browse-entry")) {
        // By index, never by a path read out of an attribute.
        var entry = open.entries[parseInt(browseButton.dataset.browseIndex, 10)];
        if (entry) { openBrowse(open.row, entry.path); }
      } else if (browseButton.classList.contains("browse-use")) {
        var target = open.row;
        // Still in the draft? The row can have been removed while the panel
        // was open, and writing into an orphaned object would look like it
        // worked while changing nothing on screen.
        if (target && state.foldersDraft.indexOf(target) !== -1) {
          target.path = open.path;
          if (!target.name) {
            // The label the server would derive anyway, shown now so the
            // operator can change it before saving rather than after.
            var parts = open.path.replace(/[\\/]+$/, "").split(/[\\/]/);
            target.name = parts[parts.length - 1] || open.path;
          }
        }
        state.browse = null;
        state.foldersNote = null;
        renderSettingsCategory();
      }
      return;
    }

    // #26's own buttons first: they share the settings pane with the folder
    // ones and a shared handler would have to tell them apart anyway.
    if (evt.target.closest(".on-connect-save")) {
      saveOnConnect();
      return;
    }

    var listButton = evt.target.closest(
      ".served-list-add, .served-list-save, .served-list-remove, " +
      ".list-folder-add, .list-folder-remove, .served-list-start");
    if (listButton) {
      handleListButton(listButton);
      return;
    }

    var button = evt.target.closest(".served-folder-add, .served-folder-save, .served-folder-remove, .served-folder-up, .served-folder-down");
    if (!button) { return; }
    var draft = state.foldersDraft || (state.foldersDraft = []);

    if (button.classList.contains("served-folder-add")) {
      draft.push({ name: "", path: "" });
    } else if (button.classList.contains("served-folder-save")) {
      saveFolders();
      return;
    } else {
      var row = button.closest(".served-folder-row");
      var index = parseInt(row.dataset.servedFolderIndex, 10);
      if (button.classList.contains("served-folder-remove")) {
        draft.splice(index, 1);
      } else if (button.classList.contains("served-folder-up") && index > 0) {
        draft.splice(index - 1, 0, draft.splice(index, 1)[0]);
      } else if (button.classList.contains("served-folder-down") && index < draft.length - 1) {
        draft.splice(index + 1, 0, draft.splice(index, 1)[0]);
      }
    }
    state.foldersNote = null;
    renderSettingsCategory();
  });

  el.settingsFields.addEventListener("click", function (evt) {
    var toggle = evt.target.closest(".settings-password-toggle");
    if (!toggle) { return; }
    var form = el.settingsFields.querySelector(".settings-password-form");
    if (form) { form.style.display = (form.style.display === "none") ? "flex" : "none"; }
  });

  el.settingsFields.addEventListener("submit", function (evt) {
    var form = evt.target.closest(".settings-password-form");
    if (!form) { return; }
    evt.preventDefault();

    var newPasswordInput = form.querySelector(".settings-new-password");
    var confirmPasswordInput = form.querySelector(".settings-confirm-password");
    var note = el.settingsFields.querySelector(".settings-password-note");
    var newPassword = newPasswordInput.value;
    var confirmPassword = confirmPasswordInput.value;

    postJson("/api/settings/password", {
      new_password: newPassword, confirm_password: confirmPassword
    }).then(function (res) {
      // Cleared unconditionally, success or not - a plaintext password must
      // never sit in the DOM a moment longer than it has to.
      newPasswordInput.value = "";
      confirmPasswordInput.value = "";
      note.style.display = "block";
      if (res.ok) {
        note.textContent = "Password changed. Rehashing…";
        note.className = "settings-note settings-password-note is-success";
        state.settingsAdminPasswordSet = true;
        form.style.display = "none";
        loadSettings(true);
      } else {
        note.textContent = (res.data && res.data.error) || "Could not change the password.";
        note.className = "settings-note settings-password-note is-error";
      }
    }).catch(function () {
      newPasswordInput.value = "";
      confirmPasswordInput.value = "";
      note.style.display = "block";
      note.textContent = "Could not reach the dashboard.";
      note.className = "settings-note settings-password-note is-error";
    });
  });

  el.settingsSaveBtn.addEventListener("click", function () {
    var dirty = state.settingsDirty;
    if (!Object.keys(dirty).length) { return; }

    el.settingsSaveBtn.disabled = true;
    el.settingsSaveStatus.style.display = "none";
    el.settingsRestartNote.style.display = "none";
    el.settingsSavebarText.textContent = "Saving…";

    postJson("/api/settings", dirty)
      .then(function (res) {
        if (res.ok) {
          state.settingsDirty = {};
          showSettingsStatus(el.settingsSaveStatus,
            "Saved " + Object.keys(dirty).length +
            (Object.keys(dirty).length === 1 ? " setting" : " settings") + ". Rehash started.", false);
          if (res.data.restart_required && res.data.restart_required.length) {
            showSettingsStatus(el.settingsRestartNote,
              "Restart required to apply: " + res.data.restart_required.join(", ") + ".", true);
          }
          loadSettings(true);
        } else {
          showSettingsStatus(el.settingsSaveStatus,
            (res.data && res.data.error) || "Could not save settings.", true);
          updateSettingsSaveBar();
        }
      })
      .catch(function () {
        showSettingsStatus(el.settingsSaveStatus, "Could not reach the dashboard.", true);
        updateSettingsSaveBar();
      });
  });

  // ------------------------------------------------------------------ Init

  // The sidebar status card is useful on every view, not only Queue, so it
  // refreshes independently of which view is active.
  loadQueue();
  setInterval(function () {
    // Keep the sidebar status fresh always; refresh the visible table only
    // when it is the one showing, so a search result is never clobbered by a
    // background poll.
    fetchJson("/api/queue").then(function (rows) {
      markConnection(true);
      renderSidebarStatus(rows);
      // The queue table lives on Stats now (#133). Same rule as before -
      // refresh the visible table only when it is the one showing, so a
      // background poll never clobbers what the operator is reading.
      if (state.active === "stats") {
        renderQueueStats(rows);
        renderQueueTable(rows);
      }
    }).catch(function () { markConnection(false); });
  }, REFRESH_MS);

  // Downloads can complete while the operator is looking at a different
  // view, so this polls independently of which tab is active - same
  // reasoning as the sidebar status card above.
  setInterval(loadDownloads, DOWNLOADS_POLL_MS);

  // Only while Stats is the view on screen. Speed now and the queue counters
  // move second to second; the rest of the page does not, and polling a view
  // nobody is looking at is the 401 storm in miniature.
  setInterval(function () {
    if (state.active === "stats") { loadStats(); }
  }, REFRESH_MS);

  // A list-fetch (Download tab, or the File Lists fetch box) can complete
  // while the operator is on any other view - keep the switcher's options
  // fresh regardless of which tab is showing, same reasoning as above.
  setInterval(pollFilelistsBots, FILELISTS_BOTS_POLL_MS);

  // Runs continuously regardless of which view is active, the same as
  // loadDownloads above: the buffer this polls (webserver._console_log) is
  // bounded server-side either way, and a console that is already caught up
  // when the operator switches to it is worth more than the handful of
  // requests saved by only polling while the tab is visible.
  pollConsoleLog();
  consoleLogTimer = setInterval(pollConsoleLog, CONSOLE_LOG_POLL_MS);
  pollFilelistsBots();

  // ---------------------------------------------------------------- Stats

  function setStat(node, value) {
    if (node) { node.textContent = value; }
  }

  function renderTopTable(node, rows, emptyText) {
    if (!node) { return; }
    if (!rows || !rows.length) {
      node.innerHTML = emptyRow(2, emptyText);
      return;
    }
    node.innerHTML = rows.map(function (row) {
      return "<tr><td>" + escapeHtml(row.name) +
             "</td><td class=\"col-num\">" + escapeHtml(String(row.count)) + "</td></tr>";
    }).join("");
  }

  function renderTopDownloads(top) {
    top = top || {};
    renderTopTable(el.stTopFiles, top.files, "Nothing sent yet.");

    // With folder packing off no album can ever be sent, so an empty table
    // would sit there for ever explaining nothing. Counts from before it was
    // switched off are still shown - they are history.
    var haveAlbums = top.albums && top.albums.length;
    var show = top.albums_enabled !== false || haveAlbums;
    if (el.stTopAlbumsWrap) { el.stTopAlbumsWrap.style.display = show ? "" : "none"; }
    if (el.stTopAlbumsLabel) { el.stTopAlbumsLabel.style.display = show ? "" : "none"; }
    if (el.stTopAlbumsOff) { el.stTopAlbumsOff.style.display = show ? "none" : ""; }
    if (show) {
      renderTopTable(el.stTopAlbums, top.albums, "No album sent yet.");
    }
  }

  function renderStats(data) {
    var t = data.transfer || {};
    var s = data.sent || {};
    var lib = data.library || {};

    // Every figure is rendered server-side by the same helpers the channel
    // advert and the admin console use, so the page cannot disagree with the
    // advert about how the same number reads. The raw values are in the
    // payload too, for anything that is not this page.
    setStat(el.stSpeed, t.speed_now_text || "0k/s");
    setStat(el.stRecord, t.record_text || "0k/s");
    setStat(el.stSending, (t.sending || 0) + " / " + (t.slots || 0));
    setStat(el.stQueued, (t.queued_files || 0).toLocaleString());
    setStat(el.stQueuedLabel,
            "Queued" + (t.queued_users ? " · " + t.queued_users + " user" +
                        (t.queued_users === 1 ? "" : "s") : ""));
    setStat(el.stUptime, t.uptime_text || "0 Min");

    setStat(el.stSentTotal, s.total_text || "0B");
    setStat(el.stSentToday, s.today_text || "0B");
    setStat(el.stSentYesterday, s.yesterday_text || "0B");
    setStat(el.stSentTotalFiles, "Total · " + (s.total_files || 0).toLocaleString() + " files");
    setStat(el.stSentTodayFiles, "Today · " + (s.today_files || 0).toLocaleString() + " files");
    setStat(el.stSentYesterdayFiles,
            "Yesterday · " + (s.yesterday_files || 0).toLocaleString() + " files");

    setStat(el.stFiles, (lib.files || 0).toLocaleString());
    setStat(el.stSize, lib.size || "0B");
    // null means "no RAR list has been built", which is not the same claim as
    // "this bot offers no albums" - so it shows as unknown rather than zero.
    setStat(el.stAlbums, lib.rar_folders === null || lib.rar_folders === undefined
            ? "—" : lib.rar_folders.toLocaleString());
    setStat(el.stBuilt, lib.list_date || "—");

    renderTopDownloads(data.top);
    setStat(el.stFoot, data.version || "");
  }

  function loadStats() {
    fetchJson("/api/stats").then(function (data) {
      markConnection(true);
      renderStats(data);
    }).catch(function () { markConnection(false); });
  }

  // -------------------------------------------------------------- Console
  //
  // Two sources feed the same on-screen log: the ambient debug stream,
  // polled from GET /api/console/log (matching webserver.py's own LOG/COMMAND
  // split - see build_console_log_payload()'s docstring), and a command's own
  // reply, appended straight from what POST /api/console/command returns
  // rather than waiting for the next poll. Both render through the same
  // appendConsoleLines(), so the transcript reads as one continuous console
  // regardless of which endpoint a given line actually came from.

  function consoleLineNode(text, category, timeSeconds, isLocal) {
    var row = document.createElement("div");
    row.className = "console-line" + (isLocal ? " is-local" : "");
    if (category) { row.setAttribute("data-category", category); }

    var stamp = document.createElement("span");
    stamp.className = "console-line-time";
    stamp.textContent = timeSeconds
      ? new Date(timeSeconds * 1000).toLocaleTimeString()
      : "";

    var body = document.createElement("span");
    body.className = "console-line-text";
    body.textContent = text;

    row.appendChild(stamp);
    row.appendChild(body);
    return row;
  }

  function appendConsoleLines(rows) {
    if (!rows.length) { return; }
    // Scrolled to (or near) the bottom already? Stay pinned there as new
    // lines arrive. Already scrolled up reading something older? Leave the
    // view alone rather than yanking it back down mid-read.
    var atBottom = el.consoleLog.scrollHeight - el.consoleLog.scrollTop
                   - el.consoleLog.clientHeight < 24;
    rows.forEach(function (row) { el.consoleLog.appendChild(row); });
    if (atBottom) { el.consoleLog.scrollTop = el.consoleLog.scrollHeight; }
  }

  // WEBUI_CONSOLE_ENABLED is off: the routes answer 404 and there is nothing
  // here to show. Take the page's Console away and stop asking.
  //
  // Handled as its own case rather than falling into the catch below, because
  // that one calls markConnection(false) - so a dashboard with the Console
  // switched off would have reported the whole daemon as unreachable, once
  // per poll, for ever. The bot is perfectly fine; one feature is not enabled.
  function disableConsoleUi() {
    if (consoleLogTimer !== null) {
      clearInterval(consoleLogTimer);
      consoleLogTimer = null;
    }
    // HIDDEN, not removed - and #view-console stays in the DOM.
    //
    // activateView() walks every key in `views` and calls
    // getElementById("view-" + key).classList on each, so deleting the console
    // section made that return null and throw. On EVERY view switch. The
    // Console went away and took the rest of the navigation with it: Settings
    // sat on its "Loading" placeholder for ever, because the exception fired
    // before the branch that calls loadSettings(), and Queue, Stats and
    // Downloads stopped refreshing for the same reason.
    //
    // Found on a real install running with the Console off - which is the
    // default, so this was the ordinary case and not a corner of it.
    var navButton = document.querySelector(".nav-item[data-view=\"console\"]");
    if (navButton) { navButton.hidden = true; }
    if (state.active === "console") { activateView("search"); }
  }

  function pollConsoleLog() {
    fetchJson("/api/console/log?since=" + state.consoleCursor).then(function (payload) {
      markConnection(true);
      state.consoleCursor = payload.cursor;
      appendConsoleLines((payload.lines || []).map(function (line) {
        return consoleLineNode(line.text, line.category, line.time, false);
      }));
    }).catch(function (err) {
      if (err && String(err.message) === "HTTP 404") {
        disableConsoleUi();
        return;
      }
      markConnection(false);
    });
  }

  el.consoleForm.addEventListener("submit", function (evt) {
    evt.preventDefault();
    var command = el.consoleInput.value.trim();
    if (!command) { return; }

    appendConsoleLines([consoleLineNode("> " + command, null, Date.now() / 1000, true)]);
    el.consoleInput.value = "";
    el.consoleInput.disabled = true;
    el.consoleRunBtn.disabled = true;

    postJson("/api/console/command", { command: command }).then(function (res) {
      // 200 carries {lines: [...]}; the one non-2xx case (an empty command,
      // rejected by build_console_command_result() before it ever reaches
      // adminchat.handle_command()) carries {error: "..."} instead.
      var lines = (res.data && res.data.lines) ||
                  (res.data && res.data.error ? [res.data.error] : []);
      appendConsoleLines(lines.map(function (text) {
        return consoleLineNode(text, null, Date.now() / 1000, true);
      }));
    }).catch(function (err) {
      appendConsoleLines([consoleLineNode(
        "Request failed: " + err.message, null, Date.now() / 1000, true)]);
    }).then(function () {
      el.consoleInput.disabled = false;
      el.consoleRunBtn.disabled = false;
      el.consoleInput.focus();
    });
  });

  // ---------------------------------------------------------------- Theme
  //
  // The stored choice is already on the root element by the time this runs -
  // index.html sets it in the head, before the first paint, so the page never
  // renders in one theme and swaps to the other. All this does is keep the two
  // buttons in step with it and write the choice down.
  //
  // Per browser, not per bot: which theme suits a screen is a fact about the
  // person looking at it, and the same daemon gets looked at from a phone in a
  // dark room and a desktop by a window. Nothing about it goes to the server.

  var THEME_KEY = "dccore-theme";

  function storedTheme() {
    // Browsers set to block site data throw on access rather than returning
    // null, so this cannot be an unguarded read.
    try {
      var saved = localStorage.getItem(THEME_KEY);
      return (saved === "dark" || saved === "light") ? saved : null;
    } catch (err) {
      return null;
    }
  }

  function currentTheme() {
    // No stored choice means no attribute, and the operating system decides -
    // so ask it the same question the stylesheet's media query asks, rather
    // than assuming dark and lighting up the wrong button.
    var chosen = document.documentElement.getAttribute("data-theme");
    if (chosen === "dark" || chosen === "light") {
      return chosen;
    }
    return (window.matchMedia &&
            window.matchMedia("(prefers-color-scheme: light)").matches)
      ? "light" : "dark";
  }

  function markThemeButtons() {
    var active = currentTheme();
    [el.themeDark, el.themeLight].forEach(function (button) {
      if (!button) {
        return;
      }
      var mine = button.getAttribute("data-theme-choice") === active;
      button.classList.toggle("is-active", mine);
      button.setAttribute("aria-pressed", mine ? "true" : "false");
    });
  }

  function chooseTheme(name) {
    document.documentElement.setAttribute("data-theme", name);
    try {
      localStorage.setItem(THEME_KEY, name);
    } catch (err) {
      // A private window cannot remember it. The page still changes now, which
      // is what was asked for; it simply starts over on the next load.
    }
    markThemeButtons();
  }

  [el.themeDark, el.themeLight].forEach(function (button) {
    if (button) {
      button.addEventListener("click", function () {
        chooseTheme(button.getAttribute("data-theme-choice"));
      });
    }
  });

  // Somebody who has never picked follows their machine, so follow it when it
  // changes too - a laptop switching to dark at sunset should take the
  // dashboard with it. An explicit choice is left alone.
  if (window.matchMedia) {
    var watcher = window.matchMedia("(prefers-color-scheme: light)");
    var onSystemChange = function () {
      if (!storedTheme()) {
        markThemeButtons();
      }
    };
    if (watcher.addEventListener) {
      watcher.addEventListener("change", onSystemChange);
    } else if (watcher.addListener) {
      watcher.addListener(onSystemChange);      // Safari before 14
    }
  }

  markThemeButtons();

  activateView("search");
})();
