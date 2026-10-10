"use strict";
/* スキル研究室（admin/skill_lab/api.py）。admin.js の後に読み込み、その部品
 * （$ / $$ / api / el / dataTable / fmtTime / fileToBase64 / startMonitorPolling 等）を使う。
 *
 * 研究テーマはインスタンス（組織別・役割別）ごとに分けて扱う。トップの「スキル研究室」タブは
 * 左にインスタンスの一覧、右に選んだインスタンスのパネルを出し、パネルには
 * そのインスタンスのドラフト・テーマだけを出す（他のインスタンスのものは出さない）。
 */

const LAB_STATUS_LABELS = {
  draft: "準備中",
  queued: "開始待ち",
  running: "実行中",
  review: "レビュー待ち",
  exhausted: "上限到達",
  error: "エラー",
  stopped: "停止",
  promoted: "昇格済み",
  returned: "差し戻し済み",
  cancelled: "取りやめ",
};
const LAB_ACTIVE = ["queued", "running"];
const LAB_CLOSED = ["promoted", "returned", "cancelled"];
const TASK_LABELS = { trial: "1回試行", draft_spec: "仕様の下書き", draft_cases: "ケースの下書き", draft_asset: "資産の下書き" };
const TASK_STATUS_LABELS = { queued: "待ち", running: "実行中", done: "完了", error: "失敗" };

let labSelectedInstance = null;

function labPath(name, suffix) {
  return `/api/instances/${encodeURIComponent(name)}/skill-lab${suffix || ""}`;
}

function themePath(name, id, suffix) {
  return labPath(name, `/themes/${encodeURIComponent(id)}${suffix || ""}`);
}

function statusBadge(status) {
  return el("span", { class: `lab-status lab-status-${status}`, text: LAB_STATUS_LABELS[status] || status });
}

function labModal(title, bodyNodes, opts) {
  opts = opts || {};
  const close = el("button", { type: "button", class: "btn-cancel", text: opts.closeText || "閉じる" });
  const errorBox = el("div", { class: "error" });
  const actions = el("div", { class: "modal-actions" }, [close, ...(opts.actions || [])]);
  const modal = el("div", { class: "modal-backdrop" }, el("div", { class: `modal ${opts.wide ? "modal-wide" : ""}` }, [
    el("h3", { text: title }),
    ...bodyNodes,
    errorBox,
    actions,
  ]));
  close.addEventListener("click", () => modal.remove());
  document.body.appendChild(modal);
  return { modal, errorBox, close: () => modal.remove() };
}

async function labAction(fn, errorTarget) {
  try {
    return await fn();
  } catch (e) {
    if (errorTarget) errorTarget.textContent = e.message;
    else alert(e.message);
    return undefined;
  }
}

/* ------------------------------------------------------------- トップタブ */

async function renderSkillLab() {
  mainEl.innerHTML = "";
  const sidebar = el("div", { class: "lab-sidebar" });
  const panel = el("div", { class: "lab-panel" });
  mainEl.append(
    el("div", { class: "view-header" }, [el("h2", { text: "スキル研究室" })]),
    el("p", {
      class: "hint",
      text:
        "スキル・サブエージェントを研究テーマとして作り、AI に不具合を自己修正させて本番運用に耐えるものに仕上げます。" +
        "テーマはインスタンスごとに分かれ、昇格先もそのインスタンス専用の置き場です（他のインスタンスには入りません）。",
    }),
    el("div", { class: "lab-layout" }, [sidebar, panel]),
  );
  const data = await api("/api/skill-lab/overview");
  const labsBox = el("div", { class: "lab-labs" }, [el("h4", { text: "スキル調整ワーカー（実行役）" })]);
  if (!data.labs.length) {
    labsBox.append(
      el("p", {
        class: "hint",
        text: "まだありません。AI による評価・修正・下書きを動かすには、インスタンス一覧の「＋ インスタンス追加」で種類「スキル調整ワーカー」を作り、[llm] main_url を設定して起動してください。",
      }),
    );
  }
  for (const lab of data.labs) {
    labsBox.append(el("div", { class: "lab-lab-row" }, [el("span", { class: `state-dot ${lab.state}` }), ` ${lab.display_name}（${STATE_LABELS[lab.state] || lab.state}）`]));
  }
  const list = el("div", { class: "lab-instance-list" });
  for (const row of data.instances) {
    const c = row.counts || {};
    const item = el("button", { class: "lab-instance-item", type: "button" }, [
      el("div", { class: "lab-instance-name", text: row.display_name }),
      el("div", { class: "hint", text: row.error ? `設定エラー: ${row.error}` : `ドラフト ${c.drafts || 0} / 進行中 ${c.open || 0} / 実行中 ${c.active || 0} / レビュー待ち ${c.review || 0}` }),
    ]);
    item.dataset.instance = row.name;
    item.addEventListener("click", () => {
      labSelectedInstance = row.name;
      $$(".lab-instance-item", list).forEach((b) => b.classList.toggle("active", b === item));
      renderSkillLabPanel(panel, row.name);
    });
    list.append(item);
  }
  sidebar.append(el("h4", { text: "対象インスタンス" }), list, labsBox);
  const initial = data.instances.find((r) => r.name === labSelectedInstance) || data.instances[0];
  if (initial) $(`.lab-instance-item[data-instance="${CSS.escape(initial.name)}"]`, list).click();
}

/* ---------------------------------------------------- インスタンスのパネル */

async function renderSkillLabPanel(container, name) {
  stopMonitorPolling();
  container.innerHTML = "";
  const [themesData, src] = await Promise.all([api(labPath(name, "/themes")), api(labPath(name, "/sources"))]);
  const meta = (typeof instanceCache !== "undefined" ? instanceCache : []).find((i) => i.name === name);
  container.append(labPanelHeader(name, meta ? meta.display_name : name, themesData.labs));

  const createBtn = el("button", { class: "primary", text: "＋ テーマを作成", onclick: () => openCreateThemeModal(container, name, themesData.labs) });
  container.append(el("div", { class: "lab-section-head" }, [el("h3", { text: "研究テーマ" }), createBtn]));
  const rows = themesData.themes.map((t) => [
    linkBtn(t.title, () => renderThemeDetail(container, name, t.id)),
    statusBadge(t.status),
    `スキル ${t.assets.skills.length} / エージェント ${t.assets.agents.length}`,
    `${t.cases} 件`,
    t.last ? `${t.last.phase === "tryout" ? "トライアウト" : "評価"} ${t.last.verdict === "pass" ? "合格" : "不合格"}（反復 ${t.iterations}）` : "-",
    fmtTime(t.updated_at),
  ]);
  container.append(dataTable(["テーマ", "状態", "資産", "ケース", "最新の結果", "更新"], rows, "まだテーマはありません。"));

  container.append(el("h3", { text: "利用者のドラフト（このインスタンス）" }));
  const draftRows = src.drafts.map((d) => [
    `${d.owner}/${d.name}`,
    d.status === "returned" ? `差し戻し中（${d.returned_reason || ""}）` : d.status,
    fmtTime(d.updated_at),
    d.imported_theme
      ? el("span", { class: "hint", text: "テーマに取り込み済み" })
      : el("button", {
          text: "テーマを作って取り込む",
          onclick: async () => {
            const theme = await labAction(() => api(labPath(name, "/themes"), { method: "POST", body: { title: `${d.owner}/${d.name}`, lab: defaultLab(themesData.labs) } }));
            if (!theme) return;
            const ok = await labAction(() =>
              api(themePath(name, theme.id, "/assets"), { method: "POST", body: { mode: "draft", asset_type: "skills", name: d.name, origin: d.path } }),
            );
            if (ok) renderThemeDetail(container, name, theme.id);
          },
        }),
  ]);
  container.append(dataTable(["ドラフト", "状態", "更新", ""], draftRows, "このインスタンスにドラフトはありません。"));
}

function defaultLab(labs) {
  return labs.length === 1 ? labs[0].name : null;
}

function labPanelHeader(name, displayName, labs) {
  const labText = labs.length
    ? labs.map((l) => `${l.display_name}（${STATE_LABELS[l.state] || l.state}）`).join("、")
    : "スキル調整ワーカーがありません（AI による修正・試行は動きません）";
  return el("div", { class: "lab-panel-header" }, [
    el("div", {}, [el("span", { class: "hint", text: "対象インスタンス" }), el("h3", { text: `${displayName}（${name}）` })]),
    el("div", { class: "hint", text: `スキル調整ワーカー: ${labText}` }),
  ]);
}

function labSelect(labs, current) {
  const select = el("select", { name: "lab" }, [el("option", { value: "", text: labs.length === 1 ? `（自動: ${labs[0].display_name}）` : "（未選択）" })]);
  for (const l of labs) {
    const opt = el("option", { value: l.name, text: l.display_name });
    if (l.name === current) opt.selected = true;
    select.append(opt);
  }
  return select;
}

function openCreateThemeModal(container, name, labs) {
  const title = el("input", { type: "text", placeholder: "例: 見積書チェック" });
  const lab = labSelect(labs, defaultLab(labs));
  const ok = el("button", { class: "primary", type: "button", text: "作成" });
  const m = labModal("研究テーマを作成", [el("label", {}, ["テーマ名", title]), el("label", {}, ["担当の スキル調整ワーカー", lab])], { actions: [ok] });
  ok.addEventListener("click", async () => {
    const theme = await labAction(() => api(labPath(name, "/themes"), { method: "POST", body: { title: title.value, lab: lab.value || null } }), m.errorBox);
    if (!theme) return;
    m.close();
    renderThemeDetail(container, name, theme.id);
  });
}

/* ------------------------------------------------------------ テーマ詳細 */

const THEME_SUBVIEWS = [
  ["overview", "概要・仕様"],
  ["assets", "資産"],
  ["cases", "ケース"],
  ["runs", "実行の記録"],
  ["review", "レビュー・昇格"],
  ["history", "履歴"],
];

async function renderThemeDetail(container, name, id, sub) {
  stopMonitorPolling();
  container.innerHTML = "";
  const state = { name, id, sub: sub || "overview", data: null, sources: null };
  const header = el("div", { class: "theme-header" });
  const nav = el("nav", { class: "subtabs" });
  const body = el("div", { class: "theme-body" });
  const back = el("button", { class: "back-btn", text: "← テーマ一覧", onclick: () => renderSkillLabPanel(container, name) });
  container.append(back, header, nav, body);
  for (const [key, label] of THEME_SUBVIEWS) {
    const b = el("button", { class: "subtab-btn", text: label });
    b.dataset.subview = key;
    b.addEventListener("click", () => {
      state.sub = key;
      $$(".subtab-btn", nav).forEach((x) => x.classList.toggle("active", x === b));
      renderThemeSub(state, body);
    });
    nav.append(b);
  }
  state.reload = async () => {
    state.data = await api(themePath(name, id));
    renderThemeHeader(state, header, container);
  };
  state.sources = await api(labPath(name, "/sources"));
  await state.reload();
  $(`.subtab-btn[data-subview="${state.sub}"]`, nav).click();
  // 実行中・タスクがある間は状態を追う（編集中の欄を消さないよう、見出しと「実行の記録」だけを更新する）。
  startMonitorPolling(body, async () => {
    const before = state.data.updated_at;
    const busy = LAB_ACTIVE.includes(state.data.status) || (state.data.tasks || []).some((t) => t.status === "queued" || t.status === "running");
    if (!busy) return;
    await state.reload();
    if (state.data.updated_at !== before && (state.sub === "runs" || state.sub === "overview")) renderThemeSub(state, body);
  });
}

function renderThemeHeader(state, header, container) {
  const d = state.data;
  const editable = !LAB_ACTIVE.includes(d.status) && !LAB_CLOSED.includes(d.status);
  const buttons = [];
  if (editable) {
    buttons.push(
      el("button", { class: "primary", text: "スキル調整ループを開始", onclick: () => startTheme(state, "loop") }),
      el("button", { text: "トライアウトだけ実行", onclick: () => startTheme(state, "tryout") }),
      el("button", {
        text: "取りやめ",
        onclick: async () => {
          if (!confirm("このテーマを取りやめます（ファイルは残ります）。よろしいですか？")) return;
          if (await labAction(() => api(themePath(state.name, state.id, "/cancel"), { method: "POST" }))) renderSkillLabPanel(container, state.name);
        },
      }),
    );
  }
  if (LAB_ACTIVE.includes(d.status)) {
    buttons.push(el("button", { text: "停止", onclick: async () => { await labAction(() => api(themePath(state.name, state.id, "/stop"), { method: "POST" })); await state.reload(); } }));
  }
  const loop = d.loop || {};
  const labName = d.lab || (d.labs.length === 1 ? d.labs[0].name : null);
  const lab = d.labs.find((l) => l.name === labName);
  const notes = [];
  if (LAB_ACTIVE.includes(d.status) && lab && !["running", "external"].includes(lab.state)) {
    notes.push(el("p", { class: "warn-box", text: `担当の スキル調整ワーカー ${lab.display_name} が起動していません。インスタンス一覧から起動してください。` }));
  }
  if (loop.message) notes.push(el("p", { class: "hint", text: loop.message }));
  header.replaceChildren(
    el("div", { class: "theme-title-row" }, [el("h3", { text: d.title }), statusBadge(d.status)]),
    el("div", { class: "hint", text: `対象インスタンス: ${d.instance} ／ 担当の スキル調整ワーカー: ${lab ? lab.display_name : "未選択"} ／ 反復 ${(d.iterations || []).length} 回 ／ 作成 ${d.created_by || ""} ${fmtTime(d.created_at)}` }),
    ...notes,
    el("div", { class: "theme-actions" }, buttons),
  );
}

async function startTheme(state, mode) {
  const label = mode === "loop" ? "スキル調整ループ（評価 → 失敗なら AI が直す → 再評価 → トライアウト）" : "トライアウト";
  if (!confirm(`${label}を開始します。よろしいですか？`)) return;
  const res = await labAction(() => api(themePath(state.name, state.id, "/start"), { method: "POST", body: { mode } }));
  if (res && !res.lab_running) alert("開始待ちにしました。担当の スキル調整ワーカーが起動していないため、起動すると始まります。");
  await state.reload();
}

function renderThemeSub(state, body) {
  body.innerHTML = "";
  const fn = { overview: renderThemeOverview, assets: renderThemeAssets, cases: renderThemeCases, runs: renderThemeRuns, review: renderThemeReview, history: renderThemeHistory }[state.sub];
  return fn(state, body);
}

function themeEditable(state) {
  return !LAB_ACTIVE.includes(state.data.status) && !LAB_CLOSED.includes(state.data.status);
}

async function addTask(state, type, params, errorBox) {
  const res = await labAction(() => api(themePath(state.name, state.id, "/tasks"), { method: "POST", body: { type, params } }), errorBox);
  if (res && !res.lab_running) alert("依頼しました。担当の スキル調整ワーカーが起動していないため、起動すると処理されます。");
  await state.reload();
  return res;
}

/* --- 概要・仕様 --- */

async function renderThemeOverview(state, body) {
  const editable = themeEditable(state);
  const spec = await api(themePath(state.name, state.id, `/files?path=${encodeURIComponent("spec.md")}`));
  const specArea = el("textarea", { rows: 16, class: "code-area" });
  specArea.value = spec.content;
  const specErr = el("div", { class: "error" });
  const request = el("input", { type: "text", placeholder: "AI への補足（任意）" });
  body.append(
    el("h4", { text: "仕様（spec.md）" }),
    el("p", { class: "hint", text: "目的・使う場面（利用者が打つ指示の例）・期待する出力・やってはいけないことを書きます。AI の修正・判定・ケースの下書きの基準になります（AI は仕様を変更しません）。" }),
    specArea,
    el("div", { class: "inline-form" }, [
      el("button", { class: "primary", text: "保存", disabled: editable ? null : "disabled", onclick: () => saveThemeFile(state, "spec.md", specArea.value, specErr) }),
      request,
      el("button", { text: "AI に下書きを頼む", disabled: editable ? null : "disabled", onclick: () => addTask(state, "draft_spec", { request: request.value }, specErr) }),
    ]),
    specErr,
  );

  const patch = await api(themePath(state.name, state.id, `/files?path=${encodeURIComponent("config_patch.json")}`));
  const patchArea = el("textarea", { rows: 8, class: "code-area" });
  patchArea.value = patch.content;
  const patchErr = el("div", { class: "error" });
  body.append(
    el("h4", { text: "設定パッチ（config_patch.json）" }),
    el("p", {
      class: "hint",
      text:
        "評価と昇格で対象インスタンスの設定へ足す項目です（足せるのは [subagent] agent_type_run_script_allowlist・[plan] plan_approval_exempt_scripts・[main_agent_tool_guard] allow_entries）。" +
        "スキルのスクリプトの計画承認の免除は自動で足すため、ここに書く必要はありません。",
    }),
    patchArea,
    el("button", { class: "primary", text: "保存", disabled: editable ? null : "disabled", onclick: () => saveThemeFile(state, "config_patch.json", patchArea.value, patchErr) }),
    patchErr,
  );

  const tasks = (state.data.tasks || []).slice().reverse();
  body.append(el("h4", { text: "AI への依頼" }));
  body.append(
    dataTable(
      ["依頼", "状態", "日時", "結果"],
      tasks.map((t) => [
        TASK_LABELS[t.type] || t.type,
        TASK_STATUS_LABELS[t.status] || t.status,
        fmtTime(t.created_at),
        t.error
          ? el("span", { class: "error", text: t.error })
          : t.type === "trial" && t.status === "done"
            ? linkBtn("結果を見る", () => showTrialResult(t.result))
            : (t.result && t.result.message) || "",
      ]),
      "まだありません。",
    ),
  );
}

async function saveThemeFile(state, path, content, errorBox) {
  errorBox.textContent = "";
  const ok = await labAction(() => api(themePath(state.name, state.id, "/files"), { method: "PUT", body: { path, content } }), errorBox);
  if (ok) {
    errorBox.textContent = "";
    await state.reload();
    flashSaved(errorBox);
  }
  return ok;
}

function flashSaved(target) {
  target.classList.add("ok-text");
  target.textContent = "保存しました（トライアウトの結果は無効になりました）。";
  setTimeout(() => {
    target.classList.remove("ok-text");
    if (target.textContent.startsWith("保存しました")) target.textContent = "";
  }, 4000);
}

/* --- 資産 --- */

function renderThemeAssets(state, body) {
  const d = state.data;
  const editable = themeEditable(state);
  const files = d.files || [];
  for (const [type, label] of [["skills", "スキル"], ["agents", "サブエージェント"]]) {
    body.append(el("h4", { text: label }));
    const names = d.summary.assets[type];
    if (!names.length) body.append(el("p", { class: "hint", text: "まだありません。" }));
    for (const n of names) {
      const src = (d.sources[type] || {})[n] || {};
      const srcText =
        src.kind === "draft" ? `利用者のドラフト ${src.owner}/${n} から取り込み`
        : src.kind === "official" ? `正式の${src.origin_label === "builtin" ? "同梱" : src.origin_label === "instance" ? "このインスタンス専用" : "共有"}資産を複製して改善`
        : "新規";
      const prefix = type === "skills" ? `assets/skills/${n}/` : `assets/agents/${n}.md`;
      const fileLinks = files
        .filter((f) => (type === "skills" ? f.path.startsWith(prefix) : f.path === prefix))
        .map((f) => el("li", {}, [linkBtn(f.path.replace(/^assets\/(skills|agents)\//, ""), () => openFileEditor(state, f.path, f.text)), ` (${f.size} B)`]));
      const actions = [];
      if (type === "agents") actions.push(el("button", { text: "フォームで編集", disabled: editable ? null : "disabled", onclick: () => openAgentEditor(state, n) }));
      if (type === "skills") actions.push(el("button", { text: "ファイルを追加", disabled: editable ? null : "disabled", onclick: () => openNewFileModal(state, n) }));
      actions.push(
        el("button", {
          class: "btn-danger",
          text: "外す",
          disabled: editable ? null : "disabled",
          onclick: async () => {
            if (!confirm(`${n} をテーマから外します（テーマ内のファイルは削除されます。元のドラフト・正式資産は残ります）。`)) return;
            if (await labAction(() => api(themePath(state.name, state.id, `/assets/${type}/${encodeURIComponent(n)}`), { method: "DELETE" }))) {
              await state.reload();
              renderThemeSub(state, body);
            }
          },
        }),
      );
      body.append(el("div", { class: "asset-card" }, [el("div", { class: "asset-title" }, [el("strong", { text: n }), el("span", { class: "hint", text: ` ${srcText}` })]), el("ul", { class: "file-list" }, fileLinks), el("div", { class: "inline-form" }, actions)]));
    }
  }
  if (editable) body.append(assetAddForms(state, body));
}

function assetAddForms(state, body) {
  const src = state.sources;
  const err = el("div", { class: "error" });
  const reload = async () => {
    await state.reload();
    renderThemeSub(state, body);
  };
  const post = (payload) => labAction(() => api(themePath(state.name, state.id, "/assets"), { method: "POST", body: payload }), err);

  const draftSel = el("select", {}, [el("option", { value: "", text: "（ドラフトを選ぶ）" }), ...src.drafts.map((d) => el("option", { value: d.path, text: `${d.owner}/${d.name}${d.imported_theme ? "（他のテーマに取り込み済み）" : ""}` }))]);
  const officialSel = el("select", {}, [
    el("option", { value: "", text: "（正式資産を選ぶ）" }),
    ...src.skills.map((s) => el("option", { value: `skills:${s.name}`, text: `スキル ${s.name}（${originText(s.origin)}）` })),
    ...src.agents.map((a) => el("option", { value: `agents:${a.name}`, text: `サブエージェント ${a.name}（${originText(a.origin)}）` })),
  ]);
  const newType = el("select", {}, [el("option", { value: "skills", text: "スキル" }), el("option", { value: "agents", text: "サブエージェント" })]);
  const newName = el("input", { type: "text", placeholder: "名前（小文字英数字・-・_）" });
  const newRequest = el("textarea", { rows: 3, placeholder: "何をさせたいか（AI に下書きを頼む場合）" });
  return el("div", { class: "asset-add" }, [
    el("h4", { text: "資産を追加" }),
    el("div", { class: "inline-form" }, [
      draftSel,
      el("button", {
        text: "ドラフトを取り込む",
        onclick: async () => {
          const d = src.drafts.find((x) => x.path === draftSel.value);
          if (d && (await post({ mode: "draft", asset_type: "skills", name: d.name, origin: d.path }))) reload();
        },
      }),
    ]),
    el("div", { class: "inline-form" }, [
      officialSel,
      el("button", {
        text: "複製して改善する",
        onclick: async () => {
          if (!officialSel.value) return;
          const [type, n] = officialSel.value.split(":");
          if (await post({ mode: "official", asset_type: type, name: n })) reload();
        },
      }),
    ]),
    el("p", { class: "hint", text: "正式資産を改善した場合も、昇格先はこのインスタンス専用の置き場です（同梱・共有の元ファイルと他のインスタンスは変わりません）。" }),
    el("div", { class: "inline-form" }, [newType, newName]),
    newRequest,
    el("div", { class: "inline-form" }, [
      el("button", {
        class: "primary",
        text: "AI に下書きを頼む",
        onclick: async () => {
          const res = await post({ mode: "ai", asset_type: newType.value, name: newName.value.trim(), request: newRequest.value });
          if (res) {
            alert("依頼しました。スキル調整ワーカーが下書きを作ると、資産に追加されます（「概要・仕様」の「AI への依頼」で状態を確認できます）。");
            reload();
          }
        },
      }),
      el("button", {
        text: "空のひな形で作る",
        onclick: async () => {
          const n = newName.value.trim();
          const content = newType.value === "skills" ? skillTemplate(n, newRequest.value) : agentTemplate(n, newRequest.value);
          if (await post({ mode: "new", asset_type: newType.value, name: n, content })) reload();
        },
      }),
    ]),
    err,
  ]);
}

function originText(origin) {
  return { builtin: "同梱", instance: "このインスタンス専用", shared: "共有" }[origin] || origin || "";
}

function skillTemplate(name, desc) {
  return `---\nname: ${name}\ndescription: ${(desc || "何をするか。どんな指示のときに使うか。").replace(/\n/g, " ")}\n---\n\n# ${name}\n\n## 手順\n\n1. \n`;
}

function agentTemplate(name, desc) {
  return (
    `---\nname: ${name}\ndescription: ${(desc || "何を任せるサブエージェントか").replace(/\n/g, " ")}\ntools: read_skill, read_skill_file, run_script, Read, Grep\n---\n\n` +
    `あなたは、メインのアシスタントから仕事を委譲されたサブエージェントです。\n\n## 手順\n\n1. \n\n## 最終回答\n\n\n---\n\n## スキル\n\n{{skills}}\n\n### run_scriptで実行できるスキル/スクリプト（このエージェント種別限定）\n\n{{run_script_allowlist}}\n`
  );
}

async function openFileEditor(state, path, isText) {
  const editable = themeEditable(state);
  if (!isText) {
    const m = labModal(path, [el("p", { class: "hint", text: "バイナリファイルのため、ここでは編集できません。" })], {
      actions: editable ? [el("button", { class: "btn-danger", text: "削除", onclick: () => deleteThemeFile(state, path, m) })] : [],
    });
    return;
  }
  const data = await api(themePath(state.name, state.id, `/files?path=${encodeURIComponent(path)}`));
  const area = el("textarea", { rows: 26, class: "code-area" });
  area.value = data.content;
  const save = el("button", { class: "primary", text: "保存", disabled: editable ? null : "disabled" });
  const del = el("button", { class: "btn-danger", text: "削除", disabled: editable ? null : "disabled" });
  const m = labModal(path, [area], { wide: true, actions: [del, save] });
  save.addEventListener("click", async () => {
    if (await saveThemeFile(state, path, area.value, m.errorBox)) m.close();
  });
  del.addEventListener("click", () => deleteThemeFile(state, path, m));
}

async function deleteThemeFile(state, path, m) {
  if (!confirm(`${path} を削除します。よろしいですか？`)) return;
  if (await labAction(() => api(themePath(state.name, state.id, `/files?path=${encodeURIComponent(path)}`), { method: "DELETE" }), m.errorBox)) {
    m.close();
    await state.reload();
    renderThemeSub(state, $(".theme-body"));
  }
}

function openNewFileModal(state, skill) {
  const rel = el("input", { type: "text", placeholder: "scripts/run.py や references/notes.md" });
  const area = el("textarea", { rows: 18, class: "code-area" });
  const save = el("button", { class: "primary", text: "作成" });
  const m = labModal(`${skill} にファイルを追加`, [el("label", {}, ["スキル内のパス", rel]), area], { wide: true, actions: [save] });
  save.addEventListener("click", async () => {
    const path = `assets/skills/${skill}/${rel.value.trim().replace(/^\/+/, "")}`;
    if (await saveThemeFile(state, path, area.value, m.errorBox)) {
      m.close();
      renderThemeSub(state, $(".theme-body"));
    }
  });
}

/* サブエージェントのフォーム編集（frontmatter の name・description・tools・model と本文、run_script の許可） */

function splitFrontmatter(text) {
  const m = /^---\r?\n([\s\S]*?)\r?\n---\r?\n?([\s\S]*)$/.exec(text);
  if (!m) return { fm: {}, body: text };
  const fm = {};
  for (const line of m[1].split(/\r?\n/)) {
    const i = line.indexOf(":");
    if (i > 0 && !/^\s/.test(line)) fm[line.slice(0, i).trim()] = line.slice(i + 1).trim().replace(/^["']|["']$/g, "");
  }
  return { fm, body: m[2] };
}

async function openAgentEditor(state, agent) {
  const path = `assets/agents/${agent}.md`;
  const [file, patchFile] = await Promise.all([
    api(themePath(state.name, state.id, `/files?path=${encodeURIComponent(path)}`)),
    api(themePath(state.name, state.id, `/files?path=${encodeURIComponent("config_patch.json")}`)),
  ]);
  const { fm, body } = splitFrontmatter(file.content);
  const desc = el("textarea", { rows: 3 });
  desc.value = fm.description || "";
  const model = el("input", { type: "text", placeholder: "空欄なら本番と同じ（inherit）" });
  model.value = fm.model || "";
  const current = new Set((fm.tools || "").split(",").map((t) => t.trim()).filter(Boolean));
  const tools = state.sources.tools || [];
  const toolBoxes = tools.map((t) => {
    const cb = el("input", { type: "checkbox", value: t });
    cb.checked = current.has(t);
    return el("label", { class: "checkbox tool-check" }, [cb, ` ${t}`]);
  });
  const bodyArea = el("textarea", { rows: 18, class: "code-area" });
  bodyArea.value = body;
  let patch = {};
  try {
    patch = JSON.parse(patchFile.content || "{}");
  } catch (e) {
    patch = {};
  }
  const allow = ((patch.subagent || {}).agent_type_run_script_allowlist || []).filter((e) => e[0] === agent).map((e) => (Array.isArray(e[1]) ? e[1].join("/") : e[1]));
  const allowInput = el("textarea", { rows: 3, placeholder: "1行に1つ。スキル名、または スキル名/スクリプト名.py" });
  allowInput.value = allow.join("\n");
  const skillNames = [...(state.sources.skills || []).map((s) => s.name), ...state.data.summary.assets.skills];
  const save = el("button", { class: "primary", text: "保存" });
  const m = labModal(
    `サブエージェント ${agent}`,
    [
      el("label", {}, ["説明（description。メインのアシスタントが委譲先を選ぶ手がかり）", desc]),
      el("div", {}, [el("div", { class: "hint", text: "使えるツール（tools）" }), el("div", { class: "tool-grid" }, toolBoxes.length ? toolBoxes : [el("span", { class: "hint", text: state.sources.tools_error || "ツール一覧を取得できません" })])]),
      el("label", {}, ["モデル（model、任意）", model]),
      el("label", {}, ["run_script で呼んでよいスキル（空欄なら制限なし）", allowInput]),
      el("p", { class: "hint", text: `スキル名の候補: ${[...new Set(skillNames)].join(", ")}` }),
      el("label", {}, ["本文（システムプロンプト。{{skills}}・{{run_script_allowlist}} はスキル一覧・許可一覧に置き換わります）", bodyArea]),
    ],
    { wide: true, actions: [save] },
  );
  save.addEventListener("click", async () => {
    const selected = toolBoxes.map((l) => l.querySelector("input")).filter((cb) => cb.checked).map((cb) => cb.value);
    const lines = ["---", `name: ${agent}`, `description: ${desc.value.replace(/\r?\n/g, " ").trim()}`];
    if (selected.length) lines.push(`tools: ${selected.join(", ")}`);
    if (model.value.trim()) lines.push(`model: ${model.value.trim()}`);
    lines.push("---", "");
    const content = lines.join("\n") + bodyArea.value.replace(/^\r?\n/, "");
    if (!(await saveThemeFile(state, path, content, m.errorBox))) return;
    const entries = allowInput.value.split(/\r?\n/).map((s) => s.trim()).filter(Boolean).map((s) => (s.includes("/") ? [agent, s.split("/", 2)] : [agent, s]));
    patch.subagent = patch.subagent || {};
    const others = (patch.subagent.agent_type_run_script_allowlist || []).filter((e) => e[0] !== agent);
    patch.subagent.agent_type_run_script_allowlist = [...others, ...entries];
    if (!patch.subagent.agent_type_run_script_allowlist.length) delete patch.subagent.agent_type_run_script_allowlist;
    if (!Object.keys(patch.subagent).length) delete patch.subagent;
    if (await saveThemeFile(state, "config_patch.json", JSON.stringify(patch, null, 2) + "\n", m.errorBox)) m.close();
  });
}

/* --- ケース --- */

function renderThemeCases(state, body) {
  const d = state.data;
  const editable = themeEditable(state);
  const err = el("div", { class: "error" });
  const rows = d.cases_list.map((c) => [
    c.id,
    c.error ? el("span", { class: "error", text: c.error }) : (c.turns || []).join(" → "),
    c.work_dir || "-",
    c.judge ? "あり" : "-",
    el("span", {}, [
      linkBtn("編集", () => openCaseEditor(state, c.id)),
      " ",
      linkBtn("YAML", () => openFileEditor(state, c.path, true)),
      " ",
      linkBtn("1回試す", () => addTask(state, "trial", { case_id: c.id }, err)),
    ]),
  ]);
  body.append(
    el("p", { class: "hint", text: "1件のケースで、複数のスキル・サブエージェントを使う流れを確かめられます。ユーザーの指示（turns）は実際の利用者が打つような短い日本語にします。" }),
    dataTable(["ID", "ユーザーの指示", "入力ファイル", "judge", ""], rows, "まだケースはありません。"),
    err,
  );
  if (editable) {
    const count = el("input", { type: "number", min: 1, max: 10, value: 3, class: "narrow" });
    const request = el("input", { type: "text", placeholder: "AI への補足（任意）" });
    body.append(
      el("div", { class: "inline-form" }, [
        el("button", { class: "primary", text: "＋ 新しいケース", onclick: () => openCaseEditor(state, null) }),
        el("span", { class: "hint", text: "AI に" }),
        count,
        el("span", { class: "hint", text: "件" }),
        request,
        el("button", { text: "下書きを頼む", onclick: () => addTask(state, "draft_cases", { count: Number(count.value) || 3, request: request.value }, err) }),
      ]),
    );
  }

  body.append(el("h4", { text: "入力ファイル（ケースの work_dir）" }));
  const fixtureFiles = d.files.filter((f) => f.path.startsWith("cases/fixtures/"));
  body.append(
    dataTable(
      ["ファイル", "サイズ", ""],
      fixtureFiles.map((f) => [f.path.replace("cases/", ""), `${f.size} B`, editable ? linkBtn("削除", () => deleteThemeFile(state, f.path, { errorBox: err, close: () => {} })) : ""]),
      "まだありません。",
    ),
  );
  if (editable) {
    const folder = el("input", { type: "text", placeholder: "フォルダ名（例: sample1）" });
    const fileInput = el("input", { type: "file", multiple: "multiple" });
    body.append(
      el("div", { class: "inline-form" }, [
        folder,
        fileInput,
        el("button", {
          text: "アップロード",
          onclick: async () => {
            if (!folder.value.trim() || !fileInput.files.length) {
              err.textContent = "フォルダ名とファイルを指定してください。";
              return;
            }
            for (const file of fileInput.files) {
              const content = await fileToBase64(file);
              const ok = await labAction(
                () => api(themePath(state.name, state.id, "/fixtures"), { method: "POST", body: { folder: folder.value.trim(), filename: file.name, content_base64: content } }),
                err,
              );
              if (!ok) return;
            }
            await state.reload();
            renderThemeSub(state, body);
          },
        }),
      ]),
      el("p", { class: "hint", text: "ケースの「入力ファイル」で fixtures/<フォルダ名> を選ぶと、評価のたびにそのフォルダの写しが作業フォルダになります（元のファイルは書き換わりません）。" }),
    );
  }
}

function linesOf(text) {
  return text.split(/\r?\n/).map((s) => s.trim()).filter(Boolean);
}

async function openCaseEditor(state, caseId) {
  const editable = themeEditable(state);
  const data = caseId ? (await api(themePath(state.name, state.id, `/cases/${encodeURIComponent(caseId)}`))).data : { turns: [""], auto_approve: true };
  const expect = data.expect || {};
  const id = el("input", { type: "text", placeholder: "例: 001_basic（英数字・-・_）" });
  id.value = caseId || "";
  if (caseId) id.disabled = true;
  const turns = el("textarea", { rows: 4, placeholder: "ユーザーの指示。複数ターンは --- だけの行で区切る" });
  turns.value = (data.turns || []).join("\n---\n");
  const assets = state.data.summary.assets;
  const readArgs = ((expect.tool_call_args_contains || {}).read_skill || {}).skill_name;
  const dispatchArgs = ((expect.tool_call_args_contains || {}).dispatch_agent || {}).agent_type;
  const skillSel = el("select", {}, [el("option", { value: "", text: "（確かめない）" }), ...[...assets.skills, ...state.sources.skills.map((s) => s.name)].filter((v, i, a) => a.indexOf(v) === i).map((n) => el("option", { value: n, text: n }))]);
  skillSel.value = readArgs || "";
  const agentSel = el("select", {}, [el("option", { value: "", text: "（確かめない）" }), ...[...assets.agents, ...state.sources.agents.map((a) => a.name)].filter((v, i, a) => a.indexOf(v) === i).map((n) => el("option", { value: n, text: n }))]);
  agentSel.value = dispatchArgs || "";
  const called = el("input", { type: "text", placeholder: "カンマ区切り（いずれかが呼ばれれば合格）" });
  called.value = (expect.tool_called_any || []).join(", ");
  const notCalled = el("input", { type: "text", placeholder: "カンマ区切り（どれも呼ばれなければ合格）" });
  notCalled.value = (expect.tool_not_called || []).join(", ");
  const contains = el("textarea", { rows: 2, placeholder: "1行に1つ（最終回答にすべて含まれれば合格）" });
  contains.value = (expect.response_contains || []).join("\n");
  const notContains = el("textarea", { rows: 2, placeholder: "1行に1つ（最終回答にどれも含まれなければ合格）" });
  notContains.value = (expect.response_not_contains || []).join("\n");
  const judgeArea = el("textarea", { rows: 3, placeholder: "客観的に決まらない点の判定観点（AI judge が判定し、昇格時に人が確認します）" });
  judgeArea.value = data.judge || "";
  const workDir = el("select", {}, [el("option", { value: "", text: "（使わない）" }), ...state.data.fixtures.map((f) => el("option", { value: f, text: f }))]);
  workDir.value = data.work_dir || "";
  const answers = el("textarea", { rows: 2, placeholder: "AI がユーザーに質問したときに返す答え（1行に1つ、順に使う）" });
  answers.value = (data.scripted_text_answers || []).join("\n");
  const autoApprove = el("input", { type: "checkbox" });
  autoApprove.checked = data.auto_approve !== false;
  const timeout = el("input", { type: "number", min: 60, placeholder: "既定900", class: "narrow" });
  timeout.value = data.timeout_seconds || "";
  const notes = el("input", { type: "text", placeholder: "このケースで確かめること" });
  notes.value = data.notes || "";
  const save = el("button", { class: "primary", text: "保存", disabled: editable ? null : "disabled" });
  const m = labModal(
    caseId ? `ケース ${caseId}` : "新しいケース",
    [
      el("label", {}, ["ID", id]),
      el("label", {}, ["ユーザーの指示（turns）", turns]),
      el("fieldset", { class: "case-expect" }, [
        el("legend", { text: "機械的な判定（expect）" }),
        el("label", {}, ["このスキルを読んだか（read_skill）", skillSel]),
        el("label", {}, ["このサブエージェントへ委譲したか（dispatch_agent）", agentSel]),
        el("label", {}, ["呼ばれるべきツール", called]),
        el("label", {}, ["呼ばれてはいけないツール", notCalled]),
        el("label", {}, ["回答に含まれるべき文字列", contains]),
        el("label", {}, ["回答に含まれてはいけない文字列", notContains]),
      ]),
      el("label", {}, ["判定観点（judge）", judgeArea]),
      el("label", {}, ["入力ファイル（work_dir）", workDir]),
      el("label", {}, ["質問への答え（scripted_text_answers）", answers]),
      el("label", { class: "checkbox" }, [autoApprove, " スクリプト実行・計画の承認ダイアログを自動で承認する"]),
      el("label", {}, ["タイムアウト（秒）", timeout]),
      el("label", {}, ["メモ", notes]),
    ],
    { wide: true, actions: [save] },
  );
  save.addEventListener("click", async () => {
    const cid = id.value.trim();
    if (!cid) {
      m.errorBox.textContent = "ID を入力してください。";
      return;
    }
    const exp = {};
    const args = {};
    if (skillSel.value) args.read_skill = { skill_name: skillSel.value };
    if (agentSel.value) args.dispatch_agent = { agent_type: agentSel.value };
    if (Object.keys(args).length) exp.tool_call_args_contains = { ...(expect.tool_call_args_contains || {}), ...args };
    else if (expect.tool_call_args_contains) {
      const rest = { ...expect.tool_call_args_contains };
      delete rest.read_skill;
      delete rest.dispatch_agent;
      if (Object.keys(rest).length) exp.tool_call_args_contains = rest;
    }
    const csv = (s) => s.split(",").map((x) => x.trim()).filter(Boolean);
    if (csv(called.value).length) exp.tool_called_any = csv(called.value);
    if (csv(notCalled.value).length) exp.tool_not_called = csv(notCalled.value);
    if (linesOf(contains.value).length) exp.response_contains = linesOf(contains.value);
    if (linesOf(notContains.value).length) exp.response_not_contains = linesOf(notContains.value);
    const payload = {
      turns: turns.value.split(/\r?\n---\r?\n/).map((s) => s.trim()).filter(Boolean),
      expect: Object.keys(exp).length ? exp : null,
      judge: judgeArea.value.trim() || null,
      work_dir: workDir.value || null,
      scripted_text_answers: linesOf(answers.value),
      auto_approve: autoApprove.checked,
      timeout_seconds: timeout.value ? Number(timeout.value) : null,
      notes: notes.value.trim() || null,
    };
    const ok = await labAction(() => api(themePath(state.name, state.id, `/cases/${encodeURIComponent(cid)}`), { method: "PUT", body: { data: payload } }), m.errorBox);
    if (ok) {
      m.close();
      await state.reload();
      renderThemeSub(state, $(".theme-body"));
    }
  });
}

function showTrialResult(r) {
  if (!r) return;
  const outcome = { pass: "合格", fail: "不合格", error: "エラー" }[r.outcome] || r.outcome;
  labModal(
    `1回試行: ${r.case_id}（${outcome}）`,
    [
      r.ai_judge ? el("p", {}, [el("strong", { text: `AI judge: ${r.ai_judge.pass ? "合格" : "不合格"} ` }), r.ai_judge.reason]) : null,
      r.error ? el("p", { class: "error", text: `${r.error}: ${r.detail || ""}` }) : null,
      el("h4", { text: "満たさなかった判定" }),
      el("pre", { class: "lab-pre", text: JSON.stringify(Object.fromEntries(Object.entries(r.rule_results || {}).filter(([, v]) => !v.pass)), null, 2) }),
      el("h4", { text: "最終回答" }),
      el("pre", { class: "lab-pre", text: r.final_answer || "" }),
      el("h4", { text: "実行の記録（抜粋）" }),
      el("pre", { class: "lab-pre", text: r.excerpt || "" }),
    ].filter(Boolean),
    { wide: true },
  );
}

/* --- 実行の記録 --- */

function renderThemeRuns(state, body) {
  const its = (state.data.iterations || []).slice().reverse();
  const rows = its.map((it) => [
    String(it.n),
    it.phase === "tryout" ? `トライアウト（各${it.repeat}回）` : "評価（1回）",
    el("span", { class: it.verdict === "pass" ? "ok-text" : "error", text: it.verdict === "pass" ? "全回合格" : "不合格あり" }),
    Object.entries(it.cases || {}).map(([cid, c]) => `${cid}: ${c.pass}/${c.pass + c.fail + c.error}`).join("、"),
    it.fix ? (it.fix.applied && it.fix.applied.length ? `AI が直した: ${it.fix.applied.join(", ")}` : `直せず: ${(it.fix.errors || []).join(" / ")}`) : "-",
    linkBtn("詳しく", () => openIterationDetail(state, it.n)),
  ]);
  body.append(
    el("p", { class: "hint", text: "評価で失敗があると AI が原因を分析して直し、もう一度評価します。全ケースに合格したらトライアウト（対象インスタンスの tryout_repeats 回ずつ）を行い、全回合格でレビュー待ちになります。" }),
    dataTable(["反復", "種類", "判定", "ケースごとの合格", "AI 修正", ""], rows, "まだ実行していません。"),
  );
}

async function openIterationDetail(state, n) {
  const it = await api(themePath(state.name, state.id, `/iterations/${n}`));
  const nodes = [];
  if (it.fix) {
    nodes.push(el("h4", { text: "AI の分析と修正" }), el("p", { text: it.fix.analysis || "" }));
    if (it.fix.applied && it.fix.applied.length) nodes.push(el("p", { text: `直したファイル: ${it.fix.applied.join(", ")}` }));
    if (it.fix.errors && it.fix.errors.length) nodes.push(el("p", { class: "error", text: `適用できなかった理由: ${it.fix.errors.join(" / ")}` }));
  }
  nodes.push(el("h4", { text: "各回の結果" }));
  for (const r of it.runs) {
    const outcome = { pass: "合格", fail: "不合格", error: "エラー" }[r.outcome] || r.outcome;
    nodes.push(
      el("details", { class: `run-detail run-${r.outcome}` }, [
        el("summary", { text: `${r.case_id}（${r.repeat_index}回目）: ${outcome}${r.ai_judge ? ` ／ AI judge: ${r.ai_judge.reason}` : ""}` }),
        Object.keys(r.failed_rules || {}).length ? el("pre", { class: "lab-pre", text: JSON.stringify(r.failed_rules, null, 2) }) : null,
        r.error ? el("p", { class: "error", text: `${r.error}: ${r.detail || ""}` }) : null,
        el("div", { class: "hint", text: "最終回答" }),
        el("pre", { class: "lab-pre", text: r.final_answer || "" }),
        el("div", { class: "hint", text: "実行の記録（抜粋）" }),
        el("pre", { class: "lab-pre", text: r.excerpt || "" }),
      ]),
    );
  }
  labModal(`反復 ${n}（${it.phase === "tryout" ? "トライアウト" : "評価"}）`, nodes, { wide: true });
}

/* --- レビュー・昇格 --- */

async function renderThemeReview(state, body) {
  const d = state.data;
  const t = d.tryout;
  if (t) {
    body.append(
      el("h4", { text: "トライアウト" }),
      el("p", {}, [
        `各ケース ${t.repeat} 回、全回合格（${fmtTime(t.at)}、反復 ${t.iteration}）。`,
        t.ai_judged ? el("strong", { text: " judge 付きのケースは AI が判定しています。昇格の承認は、その判定を人が確認したことを意味します。" }) : "",
      ]),
    );
    if (d.tryout_problems && d.tryout_problems.length) body.append(el("p", { class: "error", text: d.tryout_problems.join(" / ") }));
  } else {
    body.append(el("p", { class: "hint", text: "有効なトライアウトの結果がありません（資産・ケース・設定パッチを変えると0回からやり直しになります）。" }));
  }

  const diff = await api(themePath(state.name, state.id, "/diff"));
  body.append(el("h4", { text: "取り込み・複製した時点（新規は空）からの変更" }));
  if (diff.ai_changed_cases) body.append(el("p", { class: "warn-box", text: "AI がケースを変更しています。ケースが仕様どおりの確かめ方になっているか、「履歴」と「ケース」で確認してください。" }));
  if (!diff.files.length) body.append(el("p", { class: "hint", text: "変更はありません。" }));
  for (const f of diff.files) {
    body.append(
      el("details", { class: `diff-file ${f.important ? "diff-important" : ""}` }, [
        el("summary", { text: `${f.path}（${{ added: "追加", deleted: "削除", modified: "変更" }[f.status]}）${f.important ? " ★スクリプト" : ""}` }),
        el("pre", { class: "lab-pre diff-pre", text: f.diff }),
      ]),
    );
  }

  if (!themeEditable(state)) {
    if (d.promotion) body.append(el("h4", { text: "昇格の記録" }), el("pre", { class: "lab-pre", text: JSON.stringify(d.promotion, null, 2) }));
    if (d.returned) body.append(el("h4", { text: "差し戻しの記録" }), el("pre", { class: "lab-pre", text: JSON.stringify(d.returned, null, 2) }));
    return;
  }

  body.append(el("h4", { text: "昇格" }));
  const promoBox = el("div", { class: "promote-box" });
  body.append(promoBox);
  await renderPromoteBox(state, promoBox, []);

  body.append(el("h4", { text: "差し戻し・不採用" }));
  const reason = el("textarea", { rows: 3, placeholder: "作成者が直せるよう、具体的に（作成者の skill-creator に表示されます）" });
  const applyFixes = el("input", { type: "checkbox" });
  const err = el("div", { class: "error" });
  const doReturn = async (reject) => {
    if (!confirm(reject ? "不採用にします。よろしいですか？" : "取り込み元のドラフトへ差し戻します。よろしいですか？")) return;
    const res = await labAction(
      () => api(themePath(state.name, state.id, "/return"), { method: "POST", body: { reason: reason.value, apply_fixes: applyFixes.checked, reject } }),
      err,
    );
    if (res) {
      await state.reload();
      renderThemeSub(state, body);
    }
  };
  body.append(
    reason,
    el("label", { class: "checkbox" }, [applyFixes, " 研究室での修正をドラフトへ写してから差し戻す"]),
    el("div", { class: "inline-form" }, [el("button", { text: "差し戻す", onclick: () => doReturn(false) }), el("button", { class: "btn-danger", text: "不採用にする", onclick: () => doReturn(true) })]),
    err,
  );
}

async function renderPromoteBox(state, box, distributeTo) {
  box.innerHTML = "";
  const qs = distributeTo.map((n) => `distribute_to=${encodeURIComponent(n)}`).join("&");
  const check = await api(themePath(state.name, state.id, `/promote-check${qs ? `?${qs}` : ""}`));
  for (const p of check.problems) box.append(el("p", { class: "error", text: `× ${p}` }));
  for (const n of check.notices) box.append(el("p", { class: "hint", text: `・${n}` }));
  for (const [inst, pl] of Object.entries(check.placements)) {
    box.append(
      el("div", { class: "placement" }, [
        el("strong", { text: `${inst} の専用の置き場へ: ` }),
        el("code", { text: pl.dir }),
        el("ul", {}, [
          ...[...pl.skills, ...pl.agents].map((p) => el("li", { text: p })),
          ...(pl.replaces || []).map((r) => el("li", { class: "hint", text: `置き換え（元は evals/history/promote/ へ退避）: ${r}` })),
          ...Object.entries(pl.register || {}).map(([k, v]) => el("li", { class: "hint", text: `設定に追加: [${k.replace(".", "].")} ${JSON.stringify(v)}` })),
        ]),
      ]),
    );
  }
  const distBoxes = check.distributable.map((n) => {
    const cb = el("input", { type: "checkbox", value: n });
    cb.checked = distributeTo.includes(n);
    cb.addEventListener("change", () => renderPromoteBox(state, box, distBoxes.map((l) => l.querySelector("input")).filter((c) => c.checked).map((c) => c.value)));
    return el("label", { class: "checkbox" }, [cb, ` ${n}`]);
  });
  if (distBoxes.length) box.append(el("div", { class: "hint", text: "他のインスタンスにも配布する（任意。それぞれの専用の置き場へ複製し、設定も登録します）" }), el("div", { class: "inline-form" }, distBoxes));
  const confirmBoxes = check.confirmations.map((c) => el("label", { class: "checkbox warn-box" }, [el("input", { type: "checkbox", class: "confirm-cb" }), ` ${c}`]));
  box.append(...confirmBoxes);
  const note = el("input", { type: "text", placeholder: "判定メモ（任意。evals/promotion_log.md に残ります）" });
  const err = el("div", { class: "error" });
  const btn = el("button", { class: "primary", text: "承認して昇格する", disabled: check.ok ? null : "disabled" });
  btn.addEventListener("click", async () => {
    if (confirmBoxes.some((l) => !l.querySelector("input").checked)) {
      err.textContent = "確認事項にチェックを入れてください。";
      return;
    }
    if (!confirm("昇格します。配置先のインスタンスは再起動後に使えるようになります。よろしいですか？")) return;
    const res = await labAction(
      () => api(themePath(state.name, state.id, "/promote"), { method: "POST", body: { distribute_to: distributeTo, discard_source_changes: confirmBoxes.length > 0, note: note.value } }),
      err,
    );
    if (!res) return;
    alert(
      `昇格しました。${res.archived_drafts.length ? `ドラフト ${res.archived_drafts.length} 件をアーカイブしました。` : ""}` +
        (res.restart_needed.length ? `\n稼働中のインスタンス（${res.restart_needed.join(", ")}）を再起動すると使えるようになります。` : ""),
    );
    await state.reload();
    renderThemeSub(state, $(".theme-body"));
  });
  box.append(note, btn, err);
}

/* --- 履歴 --- */

function renderThemeHistory(state, body) {
  const rows = (state.data.history || []).slice().reverse().map((h) => [fmtTime(h.at), h.actor, h.action, h.detail || ""]);
  body.append(dataTable(["日時", "実行者", "操作", "内容"], rows, "まだありません。"));
}
