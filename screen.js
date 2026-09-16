(() => {
  const GLOBAL_KEY = "__feedBackPluginStore";
  const API = "/api/plugins/plugin_store";

  const state = window[GLOBAL_KEY] || {
    bound: false,
    busy: new Set(),
    restartRequired: false,
    restarting: false,
  };
  window[GLOBAL_KEY] = state;

  function elements() {
    return {
      app: document.getElementById("plugin-store-app"),
      list: document.getElementById("plugin-store-list"),
      banner: document.getElementById("plugin-store-banner"),
      refresh: document.getElementById("plugin-store-refresh"),
      restart: document.getElementById("plugin-store-restart"),
      source: document.getElementById("plugin-store-source"),
      root: document.getElementById("plugin-store-root"),
    };
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

    const response = await fetch(`${API}${path}`, {
      ...options,
      headers,
      credentials: "same-origin",
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
        const response = await fetch(
          `${API}/instance?_=${Date.now()}`,
          {
            method: "GET",
            cache: "no-store",
            credentials: "same-origin",
          }
        );

        if (!response.ok) continue;

        const payload = await response.json();
        if (payload.instance_id && payload.instance_id !== oldInstanceId) {
          return true;
        }
      } catch (_) {
        // Expected while the container is between processes.
      }
    }

    return false;
  }

  async function restartFeedBack() {
    if (state.restarting) return;

    if (
      !window.confirm(
        "Restart feedBack now? The page will reconnect automatically when the container is back."
      )
    ) {
      return;
    }

    state.restarting = true;
    syncRestartButton();
    setBanner("Restarting feedBack…", "info");

    try {
      const result = await api("/restart", { method: "POST" });
      const restarted = await waitForNewInstance(result.instance_id);

      if (restarted) {
        window.location.reload();
        return;
      }

      setBanner(
        "feedBack did not return within 60 seconds. Check the container restart policy/logs.",
        "error"
      );
    } catch (error) {
      setBanner(error.message || String(error), "error");
    } finally {
      state.restarting = false;
      syncRestartButton();
    }
  }

  function actionButton(label, className, disabled, handler) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `plugin-store__button ${className || ""}`.trim();
    button.textContent = label;
    button.disabled = Boolean(disabled);
    button.addEventListener("click", handler);
    return button;
  }

  function statusLabel(plugin) {
    switch (plugin.status) {
      case "installed":
        return `Installed ${plugin.installed_version || ""}`.trim();
      case "update_available":
        return `Update available: ${plugin.installed_version || "?"} → ${plugin.version}`;
      case "local_newer":
        return `Installed ${plugin.installed_version || "?"} (newer than catalog)`;
      case "broken":
        return "Installed, but manifest validation failed";
      default:
        return `Available ${plugin.version}`;
    }
  }

  async function mutate(plugin, operation) {
    const key = `${operation}:${plugin.id}`;
    if (state.busy.has(key)) return;

    if (
      operation === "remove" &&
      !window.confirm(`Remove ${plugin.name}? The change takes effect after feedBack restarts.`)
    ) {
      return;
    }

    state.busy.add(key);
    setBanner(`${operation[0].toUpperCase()}${operation.slice(1)}ing ${plugin.name}…`);

    try {
      const method = operation === "remove" ? "DELETE" : "POST";
      await api(`/${operation}/${encodeURIComponent(plugin.id)}`, { method });
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
    }
  }

  function renderPlugin(plugin) {
    const card = document.createElement("article");
    card.className = "plugin-store__card";

    const body = document.createElement("div");
    body.className = "plugin-store__card-body";

    const title = document.createElement("h2");
    title.textContent = plugin.name;

    const description = document.createElement("p");
    description.className = "plugin-store__description";
    description.textContent = plugin.description || "No description provided.";

    const status = document.createElement("p");
    status.className = `plugin-store__status plugin-store__status--${plugin.status}`;
    status.textContent = statusLabel(plugin);

    body.append(title, description, status);

    if (plugin.error) {
      const error = document.createElement("p");
      error.className = "plugin-store__error";
      error.textContent = plugin.error;
      body.append(error);
    }

    const actions = document.createElement("div");
    actions.className = "plugin-store__actions";

    if (!plugin.installed) {
      actions.append(
        actionButton(
          "Install",
          "plugin-store__button--primary",
          state.busy.has(`install:${plugin.id}`),
          () => mutate(plugin, "install")
        )
      );
    } else {
      if (plugin.status === "update_available" || plugin.status === "broken") {
        actions.append(
          actionButton(
            plugin.status === "broken" ? "Repair" : "Update",
            "plugin-store__button--primary",
            state.busy.has(`update:${plugin.id}`),
            () => mutate(plugin, "update")
          )
        );
      }

      actions.append(
        actionButton(
          "Remove",
          "plugin-store__button--danger",
          state.busy.has(`remove:${plugin.id}`),
          () => mutate(plugin, "remove")
        )
      );
    }

    card.append(body, actions);
    return card;
  }

  async function loadCatalog(forceRefresh = false) {
    const { list, refresh, source, root } = elements();
    if (!list) return;

    refresh && (refresh.disabled = true);
    list.replaceChildren();

    const loading = document.createElement("div");
    loading.className = "plugin-store__loading";
    loading.textContent = forceRefresh ? "Checking registry…" : "Loading plugin catalog…";
    list.append(loading);

    try {
      const data = await api(`/catalog?refresh=${forceRefresh ? "true" : "false"}`);
      list.replaceChildren();

      for (const plugin of data.plugins || []) {
        list.append(renderPlugin(plugin));
      }

      if (!data.plugins || data.plugins.length === 0) {
        const empty = document.createElement("div");
        empty.className = "plugin-store__loading";
        empty.textContent = "The registry contains no plugins.";
        list.append(empty);
      }

      if (source) {
        const registry = data.registry || {};
        const label = registry.source_url
          ? `Registry: ${registry.source}${registry.stale ? " (cached/stale)" : ""}`
          : "Registry: bundled";
        source.textContent = label;
      }
      if (root) {
        root.textContent = data.plugin_root ? `Install path: ${data.plugin_root}` : "";
      }

      if (data.registry && data.registry.warning) {
        setBanner(data.registry.warning, "warning");
      } else if (forceRefresh && data.registry) {
        setBanner(
          data.registry.changed ? "Registry updated." : "Registry is already current.",
          "success"
        );
      }
    } catch (error) {
      list.replaceChildren();
      const failure = document.createElement("div");
      failure.className = "plugin-store__loading plugin-store__error";
      failure.textContent = error.message || String(error);
      list.append(failure);
      setBanner(error.message || String(error), "error");
    } finally {
      refresh && (refresh.disabled = false);
    }
  }

  function bind() {
    const { refresh, restart } = elements();

    if (refresh && refresh.dataset.pluginStoreBound !== "1") {
      refresh.dataset.pluginStoreBound = "1";
      refresh.addEventListener("click", () => loadCatalog(true));
    }

    if (restart && restart.dataset.pluginStoreBound !== "1") {
      restart.dataset.pluginStoreBound = "1";
      restart.addEventListener("click", restartFeedBack);
    }

    syncRestartButton();
  }

  // Re-hydration safe: refresh element references and avoid duplicate listeners.
  bind();
  loadCatalog(false);

  if (!state.bound && window.feedBack && typeof window.feedBack.on === "function") {
    state.bound = true;
    window.feedBack.on("screen:changed", (event) => {
      const screenId =
        typeof event === "string"
          ? event
          : event && (event.screen || event.screenId || event.detail);
      if (screenId === "plugin-plugin_store") {
        bind();
        loadCatalog(false);
      }
    });
  }
})();
