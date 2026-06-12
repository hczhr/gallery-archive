function movePanelScrollTop() {
  const panel = $('#movePanel');
  return panel ? panel.scrollTop : 0;
}

function restoreMovePanelScroll(top) {
  if (top == null) return;
  const panel = $('#movePanel');
  if (!panel) return;
  panel.scrollTop = top;
  requestAnimationFrame(() => { panel.scrollTop = top; });
}

async function loadMoveWorkbench(options = {}) {
  const preservedScrollTop = options.preserveScroll ? movePanelScrollTop() : null;
  const seq = nextRequestSeq('maintenanceLoadSeq');
  const [pending, applied, hashStatus, health, folderRenameAuto, folderRenameResult, operationLog] = await Promise.all([
    API.get('/api/move-candidates?status=pending'),
    API.get('/api/move-history?status=applied'),
    API.get('/api/hash/status'),
    loadHealthSummary({render: false, updateState: false}),
    loadFolderRenameAutoStatus({render: false, updateState: false}),
    loadFolderRenamePlans({render: false, updateState: false}),
    loadOperationLog({render: false, updateState: false}),
  ]);
  if (!isCurrentRequestSeq('maintenanceLoadSeq', seq)) return;
  state.moveCandidates = pending.candidates || [];
  state.moveWaitingHashCount = pending.waiting_hash_count || 0;
  state.moveHistory = applied.history || [];
  state.hashStatus = hashStatus;
  state.healthSummary = health;
  state.folderRenameAuto = folderRenameAuto;
  state.folderRenamePlans = folderRenameResult ? folderRenameResult.plans : null;
  state.operationLog = operationLog;
  if (folderRenameResult && folderRenameResult.tags) state.tags = folderRenameResult.tags;
  renderMoveWorkbench();
  restoreMovePanelScroll(preservedScrollTop);
}

async function refreshMoveWorkbenchAutomatically() {
  if (state.mode !== 'moves' || maintenanceAutoRefreshInFlight) return;
  maintenanceAutoRefreshInFlight = true;
  try {
    await loadMoveWorkbench({preserveScroll: true});
  } finally {
    maintenanceAutoRefreshInFlight = false;
  }
}

function startMaintenanceAutoRefresh() {
  stopMaintenanceAutoRefresh();
  maintenanceAutoRefreshTimer = setInterval(refreshMoveWorkbenchAutomatically, MAINTENANCE_AUTO_REFRESH_MS);
}

function stopMaintenanceAutoRefresh() {
  if (!maintenanceAutoRefreshTimer) return;
  clearInterval(maintenanceAutoRefreshTimer);
  maintenanceAutoRefreshTimer = null;
}

function setMaintenanceView(view) {
  const selected = ['overview', 'auto-archive', 'confirmed-plans', 'paths', 'operation-log', 'guide'].includes(view) ? view : 'overview';
  state.maintenanceView = selected;
  $$('.maintenance-view-tabs [data-maintenance-view]').forEach(btn => {
    const active = btn.dataset.maintenanceView === selected;
    btn.classList.toggle('active', active);
    btn.setAttribute('aria-selected', active ? 'true' : 'false');
  });
  $$('.maintenance-view-panel[data-maintenance-view-panel]').forEach(panel => {
    const active = panel.dataset.maintenanceViewPanel === selected;
    panel.hidden = !active;
    panel.classList.toggle('active', active);
  });
}

async function loadHealthSummary(options = {}) {
  const render = options.render !== false;
  const updateState = options.updateState !== false;
  try {
    const health = await API.get('/api/health');
    if (updateState) state.healthSummary = health;
    if (render) renderHealthSummary();
    return health;
  } catch (e) {
    const health = {ok: false, error: e.message};
    if (updateState) state.healthSummary = health;
    if (render) renderHealthSummary();
    return health;
  }
}

async function loadOperationLog(options = {}) {
  const render = options.render !== false;
  const updateState = options.updateState !== false;
  try {
    const log = await API.get('/api/operation-log?limit=80&error_limit=40');
    if (updateState) state.operationLog = log;
    if (render) renderOperationLog();
    return log;
  } catch (e) {
    const log = {history: [], errors: [], error: e.message};
    if (updateState) state.operationLog = log;
    if (render) renderOperationLog();
    return log;
  }
}

function renderMoveWorkbench() {
  $('#movePendingCount').textContent = state.moveCandidates.length;
  $('#moveWaitingHashCount').textContent = state.moveWaitingHashCount || 0;
  $('#movePreviewCount').textContent = state.moveHistory.length;
  renderHashStatus();
  renderHealthSummary();
  renderFolderRenameAutoStatus();
  renderFolderRenameAutoDetails();
  renderFolderRenamePlans();
  renderMoveCandidates();
  renderMoveHistory();
  renderOperationLog();
}

function folderRenameAutoSummaryText(status) {
  if (!status) return '自动归档：读取中';
  if (status.error) return '自动归档：读取失败';
  if (!status.enabled) return '自动归档：关闭';
  const last = status.last_run;
  if (!last) return joinUiMeta(['自动归档：开启', '暂无记录']);
  const labels = {
    disabled: '已关闭',
    scope_skipped: '本轮跳过',
    no_actions: '无可执行',
    skipped: '已跳过',
    partial: '部分完成',
    executed: '已执行',
    failed: '失败',
  };
  const statusLabel = labels[last.status] || last.status || '未知';
  return joinUiMeta(['自动归档：开启', formatHealthTime(last.at), statusLabel]);
}

function renderFolderRenameAutoStatus() {
  const toggle = $('#folderRenameAutoExecuteToggle');
  const folderRenameRunArtistBtn = $('#folderRenameRunArtistBtn');
  if (!toggle) return;
  const status = state.folderRenameAuto;
  const hasError = Boolean(status && status.error);
  const saving = Boolean(status && status.saving);
  if (toggle) {
    toggle.checked = Boolean(status && status.enabled);
    toggle.disabled = !status || hasError || saving;
  }
  if (folderRenameRunArtistBtn) {
    const running = Boolean(state.folderRenameAutoRunningArtist);
    folderRenameRunArtistBtn.disabled = running || !state.currentArtist || !status || hasError;
    folderRenameRunArtistBtn.textContent = running ? '执行中...' : '重命名当前画师';
    folderRenameRunArtistBtn.title = !state.currentArtist
      ? '先选择画师'
      : '手动执行当前画师已确认且安全的归档预案';
  }
}

function folderRenameAutoScopeText(last) {
  if (!last) return '暂无范围';
  if (last.scope === 'full') return '全库扫描';
  if (last.scope === 'artist') return last.artist_id ? `当前画师 #${last.artist_id}` : '当前画师';
  if (last.scope === 'manual_artist') return last.artist_id ? `手动当前画师 #${last.artist_id}` : '手动当前画师';
  if (last.scope === 'folder') return '当前文件夹扫描';
  return last.scope || '未知范围';
}

function folderRenameAutoErrorText(error) {
  if (!error) return '未知原因';
  const labels = {
    backup_failed: '数据库备份失败',
    duplicate_in_group: '同目标存在重名文件',
    duplicate_target: '目标文件夹重复',
    execution_failed: '执行失败',
    file_count_mismatch: '文件数量与确认时不一致',
    missing_source_folder: '来源文件夹为空',
    missing_split_actions: '拆分预案没有文件动作',
    missing_target_folder: '目标文件夹为空',
    mtime_mismatch: '文件修改时间与确认时不一致',
    no_confirmed_plans: '没有已确认预案',
    same_source_target: '来源和目标相同',
    source_missing: '来源文件夹不存在',
    source_outside_artist: '来源不在画师目录内',
    split_source_missing: '拆分来源文件不存在',
    target_exists: '目标已存在',
    target_outside_artist: '目标不在画师目录内',
    total_size_mismatch: '文件总大小与确认时不一致',
  };
  if (error.code && labels[error.code]) return labels[error.code];
  return error.message || error.code || String(error);
}

function folderRenameAutoActionTargetText(action) {
  const targets = action.targets || [];
  if (targets.length) return joinUiMeta(targets);
  return action.target_folder || action.target || '未生成目标';
}

function folderRenameAutoActionSourceText(action) {
  return action.source_folder || action.source || '未知来源';
}

function folderRenameAutoActionTitleText(source, target) {
  if (!target || target === '未生成目标' || target === source) return source;
  return `${source} -> ${target}`;
}

function folderRenameAutoErrorsHtml(errors, fallbackText) {
  const messages = (errors || []).map(error => folderRenameAutoErrorText(error)).filter(Boolean);
  if (!messages.length && fallbackText) messages.push(fallbackText);
  if (!messages.length) return '';
  return `<div class="folder-rename-auto-errors">${
    messages.map(message => `<span class="folder-rename-auto-error">${escHtml(message)}</span>`).join('')
  }</div>`;
}

function folderRenameAutoSkippedControlsHtml(planId) {
  if (!planId) return '';
  const busy = state.folderRenameAutoPlanBusy.has(String(planId));
  const disabled = busy ? 'disabled' : '';
  const safePlanId = escHtml(String(planId));
  return `<div class="folder-rename-auto-action-controls" aria-label="已跳过预案操作">
    <button type="button" data-folder-rename-auto-recheck="${safePlanId}" ${disabled}>重新检查</button>
    <button type="button" data-folder-rename-auto-reconfirm="${safePlanId}" ${disabled}>重新确认</button>
    <button type="button" data-folder-rename-auto-unconfirm="${safePlanId}" ${disabled}>取消确认</button>
  </div>`;
}

function folderRenameAutoCheckMessage(check) {
  if (!check) return '';
  if (check.message) return check.message;
  if (check.status === 'ready') return check.operation === 'reconfirm'
    ? '已重新确认，当前检查通过，等待下次扫描自动执行'
    : '当前检查通过，等待下次扫描自动执行';
  if (check.status === 'draft') return '已取消确认，预案已回到待确认页';
  if (check.status === 'blocked') return check.operation === 'reconfirm'
    ? '已重新确认，但当前仍需处理'
    : '当前仍需处理';
  return '检查状态已更新';
}

function folderRenameAutoPlanCheckHtml(action) {
  const planId = action.plan_id ? String(action.plan_id) : '';
  const check = planId ? state.folderRenameAutoPlanChecks[planId] : null;
  if (!check) return '';
  const errors = check.status === 'blocked' ? ((check.action || {}).errors || check.errors || []) : [];
  return `<div class="folder-rename-auto-check ${escHtml(check.status || '')}">
    <span>${escHtml(folderRenameAutoCheckMessage(check))}</span>
    ${errors.length ? folderRenameAutoErrorsHtml(errors) : ''}
  </div>`;
}

function renderFolderRenameAutoAction(action, kind) {
  const statusLabels = {
    executed: '已执行',
    skipped: '已跳过',
    failed: '失败',
  };
  const planId = action.plan_id ? String(action.plan_id) : '';
  const check = planId ? state.folderRenameAutoPlanChecks[planId] : null;
  const displayAction = check && check.action ? {...action, ...check.action} : action;
  const source = folderRenameAutoActionSourceText(displayAction);
  const target = folderRenameAutoActionTargetText(displayAction);
  const errors = displayAction.errors || (displayAction.error ? [displayAction.error] : []);
  const detail = joinUiMeta([
    displayAction.updated_items != null ? `${displayAction.updated_items} 条记录已更新` : '',
    displayAction.file_count != null ? `${displayAction.file_count} 个文件` : '',
    displayAction.total_size ? formatBytes(displayAction.total_size) : '',
  ]);
  const errorHtml = folderRenameAutoErrorsHtml(errors);
  if (kind === 'skipped') {
    const skippedErrorHtml = check && ['ready', 'draft'].includes(check.status)
      ? ''
      : (errorHtml || folderRenameAutoErrorsHtml([], '预案当前状态不安全，已跳过自动执行'));
    const skippedControlsHtml = check && check.status === 'draft' ? '' : folderRenameAutoSkippedControlsHtml(planId);
    return `<div class="folder-rename-auto-action skipped">
      <div class="folder-rename-auto-action-head">
        <div class="folder-rename-auto-action-title">
          <b>已跳过</b>
          <span>${escHtml(folderRenameAutoActionTitleText(source, target))}</span>
        </div>
        <div class="folder-rename-auto-action-side">
          <em>${escHtml(detail || `预案 #${planId || '-'}`)}</em>
          ${skippedControlsHtml}
        </div>
      </div>
      ${skippedErrorHtml}
      ${folderRenameAutoPlanCheckHtml(action)}
    </div>`;
  }
  return `<div class="folder-rename-auto-action ${escHtml(kind)}">
    <div class="folder-rename-auto-action-head">
      <div class="folder-rename-auto-action-title">
        <b>${statusLabels[kind] || kind}</b>
        <span>${escHtml(detail || `预案 #${action.plan_id || '-'}`)}</span>
      </div>
    </div>
    <div class="folder-rename-auto-path"><span>来源</span><code title="${escHtml(source)}">${escHtml(source)}</code></div>
    <div class="folder-rename-auto-path"><span>目标</span><code title="${escHtml(target)}">${escHtml(target)}</code></div>
    ${errorHtml}
  </div>`;
}

function renderFolderRenameAutoSkippedGroup(group) {
  const actions = group.actions || [];
  if (actions.length) {
    return actions.map(action => renderFolderRenameAutoAction(action, 'skipped')).join('');
  }
  const errors = group.errors || [];
  const message = folderRenameAutoErrorsHtml(errors, '没有可执行预案');
  return `<div class="folder-rename-auto-action skipped">
    <div class="folder-rename-auto-action-head">
      <div class="folder-rename-auto-action-title">
        <b>已跳过</b>
        <span>${escHtml(group.artist_name || '画师')}</span>
      </div>
      <em>${Number(group.count || 0)} 项</em>
    </div>
    ${message}
  </div>`;
}

function folderRenameAutoSectionExpanded(key) {
  return state.folderRenameAutoExpandedSections.has(String(key));
}

function toggleFolderRenameAutoSection(key) {
  if (!key) return;
  const safeKey = String(key);
  if (state.folderRenameAutoExpandedSections.has(safeKey)) {
    state.folderRenameAutoExpandedSections.delete(safeKey);
  } else {
    state.folderRenameAutoExpandedSections.add(safeKey);
  }
  renderFolderRenameAutoDetails();
}

function renderFolderRenameAutoSection(title, count, body, emptyText, options = {}) {
  const collapsibleItems = Array.isArray(options.collapsibleItems) ? options.collapsibleItems : null;
  const itemCount = collapsibleItems ? collapsibleItems.length : Number(count || 0);
  const collapsibleKey = options.collapsibleKey ? String(options.collapsibleKey) : '';
  const collapsible = Boolean(collapsibleKey && collapsibleItems && collapsibleItems.length > 1);
  const expanded = collapsible && folderRenameAutoSectionExpanded(collapsibleKey);
  const sectionClass = `folder-rename-auto-section${collapsible ? ' collapsible' : ''}${expanded ? ' expanded' : ''}`;
  const sectionTitle = options.title ? ` title="${escHtml(options.title)}"` : '';
  let contentHtml = body || `<div class="move-empty small">${escHtml(emptyText)}</div>`;
  if (collapsible) {
    const visibleItems = expanded ? collapsibleItems : collapsibleItems.slice(0, 1);
    const hiddenCount = Math.max(0, collapsibleItems.length - 1);
    const toggleLabel = expanded ? '收起' : `已隐藏 ${hiddenCount} 条`;
    const toggleIcon = expanded ? '▴' : '▾';
    const toggleHtml = `<button type="button" class="folder-rename-auto-section-reveal" data-folder-rename-auto-toggle="${escHtml(collapsibleKey)}" aria-expanded="${expanded ? 'true' : 'false'}">
      <span class="folder-rename-auto-section-toggle-icon" aria-hidden="true">${toggleIcon}</span>
      <span>${escHtml(toggleLabel)}</span>
    </button>`;
    contentHtml = `${visibleItems.join('')}${toggleHtml}`;
  }
  return `<div class="${sectionClass}"${sectionTitle}>
    <div class="folder-rename-auto-section-head">
      <b>${escHtml(title)}</b>
      <div class="folder-rename-auto-section-meta">
        <span>${itemCount} 项</span>
      </div>
    </div>
    <div class="folder-rename-auto-section-body">${contentHtml}</div>
  </div>`;
}

function renderFolderRenameAutoDetails() {
  const summary = $('#folderRenameAutoDetailSummary');
  const list = $('#folderRenameAutoDetailList');
  if (!summary || !list) return;
  const status = state.folderRenameAuto;
  summary.textContent = folderRenameAutoSummaryText(status);
  if (!status) {
    list.innerHTML = '<div class="move-empty small">等待读取自动归档状态</div>';
    return;
  }
  if (status.error) {
    list.innerHTML = `<div class="move-empty small">读取失败：${escHtml(status.error)}</div>`;
    return;
  }
  if (!status.enabled) {
    list.innerHTML = '<div class="move-empty small">自动归档已关闭；开启后，扫描完成才会执行已确认且安全的预案。</div>';
    return;
  }
  const last = status.last_run;
  if (!last) {
    list.innerHTML = '<div class="move-empty small">自动归档已开启，暂无执行记录。</div>';
    return;
  }
  const actions = last.actions || [];
  const skipped = last.skipped || [];
  const failed = last.failed || [];
  const errors = last.errors || [];
  const skippedActions = skipped.reduce((total, group) => total + ((group.actions || []).length || Number(group.count || 0)), 0);
  const runMeta = `<div class="folder-rename-auto-run maintenance-card">
    <div><b>最近一轮</b><span>${escHtml(formatHealthTime(last.at))}</span></div>
    <div><b>触发范围</b><span>${escHtml(folderRenameAutoScopeText(last))}</span></div>
    <div><b>数据库备份</b><span>${last.backup ? escHtml(last.backup) : '本轮未创建备份'}</span></div>
  </div>`;
  const metricsHtml = `<div class="folder-rename-auto-metrics move-summary">
    <div class="maintenance-card action-card metric-card metric-ok"><strong>${actions.length}</strong><span>执行</span></div>
    <div class="maintenance-card action-card metric-card metric-info"><strong>${skippedActions}</strong><span>跳过</span></div>
    <div class="maintenance-card action-card metric-card metric-danger"><strong>${failed.length}</strong><span>失败</span></div>
  </div>`;
  const folderExecutedActions = actions.filter(action => action.kind !== 'tagged_file');
  const taggedFileExecutedActions = actions.filter(action => action.kind === 'tagged_file');
  const folderExecutedItems = folderExecutedActions.map(action => renderFolderRenameAutoAction(action, 'executed'));
  const taggedFileExecutedItems = taggedFileExecutedActions.map(action => renderFolderRenameAutoAction(action, 'executed'));
  const folderExecutedHtml = folderExecutedItems.join('');
  const taggedFileExecutedHtml = taggedFileExecutedItems.join('');
  const skippedHtml = skipped.map(group => renderFolderRenameAutoSkippedGroup(group)).join('');
  const failedHtml = failed.map(action => renderFolderRenameAutoAction(action, 'failed')).join('');
  const errorsHtml = errors.length
    ? renderFolderRenameAutoSection(
        '其它状态',
        errors.length,
        errors.map(error => `<div class="folder-rename-auto-errors">${escHtml(folderRenameAutoErrorText(error))}</div>`).join(''),
        '没有其它错误'
      )
    : '';
  list.innerHTML = [
    runMeta,
    metricsHtml,
    renderFolderRenameAutoSection('文件夹预案执行', folderExecutedActions.length, folderExecutedHtml, '本轮没有执行文件夹预案', {collapsibleKey: 'folder-executed', collapsibleItems: folderExecutedItems}),
    renderFolderRenameAutoSection('已标签文件归位', taggedFileExecutedActions.length, taggedFileExecutedHtml, '本轮没有已标签文件归位', {collapsibleKey: 'tagged-file-executed', collapsibleItems: taggedFileExecutedItems, title: '移动带标签的散落文件到日期标签文件夹'}),
    renderFolderRenameAutoSection('已跳过', skippedActions, skippedHtml, '本轮没有跳过项目'),
    renderFolderRenameAutoSection('失败', failed.length, failedHtml, '本轮没有失败项目'),
    errorsHtml,
  ].filter(Boolean).join('');
  bindFolderRenameAutoActions();
}

async function loadFolderRenameAutoStatus(options = {}) {
  const render = options.render !== false;
  const updateState = options.updateState !== false;
  try {
    const status = await API.get('/api/folder-renames/auto');
    if (updateState) state.folderRenameAuto = status;
    return status;
  } catch (e) {
    const status = {enabled: false, last_run: null, error: e.message || String(e)};
    if (updateState) state.folderRenameAuto = status;
    return status;
  } finally {
    if (render) {
      renderFolderRenameAutoStatus();
      renderFolderRenameAutoDetails();
    }
  }
}

async function setFolderRenameAutoEnabled(enabled) {
  const previous = state.folderRenameAuto;
  state.folderRenameAuto = {...(previous || {}), enabled, saving: true};
  renderFolderRenameAutoStatus();
  renderFolderRenameAutoDetails();
  try {
    const status = await API.putJson('/api/folder-renames/auto', {enabled});
    state.folderRenameAuto = status;
    renderFolderRenameAutoStatus();
    renderFolderRenameAutoDetails();
    toast(enabled ? '自动归档已开启' : '自动归档已关闭', 'success');
  } catch (e) {
    state.folderRenameAuto = previous || {enabled: !enabled, last_run: null};
    renderFolderRenameAutoStatus();
    renderFolderRenameAutoDetails();
    toast('自动归档设置失败: ' + (e.message || e), 'error');
  }
}

async function runFolderRenameForCurrentArtist() {
  if (!state.currentArtist || state.folderRenameAutoRunningArtist || isActionBusy('folder-rename-run-artist', state.currentArtist.id)) return;
  const artistId = state.currentArtist.id;
  setActionBusy('folder-rename-run-artist', artistId, true);
  state.folderRenameAutoRunningArtist = true;
  renderFolderRenameAutoStatus();
  renderFolderRenameAutoDetails();
  try {
    const result = await API.post(`/api/folder-renames/auto/run?artist_id=${artistId}`);
    state.folderRenameAuto = {...(state.folderRenameAuto || {enabled: false}), last_run: result};
    renderFolderRenameAutoStatus();
    renderFolderRenameAutoDetails();
    await loadMoveWorkbench({preserveScroll: true});
    const executed = Number(result.executed_count || 0);
    const skipped = Number(result.skipped_count || 0);
    const failed = Number(result.failed_count || 0);
    toast(`当前画师归档执行完成：执行 ${executed}，跳过 ${skipped}，失败 ${failed}`, failed ? 'error' : 'success');
  } catch (e) {
    toast('当前画师归档执行失败: ' + (e.message || e), 'error');
  } finally {
    setActionBusy('folder-rename-run-artist', artistId, false);
    state.folderRenameAutoRunningArtist = false;
    renderFolderRenameAutoStatus();
    renderFolderRenameAutoDetails();
  }
}

function bindFolderRenameAutoActions() {
  $$('#folderRenameAutoDetailList [data-folder-rename-auto-toggle]').forEach(btn => {
    btn.addEventListener('click', () => toggleFolderRenameAutoSection(btn.dataset.folderRenameAutoToggle));
  });
  $$('#folderRenameAutoDetailList [data-folder-rename-auto-recheck]').forEach(btn => {
    btn.addEventListener('click', () => handleFolderRenameAutoSkippedAction(btn.dataset.folderRenameAutoRecheck, 'recheck'));
  });
  $$('#folderRenameAutoDetailList [data-folder-rename-auto-reconfirm]').forEach(btn => {
    btn.addEventListener('click', () => handleFolderRenameAutoSkippedAction(btn.dataset.folderRenameAutoReconfirm, 'reconfirm'));
  });
  $$('#folderRenameAutoDetailList [data-folder-rename-auto-unconfirm]').forEach(btn => {
    btn.addEventListener('click', () => handleFolderRenameAutoSkippedAction(btn.dataset.folderRenameAutoUnconfirm, 'unconfirm'));
  });
}

async function handleFolderRenameAutoSkippedAction(planId, operation) {
  if (!planId || state.folderRenameAutoPlanBusy.has(String(planId)) || isActionBusy('folder-rename-plan-action', `${operation}:${planId}`)) return;
  const key = String(planId);
  const operations = {
    recheck: {
      path: `/api/folder-renames/plans/${planId}/recheck`,
      pending: '正在重新检查预案...',
      success: result => result.status === 'ready' ? '检查通过，等待下次扫描自动执行' : '检查完成，仍需处理',
    },
    reconfirm: {
      path: `/api/folder-renames/plans/${planId}/reconfirm`,
      pending: '正在重新确认预案...',
      success: result => (result.check || {}).status === 'ready' ? '已重新确认，当前可自动执行' : '已重新确认，仍需处理',
    },
    unconfirm: {
      path: `/api/folder-renames/plans/${planId}/unconfirm`,
      pending: '正在取消确认...',
      success: () => '已取消确认，预案回到待确认页',
    },
  };
  const config = operations[operation];
  if (!config) return;
  setActionBusy('folder-rename-plan-action', `${operation}:${planId}`, true);
  state.folderRenameAutoPlanBusy.add(key);
  toast(config.pending, 'success');
  renderFolderRenameAutoDetails();
  try {
    const result = await API.post(config.path);
    if (operation === 'reconfirm') {
      state.folderRenameAutoPlanChecks[key] = {...(result.check || {}), operation};
    } else if (operation === 'unconfirm') {
      state.folderRenameAutoPlanChecks[key] = {
        plan_id: Number(planId),
        status: 'draft',
        operation,
      };
    } else {
      state.folderRenameAutoPlanChecks[key] = {...result, operation};
    }
    toast(config.success(result), state.folderRenameAutoPlanChecks[key].status === 'blocked' ? 'error' : 'success');
    await loadMoveWorkbench({preserveScroll: true});
  } catch (e) {
    toast('处理已跳过预案失败: ' + (e.message || e), 'error');
  } finally {
    setActionBusy('folder-rename-plan-action', `${operation}:${planId}`, false);
    state.folderRenameAutoPlanBusy.delete(key);
    renderFolderRenameAutoDetails();
  }
}

async function loadFolderRenamePlans(options = {}) {
  const render = options.render !== false;
  const updateState = options.updateState !== false;
  if (!state.currentArtist) {
    if (updateState) {
      state.folderRenamePlans = null;
      state.folderRenameLoading = false;
    }
    if (render) renderFolderRenamePlans();
    return null;
  }
  if (updateState) state.folderRenameLoading = true;
  if (render) renderFolderRenamePlans();
  try {
    const artistId = state.currentArtist.id;
    const [plans, tags] = await Promise.all([
      API.get(`/api/folder-renames?artist_id=${artistId}`),
      API.get(`/api/tags?artist_id=${artistId}`),
    ]);
    if (updateState) state.folderRenamePlans = plans;
    if (updateState) state.tags = tags || [];
    return updateState ? plans : {plans, tags: tags || []};
  } catch (e) {
    const plans = {error: e.message, groups: []};
    if (updateState) state.folderRenamePlans = plans;
    return updateState ? plans : {plans, tags: []};
  } finally {
    if (updateState) state.folderRenameLoading = false;
    if (render) renderFolderRenamePlans();
  }
}

function folderRenameStatusLabel(status) {
  const labels = {
    manual_review: '需要人工处理',
    needs_tags: '需要标签',
    ready: '可确认',
    conflict: '有冲突',
    confirmed: '已确认',
  };
  return labels[status] || status || '未知';
}

function folderRenameSelectedIds(source) {
  return (source.selected_tags || []).map(tag => Number(tag.id)).filter(Boolean);
}

function folderRenameTagOptions(source) {
  const byId = new Map();
  (source.folder_tags || []).forEach(tag => {
    if (tag && tag.id) byId.set(Number(tag.id), tag);
  });
  (source.selected_tags || []).forEach(tag => {
    if (tag && tag.id) byId.set(Number(tag.id), tag);
  });
  return [...byId.values()];
}

function folderRenameWarningText(warnings) {
  const labels = {
    date_not_found: '没有完整日期',
    multiple_dates: '包含多个日期，已取第一个',
  };
  return joinUiMeta((warnings || []).map(warning => labels[warning] || warning));
}

function renderFolderRenameSplitPreview(source, options = {}) {
  const preview = source.split_preview || {};
  const targets = preview.targets || [];
  if (!preview.available || !targets.length) return '';
  const targetRows = targets.map(target => {
    const detail = joinUiMeta([
      `${target.file_count || 0} 个文件`,
      target.archive_count ? `${target.archive_count} 个压缩包` : '',
      target.untagged_file_count ? `${target.untagged_file_count} 个无标签` : '',
    ]);
    return `<div class="folder-rename-split-target">
      <code>${escHtml(target.target_folder)}</code>
      <span>${escHtml(detail)}</span>
    </div>`;
  }).join('');
  const disabled = source.status === 'manual_review' ? 'disabled' : '';
  const action = options.readOnly
    ? '<span class="folder-rename-badge">拆分确认</span>'
    : `<button type="button" class="primary" data-folder-rename-split="${escHtml(source.source_folder)}" ${disabled}>确认拆分预案</button>`;
  return `<div class="folder-rename-split">
    <div class="folder-rename-split-head">
      <span>按标签拆分</span>
      ${action}
    </div>
    <div class="folder-rename-split-targets">${targetRows}</div>
  </div>`;
}

function folderRenameMergeStatus(statuses) {
  const priority = ['conflict', 'manual_review', 'needs_tags', 'ready', 'confirmed'];
  return priority.find(status => statuses.includes(status)) || 'manual_review';
}

function folderRenameSourceCount(groups) {
  return groups.reduce((total, group) => total + (group.sources || []).length, 0);
}

function folderRenameFilteredGroups(groups, predicate) {
  return (groups || []).map(group => {
    const sources = (group.sources || []).filter(predicate);
    if (!sources.length) return null;
    return {
      ...group,
      sources,
      status: folderRenameMergeStatus(sources.map(source => source.status)),
      file_count: sources.reduce((total, source) => total + Number(source.file_count || 0), 0),
      total_size: sources.reduce((total, source) => total + Number(source.total_size || 0), 0),
      max_mtime: Math.max(...sources.map(source => Number(source.max_mtime || 0))),
    };
  }).filter(Boolean);
}

function folderRenameConfirmationBucket(source) {
  const value = source.confirmation_source || '';
  if (['manual', 'auto', 'split'].includes(value)) return value;
  if (source.plan_kind === 'split_by_tag') return 'split';
  return 'unknown';
}

function renderFolderRenameTagControls(source, options = {}) {
  const selectedIds = new Set(folderRenameSelectedIds(source));
  const tags = folderRenameTagOptions(source);
  if (!tags.length) return '<span class="folder-rename-muted">文件夹内暂无标签</span>';
  return tags.map(tag => {
    const selected = selectedIds.has(Number(tag.id));
    if (options.readOnly) {
      return `<span class="folder-rename-tag readonly${selected ? ' selected' : ''}">${escHtml(tag.name)}</span>`;
    }
    return `<button type="button" class="folder-rename-tag${selected ? ' selected' : ''}" data-folder-rename-tag="${tag.id}" data-source-folder="${escHtml(source.source_folder)}">${escHtml(tag.name)}</button>`;
  }).join('');
}

function renderFolderRenameSource(group, source, options = {}) {
  const selectedIds = new Set(folderRenameSelectedIds(source));
  const tagControls = renderFolderRenameTagControls(source, options);
  const splitHtml = renderFolderRenameSplitPreview(source, options);
  const canConfirm = source.parsed_date && selectedIds.size > 0 && group.status !== 'conflict' && source.status !== 'manual_review';
  const actions = options.readOnly ? '' : `<div class="folder-rename-row-actions">
    <button type="button" data-folder-rename-save="${escHtml(source.source_folder)}">保存预案</button>
    <button type="button" class="primary" data-folder-rename-confirm="${escHtml(source.source_folder)}" ${canConfirm ? '' : 'disabled'}>确认预案</button>
  </div>`;
  return `<div class="folder-rename-source" data-source-folder="${escHtml(source.source_folder)}">
    <div class="folder-rename-source-head">
      <code title="${escHtml(source.source_folder)}">${escHtml(source.source_folder)}</code>
      <span>${escHtml(folderRenameStatusLabel(source.status))}</span>
    </div>
    <div class="folder-rename-meta">
      <span>${source.file_count || 0} 个文件</span>
      ${source.archive_count ? `<span>${source.archive_count} 个压缩包</span>` : ''}
      ${source.untagged_file_count ? `<span>${source.untagged_file_count} 个无标签</span>` : ''}
      <span>${formatBytes(source.total_size || 0)}</span>
      <span>${source.parsed_date ? escHtml(source.parsed_date) : '无完整日期'}</span>
    </div>
    <div class="folder-rename-tags">${tagControls}</div>
    ${splitHtml}
    ${actions}
  </div>`;
}

function renderFolderRenameGroup(group, options = {}) {
  const conflicts = group.conflicts || [];
  const warningText = folderRenameWarningText(group.warnings);
  const conflictHtml = conflicts.length
    ? `<div class="folder-rename-warning">${conflicts.map(c => `${escHtml(c.relative_path)}: ${escHtml(c.reason)}`).join('<br>')}</div>`
    : '';
  const sources = (group.sources || []).map(source => renderFolderRenameSource(group, source, options)).join('');
  return `<div class="folder-rename-group status-${escHtml(group.status)}">
    <div class="folder-rename-group-head">
      <div>
        <b>${group.target_name ? escHtml(group.target_name) : '待生成目标名'}</b>
        <span>${escHtml(joinUiMeta([folderRenameStatusLabel(group.status), warningText]))}</span>
      </div>
      <em>${escHtml(joinUiMeta([`${group.file_count || 0} 个文件`, formatBytes(group.total_size || 0)]))}</em>
    </div>
    ${conflictHtml}
    ${sources}
  </div>`;
}

function renderFolderRenameGroupList(groups, emptyText, options = {}) {
  if (!groups.length) return `<div class="move-empty small">${escHtml(emptyText)}</div>`;
  return groups.map(group => renderFolderRenameGroup(group, options)).join('');
}

function renderFolderRenameConfirmedSections(groups) {
  if (!groups.length) return '<div class="move-empty small">没有已确认归档预案</div>';
  const sections = [
    {key: 'manual', title: '人工确认', detail: '你在页面上确认的单目标预案'},
    {key: 'auto', title: '自动确认', detail: '文件夹内标签一致后自动确认的预案'},
    {key: 'split', title: '拆分确认', detail: '按多个标签拆分到不同目标文件夹的预案'},
    {key: 'unknown', title: '来源未记录', detail: '早期版本保存的已确认预案'},
  ];
  return sections.map(section => {
    const sectionGroups = folderRenameFilteredGroups(groups, source => folderRenameConfirmationBucket(source) === section.key);
    if (!sectionGroups.length) return '';
    return `<div class="folder-rename-confirmed-section">
      <div class="folder-rename-confirmed-head">
        <div>
          <b>${section.title}</b>
          <span>${section.detail}</span>
        </div>
        <em>${folderRenameSourceCount(sectionGroups)} 个源文件夹</em>
      </div>
      ${renderFolderRenameGroupList(sectionGroups, '', {readOnly: true})}
    </div>`;
  }).join('') || '<div class="move-empty small">没有已确认归档预案</div>';
}

function renderFolderRenameLeftovers(history) {
  const entries = (history || []).filter(entry => (entry.leftover_files || []).length);
  if (!entries.length) return '';
  const rows = entries.map(entry => {
    const files = (entry.leftover_files || []).slice(0, 8).map(file => `
      <div class="folder-rename-leftover-file">
        <code title="${escHtml(file.relative_path || '')}">${escHtml(file.relative_path || '')}</code>
        <span>${escHtml(formatBytes(file.size || 0))}</span>
      </div>
    `).join('');
    const more = Number(entry.leftover_count || 0) > (entry.leftover_files || []).length
      ? `<div class="folder-rename-leftover-more">还有 ${Number(entry.leftover_count) - (entry.leftover_files || []).length} 个文件未列出</div>`
      : '';
    const targets = (entry.target_folders || []).length ? joinUiMeta(entry.target_folders || []) : (entry.target_folder || '多个目标');
    return `<div class="folder-rename-leftover-entry">
      <div class="folder-rename-leftover-head">
        <div>
          <b>${escHtml(entry.source_folder || '已执行源文件夹')}</b>
          <span>目标：${escHtml(targets)}</span>
        </div>
        <em>${Number(entry.leftover_count || 0)} 个未纳入索引</em>
      </div>
      <div class="folder-rename-leftover-files">${files}${more}</div>
    </div>`;
  }).join('');
  return `<div class="folder-rename-leftovers">
    <div class="folder-rename-leftovers-title">
      <b>归档残留</b>
      <span>这些文件留在已执行源目录里，当前不会自动移动或删除。</span>
    </div>
    ${rows}
  </div>`;
}

function renderFolderRenamePlans() {
  const summary = $('#folderRenameSummary');
  const list = $('#folderRenameGroupList');
  const confirmedSummary = $('#folderRenameConfirmedSummary');
  const confirmedList = $('#folderRenameConfirmedGroupList');
  const leftoverList = $('#folderRenameLeftoverList');
  if (!summary || !list || !confirmedSummary || !confirmedList || !leftoverList) return;
  if (!state.currentArtist) {
    summary.textContent = '请选择画师后读取文件夹归档预案';
    list.innerHTML = '<div class="move-empty small">未选择画师</div>';
    confirmedSummary.textContent = '请选择画师后查看已确认预案';
    confirmedList.innerHTML = '<div class="move-empty small">未选择画师</div>';
    leftoverList.innerHTML = '';
    return;
  }
  if (state.folderRenameLoading) {
    summary.textContent = '正在读取文件夹归档预案...';
    list.innerHTML = '<div class="move-empty small">加载中...</div>';
    confirmedSummary.textContent = '正在读取已确认预案...';
    confirmedList.innerHTML = '<div class="move-empty small">加载中...</div>';
    leftoverList.innerHTML = '';
    return;
  }
  const plans = state.folderRenamePlans;
  if (!plans) {
    summary.textContent = '尚未读取预案';
    list.innerHTML = '<div class="move-empty small">等待读取</div>';
    confirmedSummary.textContent = '尚未读取已确认预案';
    confirmedList.innerHTML = '<div class="move-empty small">等待读取</div>';
    leftoverList.innerHTML = '';
    return;
  }
  if (plans.error) {
    summary.textContent = '预案读取失败';
    list.innerHTML = `<div class="move-empty small">${escHtml(plans.error)}</div>`;
    confirmedSummary.textContent = '已确认预案读取失败';
    confirmedList.innerHTML = `<div class="move-empty small">${escHtml(plans.error)}</div>`;
    leftoverList.innerHTML = '';
    return;
  }
  const groups = plans.groups || [];
  const leftoversHtml = renderFolderRenameLeftovers(plans.execution_history || []);
  if (!groups.length) {
    summary.textContent = joinUiMeta(['0 个待处理源文件夹', '0 个目标组']);
    list.innerHTML = '<div class="move-empty small">没有可规划的顶层作品文件夹</div>';
    confirmedSummary.textContent = joinUiMeta(['0 个已确认源文件夹', '0 个目标组']);
    confirmedList.innerHTML = '<div class="move-empty small">没有已确认归档预案</div>';
    leftoverList.innerHTML = leftoversHtml;
    return;
  }
  const pendingGroups = folderRenameFilteredGroups(groups, source => !source.is_confirmed_plan);
  const confirmedGroups = folderRenameFilteredGroups(groups, source => source.is_confirmed_plan);
  summary.textContent = joinUiMeta([`${folderRenameSourceCount(pendingGroups)} 个待处理源文件夹`, `${pendingGroups.length} 个目标组`]);
  list.innerHTML = renderFolderRenameGroupList(pendingGroups, '没有待处理预案；已确认的在“已确认归档”页');
  confirmedSummary.textContent = joinUiMeta([`${folderRenameSourceCount(confirmedGroups)} 个已确认源文件夹`, `${confirmedGroups.length} 个目标组`]);
  confirmedList.innerHTML = renderFolderRenameConfirmedSections(confirmedGroups);
  leftoverList.innerHTML = leftoversHtml;
  bindFolderRenameActions();
}

function selectedFolderRenameTagIds(sourceFolder) {
  const source = $(`.folder-rename-source[data-source-folder="${cssEscape(sourceFolder)}"]`);
  if (!source) return [];
  return [...source.querySelectorAll('.folder-rename-tag.selected')]
    .map(btn => Number(btn.dataset.folderRenameTag))
    .filter(Boolean);
}

function cssEscape(value) {
  if (window.CSS && CSS.escape) return CSS.escape(value);
  return String(value || '').replace(/"/g, '\\"');
}

function bindFolderRenameActions() {
  $$('#folderRenameGroupList [data-folder-rename-tag]').forEach(btn => {
    btn.addEventListener('click', () => {
      const source = btn.closest('.folder-rename-source');
      if (!source) return;
      btn.classList.toggle('selected');
    });
  });
  $$('#folderRenameGroupList [data-folder-rename-save]').forEach(btn => {
    btn.addEventListener('click', () => saveFolderRenamePlan(btn.dataset.folderRenameSave, 'draft'));
  });
  $$('#folderRenameGroupList [data-folder-rename-confirm]').forEach(btn => {
    btn.addEventListener('click', () => saveFolderRenamePlan(btn.dataset.folderRenameConfirm, 'confirmed'));
  });
  $$('#folderRenameGroupList [data-folder-rename-split]').forEach(btn => {
    btn.addEventListener('click', () => saveFolderRenameSplitPlan(btn.dataset.folderRenameSplit, 'confirmed'));
  });
}

async function saveFolderRenamePlan(sourceFolder, status = 'draft') {
  if (!state.currentArtist || !sourceFolder) return;
  if (isActionBusy('folder-rename-save', sourceFolder)) return;
  const selected_tag_ids = selectedFolderRenameTagIds(sourceFolder);
  if (!selected_tag_ids.length) {
    toast('请先选择标签', 'error');
    return;
  }
  setActionBusy('folder-rename-save', sourceFolder, true);
  try {
    await API.putJson('/api/folder-renames', {
      artist_id: state.currentArtist.id,
      source_folder: sourceFolder,
      selected_tag_ids,
      plan_kind: 'rename_folder',
      status,
    });
    toast(status === 'confirmed' ? '预案已确认' : '预案已保存', 'success');
    await loadFolderRenamePlans();
  } catch (e) {
    toast('保存预案失败: ' + (e.message || e), 'error');
  } finally {
    setActionBusy('folder-rename-save', sourceFolder, false);
  }
}

async function saveFolderRenameSplitPlan(sourceFolder, status = 'confirmed') {
  if (!state.currentArtist || !sourceFolder) return;
  if (isActionBusy('folder-rename-save', sourceFolder)) return;
  setActionBusy('folder-rename-save', sourceFolder, true);
  try {
    await API.putJson('/api/folder-renames', {
      artist_id: state.currentArtist.id,
      source_folder: sourceFolder,
      selected_tag_ids: [],
      plan_kind: 'split_by_tag',
      status,
    });
    toast('拆分预案已确认', 'success');
    await loadFolderRenamePlans();
  } catch (e) {
    toast('保存拆分预案失败: ' + (e.message || e), 'error');
  } finally {
    setActionBusy('folder-rename-save', sourceFolder, false);
  }
}

function formatBytes(bytes) {
  const value = Number(bytes || 0);
  if (value >= 1024 * 1024 * 1024) return `${(value / 1024 / 1024 / 1024).toFixed(1)} GB`;
  if (value >= 1024 * 1024) return `${(value / 1024 / 1024).toFixed(1)} MB`;
  if (value >= 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${value} B`;
}

function formatHealthTime(timestamp) {
  if (!timestamp) return '无记录';
  const date = new Date(Number(timestamp) * 1000);
  if (Number.isNaN(date.getTime())) return '无记录';
  return date.toLocaleString();
}

function operationLogKindLabel(operation) {
  if (operation.reason === 'tagged_file') return '已标签文件归位';
  if (operation.kind === 'folder_rename') return '文件夹归档';
  if (operation.kind === 'move') return '路径变更';
  return '操作';
}

function operationLogReasonLabel(reason) {
  const labels = {
    tagged_file: '已标签文件归位',
  };
  return labels[reason] || reason || '';
}

function operationLogStatusLabel(status) {
  const labels = {
    applied: '已确认',
    executed: '已执行',
    preview: '自动确认',
    new: '新文件',
    ignored: '已忽略',
    failed: '失败',
  };
  return labels[status] || status || '已记录';
}

function renderOperationLog() {
  const summary = $('#operationLogSummary');
  const historyList = $('#operationHistoryList');
  const errorList = $('#operationErrorList');
  if (!summary || !historyList || !errorList) return;
  const log = state.operationLog;
  if (!log) {
    summary.textContent = '等待读取历史';
    historyList.innerHTML = '<div class="move-empty small">等待读取历史</div>';
    errorList.innerHTML = '<div class="move-empty small">等待读取错误</div>';
    return;
  }
  if (log.error) {
    summary.textContent = '读取历史失败';
    historyList.innerHTML = `<div class="operation-error">${escHtml(log.error)}</div>`;
    errorList.innerHTML = '<div class="move-empty small">没有可显示的错误</div>';
    return;
  }
  const history = log.history || [];
  const errors = log.errors || [];
  summary.textContent = joinUiMeta([`${history.length} 条移动/重命名历史`, `${errors.length} 条最近错误`]);
  historyList.innerHTML = history.length ? history.map(operation => {
    const source = operation.display_source || operation.source || '';
    const target = operation.display_target || operation.target || '';
    const kindClass = operation.kind === 'folder_rename'
      ? 'rename'
      : (operation.kind === 'move' ? 'move' : 'other');
    return `
      <div class="operation-entry ${kindClass}">
        <div class="operation-entry-head">
          <b>${escHtml(operationLogKindLabel(operation))}</b>
          <span>${escHtml(operationLogStatusLabel(operation.status))}</span>
          <em>${escHtml(formatHealthTime(operation.at))}</em>
        </div>
        <div class="operation-entry-meta">
          <span>${escHtml(operation.artist_name || '未知画师')}</span>
          <span>${operation.updated_items || 0} 项</span>
          <span>${escHtml(operationLogReasonLabel(operation.reason))}</span>
        </div>
        <div class="operation-path"><span>原</span><code title="${escHtml(operation.source || source)}">${escHtml(source || '-')}</code></div>
        <div class="operation-path"><span>新</span><code title="${escHtml(operation.target || target)}">${escHtml(target || '-')}</code></div>
      </div>
    `;
  }).join('') : '<div class="move-empty small">暂无移动或重命名历史</div>';
  errorList.innerHTML = errors.length ? errors.map(row => `
    <div class="operation-error">
      <b>${escHtml(row.source || 'log')}</b>
      <code>${escHtml(row.line || '')}</code>
    </div>
  `).join('') : '<div class="move-empty small">最近没有错误记录</div>';
}

function formatHealthScanStatus(scan) {
  const statusLabels = {
    idle: '空闲',
    scanning: '扫描中',
    error: '出错',
    stopping: '正在停止',
  };
  const phaseLabels = {
    complete: '已完成',
    discover: '发现目录',
    scan: '扫描文件',
    parse: '整理记录',
    stopped: '已停止',
    interrupted: '已中断',
  };
  const status = statusLabels[scan.status] || '未知状态';
  const phase = phaseLabels[scan.phase] || '';
  const showCount = Number(scan.total_estimate || 0) > 0
    && (scan.status === 'scanning' || scan.phase === 'complete');
  const count = showCount ? `${scan.scanned_count || 0}/${scan.total_estimate || 0}` : '';
  return joinUiMeta([status, phase, count]);
}

function formatHealthHashStatus(hashItems) {
  const remaining = Number(hashItems.remaining || 0);
  const errors = Number(hashItems.error || 0);
  if (remaining === 0 && errors === 0) return joinUiMeta(['已完成', '无错误']);
  const parts = [remaining ? `剩余 ${remaining}` : '已完成'];
  parts.push(errors ? `错误 ${errors}` : '无错误');
  return joinUiMeta(parts);
}

function formatHealthSchedule(schedule, nextKey) {
  if (!schedule || !schedule.enabled) return '未启用';
  const nextAt = schedule[nextKey];
  const intervalHours = schedule.interval ? Math.round(schedule.interval / 3600 * 10) / 10 : 0;
  if (!nextAt) return intervalHours ? joinUiMeta([`每 ${intervalHours} 小时`, '等待排期']) : '等待排期';
  const prefix = schedule.overdue ? '已到时间' : `下次 ${formatHealthTime(nextAt)}`;
  const suffix = schedule.deferred_by_manual ? '手动后顺延' : '';
  return intervalHours ? joinUiMeta([prefix, `每 ${intervalHours} 小时`, suffix]) : joinUiMeta([prefix, suffix]);
}

function renderHealthSummary() {
  const grid = $('#healthGrid');
  if (!grid) return;
  const health = state.healthSummary;
  if (!health) {
    grid.innerHTML = '<div class="maintenance-card status-card status-muted move-empty small">等待读取健康状态</div>';
    return;
  }
  if (health.error) {
    grid.innerHTML = `<div class="maintenance-card status-card status-danger"><b>只读检查失败</b><span>${escHtml(health.error)}</span></div>`;
    return;
  }
  const database = health.database || {};
  const backups = health.backups || {};
  const latestBackup = backups.latest || null;
  const logs = health.logs || {};
  const galleryLog = logs.gallery_log || {};
  const uiLog = logs.ui_actions_log || {};
  const scan = health.scan || {};
  const scanSchedule = health.scan_schedule || {};
  const backupSchedule = health.backup_schedule || {};
  const hash = health.hash || {};
  const hashItems = hash.items || {};
  const errors = health.recent_errors || [];
  const databaseStatus = database.exists ? 'status-ok' : 'status-danger';
  const backupStatus = backupSchedule.last_error ? 'status-danger' : (latestBackup ? 'status-ok' : 'status-warn');
  const scanStatus = scan.status === 'error' ? 'status-danger' : (scan.phase === 'interrupted' ? 'status-warn' : (scan.status === 'scanning' ? 'status-info' : 'status-ok'));
  const hashStatus = Number(hashItems.error || 0) > 0 ? 'status-warn' : 'status-ok';
  const logStatus = (galleryLog.exists === false && uiLog.exists === false) ? 'status-warn' : 'status-ok';
  const errorStatus = errors.length ? 'status-warn' : 'status-ok';
  const errorHtml = errors.length
    ? errors.slice(0, 3).map(row => `<code>${escHtml(row.source || '')}: ${escHtml(row.line || '')}</code>`).join('')
    : '<span>最近没有错误记录</span>';
  grid.innerHTML = `
    <div class="maintenance-card status-card ${databaseStatus}"><b>数据库</b><span>${escHtml(joinUiMeta([database.exists ? '文件可读' : '文件不存在', formatBytes(database.size_bytes)]))}</span></div>
    <div class="maintenance-card status-card ${backupStatus}"><b>最近备份</b><span>${escHtml(joinUiMeta([latestBackup ? latestBackup.name : '无备份', `共 ${backups.count || 0} 个`, latestBackup ? formatBytes(latestBackup.size_bytes) : '无大小']))}</span><span class="health-schedule">${escHtml(formatHealthSchedule(backupSchedule, 'next_run_at'))}</span></div>
    <div class="maintenance-card status-card ${scanStatus}"><b>扫描</b><span>${escHtml(formatHealthScanStatus(scan))}</span><span class="health-schedule">${escHtml(formatHealthSchedule(scanSchedule, 'next_auto_scan_at'))}</span></div>
    <div class="maintenance-card status-card ${hashStatus}"><b>相同文件检查</b><span>${escHtml(formatHealthHashStatus(hashItems))}</span></div>
    <div class="maintenance-card status-card ${logStatus}"><b>日志</b><span>${escHtml(joinUiMeta([`共 ${formatBytes((galleryLog.size_bytes || 0) + (uiLog.size_bytes || 0))}`, `应用 ${formatBytes(galleryLog.size_bytes)}`, `浏览器 ${formatBytes(uiLog.size_bytes)}`]))}</span></div>
    <div class="maintenance-card status-card ${errorStatus}"><b>最近错误</b><span>${errorHtml}</span></div>
  `;
  renderHashStatus();
}

function renderHashStatus() {
  const status = state.hashStatus;
  if (!status) {
    $('#hashStatusText').textContent = '等待检查';
    return;
  }
  if (status.database_error) {
    $('#hashStatusText').textContent = '数据出错';
    return;
  }
  if (!status.blake3_available) {
    $('#hashStatusText').textContent = '暂时不能检查';
    return;
  }
  const items = status.items || {};
  const candidates = status.scan_candidates || {};
  const remaining = (items.remaining || 0) + (candidates.remaining || 0);
  $('#hashStatusText').textContent = formatHashWorkerStatus(status.worker || {}, remaining);
}

function formatHashWorkerStatus(worker, remaining) {
  if (worker.last_error) return '数据出错';
  if (remaining <= 0) return '检查完成';
  if (worker.thread_alive) return joinUiMeta(['正在检查', `还剩 ${remaining}`]);
  return joinUiMeta(['等待检查', `还剩 ${remaining}`]);
}

async function startFullScan(event) {
  const source = event?.currentTarget?.id === 'emptyScanBtn' ? 'empty' : 'header';
  if (isActionBusy('scan-full')) return;
  setActionBusy('scan-full', '', true);
  logUiAction('scan_start_click', {source});
  try {
    const r = await API.post('/api/scan');
    if (r.ok) {
      state.scanRunning = true;
      state.lastScanState = {status:'scanning', phase:'discover', scanned_count:0, total_estimate:0, current_path:''};
      renderLibraryEmptyState();
      toast('扫描已启动', 'success');
    } else {
      toast(r.message || '扫描已在运行', 'error');
    }
  } catch (e) {
    toast('启动扫描失败', 'error');
  } finally {
    setActionBusy('scan-full', '', false);
  }
}

function renderMoveCandidates() {
  const list = $('#moveCandidateList');
  const candidates = state.moveCandidates || [];
  if (candidates.length === 0) {
    list.innerHTML = '<div class="move-empty">没有待确认路径</div>';
    return;
  }

  list.innerHTML = candidates.map(c => {
    const label = moveReasonLabel(c.reason);
    const isManual = c.reason === 'manual_needed';
    const oldPath = c.display_old_path || c.old_path || '无旧路径';
    const newPath = c.display_new_path || c.new_path || '';
    const oldArtistPath = c.display_item_artist_path || c.item_artist_path || '';
    const newArtistPath = c.display_candidate_artist_path || c.candidate_artist_path || '';
    const isCrossArtist = Boolean(c.is_cross_artist);
    const cannotConfirm = c.can_confirm === false || isCrossArtist;
    const warning = isCrossArtist
      ? '<div class="move-warning">同名画师，不同目录根；跨画师路径变化暂不能直接确认。请核对旧路径和新路径后选择“当作新文件”或“忽略”。</div>'
      : (isManual ? '<div class="move-warning">需要手动核对旧路径和新路径。</div>' : '');
    const artistPaths = isCrossArtist ? `
        <div class="move-artist-paths">
          <div><span>旧画师</span><code title="${escHtml(c.item_artist_path || oldArtistPath)}">${escHtml(oldArtistPath || '-')}</code></div>
          <div><span>新画师</span><code title="${escHtml(c.candidate_artist_path || newArtistPath)}">${escHtml(newArtistPath || '-')}</code></div>
        </div>` : '';
    return `<div class="move-card" data-id="${c.id}">
      <div class="move-card-main">
        <div class="move-card-top">
          <span class="move-reason">${escHtml(label)}</span>
          <span class="move-id">#${c.id}</span>
        </div>
        <div class="move-path old"><span>旧</span><code title="${escHtml(c.old_path || oldPath)}">${escHtml(oldPath)}</code></div>
        <div class="move-path new"><span>新</span><code title="${escHtml(c.new_path || newPath)}">${escHtml(newPath)}</code></div>
        ${warning}
        ${artistPaths}
        <div class="move-meta">
          <span>记录 ${c.item_id || '-'}</span>
          <span>检查码 ${shortHash(c.content_hash)}</span>
          <span>${c.content_hash ? '哈希已匹配' : '哈希未完成'}</span>
          <span>文件标记 ${c.st_dev || '-'}:${c.st_ino || '-'}</span>
        </div>
      </div>
      <div class="move-actions">
        <button class="primary" data-move-action="confirm" data-id="${c.id}" ${cannotConfirm ? 'disabled' : ''}>确认同一文件</button>
        <button data-move-action="new" data-id="${c.id}">当作新文件</button>
        <button class="danger" data-move-action="ignore" data-id="${c.id}">忽略</button>
      </div>
    </div>`;
  }).join('');
  bindMoveActions();
}

function renderMoveHistory() {
  const list = $('#moveHistoryList');
  const history = state.moveHistory || [];
  if (history.length === 0) {
    list.innerHTML = '<div class="move-empty small">暂无自动确认记录</div>';
    return;
  }
  list.innerHTML = history.slice(0, 40).map(h => {
    const oldPath = h.display_old_path || h.old_path || '';
    const newPath = h.display_new_path || h.new_path || '';
    return `
      <div class="move-history-row">
        <span>${escHtml(moveReasonLabel(h.reason))}</span>
        <code title="${escHtml(h.old_path || oldPath)}">${escHtml(oldPath)}</code>
        <b>&rarr;</b>
        <code title="${escHtml(h.new_path || newPath)}">${escHtml(newPath)}</code>
      </div>
    `;
  }).join('');
}

function bindMoveActions() {
  $$('#moveCandidateList [data-move-action]').forEach(btn => {
    btn.addEventListener('click', async () => {
      await runMoveAction(btn.dataset.id, btn.dataset.moveAction);
    });
  });
}

async function runMoveAction(id, action) {
  const paths = {
    confirm: `/api/move-candidates/${id}/confirm`,
    new: `/api/move-candidates/${id}/new`,
    ignore: `/api/move-candidates/${id}/ignore`,
  };
  const path = paths[action];
  if (!path) return;
  if (isActionBusy('move-action', `${action}:${id}`)) return;
  setActionBusy('move-action', `${action}:${id}`, true);
  try {
    const result = await API.post(path);
    if (result.action === 'blocked' && result.reason === 'cross_artist_manual_needed') {
      toast('跨画师路径变化暂不能直接确认', 'error');
    } else if (result.action === 'moved') {
      toast('路径已确认', 'success');
    } else if (result.action === 'new') {
      toast('已当作新文件', 'success');
    } else if (result.action === 'ignored') {
      toast('已忽略候选', 'success');
    } else {
      toast(result.reason || result.action || '操作完成', 'success');
    }
    await loadMoveWorkbench({preserveScroll: true});
    await loadArtists();
  } catch (e) {
    toast('路径候选操作失败: ' + e.message, 'error');
  } finally {
    setActionBusy('move-action', `${action}:${id}`, false);
  }
}

function moveReasonLabel(reason) {
  const labels = {
    inode_untrusted: '同一文件待确认',
    hash_duplicate_active: '重复文件',
    hash_multiple_missing: '多个旧路径',
    manual_needed: '手动确认',
    inode: '同一文件',
    hash_unique: '只找到一个旧文件',
    category_rename: '目录改名',
  };
  return labels[reason] || reason || '待确认';
}

function shortHash(value) {
  if (!value) return '-';
  return value.length > 12 ? value.slice(0, 12) : value;
}
