"use strict";
/* 設定ダッシュボード（管理ツール）フロントエンド。ビルド不要の素のJS。
 * バックエンドAPIは admin/server.py 参照。Cookieセッション + CSRFヘッダ
 * （X-Locohane-Admin: 1、更新系リクエストのみ）で認証する。
 */

const $ = (sel, root) => (root || document).querySelector(sel);
const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

function clone(tplId) {
  const tpl = document.getElementById(tplId);
  return tpl.content.cloneNode(true);
}

async function api(path, opts) {
  opts = opts || {};
  const method = opts.method || "GET";
  const headers = { "Content-Type": "application/json" };
  if (method !== "GET") headers["X-Locohane-Admin"] = "1";
  const res = await fetch(path, {
    method,
    headers,
    credentials: "same-origin",
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = await res.json();
      if (data && data.detail) detail = data.detail;
    } catch (e) {
      /* ignore */
    }
    const err = new Error(detail);
    err.status = res.status;
    throw err;
  }
  if (res.status === 204) return null;
  return res.json();
}

const app = $("#app");

/* ---------------------------------------------------------------- 起動 */

async function boot() {
  try {
    const me = await api("/api/me");
    showShell(me.username);
  } catch (e) {
    showLogin();
  }
}

function showLogin(message) {
  app.innerHTML = "";
  app.appendChild(clone("tpl-login"));
  if (message) $("#login-error").textContent = message;
  $("#login-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const form = new FormData(ev.target);
    try {
      const result = await api("/api/login", {
        method: "POST",
        body: { username: form.get("username"), password: form.get("password") },
      });
      showShell(result.username);
    } catch (e) {
      $("#login-error").textContent = e.message || "ログインに失敗しました。";
    }
  });
}

let mainEl = null;

function showShell(username) {
  app.innerHTML = "";
  app.appendChild(clone("tpl-shell"));
  $("#me-username").textContent = username;
  mainEl = $("#main");
  $("#logout-btn").addEventListener("click", async () => {
    await api("/api/logout", { method: "POST" }).catch(() => {});
    showLogin();
  });
  $$(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      $$(".tab-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      navigate(btn.dataset.view);
    });
  });
  navigate("instances");
}

function navigate(view) {
  if (view === "instances") return renderInstances();
  if (view === "settings") return renderSettings();
  if (view === "audit") return renderAudit();
}

/* ---------------------------------------------------- インスタンス一覧 */

const STATE_LABELS = {
  running: "稼働中",
  stopped: "停止中",
  external: "外部で起動中",
  crashed: "異常終了",
  error: "設定エラー",
};

let instanceCache = [];

async function renderInstances() {
  mainEl.innerHTML = "";
  mainEl.appendChild(clone("tpl-instances"));
  $("#add-instance-btn").addEventListener("click", openCreateInstanceModal);
  await refreshInstanceCards();
}

async function refreshInstanceCards() {
  const data = await api("/api/instances");
  instanceCache = data.instances;
  const container = $("#instance-cards");
  if (!container) return;
  container.innerHTML = "";
  for (const inst of instanceCache) {
    const card = clone("tpl-instance-card").firstElementChild;
    card.querySelector(".state-dot").classList.add(inst.state);
    card.querySelector(".display-name").textContent = inst.display_name;
    card.querySelector(".badge-default").classList.toggle("hidden", !inst.is_default);
    card.querySelector(".addr").textContent = `${inst.app_host}:${inst.app_port}`;
    card.querySelector(".state-label").textContent = STATE_LABELS[inst.state] || inst.state;
    const link = card.querySelector(".open-link");
    link.href = inst.url;

    const btnStart = card.querySelector(".btn-start");
    const btnStop = card.querySelector(".btn-stop");
    const btnRestart = card.querySelector(".btn-restart");
    const btnEdit = card.querySelector(".btn-edit");
    const btnDelete = card.querySelector(".btn-delete");

    btnStart.disabled = inst.state === "running" || inst.state === "external";
    btnStop.disabled = inst.state !== "running";
    btnRestart.disabled = inst.state !== "running";
    btnDelete.disabled = inst.state === "running" || inst.state === "external" || inst.is_default;

    btnStart.addEventListener("click", () => runInstanceAction(inst.name, "start"));
    btnStop.addEventListener("click", () => {
      if (confirm(`${inst.display_name} を停止します。生成中の処理は中断されます。よろしいですか？`)) {
        runInstanceAction(inst.name, "stop");
      }
    });
    btnRestart.addEventListener("click", () => {
      if (confirm(`${inst.display_name} を再起動します。生成中の処理は中断されます。よろしいですか？`)) {
        runInstanceAction(inst.name, "restart");
      }
    });
    btnDelete.addEventListener("click", async () => {
      const typed = prompt(`削除するには、インスタンス名 "${inst.name}" を入力してください（データも削除されます）。`);
      if (typed !== inst.name) return;
      try {
        await api(`/api/instances/${encodeURIComponent(inst.name)}`, { method: "DELETE" });
        await refreshInstanceCards();
      } catch (e) {
        alert("削除に失敗しました: " + e.message);
      }
    });
    btnEdit.addEventListener("click", () => openEditInstanceModal(inst));
    card.querySelector(".btn-config").addEventListener("click", () => renderInstanceDetail(inst.name));

    container.appendChild(card);
  }
}

async function runInstanceAction(name, action) {
  try {
    await api(`/api/instances/${encodeURIComponent(name)}/${action}`, { method: "POST" });
  } catch (e) {
    alert(`操作に失敗しました: ${e.message}`);
  }
  await refreshInstanceCards();
}

function openCreateInstanceModal() {
  const modal = clone("tpl-create-instance").firstElementChild;
  document.body.appendChild(modal);
  const select = modal.querySelector("select[name=copy_from]");
  for (const inst of instanceCache) {
    const opt = document.createElement("option");
    opt.value = inst.name;
    opt.textContent = inst.display_name;
    select.appendChild(opt);
  }
  modal.querySelector(".btn-cancel").addEventListener("click", () => modal.remove());
  modal.querySelector("form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const form = new FormData(ev.target);
    const body = {
      name: form.get("name"),
      display_name: form.get("display_name") || null,
      app_host: form.get("app_host") || "127.0.0.1",
      app_port: form.get("app_port") ? Number(form.get("app_port")) : null,
      autostart: form.get("autostart") === "on",
      headless: form.get("headless") === "on",
      watch: form.get("watch") === "on",
      copy_from: form.get("copy_from") || null,
    };
    try {
      await api("/api/instances", { method: "POST", body });
      modal.remove();
      await refreshInstanceCards();
    } catch (e) {
      modal.querySelector("#create-instance-error").textContent = e.message;
    }
  });
}

function openEditInstanceModal(inst) {
  const modal = clone("tpl-edit-instance").firstElementChild;
  document.body.appendChild(modal);
  modal.querySelector(".edit-instance-name").textContent = inst.name;
  const form = modal.querySelector("form");
  form.elements.display_name.value = inst.display_name;
  form.elements.app_host.value = inst.app_host;
  form.elements.app_port.value = inst.app_port;
  form.elements.autostart.checked = inst.autostart;
  form.elements.headless.checked = inst.headless;
  form.elements.watch.checked = inst.watch;

  modal.querySelector(".btn-cancel").addEventListener("click", () => modal.remove());
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const data = new FormData(ev.target);
    const body = {
      display_name: data.get("display_name") || null,
      app_host: data.get("app_host") || null,
      app_port: data.get("app_port") ? Number(data.get("app_port")) : null,
      autostart: data.get("autostart") === "on",
      headless: data.get("headless") === "on",
      watch: data.get("watch") === "on",
    };
    try {
      const portOrHostChanged = body.app_host !== inst.app_host || body.app_port !== inst.app_port;
      await api(`/api/instances/${encodeURIComponent(inst.name)}`, { method: "PUT", body });
      modal.remove();
      await refreshInstanceCards();
      if (portOrHostChanged && (inst.state === "running")) {
        alert("ホスト・ポートを変更しました。反映するにはインスタンスを再起動してください。");
      }
    } catch (e) {
      modal.querySelector("#edit-instance-error").textContent = e.message;
    }
  });
}

/* ------------------------------------------------------- インスタンス詳細 */

let currentInstanceName = null;

async function renderInstanceDetail(name) {
  currentInstanceName = name;
  mainEl.innerHTML = "";
  mainEl.appendChild(clone("tpl-instance-detail"));
  $(".detail-name").textContent = name;
  $(".back-btn").addEventListener("click", () => renderInstances());
  $$(".subtab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      $$(".subtab-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      renderInstanceSubview(btn.dataset.subview);
    });
  });
  await renderInstanceSubview("config");
}

function renderInstanceSubview(view) {
  const container = $("#instance-subview");
  container.innerHTML = "";
  if (view === "config") return renderConfigEditor(container);
  if (view === "users") return renderUsers(container);
  if (view === "env") return renderEnvVars(container);
  if (view === "display") return renderSettingsPanel(container, currentInstanceName);
  if (view === "backups") return renderBackups(container);
}

/* --- config.ini 編集 --- */

let configKeys = [];
let configMtime = null;
let dirtyEdits = {}; // "section.key" -> 新しい値の文字列
let resetKeys = new Set(); // "section.key" 既定値へ戻すよう指定

async function renderConfigEditor(container) {
  container.appendChild(clone("tpl-config-editor"));
  dirtyEdits = {};
  resetKeys = new Set();
  await loadConfigKeys();
  buildSectionList();
  renderKeyList();
  updateFooter();

  $("#config-search").addEventListener("input", renderKeyList);
  $("#config-changed-only").addEventListener("change", renderKeyList);
  $("#preview-btn").addEventListener("click", openPreviewModal);
  $("#save-btn").addEventListener("click", () => doSave(null));
}

async function loadConfigKeys() {
  const data = await api(`/api/instances/${encodeURIComponent(currentInstanceName)}/config`);
  configKeys = data.keys;
  configMtime = data.mtime;
}

let activeSection = null;

function buildSectionList() {
  const sections = [];
  const seen = new Set();
  for (const k of configKeys) {
    if (!seen.has(k.section)) {
      seen.add(k.section);
      sections.push(k.section);
    }
  }
  const list = $("#section-list");
  list.innerHTML = "";
  const allLi = document.createElement("li");
  allLi.textContent = "すべて";
  allLi.dataset.section = "";
  allLi.classList.add("active");
  list.appendChild(allLi);
  for (const sec of sections) {
    const li = document.createElement("li");
    const count = configKeys.filter((k) => k.section === sec).length;
    li.innerHTML = `[${sec}] <span class="count">${count}</span>`;
    li.dataset.section = sec;
    list.appendChild(li);
  }
  activeSection = "";
  $$("li", list).forEach((li) => {
    li.addEventListener("click", () => {
      activeSection = li.dataset.section;
      $$("li", list).forEach((x) => x.classList.remove("active"));
      li.classList.add("active");
      renderKeyList();
    });
  });
}

function keyId(k) {
  return `${k.section}.${k.key}`;
}

function effectiveValue(k) {
  const id = keyId(k);
  if (id in dirtyEdits) return dirtyEdits[id];
  if (resetKeys.has(id)) return k.default;
  return k.effective;
}

function isDirty(k) {
  const id = keyId(k);
  return id in dirtyEdits || resetKeys.has(id);
}

function renderKeyList() {
  const query = $("#config-search").value.trim().toLowerCase();
  const changedOnly = $("#config-changed-only").checked;
  const listEl = $("#config-keys");
  listEl.innerHTML = "";
  let shown = 0;
  for (const k of configKeys) {
    if (activeSection && k.section !== activeSection) continue;
    if (query && !(`${k.section}.${k.key}`.toLowerCase().includes(query) || k.description.toLowerCase().includes(query))) continue;
    if (changedOnly && !isDirty(k) && k.override === null) continue;
    listEl.appendChild(buildKeyRow(k));
    shown++;
  }
  if (shown === 0) {
    listEl.innerHTML = '<p class="hint">該当するキーがありません。</p>';
  }
}

function buildKeyRow(k) {
  const row = clone("tpl-key-row").firstElementChild;
  const id = keyId(k);
  row.querySelector(".key-label").textContent = `[${k.section}] ${k.key}`;
  row.querySelector(".key-desc").textContent = k.description;
  row.querySelector(".env-badge").classList.toggle("hidden", !k.env_override_active);
  if (k.env_override_active) {
    row.querySelector(".env-badge").textContent = `環境変数 ${k.env_name} で上書き中`;
  }
  const changedBadge = row.querySelector(".changed-badge");
  const resetBtn = row.querySelector(".reset-btn");
  const inputWrap = row.querySelector(".key-input-wrap");

  const currentlyOverridden = k.override !== null || id in dirtyEdits;
  changedBadge.classList.toggle("hidden", !(isDirty(k) || (k.override !== null && !resetKeys.has(id))));
  resetBtn.classList.toggle("hidden", !currentlyOverridden || resetKeys.has(id));

  const value = effectiveValue(k);

  if (k.is_admin_section) {
    const note = document.createElement("p");
    note.className = "hint";
    note.textContent = "[admin] は管理ツール自身の設定のため、ここからは変更できません（config.ini を直接編集してください）。";
    inputWrap.appendChild(note);
  } else if (k.ui_kind === "bool") {
    const wrap = document.createElement("label");
    wrap.className = "checkbox";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.checked = value.trim().toLowerCase() === "true";
    input.addEventListener("change", () => setEdit(k, input.checked ? "true" : "false"));
    wrap.appendChild(input);
    wrap.appendChild(document.createTextNode(" 有効にする"));
    inputWrap.appendChild(wrap);
  } else if (k.ui_kind === "choice") {
    const select = buildChoiceSelect(k.choices, value.trim(), (v) => setEdit(k, v));
    select.classList.add("choice-select");
    inputWrap.appendChild(select);
  } else if (k.ui_kind === "list") {
    buildListInput(k, value, inputWrap);
  } else if (k.ui_kind === "multiline") {
    inputWrap.appendChild(buildRawTextarea(k, value));
  } else {
    const input = document.createElement("input");
    input.type = "text";
    input.value = value;
    input.addEventListener("input", () => setEdit(k, input.value));
    inputWrap.appendChild(input);
  }

  resetBtn.addEventListener("click", () => {
    delete dirtyEdits[id];
    resetKeys.add(id);
    renderKeyList();
    updateFooter();
  });

  return row;
}

function buildRawTextarea(k, value) {
  const textarea = document.createElement("textarea");
  textarea.value = value;
  textarea.rows = Math.min(16, Math.max(3, value.split("\n").length));
  textarea.addEventListener("input", () => setEdit(k, textarea.value));
  return textarea;
}

// 固有キーワードの選択肢から選ぶ select。現在値が選択肢に無い場合（手動編集等）は
// その値も選択肢に残し、意図せず別の値へ書き換わらないようにする。
function buildChoiceSelect(choices, current, onChange) {
  const select = document.createElement("select");
  const matched = choices.find((c) => c === current) ?? choices.find((c) => c.toLowerCase() === current.toLowerCase());
  const options = matched === undefined ? [current, ...choices] : choices;
  for (const c of options) {
    const opt = document.createElement("option");
    opt.value = c;
    if (c === "") opt.textContent = "（未指定）";
    else if (matched === undefined && c === current) opt.textContent = `${c}（選択肢外の現在値）`;
    else opt.textContent = c;
    select.appendChild(opt);
  }
  select.value = matched === undefined ? current : matched;
  select.addEventListener("change", () => onChange(select.value));
  return select;
}

/* --- リスト値（[...]）の項目ごと編集 ---
 * config.ini のリスト値は src/config.py で ast.literal_eval される Python リテラル
 * なので、ここでもその部分集合（文字列・数値・True/False/None・リスト/タプル・
 * 辞書・# コメント・末尾カンマ）をパース/シリアライズする。パースできない値は
 * 従来どおりテキストで編集する。 */

function parsePyLiteral(text) {
  const s = text;
  let i = 0;
  const fail = (msg) => {
    throw new Error(`${msg}（${i + 1}文字目付近）`);
  };
  const skip = () => {
    for (;;) {
      while (i < s.length && /\s/.test(s[i])) i++;
      if (s[i] !== "#") return;
      while (i < s.length && s[i] !== "\n") i++;
    }
  };
  const ESC = { n: "\n", t: "\t", r: "\r", "\\": "\\", "'": "'", '"': '"', 0: "\0", a: "\x07", b: "\b", f: "\f", v: "\v" };
  const parseStr = () => {
    const q = s[i++];
    let out = "";
    while (i < s.length && s[i] !== q) {
      let c = s[i++];
      if (c === "\n") fail("文字列の途中で改行されています");
      if (c !== "\\") {
        out += c;
        continue;
      }
      c = s[i++];
      if (c === "\n") continue;
      if (c === "x" || c === "u" || c === "U") {
        const len = c === "x" ? 2 : c === "u" ? 4 : 8;
        const hex = s.slice(i, i + len);
        if (hex.length !== len || !/^[0-9a-fA-F]+$/.test(hex)) fail("不正なエスケープです");
        out += String.fromCodePoint(parseInt(hex, 16));
        i += len;
      } else if (c in ESC) {
        out += ESC[c];
      } else {
        out += "\\" + c;
      }
    }
    if (s[i] !== q) fail("文字列が閉じていません");
    i++;
    return out;
  };
  const parseValue = () => {
    skip();
    const c = s[i];
    if (c === "[" || c === "(") {
      const close = c === "[" ? "]" : ")";
      i++;
      const arr = [];
      for (;;) {
        skip();
        if (s[i] === close) break;
        arr.push(parseValue());
        skip();
        if (s[i] === ",") i++;
        else if (s[i] !== close) fail(`"," か "${close}" が必要です`);
      }
      i++;
      return arr;
    }
    if (c === "{") {
      i++;
      const obj = {};
      for (;;) {
        skip();
        if (s[i] === "}") break;
        const key = parseValue();
        if (typeof key !== "string") fail("辞書のキーは文字列にしてください");
        skip();
        if (s[i] !== ":") fail('":" が必要です');
        i++;
        obj[key] = parseValue();
        skip();
        if (s[i] === ",") i++;
        else if (s[i] !== "}") fail('"," か "}" が必要です');
      }
      i++;
      return obj;
    }
    if (c === '"' || c === "'") {
      let out = parseStr();
      // Python の暗黙の文字列連結（"a" "b"）
      for (;;) {
        const save = i;
        skip();
        if (s[i] === '"' || s[i] === "'") {
          out += parseStr();
        } else {
          i = save;
          return out;
        }
      }
    }
    const m = /^[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?/.exec(s.slice(i));
    if (m) {
      i += m[0].length;
      return Number(m[0]);
    }
    for (const [word, val] of [["True", true], ["False", false], ["None", null]]) {
      if (s.startsWith(word, i) && !/\w/.test(s[i + word.length] || "")) {
        i += word.length;
        return val;
      }
    }
    fail("解釈できない値です");
  };
  const result = parseValue();
  skip();
  if (i < s.length) fail("余分な文字があります");
  return result;
}

function toPyLiteral(v) {
  if (v === null) return "None";
  if (v === true) return "True";
  if (v === false) return "False";
  if (typeof v === "number") return String(v);
  if (typeof v === "string") return JSON.stringify(v);
  if (Array.isArray(v)) return "[" + v.map(toPyLiteral).join(", ") + "]";
  return "{" + Object.entries(v).map(([key, x]) => `${JSON.stringify(key)}: ${toPyLiteral(x)}`).join(", ") + "}";
}

// config.ini と同じ「1要素1行・末尾カンマ・閉じ括弧はインデント付き」の形で書き出す。
function serializePyList(arr) {
  if (arr.length === 0) return "[]";
  return "[\n" + arr.map((x) => `    ${toPyLiteral(x)},\n`).join("") + "    ]";
}

function parsePyList(text) {
  const data = parsePyLiteral(text.trim() || "[]");
  if (!Array.isArray(data)) throw new Error("リスト（[...]）形式ではありません");
  return data;
}

const rawListIds = new Set();

function rerenderKeyRow(k, inputWrap) {
  inputWrap.closest(".key-row").replaceWith(buildKeyRow(k));
}

function buildListInput(k, value, inputWrap) {
  const id = keyId(k);
  const toolbar = document.createElement("div");
  toolbar.className = "le-toolbar";
  const toggle = document.createElement("button");
  toggle.type = "button";
  toggle.className = "le-btn";
  const note = document.createElement("span");
  note.className = "hint";
  toolbar.append(toggle, note);
  inputWrap.appendChild(toolbar);

  let data = null;
  let parseError = null;
  if (!rawListIds.has(id)) {
    try {
      data = parsePyList(value);
    } catch (e) {
      parseError = e.message;
    }
  }

  if (data === null) {
    toggle.textContent = "項目ごとに編集";
    if (parseError) {
      note.classList.replace("hint", "error");
      note.textContent = `項目ごとの編集に切り替えられません: ${parseError}`;
    }
    toggle.addEventListener("click", () => {
      try {
        parsePyList(effectiveValue(k));
      } catch (e) {
        note.classList.replace("hint", "error");
        note.textContent = `項目ごとの編集に切り替えられません: ${e.message}`;
        return;
      }
      rawListIds.delete(id);
      rerenderKeyRow(k, inputWrap);
    });
    inputWrap.appendChild(buildRawTextarea(k, value));
    return;
  }

  toggle.textContent = "テキストで編集";
  toggle.addEventListener("click", () => {
    rawListIds.add(id);
    rerenderKeyRow(k, inputWrap);
  });
  if (/^\s*#/m.test(value)) {
    note.textContent = "※ここで編集して保存すると、リスト内のコメント行は保存値に含まれません（config.ini 自体は変わりません）。";
  }
  let defaultCanon = null;
  try {
    defaultCanon = serializePyList(parsePyList(k.default));
  } catch (e) {
    // 既定値がパースできない場合は、既定値との一致判定をしない
  }
  const emit = () => {
    const text = serializePyList(data);
    // 書式だけが違う（中身は既定値と同じ）場合は「変更なし」として扱う
    setEdit(k, text === defaultCanon ? k.default : text);
  };
  if (k.schema && schemaMatches(k.schema, data)) {
    inputWrap.appendChild(
      buildSchemaEditor(data, k.schema, (v) => {
        data = v;
        emit();
      })
    );
    return;
  }
  if (k.schema) {
    note.textContent = "※値が想定の形式と異なるため、要素の種類を選んで編集する形式で表示しています。";
  }
  inputWrap.appendChild(buildArrayEditor(data, emit, { depth: 0 }));
}

/* --- スキーマ付きリストの編集 ---
 * admin/ini_catalog.py の _KEY_SCHEMAS で構造が決まっているキーは、型を選ばせず
 * 「スキル名」「スクリプトファイル名」のような名前付きの入力欄で編集させる。 */

function schemaDefault(schema) {
  switch (schema.type) {
    case "number":
      return schema.default ?? 0;
    case "choice":
      return schema.choices[0];
    case "list":
    case "grouped":
      return [];
    case "tuple":
      return schema.items.map((it) => schemaDefault(it.schema));
    case "dict": {
      const obj = {};
      for (const f of schema.fields) if (!f.optional) obj[f.name] = schemaDefault(f.schema);
      return obj;
    }
    case "oneof":
      return schemaDefault(schema.variants[0].schema);
    default:
      return schema.default ?? "";
  }
}

function schemaMatches(schema, v) {
  switch (schema.type) {
    case "number":
      return typeof v === "number";
    case "list":
      return Array.isArray(v) && v.every((x) => schemaMatches(schema.item, x));
    case "grouped":
      return Array.isArray(v) && v.every((x) => Array.isArray(x) && x.length === 2 && typeof x[0] === "string" && schemaMatches(schema.item, x[1]));
    case "tuple":
      return Array.isArray(v) && v.length === schema.items.length && schema.items.every((it, i) => schemaMatches(it.schema, v[i]));
    case "dict": {
      if (v === null || typeof v !== "object" || Array.isArray(v)) return false;
      const names = new Set(schema.fields.map((f) => f.name));
      // 未知のキーがあると画面に出せず保存時に消えてしまうため、汎用エディタに任せる
      if (Object.keys(v).some((key) => !names.has(key))) return false;
      return schema.fields.every((f) => (f.name in v ? schemaMatches(f.schema, v[f.name]) : f.optional));
    }
    case "oneof":
      return schema.variants.some((variant) => schemaMatches(variant.schema, v));
    default:
      // string / choice（選択肢外の値は選択肢に残して表示する）
      return typeof v === "string";
  }
}

// set(v) は親に「この値が v に変わった」ことを伝える。リスト・タプル・辞書は
// 自分自身の中身を書き換えてから同じ参照で set する。
function buildSchemaEditor(value, schema, set, optional = false) {
  switch (schema.type) {
    case "list":
      return buildSchemaList(value, schema, set);
    case "grouped":
      return buildSchemaGrouped(value, schema, set);
    case "tuple":
      return buildSchemaTuple(value, schema, set);
    case "dict":
      return buildSchemaDict(value, schema, set);
    case "oneof":
      return buildSchemaOneOf(value, schema, set);
    case "choice":
      return buildChoiceSelect(optional ? ["", ...schema.choices] : schema.choices, value ?? "", set);
    case "number": {
      const input = document.createElement("input");
      input.type = "number";
      input.step = "any";
      input.placeholder = schema.placeholder || "";
      input.value = typeof value === "number" ? String(value) : "";
      input.addEventListener("input", () => {
        const text = input.value.trim();
        if (text === "" && optional) {
          input.classList.remove("invalid");
          set(undefined);
          return;
        }
        const n = Number(text);
        const ok = text !== "" && Number.isFinite(n);
        input.classList.toggle("invalid", !ok);
        if (ok) set(n);
      });
      return input;
    }
    default: {
      const text = value ?? "";
      const long = text.length > 60 || text.includes("\n");
      const input = document.createElement(long ? "textarea" : "input");
      if (long) input.rows = Math.min(8, Math.max(2, Math.ceil(text.length / 80)));
      else input.type = "text";
      input.placeholder = schema.placeholder || "";
      input.value = text;
      input.addEventListener("input", () => set(input.value));
      return input;
    }
  }
}

function buildSchemaList(arr, schema, set) {
  const box = document.createElement("div");
  box.className = "le-list";
  const render = () => {
    box.innerHTML = "";
    arr.forEach((item, idx) => {
      const itemEl = document.createElement("div");
      itemEl.className = "le-item";
      const body = document.createElement("div");
      body.className = "le-item-body";
      body.appendChild(
        buildSchemaEditor(item, schema.item, (v) => {
          arr[idx] = v;
          set(arr);
        })
      );
      const ctrl = document.createElement("div");
      ctrl.className = "le-item-ctrl";
      const swap = (a, b) => {
        [arr[a], arr[b]] = [arr[b], arr[a]];
        render();
        set(arr);
      };
      ctrl.append(
        smallBtn("↑", "上へ移動", idx === 0, () => swap(idx - 1, idx)),
        smallBtn("↓", "下へ移動", idx === arr.length - 1, () => swap(idx, idx + 1)),
        smallBtn("✕", "削除", false, () => {
          arr.splice(idx, 1);
          render();
          set(arr);
        })
      );
      itemEl.append(body, ctrl);
      box.appendChild(itemEl);
    });
    const add = smallBtn("＋ 項目を追加", "項目を追加", false, () => {
      arr.push(schemaDefault(schema.item));
      render();
      set(arr);
    });
    add.classList.add("le-add");
    box.appendChild(add);
  };
  render();
  return box;
}

// [[グループ名, item], ...] をグループ名ごとのパネルで編集する。画面上は
// グループ単位の状態を持ち、変更のたびに元の形のリスト（arr）へ書き戻す。
function buildSchemaGrouped(arr, schema, set) {
  const groups = [];
  for (const [name, item] of arr) {
    let g = groups.find((x) => x.name === name);
    if (!g) groups.push((g = { name, items: [] }));
    g.items.push(item);
  }
  const flush = () => {
    arr.length = 0;
    for (const g of groups) for (const item of g.items) arr.push([g.name, item]);
    set(arr);
  };

  const box = document.createElement("div");
  box.className = "le-list";
  const render = () => {
    box.innerHTML = "";
    groups.forEach((g, gi) => {
      const panel = document.createElement("div");
      panel.className = "le-group";
      const head = document.createElement("div");
      head.className = "le-group-head";
      const label = document.createElement("span");
      label.className = "le-sfield-label";
      label.textContent = schema.group_label;
      const nameInput = document.createElement("input");
      nameInput.type = "text";
      nameInput.placeholder = schema.group_placeholder || "";
      nameInput.value = g.name;
      nameInput.addEventListener("input", () => {
        g.name = nameInput.value;
        flush();
      });
      const count = document.createElement("span");
      count.className = "hint";
      count.textContent = `${g.items.length} 件`;
      head.append(
        label,
        nameInput,
        count,
        smallBtn("✕", `この${schema.group_label}をまとめて削除`, false, () => {
          if (g.items.length > 0 && !confirm(`${schema.group_label}「${g.name}」の ${g.items.length} 件をまとめて削除しますか？`)) return;
          groups.splice(gi, 1);
          render();
          flush();
        })
      );
      panel.appendChild(head);
      // グループ内の項目は通常のリストと同じ編集UI（並べ替え・削除・追加）
      panel.appendChild(buildSchemaList(g.items, { type: "list", item: schema.item }, () => {
        count.textContent = `${g.items.length} 件`;
        flush();
      }));
      box.appendChild(panel);
    });
    const add = smallBtn(`＋ ${schema.group_label}を追加`, `${schema.group_label}を追加`, false, () => {
      // 項目が0件のグループはリストに書き出せないため、空の項目を1件持たせる
      groups.push({ name: "", items: [schemaDefault(schema.item)] });
      render();
      flush();
    });
    add.classList.add("le-add");
    box.appendChild(add);
  };
  render();
  return box;
}

function buildSchemaTuple(arr, schema, set) {
  const box = document.createElement("div");
  box.className = "le-tuple";
  schema.items.forEach((it, idx) => {
    const fieldEl = document.createElement("div");
    fieldEl.className = it.schema.type === "number" ? "le-sfield le-sfield-narrow" : "le-sfield";
    const label = document.createElement("span");
    label.className = "le-sfield-label";
    label.textContent = it.label;
    fieldEl.append(
      label,
      buildSchemaEditor(arr[idx], it.schema, (v) => {
        arr[idx] = v;
        set(arr);
      })
    );
    box.appendChild(fieldEl);
  });
  return box;
}

function buildSchemaDict(obj, schema, set) {
  const box = document.createElement("div");
  box.className = "le-dict";
  for (const f of schema.fields) {
    const row = document.createElement("div");
    row.className = "le-field";
    const label = document.createElement("span");
    label.className = "le-field-name";
    label.textContent = f.optional ? `${f.name}（任意）` : f.name;
    const body = document.createElement("div");
    body.className = "le-item-body";
    body.appendChild(
      buildSchemaEditor(
        obj[f.name],
        f.schema,
        (v) => {
          // 任意項目は空欄なら辞書から取り除く（src/config.py 側で「未指定」扱い）
          if (v === undefined || (f.optional && v === "")) delete obj[f.name];
          else obj[f.name] = v;
          set(obj);
        },
        !!f.optional
      )
    );
    row.append(label, body);
    box.appendChild(row);
  }
  return box;
}

function buildSchemaOneOf(value, schema, set) {
  const box = document.createElement("div");
  box.className = "le-oneof";
  let current = value;
  const setCurrent = (v) => {
    current = v;
    set(v);
  };
  const select = document.createElement("select");
  select.className = "le-type";
  schema.variants.forEach((variant, i) => {
    const opt = document.createElement("option");
    opt.value = String(i);
    opt.textContent = variant.label;
    select.appendChild(opt);
  });
  const body = document.createElement("div");
  body.className = "le-oneof-body";
  const renderBody = (i) => {
    body.innerHTML = "";
    body.appendChild(buildSchemaEditor(current, schema.variants[i].schema, setCurrent));
  };
  const initial = Math.max(0, schema.variants.findIndex((variant) => schemaMatches(variant.schema, value)));
  select.value = String(initial);
  select.addEventListener("change", () => {
    // 形を切り替えても、入力済みの先頭の文字列（スキル名等）は引き継ぐ
    const head = typeof current === "string" ? current : Array.isArray(current) && typeof current[0] === "string" ? current[0] : "";
    const next = schemaDefault(schema.variants[Number(select.value)].schema);
    if (typeof next === "string") setCurrent(head);
    else {
      if (Array.isArray(next) && typeof next[0] === "string") next[0] = head;
      setCurrent(next);
    }
    renderBody(Number(select.value));
  });
  renderBody(initial);
  box.append(select, body);
  return box;
}

const VALUE_TYPES = [
  ["string", "文字"],
  ["number", "数値"],
  ["bool", "真偽"],
  ["list", "リスト"],
];

function valueType(v) {
  if (Array.isArray(v)) return "list";
  if (v !== null && typeof v === "object") return "dict";
  if (typeof v === "number") return "number";
  if (typeof v === "boolean") return "bool";
  return "string";
}

function convertValue(v, type) {
  if (type === "string") return typeof v === "string" ? v : v === null || typeof v === "object" ? "" : String(v);
  if (type === "number") return Number.isFinite(Number(v)) && v !== "" && typeof v !== "object" ? Number(v) : 0;
  if (type === "bool") return v === true;
  if (type === "dict") return {};
  if (Array.isArray(v)) return v;
  return v === "" || v === null ? [] : [v];
}

function buildTypeSelect(current, onChange, withDict = false) {
  const select = document.createElement("select");
  select.className = "le-type";
  select.title = "値の種類";
  const types = withDict || current === "dict" ? [...VALUE_TYPES, ["dict", "辞書"]] : VALUE_TYPES;
  for (const [value, label] of types) {
    const opt = document.createElement("option");
    opt.value = value;
    opt.textContent = label;
    select.appendChild(opt);
  }
  select.value = current;
  select.addEventListener("change", () => onChange(select.value));
  return select;
}

function smallBtn(text, title, disabled, onClick) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "le-btn";
  btn.textContent = text;
  btn.title = title;
  btn.disabled = disabled;
  btn.addEventListener("click", onClick);
  return btn;
}

// 追加する要素の雛形。直前の要素と同じ形で、文字列だけ空にする
// （選択肢付きフィールドは有効な値のまま残す）。
function blankLike(v) {
  if (typeof v === "string") return "";
  if (Array.isArray(v)) return v.map(blankLike);
  if (v !== null && typeof v === "object") {
    const obj = {};
    for (const [key, x] of Object.entries(v)) obj[key] = blankLike(x);
    return obj;
  }
  return v;
}

function buildValueEditor(value, set, notify, ctx) {
  if (Array.isArray(value)) return buildArrayEditor(value, notify, ctx);
  if (value !== null && typeof value === "object") return buildDictEditor(value, notify, ctx);
  return buildScalarEditor(value, set);
}

// depth=0（キー直下のリスト）は1要素1行の縦並び、それより深いリスト
// （["スキル名","スクリプト名"] 等の組）は横並びで表示する。
function buildArrayEditor(arr, notify, ctx) {
  const inline = ctx.depth > 0;
  const box = document.createElement("div");
  box.className = inline ? "le-list le-inline" : "le-list";
  const childCtx = { depth: ctx.depth + 1 };
  const render = () => {
    box.innerHTML = "";
    arr.forEach((item, idx) => {
      const itemEl = document.createElement("div");
      itemEl.className = "le-item";
      if (inline) {
        itemEl.appendChild(
          buildTypeSelect(valueType(item), (type) => {
            arr[idx] = convertValue(arr[idx], type);
            render();
            notify();
          })
        );
      }
      const body = document.createElement("div");
      body.className = "le-item-body";
      body.appendChild(
        buildValueEditor(
          item,
          (v) => {
            arr[idx] = v;
            notify();
          },
          notify,
          childCtx
        )
      );
      itemEl.appendChild(body);
      const ctrl = document.createElement("div");
      ctrl.className = "le-item-ctrl";
      if (!inline) {
        const swap = (a, b) => {
          [arr[a], arr[b]] = [arr[b], arr[a]];
          render();
          notify();
        };
        ctrl.appendChild(smallBtn("↑", "上へ移動", idx === 0, () => swap(idx - 1, idx)));
        ctrl.appendChild(smallBtn("↓", "下へ移動", idx === arr.length - 1, () => swap(idx, idx + 1)));
      }
      ctrl.appendChild(
        smallBtn("✕", "削除", false, () => {
          arr.splice(idx, 1);
          render();
          notify();
        })
      );
      itemEl.appendChild(ctrl);
      box.appendChild(itemEl);
    });
    // 空リストには手本にする要素が無いため、追加する値の種類を選ばせる
    const newTypeSelect = arr.length === 0 ? buildTypeSelect("string", () => {}, !inline) : null;
    const add = smallBtn(inline ? "＋" : "＋ 項目を追加", "項目を追加", false, () => {
      arr.push(newTypeSelect ? convertValue("", newTypeSelect.value) : blankLike(arr[arr.length - 1]));
      render();
      notify();
    });
    const addRow = document.createElement("div");
    addRow.className = "le-add";
    if (newTypeSelect) addRow.appendChild(newTypeSelect);
    addRow.appendChild(add);
    box.appendChild(addRow);
  };
  render();
  return box;
}

function buildDictEditor(obj, notify, ctx) {
  const box = document.createElement("div");
  box.className = "le-dict";
  const childCtx = { depth: ctx.depth + 1 };
  const render = () => {
    box.innerHTML = "";
    for (const name of Object.keys(obj)) {
      const row = document.createElement("div");
      row.className = "le-field";
      const label = document.createElement("span");
      label.className = "le-field-name";
      label.textContent = name;
      const body = document.createElement("div");
      body.className = "le-item-body";
      body.appendChild(
        buildValueEditor(
          obj[name],
          (v) => {
            obj[name] = v;
            notify();
          },
          notify,
          childCtx
        )
      );
      row.append(
        label,
        body,
        smallBtn("✕", "この項目を削除", false, () => {
          delete obj[name];
          render();
          notify();
        })
      );
      box.appendChild(row);
    }
    const addRow = document.createElement("div");
    addRow.className = "le-field le-field-add";
    const nameInput = document.createElement("input");
    nameInput.type = "text";
    nameInput.placeholder = "項目名";
    const typeSelect = buildTypeSelect("string", () => {});
    const addBtn = smallBtn("＋ 項目を追加", "項目を追加", false, () => {
      const name = nameInput.value.trim();
      if (!name || name in obj) {
        nameInput.classList.add("invalid");
        return;
      }
      obj[name] = convertValue("", typeSelect.value);
      render();
      notify();
    });
    addRow.append(nameInput, typeSelect, addBtn);
    box.appendChild(addRow);
  };
  render();
  return box;
}

function buildScalarEditor(value, set) {
  if (typeof value === "boolean") {
    const wrap = document.createElement("label");
    wrap.className = "checkbox";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.checked = value;
    input.addEventListener("change", () => set(input.checked));
    wrap.append(input, document.createTextNode(" True"));
    return wrap;
  }
  if (typeof value === "number") {
    const input = document.createElement("input");
    input.type = "number";
    input.step = "any";
    input.value = String(value);
    input.addEventListener("input", () => {
      const n = Number(input.value);
      const ok = input.value.trim() !== "" && Number.isFinite(n);
      input.classList.toggle("invalid", !ok);
      if (ok) set(n);
    });
    return input;
  }
  if (value === null) {
    const span = document.createElement("span");
    span.className = "hint";
    span.textContent = "None";
    return span;
  }
  if (value.length > 60 || value.includes("\n")) {
    const textarea = document.createElement("textarea");
    textarea.value = value;
    textarea.rows = Math.min(8, Math.max(2, Math.ceil(value.length / 80) + value.split("\n").length - 1));
    textarea.addEventListener("input", () => set(textarea.value));
    return textarea;
  }
  const input = document.createElement("input");
  input.type = "text";
  input.value = value;
  input.addEventListener("input", () => set(input.value));
  return input;
}

function setEdit(k, newValue) {
  const id = keyId(k);
  resetKeys.delete(id);
  if (newValue === k.default) {
    delete dirtyEdits[id];
  } else {
    dirtyEdits[id] = newValue;
  }
  updateFooter();
}

function updateFooter() {
  const count = Object.keys(dirtyEdits).length + resetKeys.size;
  $("#dirty-count").textContent = count > 0 ? `${count} 件変更` : "";
  $("#save-btn").disabled = count === 0;
}

function buildUpdatePayload() {
  const updates = {};
  for (const id of Object.keys(dirtyEdits)) {
    const [section, key] = splitKeyId(id, configKeys);
    updates[section] = updates[section] || {};
    updates[section][key] = dirtyEdits[id];
  }
  const resets = Array.from(resetKeys).map((id) => splitKeyId(id, configKeys));
  return { updates, resets, base_mtime: configMtime };
}

function splitKeyId(id, keys) {
  // セクション名自体にドットを含む場合(例: context_trim.subagent)があるため、
  // 先頭一致でキー一覧と突き合わせて正しく分割する。
  for (const k of keys) {
    if (keyId(k) === id) return [k.section, k.key];
  }
  const idx = id.lastIndexOf(".");
  return [id.slice(0, idx), id.slice(idx + 1)];
}

async function openPreviewModal() {
  const modal = clone("tpl-preview-modal").firstElementChild;
  document.body.appendChild(modal);
  modal.querySelector(".btn-cancel").addEventListener("click", () => modal.remove());
  const diffEl = modal.querySelector("#preview-diff");
  diffEl.textContent = "確認中...";
  try {
    const payload = buildUpdatePayload();
    const result = await api(`/api/instances/${encodeURIComponent(currentInstanceName)}/config/preview`, {
      method: "POST",
      body: payload,
    });
    diffEl.innerHTML = "";
    if (result.changes.length === 0) {
      diffEl.innerHTML = '<p class="hint">変更はありません。</p>';
    }
    for (const c of result.changes) {
      const div = document.createElement("div");
      div.className = "diff-item";
      div.innerHTML = `<span class="diff-key">[${c.section}] ${c.key}</span><br/>
        <span class="diff-old">${c.old === null ? "(既定値)" : escapeHtml(c.old)}</span> →
        <span class="diff-new">${c.new === null ? "(既定値)" : escapeHtml(c.new)}</span>`;
      diffEl.appendChild(div);
    }
    modal.querySelector("#confirm-save-btn").addEventListener("click", () => {
      modal.remove();
      doSave(null);
    });
  } catch (e) {
    modal.querySelector("#preview-error").textContent = e.message;
  }
}

function escapeHtml(s) {
  const div = document.createElement("div");
  div.textContent = s;
  return div.innerHTML;
}

async function doSave() {
  const payload = buildUpdatePayload();
  try {
    const result = await api(`/api/instances/${encodeURIComponent(currentInstanceName)}/config`, {
      method: "PUT",
      body: payload,
    });
    dirtyEdits = {};
    resetKeys = new Set();
    await loadConfigKeys();
    renderKeyList();
    updateFooter();
    const banner = $("#restart-banner");
    if (result.needs_restart) {
      banner.classList.remove("hidden");
      const btn = $("#restart-now-btn");
      btn.onclick = async () => {
        await runInstanceAction(currentInstanceName, "restart");
        banner.classList.add("hidden");
      };
    } else {
      banner.classList.add("hidden");
    }
  } catch (e) {
    if (e.status === 409) {
      alert("他のセッションで既に保存されています。画面を再読み込みします。");
      await renderInstanceDetail(currentInstanceName);
    } else {
      alert("保存に失敗しました: " + e.message);
    }
  }
}

/* --- ユーザー管理 --- */

async function renderUsers(container) {
  container.appendChild(clone("tpl-users"));
  const data = await api(`/api/instances/${encodeURIComponent(currentInstanceName)}/users`);
  $("#users-inherited-note").textContent = data.inherited_from_project_env
    ? "プロジェクト直下 .env の AUTH_USERS を継承しています（このインスタンス専用のユーザーを追加すると、以後は専用のものだけが使われます）。"
    : "このインスタンス専用のユーザー設定です。";
  const tbody = $("#users-tbody");
  tbody.innerHTML = "";
  for (const name of data.usernames) {
    const tr = document.createElement("tr");
    const pwInput = document.createElement("input");
    pwInput.type = "password";
    pwInput.placeholder = "新しいパスワード";
    const pwBtn = document.createElement("button");
    pwBtn.textContent = "変更";
    pwBtn.addEventListener("click", async () => {
      if (!pwInput.value) return;
      await api(`/api/instances/${encodeURIComponent(currentInstanceName)}/users/${encodeURIComponent(name)}`, {
        method: "PUT",
        body: { password: pwInput.value },
      });
      pwInput.value = "";
      alert("パスワードを変更しました。");
    });
    const delBtn = document.createElement("button");
    delBtn.textContent = "削除";
    delBtn.addEventListener("click", async () => {
      if (!confirm(`ユーザー "${name}" を削除しますか？`)) return;
      await api(`/api/instances/${encodeURIComponent(currentInstanceName)}/users/${encodeURIComponent(name)}`, {
        method: "DELETE",
      });
      const fresh = $("#instance-subview");
      fresh.innerHTML = "";
      renderUsers(fresh);
    });
    const tdName = document.createElement("td");
    tdName.textContent = name;
    const tdPw = document.createElement("td");
    tdPw.appendChild(pwInput);
    tdPw.appendChild(pwBtn);
    const tdActions = document.createElement("td");
    tdActions.appendChild(delBtn);
    tr.appendChild(tdName);
    tr.appendChild(tdPw);
    tr.appendChild(tdActions);
    tbody.appendChild(tr);
  }

  $("#add-user-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const form = new FormData(ev.target);
    try {
      await api(`/api/instances/${encodeURIComponent(currentInstanceName)}/users`, {
        method: "POST",
        body: { username: form.get("username"), password: form.get("password") },
      });
      const fresh = $("#instance-subview");
      fresh.innerHTML = "";
      renderUsers(fresh);
    } catch (e) {
      $("#users-error").textContent = e.message;
    }
  });

  $("#secret-status").textContent = data.auth_secret_set ? "設定済み" : "未設定";
  $("#gen-secret-btn").addEventListener("click", async () => {
    if (!confirm("JWT署名鍵を再生成すると、既存のログインセッションは全て無効になります。よろしいですか？")) return;
    await api(`/api/instances/${encodeURIComponent(currentInstanceName)}/auth-secret`, { method: "POST" });
    $("#secret-status").textContent = "設定済み（再生成しました）";
  });
}

/* --- 環境変数 --- */

async function renderEnvVars(container) {
  container.appendChild(clone("tpl-env"));
  const data = await api(`/api/instances/${encodeURIComponent(currentInstanceName)}/env`);
  const tbody = $("#env-tbody");
  tbody.innerHTML = "";
  for (const [key, value] of Object.entries(data.vars)) {
    const tr = document.createElement("tr");
    const delBtn = document.createElement("button");
    delBtn.textContent = "削除";
    delBtn.addEventListener("click", async () => {
      await api(`/api/instances/${encodeURIComponent(currentInstanceName)}/env/${encodeURIComponent(key)}`, {
        method: "DELETE",
      });
      const fresh = $("#instance-subview");
      fresh.innerHTML = "";
      renderEnvVars(fresh);
    });
    const tdKey = document.createElement("td");
    tdKey.textContent = key;
    const tdVal = document.createElement("td");
    tdVal.textContent = value;
    const tdActions = document.createElement("td");
    tdActions.appendChild(delBtn);
    tr.appendChild(tdKey);
    tr.appendChild(tdVal);
    tr.appendChild(tdActions);
    tbody.appendChild(tr);
  }
  $("#add-env-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const form = new FormData(ev.target);
    try {
      await api(`/api/instances/${encodeURIComponent(currentInstanceName)}/env`, {
        method: "PUT",
        body: { key: form.get("key"), value: form.get("value") || "" },
      });
      const fresh = $("#instance-subview");
      fresh.innerHTML = "";
      renderEnvVars(fresh);
    } catch (e) {
      $("#env-error").textContent = e.message;
    }
  });
}

/* --- バックアップ --- */

async function renderBackups(container) {
  container.appendChild(clone("tpl-backups"));
  const data = await api(`/api/instances/${encodeURIComponent(currentInstanceName)}/backups`);
  const tbody = $("#backups-tbody");
  tbody.innerHTML = "";
  if (data.backups.length === 0) {
    tbody.innerHTML = '<tr><td colspan="3" class="hint">まだバックアップはありません。</td></tr>';
    return;
  }
  for (const b of data.backups) {
    const tr = document.createElement("tr");
    const tdName = document.createElement("td");
    tdName.textContent = b.name;
    const tdTime = document.createElement("td");
    tdTime.textContent = new Date(b.mtime * 1000).toLocaleString("ja-JP");
    const restoreBtn = document.createElement("button");
    restoreBtn.textContent = "復元";
    restoreBtn.addEventListener("click", async () => {
      if (!confirm(`${b.name} の内容で復元します。現在の設定はさらにバックアップされます。よろしいですか？`)) return;
      try {
        const result = await api(
          `/api/instances/${encodeURIComponent(currentInstanceName)}/backups/${encodeURIComponent(b.name)}/restore`,
          { method: "POST" }
        );
        alert(result.needs_restart ? "復元しました。反映にはインスタンスの再起動が必要です。" : "復元しました。");
      } catch (e) {
        alert("復元に失敗しました: " + e.message);
      }
    });
    const tdActions = document.createElement("td");
    tdActions.appendChild(restoreBtn);
    tr.appendChild(tdName);
    tr.appendChild(tdTime);
    tr.appendChild(tdActions);
    tbody.appendChild(tr);
  }
}

/* ---------------------------------------------------------- 表示設定 */

let settingsTarget = "";

function settingsQuery() {
  return settingsTarget ? `?instance=${encodeURIComponent(settingsTarget)}` : "";
}

function renderSettings() {
  mainEl.innerHTML = "";
  return renderSettingsPanel(mainEl, "");
}

// target: "" なら共通設定（public/settings/）、インスタンス名ならそのインスタンス専用設定。
async function renderSettingsPanel(container, target) {
  settingsTarget = target;
  container.appendChild(clone("tpl-settings"));
  // インスタンス詳細ではサブタブ名が見出しを兼ねるため、タイトルは出さない。
  $("#settings-title").classList.toggle("hidden", Boolean(target));

  $$(".settings-section[data-text]").forEach((section) => {
    const file = section.dataset.text;
    section.querySelector(".save-setting-btn").addEventListener("click", () =>
      settingsAction(() =>
        api(`/api/settings/${file}${settingsQuery()}`, {
          method: "PUT",
          body: { content: section.querySelector("textarea").value },
        }),
        "保存しました（ブラウザの再読み込みで反映されます）。"
      )
    );
    section.querySelector(".reset-setting-btn").addEventListener("click", () => resetSetting(file));
  });
  $$(".settings-section[data-image]").forEach((section) => {
    const kind = section.dataset.image;
    section.querySelector(".upload-image-btn").addEventListener("click", () => uploadImage(section, kind));
    section.querySelector(".reset-setting-btn").addEventListener("click", () => resetSetting(`images/${kind}`));
  });
  await loadSettings();
}

async function loadSettings() {
  $("#settings-error").textContent = "";
  $("#settings-target-hint").textContent = settingsTarget
    ? "このインスタンスだけに適用されます。専用の設定が無い項目は、上部の「表示設定」タブの共通設定が使われます。"
    : "各インスタンスの既定値です。インスタンスごとに変えたい場合は、インスタンスの「設定」→「表示設定」タブで設定してください。";
  const data = await api(`/api/settings${settingsQuery()}`);
  const sourceLabel = (overridden) => (!settingsTarget ? "" : overridden ? "［このインスタンス専用］" : "［共通設定を使用中］");
  $$(".settings-section[data-text]").forEach((section) => {
    const info = data.texts[section.dataset.text];
    section.querySelector("textarea").value = info.content;
    section.querySelector(".setting-source").textContent = sourceLabel(info.overridden);
    section.querySelector(".reset-setting-btn").classList.toggle("hidden", !info.overridden);
  });
  $$(".settings-section[data-image]").forEach((section) => {
    const info = data.images[section.dataset.image];
    section.querySelector(".image-current").textContent = info.filename || "（なし）";
    section.querySelector(".setting-source").textContent = sourceLabel(info.overridden);
    section.querySelector(".reset-setting-btn").classList.toggle("hidden", !info.overridden);
  });
}

async function settingsAction(fn, message) {
  try {
    await fn();
    await loadSettings();
    alert(message);
  } catch (e) {
    $("#settings-error").textContent = e.message;
  }
}

function resetSetting(target) {
  if (!confirm("このインスタンス専用の設定を削除し、共通設定に戻します。よろしいですか？")) return;
  settingsAction(
    () => api(`/api/settings/${target}${settingsQuery()}`, { method: "DELETE" }),
    "共通設定に戻しました（ブラウザの再読み込みで反映されます）。"
  );
}

async function uploadImage(section, kind) {
  const file = section.querySelector("input[type=file]").files[0];
  if (!file) {
    $("#settings-error").textContent = "ファイルを選択してください。";
    return;
  }
  const base64 = await fileToBase64(file);
  settingsAction(
    () =>
      api(`/api/settings/images/${kind}${settingsQuery()}`, {
        method: "PUT",
        body: { filename: file.name, content_base64: base64 },
      }),
    "アップロードしました（ブラウザの再読み込みで反映されます）。"
  );
}

function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = reader.result;
      resolve(result.substring(result.indexOf(",") + 1));
    };
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

/* ---------------------------------------------------------- 変更履歴 */

async function renderAudit() {
  mainEl.innerHTML = "";
  mainEl.appendChild(clone("tpl-audit"));
  const data = await api("/api/audit?limit=200");
  const tbody = $("#audit-tbody");
  tbody.innerHTML = "";
  for (const entry of data.entries) {
    const tr = document.createElement("tr");
    const cells = [
      entry.ts || "",
      entry.instance || "-",
      entry.action || "",
      entry.actor || "",
      summarizeAuditEntry(entry),
    ];
    for (const text of cells) {
      const td = document.createElement("td");
      td.textContent = text;
      tr.appendChild(td);
    }
    tbody.appendChild(tr);
  }
  if (data.entries.length === 0) {
    tbody.innerHTML = '<tr><td colspan="5" class="hint">まだ履歴はありません。</td></tr>';
  }
}

function summarizeAuditEntry(entry) {
  if (entry.changes && entry.changes.length) {
    return entry.changes.map((c) => `[${c.section}].${c.key}`).join(", ");
  }
  if (entry.target_user) return `対象: ${entry.target_user}`;
  if (entry.file) return `ファイル: ${entry.file}`;
  if (entry.key) return `キー: ${entry.key}`;
  return "";
}

/* ---------------------------------------------------------- 起動 */

boot();
