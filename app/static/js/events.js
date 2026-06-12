function applyMode(mode) {
  state.mode = mode;
  state.selectedIds.clear();
  closeFilterDrawer();
  closeMobileHeaderToolsIfMobile();
  updateEditBar();
  const isMoves = mode === 'moves';
  $('#movePanel').classList.toggle('visible', isMoves);
  $('#gridContainer').style.display = isMoves ? 'none' : '';
  $('.toolbar').style.display = isMoves ? 'none' : '';
  $('#searchInput').disabled = isMoves;
  $('#searchOptionsBtn').disabled = isMoves;
  if (isMoves) closeSearchOptions();
  updateDuplicateFilesButton();
  if (isMoves) {
    setMaintenanceView(state.maintenanceView || 'overview');
    loadMoveWorkbench();
    startMaintenanceAutoRefresh();
  } else {
    stopMaintenanceAutoRefresh();
    renderSidebar();
    renderGrid();
  }
}

function bindEvents() {
  $('#mobileHeaderToggle').addEventListener('click', toggleMobileHeaderTools);
  syncMobileHeaderTools();
  syncSearchOptionsControl();
  $('#mobileFilterBtn').addEventListener('click', openFilterDrawer);
  $('#filterBackdrop').addEventListener('click', closeFilterDrawer);
  $('#filterDrawerClose').addEventListener('click', closeFilterDrawer);
  bindSidebarResize();
  bindMobileColumnToggle();
  bindLightboxVideoDiagnostics();
  window.addEventListener('resize', () => {
    setSidebarWidth(state.sidebarWidth, false);
    if (!isMobileViewport()) {
      closeFilterDrawer();
      closeMobileHeaderTools();
    }
  });
  $('#artistSearch').addEventListener('focus', e => {
    e.target.select();
    renderArtistDropdown('');
  });
  $('#artistSearch').addEventListener('input', e => {
    renderArtistDropdown(e.target.value);
  });
  $('#artistSearch').addEventListener('keydown', e => {
    if (e.key === 'Enter') {
      e.preventDefault();
      selectFirstArtistResult();
    } else if (e.key === 'Escape') {
      closeArtistDropdown();
    }
  });
  $('#duplicateToggle').addEventListener('click', () => {
    state.duplicateFoldersOpen = !state.duplicateFoldersOpen;
    renderDuplicateFolders();
  });
  $('#searchInput').addEventListener('input', debounce(e => {
    state.search = e.target.value;
    logUiAction('search_change', {search: state.search, scope: state.searchScope, target: state.searchTarget});
    scrollToItemsTop();
    loadItems();
  }, 300));
  $('#searchOptionsBtn').addEventListener('click', e => {
    e.stopPropagation();
    toggleSearchOptions();
  });
  $$('#searchOptionsMenu [data-search-scope]').forEach(btn => {
    btn.addEventListener('click', e => {
      e.stopPropagation();
      setSearchScope(btn.dataset.searchScope);
      logUiAction('search_change', {search: state.search, scope: state.searchScope, target: state.searchTarget});
      scrollToItemsTop();
      loadItems();
    });
  });
  $('#tagsOnlyToggle').addEventListener('change', e => {
    setSearchTarget(e.target.checked ? 'tags' : 'all');
    logUiAction('search_change', {search: state.search, scope: state.searchScope, target: state.searchTarget});
    scrollToItemsTop();
    loadItems();
  });
  $('#duplicateFilesBtn').addEventListener('click', () => {
    state.duplicatesOnly = !state.duplicatesOnly;
    updateDuplicateFilesButton();
    scrollToItemsTop();
    loadItems();
  });
  const gridContainer = $('#gridContainer');
  if (gridContainer) gridContainer.addEventListener('scroll', maybeLoadMoreOnScroll, {passive: true});
  window.addEventListener('scroll', maybeLoadMoreOnScroll, {passive: true});
  $('#scanFolderBtn').addEventListener('click', async () => {
    if (!state.currentArtist || !isCurrentScanScopeActive() || isActionBusy('scan-context')) return;
    setActionBusy('scan-context', '', true);
    const isFolderScan = Boolean(state.activeFolder);
    const params = new URLSearchParams();
    params.set('artist_id', state.currentArtist.id);
    if (state.activeFolder) params.set('folder', state.activeFolder);
    try {
      const r = await API.post('/api/scan/folder?' + params.toString());
      if (r.ok) toast(isFolderScan ? '当前文件夹扫描已启动' : '当前画师扫描已启动', 'success');
      else toast(r.message || '扫描已在运行', 'error');
    } catch (e) {
      toast(isFolderScan ? '启动当前文件夹扫描失败' : '启动当前画师扫描失败', 'error');
    } finally {
      setActionBusy('scan-context', '', false);
    }
  });

  $$('.mode-tabs button').forEach(btn => {
    btn.addEventListener('click', () => {
      $$('.mode-tabs button').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      logUiAction('mode_change', {mode: btn.dataset.mode});
      applyMode(btn.dataset.mode);
    });
  });

  $$('#desktopViewToggle button').forEach(btn => {
    btn.addEventListener('click', () => {
      $$('#desktopViewToggle button').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      state.view = btn.dataset.view;
      renderGrid();
    });
  });

  $('#scanBtn').addEventListener('click', startFullScan);
  $('#emptyScanBtn').addEventListener('click', startFullScan);

  $('#stopScanBtn').addEventListener('click', async () => {
    if (isActionBusy('scan-stop')) return;
    setActionBusy('scan-stop', '', true);
    try {
      const r = await API.post('/api/scan/stop');
      if (r.ok) toast('正在停止扫描...', 'success');
      else toast(r.message || '停止失败', 'error');
    } catch (e) {
      toast('停止失败', 'error');
    } finally {
      setActionBusy('scan-stop', '', false);
    }
  });

  $('#moveRefreshBtn').addEventListener('click', () => loadMoveWorkbench({preserveScroll: true}));
  $('#folderRenameAutoExecuteToggle').addEventListener('change', e => {
    setFolderRenameAutoEnabled(e.target.checked);
  });
  $('#folderRenameRunArtistBtn').addEventListener('click', runFolderRenameForCurrentArtist);
  $$('.maintenance-view-tabs [data-maintenance-view]').forEach(btn => {
    btn.addEventListener('click', () => setMaintenanceView(btn.dataset.maintenanceView));
  });

  $('#manualBackupBtn').addEventListener('click', async () => {
    if (isActionBusy('backup-manual')) return;
    setActionBusy('backup-manual', '', true);
    const btn = $('#manualBackupBtn');
    const result = $('#backupResult');
    btn.disabled = true;
    btn.textContent = '备份中…';
    result.textContent = '';
    result.style.color = '';
    try {
      const r = await API.post('/api/backup');
      result.textContent = r.ok ? '备份完成' : '备份失败';
      result.style.color = r.ok ? 'var(--status-ok)' : 'var(--status-danger)';
      await loadHealthSummary();
    } catch (e) {
      result.textContent = '备份失败: ' + (e.message || e);
      result.style.color = 'var(--status-danger)';
    } finally {
      btn.disabled = false;
      btn.textContent = '立即备份数据库';
      setActionBusy('backup-manual', '', false);
    }
  });

  $('#editApplyBtn').addEventListener('click', async () => {
    logUiAction('edit_apply_click', {selected_count: state.selectedIds.size, folder: state.activeFolder || '', artist_id: currentEditArtistId()});
    await ensureEditTagContext();
    await selectOrCreateEditTagQuery();
    const tagIds = selectedEditTagIds();
    const tagNames = selectedEditTagNames(tagIds);
    if (tagIds.length === 0 && tagNames.length === 0) { toast('请选择标签', 'error'); return; }
    if (state.selectedIds.size > 0) {
      classifyItems([...state.selectedIds], tagIds, 'add');
      return;
    }
    if (state.activeFolder) {
      classifyFolder(state.activeFolder, tagIds, 'add');
      return;
    }
    toast('请选择图片或文件夹', 'error');
  });

  $('#editSelectAllBtn').addEventListener('click', () => {
    state.selectedIds = new Set((state.allItems || []).filter(isTaggableItem).map(item => item.id));
    updateEditBar();
    renderGrid();
  });

  $('#editTagSearch').addEventListener('focus', e => {
    state.editTagQuery = e.target.value;
    openEditTagPicker();
  });
  $('#editTagSearch').addEventListener('input', e => {
    state.editTagQuery = e.target.value;
    openEditTagPicker();
  });
  $('#editTagSearch').addEventListener('keydown', e => {
    if (e.key === 'Enter') {
      e.preventDefault();
      selectFirstEditTagResult();
    } else if (e.key === 'Escape') {
      closeEditTagPicker();
    }
  });

  $('#editDeleteRoleBtn').addEventListener('click', deleteSelectedTags);

  $('#editCancelBtn').addEventListener('click', () => {
    state.selectedIds.clear();
    updateEditBar();
    renderGrid();
  });

  document.addEventListener('click', e => {
    if (!$('#editTagPicker').contains(e.target)) {
      closeEditTagPicker();
    }
    if (!$('#artistPicker').contains(e.target)) {
      closeArtistDropdown();
    }
    if (!$('#searchControl').contains(e.target)) {
      closeSearchOptions();
    }
  });
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && closeTopmostOverlay()) {
      e.preventDefault();
    }
  });

  $('#lightbox').addEventListener('click', e => {
    if (e.target === $('#lightbox') || e.target.classList.contains('close')) closeLightbox();
  });
  $('#lightbox').addEventListener('wheel', onLightboxWheel, {passive: false});
  const lightboxImg = $('#lightboxImg');
  lightboxImg.addEventListener('pointerdown', startLightboxPan);
  lightboxImg.addEventListener('pointermove', moveLightboxPan);
  lightboxImg.addEventListener('pointerup', stopLightboxPan);
  lightboxImg.addEventListener('pointercancel', stopLightboxPan);
  $('#lightboxDownloadBtn').addEventListener('click', e => {
    e.stopPropagation();
  });
  $('#lightbox .prev').addEventListener('click', e => {
    e.stopPropagation();
    moveLightbox(-1);
  });
  $('#lightbox .next').addEventListener('click', e => {
    e.stopPropagation();
    moveLightbox(1);
  });
}

function closeTopmostOverlay() {
  if ($('#lightbox').style.display === 'flex') {
    closeLightbox();
    return true;
  }
  if (state.filterDrawerOpen) {
    closeFilterDrawer();
    return true;
  }
  if ($('#editTagPicker').classList.contains('open')) {
    closeEditTagPicker();
    return true;
  }
  if ($('#artistDropdown').classList.contains('open')) {
    closeArtistDropdown();
    return true;
  }
  if (state.searchOptionsOpen) {
    closeSearchOptions();
    return true;
  }
  if (state.mobileHeaderToolsOpen) {
    closeMobileHeaderTools();
    return true;
  }
  return false;
}

function isTerminalScanState(s) {
  return s && s.status === 'idle' && ['complete', 'stopped', 'interrupted'].includes(s.phase);
}

function connectWS() {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const ws = new WebSocket(`${proto}//${location.host}/ws/scan`);
  ws.onmessage = e => {
    const s = JSON.parse(e.data);
    const wasScanning = state.lastScanState && state.lastScanState.status === 'scanning';
    state.lastScanState = s;
    state.scanRunning = s.status === 'scanning';
    const scanTerminal = isTerminalScanState(s);
    const scanJustFinished = wasScanning && scanTerminal;
    if (s.status === 'scanning') {
      showProgress(s);
      $('#scanBtn').style.display = 'none';
      updateScanFolderButton();
      $('#stopScanBtn').style.display = '';
      renderLibraryEmptyState();
    } else {
      hideProgress();
      $('#scanBtn').style.display = '';
      updateScanFolderButton();
      $('#stopScanBtn').style.display = 'none';
      renderLibraryEmptyState();
      if (scanJustFinished) {
        refreshAfterScan();
      }
    }
  };
  ws.onclose = () => setTimeout(connectWS, 3000);
}

function showProgress(s) {
  const panel = $('#progressPanel');
  panel.classList.add('visible');
  $('#progressTitle').textContent = s.phase === 'discover' ? '发现画师目录...' :
    s.phase === 'scan' ? '扫描画师文件...' :
    s.phase === 'parse' ? '解析文件元数据...' : '扫描中...';
  const pct = s.total_estimate > 0 ? Math.round(s.scanned_count / s.total_estimate * 100) : 0;
  $('#progressFill').style.width = Math.min(pct, 100) + '%';
  $('#progressCount').textContent = `${s.scanned_count} / ${s.total_estimate}`;
  $('#progressPath').textContent = s.current_path || '';
}

function hideProgress() {
  $('#progressPanel').classList.remove('visible');
}

async function refreshAfterScan() {
  const seq = await refreshCurrentView({reason: 'scan_complete'});
  if (isCurrentRequestSeq('scanRefreshSeq', seq)) toast('扫描完成', 'success');
}

async function refreshCurrentView({reason = 'manual'} = {}) {
  const currentArtistId = state.currentArtist ? state.currentArtist.id : null;
  const activeFolder = state.activeFolder;
  const currentMode = state.mode;
  const maintenanceView = state.maintenanceView;
  const gridContainer = $('#gridContainer');
  const gridScrollTop = gridContainer ? gridContainer.scrollTop : null;
  const seq = nextRequestSeq('scanRefreshSeq');
  await loadArtists();
  if (!isCurrentRequestSeq('scanRefreshSeq', seq)) return seq;
  if (currentMode === 'moves' || state.mode === 'moves') {
    setMaintenanceView(maintenanceView || 'overview');
    await loadMoveWorkbench({preserveScroll: true});
    logUiAction('refresh_current_view', {reason, mode: 'moves'});
    return seq;
  }
  if (currentArtistId) {
    state.currentArtist = state.artists.find(a => a.id === currentArtistId) || null;
    if (!state.currentArtist) {
      clearUI();
      logUiAction('refresh_current_view', {reason, artist_missing: true});
      return seq;
    }
    state.activeFolder = activeFolder;
    const [stats, tags, folders] = await Promise.all([
      API.get(`/api/artists/${currentArtistId}/stats`),
      API.get(`/api/tags?artist_id=${currentArtistId}`),
      API.get(`/api/folders?artist_id=${currentArtistId}`),
    ]);
    if (!isCurrentRequestSeq('scanRefreshSeq', seq)) return seq;
    state.stats = stats;
    state.tags = tags;
    state.folders = folders;
    renderSidebar();
    renderFolderTree();
    renderEditTagPicker();
    renderToolbar();
    await loadItems();
    if (gridScrollTop != null && $('#gridContainer')) $('#gridContainer').scrollTop = gridScrollTop;
  }
  logUiAction('refresh_current_view', {reason});
  return seq;
}

function toast(msg, type) {
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 2500);
}

function escHtml(s) {
  if (!s) return '';
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function formatSize(bytes) {
  if (!bytes) return '0 B';
  const units = ['B','KB','MB','GB'];
  let i = 0;
  let s = bytes;
  while (s >= 1024 && i < units.length - 1) { s /= 1024; i++; }
  return s.toFixed(1) + ' ' + units[i];
}

function renderTagNames(tags) {
  if (!tags || tags.length === 0) return '未加标签';
  return joinUiMeta(tags.map(t => escHtml(t.name)));
}

function downloadFileName(item) {
  return (item.file_name || 'image').replace(/[\\/:*?"<>|]/g, '_');
}

async function copyText(text) {
  if (!text) return false;
  if (navigator.clipboard && navigator.clipboard.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch (e) {}
  }
  const textarea = document.createElement('textarea');
  textarea.value = text;
  textarea.setAttribute('readonly', '');
  textarea.style.position = 'fixed';
  textarea.style.left = '-9999px';
  document.body.appendChild(textarea);
  textarea.select();
  try {
    return document.execCommand('copy');
  } catch (e) {
    return false;
  } finally {
    textarea.remove();
  }
}

function debounce(fn, ms) {
  let timer;
  return (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), ms); };
}

installFrontendErrorLogging();
init();
