function toggleSelect(id) {
  if (state.selectedIds.has(id)) {
    state.selectedIds.delete(id);
  } else {
    state.selectedIds.add(id);
  }
  logUiAction('item_select', {id, selected: state.selectedIds.has(id), selected_count: state.selectedIds.size});
  updateEditBar();
  $$('#grid .card').forEach(card => {
    const cid = parseInt(card.dataset.id);
    if (cid === id) {
      card.classList.toggle('selected', state.selectedIds.has(cid));
      const chk = card.querySelector('.check');
      if (chk) chk.classList.toggle('checked', state.selectedIds.has(cid));
    }
  });
}

function updateEditBar() {
  const bar = $('#editBar');
  if (state.mode === 'edit') {
    bar.classList.add('visible');
    if (state.selectedIds.size > 0) {
      $('#selectedCount').textContent = `已选 ${state.selectedIds.size} 张`;
    } else if (state.activeFolder) {
      $('#selectedCount').textContent = `当前文件夹：${state.activeFolder}`;
    } else {
      $('#selectedCount').textContent = '已选 0 张';
    }
  } else {
    bar.classList.remove('visible');
  }
  renderEditTagPicker();
  ensureEditTagContext();
}

function tagMatchesEditQuery(tag, query) {
  if (!query) return true;
  return (tag.name || '').toLowerCase().includes(query.toLowerCase());
}

function tagNameKey(name) {
  return (name || '').trim().toLowerCase();
}

function selectedEditArtistIds() {
  const artistIds = new Set();
  (state.allItems || []).forEach(item => {
    if (state.selectedIds.has(item.id) && isTaggableItem(item)) {
      artistIds.add(Number(item.artist_id));
    }
  });
  if (artistIds.size === 0 && state.currentArtist) {
    artistIds.add(Number(state.currentArtist.id));
  }
  return [...artistIds].filter(Boolean).sort((a, b) => a - b);
}

function currentEditArtistId() {
  const ids = selectedEditArtistIds();
  if (ids.length === 1) return ids[0];
  return state.editContextArtistId || (state.currentArtist ? state.currentArtist.id : null);
}

function mergeTagsByName(tagGroups) {
  const byName = new Map();
  tagGroups.flat().forEach(tag => {
    const key = tagNameKey(tag.name);
    if (!key) return;
    const countKey = tag.id != null ? `tag:${tag.id}` : `name:${key}`;
    const countedTagIds = tag.countedTagIds instanceof Set ? tag.countedTagIds : new Set([countKey]);
    const artistIds = Array.isArray(tag.artist_ids) ? tag.artist_ids : [tag.artist_id];
    const existing = byName.get(key);
    if (existing) {
      if (!existing.countedTagIds.has(countKey)) {
        const alreadyCounted = [...countedTagIds].some(id => existing.countedTagIds.has(id));
        if (!alreadyCounted) existing.item_count += tag.item_count || 0;
        existing.countedTagIds.add(countKey);
        countedTagIds.forEach(id => existing.countedTagIds.add(id));
      }
      artistIds.filter(id => id != null).forEach(id => {
        const artistId = Number(id);
        if (!existing.artist_ids.includes(artistId)) existing.artist_ids.push(artistId);
      });
    } else {
      byName.set(key, {
        ...tag,
        name: tag.name,
        item_count: tag.item_count || 0,
        artist_ids: artistIds.filter(id => id != null).map(id => Number(id)),
        countedTagIds: new Set(countedTagIds),
      });
    }
  });
  return [...byName.values()].sort((a, b) => (a.name || '').localeCompare(b.name || ''));
}

function editAvailableTags() {
  return mergeTagsByName([
    state.tags || [],
    state.editGlobalTagResults || [],
  ]);
}

async function loadGlobalEditTagResults(query = '') {
  const clean = (query || '').trim();
  const token = ++editGlobalTagSearchToken;

  state.editGlobalTagSearchLoading = true;
  logUiAction('edit_tag_search', {
    search: clean,
    scope: state.searchScope,
    selected_count: state.selectedIds.size,
    artist_ids: selectedEditArtistIds(),
  });
  renderEditTagPicker();
  try {
    const params = new URLSearchParams({limit: 100});
    if (clean) params.set('search', clean);
    const data = await API.get('/api/tags/search?' + params.toString());
    if (token !== editGlobalTagSearchToken) return;
    state.editGlobalTagResults = (data.tags || []).map(tag => ({...tag, global: true}));
  } catch (e) {
    if (token === editGlobalTagSearchToken) state.editGlobalTagResults = [];
  } finally {
    if (token === editGlobalTagSearchToken) {
      state.editGlobalTagSearchLoading = false;
      renderEditTagPicker();
    }
  }
}

function editTagOptions(query = '') {
  return editAvailableTags().filter(tag => tagMatchesEditQuery(tag, query));
}

async function ensureEditTagContext() {
  const artistIds = selectedEditArtistIds();
  const contextKey = artistIds.join(',');
  state.editContextArtistId = artistIds.length === 1 ? artistIds[0] : null;
  if (!artistIds.length) {
    editTagContextLoadToken++;
    state.editTagContextLoading = false;
    state.editContextKey = '';
    renderEditTagPicker();
    return;
  }
  if (
    state.currentArtist &&
    artistIds.length === 1 &&
    artistIds[0] === Number(state.currentArtist.id) &&
    state.editContextKey === contextKey &&
    (state.tags || []).length
  ) {
    state.editTagContextLoading = false;
    pruneSelectedEditTags();
    renderEditTagPicker();
    return;
  }

  const token = ++editTagContextLoadToken;
  state.editTagContextLoading = true;
  renderEditTagPicker();
  try {
    const groups = await Promise.all(
      artistIds.map(id => API.get(`/api/tags?artist_id=${id}`).catch(() => []))
    );
    if (token !== editTagContextLoadToken) return;
    state.tags = mergeTagsByName(groups);
    state.editContextKey = contextKey;
    pruneSelectedEditTags();
    logUiAction('edit_tag_context_loaded', {
      artist_ids: artistIds,
      tag_count: state.tags.length,
      selected_count: state.selectedIds.size,
    });
  } finally {
    if (token === editTagContextLoadToken) {
      state.editTagContextLoading = false;
      renderEditTagPicker();
    }
  }
}

function pruneSelectedEditTags() {
  const existing = new Set(editAvailableTags().map(tag => Number(tag.id)));
  state.selectedEditTagIds = new Set([...state.selectedEditTagIds].filter(id => existing.has(Number(id))));
  state.selectedEditTagNames = new Set(
    [...state.selectedEditTagNames].filter(name => tagNameKey(name))
  );
}

function exactEditTagMatch(query) {
  const q = (query || '').trim().toLowerCase();
  if (!q) return null;
  return editAvailableTags().find(tag => (tag.name || '').trim().toLowerCase() === q) || null;
}

function selectedEditTagNameKeys() {
  const names = selectedEditTagNames();
  return new Set(names.map(tagNameKey));
}

function renderEditTagPicker() {
  const panel = $('#editTagPickerPanel');
  if (!panel) return;
  if (state.editTagContextLoading) {
    panel.innerHTML = '<div class="tag-picker-empty">标签加载中...</div>';
    updateEditTagPickerSummary();
    return;
  }
  pruneSelectedEditTags();
  const query = (state.editTagQuery || '').trim();
  const tags = editTagOptions(query);
  const selectedNameKeys = selectedEditTagNameKeys();
  const rows = tags.map(tag => {
    const selected = state.selectedEditTagIds.has(Number(tag.id)) || selectedNameKeys.has(tagNameKey(tag.name));
    const globalAttr = tag.global ? ` data-global-tag-id="${tag.id}"` : '';
    return `
      <button class="tag-picker-option${selected ? ' selected' : ''}" type="button" data-tag-id="${tag.id}"${globalAttr} data-tag-name="${escHtml(tag.name)}" aria-pressed="${selected ? 'true' : 'false'}">
        <span>${escHtml(tag.name)}</span>
        <em>${tag.item_count || 0}</em>
      </button>
    `;
  });
  if (state.editGlobalTagSearchLoading) {
    rows.push('<div class="tag-picker-empty">正在搜索全局标签...</div>');
  }
  if (query && !state.editGlobalTagSearchLoading && !exactEditTagMatch(query)) {
    rows.push(`
      <button class="tag-picker-option tag-picker-create" type="button" data-create-tag="${escHtml(query)}">
        <span>创建标签：${escHtml(query)}</span>
      </button>
    `);
  }
  const emptyText = query
    ? '没有匹配的全局标签，回车可创建'
    : '输入标签名搜索全局已有标签';
  panel.innerHTML = rows.join('') || `<div class="tag-picker-empty">${emptyText}</div>`;
  $$('#editTagPickerPanel [data-tag-id]').forEach(btn => {
    btn.addEventListener('click', () => toggleEditTagSelection(Number(btn.dataset.tagId), btn.dataset.tagName || ''));
  });
  $$('#editTagPickerPanel [data-create-tag]').forEach(btn => {
    btn.addEventListener('click', () => createOrSelectEditTag(btn.dataset.createTag || ''));
  });
  updateEditTagPickerSummary();
}

function updateEditTagPickerSummary() {
  const count = selectedEditTagNames().length;
  const summary = $('#editTagPickerSummary');
  if (summary) summary.textContent = count ? `已选 ${count} 个标签` : '未选标签';
  const input = $('#editTagSearch');
  if (input) input.placeholder = count ? '继续搜索标签...' : '选择/搜索标签...';
}

function clearEditTagQuery() {
  state.editTagQuery = '';
  const input = $('#editTagSearch');
  if (input) input.value = '';
}

function openEditTagPicker() {
  $('#editTagPicker').classList.add('open');
  renderEditTagPicker();
  ensureEditTagContext();
  loadGlobalEditTagResults(state.editTagQuery);
}

function closeEditTagPicker() {
  $('#editTagPicker').classList.remove('open');
  clearEditTagQuery();
  renderEditTagPicker();
}

function setSelectedEditTagName(name, selected) {
  const key = tagNameKey(name);
  if (!key) return;
  const retained = [...state.selectedEditTagNames].filter(existing => tagNameKey(existing) !== key);
  if (selected) retained.push(name.trim());
  state.selectedEditTagNames = new Set(retained);
}

function removeSelectedEditTag(tagId, tagName = '') {
  const id = Number(tagId);
  const tag = editAvailableTags().find(t => Number(t.id) === id);
  const name = tagName || tag?.name || '';
  state.selectedEditTagIds.delete(id);
  setSelectedEditTagName(name, false);
}

function selectEditTag(tagId, tagName = '') {
  const tag = editAvailableTags().find(t => Number(t.id) === Number(tagId));
  const name = tagName || tag?.name || '';
  state.selectedEditTagIds.add(Number(tagId));
  setSelectedEditTagName(name, true);
  logUiAction('edit_tag_select', {
    name,
    selected: true,
    global: Boolean(tag?.global),
    selected_count: state.selectedIds.size,
    artist_ids: selectedEditArtistIds(),
  });
  clearEditTagQuery();
  openEditTagPicker();
}

function toggleEditTagSelection(tagId, tagName = '') {
  const id = Number(tagId);
  const tag = editAvailableTags().find(t => Number(t.id) === id);
  const name = tagName || tag?.name || '';
  const selected = state.selectedEditTagIds.has(id) || selectedEditTagNameKeys().has(tagNameKey(name));
  if (selected) {
    removeSelectedEditTag(id, name);
  } else {
    state.selectedEditTagIds.add(id);
    setSelectedEditTagName(name, true);
  }
  logUiAction('edit_tag_select', {
    name,
    selected: !selected,
    global: Boolean(tag?.global),
    selected_count: state.selectedIds.size,
    artist_ids: selectedEditArtistIds(),
  });
  renderEditTagPicker();
  ensureEditTagContext();
}

function selectFirstEditTagResult() {
  const query = (state.editTagQuery || '').trim();
  const exact = exactEditTagMatch(query);
  if (exact) {
    selectEditTag(exact.id, exact.name);
    return;
  }
  const first = $('#editTagPickerPanel [data-tag-id]');
  if (first) {
    selectEditTag(Number(first.dataset.tagId), first.dataset.tagName || '');
    return;
  }
  if (query) createOrSelectEditTag(query);
}

function addVirtualEditTag(tagName) {
  const key = tagNameKey(tagName);
  if (!key) return null;
  const existing = exactEditTagMatch(tagName);
  if (existing) {
    selectEditTag(existing.id, existing.name);
    return existing;
  }
  const virtual = {id: `name:${key}`, name: tagName.trim(), item_count: 0, virtual: true};
  state.tags = [...(state.tags || []).filter(tag => tagNameKey(tag.name) !== key), virtual];
  setSelectedEditTagName(tagName, true);
  clearEditTagQuery();
  renderEditTagPicker();
  openEditTagPicker();
  return virtual;
}

async function createOrSelectEditTag(name = '') {
  const tagName = (name || state.editTagQuery || $('#editTagSearch')?.value || '').trim();
  if (!tagName) {
    toast('请输入标签名', 'error');
    return;
  }
  const existing = exactEditTagMatch(tagName);
  if (existing) {
    selectEditTag(existing.id, existing.name);
    return existing;
  }
  const artistId = currentEditArtistId();
  if (!artistId) return addVirtualEditTag(tagName);
  try {
    const created = await API.post(`/api/tags?artist_id=${artistId}&name=${encodeURIComponent(tagName)}`);
    state.tags = await API.get(`/api/tags?artist_id=${artistId}`);
    if (created && created.id) selectEditTag(Number(created.id), created.name || tagName);
    setSelectedEditTagName(tagName, true);
    clearEditTagQuery();
    renderEditTagPicker();
    openEditTagPicker();
    toast('标签已创建', 'success');
    return created;
  } catch (e) {
    toast('创建失败', 'error');
    return null;
  }
}

async function selectOrCreateEditTagQuery() {
  const tagName = (state.editTagQuery || $('#editTagSearch')?.value || '').trim();
  if (!tagName) return null;
  return createOrSelectEditTag(tagName);
}

function selectedEditTagIds() {
  const selected = state.selectedEditTagIds;
  const selectedNames = selectedEditTagNameKeys();
  return editAvailableTags()
    .filter(tag => selected.has(Number(tag.id)) || selectedNames.has(tagNameKey(tag.name)))
    .map(tag => Number(tag.id))
    .filter(id => Number.isFinite(id));
}

function selectedEditTagNames(extraTagIds = []) {
  const names = [...state.selectedEditTagNames];
  const ids = new Set([...state.selectedEditTagIds, ...(extraTagIds || [])].map(id => Number(id)));
  editAvailableTags().forEach(tag => {
    if (ids.has(Number(tag.id)) && tag.name) names.push(tag.name);
  });
  const byKey = new Map();
  names.forEach(name => {
    const key = tagNameKey(name);
    if (key && !byKey.has(key)) byKey.set(key, name.trim());
  });
  return [...byKey.values()];
}

function clearSelectedEditTags() {
  state.selectedEditTagIds.clear();
  state.selectedEditTagNames.clear();
  renderEditTagPicker();
  updateEditTagPickerSummary();
}

async function classifyItems(ids, tagIds, mode='add') {
  if (!ids.length) return;
  if (isActionBusy('edit-classify-items')) return;
  setActionBusy('edit-classify-items', '', true);
  const artistId = currentEditArtistId();
  const tagNames = selectedEditTagNames(tagIds);
  if (!tagNames.length && !tagIds.length) {
    setActionBusy('edit-classify-items', '', false);
    return;
  }
  try {
    let result = null;
    if (tagNames.length) {
      result = await API.putJson('/api/items/tags-by-name', {item_ids: ids, tag_names: tagNames, mode});
    } else if (artistId) {
      result = await API.put(`/api/items/tags?artist_id=${artistId}&item_ids=${ids.join(',')}&tag_ids=${tagIds.join(',')}&mode=${mode}`);
    }
    logUiAction('edit_apply_result', {
      target: 'items',
      mode,
      requested_count: ids.length,
      updated: result?.updated || 0,
      artists: result?.artists || 0,
      tags: result?.tags || tagNames.length || tagIds.length,
      propagated: result?.propagated || 0,
      tag_names: tagNames,
    });
    clearSelectedEditTags();
    state.selectedIds.clear();
    updateEditBar();
    toast('标签已更新', 'success');

    if (state.currentArtist) {
      state.stats = await API.get(`/api/artists/${state.currentArtist.id}/stats`);
      state.tags = await API.get(`/api/tags?artist_id=${state.currentArtist.id}`);
      renderSidebar();
    } else {
      await ensureEditTagContext();
    }
    renderEditTagPicker();
    loadItems();
  } catch (e) {
    logUiAction('edit_apply_result', {
      target: 'items',
      mode,
      requested_count: ids.length,
      error: e.message,
    });
    toast('分类失败: ' + e.message, 'error');
  } finally {
    setActionBusy('edit-classify-items', '', false);
  }
}

async function classifyFolder(folder, tagIds, mode='add') {
  if (!state.currentArtist || !folder) return;
  if (isActionBusy('edit-classify-folder', folder)) return;
  setActionBusy('edit-classify-folder', folder, true);
  try {
    const result = await API.put(`/api/folders/tags?artist_id=${state.currentArtist.id}&folder=${encodeURIComponent(folder)}&tag_ids=${tagIds.join(',')}&mode=${mode}`);
    logUiAction('edit_apply_result', {
      target: 'folder',
      mode,
      folder,
      updated: result?.updated || 0,
      tag_ids: tagIds,
    });
    clearSelectedEditTags();
    updateEditBar();
    toast(`文件夹标签已更新：${result.updated} 张`, 'success');

    state.stats = await API.get(`/api/artists/${state.currentArtist.id}/stats`);
    state.tags = await API.get(`/api/tags?artist_id=${state.currentArtist.id}`);
    state.folders = await API.get(`/api/folders?artist_id=${state.currentArtist.id}`);
    renderEditTagPicker();
    renderSidebar();
    renderFolderTree();
    loadItems();
  } catch (e) {
    logUiAction('edit_apply_result', {
      target: 'folder',
      mode,
      folder,
      error: e.message,
    });
    toast('文件夹分类失败: ' + e.message, 'error');
  } finally {
    setActionBusy('edit-classify-folder', folder, false);
  }
}

async function deleteSelectedTags() {
  if (!state.currentArtist) return;
  if (isActionBusy('edit-delete-tags')) return;
  const tagIds = selectedEditTagIds();
  if (tagIds.length === 0) {
    toast('请选择要删除的标签', 'error');
    return;
  }
  if (!confirm(`确认删除 ${tagIds.length} 个标签？`)) return;
  setActionBusy('edit-delete-tags', '', true);
  const deletedTagNamesById = new Map(
    tagIds.map(tagId => {
      const tag = editAvailableTags().find(item => Number(item.id) === Number(tagId));
      return [Number(tagId), tag?.name || ''];
    })
  );

  try {
    for (const tagId of tagIds) {
      await API.del(`/api/tags/${tagId}?artist_id=${state.currentArtist.id}`);
    }
    tagIds.forEach(tagId => removeSelectedEditTag(tagId, deletedTagNamesById.get(Number(tagId)) || ''));
    state.selectedIds.clear();
    state.stats = await API.get(`/api/artists/${state.currentArtist.id}/stats`);
    state.tags = await API.get(`/api/tags?artist_id=${state.currentArtist.id}`);
    state.folders = await API.get(`/api/folders?artist_id=${state.currentArtist.id}`);
    renderSidebar();
    renderFolderTree();
    renderEditTagPicker();
    loadItems();
    toast(`已删除 ${tagIds.length} 个标签`, 'success');
  } catch (e) {
    toast('删除标签失败: ' + e.message, 'error');
  } finally {
    setActionBusy('edit-delete-tags', '', false);
  }
}
