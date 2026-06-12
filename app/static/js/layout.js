function renderLibraryEmptyState() {
  const panel = $('#libraryEmptyState');
  if (!panel) return;
  const isLibraryEmpty = !state.currentArtist && state.artists.length === 0;
  panel.style.display = isLibraryEmpty ? '' : 'none';
  if (!isLibraryEmpty) return;

  const scanState = state.lastScanState || {};
  const scanButton = $('#emptyScanBtn');
  const isScanning = state.scanRunning || scanState.status === 'scanning';
  panel.classList.toggle('scanning', isScanning);
  scanButton.disabled = isScanning;
  scanButton.textContent = isScanning ? '扫描中' : '开始全量扫描';

  if (isScanning) {
    $('#libraryEmptyKicker').textContent = '正在重建';
    $('#libraryEmptyTitle').textContent = '正在重建画廊档案';
    $('#libraryEmptyText').textContent = scanState.current_path || '正在扫描挂载目录，发现的画师会自动出现在左上角选择器。';
    $('#libraryEmptyMeta').textContent = scanState.total_estimate > 0
      ? `${scanState.scanned_count || 0} / ${scanState.total_estimate}`
      : '顶部进度条会持续更新当前扫描阶段。';
    return;
  }

  if (scanState.status === 'idle' && scanState.phase === 'complete') {
    $('#libraryEmptyKicker').textContent = '扫描完成';
    $('#libraryEmptyTitle').textContent = '没有发现媒体内容';
    $('#libraryEmptyText').textContent = '请确认 Docker 挂载的图片目录存在，并且目录里有可识别的图片、视频或压缩包。';
    $('#libraryEmptyMeta').textContent = '可以重新扫描，或先检查 compose 里的 /pictures1、/pictures2 挂载。';
    return;
  }

  $('#libraryEmptyKicker').textContent = '画廊档案';
  $('#libraryEmptyTitle').textContent = '画廊档案是空的';
  $('#libraryEmptyText').textContent = '从挂载的图片目录开始扫描，重建画师、文件夹和媒体索引。';
  $('#libraryEmptyMeta').textContent = '扫描会在后台运行，可以随时查看顶部进度。';
}

function isMobileViewport() {
  return window.matchMedia('(max-width:768px)').matches;
}

function syncFilterDrawer() {
  document.body.classList.toggle('filter-drawer-open', state.filterDrawerOpen);
  const backdrop = $('#filterBackdrop');
  if (backdrop) backdrop.hidden = !state.filterDrawerOpen;
  const btn = $('#mobileFilterBtn');
  if (btn) btn.setAttribute('aria-expanded', state.filterDrawerOpen ? 'true' : 'false');
}

function openFilterDrawer() {
  state.filterDrawerOpen = true;
  syncFilterDrawer();
}

function closeFilterDrawer() {
  state.filterDrawerOpen = false;
  syncFilterDrawer();
}

function closeFilterDrawerIfMobile() {
  if (isMobileViewport()) closeFilterDrawer();
}

function validSearchScope(scope) {
  return ['auto', 'artist', 'folder', 'global'].includes(scope) ? scope : 'auto';
}

function effectiveSearchScope() {
  const scope = validSearchScope(state.searchScope);
  if (scope !== 'auto') return scope;
  if (state.activeFolder) return 'folder';
  if (state.currentArtist) return 'artist';
  return 'global';
}

function normalizeSearchScope() {
  state.searchScope = validSearchScope(state.searchScope);
  if (state.searchScope === 'artist' && !state.currentArtist) state.searchScope = 'auto';
  if (state.searchScope === 'folder' && !state.activeFolder) state.searchScope = 'auto';
}

function searchOptionsLabel() {
  const labels = {auto: '自动', artist: '画师', folder: '文件夹', global: '全局'};
  const scope = validSearchScope(state.searchScope);
  const tagsOnly = state.searchTarget === 'tags';
  if (scope === 'auto' && tagsOnly) return '仅标签';
  if (scope === 'auto') return '自动';
  return tagsOnly ? `${labels[scope]}/标签` : labels[scope];
}

function syncSearchOptionsControl() {
  normalizeSearchScope();
  const input = $('#searchInput');
  if (input) input.placeholder = state.searchTarget === 'tags' ? '搜索标签...' : '搜索标签/文件名...';

  const btn = $('#searchOptionsBtn');
  if (btn) {
    btn.textContent = searchOptionsLabel();
    btn.classList.toggle('active', state.searchScope !== 'auto' || state.searchTarget === 'tags');
    btn.setAttribute('aria-expanded', state.searchOptionsOpen ? 'true' : 'false');
  }

  const menu = $('#searchOptionsMenu');
  if (menu) menu.hidden = !state.searchOptionsOpen;

  $$('#searchOptionsMenu [data-search-scope]').forEach(scopeBtn => {
    const scope = scopeBtn.dataset.searchScope;
    const unavailable = (scope === 'artist' && !state.currentArtist) || (scope === 'folder' && !state.activeFolder);
    scopeBtn.classList.toggle('active', state.searchScope === scope);
    scopeBtn.disabled = unavailable;
  });

  const tagsOnly = $('#tagsOnlyToggle');
  if (tagsOnly) tagsOnly.checked = state.searchTarget === 'tags';
}

function openSearchOptions() {
  state.searchOptionsOpen = true;
  syncSearchOptionsControl();
}

function closeSearchOptions() {
  state.searchOptionsOpen = false;
  syncSearchOptionsControl();
}

function toggleSearchOptions() {
  state.searchOptionsOpen ? closeSearchOptions() : openSearchOptions();
}

function setSearchScope(scope) {
  state.searchScope = validSearchScope(scope);
  syncSearchOptionsControl();
}

function setSearchTarget(target) {
  state.searchTarget = target === 'tags' ? 'tags' : 'all';
  syncSearchOptionsControl();
}

function syncMobileHeaderTools() {
  const header = $('#appHeader');
  if (!header) return;
  header.classList.toggle('mobile-tools-open', state.mobileHeaderToolsOpen);
  header.classList.toggle('mobile-tools-collapsed', !state.mobileHeaderToolsOpen);
  const btn = $('#mobileHeaderToggle');
  if (btn) {
    btn.setAttribute('aria-expanded', state.mobileHeaderToolsOpen ? 'true' : 'false');
    btn.setAttribute('aria-label', state.mobileHeaderToolsOpen ? '收起搜索扫描工具' : '展开搜索扫描工具');
    btn.textContent = state.mobileHeaderToolsOpen ? '收起' : '工具';
  }
}

function toggleMobileHeaderTools() {
  state.mobileHeaderToolsOpen = !state.mobileHeaderToolsOpen;
  syncMobileHeaderTools();
}

function closeMobileHeaderTools() {
  state.mobileHeaderToolsOpen = false;
  syncMobileHeaderTools();
}

function closeMobileHeaderToolsIfMobile() {
  if (!isMobileViewport()) return;
  closeMobileHeaderTools();
}

function sidebarViewportMaxWidth() {
  return Math.max(SIDEBAR_WIDTH_MIN, Math.min(SIDEBAR_WIDTH_MAX, Math.floor(window.innerWidth * 0.45)));
}

function normalizeSidebarWidth(width) {
  const parsed = Number(width);
  if (!Number.isFinite(parsed)) return SIDEBAR_WIDTH_DEFAULT;
  return Math.max(SIDEBAR_WIDTH_MIN, Math.min(SIDEBAR_WIDTH_MAX, Math.round(parsed)));
}

function setSidebarWidth(width, persist = false) {
  const desired = normalizeSidebarWidth(width);
  state.sidebarWidth = desired;
  const applied = Math.min(desired, sidebarViewportMaxWidth());
  document.documentElement.style.setProperty('--sidebar-width', `${applied}px`);
  if (persist) {
    try { localStorage.setItem(SIDEBAR_WIDTH_STORAGE_KEY, String(desired)); } catch (e) {}
  }
}

function loadSidebarWidth() {
  let saved = null;
  try { saved = localStorage.getItem(SIDEBAR_WIDTH_STORAGE_KEY); } catch (e) {}
  setSidebarWidth(saved || SIDEBAR_WIDTH_DEFAULT, false);
}

function bindSidebarResize() {
  const handle = $('#sidebarResizer');
  if (!handle) return;
  let startX = 0;
  let startWidth = SIDEBAR_WIDTH_DEFAULT;

  const stopResize = e => {
    document.body.classList.remove('sidebar-resizing');
    window.removeEventListener('pointermove', resizeSidebar);
    window.removeEventListener('pointerup', stopResize);
    window.removeEventListener('pointercancel', stopResize);
    if (e && e.pointerId != null && handle.releasePointerCapture) {
      try { handle.releasePointerCapture(e.pointerId); } catch (err) {}
    }
    setSidebarWidth(state.sidebarWidth, true);
  };

  const resizeSidebar = e => {
    setSidebarWidth(startWidth + e.clientX - startX, false);
  };

  handle.addEventListener('pointerdown', e => {
    if (isMobileViewport()) return;
    e.preventDefault();
    startX = e.clientX;
    startWidth = state.sidebarWidth || SIDEBAR_WIDTH_DEFAULT;
    document.body.classList.add('sidebar-resizing');
    if (handle.setPointerCapture) handle.setPointerCapture(e.pointerId);
    window.addEventListener('pointermove', resizeSidebar);
    window.addEventListener('pointerup', stopResize);
    window.addEventListener('pointercancel', stopResize);
  });

  handle.addEventListener('keydown', e => {
    if (isMobileViewport()) return;
    let next = state.sidebarWidth || SIDEBAR_WIDTH_DEFAULT;
    if (e.key === 'ArrowLeft') next -= 20;
    else if (e.key === 'ArrowRight') next += 20;
    else if (e.key === 'Home') next = SIDEBAR_WIDTH_DEFAULT;
    else if (e.key === 'End') next = SIDEBAR_WIDTH_MAX;
    else return;
    e.preventDefault();
    setSidebarWidth(next, true);
  });
}

function normalizeMobileColumns(value) {
  const parsed = parseInt(value, 10);
  return [1, 2, 3].includes(parsed) ? parsed : MOBILE_COLUMNS_DEFAULT;
}

function setMobileColumns(columns, persist = false) {
  state.mobileColumns = normalizeMobileColumns(columns);
  document.documentElement.style.setProperty('--mobile-grid-columns', String(state.mobileColumns));
  $$('#mobileColumnToggle [data-mobile-columns]').forEach(btn => {
    btn.classList.toggle('active', Number(btn.dataset.mobileColumns) === state.mobileColumns);
  });
  if (persist) {
    try { localStorage.setItem(MOBILE_COLUMNS_STORAGE_KEY, String(state.mobileColumns)); } catch (e) {}
  }
}

function loadMobileColumns() {
  let saved = null;
  try { saved = localStorage.getItem(MOBILE_COLUMNS_STORAGE_KEY); } catch (e) {}
  setMobileColumns(saved || MOBILE_COLUMNS_DEFAULT, false);
}

function bindMobileColumnToggle() {
  $$('#mobileColumnToggle [data-mobile-columns]').forEach(btn => {
    btn.addEventListener('click', () => {
      setMobileColumns(btn.dataset.mobileColumns, true);
      logUiAction('mobile_column_change', collectUiLogContext({
        columns: state.mobileColumns,
      }));
    });
  });
}

function renderDuplicateFolders() {
  const section = $('#duplicateSection');
  const groups = state.duplicateFolders || [];
  if (!groups.length) {
    section.style.display = 'none';
    $('#duplicateList').innerHTML = '';
    $('#duplicateCount').textContent = '0';
    return;
  }

  section.style.display = '';
  section.classList.toggle('open', state.duplicateFoldersOpen);
  $('#duplicateCount').textContent = `${groups.length}组`;
  $('#duplicateList').innerHTML = groups.map(group => `
    <div class="duplicate-group">
      <div class="duplicate-name">${escHtml(group.name)} <span>${group.count}</span></div>
      ${group.paths.map(path => `
        <button class="duplicate-path" type="button" data-artist-id="${path.id}" title="${escHtml(path.path)}">
          <span>${escHtml(path.display_path || path.path)}</span>
          <strong>${path.item_count || 0}</strong>
        </button>
      `).join('')}
    </div>
  `).join('');

  $$('#duplicateList .duplicate-path').forEach(btn => {
    btn.addEventListener('click', () => {
      const id = btn.dataset.artistId;
      selectArtist(id);
      closeFilterDrawerIfMobile();
    });
  });
}

function renderSidebar() {
  const s = state.stats;
  if (!s) {
    $('#sidebarList').innerHTML = '';
    $('#sidebarTotal').textContent = '';
    return;
  }
  let html = `<div class="sidebar-item${!state.activeRole ? ' active' : ''}" data-role="">
    <span>全部</span><span class="count">${s.total}</span></div>`;

  html += `<div class="sidebar-item${state.activeRole === '__untagged__' ? ' active' : ''} unclassified" data-role="__untagged__">
    <span>未加标签</span><span class="count">${s.untagged || 0}</span></div>`;

  const tags = s.tags || [];
  tags.forEach(r => {
    const isUnclassified = false;
    const cls = [
      'sidebar-item',
      state.activeRole === String(r.id) ? 'active' : '',
      isUnclassified ? 'unclassified' : '',
    ].filter(Boolean).join(' ');
    html += `<div class="${cls}" data-role="${r.id}">
      <span>${escHtml(r.name)}</span><span class="count">${r.count}</span></div>`;
  });

  if (s.archives > 0) {
    html += `<div class="sidebar-item${state.activeRole === '__archives__' ? ' active' : ''}" data-role="__archives__">
      <span>压缩包</span><span class="count">${s.archives}</span></div>`;
  }
  if ((s.videos || 0) > 0) {
    html += `<div class="sidebar-item${state.activeRole === '__videos__' ? ' active' : ''}" data-role="__videos__">
      <span>视频</span><span class="count">${s.videos}</span></div>`;
  }
  if ((s.sources || 0) > 0) {
    html += `<div class="sidebar-item${state.activeRole === '__sources__' ? ' active' : ''}" data-role="__sources__">
      <span>源文件</span><span class="count">${s.sources}</span></div>`;
  }

  $('#sidebarList').innerHTML = html;
  $('#sidebarTotal').textContent = `共 ${s.total} 项`;

  bindSidebarEvents();
}

function renderFolderTree() {
  const tree = state.folders;
  if (!tree) {
    $('#folderTree').innerHTML = '';
    $('#folderTotal').textContent = '';
    return;
  }

  $('#folderTotal').textContent = `${tree.item_count || 0}`;
  $('#folderTree').innerHTML = renderFolderNode(tree, 0);
  bindFolderEvents();
}

function renderFolderNode(node, level) {
  const path = node.path || '';
  const name = path ? node.name : '全部';
  const active = state.activeFolder === path || (!state.activeFolder && !path);
  let html = `<div class="folder-item${active ? ' active' : ''}" data-folder="${escHtml(path)}" title="${escHtml(path || name)}" style="--level:${level}">
    <span class="folder-name">${escHtml(name)}</span><span class="count">${node.item_count || 0}</span>
  </div>`;
  (node.children || []).forEach(child => {
    html += renderFolderNode(child, level + 1);
  });
  return html;
}

function bindFolderEvents() {
  $$('#folderTree .folder-item').forEach(el => {
    el.addEventListener('click', () => {
      const folder = el.dataset.folder || '';
      state.activeFolder = folder || null;
      state.search = '';
      $('#searchInput').value = '';
      state.tagSearchResults = [];
      state.selectedIds.clear();
      syncSearchOptionsControl();
      updateEditBar();
      renderFolderTree();
      updateDuplicateFilesButton();
      scrollToItemsTop();
      loadItems();
      closeFilterDrawerIfMobile();
    });
  });
}

function bindSidebarEvents() {
  $$('#sidebarList .sidebar-item').forEach(el => {
    el.addEventListener('click', () => {
      state.activeRole = el.dataset.role || null;
      state.selectedIds.clear();
      updateEditBar();
      renderSidebar();
      scrollToItemsTop();
      loadItems();
      closeFilterDrawerIfMobile();
    });
    if (state.mode === 'edit') {
      el.addEventListener('dragover', e => { e.preventDefault(); el.classList.add('drag-over'); });
      el.addEventListener('dragleave', () => el.classList.remove('drag-over'));
      el.addEventListener('drop', e => {
        e.preventDefault();
        el.classList.remove('drag-over');
        const role = el.dataset.role || null;
        if (state.selectedIds.size > 0 && role && !role.startsWith('__')) {
          classifyItems([...state.selectedIds], [parseInt(role)], 'add');
        }
      });
    }
  });
}

function renderToolbar() {
  updateDuplicateFilesButton();
}

async function loadItems(options = {}) {
  const append = Boolean(options.append);
  if (append && !state.hasMoreItems) return;
  if (append && (state.loadingItems || state.loadingMoreItems)) return;
  const seq = append ? Number(state.itemLoadSeq || 0) : nextRequestSeq('itemLoadSeq');
  const searchScope = effectiveSearchScope();
  const globalSearch = isGlobalSearchActive();
  const folderScoped = state.activeFolder && (!state.search || searchScope === 'folder');
  const duplicateScopeActive = isDuplicateFilesScopeActive();
  if (state.duplicatesOnly && !duplicateScopeActive) {
    state.duplicatesOnly = false;
  }
  updateDuplicateFilesButton();
  if (!state.currentArtist && !globalSearch) {
    state.allItems = [];
    state.itemsOffset = 0;
    state.hasMoreItems = false;
    if (!append) {
      releaseAllImageLoads();
      releaseAllVideoPreviewLoads();
    }
    renderGrid();
    return;
  }
  state.loadingItems = true;
  state.loadingMoreItems = append;
  if (!append) {
    releaseAllImageLoads();
    releaseAllVideoPreviewLoads();
  }

  const offset = append ? state.itemsOffset : 0;
  const previousCount = append ? state.allItems.length : 0;
  const params = new URLSearchParams({limit: ITEM_PAGE_LIMIT, offset, sort: 'date_desc'});

  if (!globalSearch) {
    const aid = state.currentArtist.id;
    params.set('artist_id', aid);

    if (state.activeRole === '__archives__') {
      params.set('archive_only', 'true');
    } else if (state.activeRole === '__videos__') {
      params.set('media_type', 'video');
    } else if (state.activeRole === '__sources__') {
      params.set('media_type', 'source');
    } else if (state.activeRole === '__untagged__') {
      params.set('untagged', 'true');
    } else if (state.activeRole) {
      params.set('tag_id', state.activeRole);
    }
  }

  if (state.search) params.set('search', state.search);
  if (state.search && state.searchTarget === 'tags') params.set('search_tags_only', '1');
  if (!globalSearch && folderScoped) params.set('folder', state.activeFolder);
  if (!globalSearch && state.duplicatesOnly) params.set('duplicates_only', 'true');

  let tagSearchPromise = Promise.resolve({tags: []});
  if (state.search && state.searchTarget === 'tags') {
    const tagParams = new URLSearchParams({search: state.search, limit: 100});
    if (!globalSearch && state.currentArtist) tagParams.set('artist_id', state.currentArtist.id);
    tagSearchPromise = API.get('/api/tags/search?' + tagParams.toString());
  }

  try {
    const [data, tagData] = await Promise.all([
      API.get('/api/items?' + params.toString()),
      tagSearchPromise,
    ]);
    if (!isCurrentRequestSeq('itemLoadSeq', seq)) return;
    const nextItems = data.items || [];
    state.allItems = append ? state.allItems.concat(nextItems) : nextItems;
    state.itemsOffset = state.allItems.length;
    state.hasMoreItems = nextItems.length === ITEM_PAGE_LIMIT;
    if (!append) state.tagSearchResults = tagData.tags || [];
    if (append) {
      appendItemsToGrid(nextItems, previousCount);
    } else {
      renderGrid();
    }
    updateDuplicateFilesButton();
    logUiAction('items_loaded', collectUiLogContext({
      append,
      returned_count: nextItems.length,
      offset: state.itemsOffset,
      has_more: state.hasMoreItems,
      mobile: isMobileViewport(),
    }));
  } finally {
    if (isCurrentRequestSeq('itemLoadSeq', seq)) {
      state.loadingItems = false;
      state.loadingMoreItems = false;
    }
  }
  if (!isCurrentRequestSeq('itemLoadSeq', seq)) return;
  requestAnimationFrame(maybeLoadMoreOnScroll);
}

function scrollToItemsTop() {
  const container = $('#gridContainer');
  if (container) container.scrollTo({top: 0, behavior: 'auto'});
  window.scrollTo({top: 0, behavior: 'auto'});
}

function remainingScrollDistance() {
  const container = $('#gridContainer');
  if (container && container.clientHeight) {
    return container.scrollHeight - container.scrollTop - container.clientHeight;
  }
  return document.documentElement.scrollHeight - (window.scrollY + window.innerHeight);
}

function maybeLoadMoreOnScroll() {
  if (state.mode === 'moves') return;
  if (!state.hasMoreItems || state.loadingItems || state.loadingMoreItems) return;
  if (remainingScrollDistance() <= INFINITE_SCROLL_THRESHOLD) {
    loadItems({append: true});
  }
}

function isCurrentFolderScopeActive() {
  return Boolean(state.currentArtist && state.activeFolder && state.mode !== 'moves' && !isGlobalSearchActive() && (!state.search || effectiveSearchScope() === 'folder'));
}

function isDuplicateFilesScopeActive() {
  return Boolean(state.currentArtist && state.mode !== 'moves' && !isGlobalSearchActive());
}

function isCurrentArtistScanScopeActive() {
  return Boolean(state.currentArtist && !state.activeFolder && state.mode !== 'moves' && !isGlobalSearchActive() && (!state.search || effectiveSearchScope() === 'artist'));
}

function isCurrentScanScopeActive() {
  return isCurrentFolderScopeActive() || isCurrentArtistScanScopeActive();
}

function updateDuplicateFilesButton() {
  const btn = $('#duplicateFilesBtn');
  if (!btn) return;
  const visible = isDuplicateFilesScopeActive();
  if (!visible) state.duplicatesOnly = false;
  btn.style.display = visible ? '' : 'none';
  btn.classList.toggle('active', state.duplicatesOnly);
  btn.setAttribute('aria-pressed', state.duplicatesOnly ? 'true' : 'false');
  updateScanFolderButton();
}

function updateScanFolderButton() {
  const btn = $('#scanFolderBtn');
  if (!btn) return;
  const label = state.activeFolder ? '扫描当前文件夹' : '扫描当前画师';
  btn.textContent = label;
  btn.title = label;
  btn.style.display = !state.scanRunning && isCurrentScanScopeActive() ? '' : 'none';
}

function isGlobalSearchActive() {
  return Boolean(state.search && effectiveSearchScope() === 'global');
}

function renderTagSearchResults() {
  if (!state.search || state.searchTarget !== 'tags' || !state.tagSearchResults.length) return '';
  return `<div class="tag-result-section">
    <div class="tag-result-title">标签结果</div>
    <div class="tag-result-list">
      ${state.tagSearchResults.map(tag => `
        <button class="tag-result-card" type="button" data-tag-jump="${tag.id}" data-artist-id="${tag.artist_id}" title="转到 ${escHtml(tag.artist_name || '')}">
          <span>${escHtml(tag.name)}</span>
          <em>${escHtml(tag.artist_name || '未知画师')}</em>
          <strong>${tag.item_count || 0} 项</strong>
        </button>
      `).join('')}
    </div>
  </div>`;
}
