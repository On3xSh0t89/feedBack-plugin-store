(() => {
  'use strict';

  const GLOBAL_KEY = "__feedBackPluginStore";
  const API = "/api/plugins/plugin_store";
  const state = window[GLOBAL_KEY] || {
    bound: false,
    busy: new Set(),
    restartRequired: false,
    restarting: false,
    catalog: null,
    sidebarObserver: null,
    selfUpdating: false,
    searchQuery: "",
    filter: "all",
    versionContext: null,
  };
  window[GLOBAL_KEY] = state;

  function elements() {
    return {
      app: document.getElementById("plugin-store-app"),
      list: document.getElementById("plugin-store-list"),
      banner: document.getElementById("plugin-store-banner"),
      updateBanner: document.getElementById("plugin-store-update-banner"),
      updateTitle: document.getElementById("plugin-store-update-title"),
      updateDetail: document.getElementById("plugin-store-update-detail"),
      updateLink: document.getElementById("plugin-store-update-link"),
      refresh: document.getElementById("plugin-store-refresh"),
      restart: document.getElementById("plugin-store-restart"),
      search: document.getElementById("plugin-store-search"),
      filters: document.getElementById("plugin-store-filters"),
      updateAll: document.getElementById("plugin-store-update-all"),
      summaryInstalled: document.getElementById("plugin-store-summary-installed"),
      summaryUpdates: document.getElementById("plugin-store-summary-updates"),
      summaryAvailable: document.getElementById("plugin-store-summary-available"),
      addStore: document.getElementById("plugin-store-add-store"),
      installGithub: document.getElementById("plugin-store-install-github"),
      root: document.getElementById("plugin-store-root"),
      host: document.getElementById("plugin-store-host"),
      dialog: document.getElementById("plugin-store-add-dialog"),
      addForm: document.getElementById("plugin-store-add-form"),
      addUrl: document.getElementById("plugin-store-add-url"),
      addAck: document.getElementById("plugin-store-add-ack"),
      addSubmit: document.getElementById("plugin-store-add-submit"),
      addError: document.getElementById("plugin-store-add-error"),
      addClose: document.getElementById("plugin-store-add-close"),
      addCancel: document.getElementById("plugin-store-add-cancel"),
      githubDialog: document.getElementById("plugin-store-github-dialog"),
      githubForm: document.getElementById("plugin-store-github-form"),
      githubUrl: document.getElementById("plugin-store-github-url"),
      githubAck: document.getElementById("plugin-store-github-ack"),
      githubSubmit: document.getElementById("plugin-store-github-submit"),
      githubError: document.getElementById("plugin-store-github-error"),
      githubClose: document.getElementById("plugin-store-github-close"),
      githubCancel: document.getElementById("plugin-store-github-cancel"),
      versionsDialog: document.getElementById("plugin-store-versions-dialog"),
      versionsTitle: document.getElementById("plugin-store-versions-title"),
      versionsRepo: document.getElementById("plugin-store-versions-repo"),
      versionsList: document.getElementById("plugin-store-versions-list"),
      versionsClose: document.getElementById("plugin-store-versions-close"),
    };
  }

  function sidebarStoreIcon() {
    return `
      <svg class="w-5 h-5 shrink-0" fill="none" stroke="currentColor"
           stroke-width="1.8" viewBox="0 0 24 24" aria-hidden="true">
        <path stroke-linecap="round" stroke-linejoin="round"
              d="M4 7h16l-1 13H5L4 7zm3 0V5a5 5 0 0110 0v2M8 11h.01M12 11h.01M16 11h.01"/>
      </svg>`;
  }

  function syncSidebarEntryActive() {
    const entry = document.getElementById("plugin-store-sidebar-entry");
    const pluginScreen = document.getElementById("plugin-plugin_store");
    if (!entry || !pluginScreen) return;

    const active = pluginScreen.classList.contains("active");
    entry.classList.toggle("bg-fb-card", active);
    entry.classList.toggle("text-fb-text", active);
    entry.classList.toggle("text-fb-textDim", !active);
  }

  function ensureSidebarEntry() {
    const nav = document.getElementById("v3-nav");
    if (!nav) return;

    if (document.getElementById("plugin-store-sidebar-entry")) {
      syncSidebarEntryActive();
      return;
    }

    const pluginsEntry = nav.querySelector('[data-v3-nav="plugins"]');
    if (!pluginsEntry) return;

    const entry = document.createElement("a");
    entry.id = "plugin-store-sidebar-entry";
    entry.href = "#/plugin-store";
    entry.className =
      "flex items-center gap-3 px-3 py-2 rounded-lg text-sm text-fb-textDim " +
      "hover:text-fb-text hover:bg-fb-card/50 transition-colors";
    entry.innerHTML =
      sidebarStoreIcon() +
      '<span class="truncate v3-nav-label">Plugin Store</span>';

    entry.addEventListener("click", (event) => {
      event.preventDefault();
      if (typeof window.showScreen === "function") {
        window.showScreen("plugin-plugin_store");
      }
      syncSidebarEntryActive();
    });

    pluginsEntry.insertAdjacentElement("afterend", entry);
    syncSidebarEntryActive();
  }

  async function checkSelfUpdate(forceRefresh = false) {
    const {
      updateBanner,
      updateTitle,
      updateDetail,
      updateLink,
    } = elements();

    if (!updateBanner) return;

    try {
      const status = await api(
        `/self-update?refresh=${forceRefresh ? "true" : "false"}`
      );

      if (!status.update_available) {
        updateBanner.hidden = true;
        return;
      }

      const installed = status.installed_version || "unknown";
      const available = status.available_version || "newer";

      if (updateTitle) {
        updateTitle.textContent = `Plugin Store ${available} is available`;
      }
      if (updateDetail) {
        updateDetail.textContent =
          `You are running ${installed}. Update and restart feedBack to apply ${available}.`;
      }
      if (updateLink) {
        updateLink.disabled = state.selfUpdating;
        updateLink.textContent = state.selfUpdating
          ? "Updating…"
          : "Update Plugin Store";
      }

      updateBanner.hidden = false;
    } catch (_) {
      // Advisory only: a failed GitHub check must never break the catalog.
      updateBanner.hidden = true;
    }
  }

  function setBanner(message, kind = "info") {
    const { banner } = elements();
    if (!banner) return;
    if (!message) {
      banner.hidden = true;
      banner.textContent = "";
      banner.dataset.kind = "";
      return;
    }
    banner.hidden = false;
    banner.dataset.kind = kind;
    banner.textContent = message;
  }

  async function api(path, options = {}) {
    const headers = new Headers(options.headers || {});
    if (options.method && options.method !== "GET") {
      headers.set("X-FeedBack-Plugin-Store", "1");
    }
    if (options.body && !headers.has("Content-Type")) {
      headers.set("Content-Type", "application/json");
    }

    const response = await fetch(`${API}${path}`, {
      ...options,
      headers,
      credentials: "same-origin",
      cache: "no-store",
    });

    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const message =
        payload && typeof payload.detail === "string"
          ? payload.detail
          : `Request failed with HTTP ${response.status}`;
      throw new Error(message);
    }
    return payload;
  }

  function syncRestartButton() {
    const { restart } = elements();
    if (!restart) return;
    restart.hidden = !state.restartRequired;
    restart.disabled = state.restarting;
    restart.textContent = state.restarting ? "Restarting…" : "Restart feedBack";
  }

  function sleep(ms) {
    return new Promise((resolve) => window.setTimeout(resolve, ms));
  }

  async function waitForNewInstance(oldInstanceId, timeoutMs = 60000) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      await sleep(1000);
      try {
        const response = await fetch(`${API}/instance?_=${Date.now()}`, {
          method: "GET",
          cache: "no-store",
          credentials: "same-origin",
        });
        if (!response.ok) continue;
        const payload = await response.json();
        if (payload.instance_id && payload.instance_id !== oldInstanceId) {
          return true;
        }
      } catch (_) {
        // Expected while Docker is between the old and new feedBack process.
      }
    }
    return false;
  }

  async function refreshPluginStoreFrontendAssets(expectedVersion) {
    // Some feedBack builds keep plugin screen HTML / classic JS cached across
    // a backend restart. Force-refresh the exact URLs the host loader uses so
    // the subsequent full-page navigation cannot resurrect the old Plugin
    // Store frontend from HTTP cache.
    const urls = [
      "/api/plugins/plugin_store/screen.html",
      "/api/plugins/plugin_store/screen.js",
      "/api/plugins/plugin_store/settings.html",
      "/api/plugins/plugin_store/assets/plugin.css",
    ];

    // Wait briefly for the restarted backend to report the expected plugin
    // version before refreshing assets. Plugin registration is incremental.
    const deadline = Date.now() + 15000;
    while (Date.now() < deadline) {
      try {
        const response = await fetch(`/api/plugins?_=${Date.now()}`, {
          cache: "no-store",
          credentials: "same-origin",
        });
        if (response.ok) {
          const plugins = await response.json();
          const self = Array.isArray(plugins)
            ? plugins.find((plugin) => plugin && plugin.id === "plugin_store")
            : null;
          if (!expectedVersion || (self && self.version === expectedVersion)) {
            break;
          }
        }
      } catch (_) {
        // Backend may still be completing plugin registration.
      }
      await sleep(500);
    }

    await Promise.allSettled(
      urls.map((url) =>
        fetch(url, {
          method: "GET",
          cache: "reload",
          credentials: "same-origin",
          headers: {
            "Cache-Control": "no-cache",
          },
        })
      )
    );
  }

  function hardReloadAfterSelfUpdate(version) {
    const url = new URL(window.location.href);
    url.searchParams.set(
      "_plugin_store_reload",
      `${version || "updated"}-${Date.now()}`
    );
    window.location.replace(url.toString());
  }

  async function updatePluginStore() {
    if (state.selfUpdating || state.restarting) return;

    state.selfUpdating = true;
    const { updateLink } = elements();
    if (updateLink) {
      updateLink.disabled = true;
      updateLink.textContent = "Updating…";
    }
    setBanner(
      "Downloading and validating the Plugin Store update…",
      "info"
    );

    try {
      const result = await api("/self-update/install", { method: "POST" });
      state.restarting = true;
      syncRestartButton();
      if (updateLink) updateLink.textContent = "Restarting…";
      setBanner(
        `Plugin Store ${result.version || "update"} installed. Restarting feedBack…`,
        "success"
      );

      const restarted = await waitForNewInstance(result.instance_id);
      if (restarted) {
        setBanner("feedBack is back. Refreshing Plugin Store assets…", "success");
        await refreshPluginStoreFrontendAssets(result.version);
        hardReloadAfterSelfUpdate(result.version);
        return;
      }

      setBanner(
        "The Plugin Store was updated, but feedBack did not return within 60 seconds. Check the container restart policy and logs.",
        "error"
      );
    } catch (error) {
      setBanner(error.message || String(error), "error");
      state.selfUpdating = false;
      state.restarting = false;
      syncRestartButton();
      if (updateLink) {
        updateLink.disabled = false;
        updateLink.textContent = "Update Plugin Store";
      }
    }
  }

  async function restartFeedBack(confirmRestart = true) {
    if (state.restarting) return;
    if (confirmRestart && !window.confirm(
      "Restart feedBack now? The page will wait for the container to return and then reload automatically."
    )) return;

    state.restarting = true;
    syncRestartButton();
    setBanner("Restarting feedBack… waiting for the server to come back online.", "info");

    try {
      const result = await api("/restart", { method: "POST" });
      const restarted = await waitForNewInstance(result.instance_id);
      if (restarted) {
        setBanner("feedBack is back. Reloading…", "success");
        await sleep(250);
        window.location.reload();
        return;
      }
      setBanner(
        "feedBack did not return within 60 seconds. Check the container restart policy and logs.",
        "error"
      );
    } catch (error) {
      setBanner(error.message || String(error), "error");
    } finally {
      state.restarting = false;
      syncRestartButton();
    }
  }

  function openSettingsPanel(pluginId, tries = 16) {
    let target = null;
    const all = document.querySelectorAll("#plugin-settings details[data-plugin-id]");
    for (const details of all) {
      if (details.getAttribute("data-plugin-id") === pluginId) {
        target = details;
        break;
      }
    }
    if (target) {
      target.open = true;
      try {
        target.scrollIntoView({ behavior: "smooth", block: "center" });
      } catch (_) {
        target.scrollIntoView();
      }
      return;
    }
    if (tries > 0) {
      window.requestAnimationFrame(() => openSettingsPanel(pluginId, tries - 1));
    }
  }

  function openPluginSettings(plugin) {
    if (!plugin || !plugin.installed || !plugin.has_settings) return;
    if (typeof window.showScreen === "function") {
      window.showScreen("settings");
      openSettingsPanel(plugin.id);
    }
  }

  function openPluginScreen(plugin) {
    if (!plugin || !plugin.installed || !plugin.has_screen) return;
    const screenId = plugin.nav_screen || `plugin-${plugin.id}`;
    if (typeof window.showScreen !== "function") return;

    if (document.getElementById(screenId)) {
      window.showScreen(screenId);
      return;
    }
    if (plugin.has_settings) {
      openPluginSettings(plugin);
      return;
    }
    setBanner(`${plugin.name} is still loading. Try opening it again in a moment.`, "warning");
  }

  function openInstalledPlugin(plugin) {
    if (!plugin || !plugin.installed) return;
    if (plugin.has_settings) openPluginSettings(plugin);
    else if (plugin.has_screen) openPluginScreen(plugin);
  }

  function actionButton(label, className, disabled, handler, title = "") {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `plugin-store__button ${className || ""}`.trim();
    button.textContent = label;
    button.disabled = Boolean(disabled);
    if (title) button.title = title;
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      handler(event);
    });
    return button;
  }

  function badge(text, kind = "default") {
    const el = document.createElement("span");
    el.className = `plugin-store__badge plugin-store__badge--${kind}`;
    el.textContent = text;
    return el;
  }

  function pluginMatchesView(plugin, store) {
    const filter = state.filter || "all";
    if (filter === "installed" && !plugin.installed) return false;
    if (
      filter === "updates" &&
      !(plugin.status === "update_available" && plugin.can_update)
    ) return false;
    if (filter === "available" && plugin.installed) return false;

    const query = (state.searchQuery || "").trim().toLowerCase();
    if (!query) return true;

    const haystack = [
      plugin.id,
      plugin.name,
      plugin.description,
      plugin.repository,
      store && store.name,
    ]
      .filter(Boolean)
      .join(" ")
      .toLowerCase();

    return haystack.includes(query);
  }

  function catalogSummary(data) {
    const seen = new Map();

    for (const store of (data && data.stores) || []) {
      for (const plugin of store.plugins || []) {
        if (!plugin || !plugin.id) continue;

        // Prefer an installed/update entry when duplicate ids appear across stores.
        const existing = seen.get(plugin.id);
        if (
          !existing ||
          plugin.installed ||
          plugin.status === "update_available"
        ) {
          seen.set(plugin.id, plugin);
        }
      }
    }

    let installed = 0;
    let updates = 0;
    let available = 0;

    for (const plugin of seen.values()) {
      if (plugin.installed) installed += 1;
      if (plugin.status === "update_available" && plugin.can_update) updates += 1;
      if (!plugin.installed && plugin.can_install) available += 1;
    }

    return { installed, updates, available };
  }

  function renderCatalogSummary(data) {
    const {
      summaryInstalled,
      summaryUpdates,
      summaryAvailable,
    } = elements();

    const summary = catalogSummary(data);

    if (summaryInstalled) summaryInstalled.textContent = String(summary.installed);
    if (summaryUpdates) summaryUpdates.textContent = String(summary.updates);
    if (summaryAvailable) summaryAvailable.textContent = String(summary.available);
  }

  function availableUpdates(data) {
    const updates = [];
    for (const store of (data && data.stores) || []) {
      for (const plugin of store.plugins || []) {
        if (
          plugin.status === "update_available" &&
          plugin.can_update &&
          !plugin.excluded
        ) {
          updates.push({ store, plugin });
        }
      }
    }
    return updates;
  }

  function syncUpdateAllButton() {
    const { updateAll } = elements();
    if (!updateAll) return;
    const updates = availableUpdates(state.catalog);
    const busy = state.busy.has("update-all");
    updateAll.hidden = updates.length === 0;
    updateAll.disabled = busy;
    updateAll.textContent = busy
      ? "Updating…"
      : `Update All (${updates.length})`;
  }

  function statusLabel(plugin) {
    switch (plugin.status) {
      case "installed":
        return `Installed ${plugin.installed_version || ""}`.trim();
      case "update_available":
        return `Update available: ${plugin.installed_version || "?"} → ${plugin.version}`;
      case "local_newer":
        return `Installed ${plugin.installed_version || "?"} (newer than catalog)`;
      case "installed_external":
        return `Installed ${plugin.installed_version || ""} — not managed by this store`.trim();
      case "incompatible":
        return "Not compatible with this feedBack version";
      case "broken":
        return "Installed, but manifest validation failed";
      default:
        return `Available ${plugin.version}`;
    }
  }

  function thirdPartyInstallWarning(plugin, store) {
    return [
      `Install ${plugin.name} from third-party store “${store.name}”?`,
      "",
      `Repository: ${plugin.repository}`,
      "",
      "This plugin can execute server-side and browser code with feedBack's permissions. Third-party plugins are not reviewed or endorsed by feedBack.",
      "",
      "Continue only if you trust this store and repository."
    ].join("\n");
  }

  async function mutate(plugin, store, operation) {
    const key = `${operation}:${store.id}:${plugin.id}`;
    if (state.busy.has(key)) return;

    if (operation === "remove") {
      if (!window.confirm(
        `Remove ${plugin.name}? The plugin files will be deleted. The change takes effect after feedBack restarts.`
      )) return;
    } else if (store.third_party) {
      if (!window.confirm(thirdPartyInstallWarning(plugin, store))) return;
    }

    state.busy.add(key);
    renderCatalog(state.catalog);
    setBanner(`${operation[0].toUpperCase()}${operation.slice(1)}ing ${plugin.name}…`);

    try {
      const method = operation === "remove" ? "DELETE" : "POST";
      const options = { method };
      if (method === "POST") {
        options.body = JSON.stringify({
          acknowledge_third_party: store.third_party === true,
        });
      }
      await api(
        `/${operation}/${encodeURIComponent(store.id)}/${encodeURIComponent(plugin.id)}`,
        options
      );
      state.restartRequired = true;
      syncRestartButton();
      setBanner(
        `${plugin.name}: ${operation} completed. Restart feedBack to apply the change.`,
        "success"
      );
      await loadCatalog(false);
    } catch (error) {
      setBanner(error.message || String(error), "error");
    } finally {
      state.busy.delete(key);
      renderCatalog(state.catalog);
    }
  }

  async function checkPlugin(plugin, store) {
    const key = `check:${store.id}:${plugin.id}`;
    if (state.busy.has(key)) return;
    state.busy.add(key);
    renderCatalog(state.catalog);
    setBanner(`Checking ${plugin.name}…`);

    try {
      const checked = await api(
        `/check/${encodeURIComponent(store.id)}/${encodeURIComponent(plugin.id)}`
      );
      setBanner(
        checked.status === "update_available"
          ? `${plugin.name}: update ${checked.version} is available.`
          : `${plugin.name} is up to date.`,
        checked.status === "update_available" ? "warning" : "success"
      );
      await loadCatalog(false);
    } catch (error) {
      setBanner(error.message || String(error), "error");
    } finally {
      state.busy.delete(key);
      renderCatalog(state.catalog);
    }
  }

  async function toggleExcluded(plugin) {
    const next = !plugin.excluded;
    const key = `exclude:${plugin.id}`;
    if (state.busy.has(key)) return;
    state.busy.add(key);
    renderCatalog(state.catalog);

    try {
      await api(`/exclude/${encodeURIComponent(plugin.id)}`, {
        method: "POST",
        body: JSON.stringify({ excluded: next }),
      });
      setBanner(
        next
          ? `${plugin.name} will be skipped by Update All.`
          : `${plugin.name} is included in Update All again.`,
        "success"
      );
      await loadCatalog(false);
    } catch (error) {
      setBanner(error.message || String(error), "error");
    } finally {
      state.busy.delete(key);
      renderCatalog(state.catalog);
    }
  }

  function closeVersionsDialog() {
    const { versionsDialog } = elements();
    state.versionContext = null;
    if (!versionsDialog) return;
    if (typeof versionsDialog.close === "function") versionsDialog.close();
    else versionsDialog.removeAttribute("open");
  }

  async function installVersion(plugin, store, item) {
    if (item.compatible === false) return;

    if (store.third_party) {
      if (!window.confirm(thirdPartyInstallWarning(plugin, store))) return;
    }

    const action = plugin.installed ? "Switch" : "Install";
    if (!window.confirm(
      `${action} ${plugin.name} ${item.version} (${item.label || item.ref})?\n\n` +
      (plugin.installed
        ? "The current version will be saved as a rollback snapshot. "
        : "") +
      "Restart feedBack after the change."
    )) return;

    const key = `version:${store.id}:${plugin.id}:${item.ref}`;
    if (state.busy.has(key)) return;
    state.busy.add(key);

    const { versionsList } = elements();
    if (versionsList) {
      const loading = document.createElement("div");
      loading.className = "plugin-store__loading";
      loading.textContent = `Installing ${plugin.name} ${item.version}…`;
      versionsList.replaceChildren(loading);
    }

    try {
      const result = await api(
        `/version/${encodeURIComponent(store.id)}/${encodeURIComponent(plugin.id)}`,
        {
          method: "POST",
          body: JSON.stringify({
            ref: item.ref,
            ref_kind: item.ref_kind,
            acknowledge_third_party: store.third_party === true,
          }),
        }
      );
      state.restartRequired = true;
      syncRestartButton();
      closeVersionsDialog();
      setBanner(
        `${plugin.name} ${result.version} installed. Restart feedBack to apply it.`,
        "success"
      );
      await loadCatalog(false);
    } catch (error) {
      setBanner(error.message || String(error), "error");
      closeVersionsDialog();
    } finally {
      state.busy.delete(key);
    }
  }

  function renderVersions(plugin, store, data) {
    const { versionsTitle, versionsRepo, versionsList } = elements();
    if (!versionsList) return;

    if (versionsTitle) versionsTitle.textContent = `${plugin.name} Versions`;
    if (versionsRepo) versionsRepo.textContent = data.repository || plugin.repository || "";
    versionsList.replaceChildren();

    const versions = Array.isArray(data.versions) ? data.versions : [];
    if (!versions.length) {
      const empty = document.createElement("div");
      empty.className = "plugin-store__loading";
      empty.textContent = "No tagged versions were found.";
      versionsList.append(empty);
      return;
    }

    for (const item of versions) {
      const row = document.createElement("div");
      row.className = "plugin-store__version-row";

      const info = document.createElement("div");
      info.className = "plugin-store__version-info";

      const version = document.createElement("strong");
      version.textContent = item.version || item.ref;
      info.append(version);

      const ref = document.createElement("span");
      ref.textContent =
        item.label === "Latest"
          ? `Latest · ${item.ref}`
          : `Git tag · ${item.label || item.ref}`;
      info.append(ref);

      if (item.compatibility_reason) {
        const reason = document.createElement("small");
        reason.className = "plugin-store__error";
        reason.textContent = item.compatibility_reason;
        info.append(reason);
      }

      const isInstalled =
        plugin.installed &&
        plugin.installed_version === item.version;

      const button = actionButton(
        isInstalled
          ? "Installed"
          : plugin.installed
            ? "Switch"
            : "Install",
        isInstalled
          ? "plugin-store__button--secondary"
          : "plugin-store__button--primary",
        isInstalled || item.compatible === false,
        () => installVersion(plugin, store, item)
      );

      row.append(info, button);
      versionsList.append(row);
    }
  }

  async function showVersions(plugin, store) {
    const {
      versionsDialog,
      versionsTitle,
      versionsRepo,
      versionsList,
    } = elements();
    if (!versionsDialog || !versionsList) return;

    state.versionContext = { plugin, store };
    if (versionsTitle) versionsTitle.textContent = `${plugin.name} Versions`;
    if (versionsRepo) versionsRepo.textContent = plugin.repository || "";
    const loading = document.createElement("div");
    loading.className = "plugin-store__loading";
    loading.textContent = "Loading GitHub versions…";
    versionsList.replaceChildren(loading);

    if (typeof versionsDialog.showModal === "function") versionsDialog.showModal();
    else versionsDialog.setAttribute("open", "");

    try {
      const data = await api(
        `/versions/${encodeURIComponent(store.id)}/${encodeURIComponent(plugin.id)}`
      );
      renderVersions(plugin, store, data);
    } catch (error) {
      versionsList.replaceChildren();
      const failure = document.createElement("div");
      failure.className = "plugin-store__dialog-error";
      failure.textContent = error.message || String(error);
      versionsList.append(failure);
    }
  }

  async function rollbackPlugin(plugin) {
    const version = plugin.rollback_version || "previous version";
    if (!window.confirm(
      `Roll back ${plugin.name} to ${version}?\n\nThe current version will be saved as another rollback snapshot. The change takes effect after feedBack restarts.`
    )) return;

    const key = `rollback:${plugin.id}`;
    if (state.busy.has(key)) return;
    state.busy.add(key);
    renderCatalog(state.catalog);
    setBanner(`Rolling back ${plugin.name}…`);

    try {
      const result = await api(`/rollback/${encodeURIComponent(plugin.id)}`, {
        method: "POST",
      });
      state.restartRequired = true;
      syncRestartButton();
      setBanner(
        `${plugin.name} rolled back to ${result.version || version}. Restart feedBack to apply it.`,
        "success"
      );
      await loadCatalog(false);
    } catch (error) {
      setBanner(error.message || String(error), "error");
    } finally {
      state.busy.delete(key);
      renderCatalog(state.catalog);
    }
  }

  async function updateAllPlugins() {
    const updates = availableUpdates(state.catalog);
    if (!updates.length || state.busy.has("update-all")) return;

    const thirdParty = updates.filter(({ store }) => store.third_party);
    const names = updates.map(({ plugin }) => plugin.name).join(", ");
    let message =
      `Update ${updates.length} plugin${updates.length === 1 ? "" : "s"}?\n\n${names}\n\n` +
      "A rollback snapshot will be created before each update. Restart feedBack once when all updates finish.";

    if (thirdParty.length) {
      message +=
        `\n\nWARNING: ${thirdParty.length} update${thirdParty.length === 1 ? "" : "s"} ` +
        "come from third-party stores. Those plugins can execute unreviewed code with feedBack's permissions.";
    }
    if (!window.confirm(message)) return;

    state.busy.add("update-all");
    syncUpdateAllButton();
    renderCatalog(state.catalog);
    setBanner(`Updating ${updates.length} plugins…`);

    try {
      const result = await api("/update-all", {
        method: "POST",
        body: JSON.stringify({
          acknowledge_third_party: thirdParty.length > 0,
        }),
      });

      if (result.updated_count > 0) {
        state.restartRequired = true;
        syncRestartButton();
      }

      if (result.failed_count > 0) {
        const failures = (result.failed || [])
          .map((item) => `${item.name || item.plugin_id}: ${item.error}`)
          .join("  ");
        setBanner(
          `Updated ${result.updated_count}; ${result.failed_count} failed. ${failures}`,
          "warning"
        );
      } else if (result.updated_count > 0) {
        setBanner(
          `Updated ${result.updated_count} plugin${result.updated_count === 1 ? "" : "s"}. Restart feedBack to apply the changes.`,
          "success"
        );
      } else {
        setBanner("No plugin updates are currently available.", "info");
      }

      await loadCatalog(false);
    } catch (error) {
      setBanner(error.message || String(error), "error");
    } finally {
      state.busy.delete("update-all");
      syncUpdateAllButton();
      renderCatalog(state.catalog);
    }
  }

  function renderPlugin(plugin, store) {
    const card = document.createElement("article");
    card.className = "plugin-store__card";

    const canOpenInstalled = plugin.installed && (plugin.has_settings || plugin.has_screen);
    if (canOpenInstalled) {
      card.classList.add("plugin-store__card--openable");
      card.tabIndex = 0;
      card.setAttribute("role", "button");
      card.setAttribute(
        "aria-label",
        plugin.has_settings ? `Open ${plugin.name} settings` : `Open ${plugin.name}`
      );
      card.addEventListener("click", () => openInstalledPlugin(plugin));
      card.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          openInstalledPlugin(plugin);
        }
      });
    }

    const body = document.createElement("div");
    body.className = "plugin-store__card-body";

    const titleRow = document.createElement("div");
    titleRow.className = "plugin-store__title-row";
    const title = document.createElement("h3");
    title.textContent = plugin.name;
    titleRow.append(title);
    if (store.third_party) titleRow.append(badge("THIRD-PARTY", "warning"));
    if (plugin.excluded) titleRow.append(badge("EXCLUDED", "warning"));
    body.append(titleRow);

    const description = document.createElement("p");
    description.className = "plugin-store__description";
    description.textContent = plugin.description || "No description provided.";
    body.append(description);

    const status = document.createElement("p");
    status.className = `plugin-store__status plugin-store__status--${plugin.status}`;
    status.textContent = statusLabel(plugin);
    body.append(status);

    if (store.third_party) {
      const repo = document.createElement("p");
      repo.className = "plugin-store__repo";
      repo.textContent = plugin.repository;
      body.append(repo);
    }

    if (plugin.compatibility_reason) {
      const compatibility = document.createElement("p");
      compatibility.className = "plugin-store__compatibility";
      compatibility.textContent = plugin.compatibility_reason;
      body.append(compatibility);
    }

    if (plugin.error) {
      const error = document.createElement("p");
      error.className = "plugin-store__error";
      error.textContent = plugin.error;
      body.append(error);
    }

    const actions = document.createElement("div");
    actions.className = "plugin-store__actions";

    if (!plugin.installed) {
      const busy = state.busy.has(`install:${store.id}:${plugin.id}`);
      const disabled = busy || !plugin.can_install;
      actions.append(
        actionButton(
          plugin.compatible === false ? "Incompatible" : "Install",
          "plugin-store__button--primary",
          disabled,
          () => mutate(plugin, store, "install"),
          plugin.compatibility_reason || ""
        )
      );
    } else {
      if (plugin.status === "update_available" || plugin.status === "broken") {
        const busy = state.busy.has(`update:${store.id}:${plugin.id}`);
        actions.append(
          actionButton(
            plugin.status === "broken" ? "Repair" : "Update",
            "plugin-store__button--primary",
            busy || !plugin.can_update,
            () => mutate(plugin, store, "update"),
            !plugin.can_update && store.third_party
              ? "Automatic update is blocked because this install is not managed by this store or is incompatible."
              : ""
          )
        );
      }

      if (plugin.rollback_available) {
        const rollbackBusy = state.busy.has(`rollback:${plugin.id}`);
        actions.append(
          actionButton(
            `Roll Back to ${plugin.rollback_version || "Previous"}`,
            "plugin-store__button--secondary",
            rollbackBusy,
            () => rollbackPlugin(plugin)
          )
        );
      }

      if (plugin.has_settings) {
        actions.append(
          actionButton(
            "Settings",
            "plugin-store__button--secondary",
            false,
            () => openPluginSettings(plugin)
          )
        );
      }
      if (plugin.has_screen) {
        actions.append(
          actionButton(
            "Open Plugin",
            "plugin-store__button--secondary",
            false,
            () => openPluginScreen(plugin)
          )
        );
      }
      if (plugin.can_remove) {
        const busy = state.busy.has(`remove:${store.id}:${plugin.id}`);
        actions.append(
          actionButton(
            "Remove",
            "plugin-store__button--danger",
            busy,
            () => mutate(plugin, store, "remove")
          )
        );
      }
    }

    const checkBusy = state.busy.has(`check:${store.id}:${plugin.id}`);
    actions.append(
      actionButton(
        checkBusy ? "Checking…" : "Check",
        "plugin-store__button--secondary",
        checkBusy,
        () => checkPlugin(plugin, store),
        "Check just this plugin for a newer version."
      )
    );

    actions.append(
      actionButton(
        "Versions",
        "plugin-store__button--secondary",
        false,
        () => showVersions(plugin, store),
        "View tagged GitHub versions and switch to a specific release."
      )
    );

    if (plugin.installed) {
      const excludeBusy = state.busy.has(`exclude:${plugin.id}`);
      actions.append(
        actionButton(
          plugin.excluded ? "Include in Update All" : "Exclude from Update All",
          "plugin-store__button--secondary",
          excludeBusy,
          () => toggleExcluded(plugin)
        )
      );
    }

    card.append(body, actions);
    return card;
  }

  async function removeThirdPartyStore(store) {
    const pluginCount = Array.isArray(store.plugins) ? store.plugins.length : 0;
    if (!window.confirm(
      `Remove third-party store “${store.name}”?\n\nThis removes the catalog source only. Any plugins already installed from it will remain installed.\n\nCatalog plugins: ${pluginCount}`
    )) return;

    try {
      const result = await api(`/stores/${encodeURIComponent(store.id)}`, {
        method: "DELETE",
      });
      const preserved = result.installed_plugins_preserved || [];
      const suffix = preserved.length
        ? ` Installed plugins were preserved: ${preserved.join(", ")}.`
        : "";
      setBanner(`Removed third-party store “${store.name}”.${suffix}`, "success");
      await loadCatalog(false);
    } catch (error) {
      setBanner(error.message || String(error), "error");
    }
  }

  function renderStore(store) {
    const section = document.createElement("section");
    section.className = "plugin-store__store";
    if (store.third_party) section.classList.add("plugin-store__store--third-party");

    const header = document.createElement("div");
    header.className = "plugin-store__store-header";

    const headingWrap = document.createElement("div");
    const headingLine = document.createElement("div");
    headingLine.className = "plugin-store__store-heading-line";
    const heading = document.createElement("h2");
    heading.textContent = store.name;
    headingLine.append(heading);
    headingLine.append(
      badge(store.third_party ? "THIRD-PARTY STORE" : "OFFICIAL", store.third_party ? "warning" : "official")
    );
    headingWrap.append(headingLine);

    if (store.description) {
      const description = document.createElement("p");
      description.className = "plugin-store__store-description";
      description.textContent = store.description;
      headingWrap.append(description);
    }

    if (store.url) {
      const source = document.createElement("p");
      source.className = "plugin-store__store-source";
      source.textContent = store.url;
      headingWrap.append(source);
    }
    header.append(headingWrap);

    if (store.third_party && !store.direct) {
      header.append(
        actionButton(
          "Remove Store",
          "plugin-store__button--danger",
          false,
          () => removeThirdPartyStore(store)
        )
      );
    }
    section.append(header);

    if (store.third_party) {
      const risk = document.createElement("div");
      risk.className = "plugin-store__third-party-notice";
      risk.textContent =
        "Third-party plugins are unreviewed and may execute code with feedBack's permissions. Verify the repository before installing.";
      section.append(risk);
    }

    if (store.warning) {
      const warning = document.createElement("div");
      warning.className = "plugin-store__store-warning";
      warning.textContent = store.warning;
      section.append(warning);
    }

    const grid = document.createElement("div");
    grid.className = "plugin-store__grid";
    const visiblePlugins = (store.plugins || []).filter((plugin) =>
      pluginMatchesView(plugin, store)
    );

    for (const plugin of visiblePlugins) {
      grid.append(renderPlugin(plugin, store));
    }

    if (visiblePlugins.length === 0) {
      if ((store.plugins || []).length === 0 && store.warning) {
        const empty = document.createElement("div");
        empty.className = "plugin-store__loading";
        empty.textContent = "This store is currently unavailable.";
        grid.append(empty);
      } else {
        return null;
      }
    }

    section.append(grid);
    return section;
  }

  function renderCatalog(data) {
    if (!data) return;
    const { list, root, host } = elements();
    if (!list) return;

    list.replaceChildren();
    let renderedStores = 0;
    for (const store of data.stores || []) {
      const section = renderStore(store);
      if (section) {
        list.append(section);
        renderedStores += 1;
      }
    }
    if (renderedStores === 0) {
      const empty = document.createElement("div");
      empty.className = "plugin-store__loading";
      empty.textContent =
        (state.searchQuery || state.filter !== "all")
          ? "No plugins match the current search/filter."
          : "No plugin stores are configured.";
      list.append(empty);
    }

    const { filters } = elements();
    if (filters) {
      for (const button of filters.querySelectorAll("[data-filter]")) {
        button.classList.toggle("is-active", button.dataset.filter === state.filter);
      }
    }
    syncUpdateAllButton();
    renderCatalogSummary(data);

    if (root) root.textContent = data.plugin_root ? `Install path: ${data.plugin_root}` : "";
    if (host) {
      host.textContent = data.host_version
        ? `feedBack ${data.host_version} · Plugin spec v${data.plugin_spec_major || 1}`
        : `feedBack version unavailable · Plugin spec v${data.plugin_spec_major || 1}`;
    }
  }

  async function loadCatalog(forceRefresh = false) {
    const { list, refresh } = elements();
    if (!list) return;
    if (refresh) refresh.disabled = true;

    if (!state.catalog) {
      list.replaceChildren();
      const loading = document.createElement("div");
      loading.className = "plugin-store__loading";
      loading.textContent = forceRefresh ? "Checking plugin stores…" : "Loading plugin catalog…";
      list.append(loading);
    }

    try {
      const data = await api(`/catalog?refresh=${forceRefresh ? "true" : "false"}`);
      state.catalog = data;
      renderCatalog(data);

      const warnings = (data.stores || [])
        .filter((store) => store.warning)
        .map((store) => `${store.name}: ${store.warning}`);
      if (warnings.length) {
        setBanner(warnings.join("  "), "warning");
      } else if (forceRefresh) {
        setBanner("Plugin stores refreshed.", "success");
      }
    } catch (error) {
      setBanner(error.message || String(error), "error");
      if (!state.catalog) {
        list.replaceChildren();
        const failure = document.createElement("div");
        failure.className = "plugin-store__loading plugin-store__error";
        failure.textContent = error.message || String(error);
        list.append(failure);
      }
    } finally {
      if (refresh) refresh.disabled = false;
    }
  }

  function showAddStoreDialog() {
    const { dialog, addUrl, addAck, addError } = elements();
    if (!dialog) return;
    if (addUrl) addUrl.value = "";
    if (addAck) addAck.checked = false;
    if (addError) {
      addError.hidden = true;
      addError.textContent = "";
    }
    if (typeof dialog.showModal === "function") dialog.showModal();
    else dialog.setAttribute("open", "");
    window.setTimeout(() => addUrl && addUrl.focus(), 0);
  }

  function closeAddStoreDialog() {
    const { dialog } = elements();
    if (!dialog) return;
    if (typeof dialog.close === "function") dialog.close();
    else dialog.removeAttribute("open");
  }

  async function submitAddStore(event) {
    event.preventDefault();
    const { addUrl, addAck, addSubmit, addError } = elements();
    const url = addUrl ? addUrl.value.trim() : "";
    const acknowledged = Boolean(addAck && addAck.checked);

    if (!url || !acknowledged) {
      if (addError) {
        addError.hidden = false;
        addError.textContent = "Enter the YAML URL and acknowledge the third-party plugin warning.";
      }
      return;
    }

    if (addSubmit) addSubmit.disabled = true;
    if (addError) {
      addError.hidden = false;
      addError.textContent = "Checking store YAML and plugin compatibility…";
    }

    try {
      const result = await api("/stores", {
        method: "POST",
        body: JSON.stringify({ url, acknowledge_risk: true }),
      });
      closeAddStoreDialog();
      setBanner(`Added third-party store “${result.store.name}”.`, "success");
      await loadCatalog(false);
    } catch (error) {
      if (addError) {
        addError.hidden = false;
        addError.textContent = error.message || String(error);
      }
    } finally {
      if (addSubmit) addSubmit.disabled = false;
    }
  }

  function showGithubDialog() {
    const {
      githubDialog,
      githubUrl,
      githubAck,
      githubError,
    } = elements();
    if (!githubDialog) return;
    if (githubUrl) githubUrl.value = "";
    if (githubAck) githubAck.checked = false;
    if (githubError) {
      githubError.hidden = true;
      githubError.textContent = "";
    }
    if (typeof githubDialog.showModal === "function") githubDialog.showModal();
    else githubDialog.setAttribute("open", "");
    window.setTimeout(() => githubUrl && githubUrl.focus(), 0);
  }

  function closeGithubDialog() {
    const { githubDialog } = elements();
    if (!githubDialog) return;
    if (typeof githubDialog.close === "function") githubDialog.close();
    else githubDialog.removeAttribute("open");
  }

  async function submitGithubInstall(event) {
    event.preventDefault();
    const {
      githubUrl,
      githubAck,
      githubSubmit,
      githubError,
    } = elements();

    const repository = githubUrl ? githubUrl.value.trim() : "";
    const acknowledged = Boolean(githubAck && githubAck.checked);

    if (!repository || !acknowledged) {
      if (githubError) {
        githubError.hidden = false;
        githubError.textContent =
          "Enter a public GitHub repository URL and acknowledge the third-party code warning.";
      }
      return;
    }

    if (githubSubmit) githubSubmit.disabled = true;
    if (githubError) {
      githubError.hidden = false;
      githubError.textContent =
        "Validating plugin.json, feedBack compatibility, and repository structure…";
    }

    try {
      const result = await api("/direct/install", {
        method: "POST",
        body: JSON.stringify({
          repository,
          acknowledge_third_party: true,
        }),
      });
      closeGithubDialog();
      state.restartRequired = true;
      syncRestartButton();
      setBanner(
        `${result.plugin_id} ${result.version} installed from GitHub. Restart feedBack to load it.`,
        "success"
      );
      await loadCatalog(false);
    } catch (error) {
      if (githubError) {
        githubError.hidden = false;
        githubError.textContent = error.message || String(error);
      }
    } finally {
      if (githubSubmit) githubSubmit.disabled = false;
    }
  }

  function bind() {
    const {
      refresh, restart, addStore, addForm, addClose, addCancel, dialog,
      search, filters, updateAll, updateLink,
      installGithub, githubDialog, githubForm, githubClose, githubCancel,
      versionsDialog, versionsClose,
    } = elements();

    if (updateLink && updateLink.dataset.pluginStoreBound !== "1") {
      updateLink.dataset.pluginStoreBound = "1";
      updateLink.addEventListener("click", updatePluginStore);
    }
    if (refresh && refresh.dataset.pluginStoreBound !== "1") {
      refresh.dataset.pluginStoreBound = "1";
      refresh.addEventListener("click", () => {
        checkSelfUpdate(true);
        loadCatalog(true);
      });
    }
    if (search && search.dataset.pluginStoreBound !== "1") {
      search.dataset.pluginStoreBound = "1";
      search.value = state.searchQuery || "";
      search.addEventListener("input", () => {
        state.searchQuery = search.value || "";
        renderCatalog(state.catalog);
      });
    }
    if (filters && filters.dataset.pluginStoreBound !== "1") {
      filters.dataset.pluginStoreBound = "1";
      filters.addEventListener("click", (event) => {
        const button = event.target.closest("[data-filter]");
        if (!button) return;
        state.filter = button.dataset.filter || "all";
        renderCatalog(state.catalog);
      });
    }
    if (updateAll && updateAll.dataset.pluginStoreBound !== "1") {
      updateAll.dataset.pluginStoreBound = "1";
      updateAll.addEventListener("click", updateAllPlugins);
    }

    if (restart && restart.dataset.pluginStoreBound !== "1") {
      restart.dataset.pluginStoreBound = "1";
      restart.addEventListener("click", restartFeedBack);
    }
    if (installGithub && installGithub.dataset.pluginStoreBound !== "1") {
      installGithub.dataset.pluginStoreBound = "1";
      installGithub.addEventListener("click", showGithubDialog);
    }
    if (githubForm && githubForm.dataset.pluginStoreBound !== "1") {
      githubForm.dataset.pluginStoreBound = "1";
      githubForm.addEventListener("submit", submitGithubInstall);
    }
    for (const button of [githubClose, githubCancel]) {
      if (button && button.dataset.pluginStoreBound !== "1") {
        button.dataset.pluginStoreBound = "1";
        button.addEventListener("click", closeGithubDialog);
      }
    }
    if (githubDialog && githubDialog.dataset.pluginStoreBound !== "1") {
      githubDialog.dataset.pluginStoreBound = "1";
      githubDialog.addEventListener("click", (event) => {
        if (event.target === githubDialog) closeGithubDialog();
      });
    }
    if (versionsClose && versionsClose.dataset.pluginStoreBound !== "1") {
      versionsClose.dataset.pluginStoreBound = "1";
      versionsClose.addEventListener("click", closeVersionsDialog);
    }
    if (versionsDialog && versionsDialog.dataset.pluginStoreBound !== "1") {
      versionsDialog.dataset.pluginStoreBound = "1";
      versionsDialog.addEventListener("click", (event) => {
        if (event.target === versionsDialog) closeVersionsDialog();
      });
    }

    if (addStore && addStore.dataset.pluginStoreBound !== "1") {
      addStore.dataset.pluginStoreBound = "1";
      addStore.addEventListener("click", showAddStoreDialog);
    }
    if (addForm && addForm.dataset.pluginStoreBound !== "1") {
      addForm.dataset.pluginStoreBound = "1";
      addForm.addEventListener("submit", submitAddStore);
    }
    for (const button of [addClose, addCancel]) {
      if (button && button.dataset.pluginStoreBound !== "1") {
        button.dataset.pluginStoreBound = "1";
        button.addEventListener("click", closeAddStoreDialog);
      }
    }
    if (dialog && dialog.dataset.pluginStoreBound !== "1") {
      dialog.dataset.pluginStoreBound = "1";
      dialog.addEventListener("click", (event) => {
        if (event.target === dialog) closeAddStoreDialog();
      });
    }

    syncRestartButton();
  }

  bind();
  ensureSidebarEntry();
  checkSelfUpdate(false);
  loadCatalog(false);

  // feedBack v3 currently rebuilds a hard-coded sidebar. Re-add the Plugin
  // Store shortcut if the shell replaces the nav DOM after plugins load.
  if (!state.sidebarObserver && document.body) {
    state.sidebarObserver = new MutationObserver(() => ensureSidebarEntry());
    state.sidebarObserver.observe(document.body, {
      childList: true,
      subtree: true,
    });
  }

  if (!state.bound && window.feedBack && typeof window.feedBack.on === "function") {
    state.bound = true;
    window.feedBack.on("screen:changed", (event) => {
      const screenId =
        typeof event === "string"
          ? event
          : event && (event.screen || event.screenId || event.detail);

      ensureSidebarEntry();
      syncSidebarEntryActive();

      if (screenId === "plugin-plugin_store") {
        bind();
        checkSelfUpdate(false);
        loadCatalog(false);
      }
    });
  }
})();
