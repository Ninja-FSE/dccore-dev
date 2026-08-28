/* DCCore Dashboard - vanilla JS, no build step, no framework.
 *
 * Talks to the three original read-only endpoints (/api/search, /api/queue,
 * /api/filelists) plus the cross-bot search/fetch endpoints added alongside
 * them: POST /api/search/broadcast, GET /api/search/broadcast/status,
 * POST /api/fetch/enqueue, GET /api/fetch/status, GET /api/fetch/<id>/download,
 * POST /api/filelists/fetch, GET /api/filelists/bots, GET /api/filelists/bot/<nick>.
 * See webserver.py - NONE of these routes require authentication, including
 * the mutating ones this file calls.
 */
(function () {
  "use strict";

  var REFRESH_MS = 8000;
  var BROADCAST_POLL_MS = 2000;
  var DOWNLOADS_POLL_MS = 4000;
  var FILELISTS_BOTS_POLL_MS = 4000;
  // Matches webserver.py's FILELISTS_DEFAULT_PAGE_SIZE - keep the two in
  // sync if either changes, so a page here always lines up with a page the
  // server actually hands back.
  var FILELISTS_PAGE_SIZE = 200;

  var views = {
    search:    { title: "Search",     sub: "Find a file across the current master list." },
    queue:     { title: "Queue",      sub: "Who is waiting, who is sending, right now." },
    download:  { title: "Download",   sub: "Bulk-paste \"!<bot> <filename>\" requests and track their progress." },
    filelists: { title: "File Lists", sub: "Every file this bot - or a fetched bot's list - is currently offering." }
  };

  var state = {
    active: "search", filelistsLoaded: false, filelistsSource: "__own__",
    filelistsOffset: 0, filelistsTotal: 0, filelistsReturned: 0,
    filelistsHistory: []
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
    filelistsSourceSelect: document.getElementById("filelists-source-select"),
    filelistsPrevBtn:     document.getElementById("filelists-prev-btn"),
    filelistsNextBtn:     document.getElementById("filelists-next-btn"),
    filelistsPageInfo:    document.getElementById("filelists-page-info"),
    filelistsExpandAll:   document.getElementById("filelists-expand-all"),
    filelistsCollapseAll: document.getElementById("filelists-collapse-all")
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

    if (name === "queue") { loadQueue(); }
    if (name === "download") { loadDownloads(); }
    if (name === "filelists") {
      pollFilelistsBots();
      if (!state.filelistsLoaded) { loadFilelists(); }
    }
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
      var action;
      if (rejected) {
        action = "<span class=\"col-dim\">" + escapeHtml(row.list_processing_error) + "</span>";
      } else if (state === "complete") {
        action = "<a class=\"btn btn-small\" href=\"/api/fetch/" + encodeURIComponent(row.id) + "/download\">Download</a>";
      } else if (state === "failed") {
        action = "<span class=\"col-dim\">" + escapeHtml(row.reason || "") + "</span>";
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

  el.filelistsSourceSelect.addEventListener("change", function () {
    state.filelistsSource = el.filelistsSourceSelect.value;
    state.filelistsOffset = 0;
    state.filelistsHistory = [];
    loadFilelists();
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
    }).catch(function () { markConnection(false); });
  }

  function renderFilelistsSwitcher(rows) {
    var select = el.filelistsSourceSelect;
    var previous = state.filelistsSource || "__own__";

    select.innerHTML = "";
    var ownOption = document.createElement("option");
    ownOption.value = "__own__";
    ownOption.textContent = "Our own list";
    select.appendChild(ownOption);

    rows.forEach(function (row) {
      var opt = document.createElement("option");
      opt.value = row.bot;
      opt.textContent = row.bot + " (" + row.count + " files)";
      select.appendChild(opt);
    });

    var stillAvailable = previous === "__own__" ||
      rows.some(function (row) { return row.bot === previous; });
    select.value = stillAvailable ? previous : "__own__";
    state.filelistsSource = select.value;
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
      return "<tr class=\"folder-row\">" +
        "<td colspan=\"4\">" +
          "<button type=\"button\" class=\"folder-toggle\" aria-expanded=\"false\"" +
                 " data-folder-index=\"" + index + "\">" +
            "<span class=\"folder-caret\" aria-hidden=\"true\"></span>" +
            "<span class=\"folder-name\">" +
              escapeHtml(folderLabel(group.folder)) + "</span>" +
            "<span class=\"folder-count\">" + count.toLocaleString() +
              (count === 1 ? " file" : " files") + "</span>" +
          "</button>" +
        "</td></tr>";
    }

    function folderFilesHtml(group, index) {
      var entries = group.entries || [];
      var rows = entries.map(function (row) {
        return "<tr class=\"file-row is-hidden\" data-folder-index=\"" + index + "\">" +
          "<td class=\"col-mono col-indent\">" + escapeHtml(row.title) + "</td>" +
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
          index + "\"><td colspan=\"4\">Showing the first " +
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
      if (!toggle) { return; }
      var expanded = toggle.getAttribute("aria-expanded") === "true";
      toggle.setAttribute("aria-expanded", expanded ? "false" : "true");
      setFolderExpanded(toggle.getAttribute("data-folder-index"), !expanded);
    });

    el.filelistsExpandAll.addEventListener("click", function () { setAllFolders(true); });
    el.filelistsCollapseAll.addEventListener("click", function () { setAllFolders(false); });

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

    function loadFilelists() {
      el.filelistsBody.innerHTML = emptyRow(4, "Loading…");
      var source = state.filelistsSource || "__own__";
      var offset = state.filelistsOffset || 0;
      var base = (source === "__own__")
        ? "/api/filelists"
        : "/api/filelists/bot/" + encodeURIComponent(source);
      var url = base + "?offset=" + offset + "&limit=" + FILELISTS_PAGE_SIZE;

      fetchJson(url)
        .then(function (payload) {
          markConnection(true);
          state.filelistsLoaded = true;
          var groups = folderGroupsFrom(payload);
          state.filelistsTotal = Array.isArray(payload)
            ? groups.length : (payload.total || 0);
          state.filelistsReturned = groups.length;
          renderFilelistsPager(Array.isArray(payload)
            ? payload.length : (payload.total_files || 0));
          if (!groups.length) {
            el.filelistsBody.innerHTML = emptyRow(4, "No files published yet.");
            return;
          }
          el.filelistsBody.innerHTML = groups.map(function (group, index) {
            return folderHeadingHtml(group, index) + folderFilesHtml(group, index);
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

  // A list-fetch (Download tab, or the File Lists fetch box) can complete
  // while the operator is on any other view - keep the switcher's options
  // fresh regardless of which tab is showing, same reasoning as above.
  setInterval(pollFilelistsBots, FILELISTS_BOTS_POLL_MS);
  pollFilelistsBots();

  activateView("search");
})();
