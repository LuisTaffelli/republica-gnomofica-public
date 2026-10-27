const test = require("node:test");
const assert = require("node:assert/strict");
const { createApp, loadPending, storePending, STORAGE_KEY } = require("../miku/web/static/app.js");

const SESSION = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const REQUEST = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const session = { id: SESSION, title: "Una idea", created_at: "2026-10-04T12:00:00Z", closed_at: null };
const receipt = state => ({ request_id: REQUEST, session_id: SESSION, state, result: null });

class Element {
  constructor(tag = "div") {
    this.tagName = tag; this.children = []; this.listeners = {}; this.attributes = {};
    this.value = ""; this.hidden = false; this.disabled = true; this.className = "";
    this.scrollTop = 0; this.scrollHeight = 100; this.clientHeight = 100;
    const classes = new Set();
    this.classList = { remove: x => classes.delete(x), toggle: x => {
      if (classes.has(x)) { classes.delete(x); return false; } classes.add(x); return true;
    } };
  }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return (this._text || "") + this.children.map(c => c.textContent).join(""); }
  set innerHTML(_) { throw Error("HTML insertion is forbidden in this UI"); }
  setAttribute(key, value) { this.attributes[key] = value; }
  replaceChildren(...children) { this._text = ""; this.children = children; }
  append(...children) { this.children.push(...children); }
  addEventListener(name, callback) { (this.listeners[name] ||= []).push(callback); }
  async emit(name, event = {}) { for (const cb of this.listeners[name] || []) await cb({ preventDefault() {}, ...event }); }
  focus() { this.focused = true; }
}
function memoryStorage() {
  const values = new Map();
  return { getItem: k => values.get(k) ?? null, setItem: (k, v) => values.set(k, v), removeItem: k => values.delete(k) };
}
function response(data, status = 200) { return { ok: status >= 200 && status < 300, status, json: async () => data }; }
function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
}
const tick = () => new Promise(resolve => setImmediate(resolve));
function setup(extra = {}) {
  const elements = new Map();
  const document = { getElementById: id => {
    if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id);
  }, createElement: tag => new Element(tag) };
  const storage = extra.storage || memoryStorage();
  const calls = [], timers = new Map();
  let nextTimer = 1, idsGenerated = 0;
  const app = createApp({ document, storage, randomUUID: () => { idsGenerated++; return REQUEST; },
    setTimeout: cb => { const id = nextTimer++; timers.set(id, cb); return id; }, clearTimeout: id => timers.delete(id),
    fetch: async (path, config) => {
      calls.push({ path, ...config });
      const custom = await extra.respond?.(path, config);
      if (custom) return custom;
      if (path === "/api/bootstrap") return response({ csrf_token: "test-csrf", active_request: extra.active || null });
      if (path === "/api/sessions") return response({ sessions: [extra.session || session] });
      if (path === "/api/sessions/" + SESSION) return response({ session: extra.session || session, messages: extra.messages || [] });
      if (path === "/api/requests/" + REQUEST) return response(receipt("running"));
      if (path.endsWith("/turns")) return response(receipt("running"), 202);
      throw Error("Unexpected request: " + path);
    } });
  return { app, elements, storage, calls, timers, idsGenerated: () => idsGenerated,
    element: id => document.getElementById(id), posts: () => calls.filter(c => c.method === "POST") };
}

test("una respuesta perdida se recupera por GET, sin volver a enviar ni guardar el texto", async () => {
  const ui = setup({ respond: async path => { if (path.endsWith("/turns")) throw Error("lost socket after acceptance"); } });
  await ui.app.connect(); ui.element("message-input").value = "Texto privado";
  await ui.app.send();
  assert.equal(ui.posts().length, 1);
  assert.equal(ui.calls.filter(c => c.path === "/api/requests/" + REQUEST).length, 1);
  assert.deepEqual(JSON.parse(ui.storage.getItem(STORAGE_KEY)), { request_id: REQUEST, session_id: SESSION });
  assert.equal(ui.storage.getItem(STORAGE_KEY).includes("Texto privado"), false);
  assert.equal(ui.element("message-input").value, "");
  assert.equal(ui.element("send-message").disabled, true);
  assert.equal(ui.app.state.online, true);
  assert.equal(ui.posts()[0].credentials, "same-origin");
  assert.equal(ui.posts()[0].headers["X-CSRF-Token"], "test-csrf");
  assert.equal(ui.posts()[0].headers["Content-Type"], "application/json");
});

test("dos envíos simultáneos producen un único POST y un único identificador", async () => {
  let release;
  const waiting = new Promise(resolve => { release = resolve; });
  const ui = setup({ respond: async path => { if (path.endsWith("/turns")) { await waiting; return response(receipt("running"), 202); } } });
  await ui.app.connect(); ui.element("message-input").value = "Hola";
  const first = ui.app.send(); await ui.app.send(); release(); await first;
  assert.equal(ui.posts().length, 1); assert.equal(ui.idsGenerated(), 1);
  await ui.app.closeSession(); await ui.app.createSession();
  assert.equal(ui.posts().length, 1);
});

test("recargar un turno pendiente recupera historial y estado sin ningún POST", async () => {
  const storage = memoryStorage(); storePending(storage, { request_id: REQUEST, session_id: SESSION, text: "Nunca persistir" });
  const ui = setup({ storage, respond: async path => path.startsWith("/api/requests/") ? response(receipt("completed")) : null });
  await ui.app.connect();
  assert.equal(ui.posts().length, 0); assert.equal(ui.idsGenerated(), 0);
  assert.equal(ui.storage.getItem(STORAGE_KEY), null); assert.equal(ui.app.state.pending, null);
  assert.ok(ui.calls.filter(c => c.path === "/api/sessions/" + SESSION).length >= 2);
});

test("envío desconocido requiere reintento explícito con el mismo UUID y texto", async () => {
  let attempts = 0;
  const ui = setup({ respond: async path => {
    if (path.endsWith("/turns")) { if (++attempts === 1) throw Error("offline"); return response(receipt("running")); }
    if (path.startsWith("/api/requests/")) return response({ error: { code: "not_found", message: "unknown" } }, 404);
  } });
  await ui.app.connect(); ui.element("message-input").value = "Mensaje incierto";
  await ui.app.send();
  assert.equal(ui.posts().length, 1); assert.equal(ui.app.state.unknown, true);
  assert.equal(ui.element("retry-request").hidden, false);
  await ui.element("retry-request").emit("click");
  assert.equal(ui.posts().length, 2); assert.equal(ui.idsGenerated(), 1);
  assert.deepEqual(ui.posts().map(p => JSON.parse(p.body)), [
    { request_id: REQUEST, text: "Mensaje incierto" }, { request_id: REQUEST, text: "Mensaje incierto" },
  ]);
});

test("tras reload un envío desconocido no puede reconstruir ni reenviar texto perdido", async () => {
  const storage = memoryStorage(); storePending(storage, { request_id: REQUEST, session_id: SESSION });
  const ui = setup({ storage, respond: async path => path.startsWith("/api/requests/")
    ? response({ error: { code: "not_found", message: "unknown" } }, 404) : null });
  await ui.app.connect();
  assert.equal(ui.element("retry-request").hidden, true);
  assert.equal(ui.element("forget-request").hidden, false);
  assert.equal(ui.posts().length, 0);
  await ui.element("forget-request").emit("click");
  assert.equal(ui.app.state.pending, null); assert.equal(ui.storage.getItem(STORAGE_KEY), null);
  assert.equal(ui.posts().length, 0);
});

test("sin autorización no se consulta historial ni se habilita crear o enviar", async () => {
  const ui = setup({ respond: async () => response({ error: { code: "unauthorized", message: "Acceso denegado" } }, 401) });
  await ui.app.connect(); ui.element("message-input").value = "No enviar";
  await ui.app.send(); await ui.app.createSession();
  assert.equal(ui.calls.length, 1); assert.equal(ui.posts().length, 0);
  assert.equal(ui.element("new-session").disabled, true);
  assert.equal(ui.element("message-input").disabled, true);
  assert.equal(ui.element("notice").textContent, "Acceso denegado");
});

test("historial hostil se representa literalmente sin crear elementos HTML", async () => {
  const attack = '<img src=x onerror="alert(document.cookie)"><script>evil()</script>';
  const ui = setup({ session: { ...session, title: attack }, messages: [{ id: REQUEST, role: "assistant", content: attack, timestamp: "2026-10-04T12:00:00Z" }] });
  await ui.app.connect();
  assert.equal(ui.element("session-title").textContent, attack);
  assert.ok(ui.element("messages").textContent.includes(attack));
  const descendants = element => [element, ...element.children.flatMap(descendants)];
  assert.equal(descendants(ui.element("messages")).some(e => e.tagName === "img" || e.tagName === "script"), false);
});

test("una sesión cerrada queda sólo para consulta, sin reapertura implícita", async () => {
  const ui = setup({ session: { ...session, closed_at: "2026-10-04T13:00:00Z" } });
  await ui.app.connect(); ui.element("message-input").value = "Reabrir no autorizado";
  await ui.app.send(); await ui.app.closeSession();
  assert.equal(ui.posts().length, 0); assert.equal(ui.element("message-input").disabled, true);
  assert.equal(ui.element("close-session").hidden, true);
  assert.ok(ui.element("session-subtitle").textContent.includes("Solo lectura"));
});

test("un turno de otra pestaña anunciado por bootstrap se observa sin reenviarlo", async () => {
  const ui = setup({ active: receipt("running") }); await ui.app.connect();
  assert.equal(ui.posts().length, 0); assert.equal(ui.app.state.pending.request_id, REQUEST);
  assert.equal(ui.element("send-message").disabled, true); assert.equal(ui.element("close-session").disabled, true);
});

test("un receipt interrumpido conserva la incertidumbre sobre el final del turno", async () => {
  const storage = memoryStorage(); storePending(storage, { request_id: REQUEST, session_id: SESSION });
  const ui = setup({ storage, respond: async path => path.startsWith("/api/requests/") ? response(receipt("interrupted")) : null });
  await ui.app.connect();
  assert.equal(ui.posts().length, 0); assert.equal(ui.storage.getItem(STORAGE_KEY), null);
  assert.match(ui.element("notice").textContent, /No se pudo confirmar el final del turno/);
  assert.doesNotMatch(ui.element("notice").textContent, /se interrumpió|se detuvo/);
});

test("storage corrupto o inaccesible no se usa como una ruta API", async () => {
  const storage = memoryStorage(); storage.setItem(STORAGE_KEY, JSON.stringify({ request_id: "../../secrets", session_id: SESSION }));
  assert.equal(loadPending(storage), null); assert.equal(loadPending(null), null);
  assert.equal(storePending(null, receipt("running")), false);
  const ui = setup({ storage }); await ui.app.connect();
  assert.equal(ui.calls.some(c => c.path.includes("secrets")), false);
});

test("ocupado mantiene el borrador pero no reintenta el POST", async () => {
  const ui = setup({ respond: async path => path.endsWith("/turns")
    ? response({ error: { code: "busy", message: "Ya hay un turno activo" } }, 409) : null });
  await ui.app.connect(); ui.element("message-input").value = "Probá después"; await ui.app.send();
  assert.equal(ui.posts().length, 1); assert.equal(ui.app.state.pending, null);
  assert.equal(ui.element("message-input").value, "Probá después");
  assert.match(ui.element("notice").textContent, /turno activo/);
});

test("un receipt que corresponde a otro envío nunca libera el pendiente", async () => {
  const storage = memoryStorage(); storePending(storage, { request_id: REQUEST, session_id: SESSION });
  const ui = setup({ storage, respond: async path => path.startsWith("/api/requests/")
    ? response({ ...receipt("completed"), session_id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc" }) : null });
  await ui.app.connect();
  assert.equal(ui.app.state.pending.request_id, REQUEST); assert.equal(ui.posts().length, 0);
  assert.match(ui.element("notice").textContent, /no corresponde/);
});

for (const status of [401, 403]) {
  test(`un reintento ${status} conserva el UUID incierto para recuperar el original`, async () => {
    let attempts = 0, completed = false;
    const ui = setup({ respond: async path => {
      if (path.endsWith("/turns")) {
        if (++attempts === 1) throw Error("lost connection");
        return response({ error: { code: "unauthorized", message: "Volvé a conectarte" } }, status);
      }
      if (path.startsWith("/api/requests/")) return completed ? response(receipt("completed"))
        : response({ error: { code: "not_found", message: "unknown" } }, 404);
    } });
    await ui.app.connect(); ui.element("message-input").value = "No duplicar";
    await ui.app.send(); await ui.element("retry-request").emit("click");
    assert.equal(ui.app.state.pending.request_id, REQUEST);
    assert.equal(ui.app.state.pendingText, "No duplicar");
    assert.equal(loadPending(ui.storage).request_id, REQUEST);
    assert.equal(ui.element("message-input").disabled, true);
    completed = true; await ui.app.connect();
    assert.equal(ui.posts().length, 2); assert.equal(ui.idsGenerated(), 1);
    assert.equal(ui.app.state.pending, null); assert.equal(ui.element("message-input").value, "");
  });
}

test("fallar una lectura después de aceptar el turno no borra su referencia", async () => {
  let accepted = false;
  const ui = setup({ respond: async path => {
    if (path.endsWith("/turns")) { accepted = true; return response(receipt("running")); }
    if (accepted && path === "/api/sessions/" + SESSION) return response({ error: { code: "read_failed", message: "No pude leer historial" } }, 500);
  } });
  await ui.app.connect(); ui.element("message-input").value = "Persistir referencia"; await ui.app.send();
  assert.equal(ui.app.state.pending.request_id, REQUEST); assert.equal(loadPending(ui.storage).request_id, REQUEST);
  assert.equal(ui.posts().length, 1); assert.equal(ui.element("send-message").disabled, true);
});

test("ocupado descubre el turno de otra pestaña y lo observa sin perder el borrador", async () => {
  const otherRequest = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
  let busyObserved = false;
  const ui = setup({ respond: async path => {
    if (path.endsWith("/turns")) {
      busyObserved = true;
      return response({ error: { code: "busy", message: "Ya hay un turno activo" } }, 409);
    }
    if (busyObserved && path === "/api/bootstrap") return response({ csrf_token: "new-csrf", active_request: { ...receipt("running"), request_id: otherRequest } });
  } });
  await ui.app.connect(); ui.element("message-input").value = "Conservar para después";
  await ui.app.send();
  assert.equal(ui.posts().length, 1); assert.equal(ui.app.state.pending.request_id, otherRequest);
  assert.equal(ui.element("message-input").value, "Conservar para después");
  assert.equal(ui.element("send-message").disabled, true); assert.equal(ui.app.state.csrf, "new-csrf");
  assert.deepEqual(loadPending(ui.storage), { request_id: otherRequest, session_id: SESSION });
});

test("un fallo 500 al consultar el envío ofrece reconectar y conserva el UUID", async () => {
  let lookupFailed = true;
  const storage = memoryStorage(); storePending(storage, { request_id: REQUEST, session_id: SESSION });
  const ui = setup({ storage, respond: async path => {
    if (path.startsWith("/api/requests/")) return lookupFailed
      ? response({ error: { code: "read_failed", message: "No pude consultar el turno" } }, 500)
      : response(receipt("completed"));
  } });
  await ui.app.connect();
  assert.equal(ui.element("reconnect").hidden, false);
  assert.equal(ui.app.state.pending.request_id, REQUEST);
  assert.equal(loadPending(ui.storage).request_id, REQUEST);
  assert.equal(ui.element("message-input").disabled, true);
  assert.equal(ui.timers.size, 0); assert.equal(ui.posts().length, 0);
  lookupFailed = false; await ui.element("reconnect").emit("click");
  assert.equal(ui.app.state.pending, null); assert.equal(loadPending(ui.storage), null);
  assert.equal(ui.posts().length, 0);
});

test("un cierre confirmado mantiene solo lectura aunque falle actualizar la lista", async () => {
  const closed = { ...session, closed_at: "2026-10-04T15:00:00Z" };
  let closeConfirmed = false;
  const ui = setup({ respond: async path => {
    if (path.endsWith("/close")) { closeConfirmed = true; return response({ session: closed }); }
    if (closeConfirmed && path === "/api/sessions") return response({ error: { code: "read_failed", message: "Falló leer conversaciones" } }, 500);
  } });
  await ui.app.connect(); await ui.app.closeSession();
  assert.equal(ui.app.state.session.closed_at, closed.closed_at);
  assert.equal(ui.element("message-input").disabled, true);
  assert.equal(ui.element("close-session").hidden, true);
  assert.match(ui.element("session-subtitle").textContent, /Cerrada · Solo lectura/);
  assert.equal(ui.app.state.sessions[0].closed_at, closed.closed_at);
  ui.element("message-input").value = "No reabrir"; await ui.app.send();
  assert.equal(ui.posts().length, 1); assert.ok(ui.posts()[0].path.endsWith("/close"));
});

test("fallar bootstrap al reconectar un pendiente mantiene disponible otra consulta", async () => {
  let bootstraps = 0;
  const storage = memoryStorage(); storePending(storage, { request_id: REQUEST, session_id: SESSION });
  const ui = setup({ storage, respond: async path => {
    if (path === "/api/bootstrap" && ++bootstraps > 1) return response({ error: { code: "read_failed", message: "No disponible" } }, 500);
    if (path.startsWith("/api/requests/")) return response({ error: { code: "read_failed", message: "No disponible" } }, 500);
  } });
  await ui.app.connect(); await ui.element("reconnect").emit("click");
  assert.equal(ui.element("reconnect").hidden, false);
  assert.equal(ui.app.state.pending.request_id, REQUEST);
  assert.equal(loadPending(ui.storage).request_id, REQUEST);
  assert.equal(ui.posts().length, 0);
});

test("reconectar durante una consulta no revive un turno que ya terminó", async () => {
  let race = false, listCalls = 0;
  const pendingReceipt = deferred(), bootstrapSnapshot = deferred(), oldHistory = deferred();
  const ui = setup({ respond: async path => {
    if (!race) return null;
    if (path.startsWith("/api/requests/")) return pendingReceipt.promise;
    if (path === "/api/bootstrap") return bootstrapSnapshot.promise;
    if (path === "/api/sessions" && ++listCalls === 1) return oldHistory.promise;
  } });
  await ui.app.connect(); ui.element("message-input").value = "No revivir"; await ui.app.send();
  race = true;
  const check = ui.app.checkPending(), reconnect = ui.app.connect();
  await tick(); pendingReceipt.resolve(response(receipt("completed")));
  await tick(); bootstrapSnapshot.resolve(response({ csrf_token: "test-csrf", active_request: receipt("running") }));
  await reconnect; oldHistory.resolve(response({ sessions: [session] })); await check;
  assert.equal(ui.app.state.pending, null); assert.equal(loadPending(ui.storage), null);
  assert.equal(ui.timers.size, 0); assert.equal(ui.element("message-input").disabled, false);
  assert.equal(ui.posts().length, 1);
});

test("terminar un turno en A no cancela la selección humana pendiente de B", async () => {
  const second = { ...session, id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc", title: "Segunda conversación" };
  const secondRead = deferred();
  let completed = false;
  const ui = setup({ respond: async path => {
    if (path === "/api/sessions/" + second.id) return secondRead.promise;
    if (path.startsWith("/api/requests/") && completed) return response(receipt("completed"));
    if (path === "/api/sessions") return response({ sessions: [session, second] });
  } });
  await ui.app.connect(); ui.element("message-input").value = "Turno A"; await ui.app.send();
  const selection = ui.app.selectSession(second.id);
  completed = true; await ui.app.checkPending();
  secondRead.resolve(response({ session: second, messages: [] })); await selection;
  assert.equal(ui.app.state.session.id, second.id);
  assert.equal(ui.element("session-title").textContent, "Segunda conversación");
  assert.equal(ui.app.state.pending, null); assert.equal(ui.posts().length, 1);
});

test("una lectura anterior al cierre no puede volver a mostrar la sesión abierta", async () => {
  let retainRead = false, closed = false;
  const oldRead = deferred();
  const closedSession = { ...session, closed_at: "2026-10-04T15:00:00Z" };
  const ui = setup({ respond: async path => {
    if (path === "/api/sessions/" + SESSION && retainRead) return oldRead.promise;
    if (path.endsWith("/close")) { closed = true; return response({ session: closedSession }); }
    if (path === "/api/sessions" && closed) return response({ error: { code: "internal_error", message: "list failed" } }, 500);
  } });
  await ui.app.connect(); retainRead = true;
  const selection = ui.app.selectSession(SESSION);
  await tick(); await ui.app.closeSession();
  assert.equal(ui.app.state.session.closed_at, closedSession.closed_at);
  oldRead.resolve(response({ session, messages: [] })); await selection;
  assert.equal(ui.app.state.session.closed_at, closedSession.closed_at);
  assert.equal(ui.element("message-input").disabled, true);
  assert.equal(ui.element("close-session").hidden, true);
  assert.match(ui.element("session-subtitle").textContent, /Cerrada · Solo lectura/);
});
