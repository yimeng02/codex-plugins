'use strict';

const $ = (id) => document.getElementById(id);
const scopeNames = { global: '全局', project: '项目', skill: 'Skill', plugin: '插件' };
const model = {
  categories: [], entries: [], revision: null, category: 'all', selected: new Set(),
  editingId: null, draft: false, dirty: false, busy: false, loaded: false,
  categoryEditingId: null, history: [], historyEntryId: null, historyVersion: null,
  markdown: '', markdownScope: 'global',
};

function node(tag, className, text) {
  const result = document.createElement(tag);
  if (className) result.className = className;
  if (text !== undefined) result.textContent = String(text);
  return result;
}

function notice(message, error = false) {
  $('notice').textContent = message;
  $('notice').classList.toggle('error', error);
  $('notice').hidden = !message;
  const dialog = document.querySelector('dialog[open]');
  if (dialog && message) {
    let local = dialog.querySelector('.dialog-notice');
    if (!local) {
      local = node('div', 'notice dialog-notice');
      local.setAttribute('role', 'status');
      dialog.querySelector('.dialog-header').after(local);
    }
    local.textContent = message;
    local.classList.toggle('error', error);
  }
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { ...options.headers, ...(options.body !== undefined ? { 'Content-Type': 'application/json' } : {}) },
    body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
  });
  let data;
  try { data = await response.json(); }
  catch { data = null; }
  if (!response.ok) throw new Error(data?.error || `请求失败（HTTP ${response.status}）`);
  return data;
}

function reportError(error) {
  notice(error instanceof Error ? error.message : '操作失败，请稍后重试。', true);
}

async function run(action) {
  try { await action(); }
  catch (error) { reportError(error); }
}

async function mutate(action) {
  if (model.busy) return;
  model.busy = true;
  updateWriteState();
  try { await action(); }
  catch (error) {
    reportError(error);
    if (model.dirty) $('save-status').textContent = '保存失败，更改仍保留在编辑器中';
  } finally {
    model.busy = false;
    updateWriteState();
  }
}

function updateWriteState() {
  document.querySelectorAll('[data-write]').forEach((button) => { button.disabled = model.busy; });
  $('save-entry').disabled = model.busy || isDeleted(currentEntry() || {});
  $('restore-version').disabled = model.busy || !model.historyVersion;
  $('save-entry').textContent = model.busy ? '处理中…' : '保存记忆';
}

function isDeleted(entry) { return Boolean(entry.deleted || entry.deleted_at); }
function categoryName(id) { return model.categories.find((category) => category.id === id)?.name || '未分类'; }
function currentEntry() { return model.entries.find((entry) => entry.id === model.editingId); }
function activeEntries() { return model.entries.filter((entry) => !isDeleted(entry)); }

function dateTime(value) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).format(date);
}

function categoryOptions() {
  const result = [], visited = new Set();
  function append(parentId, depth) {
    model.categories.filter((category) => (category.parent_id || null) === parentId).forEach((category) => {
      if (visited.has(category.id)) return;
      visited.add(category.id);
      result.push({ ...category, depth });
      append(category.id, depth + 1);
    });
  }
  append(null, 0);
  model.categories.forEach((category) => {
    if (!visited.has(category.id)) result.push({ ...category, depth: 0 });
  });
  return result;
}

function fillCategorySelect(select, placeholder, selected = select.value, exclude = new Set()) {
  const fragment = document.createDocumentFragment();
  if (placeholder !== null) {
    const option = node('option', '', placeholder);
    option.value = '';
    fragment.append(option);
  }
  categoryOptions().forEach((category) => {
    if (exclude.has(category.id)) return;
    const option = node('option', '', `${'　'.repeat(Math.min(category.depth, 5))}${category.name}`);
    option.value = category.id;
    fragment.append(option);
  });
  select.replaceChildren(fragment);
  if ([...select.options].some((option) => option.value === selected)) select.value = selected;
}

function navButton(label, count, key, symbol, depth = 0) {
  const button = node('button', `nav-button${model.category === key ? ' active' : ''}`);
  button.type = 'button';
  button.title = label;
  button.setAttribute('aria-current', model.category === key ? 'page' : 'false');
  if (depth) button.classList.add(`depth-${Math.min(depth, 5)}`);
  button.append(node('span', symbol ? 'nav-symbol' : 'category-dot', symbol || undefined), node('span', 'nav-name', label), node('span', 'nav-count', count));
  button.addEventListener('click', () => {
    model.category = key;
    model.selected.clear();
    renderSidebar();
    renderList();
  });
  return button;
}

function renderSidebar() {
  const active = activeEntries();
  $('library-nav').replaceChildren(
    navButton('全部记忆', active.length, 'all', '▦'),
    navButton('回收站', model.entries.filter(isDeleted).length, 'trash', '♧'),
  );
  const fragment = document.createDocumentFragment();
  categoryOptions().forEach((category) => fragment.append(navButton(category.name, active.filter((entry) => entry.category_id === category.id).length, category.id, null, category.depth)));
  const add = node('button', 'nav-button add-category-mobile', '＋ 新增分类');
  add.addEventListener('click', () => openCategory());
  fragment.append(add);
  if (model.categories.some((category) => category.id === model.category)) {
    const controls = node('div', 'category-mobile-controls');
    const edit = node('button', 'text-button', '编辑分类');
    edit.addEventListener('click', () => openCategory(model.category));
    const remove = node('button', 'text-button danger', '删除分类');
    remove.addEventListener('click', deleteCategory);
    controls.append(edit, remove);
    fragment.append(controls);
  }
  $('category-list').replaceChildren(fragment);
  $('category-actions').hidden = !model.categories.some((category) => category.id === model.category);
}

function visibleEntries() {
  const search = $('search').value.trim().toLocaleLowerCase();
  const scope = $('scope-filter').value;
  const status = $('status-filter').value;
  return model.entries.filter((entry) => {
    if (isDeleted(entry) !== (model.category === 'trash')) return false;
    if (!['all', 'trash'].includes(model.category) && entry.category_id !== model.category) return false;
    if (scope && entry.scope !== scope) return false;
    if (status && Boolean(entry.enabled) !== (status === 'enabled')) return false;
    if (search) {
      const searchable = [entry.title, entry.content, entry.target, ...(Array.isArray(entry.tags) ? entry.tags : [])].join(' ').toLocaleLowerCase();
      if (!searchable.includes(search)) return false;
    }
    return true;
  }).sort((a, b) => String(b.updated_at || '').localeCompare(String(a.updated_at || '')) || String(a.title || '').localeCompare(String(b.title || ''), 'zh-CN'));
}

function renderList() {
  const entries = visibleEntries();
  const visibleIds = new Set(entries.map((entry) => entry.id));
  model.selected = new Set([...model.selected].filter((id) => visibleIds.has(id)));
  const title = model.category === 'all' ? '全部记忆' : model.category === 'trash' ? '回收站' : categoryName(model.category);
  $('list-heading').replaceChildren(document.createTextNode(`${title} `), node('span', 'count-badge', entries.length));
  $('list-description').textContent = model.category === 'trash' ? '删除的记忆保留在这里，可随时恢复。' : '将反复提醒的事，变成可维护的规则。';
  const fragment = document.createDocumentFragment();
  entries.forEach((entry) => {
    const row = node('tr', model.editingId === entry.id ? 'selected-row' : '');
    const checkboxCell = node('td', 'check-cell');
    const checkbox = node('input');
    checkbox.type = 'checkbox';
    checkbox.checked = model.selected.has(entry.id);
    checkbox.setAttribute('aria-label', `选择 ${entry.title || '无标题记忆'}`);
    checkbox.addEventListener('click', (event) => event.stopPropagation());
    checkbox.addEventListener('change', () => {
      if (checkbox.checked) model.selected.add(entry.id); else model.selected.delete(entry.id);
      renderSelection();
    });
    checkboxCell.append(checkbox);
    const titleCell = node('td');
    const titleButton = node('button', 'entry-title-button', entry.title || '无标题记忆');
    titleButton.type = 'button';
    titleButton.title = entry.title || '';
    const subtitle = node('div', 'entry-subtitle', categoryName(entry.category_id));
    const tags = Array.isArray(entry.tags) ? entry.tags : [];
    if (tags.length) subtitle.append(document.createTextNode('  ·  '), node('span', 'tag-label', tags.slice(0, 2).map((tag) => `#${tag}`).join(' ')));
    titleCell.append(titleButton, subtitle);
    const scopeCell = node('td');
    scopeCell.append(node('span', `scope-pill ${scopeNames[entry.scope] ? entry.scope : ''}`, scopeNames[entry.scope] || entry.scope || '全局'));
    const statusCell = node('td');
    statusCell.append(node('span', `state-pill${entry.enabled ? ' enabled' : ''}`, entry.enabled ? '已启用' : '已停用'));
    const priorityCell = node('td', 'priority-number', entry.priority ?? 50);
    const updatedCell = node('td', 'updated-text', dateTime(entry.updated_at));
    row.append(checkboxCell, titleCell, scopeCell, statusCell, priorityCell, updatedCell);
    row.addEventListener('click', () => openEntry(entry));
    fragment.append(row);
  });
  $('entry-list').replaceChildren(fragment);
  $('list-empty').hidden = entries.length > 0;
  $('empty-title').textContent = model.category === 'trash' ? '回收站是空的' : model.entries.length ? '没有匹配的记忆' : '还没有记忆';
  $('empty-description').textContent = model.category === 'trash' ? '移入回收站的记忆会显示在这里。' : model.entries.length ? '试着调整搜索关键词或筛选条件。' : '新增第一条规则，或导入已有资料。';
  $('empty-new-entry').hidden = model.category === 'trash';
  $('result-summary').textContent = `显示 ${entries.length} 条 · 资料库共 ${activeEntries().length} 条记忆`;
  renderSelection();
}

function renderSelection() {
  const entries = visibleEntries();
  const count = model.selected.size;
  $('bulk-bar').hidden = count === 0;
  $('selection-count').textContent = `已选 ${count} 项`;
  $('select-all').checked = entries.length > 0 && count === entries.length;
  $('select-all').indeterminate = count > 0 && count < entries.length;
  const trash = model.category === 'trash';
  ['bulk-enable', 'bulk-disable', 'bulk-category', 'bulk-move', 'bulk-delete'].forEach((id) => { $(id).hidden = trash; });
  $('bulk-restore').hidden = !trash;
  document.querySelectorAll('#entry-list input[type="checkbox"]').forEach((checkbox, index) => { checkbox.checked = model.selected.has(entries[index].id); });
}

async function refresh() {
  const state = await api('/api/state');
  if (!state || !Array.isArray(state.categories) || !Array.isArray(state.entries)) throw new Error('本地服务返回了无效的资料库格式。');
  model.categories = state.categories;
  model.entries = state.entries;
  model.revision = state.revision;
  model.loaded = true;
  if (!['all', 'trash'].includes(model.category) && !model.categories.some((category) => category.id === model.category)) model.category = 'all';
  fillCategorySelect($('bulk-category'), '移动到分类…');
  fillCategorySelect($('entry-category'), null);
  renderSidebar();
  renderList();
  $('storage-status').textContent = `本地资料库已连接${state.revision !== undefined ? ` · r${state.revision}` : ''}`;
}

function confirmDiscard() {
  if (model.busy) { notice('操作正在进行，请稍候。'); return false; }
  return !model.dirty || window.confirm('有尚未保存的更改。放弃这些更改并继续？');
}

function setDirty(dirty) {
  model.dirty = dirty;
  $('save-status').textContent = dirty ? '有尚未保存的更改' : '所有更改已保存';
  $('save-status').classList.toggle('dirty', dirty);
}

function targetHint() {
  const scope = $('entry-scope').value;
  const global = scope === 'global';
  $('entry-target').disabled = global || isDeleted(currentEntry() || {});
  $('entry-target').required = !global;
  $('target-hint').textContent = global ? '全局记忆无需填写目标。' : `请填写${scope === 'project' ? '项目路径' : scope === 'skill' ? 'Skill 名称' : '插件名称'}；导出时按关联目标精确匹配。`;
}

function openEntry(entry, force = false) {
  if (!force && !confirmDiscard()) return;
  model.draft = !entry;
  model.editingId = entry?.id || null;
  $('editor-placeholder').hidden = true;
  $('editor-form').hidden = false;
  $('editor-kind').textContent = entry ? 'EDIT MEMORY' : 'NEW MEMORY';
  $('editor-heading').textContent = entry ? '编辑记忆' : '新增记忆';
  fillCategorySelect($('entry-category'), null, entry?.category_id || (model.categories.some((category) => category.id === model.category) ? model.category : model.categories[0]?.id));
  $('entry-title').value = entry?.title || '';
  $('entry-content').value = entry?.content || '';
  $('entry-scope').value = entry?.scope || 'global';
  $('entry-target').value = entry?.target || '';
  $('entry-priority').value = entry?.priority ?? 50;
  $('entry-tags').value = Array.isArray(entry?.tags) ? entry.tags.join(', ') : '';
  $('entry-enabled').checked = entry ? Boolean(entry.enabled) : true;
  const deleted = entry ? isDeleted(entry) : false;
  $('deleted-banner').hidden = !deleted;
  $('save-entry').hidden = deleted;
  $('delete-entry').hidden = !entry || deleted;
  $('restore-entry').hidden = !deleted;
  $('history-button').hidden = !entry;
  document.querySelectorAll('.editor-fields input, .editor-fields textarea, .editor-fields select').forEach((input) => { input.disabled = deleted; });
  $('entry-meta').textContent = entry ? `创建于 ${dateTime(entry.created_at)} · 更新于 ${dateTime(entry.updated_at)}${entry.version ? ` · 版本 ${entry.version}` : ''}` : '保存后会记录版本，后续修改可查看与恢复。';
  targetHint();
  setDirty(false);
  updateWriteState();
  renderList();
  if (!entry) $('entry-title').focus();
}

function closeEditor(force = false) {
  if (!force && !confirmDiscard()) return false;
  model.editingId = null;
  model.draft = false;
  setDirty(false);
  $('editor-form').hidden = true;
  $('editor-placeholder').hidden = false;
  renderList();
  return true;
}

function entryPayload() {
  return {
    title: $('entry-title').value.trim(), content: $('entry-content').value.trim(),
    category_id: $('entry-category').value, scope: $('entry-scope').value,
    target: $('entry-scope').value === 'global' ? '' : $('entry-target').value.trim(),
    priority: Number($('entry-priority').value), enabled: $('entry-enabled').checked,
    tags: [...new Set($('entry-tags').value.split(/[,，]/).map((tag) => tag.trim()).filter(Boolean))],
  };
}

function saveEntry(event) {
  event.preventDefault();
  const payload = entryPayload();
  if (!payload.title || !payload.content) return notice('标题和规则正文不能为空。', true);
  if (!payload.category_id) return notice('请先新增分类，再保存记忆。', true);
  if (payload.scope !== 'global' && !payload.target) return notice('请填写项目、Skill 或插件的关联目标。', true);
  mutate(async () => {
    const previousId = model.editingId;
    const result = await api(previousId ? `/api/entries/${encodeURIComponent(previousId)}` : '/api/entries', { method: previousId ? 'PATCH' : 'POST', body: payload });
    setDirty(false);
    await refresh();
    const savedId = result?.id || result?.entry?.id || previousId;
    const saved = model.entries.find((entry) => entry.id === savedId);
    if (saved) openEntry(saved, true); else closeEditor(true);
    notice('记忆已保存。');
  });
}

function descendants(id) {
  const ids = new Set([id]);
  let changed = true;
  while (changed) {
    changed = false;
    model.categories.forEach((category) => {
      if (ids.has(category.parent_id) && !ids.has(category.id)) { ids.add(category.id); changed = true; }
    });
  }
  return ids;
}

function openCategory(id = null) {
  const category = model.categories.find((item) => item.id === id);
  model.categoryEditingId = category?.id || null;
  $('category-dialog-title').textContent = category ? '编辑分类' : '新增分类';
  $('category-name').value = category?.name || '';
  fillCategorySelect($('category-parent'), '无上级分类', category?.parent_id || '', category ? descendants(category.id) : new Set());
  $('category-dialog').showModal();
  $('category-name').focus();
}

function saveCategory(event) {
  event.preventDefault();
  const name = $('category-name').value.trim();
  if (!name) return notice('分类名称不能为空。', true);
  mutate(async () => {
    const id = model.categoryEditingId;
    const result = await api(id ? `/api/categories/${encodeURIComponent(id)}` : '/api/categories', { method: id ? 'PATCH' : 'POST', body: { name, parent_id: $('category-parent').value || null } });
    $('category-dialog').close();
    if (result?.id) model.category = result.id;
    await refresh();
    notice('分类已保存。');
  });
}

function deleteCategory() {
  const category = model.categories.find((item) => item.id === model.category);
  if (!category || !window.confirm(`删除分类“${category.name}”？请先移走该分类的记忆与子分类。`)) return;
  mutate(async () => {
    await api(`/api/categories/${encodeURIComponent(category.id)}`, { method: 'DELETE', body: {} });
    model.category = 'all';
    await refresh();
    notice('分类已删除。');
  });
}

function bulk(action, ids = [...model.selected]) {
  if (!ids.length) return;
  const categoryId = $('bulk-category').value;
  if (action === 'move' && !categoryId) return notice('请先选择要移动到的分类。', true);
  if (model.dirty && ids.includes(model.editingId) && !confirmDiscard()) return;
  if (action === 'delete' && !window.confirm(`将 ${ids.length} 条记忆移入回收站？之后可以恢复。`)) return;
  mutate(async () => {
    await api('/api/bulk', { method: 'POST', body: { ids, action, ...(action === 'move' ? { category_id: categoryId } : {}) } });
    model.selected.clear();
    const changedCurrent = ids.includes(model.editingId);
    await refresh();
    if (changedCurrent) {
      const entry = currentEntry();
      if (entry) openEntry(entry, true); else closeEditor(true);
    }
    notice(({ enable: '所选记忆已启用。', disable: '所选记忆已停用。', move: '所选记忆已移动。', delete: '记忆已移入回收站。', restore: '记忆已恢复。' })[action] || '操作已完成。');
  });
}

function download(content, filename, type) {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const link = node('a');
  link.href = url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function exportJSON() {
  run(async () => {
    const data = await api('/api/export');
    download(JSON.stringify(data, null, 2) + '\n', `codex-memory-${new Date().toISOString().slice(0, 10)}.json`, 'application/json;charset=utf-8');
    notice('JSON 备份已导出。');
  });
}

function importJSON(event) {
  const file = event.target.files[0];
  event.target.value = '';
  if (!file || !confirmDiscard()) return;
  if (file.size > 10 * 1024 * 1024) return notice('文件超过 10 MB，请检查是否选择了正确的 JSON 备份。', true);
  mutate(async () => {
    let payload;
    try { payload = JSON.parse(await file.text()); }
    catch { throw new Error('无法读取 JSON，请选择有效的记忆备份文件。'); }
    const result = await api('/api/import', { method: 'POST', body: payload });
    closeEditor(true);
    await refresh();
    notice(result?.message || 'JSON 已导入，资料库已刷新。');
  });
}

function renderTargetHint() {
  const global = $('render-scope').value === 'global';
  $('render-target').disabled = global;
  $('render-target').required = !global;
  if (global) $('render-target').value = '';
  invalidatePreview();
}

function invalidatePreview() {
  model.markdown = '';
  $('render-preview').value = '';
  $('render-summary').textContent = '请生成预览 · 仅包含所选范围与目标的启用记忆';
  $('copy-markdown').disabled = true;
  $('download-markdown').disabled = true;
}

function openRender() {
  const entry = currentEntry();
  $('render-scope').value = entry?.scope || $('scope-filter').value || 'global';
  $('render-target').value = entry?.target || '';
  renderTargetHint();
  $('render-dialog').showModal();
}

function renderMarkdown(event) {
  event.preventDefault();
  run(async () => {
    const scope = $('render-scope').value;
    const target = scope === 'global' ? '' : $('render-target').value.trim();
    const result = await api('/api/render', { method: 'POST', body: { scope, target } });
    if (scope !== $('render-scope').value || target !== (scope === 'global' ? '' : $('render-target').value.trim())) return;
    model.markdown = result.markdown || '';
    model.markdownScope = scope;
    $('render-preview').value = model.markdown;
    $('render-summary').textContent = `${scopeNames[scope]}${target ? ` · ${target}` : ''} · ${result.count ?? 0} 条启用记忆`;
    $('copy-markdown').disabled = !model.markdown;
    $('download-markdown').disabled = !model.markdown;
  });
}

async function showHistory() {
  const entry = currentEntry();
  if (!entry) return;
  model.historyEntryId = entry.id;
  model.historyVersion = null;
  $('history-entry-title').textContent = entry.title;
  $('history-list').replaceChildren(node('p', 'field-hint', '正在读取历史版本…'));
  $('history-preview').textContent = '选择一个版本查看内容。';
  $('history-status').textContent = '恢复后将生成一个新的当前版本。';
  $('restore-version').disabled = true;
  $('history-dialog').showModal();
  await run(async () => {
    let result;
    try { result = await api(`/api/entries/${encodeURIComponent(entry.id)}/history`); }
    catch (error) {
      $('history-list').replaceChildren(node('p', 'field-hint', '无法读取历史记录，请关闭后重试。'));
      throw error;
    }
    if (model.historyEntryId !== entry.id) return;
    model.history = result.history || [];
    const fragment = document.createDocumentFragment();
    model.history.forEach((version, index) => {
      const button = node('button', 'history-item', `版本 ${version.version ?? model.history.length - index}`);
      button.append(node('small', '', dateTime(version.saved_at || version.created_at)));
      button.addEventListener('click', () => {
        model.historyVersion = version.version_id || version.id;
        document.querySelectorAll('.history-item').forEach((item) => item.classList.toggle('active', item === button));
        const snapshot = version.entry || version.snapshot || {};
        $('history-preview').textContent = [snapshot.title || '无标题', '', snapshot.content || '', '', `分类：${categoryName(snapshot.category_id)}`, `范围：${scopeNames[snapshot.scope] || snapshot.scope || '全局'}`, `目标：${snapshot.target || '—'}`, `标签：${Array.isArray(snapshot.tags) ? snapshot.tags.join(', ') : ''}`, `优先级：${snapshot.priority ?? 50}`, `状态：${snapshot.enabled ? '启用' : '停用'}`].join('\n');
        updateWriteState();
      });
      fragment.append(button);
    });
    if (!model.history.length) fragment.append(node('p', 'field-hint', '暂无历史版本。'));
    $('history-list').replaceChildren(fragment);
  });
}

function restoreVersion() {
  if (!model.historyVersion || !confirmDiscard()) return;
  if (!window.confirm('将这条记忆恢复到所选历史版本？当前版本仍保留在历史记录中。')) return;
  mutate(async () => {
    await api(`/api/entries/${encodeURIComponent(model.historyEntryId)}/restore`, { method: 'POST', body: { version_id: model.historyVersion } });
    $('history-dialog').close();
    setDirty(false);
    await refresh();
    const entry = model.entries.find((item) => item.id === model.historyEntryId);
    if (entry) openEntry(entry, true);
    notice('已恢复所选历史版本。');
  });
}

$('new-entry').addEventListener('click', () => openEntry());
$('empty-new-entry').addEventListener('click', () => openEntry());
$('placeholder-new-entry').addEventListener('click', () => openEntry());
$('close-editor').addEventListener('click', () => closeEditor());
$('editor-form').addEventListener('submit', saveEntry);
$('editor-form').addEventListener('input', (event) => {
  if (event.target.matches('input, textarea, select')) setDirty(true);
});
$('editor-form').addEventListener('change', (event) => {
  if (event.target.matches('input, textarea, select')) setDirty(true);
});
$('entry-scope').addEventListener('change', targetHint);
['search', 'scope-filter', 'status-filter'].forEach((id) => $(id).addEventListener(id === 'search' ? 'input' : 'change', () => { model.selected.clear(); renderList(); }));
$('select-all').addEventListener('change', () => { model.selected = new Set($('select-all').checked ? visibleEntries().map((entry) => entry.id) : []); renderSelection(); });
$('clear-selection').addEventListener('click', () => { model.selected.clear(); renderSelection(); });
['enable', 'disable', 'move', 'delete', 'restore'].forEach((action) => $(`bulk-${action}`).addEventListener('click', () => bulk(action)));
$('delete-entry').addEventListener('click', () => bulk('delete', model.editingId ? [model.editingId] : []));
$('restore-entry').addEventListener('click', () => bulk('restore', model.editingId ? [model.editingId] : []));
$('add-category').addEventListener('click', () => openCategory());
$('edit-category').addEventListener('click', () => openCategory(model.category));
$('delete-category').addEventListener('click', deleteCategory);
$('category-form').addEventListener('submit', saveCategory);
$('export-button').addEventListener('click', exportJSON);
$('import-button').addEventListener('click', () => $('import-file').click());
$('import-file').addEventListener('change', importJSON);
$('render-button').addEventListener('click', openRender);
$('render-scope').addEventListener('change', renderTargetHint);
$('render-target').addEventListener('input', invalidatePreview);
$('render-form').addEventListener('submit', renderMarkdown);
$('download-markdown').addEventListener('click', () => download(model.markdown, model.markdownScope === 'skill' ? 'SKILL.rules.md' : 'AGENTS.rules.md', 'text/markdown;charset=utf-8'));
$('copy-markdown').addEventListener('click', () => run(async () => {
  if (!navigator.clipboard?.writeText) {
    $('render-preview').focus(); $('render-preview').select();
    notice('已选中规则，请按 Ctrl+C 或 Command+C 复制。');
    return;
  }
  await navigator.clipboard.writeText(model.markdown);
  notice('规则内容已复制。');
}));
$('history-button').addEventListener('click', showHistory);
$('restore-version').addEventListener('click', restoreVersion);
document.querySelectorAll('[data-close-dialog]').forEach((button) => button.addEventListener('click', () => $(button.dataset.closeDialog).close()));
document.querySelectorAll('dialog').forEach((dialog) => dialog.addEventListener('close', () => dialog.querySelector('.dialog-notice')?.remove()));
window.addEventListener('beforeunload', (event) => {
  if (!model.dirty) return;
  event.preventDefault();
  event.returnValue = '';
});

run(async () => {
  try { await refresh(); }
  catch (error) {
    $('storage-status').textContent = '连接失败，请检查本地服务';
    $('result-summary').textContent = '资料库尚未加载';
    $('list-empty').hidden = false;
    $('empty-title').textContent = '无法读取资料库';
    $('empty-description').textContent = '请确认本地服务正常运行，然后刷新页面。';
    $('empty-new-entry').hidden = true;
    throw error;
  }
});
