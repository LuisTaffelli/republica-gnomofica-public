/* No message or history content is stored in the browser. Only an in-flight
   request's two identifiers survive a reload, for read-only recovery. */
(function (root) {
  "use strict";
  const STORAGE_KEY = "miku.pending-request.v1";
  const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  const TERMINAL = new Set(["completed", "failed", "interrupted"]);

  class ApiError extends Error {
    constructor(message, code, status) { super(message); this.code = code; this.status = status; }
  }

  function loadPending(storage) {
    try {
      const value = JSON.parse(storage.getItem(STORAGE_KEY));
      if (value && UUID.test(value.request_id) && UUID.test(value.session_id)) {
        return { request_id: value.request_id, session_id: value.session_id };
      }
    } catch (_) { /* Invalid/unavailable storage cannot grant any capability. */ }
    return null;
  }

  function storePending(storage, pending) {
    try {
      if (pending) storage.setItem(STORAGE_KEY, JSON.stringify({
        request_id: pending.request_id, session_id: pending.session_id,
      }));
      else storage.removeItem(STORAGE_KEY);
      return true;
    } catch (_) { return false; }
  }

  function createApp(options) {
    const doc = options.document;
    const fetcher = options.fetch;
    const storage = options.storage;
    const schedule = options.setTimeout;
    const unschedule = options.clearTimeout;
    const el = Object.fromEntries([
      "new-session", "refresh-sessions", "session-list", "toggle-sidebar", "sidebar",
      "session-title", "session-subtitle", "close-session", "connection-label", "connection-dot",
      "reconnect", "notice", "recovery-actions", "retry-request", "forget-request", "welcome",
      "messages", "composer", "message-input", "send-message",
    ].map(id => [id, doc.getElementById(id)]));
    const state = { csrf: null, session: null, sessions: [], pending: loadPending(storage),
      pendingText: null, unknown: false, operation: false, online: false, polling: false };
    let attempted = !!state.pending;
    let timer = null;
    let selectionVersion = 0;
    let intendedSessionId = null;

    function notice(message) { el.notice.textContent = message || ""; el.notice.hidden = !message; }
    function connection(message, kind) {
      el["connection-label"].textContent = message;
      el["connection-dot"].className = "status-dot " + kind;
      el.reconnect.hidden = kind !== "offline";
    }
    function controls() {
      const busy = !!state.pending || state.operation;
      el["new-session"].disabled = !state.online || busy;
      el["refresh-sessions"].disabled = !state.online || state.operation;
      el["message-input"].disabled = !state.online || busy || !state.session || !!state.session.closed_at;
      el["send-message"].disabled = el["message-input"].disabled || !el["message-input"].value.trim();
      el["close-session"].hidden = !state.session || !!state.session.closed_at;
      el["close-session"].disabled = !state.online || busy;
      el["recovery-actions"].hidden = !state.unknown;
      el["retry-request"].hidden = !state.unknown || state.pendingText === null;
      el["retry-request"].disabled = state.operation;
      el["forget-request"].hidden = !state.unknown;
      el["forget-request"].disabled = state.operation;
    }
    function restingConnection() {
      if (!state.online) { connection("Sin conexión", "offline"); return; }
      connection(state.pending ? "Miku está preparando una respuesta…" : "Lista para conversar", state.pending ? "busy" : "connected");
    }
    async function api(path, body) {
      const config = { credentials: "same-origin", cache: "no-store", headers: { Accept: "application/json" } };
      if (body !== undefined) {
        config.method = "POST";
        config.headers["Content-Type"] = "application/json";
        config.headers["X-CSRF-Token"] = state.csrf;
        config.body = JSON.stringify(body);
      }
      let response;
      try { response = await fetcher(path, config); }
      catch (_) { throw new ApiError("Se perdió la conexión con República.", "network_error", 0); }
      let data;
      try { data = await response.json(); }
      catch (_) { throw new ApiError("El servidor devolvió una respuesta que no pude leer.", "invalid_response", response.status); }
      if (!response.ok) {
        throw new ApiError(data.error?.message || "No se pudo completar la operación.", data.error?.code || "server_error", response.status);
      }
      return data;
    }
    function report(error) {
      notice(error.message || "No se pudo completar la operación.");
      if (!error.status || error.status === 401 || error.status === 403) {
        state.online = false;
        connection(error.status ? "Acceso no autorizado" : "Sin conexión", "offline");
      } else if (state.pending) {
        connection("No pude consultar el estado del envío", "offline");
      }
      controls();
    }
    function dateLabel(value, timeOnly = false) {
      const date = new Date(value);
      if (!value || Number.isNaN(date.valueOf())) return "";
      return timeOnly ? date.toLocaleTimeString("es", { hour: "2-digit", minute: "2-digit" })
        : date.toLocaleDateString("es", { day: "numeric", month: "short" });
    }
    function renderSessions() {
      el["session-list"].replaceChildren();
      if (!state.sessions.length) {
        const p = doc.createElement("p"); p.className = "list-empty";
        p.textContent = "Todavía no hay conversaciones. Tu próxima idea puede ser la primera.";
        el["session-list"].append(p);
      }
      for (const session of state.sessions) {
        const button = doc.createElement("button"); button.type = "button"; button.className = "session-item";
        button.setAttribute("aria-current", String(state.session?.id === session.id));
        const title = doc.createElement("strong"); title.textContent = session.title || "Conversación con Miku";
        const meta = doc.createElement("small");
        meta.textContent = [dateLabel(session.updated_at || session.created_at), session.closed_at ? "Cerrada" : "Para retomar"].filter(Boolean).join(" · ");
        button.append(title, meta);
        button.addEventListener("click", () => { selectSession(session.id).catch(report); });
        el["session-list"].append(button);
      }
    }
    function renderMessages(messages) {
      const view = el.messages;
      const nearEnd = view.scrollHeight - view.scrollTop - view.clientHeight < 90;
      view.replaceChildren();
      if (!messages.length) {
        const p = doc.createElement("p"); p.className = "history-empty";
        p.textContent = state.session.closed_at ? "Esta conversación está cerrada. Podés empezar una nueva." : "Todo listo. ¿Por dónde empezamos?";
        view.append(p);
      }
      for (const message of messages) {
        const human = message.role === "user";
        const article = doc.createElement("article"); article.className = "message " + (human ? "user" : "assistant");
        const badge = doc.createElement("span"); badge.className = "message-badge"; badge.setAttribute("aria-hidden", "true"); badge.textContent = human ? "Vos" : "m.";
        const main = doc.createElement("div"); main.className = "message-main";
        const meta = doc.createElement("p"); meta.className = "message-meta";
        const name = doc.createElement("span"); name.textContent = human ? "Vos" : message.role === "assistant" ? "Miku" : "Sistema";
        const time = doc.createElement("time"); time.dateTime = message.timestamp || ""; time.textContent = dateLabel(message.timestamp, true);
        const text = doc.createElement("p"); text.className = "message-content"; text.textContent = message.content;
        meta.append(name, time); main.append(meta, text); article.append(badge, main); view.append(article);
      }
      if (nearEnd) view.scrollTop = view.scrollHeight;
    }
    async function refreshSessions() {
      const data = await api("/api/sessions"); state.sessions = data.sessions; renderSessions();
    }
    async function selectSession(id, keepPosition = false, background = false) {
      if (!UUID.test(id)) return;
      // An automatic refresh must not supersede a human selection still loading.
      if (background && intendedSessionId && intendedSessionId !== id) return;
      if (!background || !intendedSessionId) intendedSessionId = id;
      const version = ++selectionVersion;
      const data = await api("/api/sessions/" + id);
      if (version !== selectionVersion) return;
      state.session = data.session;
      el["session-title"].textContent = data.session.title || "Conversación con Miku";
      el["session-subtitle"].textContent = (data.session.closed_at ? "Cerrada · Solo lectura" : "Podés continuar acá") + " · " + data.session.id.slice(0, 8);
      el.welcome.hidden = true; el.messages.hidden = false;
      renderMessages(data.messages);
      if (!keepPosition) el.messages.scrollTop = el.messages.scrollHeight;
      el.sidebar.classList.remove("is-open"); el["toggle-sidebar"].setAttribute("aria-expanded", "false");
      renderSessions(); controls();
    }
    function remember(pending) {
      state.pending = pending;
      if (!storePending(storage, pending)) {
        notice("El navegador no pudo guardar la referencia del envío. Evitá recargar hasta que termine; después podés recuperar la respuesta desde el historial.");
      }
    }
    function stopPolling() { if (timer !== null) unschedule(timer); timer = null; }
    function pollLater() {
      stopPolling();
      timer = schedule(() => { timer = null; checkPending(); }, 1300);
    }
    async function acceptReceipt(receipt) {
      if (!state.pending || receipt.request_id !== state.pending.request_id || receipt.session_id !== state.pending.session_id) {
        throw new ApiError("La respuesta no corresponde al envío pendiente. Reconectá para verificar el estado.", "receipt_mismatch", 0);
      }
      if (receipt.state !== "running" && !TERMINAL.has(receipt.state)) {
        throw new ApiError("No pude reconocer el estado del envío. Reconectá para verificarlo.", "invalid_response", 0);
      }
      state.unknown = false; state.online = true;
      if (state.pendingText !== null && el["message-input"].value.trim() === state.pendingText) el["message-input"].value = "";
      state.pendingText = null;
      if (TERMINAL.has(receipt.state)) {
        stopPolling(); remember(null);
        notice(receipt.state === "completed" ? "" : receipt.state === "interrupted"
          ? "No se pudo confirmar el final del turno. Revisá el historial antes de volver a escribir; no se reenviará automáticamente."
          : "El turno no pudo completarse. Revisá el historial antes de volver a escribir; no se reenviará automáticamente.");
        await refreshSessions();
        if (state.session?.id === receipt.session_id) await selectSession(receipt.session_id, true, true);
      } else { pollLater(); }
      restingConnection(); controls();
    }
    async function checkPending() {
      if (!state.pending || state.polling) return;
      state.polling = true;
      const requestId = state.pending.request_id;
      try { await acceptReceipt(await api("/api/requests/" + requestId)); }
      catch (error) {
        stopPolling();
        if (error.status === 404 && state.pending?.request_id === requestId) {
          state.unknown = true; state.online = true;
          notice("No se encontró confirmación del envío. No se reenviará solo. Revisá el historial o reconectá para consultar otra vez.");
          connection("Envío sin confirmar", "offline"); controls();
        } else {
          report(error);
          if (state.online) connection("No pude consultar el estado del envío", "offline");
        }
      } finally { state.polling = false; }
    }
    async function postPending() {
      if (!state.pending || state.pendingText === null || state.operation) return;
      state.operation = true; state.unknown = false; notice(""); controls();
      connection("Enviando tu mensaje…", "busy");
      const pending = state.pending;
      const firstAttempt = !attempted;
      attempted = true;
      let accepted = false;
      try {
        const receipt = await api("/api/sessions/" + pending.session_id + "/turns", {
          request_id: pending.request_id, text: state.pendingText,
        });
        accepted = true;
        await acceptReceipt(receipt);
        if (state.session?.id === pending.session_id && state.pending) await selectSession(pending.session_id, true, true);
      } catch (error) {
        if (!accepted && firstAttempt && error.status === 409 && error.code === "busy") {
          remember(null); state.pendingText = null; report(error);
          connection("Miku está ocupada en otra conversación", "busy");
          try {
            const current = await api("/api/bootstrap");
            state.csrf = current.csrf_token;
            if (current.active_request) {
              attempted = true;
              remember({ request_id: current.active_request.request_id, session_id: current.active_request.session_id });
              await acceptReceipt(current.active_request);
            }
          } catch (readError) { report(readError); }
        } else if (error.status === 401 || error.status === 403 || accepted) {
          // A rejected retry cannot prove the original request was not accepted.
          // Nor can a later history-fetch failure invalidate an accepted receipt.
          report(error);
        } else {
          report(error);
          await checkPending();
        }
      } finally { state.operation = false; controls(); }
    }
    async function send() {
      const text = el["message-input"].value.trim();
      if (state.pending || state.operation || !state.online || !state.session || state.session.closed_at || !text) return;
      if (text.length > 8192) { notice("El mensaje puede tener hasta 8192 caracteres."); return; }
      let id;
      try { id = options.randomUUID(); }
      catch (_) { notice("Este navegador necesita una conexión segura para crear envíos. No se envió el mensaje."); return; }
      state.pendingText = text;
      attempted = false;
      remember({ request_id: id, session_id: state.session.id });
      await postPending();
    }
    async function createSession() {
      if (state.pending || state.operation || !state.online) return;
      state.operation = true; controls(); notice("");
      try {
        const data = await api("/api/sessions", {});
        await refreshSessions(); await selectSession(data.session.id);
        el["message-input"].value = "";
      } catch (error) {
        report(error); // Never retry a session creation automatically.
      } finally { state.operation = false; controls(); if (!el["message-input"].disabled) el["message-input"].focus(); }
    }
    async function closeSession() {
      if (state.pending || state.operation || !state.online || !state.session || state.session.closed_at) return;
      state.operation = true; controls(); notice("");
      const id = state.session.id;
      try {
        const data = await api("/api/sessions/" + id + "/close", {});
        // A read started before this confirmed close cannot reopen its UI.
        // A pending human selection of another conversation remains valid.
        if (intendedSessionId === id) selectionVersion++;
        state.sessions = state.sessions.map(session => session.id === id ? data.session : session);
        if (state.session?.id === id) {
          state.session = data.session;
          el["session-title"].textContent = data.session.title || "Conversación con Miku";
          el["session-subtitle"].textContent = "Cerrada · Solo lectura · " + data.session.id.slice(0, 8);
        }
        renderSessions(); controls();
        await refreshSessions(); if (state.session?.id === id) await selectSession(id, true, true);
      } catch (error) { report(error); }
      finally { state.operation = false; controls(); }
    }
    async function connect() {
      // The poll owns its receipt until it finishes refreshing the history.
      // Do not race it with a potentially older bootstrap snapshot.
      if (state.operation || state.polling) return;
      stopPolling(); state.operation = true; controls(); connection("Conectando…", "busy");
      try {
        const data = await api("/api/bootstrap"); state.csrf = data.csrf_token; state.online = true;
        notice("");
        if (!state.pending && data.active_request) remember({ request_id: data.active_request.request_id, session_id: data.active_request.session_id });
        await refreshSessions();
        const selectedId = state.session?.id || state.pending?.session_id || state.sessions.find(s => !s.closed_at)?.id || state.sessions[0]?.id;
        if (selectedId) await selectSession(selectedId, !!state.session, true);
        restingConnection();
        if (state.pending) await checkPending();
      } catch (error) { report(error); }
      finally { state.operation = false; controls(); }
    }
    el.composer.addEventListener("submit", event => { event.preventDefault(); send(); });
    el["message-input"].addEventListener("input", controls);
    el["message-input"].addEventListener("keydown", event => {
      if (event.key === "Enter" && (event.ctrlKey || event.metaKey) && !event.isComposing) { event.preventDefault(); send(); }
    });
    el["new-session"].addEventListener("click", createSession);
    el["close-session"].addEventListener("click", closeSession);
    el["refresh-sessions"].addEventListener("click", () => refreshSessions().catch(report));
    el.reconnect.addEventListener("click", connect);
    el["retry-request"].addEventListener("click", postPending);
    el["forget-request"].addEventListener("click", () => {
      if (!state.unknown || state.operation) return;
      stopPolling(); remember(null); state.pendingText = null; state.unknown = false;
      notice("Referencia liberada. No se reenvió ningún mensaje."); restingConnection(); controls();
    });
    el["toggle-sidebar"].addEventListener("click", () => {
      const open = el.sidebar.classList.toggle("is-open"); el["toggle-sidebar"].setAttribute("aria-expanded", String(open));
    });
    return { connect, send, createSession, closeSession, selectSession, checkPending, state };
  }

  if (typeof module === "object" && module.exports) module.exports = { createApp, loadPending, storePending, STORAGE_KEY };
  else {
    let storage;
    try { storage = root.sessionStorage; } catch (_) { storage = null; }
    const app = createApp({ document: root.document, fetch: root.fetch.bind(root), storage,
      randomUUID: () => root.crypto.randomUUID(), setTimeout: root.setTimeout.bind(root), clearTimeout: root.clearTimeout.bind(root) });
    root.addEventListener("online", () => app.connect());
    app.connect();
  }
})(typeof window !== "undefined" ? window : globalThis);
