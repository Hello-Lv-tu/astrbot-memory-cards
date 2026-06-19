const bridge = window.AstrBotPluginPage;
await bridge.ready();

const categories = ["偏好", "习惯", "人物", "事件", "雷区", "目标", "待办", "其他"];
const state = {
  users: [],
  notes: [],
  scopeKey: "",
  category: "全部",
  keyword: "",
  editingId: null,
  deletingId: null,
  qualityPreview: null,
  notesRequestVersion: 0,
};

const elements = {
  userSelect: document.getElementById("user-select"),
  searchInput: document.getElementById("search-input"),
  categoryList: document.getElementById("category-list"),
  newNote: document.getElementById("new-note"),
  qualityButton: document.getElementById("quality-button"),
  historyButton: document.getElementById("history-button"),
  noteGrid: document.getElementById("note-grid"),
  noteCount: document.getElementById("note-count"),
  status: document.getElementById("status"),
  emptyState: document.getElementById("empty-state"),
  noteDialog: document.getElementById("note-dialog"),
  noteForm: document.getElementById("note-form"),
  dialogTitle: document.getElementById("dialog-title"),
  noteId: document.getElementById("note-id"),
  noteCategory: document.getElementById("note-category"),
  noteContent: document.getElementById("note-content"),
  contentCount: document.getElementById("content-count"),
  deleteDialog: document.getElementById("delete-dialog"),
  confirmDelete: document.getElementById("confirm-delete"),
  qualityPanel: document.getElementById("quality-panel"),
  qualityList: document.getElementById("quality-list"),
  qualityFingerprint: document.getElementById("quality-fingerprint"),
  applyQuality: document.getElementById("apply-quality"),
  cancelQuality: document.getElementById("cancel-quality"),
  historyPanel: document.getElementById("history-panel"),
  historyList: document.getElementById("history-list"),
};

function setStatus(message, isError = false) {
  elements.status.textContent = message;
  elements.status.classList.toggle("error", isError);
}

function unwrap(result) {
  if (!result || result.ok !== true) {
    throw new Error(result?.message || "操作失败，请稍后重试");
  }
  return result;
}

function formatDate(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "";
  }
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function createButton(label, className, handler) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = className;
  button.textContent = label;
  button.addEventListener("click", handler);
  return button;
}

function renderUsers() {
  elements.userSelect.replaceChildren();
  if (state.users.length === 0) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "还没有收到私聊消息";
    elements.userSelect.append(option);
    elements.userSelect.disabled = true;
    return;
  }

  elements.userSelect.disabled = false;
  for (const user of state.users) {
    const option = document.createElement("option");
    option.value = user.scope_key;
    const pending = user.pending_message_count
      ? ` · 待整理 ${user.pending_message_count}`
      : "";
    option.textContent = `${user.display_name || user.user_id} · ${user.platform_id} (${user.note_count})${pending}`;
    elements.userSelect.append(option);
  }
  elements.userSelect.value = state.scopeKey;
}

function openEditor(note = null) {
  if (!state.scopeKey) {
    setStatus("请先让用户与机器人私聊一次。", true);
    return;
  }
  state.editingId = note?.id || null;
  elements.dialogTitle.textContent = note ? "编辑便签" : "新建便签";
  elements.noteId.value = note?.id || "";
  elements.noteCategory.value = categories.includes(note?.category) ? note.category : "其他";
  elements.noteContent.value = note?.content || "";
  elements.contentCount.textContent = String(elements.noteContent.value.length);
  elements.noteDialog.showModal();
  elements.noteContent.focus();
}

function requestDelete(noteId) {
  state.deletingId = noteId;
  elements.deleteDialog.showModal();
}

function renderNotes() {
  elements.noteGrid.replaceChildren();
  elements.noteCount.textContent = String(state.notes.length);
  elements.emptyState.hidden = state.notes.length !== 0;

  for (const note of state.notes) {
    const article = document.createElement("article");
    article.className = "note-card";
    article.dataset.category = note.category;

    const header = document.createElement("header");
    const category = document.createElement("span");
    category.className = "note-category";
    category.textContent = note.category;
    const labels = document.createElement("div");
    labels.className = "note-labels";
    labels.append(category);
    if (note.source === "auto") {
      const source = document.createElement("span");
      source.className = "source-badge";
      source.textContent = "自动生成";
      labels.append(source);
    }
    const date = document.createElement("time");
    date.dateTime = note.updated_at;
    date.textContent = formatDate(note.updated_at);
    header.append(labels, date);

    const content = document.createElement("p");
    content.className = "note-content";
    content.textContent = note.content;

    const actions = document.createElement("footer");
    actions.append(
      createButton("编辑", "text-button", () => openEditor(note)),
      createButton("删除", "text-button danger-text", () => requestDelete(note.id)),
    );

    article.append(header, content, actions);
    elements.noteGrid.append(article);
  }
}

function renderQualityPreview(preview) {
  elements.qualityList.replaceChildren();
  elements.qualityPanel.hidden = false;
  elements.qualityFingerprint.textContent = preview?.fingerprint
    ? "版本指纹：" + preview.fingerprint
    : "";
  const items = preview?.items || [];
  elements.applyQuality.disabled = !preview?.preview_id || items.length === 0;
  if (items.length === 0) {
    const empty = document.createElement("p");
    empty.textContent = "当前没有可整理项。";
    elements.qualityList.append(empty);
    return;
  }
  for (const item of items) {
    const row = document.createElement("article");
    row.className = "quality-item";
    const title = document.createElement("h3");
    title.textContent = item.action;
    const content = document.createElement("p");
    content.textContent = item.content || item.reason || "";
    const before = document.createElement("p");
    before.className = "muted";
    before.textContent = item.before?.length
      ? "整理前：" + item.before.map((note) => note.content).join(" / ")
      : "";
    const ids = document.createElement("p");
    ids.className = "muted";
    ids.textContent = item.note_ids ? "候选 ID：" + item.note_ids.join(", ") : "";
    row.append(title, before, content, ids);
    elements.qualityList.append(row);
  }
}

function renderHistory(items) {
  elements.historyList.replaceChildren();
  elements.historyPanel.hidden = false;
  if (items.length === 0) {
    const empty = document.createElement("p");
    empty.textContent = "暂无变更历史。";
    elements.historyList.append(empty);
    return;
  }
  for (const item of items) {
    const row = document.createElement("article");
    row.className = "history-item";
    const title = document.createElement("h3");
    title.textContent = item.change_type + " · #" + item.note_id;
    const content = document.createElement("p");
    content.textContent = item.before_content;
    const reason = document.createElement("p");
    reason.className = "muted";
    reason.textContent = item.reason || formatDate(item.created_at);
    row.append(title, content, reason);
    elements.historyList.append(row);
  }
}

async function loadUsers() {
  setStatus("正在读取私聊用户…");
  try {
    const result = unwrap(await bridge.apiGet("memory/users"));
    state.users = result.items;
    if (!state.users.some((user) => user.scope_key === state.scopeKey)) {
      state.scopeKey = state.users[0]?.scope_key || "";
    }
    renderUsers();
    await loadNotes();
  } catch (error) {
    setStatus(error.message, true);
  }
}

async function loadNotes() {
  const requestVersion = ++state.notesRequestVersion;
  const scopeKey = state.scopeKey;
  const category = state.category;
  const keyword = state.keyword;
  if (!state.scopeKey) {
    state.notes = [];
    renderNotes();
    setStatus("用户私聊机器人后，会自动出现在这里。");
    return;
  }
  setStatus("正在整理便签…");
  try {
    const result = unwrap(
      await bridge.apiGet("memory/notes", {
        scope_key: scopeKey,
        category,
        keyword,
        limit: 100,
        offset: 0,
      }),
    );
    if (
      requestVersion !== state.notesRequestVersion ||
      scopeKey !== state.scopeKey ||
      category !== state.category ||
      keyword !== state.keyword
    ) {
      return;
    }
    state.notes = result.items;
    renderNotes();
    setStatus(result.total === 0 ? "" : `已显示 ${result.total} 条便签`);
  } catch (error) {
    setStatus(error.message, true);
  }
}

async function saveNote() {
  const content = elements.noteContent.value.trim();
  if (!content) {
    setStatus("便签内容不能为空。", true);
    return;
  }
  const editing = state.editingId !== null;
  const endpoint = editing ? "memory/notes/update" : "memory/notes/create";
  const payload = {
    scope_key: state.scopeKey,
    category: elements.noteCategory.value,
    content,
  };
  if (editing) {
    payload.id = state.editingId;
  }

  setStatus(editing ? "正在保存修改…" : "正在创建便签…");
  try {
    unwrap(await bridge.apiPost(endpoint, payload));
    elements.noteDialog.close();
    await loadUsers();
    setStatus(editing ? "便签已更新。" : "便签已创建。");
  } catch (error) {
    setStatus(error.message, true);
  }
}

async function deleteNote() {
  if (state.deletingId === null) {
    return;
  }
  setStatus("正在删除便签…");
  try {
    unwrap(
      await bridge.apiPost("memory/notes/delete", {
        scope_key: state.scopeKey,
        id: state.deletingId,
      }),
    );
    state.deletingId = null;
    elements.deleteDialog.close();
    await loadUsers();
    setStatus("便签已删除。");
  } catch (error) {
    setStatus(error.message, true);
  }
}

async function loadQualityPreview() {
  if (!state.scopeKey) {
    setStatus("请先选择私聊用户。", true);
    return;
  }
  setStatus("正在生成质量整理预览…");
  try {
    const result = unwrap(
      await bridge.apiPost("memory/quality/preview", {
        scope_key: state.scopeKey,
      }),
    );
    state.qualityPreview = result.preview;
    renderQualityPreview(result.preview);
    setStatus("质量整理预览已生成。");
  } catch (error) {
    setStatus(error.message, true);
  }
}

async function applyQualityPreview() {
  if (!state.qualityPreview?.preview_id) {
    return;
  }
  setStatus("正在应用质量整理…");
  try {
    unwrap(
      await bridge.apiPost("memory/quality/apply", {
        scope_key: state.scopeKey,
        preview_id: state.qualityPreview.preview_id,
      }),
    );
    state.qualityPreview = null;
    elements.qualityPanel.hidden = true;
    await loadUsers();
    setStatus("质量整理已应用。");
  } catch (error) {
    setStatus(error.message, true);
  }
}

function cancelQualityPreview() {
  state.qualityPreview = null;
  elements.qualityPanel.hidden = true;
  elements.qualityList.replaceChildren();
  setStatus("已取消质量整理。");
}

async function loadHistory() {
  if (!state.scopeKey) {
    setStatus("请先选择私聊用户。", true);
    return;
  }
  setStatus("正在读取变更历史…");
  try {
    const result = unwrap(
      await bridge.apiGet("memory/history", {
        scope_key: state.scopeKey,
        limit: 100,
        offset: 0,
      }),
    );
    renderHistory(result.items);
    setStatus("变更历史已读取。");
  } catch (error) {
    setStatus(error.message, true);
  }
}

elements.userSelect.addEventListener("change", async () => {
  state.scopeKey = elements.userSelect.value;
  cancelQualityPreview();
  elements.historyPanel.hidden = true;
  await loadNotes();
});

let searchTimer;
elements.searchInput.addEventListener("input", () => {
  window.clearTimeout(searchTimer);
  searchTimer = window.setTimeout(async () => {
    state.keyword = elements.searchInput.value.trim();
    await loadNotes();
  }, 250);
});

elements.categoryList.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-category]");
  if (!button) {
    return;
  }
  state.category = button.dataset.category;
  for (const chip of elements.categoryList.querySelectorAll(".category-chip")) {
    chip.classList.toggle("active", chip === button);
  }
  await loadNotes();
});

elements.newNote.addEventListener("click", () => openEditor());
elements.qualityButton.addEventListener("click", loadQualityPreview);
elements.historyButton.addEventListener("click", loadHistory);
elements.applyQuality.addEventListener("click", applyQualityPreview);
elements.cancelQuality.addEventListener("click", cancelQualityPreview);
elements.noteContent.addEventListener("input", () => {
  elements.contentCount.textContent = String(elements.noteContent.value.length);
});
elements.noteForm.addEventListener("submit", async (event) => {
  if (event.submitter?.value !== "default") {
    return;
  }
  event.preventDefault();
  await saveNote();
});
elements.confirmDelete.addEventListener("click", async (event) => {
  event.preventDefault();
  await deleteNote();
});

await loadUsers();
