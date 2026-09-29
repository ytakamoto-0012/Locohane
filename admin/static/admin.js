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
  } else if (k.ui_kind === "multiline") {
    const textarea = document.createElement("textarea");
    textarea.value = value;
    textarea.rows = Math.min(16, Math.max(3, value.split("\n").length));
    textarea.addEventListener("input", () => setEdit(k, textarea.value));
    inputWrap.appendChild(textarea);
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
