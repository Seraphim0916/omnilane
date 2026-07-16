"use strict";

(function () {
  const TOKEN_STORAGE_KEY = "omnilane.live-ui.token";
  const DETAIL_CACHE_LIMIT = 50, DETAIL_PREFETCH_LIMIT = 12, DETAIL_CONCURRENCY = 3;
  const OUTPUT_BOTTOM_THRESHOLD = 28;
  const MOBILE_QUERY = "(max-width: 760px)";
  const VALID_STATES = new Set(["starting", "running", "succeeded", "failed", "dead", "invalid"]);

  const elements = {
    connection: document.getElementById("connection-status"),
    connectionLabel: document.getElementById("connection-label"),
    jobCount: document.getElementById("job-count"), search: document.getElementById("job-search"),
    filter: document.getElementById("status-filter"),
    filterButtons: Array.from(document.querySelectorAll(".filter-button")),
    jobList: document.getElementById("job-list"), listMessage: document.getElementById("list-message"),
    inspector: document.querySelector(".job-inspector"), inspectorState: document.getElementById("inspector-state"),
    stateCode: document.getElementById("state-code"), stateTitle: document.getElementById("state-title"),
    stateMessage: document.getElementById("state-message"), mobileBack: document.getElementById("mobile-back"),
    jobDetail: document.getElementById("job-detail"), selectedJobId: document.getElementById("selected-job-id"),
    selectedJobState: document.getElementById("selected-job-state"), selectedJobTime: document.getElementById("selected-job-time"),
    routeTrack: document.getElementById("route-track"), routeLane: document.getElementById("route-lane"),
    routeVendor: document.getElementById("route-vendor"), routeModel: document.getElementById("route-model"),
    routeState: document.getElementById("route-state"), factEffort: document.getElementById("fact-effort"),
    factMode: document.getElementById("fact-mode"), factTimeout: document.getElementById("fact-timeout"),
    factCandidate: document.getElementById("fact-candidate"), factStarted: document.getElementById("fact-started"),
    factWorkdir: document.getElementById("fact-workdir"), requestMarkers: document.getElementById("request-markers"),
    requestEmpty: document.getElementById("request-empty"), requestContent: document.getElementById("request-content"),
    resultMarkers: document.getElementById("result-markers"), resultEmpty: document.getElementById("result-empty"),
    resultContent: document.getElementById("result-content"), compareToggle: document.getElementById("compare-toggle"),
    compareReferenceLabel: document.getElementById("compare-reference-label"), comparePanel: document.getElementById("compare-panel"),
    compareClear: document.getElementById("compare-clear"), compareReferenceId: document.getElementById("compare-reference-id"),
    compareReferenceLane: document.getElementById("compare-reference-lane"), compareReferenceVendor: document.getElementById("compare-reference-vendor"),
    compareReferenceModel: document.getElementById("compare-reference-model"), compareReferenceState: document.getElementById("compare-reference-state"),
    compareReferenceOutput: document.getElementById("compare-reference-output"), compareCurrentId: document.getElementById("compare-current-id"),
    compareCurrentLane: document.getElementById("compare-current-lane"), compareCurrentVendor: document.getElementById("compare-current-vendor"),
    compareCurrentModel: document.getElementById("compare-current-model"), compareCurrentState: document.getElementById("compare-current-state"),
    compareCurrentOutput: document.getElementById("compare-current-output"),
  };

  const state = {
    token: readToken(), jobs: [], selectedId: null,
    query: "", filter: "all", eventSource: null,
    detailCache: new Map(), detailInFlight: new Map(), detailQueue: [],
    activeDetailRequests: 0, detailGeneration: 0, detailSequence: 0,
    hasSnapshot: false, unauthorized: false, authProbeInFlight: false,
    reconnectTimer: null, mobileListScroll: 0, mobileFocusId: null,
    currentDetail: null, compareReference: null,
  };

  function boardUrl() { return window.location.pathname + window.location.search; }

  function readToken() {
    const fragment = new URLSearchParams(window.location.hash.slice(1));
    const fragmentToken = fragment.get("token");

    if (fragmentToken) {
      try {
        window.sessionStorage.setItem(TOKEN_STORAGE_KEY, fragmentToken);
      } catch (_error) {
        // Keep the fragment token in memory when session storage is unavailable.
      }
      window.history.replaceState(null, document.title, window.location.pathname + window.location.search);
      return fragmentToken;
    }

    if (window.location.hash) {
      window.history.replaceState(null, document.title, window.location.pathname + window.location.search);
    }

    try {
      return window.sessionStorage.getItem(TOKEN_STORAGE_KEY);
    } catch (_error) {
      return null;
    }
  }

  function clearStoredToken() {
    try {
      window.sessionStorage.removeItem(TOKEN_STORAGE_KEY);
    } catch (_error) {
      // An unavailable session store has nothing useful to clear.
    }
  }

  function setText(element, value) { element.textContent = value; }

  function textOrFallback(value, fallback) { return typeof value === "string" && value.length > 0 ? value : fallback; }

  function setConnection(mode, message) {
    elements.connection.dataset.mode = mode;
    setText(elements.connectionLabel, message);
  }

  function setControlsDisabled(disabled) {
    elements.search.disabled = disabled;
    elements.filterButtons.forEach(function (button) {
      button.disabled = disabled;
    });
  }

  function showInspectorState(code, title, message) {
    setText(elements.stateCode, code);
    setText(elements.stateTitle, title);
    setText(elements.stateMessage, message);
    elements.inspectorState.hidden = false;
    elements.jobDetail.hidden = true;
  }

  function isAuthError(error) { return Boolean(error && (error.status === 401 || error.status === 403)); }

  function cancelDetailRequests(clearCache) {
    state.detailGeneration += 1;
    state.detailQueue.splice(0).forEach(function (entry) {
      entry.resolve(null);
    });
    state.detailInFlight.forEach(function (entry) {
      entry.controller.abort();
    });
    state.detailInFlight.clear();
    if (clearCache) {
      state.detailCache.clear();
    }
  }

  function showUnauthorized() {
    if (state.unauthorized && state.token === null && state.jobs.length === 0) {
      return;
    }
    state.unauthorized = true;
    state.token = null;
    state.jobs = [];
    state.selectedId = null;
    state.currentDetail = null;
    state.compareReference = null;
    state.hasSnapshot = false;
    clearStoredToken();
    closeEventStream();
    cancelDetailRequests(true);
    setConnection("unauthorized", "Not authorized");
    setText(elements.jobCount, "0 jobs");
    setControlsDisabled(true);
    renderQueue();
    setMobileView("list", false);
    showInspectorState(
      "Local · read only",
      "Local access required",
      "Run `omnilane ui url` for a fresh local link."
    );
  }

  function showNoJobs() {
    showInspectorState(
      "Queue · empty",
      "No tasks yet",
      "Run an Omnilane task. This board will update automatically."
    );
  }

  function showNoMatches() {
    showInspectorState(
      "Filter · no match",
      "No matching tasks",
      "Clear the search or choose another state."
    );
  }

  function showReconnecting() {
    setConnection("reconnecting", "Reconnecting");
    if (!state.hasSnapshot) {
      showInspectorState(
        "Link · retrying",
        "Local board unavailable",
        "Reconnecting to the local job board."
      );
    }
  }

  function normalizeSummary(value) {
    if (!value || typeof value !== "object" || typeof value.id !== "string") {
      return null;
    }

    const meta = value.meta && typeof value.meta === "object" ? value.meta : {};
    const jobState = VALID_STATES.has(value.state) ? value.state : "invalid";
    const exitCode = Number.isInteger(value.exitCode) ? value.exitCode : null;

    return {
      id: value.id,
      state: jobState,
      exitCode: exitCode,
      meta: meta,
      signals: value.signals && typeof value.signals === "object" ? value.signals : {},
    };
  }

  function summarySignature(summary) {
    if (!summary) {
      return "";
    }
    return JSON.stringify({
      state: summary.state,
      exitCode: summary.exitCode,
      meta: summary.meta,
      signals: summary.signals,
    });
  }

  function summaryById(jobId) {
    return state.jobs.find(function (job) {
      return job.id === jobId;
    }) || null;
  }

  function cachedDetail(jobId) {
    const entry = state.detailCache.get(jobId);
    const summary = summaryById(jobId);
    if (!entry || !summary || entry.signature !== summarySignature(summary)) {
      return null;
    }
    state.detailCache.delete(jobId);
    state.detailCache.set(jobId, entry);
    return entry.detail;
  }

  function taskSummary(job) {
    const detail = cachedDetail(job.id);
    if (!detail || typeof detail.task !== "string" || detail.task.trim().length === 0) {
      return "Loading task…";
    }
    const compact = detail.task.replace(/\s+/g, " ").trim();
    return compact.length > 150 ? compact.slice(0, 147) + "…" : compact;
  }

  function activeFilterMatches(job) {
    if (state.filter === "active") {
      return job.state === "starting" || job.state === "running";
    }
    if (state.filter === "succeeded") {
      return job.state === "succeeded";
    }
    if (state.filter === "issues") {
      return job.state === "failed" || job.state === "dead" || job.state === "invalid";
    }
    return true;
  }

  function searchMatches(job) {
    if (!state.query) {
      return true;
    }
    const meta = job.meta;
    const searchText = [
      job.id,
      job.state,
      taskSummary(job),
      textOrFallback(meta.lane, ""),
      textOrFallback(meta.vendor, ""),
      textOrFallback(meta.model, ""),
    ].join(" ").toLocaleLowerCase();
    return searchText.includes(state.query);
  }

  function visibleJobs() {
    return state.jobs.filter(function (job) {
      return activeFilterMatches(job) && searchMatches(job);
    });
  }

  function routeLabel(job) {
    const meta = job.meta;
    return [
      textOrFallback(meta.lane, "Unknown lane"),
      textOrFallback(meta.vendor, "Unknown vendor"),
      textOrFallback(meta.model, "Unknown model"),
    ].join(" · ");
  }

  function createTextElement(tagName, className) {
    const element = document.createElement(tagName);
    element.className = className;
    return element;
  }

  function createJobRow(job) {
    const item = document.createElement("li");
    item.dataset.jobId = job.id;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "job-card";
    button.dataset.jobId = job.id;
    button.appendChild(createTextElement("span", "card-task"));
    button.appendChild(createTextElement("span", "card-job-id"));
    button.appendChild(createTextElement("span", "card-route"));
    button.appendChild(createTextElement("span", "card-state"));
    button.addEventListener("click", function () {
      selectJob(button.dataset.jobId, true, true);
    });
    item.appendChild(button);
    return item;
  }

  function updateJobRow(item, job) {
    item.dataset.jobId = job.id;
    const button = item.querySelector(".job-card");
    button.dataset.jobId = job.id;
    button.className = "job-card";
    if (job.id === state.selectedId) {
      button.classList.add("is-selected");
      button.setAttribute("aria-current", "true");
    } else {
      button.removeAttribute("aria-current");
    }
    setText(button.querySelector(".card-task"), taskSummary(job));
    setText(button.querySelector(".card-job-id"), job.id);
    setText(button.querySelector(".card-route"), routeLabel(job));
    const stateElement = button.querySelector(".card-state");
    stateElement.className = "card-state state-" + job.state;
    setText(stateElement, job.state);
  }

  function renderQueue() {
    const jobs = visibleJobs();
    const listScroll = elements.jobList.scrollTop;
    const inspectorScroll = elements.inspector.scrollTop;
    const focusedCard = document.activeElement && document.activeElement.closest
      ? document.activeElement.closest(".job-card")
      : null;
    const focusedJobId = focusedCard ? focusedCard.dataset.jobId : null;
    const existing = new Map();
    Array.from(elements.jobList.children).forEach(function (item) {
      existing.set(item.dataset.jobId, item);
    });

    let insertionPoint = elements.jobList.firstChild;
    jobs.forEach(function (job) {
      const item = existing.get(job.id) || createJobRow(job);
      existing.delete(job.id);
      updateJobRow(item, job);
      if (item !== insertionPoint) {
        elements.jobList.insertBefore(item, insertionPoint);
      }
      insertionPoint = item.nextSibling;
    });
    existing.forEach(function (item) {
      item.remove();
    });
    elements.jobList.scrollTop = listScroll;
    elements.inspector.scrollTop = inspectorScroll;
    if (focusedJobId) {
      const focusTarget = elements.jobList.querySelector(
        '.job-card[data-job-id="' + CSS.escape(focusedJobId) + '"]'
      );
      if (focusTarget && document.activeElement !== focusTarget) {
        focusTarget.focus({ preventScroll: true });
      }
    }

    if (state.jobs.length === 0) {
      setText(elements.listMessage, state.unauthorized ? "A fresh local link is required." : "No tasks yet.");
      elements.listMessage.hidden = false;
    } else if (jobs.length === 0) {
      setText(elements.listMessage, "No tasks match this filter.");
      elements.listMessage.hidden = false;
    } else {
      elements.listMessage.hidden = true;
    }

    const countLabel = jobs.length === state.jobs.length
      ? String(state.jobs.length) + (state.jobs.length === 1 ? " job" : " jobs")
      : String(jobs.length) + " / " + String(state.jobs.length) + " jobs";
    setText(elements.jobCount, countLabel);
  }

  function reconcileSelection() {
    const jobs = visibleJobs();
    const selectedVisible = jobs.some(function (job) {
      return job.id === state.selectedId;
    });
    if (!selectedVisible) {
      state.selectedId = jobs.length > 0 ? jobs[0].id : null;
    }
    return jobs;
  }

  function currentSummary() {
    return summaryById(state.selectedId);
  }

  function updateFilterSelection() {
    const oldId = state.selectedId;
    const jobs = reconcileSelection();
    renderQueue();

    if (jobs.length === 0) {
      if (state.jobs.length === 0) {
        showNoJobs();
      } else {
        showNoMatches();
      }
      return;
    }

    if (oldId !== state.selectedId || elements.jobDetail.hidden) {
      selectJob(state.selectedId, true, false);
    }
  }

  function cacheDetail(jobId, signature, detail) {
    state.detailCache.delete(jobId);
    state.detailCache.set(jobId, { signature: signature, detail: detail });
    while (state.detailCache.size > DETAIL_CACHE_LIMIT) {
      state.detailCache.delete(state.detailCache.keys().next().value);
    }
  }

  function drainDetailQueue() {
    while (state.activeDetailRequests < DETAIL_CONCURRENCY && state.detailQueue.length > 0) {
      const entry = state.detailQueue.shift();
      state.activeDetailRequests += 1;
      requestJson("/api/jobs/" + encodeURIComponent(entry.jobId), entry.controller.signal)
        .then(function (payload) {
          if (!payload || payload.ok !== true || !payload.job || typeof payload.job !== "object") {
            throw new Error("Invalid detail response");
          }
          if (entry.generation === state.detailGeneration) {
            cacheDetail(entry.jobId, entry.signature, payload.job);
          }
          entry.resolve(payload.job);
        })
        .catch(function (error) {
          if (isAuthError(error)) {
            showUnauthorized();
            entry.resolve(null);
            return;
          }
          if (error && error.name === "AbortError") {
            entry.resolve(null);
            return;
          }
          entry.reject(error);
        })
        .finally(function () {
          state.activeDetailRequests = Math.max(0, state.activeDetailRequests - 1);
          state.detailInFlight.delete(entry.jobId);
          drainDetailQueue();
        });
    }
  }

  function requestDetail(jobId, priority) {
    const cached = cachedDetail(jobId);
    if (cached) {
      return Promise.resolve(cached);
    }
    const existing = state.detailInFlight.get(jobId);
    if (existing) {
      return existing.promise;
    }
    const summary = summaryById(jobId);
    if (!summary || state.unauthorized || !state.token) {
      return Promise.resolve(null);
    }

    const controller = new AbortController();
    let resolveRequest;
    let rejectRequest;
    const promise = new Promise(function (resolve, reject) {
      resolveRequest = resolve;
      rejectRequest = reject;
    });
    const entry = {
      jobId: jobId,
      signature: summarySignature(summary),
      generation: state.detailGeneration,
      controller: controller,
      promise: promise,
      resolve: resolveRequest,
      reject: rejectRequest,
    };
    state.detailInFlight.set(jobId, entry);
    if (priority) {
      state.detailQueue.unshift(entry);
    } else {
      state.detailQueue.push(entry);
    }
    drainDetailQueue();
    return promise;
  }

  function prefetchVisibleTasks(jobs) {
    jobs.slice(0, DETAIL_PREFETCH_LIMIT).forEach(function (job) {
      requestDetail(job.id, false)
        .then(function (detail) {
          if (!detail) {
            return;
          }
          const item = elements.jobList.querySelector('li[data-job-id="' + CSS.escape(job.id) + '"]');
          const current = summaryById(job.id);
          if (item && current) {
            updateJobRow(item, current);
          } else if (state.query) {
            renderQueue();
          }
        })
        .catch(function () {
          // List summaries are optional; selected detail reports actionable failures.
        });
    });
  }

  function applySnapshot(payload) {
    if (!payload || payload.ok !== true || !Array.isArray(payload.jobs)) {
      return;
    }

    const previousSummary = currentSummary();
    const previousSignature = summarySignature(previousSummary);
    const previousId = state.selectedId;
    state.jobs = payload.jobs.map(normalizeSummary).filter(Boolean);
    state.hasSnapshot = true;
    state.unauthorized = false;
    setControlsDisabled(false);
    setConnection("live", "Live local signal");

    const jobs = reconcileSelection();
    renderQueue();
    prefetchVisibleTasks(jobs);

    if (state.jobs.length === 0) {
      state.selectedId = null;
      showNoJobs();
      return;
    }

    if (jobs.length === 0) {
      showNoMatches();
      return;
    }

    const selectedSummary = currentSummary();
    renderSummary(selectedSummary);
    const signatureChanged = previousSignature !== summarySignature(selectedSummary);
    if (previousId !== state.selectedId || signatureChanged) {
      state.currentDetail = null;
      renderCompare(null);
      fetchDetail(state.selectedId, previousId !== state.selectedId);
    } else {
      const detail = cachedDetail(state.selectedId);
      if (detail) {
        renderDetail(detail);
      }
    }
  }

  function setStateClass(element, jobState) {
    VALID_STATES.forEach(function (name) {
      element.classList.remove("state-" + name);
    });
    element.classList.add("state-" + jobState);
  }

  function metadataValue(meta, name, fallback) {
    return textOrFallback(meta[name], fallback);
  }

  function detailSnapshot(detail, fallbackSummary) {
    if (!detail || typeof detail !== "object") {
      return null;
    }
    const summary = normalizeSummary(detail.summary) || fallbackSummary;
    if (!summary) {
      return null;
    }
    return {
      summary: {
        id: summary.id,
        state: summary.state,
        exitCode: summary.exitCode,
        meta: Object.assign({}, summary.meta),
        signals: {},
      },
      output: typeof detail.output === "string" ? detail.output : "",
      outputTruncated: detail.outputTruncated === true,
      invalidFiles: Array.isArray(detail.invalidFiles) ? detail.invalidFiles.slice() : [],
    };
  }

  function comparisonOutput(snapshot) {
    const invalidFiles = invalidFileSet(snapshot);
    if (invalidFiles.has("out.txt")) {
      return "The public result could not be read safely.";
    }
    if (snapshot.output.length > 0) {
      return snapshot.output;
    }
    return emptyResultMessage(snapshot.summary);
  }

  function renderComparisonSide(prefix, snapshot) {
    const summary = snapshot.summary;
    const meta = summary.meta;
    setText(elements[prefix + "Id"], summary.id);
    setText(elements[prefix + "Lane"], metadataValue(meta, "lane", "Unknown"));
    setText(elements[prefix + "Vendor"], metadataValue(meta, "vendor", "Unknown"));
    setText(elements[prefix + "Model"], metadataValue(meta, "model", "Unknown"));
    setText(elements[prefix + "State"], summary.state);
    setText(elements[prefix + "Output"], comparisonOutput(snapshot));
  }

  function renderCompare(detail) {
    const current = detailSnapshot(detail, currentSummary());
    const reference = state.compareReference;
    const isReference = Boolean(reference && current && reference.summary.id === current.summary.id);
    elements.compareToggle.disabled = !current;
    elements.compareToggle.setAttribute("aria-pressed", isReference ? "true" : "false");
    setText(
      elements.compareToggle,
      isReference ? "Unpin reference" : reference ? "Replace reference" : "Pin for compare"
    );
    setText(
      elements.compareReferenceLabel,
      reference ? "Reference " + reference.summary.id + " pinned" : "No reference pinned"
    );

    if (!reference || !current || isReference) {
      elements.comparePanel.hidden = true;
      return;
    }
    renderComparisonSide("compareReference", reference);
    renderComparisonSide("compareCurrent", current);
    elements.comparePanel.hidden = false;
  }

  function toggleCompareReference() {
    const current = detailSnapshot(state.currentDetail, currentSummary());
    if (!current) {
      return;
    }
    if (state.compareReference && state.compareReference.summary.id === current.summary.id) {
      state.compareReference = null;
    } else {
      state.compareReference = current;
    }
    renderCompare(state.currentDetail);
  }

  function clearCompareReference() {
    state.compareReference = null;
    renderCompare(state.currentDetail);
    elements.compareToggle.focus();
  }

  function renderSummary(summary) {
    if (!summary) {
      return;
    }
    const meta = summary.meta;
    elements.inspectorState.hidden = true;
    elements.jobDetail.hidden = false;
    setText(elements.selectedJobId, summary.id);
    setText(elements.selectedJobState, summary.state);
    setText(elements.selectedJobTime, "Started " + metadataValue(meta, "started", "time unavailable"));
    setStateClass(elements.selectedJobState, summary.state);
    elements.routeTrack.dataset.state = summary.state;
    setText(elements.routeLane, metadataValue(meta, "lane", "Unknown"));
    setText(elements.routeVendor, metadataValue(meta, "vendor", "Unknown"));
    setText(elements.routeModel, metadataValue(meta, "model", "Unknown"));
    setText(elements.routeState, summary.state);
    setText(elements.factEffort, metadataValue(meta, "effort", "Not recorded"));
    setText(elements.factMode, metadataValue(meta, "mode", "Not recorded"));
    setText(elements.factTimeout, Number.isInteger(meta.timeout) ? String(meta.timeout) + " s" : "Not recorded");
    setText(elements.factCandidate, metadataValue(meta, "candidate", "Not recorded"));
    setText(elements.factStarted, metadataValue(meta, "started", "Not recorded"));
    setText(elements.factWorkdir, metadataValue(meta, "workdir", "Not recorded"));
  }

  function isMobile() {
    return window.matchMedia(MOBILE_QUERY).matches;
  }

  function rememberMobileListState() {
    state.mobileListScroll = elements.jobList.scrollTop;
    const active = document.activeElement && document.activeElement.closest
      ? document.activeElement.closest(".job-card")
      : null;
    state.mobileFocusId = active ? active.dataset.jobId : state.selectedId;
  }

  function restoreMobileListState() {
    window.requestAnimationFrame(function () {
      elements.jobList.scrollTop = state.mobileListScroll;
      if (!state.mobileFocusId) {
        return;
      }
      const button = elements.jobList.querySelector(
        '.job-card[data-job-id="' + CSS.escape(state.mobileFocusId) + '"]'
      );
      if (button) {
        button.focus({ preventScroll: true });
      }
    });
  }

  function setMobileView(view, restore) {
    document.body.dataset.mobileView = view;
    elements.mobileBack.hidden = view !== "detail";
    if (view === "list" && restore) {
      restoreMobileListState();
    }
  }

  function enterMobileDetail(jobId, pushHistory) {
    setMobileView("detail", false);
    elements.inspector.scrollTop = 0;
    if (pushHistory) {
      window.history.pushState(
        { omnilaneLiveBoard: true, view: "detail", jobId: jobId },
        document.title,
        boardUrl()
      );
    }
  }

  function returnToMobileList(useHistory) {
    if (useHistory && window.history.state && window.history.state.view === "detail") {
      window.history.back();
      return;
    }
    setMobileView("list", true);
  }

  function selectJob(jobId, showLoading, navigateMobile) {
    const job = summaryById(jobId);
    if (!job) {
      return;
    }

    if (navigateMobile && isMobile()) {
      rememberMobileListState();
    }
    state.selectedId = jobId;
    state.currentDetail = null;
    renderQueue();
    renderSummary(job);
    const detail = cachedDetail(jobId);
    if (detail) {
      renderDetail(detail);
    } else {
      if (showLoading) {
        showDetailLoading(job);
      }
      fetchDetail(jobId, false);
    }
    if (navigateMobile && isMobile()) {
      enterMobileDetail(jobId, true);
    }
  }

  function showDetailLoading(job) {
    state.currentDetail = null;
    renderCompare(null);
    clearMarkers(elements.requestMarkers);
    clearMarkers(elements.resultMarkers);
    elements.requestContent.hidden = true;
    elements.resultContent.hidden = true;
    elements.requestEmpty.hidden = false;
    elements.resultEmpty.hidden = false;
    setText(elements.requestEmpty, "Loading task…");
    setText(elements.resultEmpty, job.state === "running" ? "Worker is running. Waiting for output…" : "Loading output…");
  }

  function fetchDetail(jobId, showLoading) {
    const sequence = state.detailSequence + 1;
    state.detailSequence = sequence;
    const job = summaryById(jobId);
    if (showLoading && job) {
      showDetailLoading(job);
    }
    requestDetail(jobId, true)
      .then(function (detail) {
        if (!detail || sequence !== state.detailSequence || state.selectedId !== jobId) {
          return;
        }
        renderDetail(detail);
      })
      .catch(function (error) {
        if (isAuthError(error) || (error && error.name === "AbortError")) {
          return;
        }
        if (sequence === state.detailSequence && state.selectedId === jobId) {
          showDetailError();
          showReconnecting();
        }
      });
  }

  function clearMarkers(container) {
    container.replaceChildren();
  }

  function addMarker(container, message, isFault) {
    const marker = document.createElement("span");
    marker.className = isFault ? "content-marker is-fault" : "content-marker";
    marker.textContent = message;
    container.appendChild(marker);
  }

  function invalidFileSet(detail) {
    if (!Array.isArray(detail.invalidFiles)) {
      return new Set();
    }
    return new Set(detail.invalidFiles.filter(function (name) {
      return name === "task.txt" || name === "out.txt";
    }));
  }

  function emptyResultMessage(summary) {
    if (summary.state === "starting") {
      return "Dispatch is starting. No public result has been recorded yet.";
    }
    if (summary.state === "running") {
      return "Worker is running. The public result will appear here.";
    }
    if (summary.state === "failed") {
      const code = summary.exitCode === null ? "an unknown code" : "exit code " + String(summary.exitCode);
      return "Dispatch failed with " + code + ". No public result was recorded.";
    }
    if (summary.state === "dead") {
      return "The worker is gone and no exit code was recorded.";
    }
    if (summary.state === "invalid") {
      return "This dispatch contains invalid metadata or control files. No safe result is available.";
    }
    return "Dispatch completed without a public result.";
  }

  function renderPlainText(contentElement, emptyElement, value, emptyMessage, followBottom) {
    const text = typeof value === "string" ? value : "";
    const wasVisible = !contentElement.hidden && contentElement.textContent.length > 0;
    const previousScroll = contentElement.scrollTop;
    const wasNearBottom = wasVisible && (
      contentElement.scrollHeight - contentElement.clientHeight - contentElement.scrollTop <= OUTPUT_BOTTOM_THRESHOLD
    );

    if (text.length > 0) {
      contentElement.textContent = text;
      contentElement.hidden = false;
      emptyElement.hidden = true;
      if (!wasVisible) {
        contentElement.scrollTop = 0;
      } else if (followBottom && wasNearBottom) {
        contentElement.scrollTop = contentElement.scrollHeight;
      } else {
        const maximum = Math.max(0, contentElement.scrollHeight - contentElement.clientHeight);
        contentElement.scrollTop = Math.min(previousScroll, maximum);
      }
    } else {
      contentElement.textContent = "";
      contentElement.hidden = true;
      setText(emptyElement, emptyMessage);
      emptyElement.hidden = false;
    }
  }

  function renderDetail(detail) {
    const detailSummary = normalizeSummary(detail.summary);
    const summary = detailSummary || currentSummary();
    if (!summary) {
      showDetailError();
      return;
    }

    state.currentDetail = detail;
    renderSummary(summary);
    renderCompare(detail);
    clearMarkers(elements.requestMarkers);
    clearMarkers(elements.resultMarkers);
    const invalidFiles = invalidFileSet(detail);

    if (detail.taskTruncated === true) {
      addMarker(elements.requestMarkers, "Large content shortened to 512 KiB", false);
    }
    if (detail.outputTruncated === true) {
      addMarker(elements.resultMarkers, "Large content shortened to 512 KiB", false);
    }
    if (invalidFiles.has("task.txt")) {
      addMarker(elements.requestMarkers, "Task file could not be read safely", true);
    }
    if (invalidFiles.has("out.txt")) {
      addMarker(elements.resultMarkers, "Result file could not be read safely", true);
    }
    if (summary.state === "failed") {
      addMarker(
        elements.resultMarkers,
        summary.exitCode === null
          ? "Dispatch failed without a readable exit code"
          : "Dispatch failed with exit code " + String(summary.exitCode),
        true
      );
    } else if (summary.state === "dead") {
      addMarker(elements.resultMarkers, "Worker gone · exit code not recorded", true);
    } else if (summary.state === "invalid") {
      addMarker(elements.resultMarkers, "Invalid dispatch · safe fields only", true);
    }

    renderPlainText(
      elements.requestContent,
      elements.requestEmpty,
      detail.task,
      invalidFiles.has("task.txt") ? "The task could not be read safely." : "No task was recorded.",
      false
    );
    renderPlainText(
      elements.resultContent,
      elements.resultEmpty,
      detail.output,
      invalidFiles.has("out.txt") ? "The public result could not be read safely." : emptyResultMessage(summary),
      true
    );
  }

  function showDetailError() {
    clearMarkers(elements.requestMarkers);
    clearMarkers(elements.resultMarkers);
    addMarker(elements.resultMarkers, "Detail temporarily unavailable", true);
    setText(elements.requestEmpty, "The last task snapshot is preserved while detail reloads.");
    setText(elements.resultEmpty, "Reconnecting to the local job board.");
    elements.requestEmpty.hidden = false;
    elements.resultEmpty.hidden = false;
  }

  async function requestJson(path, signal) {
    const response = await window.fetch(path, {
      method: "GET",
      headers: {
        Authorization: "Bearer " + state.token,
        Accept: "application/json",
      },
      cache: "no-store",
      credentials: "omit",
      referrerPolicy: "no-referrer",
      signal: signal,
    });

    if (!response.ok) {
      const error = new Error("Request failed");
      error.status = response.status;
      throw error;
    }
    return response.json();
  }

  function closeEventStream() {
    if (state.reconnectTimer !== null) {
      window.clearTimeout(state.reconnectTimer);
      state.reconnectTimer = null;
    }
    if (state.eventSource) {
      state.eventSource.close();
      state.eventSource = null;
    }
  }

  function openEventStream() {
    closeEventStream();
    if (!state.token) {
      return;
    }

    const source = new EventSource("/api/events?token=" + encodeURIComponent(state.token));
    state.eventSource = source;
    source.addEventListener("snapshot", function (event) {
      try {
        const payload = JSON.parse(event.data);
        applySnapshot(payload);
      } catch (_error) {
        showReconnecting();
      }
    });
    source.onerror = function () {
      if (state.unauthorized) {
        return;
      }
      showReconnecting();
      probeAuthorization();
      if (
        source.readyState === EventSource.CLOSED &&
        state.eventSource === source &&
        state.reconnectTimer === null
      ) {
        state.reconnectTimer = window.setTimeout(function () {
          state.reconnectTimer = null;
          if (!state.unauthorized && state.eventSource === source) {
            openEventStream();
          }
        }, 3000);
      }
    };
  }

  async function probeAuthorization() {
    if (state.authProbeInFlight || !state.token || state.unauthorized) {
      return;
    }
    state.authProbeInFlight = true;
    try {
      await requestJson("/api/health");
    } catch (error) {
      if (isAuthError(error)) {
        showUnauthorized();
      }
    } finally {
      state.authProbeInFlight = false;
    }
  }

  async function loadInitialSnapshot() {
    try {
      const payload = await requestJson("/api/jobs");
      applySnapshot(payload);
    } catch (error) {
      if (isAuthError(error)) {
        showUnauthorized();
      } else {
        showReconnecting();
      }
    }
  }

  function bindControls() {
    elements.search.addEventListener("input", function () {
      state.query = elements.search.value.trim().toLocaleLowerCase();
      updateFilterSelection();
    });

    elements.filter.addEventListener("click", function (event) {
      const button = event.target.closest("button[data-filter]");
      if (!button || button.disabled) {
        return;
      }
      const nextFilter = button.dataset.filter;
      if (!["all", "active", "succeeded", "issues"].includes(nextFilter)) {
        return;
      }
      state.filter = nextFilter;
      elements.filterButtons.forEach(function (candidate) {
        const active = candidate === button;
        candidate.classList.toggle("is-active", active);
        candidate.setAttribute("aria-pressed", active ? "true" : "false");
      });
      updateFilterSelection();
    });

    elements.mobileBack.addEventListener("click", function () {
      returnToMobileList(true);
    });

    elements.compareToggle.addEventListener("click", toggleCompareReference);
    elements.compareClear.addEventListener("click", clearCompareReference);

    window.addEventListener("popstate", function (event) {
      const historyState = event.state;
      if (historyState && historyState.omnilaneLiveBoard && historyState.view === "detail") {
        if (historyState.jobId && summaryById(historyState.jobId)) {
          selectJob(historyState.jobId, true, false);
        }
        setMobileView("detail", false);
      } else {
        setMobileView("list", true);
      }
    });

    window.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && isMobile() && document.body.dataset.mobileView === "detail") {
        event.preventDefault();
        returnToMobileList(true);
      }
    });
  }

  function initializeHistory() {
    window.history.replaceState(
      { omnilaneLiveBoard: true, view: "list" },
      document.title,
      boardUrl()
    );
    setMobileView("list", false);
  }

  function start() {
    initializeHistory();
    bindControls();
    if (!state.token) {
      showUnauthorized();
      return;
    }

    setControlsDisabled(false);
    setConnection("waiting", "Connecting");
    openEventStream();
    loadInitialSnapshot();
  }

  start();
})();
