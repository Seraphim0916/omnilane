"use strict";

(function () {
  const TOKEN_STORAGE_KEY = "omnilane.live-ui.token";
  const VALID_STATES = new Set([
    "starting",
    "running",
    "succeeded",
    "failed",
    "dead",
    "invalid",
  ]);

  const elements = {
    connection: document.getElementById("connection-status"),
    connectionLabel: document.getElementById("connection-label"),
    jobCount: document.getElementById("job-count"),
    search: document.getElementById("job-search"),
    filter: document.getElementById("status-filter"),
    filterButtons: Array.from(document.querySelectorAll(".filter-button")),
    mobileSelect: document.getElementById("mobile-job-select"),
    jobList: document.getElementById("job-list"),
    listMessage: document.getElementById("list-message"),
    inspectorState: document.getElementById("inspector-state"),
    stateCode: document.getElementById("state-code"),
    stateTitle: document.getElementById("state-title"),
    stateMessage: document.getElementById("state-message"),
    jobDetail: document.getElementById("job-detail"),
    selectedJobId: document.getElementById("selected-job-id"),
    selectedJobState: document.getElementById("selected-job-state"),
    routeTrack: document.getElementById("route-track"),
    routeLane: document.getElementById("route-lane"),
    routeVendor: document.getElementById("route-vendor"),
    routeModel: document.getElementById("route-model"),
    routeState: document.getElementById("route-state"),
    factEffort: document.getElementById("fact-effort"),
    factMode: document.getElementById("fact-mode"),
    factTimeout: document.getElementById("fact-timeout"),
    factCandidate: document.getElementById("fact-candidate"),
    factStarted: document.getElementById("fact-started"),
    factWorkdir: document.getElementById("fact-workdir"),
    requestMarkers: document.getElementById("request-markers"),
    requestEmpty: document.getElementById("request-empty"),
    requestContent: document.getElementById("request-content"),
    resultMarkers: document.getElementById("result-markers"),
    resultEmpty: document.getElementById("result-empty"),
    resultContent: document.getElementById("result-content"),
  };

  const state = {
    token: readToken(),
    jobs: [],
    selectedId: null,
    query: "",
    filter: "all",
    eventSource: null,
    detailController: null,
    detailSequence: 0,
    hasSnapshot: false,
    unauthorized: false,
    authProbeInFlight: false,
    reconnectTimer: null,
  };

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

  function setText(element, value) {
    element.textContent = value;
  }

  function textOrFallback(value, fallback) {
    return typeof value === "string" && value.length > 0 ? value : fallback;
  }

  function setConnection(mode, message) {
    elements.connection.dataset.mode = mode;
    setText(elements.connectionLabel, message);
  }

  function setControlsDisabled(disabled) {
    elements.search.disabled = disabled;
    elements.filterButtons.forEach(function (button) {
      button.disabled = disabled;
    });
    elements.mobileSelect.disabled = disabled || visibleJobs().length === 0;
  }

  function showInspectorState(code, title, message) {
    setText(elements.stateCode, code);
    setText(elements.stateTitle, title);
    setText(elements.stateMessage, message);
    elements.inspectorState.hidden = false;
    elements.jobDetail.hidden = true;
  }

  function showUnauthorized() {
    state.unauthorized = true;
    state.token = null;
    state.jobs = [];
    state.selectedId = null;
    state.hasSnapshot = false;
    clearStoredToken();
    closeEventStream();
    abortDetailRequest();
    setConnection("unauthorized", "Not authorized");
    setText(elements.jobCount, "0 jobs");
    setControlsDisabled(true);
    renderQueue();
    showInspectorState(
      "AUTH / LOCAL LINK",
      "Local access required",
      "This Live UI link is not authorized. Run `omnilane ui url` for a fresh local link."
    );
  }

  function showNoJobs() {
    showInspectorState(
      "QUEUE / EMPTY",
      "No dispatches yet",
      "No dispatches yet. Run an omnilane task; this board will update automatically."
    );
  }

  function showNoMatches() {
    showInspectorState(
      "FILTER / NO MATCH",
      "No matching dispatches",
      "Clear the search or choose another status to inspect the queue."
    );
  }

  function showReconnecting() {
    setConnection("reconnecting", "Reconnecting to the local job board");
    if (!state.hasSnapshot) {
      showInspectorState(
        "LINK / RETRYING",
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
    ].join(" → ");
  }

  function createTextElement(tagName, className, value) {
    const element = document.createElement(tagName);
    element.className = className;
    element.textContent = value;
    return element;
  }

  function buildJobCard(job) {
    const item = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.className = "job-card";
    if (job.id === state.selectedId) {
      button.classList.add("is-selected");
      button.setAttribute("aria-current", "true");
    }

    button.appendChild(createTextElement("span", "card-job-id", job.id));
    button.appendChild(createTextElement("span", "card-route", routeLabel(job)));
    button.appendChild(createTextElement("span", "card-state state-" + job.state, job.state));
    button.addEventListener("click", function () {
      selectJob(job.id, true);
    });
    item.appendChild(button);
    return item;
  }

  function renderMobileSelect(jobs) {
    elements.mobileSelect.replaceChildren();
    if (jobs.length === 0) {
      const option = document.createElement("option");
      option.textContent = state.jobs.length === 0 ? "No dispatches" : "No matching dispatches";
      elements.mobileSelect.appendChild(option);
      elements.mobileSelect.disabled = true;
      return;
    }

    jobs.forEach(function (job, index) {
      const option = document.createElement("option");
      option.value = String(index);
      option.textContent = job.id + " · " + job.state + " · " + textOrFallback(job.meta.model, "unknown model");
      if (job.id === state.selectedId) {
        option.selected = true;
      }
      elements.mobileSelect.appendChild(option);
    });
    elements.mobileSelect.disabled = state.unauthorized;
  }

  function renderQueue() {
    const jobs = visibleJobs();
    elements.jobList.replaceChildren();
    jobs.forEach(function (job) {
      elements.jobList.appendChild(buildJobCard(job));
    });
    renderMobileSelect(jobs);

    if (state.jobs.length === 0) {
      setText(elements.listMessage, state.unauthorized ? "A fresh local link is required." : "No dispatches yet.");
      elements.listMessage.hidden = false;
    } else if (jobs.length === 0) {
      setText(elements.listMessage, "No dispatches match this filter.");
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
    return state.jobs.find(function (job) {
      return job.id === state.selectedId;
    }) || null;
  }

  function updateFilterSelection() {
    const oldId = state.selectedId;
    const jobs = reconcileSelection();
    renderQueue();

    if (jobs.length === 0) {
      abortDetailRequest();
      if (state.jobs.length === 0) {
        showNoJobs();
      } else {
        showNoMatches();
      }
      return;
    }

    if (oldId !== state.selectedId || elements.jobDetail.hidden) {
      selectJob(state.selectedId, true);
    }
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

    if (state.jobs.length === 0) {
      state.selectedId = null;
      abortDetailRequest();
      showNoJobs();
      return;
    }

    if (jobs.length === 0) {
      abortDetailRequest();
      showNoMatches();
      return;
    }

    const selectedSummary = currentSummary();
    renderSummary(selectedSummary);
    if (previousId !== state.selectedId || previousSignature !== summarySignature(selectedSummary)) {
      fetchDetail(state.selectedId);
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

  function renderSummary(summary) {
    if (!summary) {
      return;
    }
    const meta = summary.meta;
    elements.inspectorState.hidden = true;
    elements.jobDetail.hidden = false;
    setText(elements.selectedJobId, summary.id);
    setText(elements.selectedJobState, summary.state.toLocaleUpperCase());
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

  function abortDetailRequest() {
    if (state.detailController) {
      state.detailController.abort();
      state.detailController = null;
    }
  }

  function selectJob(jobId, forceFetch) {
    const job = state.jobs.find(function (item) {
      return item.id === jobId;
    });
    if (!job) {
      return;
    }

    const changed = state.selectedId !== jobId;
    state.selectedId = jobId;
    renderQueue();
    renderSummary(job);
    if (changed || forceFetch) {
      showDetailLoading(job);
      fetchDetail(jobId);
    }
  }

  function showDetailLoading(job) {
    clearMarkers(elements.requestMarkers);
    clearMarkers(elements.resultMarkers);
    elements.requestContent.hidden = true;
    elements.resultContent.hidden = true;
    elements.requestEmpty.hidden = false;
    elements.resultEmpty.hidden = false;
    setText(elements.requestEmpty, "Loading operator request…");
    setText(elements.resultEmpty, job.state === "running" ? "Worker is running. Waiting for public result…" : "Loading public result…");
  }

  async function fetchDetail(jobId) {
    abortDetailRequest();
    const controller = new AbortController();
    const sequence = state.detailSequence + 1;
    state.detailSequence = sequence;
    state.detailController = controller;

    try {
      const payload = await requestJson("/api/jobs/" + encodeURIComponent(jobId), controller.signal);
      if (sequence !== state.detailSequence || state.selectedId !== jobId) {
        return;
      }
      if (!payload || payload.ok !== true || !payload.job || typeof payload.job !== "object") {
        throw new Error("Invalid detail response");
      }
      renderDetail(payload.job);
    } catch (error) {
      if (error && error.name === "AbortError") {
        return;
      }
      if (error && error.status === 401) {
        showUnauthorized();
        return;
      }
      if (sequence === state.detailSequence && state.selectedId === jobId) {
        showDetailError();
        showReconnecting();
      }
    } finally {
      if (sequence === state.detailSequence) {
        state.detailController = null;
      }
    }
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
      return "Dispatch is starting. Public result has not been recorded yet.";
    }
    if (summary.state === "running") {
      return "Worker is running. Public result will appear here.";
    }
    if (summary.state === "failed") {
      const code = summary.exitCode === null ? "an unknown code" : "exit code " + String(summary.exitCode);
      return "Dispatch failed with " + code + ". No public result was recorded.";
    }
    if (summary.state === "dead") {
      return "The worker is gone and no exit code was recorded.";
    }
    if (summary.state === "invalid") {
      return "This dispatch contains invalid metadata or control files. No safe public result is available.";
    }
    return "Dispatch completed without a public result.";
  }

  function renderPlainText(contentElement, emptyElement, value, emptyMessage) {
    const text = typeof value === "string" ? value : "";
    if (text.length > 0) {
      contentElement.textContent = text;
      contentElement.hidden = false;
      emptyElement.hidden = true;
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

    renderSummary(summary);
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
      addMarker(elements.requestMarkers, "Request file could not be read safely", true);
    }
    if (invalidFiles.has("out.txt")) {
      addMarker(elements.resultMarkers, "Public result could not be read safely", true);
    }
    if (summary.state === "failed") {
      const failureMessage = summary.exitCode === null
        ? "Dispatch failed without a readable exit code"
        : "Dispatch failed with exit code " + String(summary.exitCode);
      addMarker(elements.resultMarkers, failureMessage, true);
    } else if (summary.state === "dead") {
      addMarker(elements.resultMarkers, "Worker gone · exit code not recorded", true);
    } else if (summary.state === "invalid") {
      addMarker(elements.resultMarkers, "Invalid dispatch · safe fields only", true);
    }

    renderPlainText(
      elements.requestContent,
      elements.requestEmpty,
      detail.task,
      invalidFiles.has("task.txt") ? "The operator request could not be read safely." : "No operator request was recorded."
    );
    renderPlainText(
      elements.resultContent,
      elements.resultEmpty,
      detail.output,
      invalidFiles.has("out.txt") ? "The public result could not be read safely." : emptyResultMessage(summary)
    );
  }

  function showDetailError() {
    clearMarkers(elements.requestMarkers);
    clearMarkers(elements.resultMarkers);
    addMarker(elements.resultMarkers, "Detail temporarily unavailable", true);
    elements.requestContent.hidden = true;
    elements.resultContent.hidden = true;
    elements.requestEmpty.hidden = false;
    elements.resultEmpty.hidden = false;
    setText(elements.requestEmpty, "The last route snapshot is preserved while this detail reloads.");
    setText(elements.resultEmpty, "Reconnecting to the local job board.");
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
      if (error && error.status === 401) {
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
      if (error && error.status === 401) {
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

    elements.mobileSelect.addEventListener("change", function () {
      const jobs = visibleJobs();
      const index = Number.parseInt(elements.mobileSelect.value, 10);
      if (Number.isInteger(index) && jobs[index]) {
        selectJob(jobs[index].id, true);
      }
    });
  }

  function start() {
    bindControls();
    if (!state.token) {
      showUnauthorized();
      return;
    }

    setControlsDisabled(false);
    setConnection("waiting", "Connecting to local board");
    openEventStream();
    loadInitialSnapshot();
  }

  start();
})();
