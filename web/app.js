/* DCCore Dashboard - vanilla JS, no build step, no framework.
 *
 * Talks to the three original read-only endpoints (/api/search, /api/queue,
 * /api/filelists) plus the cross-bot search/fetch endpoints added alongside
 * them: POST /api/search/broadcast, GET /api/search/broadcast/status,
 * POST /api/fetch/enqueue, GET /api/fetch/status, GET /api/fetch/<id>/download.
 * See webserver.py - NONE of these routes require authentication, including
 * the mutating ones this file calls.
 */
(function () {
  "use strict";

  var REFRESH_MS = 8000;
  var BROADCAST_POLL_MS = 2000;
  var DOWNLOADS_POLL_MS = 4000;

  var views = {
    search:    { title: "Search",     sub: "Find a file across the current master list." },
    queue:     { title: "Queue",      sub: "Who is waiting, who is sending, right now." },
    filelists: { title: "File Lists", sub: "Every file this bot is currently offering." }
  };

  var state = { active: "search", filelistsLoaded: false };

  var el = {
    navItems:     document.querySelectorAll(".nav-item"),
    pageTitle:    document.getElementById("page-title"),
    pageSub:      document.getElementById("page-sub"),
    searchForm:   document.getElementById("search-form"),
    searchInput:  document.getElementById("search-input"),
    searchBody:   document.getElementById("search-body"),
    queueBody:    document.getElementById("queue-body"),
    filelistsBody:document.getElementById("filelists-body"),
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
    downloadsBody:       document.getElementById("downloads-body")
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
      if (!res.ok) { throw new Error("HTTP " + res.status); }
      return res.json();
    });
  }

  // postJson deliberately does NOT throw on a non-2xx response - the mutating
  // routes (broadcast/enqueue) return a meaningful JSON error body (409
  // already-in-progress, 429 cooldown, 503 IRC down, 400 bad input) that the
  // caller wants to show the operator, not treat as a network failure.
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
    Object.keys(views).forEach(function (key) {
      document.getElementById("view-" + key).classList.toggle("is-active", key === name);
    });
    el.navItems.forEach(function (btn) {
      btn.classList.toggle("is-active", btn.dataset.view === name);
    });
    el.pageTitle.textContent = views[name].title;
    el.pageSub.textContent = views[name].sub;

    if (name === "queue") { loadQueue(); loadDownloads(); }
    if (name === "filelists" && !state.filelistsLoaded) { loadFilelists(); }
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
  function renderBroadcastResults(results) {
    el.broadcastBody.innerHTML = "";
    if (!results.length) {
      el.broadcastBody.innerHTML = emptyRow(3, "No replies yet.");
      updateDownloadSelectedState();
      return;
    }
    results.forEach(function (entry) {
      var tr = document.createElement("tr");

      var checkTd = document.createElement("td");
      checkTd.className = "col-check";
      if (entry.bot && entry.filename) {
        var box = document.createElement("input");
        box.type = "checkbox";
        box.className = "broadcast-check";
        box.dataset.bot = entry.bot;
        box.dataset.filename = entry.filename;
        box.addEventListener("change", updateDownloadSelectedState);
        checkTd.appendChild(box);
      }
      tr.appendChild(checkTd);

      var fromTd = document.createElement("td");
      fromTd.className = "col-mono";
      fromTd.textContent = entry.from;
      tr.appendChild(fromTd);

      var textTd = document.createElement("td");
      textTd.className = "col-mono";
      textTd.textContent = entry.text;
      tr.appendChild(textTd);

      el.broadcastBody.appendChild(tr);
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

  // -------------------------------------------------------------- Downloads

  var DOWNLOAD_STATE_LABELS = {
    pending: "Pending", offered: "Offered", listening: "Listening",
    receiving: "Receiving", complete: "Complete", failed: "Failed"
  };

  function loadDownloads() {
    fetchJson("/api/fetch/status").then(function (rows) {
      markConnection(true);
      renderDownloads(rows);
    }).catch(function () { markConnection(false); });
  }

  function renderDownloads(rows) {
    if (!rows.length) {
      el.downloadsBody.innerHTML = emptyRow(5, "Nothing queued yet.");
      return;
    }
    el.downloadsBody.innerHTML = rows.map(function (row) {
      var state = row.state || "pending";
      var label = DOWNLOAD_STATE_LABELS[state] || state;
      var progress = row.total_size
        ? Math.round(100 * (row.bytes_received || 0) / row.total_size) + "%"
        : (row.bytes_received ? row.bytes_received + " B" : "—");
      var action = state === "complete"
        ? "<a class=\"btn btn-small\" href=\"/api/fetch/" + encodeURIComponent(row.id) + "/download\">Download</a>"
        : (state === "failed" ? "<span class=\"col-dim\">" + escapeHtml(row.reason || "") + "</span>" : "");
      return "<tr>" +
        "<td class=\"col-mono\">" + escapeHtml(row.bot) + "</td>" +
        "<td class=\"col-dim col-mono\">" + escapeHtml(row.filename) + "</td>" +
        "<td><span class=\"status-pill status-" + escapeHtml(state) + "\">" + escapeHtml(label) + "</span></td>" +
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

  function loadFilelists() {
    el.filelistsBody.innerHTML = emptyRow(4, "Loading…");
    fetchJson("/api/filelists")
      .then(function (rows) {
        markConnection(true);
        state.filelistsLoaded = true;
        if (!rows.length) {
          el.filelistsBody.innerHTML = emptyRow(4, "No files published yet.");
          return;
        }
        el.filelistsBody.innerHTML = rows.map(function (row) {
          return "<tr>" +
            "<td class=\"col-mono\">" + escapeHtml(row.title) + "</td>" +
            "<td class=\"col-mono\">" + escapeHtml(row.size) + "</td>" +
            "<td class=\"col-dim\">" + escapeHtml(row.format) + "</td>" +
            "<td class=\"col-dim col-mono\">" + escapeHtml(row.source) + "</td>" +
            "</tr>";
        }).join("");
      })
      .catch(function (err) {
        markConnection(false);
        el.filelistsBody.innerHTML = emptyRow(4, "Could not load file lists: " + err.message);
      });
  }

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
      if (state.active === "queue") {
        renderQueueStats(rows);
        renderQueueTable(rows);
      }
    }).catch(function () { markConnection(false); });
  }, REFRESH_MS);

  // Downloads can complete while the operator is looking at a different
  // view, so this polls independently of which tab is active - same
  // reasoning as the sidebar status card above.
  setInterval(loadDownloads, DOWNLOADS_POLL_MS);

  activateView("search");
})();
