const API = {
  async parseResponse(r) {
    let body = {};
    try {
      body = await r.json();
    } catch (e) {
      body = {};
    }
    if (!r.ok) {
      const detail = body.detail || body.message || body.error;
      throw new Error(detail || r.statusText || `HTTP ${r.status}`);
    }
    return body;
  },
  async get(path) {
    const r = await fetch(path);
    return this.parseResponse(r);
  },
  async post(path) {
    const r = await fetch(path, {method:'POST', keepalive:true});
    return this.parseResponse(r);
  },
  async put(path) {
    const r = await fetch(path, {method:'PUT'});
    return this.parseResponse(r);
  },
  async putJson(path, data) {
    const r = await fetch(path, {
      method:'PUT',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify(data || {})
    });
    return this.parseResponse(r);
  },
  async del(path) {
    const r = await fetch(path, {method:'DELETE'});
    return this.parseResponse(r);
  },
  fileUrl(filePath, version) {
    const params = new URLSearchParams({path: filePath});
    if (version) params.set('v', version);
    return '/api/file?' + params.toString();
  },
  previewUrl(filePath, version, maxEdge) {
    const params = new URLSearchParams({path: filePath});
    if (version) params.set('v', version);
    if (maxEdge) params.set('max', String(maxEdge));
    return '/api/file/preview?' + params.toString();
  },
  streamUrl(filePath) {
    return '/api/file/stream?path=' + encodeURIComponent(filePath);
  },
  videoFrameUrl(filePath, version) {
    const params = new URLSearchParams({path: filePath, t: '0.1'});
    if (version) params.set('v', version);
    return '/api/file/video-frame?' + params.toString();
  },
  videoCompatibleUrl(filePath) {
    return '/api/file/video-compatible?path=' + encodeURIComponent(filePath);
  },
  videoHlsUrl(filePath) {
    return '/api/file/video-hls?path=' + encodeURIComponent(filePath);
  },
  videoTranscodeUrl(filePath) {
    return '/api/file/video-transcode?path=' + encodeURIComponent(filePath);
  },
  videoTranscodeStatusUrl(filePath) {
    return '/api/file/video-transcode-status?path=' + encodeURIComponent(filePath);
  },
  videoTranscodedUrl(filePath) {
    return '/api/file/video-transcoded?path=' + encodeURIComponent(filePath);
  }
};

const LIGHTBOX_ZOOM_MIN = 0.5;
const LIGHTBOX_ZOOM_MAX = 4;
const LIGHTBOX_ZOOM_STEP = 0.15;
const LIGHTBOX_DOUBLE_TAP_ZOOM = 2;
const LIGHTBOX_DOUBLE_TAP_DELAY_MS = 320;
const LIGHTBOX_DOUBLE_TAP_DISTANCE_PX = 36;
const LIGHTBOX_WHEEL_NAV_DELAY = 180;
const UI_FIELD_SEPARATOR = ' \u00b7 ';

let state = {
  artists: [],
  currentArtist: null,
  stats: null,
  mode: 'browse',
  view: 'grid',
  maintenanceView: 'overview',
  activeRole: null,
  activeFolder: null,
  search: '',
  searchScope: 'auto',
  searchTarget: 'all',
  searchOptionsOpen: false,
  duplicatesOnly: false,
  scanRunning: false,
  lastScanState: null,
  selectedIds: new Set(),
  allItems: [],
  itemsOffset: 0,
  hasMoreItems: false,
  loadingItems: false,
  loadingMoreItems: false,
  itemLoadSeq: 0,
  artistLoadSeq: 0,
  scanRefreshSeq: 0,
  maintenanceLoadSeq: 0,
  actionBusy: new Set(),
  lightboxIndex: -1,
  lastFocusedBeforeLightbox: null,
  lightboxZoom: 1,
  lightboxPanX: 0,
  lightboxPanY: 0,
  lightboxPanActive: false,
  lightboxPanPointerX: 0,
  lightboxPanPointerY: 0,
  lightboxPanStartX: 0,
  lightboxPanStartY: 0,
  lightboxPointers: new Map(),
  lightboxPinchActive: false,
  lightboxPinchStartDistance: 0,
  lightboxPinchStartZoom: 1,
  lightboxTapPointerId: null,
  lightboxTapStartX: 0,
  lightboxTapStartY: 0,
  lightboxTapMoved: false,
  lightboxLastTapAt: 0,
  lightboxLastTapX: 0,
  lightboxLastTapY: 0,
  lightboxWheelLastAt: 0,
  lightboxLoadToken: 0,
  tags: [],
  tagSearchResults: [],
  editContextArtistId: null,
  editTagContextLoading: false,
  editGlobalTagResults: [],
  editGlobalTagSearchLoading: false,
  selectedEditTagIds: new Set(),
  selectedEditTagNames: new Set(),
  editTagQuery: '',
  folders: null,
  moveCandidates: [],
  moveWaitingHashCount: 0,
  moveHistory: [],
  folderRenamePlans: null,
  folderRenameLoading: false,
  folderRenameAuto: null,
  folderRenameAutoRunningArtist: false,
  folderRenameAutoPlanChecks: {},
  folderRenameAutoPlanBusy: new Set(),
  folderRenameAutoExpandedSections: new Set(),
  hashStatus: null,
  healthSummary: null,
  operationLog: null,
  duplicateFolders: [],
  filterDrawerOpen: false,
  mobileHeaderToolsOpen: false,
  duplicateFoldersOpen: false,
  mobileColumns: 2,
  sidebarWidth: 260,
};

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);
const ITEM_PAGE_LIMIT = 120;
const INFINITE_SCROLL_THRESHOLD = 700;
const IMAGE_PREVIEW_MAX_EDGE = 512;
const LIGHTBOX_VIDEO_FALLBACK_DELAY_MS = 12000;
const SIDEBAR_WIDTH_STORAGE_KEY = 'gallery.sidebarWidthPx';
const SIDEBAR_WIDTH_DEFAULT = 260;
const SIDEBAR_WIDTH_MIN = 180;
const SIDEBAR_WIDTH_MAX = 520;
const MOBILE_COLUMNS_STORAGE_KEY = 'gallery.mobileColumns';
const MOBILE_COLUMNS_DEFAULT = 2;
const MAX_IMAGE_LOADS = 2;
const IMAGE_OBSERVER_ROOT_MARGIN = '480px';
const IMAGE_LOAD_TIMEOUT_MS = 12000;
const MAX_VIDEO_PREVIEW_LOADS = 1;
const VIDEO_PREVIEW_HOVER_DELAY_MS = 250;
const VIDEO_PREVIEW_LOAD_TIMEOUT_MS = 8000;
const MAX_ARTIST_DROPDOWN_RESULTS = 1000;
const FRONTEND_ERROR_LOG_LIMIT = 20;
const FRONTEND_ERROR_DEDUPE_MS = 30000;
const MAINTENANCE_AUTO_REFRESH_MS = 10000;
let activeVideoPreviewLoads = 0;
const pendingVideoPreviews = [];
let videoPreviewObserver = null;
let activeImageLoads = 0;
const pendingImageLoads = [];
let imageObserver = null;
let editTagContextLoadToken = 0;
let editGlobalTagSearchToken = 0;
let frontendErrorLogCount = 0;
let maintenanceAutoRefreshTimer = null;
let maintenanceAutoRefreshInFlight = false;
const frontendErrorLastSeen = new Map();

function nextRequestSeq(name) {
  state[name] = Number(state[name] || 0) + 1;
  return state[name];
}

function isCurrentRequestSeq(name, seq) {
  return Number(state[name] || 0) === Number(seq);
}

function actionBusyKey(name, id = '') {
  return id ? `${name}:${id}` : name;
}

function isActionBusy(name, id = '') {
  return state.actionBusy.has(actionBusyKey(name, id));
}

function setActionBusy(name, id = '', busy = true) {
  const key = actionBusyKey(name, id);
  if (busy) state.actionBusy.add(key);
  else state.actionBusy.delete(key);
}

function logUiAction(event, data = {}) {
  const payload = JSON.stringify({event, data});
  try {
    if (navigator.sendBeacon) {
      const blob = new Blob([payload], {type:'application/json'});
      if (navigator.sendBeacon('/api/ui-log', blob)) return;
    }
  } catch (e) {}
  fetch('/api/ui-log', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: payload,
    keepalive:true,
  }).catch(() => {});
}

function collectUiLogContext(extra = {}) {
  const grid = $('#grid');
  const container = $('#gridContainer');
  return Object.assign({
    mode: state.mode,
    artist_id: state.currentArtist ? state.currentArtist.id : null,
    artist_name: state.currentArtist ? state.currentArtist.name : '',
    folder: state.activeFolder || '',
    search_scope: state.searchScope,
    search_target: state.searchTarget,
    loaded_count: state.allItems.length,
    card_count: grid ? grid.querySelectorAll('.card').length : 0,
    has_more: state.hasMoreItems,
    mobile_columns: state.mobileColumns,
    viewport: `${window.innerWidth}x${window.innerHeight}`,
    scroll_top: container ? Math.round(container.scrollTop) : Math.round(window.scrollY || 0),
    user_agent: navigator.userAgent,
  }, extra);
}

function frontendErrorText(value) {
  if (!value) return '';
  if (value.message) return String(value.message);
  try {
    return typeof value === 'string' ? value : JSON.stringify(value);
  } catch (e) {
    return String(value);
  }
}

function frontendErrorStack(value) {
  if (!value || !value.stack) return '';
  return String(value.stack);
}

function joinUiMeta(parts) {
  return parts.map(part => String(part || '').trim()).filter(Boolean).join(UI_FIELD_SEPARATOR);
}

function logFrontendError(event, data) {
  if (frontendErrorLogCount >= FRONTEND_ERROR_LOG_LIMIT) return;
  const key = `${event}:${data.message || data.reason || ''}:${data.source || ''}:${data.line || ''}`;
  const now = Date.now();
  const lastSeen = frontendErrorLastSeen.get(key) || 0;
  if (now - lastSeen < FRONTEND_ERROR_DEDUPE_MS) return;
  frontendErrorLastSeen.set(key, now);
  frontendErrorLogCount += 1;
  logUiAction(event, collectUiLogContext(data));
}

function installFrontendErrorLogging() {
  window.addEventListener('error', event => {
    logFrontendError('frontend_error', {
      message: frontendErrorText(event.error) || String(event.message || ''),
      source: event.filename || '',
      line: event.lineno || 0,
      column: event.colno || 0,
      stack: frontendErrorStack(event.error),
    });
  });
  window.addEventListener('unhandledrejection', event => {
    const reason = event.reason;
    logFrontendError('frontend_rejection', {
      reason: frontendErrorText(reason),
      stack: frontendErrorStack(reason),
    });
  });
}

async function init() {
  loadSidebarWidth();
  loadMobileColumns();
  bindEvents();
  connectWS();
  await loadArtists();
  if (!state.currentArtist) clearUI();
}

async function loadArtists() {
  const [artists, duplicates] = await Promise.all([
    API.get('/api/artists'),
    API.get('/api/artists/duplicates'),
  ]);
  state.artists = artists;
  state.duplicateFolders = duplicates.groups || [];
  if (state.currentArtist) {
    state.currentArtist = state.artists.find(a => a.id === state.currentArtist.id) || null;
  }
  setArtistSearchLabel();
  renderDuplicateFolders();
  renderLibraryEmptyState();
}

async function selectArtist(id, options = {}) {
  const seq = nextRequestSeq('artistLoadSeq');
  const preservedArtistChangeScrollTop = state.mode === 'moves' ? movePanelScrollTop() : null;
  state.currentArtist = id ? state.artists.find(a => a.id === parseInt(id)) : null;
  syncSearchOptionsControl();
  setArtistSearchLabel();
  closeArtistDropdown();
  state.activeRole = options.tagId ? String(options.tagId) : null;
  state.activeFolder = null;
  state.selectedIds.clear();
  state.editContextArtistId = state.currentArtist ? state.currentArtist.id : null;
  state.editContextKey = state.currentArtist ? String(state.currentArtist.id) : '';
  state.selectedEditTagIds.clear();
  state.selectedEditTagNames.clear();
  state.editTagQuery = '';
  state.duplicatesOnly = false;
  state.tagSearchResults = [];
  state.search = '';
  $('#searchInput').value = '';
  const editTagSearch = $('#editTagSearch');
  if (editTagSearch) editTagSearch.value = '';

  if (!state.currentArtist) {
    clearUI();
    return;
  }
  const artistId = state.currentArtist.id;

  const [stats, tags, folders] = await Promise.all([
    API.get(`/api/artists/${artistId}/stats`),
    API.get(`/api/tags?artist_id=${artistId}`),
    API.get(`/api/folders?artist_id=${artistId}`),
  ]);
  if (!isCurrentRequestSeq('artistLoadSeq', seq)) return;
  state.stats = stats;
  state.tags = tags;
  state.editContextKey = String(artistId);
  state.folders = folders;
  renderSidebar();
  renderFolderTree();
  renderEditTagPicker();
  renderToolbar();
  if (state.mode === 'moves') {
    await loadMoveWorkbench({preserveScroll: true});
    restoreMovePanelScroll(preservedArtistChangeScrollTop);
    return;
  }
  scrollToItemsTop();
  loadItems();
}

function artistOptionLabel(artist) {
  return `${artist.name} (${artist.item_count})`;
}

function setArtistSearchLabel(value = null) {
  const input = $('#artistSearch');
  if (!input) return;
  input.value = value !== null ? value : (state.currentArtist ? artistOptionLabel(state.currentArtist) : '');
}

function artistMatchesQuery(artist, query) {
  if (!query) return true;
  const text = `${artist.name || ''} ${artist.path || ''}`.toLowerCase();
  return text.includes(query.toLowerCase());
}

function renderArtistDropdown(query = '') {
  const dropdown = $('#artistDropdown');
  if (!dropdown) return;
  const results = state.artists.filter(a => artistMatchesQuery(a, query.trim())).slice(0, MAX_ARTIST_DROPDOWN_RESULTS);
  $('#artistPicker').classList.add('open');
  dropdown.classList.add('open');
  if (results.length === 0) {
    dropdown.innerHTML = '<div class="artist-empty">没有匹配画师</div>';
    return;
  }
  dropdown.innerHTML = results.map(a => `
    <button class="artist-option" type="button" data-artist-id="${a.id}" title="${escHtml(a.path || a.name)}">
      <span>${escHtml(a.name)}</span>
      <strong>${a.item_count || 0}</strong>
    </button>
  `).join('');
  $$('#artistDropdown .artist-option').forEach(btn => {
    btn.addEventListener('click', () => selectArtist(btn.dataset.artistId));
  });
}

function closeArtistDropdown() {
  const dropdown = $('#artistDropdown');
  if (dropdown) dropdown.classList.remove('open');
  $('#artistPicker').classList.remove('open');
  setArtistSearchLabel();
}

function selectFirstArtistResult() {
  const first = $('#artistDropdown .artist-option');
  if (first) selectArtist(first.dataset.artistId);
}

function clearUI() {
  state.allItems = [];
  state.itemsOffset = 0;
  state.hasMoreItems = false;
  releaseAllImageLoads();
  releaseAllVideoPreviews();
  $('#sidebarList').innerHTML = '';
  $('#sidebarTotal').textContent = '';
  $('#grid').innerHTML = state.artists.length === 0 ? '' : '<div class="empty">请选择一个画师</div>';
  $('#folderTree').innerHTML = '';
  $('#folderTotal').textContent = '';
  renderLibraryEmptyState();
  updateDuplicateFilesButton();
}
