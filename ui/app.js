"use strict";

(function () {
  const TOKEN_STORAGE_KEY = "omnilane.live-ui.token";
  const LANGUAGE_STORAGE_KEY = "omnilane.live-ui.language";
  const DETAIL_CACHE_LIMIT = 50, DETAIL_PREFETCH_LIMIT = 12, DETAIL_CONCURRENCY = 3;
  const OUTPUT_BOTTOM_THRESHOLD = 28;
  const MOBILE_QUERY = "(max-width: 760px)";
  const VALID_STATES = new Set(["starting", "running", "succeeded", "failed", "dead", "invalid"]);
  const DEFAULT_LANGUAGE = "en";
  const LANGUAGES = [
    { code: "en", label: "English" },
    { code: "ja", label: "日本語" },
    { code: "ko", label: "한국어" },
    { code: "zh-TW", label: "繁體中文" },
    { code: "zh-CN", label: "简体中文" },
  ];

  const elements = {
    connection: document.getElementById("connection-status"),
    connectionLabel: document.getElementById("connection-label"),
    languageSelect: document.getElementById("language-select"),
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
    language: DEFAULT_LANGUAGE, connection: { mode: "waiting", key: "connection.connecting" },
    inspectorView: null, detailPlaceholder: null,
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

  function isSupportedLanguage(code) {
    return LANGUAGES.some(function (language) {
      return language.code === code;
    });
  }

  function readStoredLanguage() {
    try {
      const stored = window.localStorage.getItem(LANGUAGE_STORAGE_KEY);
      return isSupportedLanguage(stored) ? stored : null;
    } catch (_error) {
      return null;
    }
  }

  function storeLanguage(code) {
    try {
      window.localStorage.setItem(LANGUAGE_STORAGE_KEY, code);
    } catch (_error) {
      // A blocked local store still leaves the choice active for this page.
    }
  }

  function matchLanguage(tag) {
    if (typeof tag !== "string" || tag.length === 0) {
      return null;
    }
    const lower = tag.toLowerCase();
    if (lower === "zh" || lower.startsWith("zh-hans") || lower.startsWith("zh-cn") || lower.startsWith("zh-sg")) {
      return "zh-CN";
    }
    if (lower.startsWith("zh")) {
      return "zh-TW";
    }
    if (lower.startsWith("ja")) {
      return "ja";
    }
    if (lower.startsWith("ko")) {
      return "ko";
    }
    if (lower.startsWith("en")) {
      return "en";
    }
    return null;
  }

  function detectLanguage() {
    const stored = readStoredLanguage();
    if (stored) {
      return stored;
    }
    const tags = Array.isArray(window.navigator.languages) && window.navigator.languages.length > 0
      ? window.navigator.languages
      : [window.navigator.language];
    for (let index = 0; index < tags.length; index += 1) {
      const matched = matchLanguage(tags[index]);
      if (matched) {
        return matched;
      }
    }
    return DEFAULT_LANGUAGE;
  }

  function lookupMessage(language, key) {
    const table = MESSAGES[language];
    return table && Object.prototype.hasOwnProperty.call(table, key) ? table[key] : undefined;
  }

  function selectPluralForm(forms, params) {
    const count = params && typeof params.n === "number" ? params.n : 0;
    let category;
    try {
      category = new Intl.PluralRules(state.language).select(count);
    } catch (_error) {
      category = count === 1 ? "one" : "other";
    }
    return Object.prototype.hasOwnProperty.call(forms, category) ? forms[category] : forms.other;
  }

  function interpolate(template, params) {
    if (!params) {
      return template;
    }
    return template.replace(/\{(\w+)\}/g, function (match, name) {
      return Object.prototype.hasOwnProperty.call(params, name) ? String(params[name]) : match;
    });
  }

  function t(key, params) {
    let entry = lookupMessage(state.language, key);
    if (entry === undefined) {
      entry = lookupMessage(DEFAULT_LANGUAGE, key);
    }
    if (entry === undefined) {
      return key;
    }
    if (typeof entry === "object") {
      entry = selectPluralForm(entry, params);
    }
    return interpolate(entry, params);
  }

  function jobStateLabel(value) {
    return t("jobState." + value);
  }

  function applyStaticTranslations() {
    document.documentElement.lang = state.language;
    document.querySelectorAll("[data-i18n]").forEach(function (element) {
      element.textContent = t(element.dataset.i18n);
    });
    document.querySelectorAll("[data-i18n-attr]").forEach(function (element) {
      element.dataset.i18nAttr.split(";").forEach(function (pair) {
        const separator = pair.indexOf(":");
        if (separator < 0) {
          return;
        }
        const attribute = pair.slice(0, separator).trim();
        const key = pair.slice(separator + 1).trim();
        if (attribute && key) {
          element.setAttribute(attribute, t(key));
        }
      });
    });
  }

  function populateLanguageSelect() {
    const options = document.createDocumentFragment();
    LANGUAGES.forEach(function (language) {
      const option = document.createElement("option");
      option.value = language.code;
      option.lang = language.code;
      option.textContent = language.label;
      options.appendChild(option);
    });
    elements.languageSelect.replaceChildren(options);
    elements.languageSelect.value = state.language;
  }

  function refreshLanguage() {
    applyStaticTranslations();
    renderConnection();
    renderQueue();

    if (elements.jobDetail.hidden) {
      if (state.inspectorView) {
        showInspectorState(state.inspectorView.code, state.inspectorView.title, state.inspectorView.message);
      }
      return;
    }

    if (state.currentDetail) {
      renderDetail(state.currentDetail);
      return;
    }

    const summary = currentSummary();
    if (summary) {
      renderSummary(summary);
    }
    if (state.detailPlaceholder === "loading" && summary) {
      showDetailLoading(summary);
    } else if (state.detailPlaceholder === "error") {
      showDetailError();
    } else {
      renderCompare(null);
    }
  }

  function setLanguage(code) {
    if (!isSupportedLanguage(code) || code === state.language) {
      return;
    }
    state.language = code;
    storeLanguage(code);
    elements.languageSelect.value = code;
    refreshLanguage();
  }

  function initializeLanguage() {
    state.language = detectLanguage();
    populateLanguageSelect();
    applyStaticTranslations();
  }

  function setText(element, value) { element.textContent = value; }

  function textOrFallback(value, fallback) { return typeof value === "string" && value.length > 0 ? value : fallback; }

  function renderConnection() {
    elements.connection.dataset.mode = state.connection.mode;
    setText(elements.connectionLabel, t(state.connection.key));
  }

  function setConnection(mode, key) {
    state.connection = { mode: mode, key: key };
    renderConnection();
  }

  function setControlsDisabled(disabled) {
    elements.search.disabled = disabled;
    elements.filterButtons.forEach(function (button) {
      button.disabled = disabled;
    });
  }

  function showInspectorState(codeKey, titleKey, messageKey) {
    state.inspectorView = { code: codeKey, title: titleKey, message: messageKey };
    setText(elements.stateCode, t(codeKey));
    setText(elements.stateTitle, t(titleKey));
    setText(elements.stateMessage, t(messageKey));
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
    setConnection("unauthorized", "connection.unauthorized");
    setText(elements.jobCount, t("count.jobs", { n: 0 }));
    setControlsDisabled(true);
    renderQueue();
    setMobileView("list", false);
    showInspectorState(
      "state.local.code",
      "state.unauthorized.title",
      "state.unauthorized.message"
    );
  }

  function showNoJobs() {
    showInspectorState(
      "state.empty.code",
      "state.empty.title",
      "state.empty.message"
    );
  }

  function showNoMatches() {
    showInspectorState(
      "state.noMatch.code",
      "state.noMatch.title",
      "state.noMatch.message"
    );
  }

  function showReconnecting() {
    setConnection("reconnecting", "connection.reconnecting");
    if (!state.hasSnapshot) {
      showInspectorState(
        "state.retry.code",
        "state.retry.title",
        "state.retry.message"
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
      return t("loading.task");
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
      jobStateLabel(job.state),
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
      textOrFallback(meta.lane, t("value.unknownLane")),
      textOrFallback(meta.vendor, t("value.unknownVendor")),
      textOrFallback(meta.model, t("value.unknownModel")),
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
    setText(stateElement, jobStateLabel(job.state));
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
      setText(elements.listMessage, state.unauthorized ? t("list.unauthorized") : t("list.empty"));
      elements.listMessage.hidden = false;
    } else if (jobs.length === 0) {
      setText(elements.listMessage, t("list.noMatch"));
      elements.listMessage.hidden = false;
    } else {
      elements.listMessage.hidden = true;
    }

    const countLabel = jobs.length === state.jobs.length
      ? t("count.jobs", { n: state.jobs.length })
      : t("count.filtered", { shown: jobs.length, total: state.jobs.length });
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
    setConnection("live", "connection.live");

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
      return t("result.unsafe");
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
    setText(elements[prefix + "Lane"], metadataValue(meta, "lane", t("value.unknown")));
    setText(elements[prefix + "Vendor"], metadataValue(meta, "vendor", t("value.unknown")));
    setText(elements[prefix + "Model"], metadataValue(meta, "model", t("value.unknown")));
    setText(elements[prefix + "State"], jobStateLabel(summary.state));
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
      isReference ? t("compare.unpin") : reference ? t("compare.replace") : t("compare.pin")
    );
    setText(
      elements.compareReferenceLabel,
      reference ? t("compare.pinned", { id: reference.summary.id }) : t("compare.none")
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
    setText(elements.selectedJobState, jobStateLabel(summary.state));
    setText(
      elements.selectedJobTime,
      t("detail.timeStarted", { value: metadataValue(meta, "started", t("detail.timeUnavailable")) })
    );
    setStateClass(elements.selectedJobState, summary.state);
    elements.routeTrack.dataset.state = summary.state;
    setText(elements.routeLane, metadataValue(meta, "lane", t("value.unknown")));
    setText(elements.routeVendor, metadataValue(meta, "vendor", t("value.unknown")));
    setText(elements.routeModel, metadataValue(meta, "model", t("value.unknown")));
    setText(elements.routeState, jobStateLabel(summary.state));
    setText(elements.factEffort, metadataValue(meta, "effort", t("value.notRecorded")));
    setText(elements.factMode, metadataValue(meta, "mode", t("value.notRecorded")));
    setText(
      elements.factTimeout,
      Number.isInteger(meta.timeout) ? t("value.seconds", { n: meta.timeout }) : t("value.notRecorded")
    );
    setText(elements.factCandidate, metadataValue(meta, "candidate", t("value.notRecorded")));
    setText(elements.factStarted, metadataValue(meta, "started", t("value.notRecorded")));
    setText(elements.factWorkdir, metadataValue(meta, "workdir", t("value.notRecorded")));
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
    state.detailPlaceholder = "loading";
    renderCompare(null);
    clearMarkers(elements.requestMarkers);
    clearMarkers(elements.resultMarkers);
    elements.requestContent.hidden = true;
    elements.resultContent.hidden = true;
    elements.requestEmpty.hidden = false;
    elements.resultEmpty.hidden = false;
    setText(elements.requestEmpty, t("loading.task"));
    setText(elements.resultEmpty, job.state === "running" ? t("loading.running") : t("loading.output"));
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
      return t("result.starting");
    }
    if (summary.state === "running") {
      return t("result.running");
    }
    if (summary.state === "failed") {
      return summary.exitCode === null
        ? t("result.failedUnknown")
        : t("result.failedCode", { code: summary.exitCode });
    }
    if (summary.state === "dead") {
      return t("result.dead");
    }
    if (summary.state === "invalid") {
      return t("result.invalid");
    }
    return t("result.none");
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
    state.detailPlaceholder = null;
    renderSummary(summary);
    renderCompare(detail);
    clearMarkers(elements.requestMarkers);
    clearMarkers(elements.resultMarkers);
    const invalidFiles = invalidFileSet(detail);

    if (detail.taskTruncated === true) {
      addMarker(elements.requestMarkers, t("marker.truncated"), false);
    }
    if (detail.outputTruncated === true) {
      addMarker(elements.resultMarkers, t("marker.truncated"), false);
    }
    if (invalidFiles.has("task.txt")) {
      addMarker(elements.requestMarkers, t("marker.taskUnsafe"), true);
    }
    if (invalidFiles.has("out.txt")) {
      addMarker(elements.resultMarkers, t("marker.resultUnsafe"), true);
    }
    if (summary.state === "failed") {
      addMarker(
        elements.resultMarkers,
        summary.exitCode === null
          ? t("marker.failedNoCode")
          : t("marker.failedCode", { code: summary.exitCode }),
        true
      );
    } else if (summary.state === "dead") {
      addMarker(elements.resultMarkers, t("marker.dead"), true);
    } else if (summary.state === "invalid") {
      addMarker(elements.resultMarkers, t("marker.invalid"), true);
    }

    renderPlainText(
      elements.requestContent,
      elements.requestEmpty,
      detail.task,
      invalidFiles.has("task.txt") ? t("task.unsafe") : t("task.none"),
      false
    );
    renderPlainText(
      elements.resultContent,
      elements.resultEmpty,
      detail.output,
      invalidFiles.has("out.txt") ? t("result.unsafe") : emptyResultMessage(summary),
      true
    );
  }

  function showDetailError() {
    state.detailPlaceholder = "error";
    clearMarkers(elements.requestMarkers);
    clearMarkers(elements.resultMarkers);
    addMarker(elements.resultMarkers, t("marker.detailUnavailable"), true);
    setText(elements.requestEmpty, t("error.taskPreserved"));
    setText(elements.resultEmpty, t("error.reconnecting"));
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
    elements.languageSelect.addEventListener("change", function () {
      setLanguage(elements.languageSelect.value);
    });

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

  // The locale tables sit after the board logic so the readable code stays at the
  // top of the file; nothing reads MESSAGES until start() runs at the very end.
  const MESSAGES = {
    en: {
      "app.title": "Omnilane Live Board",
      "header.liveJobs": "Live jobs",
      "header.readOnly": "Read only",
      "header.language": "Language",
      "connection.connecting": "Connecting",
      "connection.live": "Live local signal",
      "connection.reconnecting": "Reconnecting",
      "connection.unauthorized": "Not authorized",
      "index.sectionLabel": "Recent work",
      "index.title": "Task record",
      "search.label": "Find a task",
      "search.placeholder": "Task, ID, lane, model",
      "filter.legend": "Show",
      "filter.all": "All",
      "filter.active": "Active",
      "filter.done": "Done",
      "filter.issues": "Issues",
      "list.ariaLabel": "Recent tasks",
      "list.waiting": "Waiting for tasks.",
      "list.empty": "No tasks yet.",
      "list.unauthorized": "A fresh local link is required.",
      "list.noMatch": "No tasks match this filter.",
      "count.jobs": { one: "{n} job", other: "{n} jobs" },
      "count.filtered": "{shown} / {total} jobs",
      "inspector.ariaLabel": "Selected task detail",
      "state.local.code": "Local · read only",
      "state.connecting.title": "Connecting to the job board",
      "state.connecting.message": "The newest task will appear here.",
      "state.unauthorized.title": "Local access required",
      "state.unauthorized.message": "Run `omnilane ui url` for a fresh local link.",
      "state.empty.code": "Queue · empty",
      "state.empty.title": "No tasks yet",
      "state.empty.message": "Run an Omnilane task. This board will update automatically.",
      "state.noMatch.code": "Filter · no match",
      "state.noMatch.title": "No matching tasks",
      "state.noMatch.message": "Clear the search or choose another state.",
      "state.retry.code": "Link · retrying",
      "state.retry.title": "Local board unavailable",
      "state.retry.message": "Reconnecting to the local job board.",
      "mobile.back": "Back to tasks",
      "detail.sectionLabel": "Selected task",
      "detail.timeStarted": "Started {value}",
      "detail.timeUnavailable": "time unavailable",
      "detail.timeUnknown": "Started time unavailable",
      "compare.pin": "Pin for compare",
      "compare.unpin": "Unpin reference",
      "compare.replace": "Replace reference",
      "compare.none": "No reference pinned",
      "compare.pinned": "Reference {id} pinned",
      "compare.sectionLabel": "Reference snapshot",
      "compare.heading": "Route and result comparison",
      "compare.clear": "Clear reference",
      "compare.roleReference": "Pinned reference",
      "compare.roleCurrent": "Current selection",
      "compare.outputLabel": "Public result",
      "field.lane": "Lane",
      "field.vendor": "Vendor",
      "field.model": "Model",
      "field.state": "State",
      "field.effort": "Effort",
      "field.mode": "Mode",
      "field.watchdog": "Watchdog",
      "field.candidate": "Candidate",
      "field.started": "Started",
      "field.workdir": "Workdir",
      "task.sectionLabel": "Task",
      "task.heading": "What the model was asked",
      "output.sectionLabel": "Output",
      "output.heading": "Public result",
      "routing.sectionLabel": "Routing",
      "routing.heading": "Model path",
      "meta.summary": "Technical metadata",
      "meta.ariaLabel": "Task metadata",
      "value.unknown": "Unknown",
      "value.notRecorded": "Not recorded",
      "value.unknownLane": "Unknown lane",
      "value.unknownVendor": "Unknown vendor",
      "value.unknownModel": "Unknown model",
      "value.seconds": "{n} s",
      "loading.task": "Loading task…",
      "loading.output": "Loading output…",
      "loading.running": "Worker is running. Waiting for output…",
      "result.starting": "Dispatch is starting. No public result has been recorded yet.",
      "result.running": "Worker is running. The public result will appear here.",
      "result.failedCode": "Dispatch failed with exit code {code}. No public result was recorded.",
      "result.failedUnknown": "Dispatch failed with an unknown code. No public result was recorded.",
      "result.dead": "The worker is gone and no exit code was recorded.",
      "result.invalid": "This dispatch contains invalid metadata or control files. No safe result is available.",
      "result.none": "Dispatch completed without a public result.",
      "result.unsafe": "The public result could not be read safely.",
      "task.unsafe": "The task could not be read safely.",
      "task.none": "No task was recorded.",
      "marker.truncated": "Large content shortened to 512 KiB",
      "marker.taskUnsafe": "Task file could not be read safely",
      "marker.resultUnsafe": "Result file could not be read safely",
      "marker.failedNoCode": "Dispatch failed without a readable exit code",
      "marker.failedCode": "Dispatch failed with exit code {code}",
      "marker.dead": "Worker gone · exit code not recorded",
      "marker.invalid": "Invalid dispatch · safe fields only",
      "marker.detailUnavailable": "Detail temporarily unavailable",
      "error.taskPreserved": "The last task snapshot is preserved while detail reloads.",
      "error.reconnecting": "Reconnecting to the local job board.",
      "jobState.starting": "starting",
      "jobState.running": "running",
      "jobState.succeeded": "succeeded",
      "jobState.failed": "failed",
      "jobState.dead": "dead",
      "jobState.invalid": "invalid",
      "noscript": "JavaScript is required to read this local board.",
    },
    ja: {
      "app.title": "Omnilane ライブボード",
      "header.liveJobs": "実行中のジョブ",
      "header.readOnly": "読み取り専用",
      "header.language": "言語",
      "connection.connecting": "接続中",
      "connection.live": "ローカル信号を受信中",
      "connection.reconnecting": "再接続中",
      "connection.unauthorized": "未認証",
      "index.sectionLabel": "最近の作業",
      "index.title": "タスク記録",
      "search.label": "タスクを検索",
      "search.placeholder": "タスク、ID、レーン、モデル",
      "filter.legend": "表示",
      "filter.all": "すべて",
      "filter.active": "実行中",
      "filter.done": "完了",
      "filter.issues": "問題あり",
      "list.ariaLabel": "最近のタスク",
      "list.waiting": "タスクを待機しています。",
      "list.empty": "タスクはまだありません。",
      "list.unauthorized": "新しいローカルリンクが必要です。",
      "list.noMatch": "このフィルターに一致するタスクはありません。",
      "count.jobs": { other: "{n} 件のジョブ" },
      "count.filtered": "{shown} / {total} 件のジョブ",
      "inspector.ariaLabel": "選択したタスクの詳細",
      "state.local.code": "ローカル · 読み取り専用",
      "state.connecting.title": "ジョブボードに接続しています",
      "state.connecting.message": "最新のタスクがここに表示されます。",
      "state.unauthorized.title": "ローカルアクセスが必要です",
      "state.unauthorized.message": "`omnilane ui url` を実行して新しいローカルリンクを取得してください。",
      "state.empty.code": "キュー · 空",
      "state.empty.title": "タスクはまだありません",
      "state.empty.message": "Omnilane タスクを実行してください。このボードは自動的に更新されます。",
      "state.noMatch.code": "フィルター · 一致なし",
      "state.noMatch.title": "一致するタスクがありません",
      "state.noMatch.message": "検索条件を消すか、別の状態を選んでください。",
      "state.retry.code": "リンク · 再試行中",
      "state.retry.title": "ローカルボードに接続できません",
      "state.retry.message": "ローカルジョブボードに再接続しています。",
      "mobile.back": "タスク一覧に戻る",
      "detail.sectionLabel": "選択したタスク",
      "detail.timeStarted": "開始 {value}",
      "detail.timeUnavailable": "時刻不明",
      "detail.timeUnknown": "開始 時刻不明",
      "compare.pin": "比較用にピン留め",
      "compare.unpin": "ピン留めを解除",
      "compare.replace": "基準を差し替える",
      "compare.none": "基準は未設定",
      "compare.pinned": "基準 {id} をピン留め中",
      "compare.sectionLabel": "基準スナップショット",
      "compare.heading": "ルートと結果の比較",
      "compare.clear": "基準をクリア",
      "compare.roleReference": "ピン留めした基準",
      "compare.roleCurrent": "現在の選択",
      "compare.outputLabel": "公開結果",
      "field.lane": "レーン",
      "field.vendor": "ベンダー",
      "field.model": "モデル",
      "field.state": "状態",
      "field.effort": "エフォート",
      "field.mode": "モード",
      "field.watchdog": "ウォッチドッグ",
      "field.candidate": "候補",
      "field.started": "開始時刻",
      "field.workdir": "作業ディレクトリ",
      "task.sectionLabel": "タスク",
      "task.heading": "モデルへの依頼内容",
      "output.sectionLabel": "出力",
      "output.heading": "公開結果",
      "routing.sectionLabel": "ルーティング",
      "routing.heading": "モデル経路",
      "meta.summary": "技術メタデータ",
      "meta.ariaLabel": "タスクのメタデータ",
      "value.unknown": "不明",
      "value.notRecorded": "記録なし",
      "value.unknownLane": "レーン不明",
      "value.unknownVendor": "ベンダー不明",
      "value.unknownModel": "モデル不明",
      "value.seconds": "{n} 秒",
      "loading.task": "タスクを読み込み中…",
      "loading.output": "出力を読み込み中…",
      "loading.running": "ワーカーが実行中です。出力を待っています…",
      "result.starting": "ディスパッチを開始しています。公開結果はまだ記録されていません。",
      "result.running": "ワーカーが実行中です。公開結果はここに表示されます。",
      "result.failedCode": "ディスパッチは終了コード {code} で失敗しました。公開結果は記録されていません。",
      "result.failedUnknown": "ディスパッチは不明なコードで失敗しました。公開結果は記録されていません。",
      "result.dead": "ワーカーが消失し、終了コードは記録されていません。",
      "result.invalid": "このディスパッチには無効なメタデータまたは制御ファイルが含まれています。安全な結果はありません。",
      "result.none": "ディスパッチは公開結果なしで完了しました。",
      "result.unsafe": "公開結果を安全に読み取れませんでした。",
      "task.unsafe": "タスクを安全に読み取れませんでした。",
      "task.none": "タスクは記録されていません。",
      "marker.truncated": "大きな内容を 512 KiB に短縮しました",
      "marker.taskUnsafe": "タスクファイルを安全に読み取れませんでした",
      "marker.resultUnsafe": "結果ファイルを安全に読み取れませんでした",
      "marker.failedNoCode": "終了コードを読み取れないまま失敗しました",
      "marker.failedCode": "終了コード {code} で失敗しました",
      "marker.dead": "ワーカー消失 · 終了コード未記録",
      "marker.invalid": "無効なディスパッチ · 安全な項目のみ",
      "marker.detailUnavailable": "詳細を一時的に取得できません",
      "error.taskPreserved": "詳細の再読み込み中は直前のタスクを表示しています。",
      "error.reconnecting": "ローカルジョブボードに再接続しています。",
      "jobState.starting": "開始中",
      "jobState.running": "実行中",
      "jobState.succeeded": "成功",
      "jobState.failed": "失敗",
      "jobState.dead": "消失",
      "jobState.invalid": "無効",
      "noscript": "このローカルボードを表示するには JavaScript が必要です。",
    },
    ko: {
      "app.title": "Omnilane 라이브 보드",
      "header.liveJobs": "실행 중인 작업",
      "header.readOnly": "읽기 전용",
      "header.language": "언어",
      "connection.connecting": "연결 중",
      "connection.live": "로컬 신호 수신 중",
      "connection.reconnecting": "다시 연결 중",
      "connection.unauthorized": "인증되지 않음",
      "index.sectionLabel": "최근 작업",
      "index.title": "작업 기록",
      "search.label": "작업 찾기",
      "search.placeholder": "작업, ID, 레인, 모델",
      "filter.legend": "표시",
      "filter.all": "전체",
      "filter.active": "진행 중",
      "filter.done": "완료",
      "filter.issues": "문제",
      "list.ariaLabel": "최근 작업",
      "list.waiting": "작업을 기다리는 중입니다.",
      "list.empty": "아직 작업이 없습니다.",
      "list.unauthorized": "새 로컬 링크가 필요합니다.",
      "list.noMatch": "이 필터와 일치하는 작업이 없습니다.",
      "count.jobs": { other: "작업 {n}개" },
      "count.filtered": "작업 {shown} / {total}개",
      "inspector.ariaLabel": "선택한 작업 상세",
      "state.local.code": "로컬 · 읽기 전용",
      "state.connecting.title": "작업 보드에 연결하는 중",
      "state.connecting.message": "가장 최근 작업이 여기에 표시됩니다.",
      "state.unauthorized.title": "로컬 접근 권한이 필요합니다",
      "state.unauthorized.message": "`omnilane ui url` 을 실행해 새 로컬 링크를 받으세요.",
      "state.empty.code": "대기열 · 비어 있음",
      "state.empty.title": "아직 작업이 없습니다",
      "state.empty.message": "Omnilane 작업을 실행하세요. 이 보드는 자동으로 갱신됩니다.",
      "state.noMatch.code": "필터 · 일치 없음",
      "state.noMatch.title": "일치하는 작업이 없습니다",
      "state.noMatch.message": "검색어를 지우거나 다른 상태를 선택하세요.",
      "state.retry.code": "링크 · 재시도 중",
      "state.retry.title": "로컬 보드를 사용할 수 없습니다",
      "state.retry.message": "로컬 작업 보드에 다시 연결하는 중입니다.",
      "mobile.back": "작업 목록으로",
      "detail.sectionLabel": "선택한 작업",
      "detail.timeStarted": "시작 {value}",
      "detail.timeUnavailable": "시각 불명",
      "detail.timeUnknown": "시작 시각 불명",
      "compare.pin": "비교용으로 고정",
      "compare.unpin": "고정 해제",
      "compare.replace": "기준 교체",
      "compare.none": "고정된 기준 없음",
      "compare.pinned": "기준 {id} 고정됨",
      "compare.sectionLabel": "기준 스냅샷",
      "compare.heading": "경로와 결과 비교",
      "compare.clear": "기준 지우기",
      "compare.roleReference": "고정된 기준",
      "compare.roleCurrent": "현재 선택",
      "compare.outputLabel": "공개 결과",
      "field.lane": "레인",
      "field.vendor": "벤더",
      "field.model": "모델",
      "field.state": "상태",
      "field.effort": "노력 수준",
      "field.mode": "모드",
      "field.watchdog": "워치독",
      "field.candidate": "후보",
      "field.started": "시작 시각",
      "field.workdir": "작업 디렉터리",
      "task.sectionLabel": "작업",
      "task.heading": "모델에 요청한 내용",
      "output.sectionLabel": "출력",
      "output.heading": "공개 결과",
      "routing.sectionLabel": "라우팅",
      "routing.heading": "모델 경로",
      "meta.summary": "기술 메타데이터",
      "meta.ariaLabel": "작업 메타데이터",
      "value.unknown": "알 수 없음",
      "value.notRecorded": "기록 없음",
      "value.unknownLane": "레인 불명",
      "value.unknownVendor": "벤더 불명",
      "value.unknownModel": "모델 불명",
      "value.seconds": "{n}초",
      "loading.task": "작업을 불러오는 중…",
      "loading.output": "출력을 불러오는 중…",
      "loading.running": "워커가 실행 중입니다. 출력을 기다리는 중…",
      "result.starting": "디스패치를 시작하는 중입니다. 아직 기록된 공개 결과가 없습니다.",
      "result.running": "워커가 실행 중입니다. 공개 결과가 여기에 표시됩니다.",
      "result.failedCode": "디스패치가 종료 코드 {code}로 실패했습니다. 공개 결과가 기록되지 않았습니다.",
      "result.failedUnknown": "디스패치가 알 수 없는 코드로 실패했습니다. 공개 결과가 기록되지 않았습니다.",
      "result.dead": "워커가 사라졌고 종료 코드가 기록되지 않았습니다.",
      "result.invalid": "이 디스패치에는 잘못된 메타데이터나 제어 파일이 있습니다. 안전한 결과가 없습니다.",
      "result.none": "디스패치가 공개 결과 없이 완료되었습니다.",
      "result.unsafe": "공개 결과를 안전하게 읽을 수 없습니다.",
      "task.unsafe": "작업 내용을 안전하게 읽을 수 없습니다.",
      "task.none": "기록된 작업이 없습니다.",
      "marker.truncated": "큰 내용을 512 KiB로 줄였습니다",
      "marker.taskUnsafe": "작업 파일을 안전하게 읽을 수 없습니다",
      "marker.resultUnsafe": "결과 파일을 안전하게 읽을 수 없습니다",
      "marker.failedNoCode": "종료 코드를 읽지 못한 채 실패했습니다",
      "marker.failedCode": "종료 코드 {code}로 실패했습니다",
      "marker.dead": "워커 사라짐 · 종료 코드 미기록",
      "marker.invalid": "잘못된 디스패치 · 안전한 항목만",
      "marker.detailUnavailable": "상세 정보를 일시적으로 볼 수 없습니다",
      "error.taskPreserved": "상세 정보를 다시 불러오는 동안 마지막 작업 스냅샷을 유지합니다.",
      "error.reconnecting": "로컬 작업 보드에 다시 연결하는 중입니다.",
      "jobState.starting": "시작 중",
      "jobState.running": "실행 중",
      "jobState.succeeded": "성공",
      "jobState.failed": "실패",
      "jobState.dead": "사라짐",
      "jobState.invalid": "잘못됨",
      "noscript": "이 로컬 보드를 읽으려면 JavaScript가 필요합니다.",
    },
    "zh-TW": {
      "app.title": "Omnilane 即時看板",
      "header.liveJobs": "進行中的工作",
      "header.readOnly": "唯讀",
      "header.language": "語言",
      "connection.connecting": "連線中",
      "connection.live": "本機訊號即時連線",
      "connection.reconnecting": "重新連線中",
      "connection.unauthorized": "未授權",
      "index.sectionLabel": "最近的工作",
      "index.title": "任務紀錄",
      "search.label": "尋找任務",
      "search.placeholder": "任務、ID、車道、模型",
      "filter.legend": "顯示",
      "filter.all": "全部",
      "filter.active": "進行中",
      "filter.done": "完成",
      "filter.issues": "有問題",
      "list.ariaLabel": "最近的任務",
      "list.waiting": "等待任務中。",
      "list.empty": "尚無任務。",
      "list.unauthorized": "需要新的本機連結。",
      "list.noMatch": "沒有符合此篩選的任務。",
      "count.jobs": { other: "{n} 筆工作" },
      "count.filtered": "{shown} / {total} 筆工作",
      "inspector.ariaLabel": "所選任務的詳細內容",
      "state.local.code": "本機 · 唯讀",
      "state.connecting.title": "正在連線到工作看板",
      "state.connecting.message": "最新的任務會顯示在這裡。",
      "state.unauthorized.title": "需要本機存取權",
      "state.unauthorized.message": "執行 `omnilane ui url` 取得新的本機連結。",
      "state.empty.code": "佇列 · 空的",
      "state.empty.title": "尚無任務",
      "state.empty.message": "執行一個 Omnilane 任務,此看板會自動更新。",
      "state.noMatch.code": "篩選 · 無相符",
      "state.noMatch.title": "沒有相符的任務",
      "state.noMatch.message": "清除搜尋條件或改選其他狀態。",
      "state.retry.code": "連線 · 重試中",
      "state.retry.title": "本機看板無法使用",
      "state.retry.message": "正在重新連線到本機工作看板。",
      "mobile.back": "返回任務清單",
      "detail.sectionLabel": "所選任務",
      "detail.timeStarted": "開始於 {value}",
      "detail.timeUnavailable": "時間不明",
      "detail.timeUnknown": "開始時間不明",
      "compare.pin": "釘選以比較",
      "compare.unpin": "取消釘選",
      "compare.replace": "更換參考",
      "compare.none": "未釘選參考",
      "compare.pinned": "已釘選參考 {id}",
      "compare.sectionLabel": "參考快照",
      "compare.heading": "路由與結果比較",
      "compare.clear": "清除參考",
      "compare.roleReference": "已釘選的參考",
      "compare.roleCurrent": "目前選取",
      "compare.outputLabel": "公開結果",
      "field.lane": "車道",
      "field.vendor": "廠商",
      "field.model": "模型",
      "field.state": "狀態",
      "field.effort": "運算力度",
      "field.mode": "模式",
      "field.watchdog": "看門狗",
      "field.candidate": "候選",
      "field.started": "開始時間",
      "field.workdir": "工作目錄",
      "task.sectionLabel": "任務",
      "task.heading": "詢問模型的內容",
      "output.sectionLabel": "輸出",
      "output.heading": "公開結果",
      "routing.sectionLabel": "路由",
      "routing.heading": "模型路徑",
      "meta.summary": "技術中繼資料",
      "meta.ariaLabel": "任務中繼資料",
      "value.unknown": "不明",
      "value.notRecorded": "未記錄",
      "value.unknownLane": "車道不明",
      "value.unknownVendor": "廠商不明",
      "value.unknownModel": "模型不明",
      "value.seconds": "{n} 秒",
      "loading.task": "載入任務中…",
      "loading.output": "載入輸出中…",
      "loading.running": "工作端執行中,等待輸出…",
      "result.starting": "派工正在啟動,尚未記錄公開結果。",
      "result.running": "工作端執行中,公開結果會顯示在這裡。",
      "result.failedCode": "派工失敗,結束代碼 {code}。沒有記錄公開結果。",
      "result.failedUnknown": "派工失敗,結束代碼不明。沒有記錄公開結果。",
      "result.dead": "工作端已消失,且沒有記錄結束代碼。",
      "result.invalid": "這筆派工含有無效的中繼資料或控制檔,沒有可安全顯示的結果。",
      "result.none": "派工完成,但沒有公開結果。",
      "result.unsafe": "無法安全讀取公開結果。",
      "task.unsafe": "無法安全讀取任務內容。",
      "task.none": "沒有記錄任務內容。",
      "marker.truncated": "過長內容已截短為 512 KiB",
      "marker.taskUnsafe": "無法安全讀取任務檔",
      "marker.resultUnsafe": "無法安全讀取結果檔",
      "marker.failedNoCode": "派工失敗,且讀不到結束代碼",
      "marker.failedCode": "派工失敗,結束代碼 {code}",
      "marker.dead": "工作端消失 · 未記錄結束代碼",
      "marker.invalid": "無效派工 · 僅顯示安全欄位",
      "marker.detailUnavailable": "暫時無法取得詳細內容",
      "error.taskPreserved": "重新載入詳細內容時,保留上一份任務快照。",
      "error.reconnecting": "正在重新連線到本機工作看板。",
      "jobState.starting": "啟動中",
      "jobState.running": "執行中",
      "jobState.succeeded": "成功",
      "jobState.failed": "失敗",
      "jobState.dead": "消失",
      "jobState.invalid": "無效",
      "noscript": "需要 JavaScript 才能閱讀這個本機看板。",
    },
    "zh-CN": {
      "app.title": "Omnilane 实时看板",
      "header.liveJobs": "进行中的作业",
      "header.readOnly": "只读",
      "header.language": "语言",
      "connection.connecting": "连接中",
      "connection.live": "本地信号实时连接",
      "connection.reconnecting": "重新连接中",
      "connection.unauthorized": "未授权",
      "index.sectionLabel": "最近的工作",
      "index.title": "任务记录",
      "search.label": "查找任务",
      "search.placeholder": "任务、ID、车道、模型",
      "filter.legend": "显示",
      "filter.all": "全部",
      "filter.active": "进行中",
      "filter.done": "完成",
      "filter.issues": "有问题",
      "list.ariaLabel": "最近的任务",
      "list.waiting": "等待任务中。",
      "list.empty": "暂无任务。",
      "list.unauthorized": "需要新的本地链接。",
      "list.noMatch": "没有符合此筛选的任务。",
      "count.jobs": { other: "{n} 个作业" },
      "count.filtered": "{shown} / {total} 个作业",
      "inspector.ariaLabel": "所选任务的详细内容",
      "state.local.code": "本地 · 只读",
      "state.connecting.title": "正在连接到作业看板",
      "state.connecting.message": "最新的任务会显示在这里。",
      "state.unauthorized.title": "需要本地访问权限",
      "state.unauthorized.message": "运行 `omnilane ui url` 获取新的本地链接。",
      "state.empty.code": "队列 · 空",
      "state.empty.title": "暂无任务",
      "state.empty.message": "运行一个 Omnilane 任务,此看板会自动更新。",
      "state.noMatch.code": "筛选 · 无匹配",
      "state.noMatch.title": "没有匹配的任务",
      "state.noMatch.message": "清除搜索条件或改选其他状态。",
      "state.retry.code": "连接 · 重试中",
      "state.retry.title": "本地看板不可用",
      "state.retry.message": "正在重新连接到本地作业看板。",
      "mobile.back": "返回任务列表",
      "detail.sectionLabel": "所选任务",
      "detail.timeStarted": "开始于 {value}",
      "detail.timeUnavailable": "时间不详",
      "detail.timeUnknown": "开始时间不详",
      "compare.pin": "固定以比较",
      "compare.unpin": "取消固定",
      "compare.replace": "更换参考",
      "compare.none": "未固定参考",
      "compare.pinned": "已固定参考 {id}",
      "compare.sectionLabel": "参考快照",
      "compare.heading": "路由与结果比较",
      "compare.clear": "清除参考",
      "compare.roleReference": "已固定的参考",
      "compare.roleCurrent": "当前选择",
      "compare.outputLabel": "公开结果",
      "field.lane": "车道",
      "field.vendor": "厂商",
      "field.model": "模型",
      "field.state": "状态",
      "field.effort": "算力档位",
      "field.mode": "模式",
      "field.watchdog": "看门狗",
      "field.candidate": "候选",
      "field.started": "开始时间",
      "field.workdir": "工作目录",
      "task.sectionLabel": "任务",
      "task.heading": "向模型提出的内容",
      "output.sectionLabel": "输出",
      "output.heading": "公开结果",
      "routing.sectionLabel": "路由",
      "routing.heading": "模型路径",
      "meta.summary": "技术元数据",
      "meta.ariaLabel": "任务元数据",
      "value.unknown": "未知",
      "value.notRecorded": "未记录",
      "value.unknownLane": "车道未知",
      "value.unknownVendor": "厂商未知",
      "value.unknownModel": "模型未知",
      "value.seconds": "{n} 秒",
      "loading.task": "正在加载任务…",
      "loading.output": "正在加载输出…",
      "loading.running": "工作进程运行中,等待输出…",
      "result.starting": "派发正在启动,尚未记录公开结果。",
      "result.running": "工作进程运行中,公开结果会显示在这里。",
      "result.failedCode": "派发失败,退出码 {code}。未记录公开结果。",
      "result.failedUnknown": "派发失败,退出码未知。未记录公开结果。",
      "result.dead": "工作进程已消失,且未记录退出码。",
      "result.invalid": "这条派发含有无效的元数据或控制文件,没有可安全显示的结果。",
      "result.none": "派发完成,但没有公开结果。",
      "result.unsafe": "无法安全读取公开结果。",
      "task.unsafe": "无法安全读取任务内容。",
      "task.none": "未记录任务内容。",
      "marker.truncated": "过长内容已截断为 512 KiB",
      "marker.taskUnsafe": "无法安全读取任务文件",
      "marker.resultUnsafe": "无法安全读取结果文件",
      "marker.failedNoCode": "派发失败,且读不到退出码",
      "marker.failedCode": "派发失败,退出码 {code}",
      "marker.dead": "工作进程消失 · 未记录退出码",
      "marker.invalid": "无效派发 · 仅显示安全字段",
      "marker.detailUnavailable": "暂时无法获取详细内容",
      "error.taskPreserved": "重新加载详细内容时,保留上一份任务快照。",
      "error.reconnecting": "正在重新连接到本地作业看板。",
      "jobState.starting": "启动中",
      "jobState.running": "运行中",
      "jobState.succeeded": "成功",
      "jobState.failed": "失败",
      "jobState.dead": "消失",
      "jobState.invalid": "无效",
      "noscript": "需要 JavaScript 才能查看这个本地看板。",
    },
  };

  function start() {
    initializeLanguage();
    initializeHistory();
    bindControls();
    if (!state.token) {
      showUnauthorized();
      return;
    }

    setControlsDisabled(false);
    setConnection("waiting", "connection.connecting");
    openEventStream();
    loadInitialSnapshot();
  }

  start();
})();
