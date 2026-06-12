function renderGrid() {
  const grid = $('#grid');
  const items = state.allItems;
  const tagResultsHtml = renderTagSearchResults();

  if (items.length === 0 && !tagResultsHtml) {
    releaseAllImageLoads();
    releaseAllVideoPreviews();
    grid.innerHTML = state.duplicatesOnly
      ? '<div class="empty">当前范围没有重复文件</div>'
      : '<div class="empty">没有找到文件</div>';
    return;
  }

  grid.className = 'grid';
  if (state.view === 'compact') grid.classList.add('compact');
  if (state.view === 'list') grid.classList.add('list');

  const html = tagResultsHtml + renderItemCards(items, 0);

  releaseAllImageLoads();
  releaseAllVideoPreviews();
  grid.innerHTML = html;
  bindGridEvents();
  observeImages();
  bindGifPreviewEvents();
  bindVideoPreviewEvents();
  observeVideoPreviews();
}

function appendItemsToGrid(items, startIndex) {
  if (!items.length) return;
  const grid = $('#grid');
  grid.insertAdjacentHTML('beforeend', renderItemCards(items, startIndex));
  bindGridEvents();
  observeImages();
  bindGifPreviewEvents();
  bindVideoPreviewEvents();
  observeVideoPreviews();
}

function renderItemCards(items, startIndex = 0) {
  let html = '';
  items.forEach((item, offset) => {
    const idx = startIndex + offset;
    const sel = state.selectedIds.has(item.id) ? ' selected' : '';
    const chk = state.selectedIds.has(item.id) ? ' checked' : '';

    const mediaType = item.media_type || (item.is_archive ? 'archive' : 'image');
    const isArchive = mediaType === 'archive' || item.is_archive;
    const fileUrl = API.fileUrl(item.file_path, fileVersionParam(item));
    const previewFileUrl = API.previewUrl(item.file_path, fileVersionParam(item), IMAGE_PREVIEW_MAX_EDGE);
    const checkVisible = state.mode === 'edit' ? ' visible' : '';
    const dateHtml = item.date ? escHtml(item.date) : '';
    const shownFolderPath = isGlobalSearchActive()
      ? (item.display_folder_path || item.folder_path || item.artist_name || '')
      : (item.folder_path || '');
    const folderBadge = shownFolderPath ? `<div class="folder-badge">${escHtml(shownFolderPath)}</div>` : '';
    const download = `<a class="card-download" data-download href="${fileUrl}" download="${escHtml(downloadFileName(item))}" title="下载文件" aria-label="下载 ${escHtml(item.file_name)}">↓</a>`;
    const artistJump = isGlobalSearchActive() && item.artist_id
      ? `<button class="artist-jump" type="button" data-artist-jump="${item.artist_id}" title="转到 ${escHtml(item.artist_name || '画师')}">转到画师</button>`
      : '';
    const previewUrl = escHtml(videoPreviewUrl(item));

    if (isArchive) {
      html += `<div class="card archive-card${sel}" data-id="${item.id}" data-idx="${idx}" draggable="${state.mode === 'edit'}">
        <div class="check${checkVisible}${chk}" data-check="${item.id}"></div>
        ${download}
        ${folderBadge}
        <div class="media-file-icon">ZIP</div>
        <div class="info">
          <div class="role">${escHtml(item.file_name)}</div>
          <div class="date">${joinUiMeta([renderTagNames(item.tags), formatSize(item.file_size), dateHtml])}</div>
          ${artistJump}
        </div>
      </div>`;
    } else if (mediaType === 'video') {
      html += `<div class="card video-card${sel}" data-id="${item.id}" data-idx="${idx}" draggable="${state.mode === 'edit'}">
        <div class="check${checkVisible}${chk}" data-check="${item.id}"></div>
        ${download}
        ${folderBadge}
        <div class="video-preview">
          <img class="video-thumb loading" data-preview-src="${previewUrl}" alt="" decoding="async">
          <div class="media-file-icon">▶</div>
        </div>
        <div class="info">
          <div class="role">${escHtml(item.file_name)}</div>
          <div class="date">${joinUiMeta([renderTagNames(item.tags), dateHtml])}</div>
          ${artistJump}
        </div>
      </div>`;
    } else if (mediaType === 'source') {
      html += `<div class="card source-card${sel}" data-id="${item.id}" data-idx="${idx}" draggable="${state.mode === 'edit'}">
        <div class="check${checkVisible}${chk}" data-check="${item.id}"></div>
        ${download}
        ${folderBadge}
        <div class="media-file-icon">SRC</div>
        <div class="info">
          <div class="role">${escHtml(item.file_name)}</div>
          <div class="date">${joinUiMeta([renderTagNames(item.tags), dateHtml])}</div>
          ${artistJump}
        </div>
      </div>`;
    } else if (isGifItem(item)) {
      html += `<div class="card gif-card${sel}" data-id="${item.id}" data-idx="${idx}" draggable="${state.mode === 'edit'}">
        <div class="check${checkVisible}${chk}" data-check="${item.id}"></div>
        ${download}
        <div class="gif-preview">
          <img class="thumb gif-thumb" data-gif-src="${fileUrl}" loading="lazy" decoding="async" alt="">
          <div class="gif-placeholder">GIF</div>
        </div>
        ${folderBadge}
        <div class="info">
          <div class="role">${escHtml(item.file_name)}</div>
          <div class="date">${joinUiMeta([renderTagNames(item.tags), dateHtml])}</div>
          ${artistJump}
        </div>
      </div>`;
    } else {
      html += `<div class="card${sel}" data-id="${item.id}" data-idx="${idx}" draggable="${state.mode === 'edit'}">
        <div class="check${checkVisible}${chk}" data-check="${item.id}"></div>
        ${download}
        <img class="thumb loading" data-src="${previewFileUrl}" decoding="async" fetchpriority="low">
        ${folderBadge}
        <div class="info">
          <div class="role">${escHtml(item.file_name)}</div>
          <div class="date">${joinUiMeta([renderTagNames(item.tags), dateHtml])}</div>
          ${artistJump}
        </div>
      </div>`;
    }
  });
  return html;
}

function bindGridEvents() {
  $$('#grid .card').forEach(card => {
    if (card.dataset.gridBound === '1') return;
    card.dataset.gridBound = '1';
    card.addEventListener('click', e => {
      if (state.mode === 'edit' && e.target.classList.contains('check')) return;
      if (state.mode === 'edit') {
        toggleSelect(parseInt(card.dataset.id));
      } else {
        if (card.classList.contains('archive-card')) return;
        const idx = parseInt(card.dataset.idx);
        openLightbox(idx);
      }
    });
  });

  $$('#grid .check').forEach(chk => {
    if (chk.dataset.checkBound === '1') return;
    chk.dataset.checkBound = '1';
    chk.addEventListener('click', e => {
      e.stopPropagation();
      toggleSelect(parseInt(chk.dataset.check));
    });
  });

  $$('#grid [data-download]').forEach(link => {
    if (link.dataset.downloadBound === '1') return;
    link.dataset.downloadBound = '1';
    link.addEventListener('click', e => {
      e.stopPropagation();
    });
  });

  $$('#grid [data-artist-jump]').forEach(btn => {
    if (btn.dataset.artistJumpBound === '1') return;
    btn.dataset.artistJumpBound = '1';
    btn.addEventListener('click', e => {
      e.stopPropagation();
      jumpToArtist(parseInt(btn.dataset.artistJump));
    });
  });

  $$('#grid [data-tag-jump]').forEach(btn => {
    if (btn.dataset.tagJumpBound === '1') return;
    btn.dataset.tagJumpBound = '1';
    btn.addEventListener('click', e => {
      e.stopPropagation();
      jumpToTag(parseInt(btn.dataset.artistId), parseInt(btn.dataset.tagJump));
    });
  });

  if (state.mode === 'edit') {
    $$('#grid .card').forEach(card => {
      if (card.dataset.dragBound === '1') return;
      card.dataset.dragBound = '1';
      card.addEventListener('dragstart', e => {
        if (!state.selectedIds.has(parseInt(card.dataset.id))) {
          state.selectedIds.clear();
          state.selectedIds.add(parseInt(card.dataset.id));
        }
        e.dataTransfer.setData('text/plain', [...state.selectedIds].join(','));
        renderGrid();
      });
    });
  }
}

async function jumpToArtist(artistId) {
  if (!artistId) return;
  state.searchScope = 'auto';
  syncSearchOptionsControl();
  await selectArtist(artistId);
}

async function jumpToTag(artistId, tagId) {
  if (!artistId || !tagId) return;
  state.search = '';
  $('#searchInput').value = '';
  state.searchScope = 'auto';
  syncSearchOptionsControl();
  await selectArtist(artistId, {tagId});
}

function fileVersionParam(item) {
  const size = Number(item.file_size || 0);
  const mtime = Math.round(Number(item.file_mtime || item.mtime || 0));
  return `${size}-${mtime}`;
}

function isGifItem(item) {
  const mediaType = item.media_type || (item.is_archive ? 'archive' : 'image');
  if (mediaType !== 'image') return false;
  const name = (item.file_name || item.file_path || '').toLowerCase();
  return name.endsWith('.gif');
}

function observeImages() {
  if (!imageObserver) {
    const container = $('#gridContainer');
    imageObserver = new IntersectionObserver((entries) => {
      entries.forEach(entry => {
        if (entry.isIntersecting) {
          const img = entry.target;
          if (img.dataset.src) {
            if (imageObserver) imageObserver.unobserve(img);
            delete img.dataset.imageObserved;
            queueImageLoad(img);
          }
        }
      });
    }, { root: container || null, rootMargin: IMAGE_OBSERVER_ROOT_MARGIN });
  }

  $$('#grid .thumb[data-src]').forEach(img => {
    if (img.dataset.imageObserved === '1' || img.dataset.imageQueued === '1' || img.dataset.imageLoading === '1' || img.dataset.imageLoaded === '1') return;
    img.dataset.imageObserved = '1';
    imageObserver.observe(img);
  });
}

function isImageNearLoadWindow(img) {
  const container = $('#gridContainer');
  if (!container) return true;
  const margin = parseInt(IMAGE_OBSERVER_ROOT_MARGIN, 10) || 0;
  const viewport = container.getBoundingClientRect();
  const rect = img.getBoundingClientRect();
  return rect.bottom >= viewport.top - margin
    && rect.top <= viewport.bottom + margin
    && rect.right >= viewport.left
    && rect.left <= viewport.right;
}

function reobserveImage(img) {
  delete img.dataset.imageQueued;
  if (!img.isConnected || !img.dataset.src || !imageObserver) return;
  delete img.dataset.imageObserved;
  img.dataset.imageObserved = '1';
  imageObserver.observe(img);
}

function queueImageLoad(img) {
  if (!img.dataset.src || img.dataset.imageQueued === '1' || img.dataset.imageLoading === '1' || img.dataset.imageLoaded === '1') return;
  if (!isImageNearLoadWindow(img)) {
    reobserveImage(img);
    return;
  }
  img.dataset.imageQueued = '1';
  if (!pendingImageLoads.includes(img)) pendingImageLoads.push(img);
  pumpImageLoadQueue();
}

function pumpImageLoadQueue() {
  while (activeImageLoads < MAX_IMAGE_LOADS && pendingImageLoads.length) {
    const img = pendingImageLoads.shift();
    delete img.dataset.imageQueued;
    if (!img.isConnected || !img.dataset.src || img.dataset.imageLoading === '1' || img.dataset.imageLoaded === '1') continue;
    if (!isImageNearLoadWindow(img)) {
      reobserveImage(img);
      continue;
    }
    activeImageLoads += 1;
    img.dataset.imageLoading = '1';
    img.classList.remove('failed');
    img.onload = () => finishImageLoad(img, true);
    img.onerror = () => finishImageLoad(img, false, true);
    img.dataset.imageLoadTimer = String(setTimeout(() => {
      finishImageLoad(img, false, true);
    }, IMAGE_LOAD_TIMEOUT_MS));
    const src = img.dataset.src;
    img.src = src;
    img.removeAttribute('data-src');
  }
}

function clearImageLoadTimer(img) {
  if (!img.dataset.imageLoadTimer) return;
  clearTimeout(Number(img.dataset.imageLoadTimer));
  delete img.dataset.imageLoadTimer;
}

function finishImageLoad(img, loaded, clearSource = false) {
  if (img.dataset.imageLoading === '1') {
    activeImageLoads = Math.max(0, activeImageLoads - 1);
  }
  clearImageLoadTimer(img);
  delete img.dataset.imageLoading;
  delete img.dataset.imageQueued;
  img.onload = null;
  img.onerror = null;
  img.classList.remove('loading');
  if (loaded) {
    img.dataset.imageLoaded = '1';
    img.classList.remove('failed');
  } else {
    img.classList.add('failed');
  }
  if (clearSource) {
    img.removeAttribute('src');
  }
  pumpImageLoadQueue();
}

function releaseAllImageLoads() {
  if (imageObserver) {
    imageObserver.disconnect();
    imageObserver = null;
  }
  const images = new Set([
    ...pendingImageLoads,
    ...$$('#grid .thumb[data-image-loading="1"], #grid .thumb[data-image-queued="1"]'),
  ]);
  pendingImageLoads.splice(0);
  activeImageLoads = 0;
  images.forEach(img => {
    if (!img) return;
    clearImageLoadTimer(img);
    img.onload = null;
    img.onerror = null;
    delete img.dataset.imageLoading;
    delete img.dataset.imageObserved;
    delete img.dataset.imageQueued;
    img.removeAttribute('src');
  });
}

function videoPreviewUrl(item) {
  return API.videoFrameUrl(item.file_path, fileVersionParam(item));
}

function bindGifPreviewEvents() {
  $$('#grid .gif-card').forEach(card => {
    if (card.dataset.gifBound === '1') return;
    card.dataset.gifBound = '1';
    const img = card.querySelector('.gif-thumb[data-gif-src]');
    if (!img) return;
    card.addEventListener('pointerenter', () => playGifPreview(img));
    card.addEventListener('pointerleave', () => stopGifPreview(img));
  });
}

function playGifPreview(img) {
  if (!img.dataset.gifSrc) return;
  img.src = img.dataset.gifSrc;
  img.classList.add('playing');
}

function stopGifPreview(img) {
  img.onload = null;
  img.onerror = null;
  img.classList.remove('playing');
  img.removeAttribute('src');
}

function bindVideoPreviewEvents() {
  $$('#grid .video-card').forEach(card => {
    if (card.dataset.videoBound === '1') return;
    card.dataset.videoBound = '1';
    const video = card.querySelector('.video-thumb[data-preview-src]');
    if (!video) return;
    card.addEventListener('pointerenter', () => scheduleVideoPreview(video));
    card.addEventListener('pointerleave', () => clearVideoPreviewTimer(video));
  });
}

function observeVideoPreviews() {
  if (!videoPreviewObserver) {
    videoPreviewObserver = new IntersectionObserver((entries) => {
      entries.forEach(entry => {
        if (entry.isIntersecting) {
          queueVideoPreview(entry.target);
          if (videoPreviewObserver) videoPreviewObserver.unobserve(entry.target);
        }
      });
    }, { rootMargin: '200px' });
  }

  $$('#grid .video-thumb[data-preview-src]').forEach(video => {
    if (video.dataset.previewObserved === '1') return;
    video.dataset.previewObserved = '1';
    videoPreviewObserver.observe(video);
  });
}

function scheduleVideoPreview(video) {
  if (!video.dataset.previewSrc || video.dataset.loading === '1' || video.dataset.loaded === '1') return;
  clearVideoPreviewTimer(video);
  video.dataset.previewTimer = String(setTimeout(() => {
    delete video.dataset.previewTimer;
    queueVideoPreview(video);
  }, VIDEO_PREVIEW_HOVER_DELAY_MS));
}

function clearVideoPreviewTimer(video) {
  if (!video.dataset.previewTimer) return;
  clearTimeout(Number(video.dataset.previewTimer));
  delete video.dataset.previewTimer;
}

function clearVideoPreviewLoadTimer(video) {
  if (!video.dataset.previewLoadTimer) return;
  clearTimeout(Number(video.dataset.previewLoadTimer));
  delete video.dataset.previewLoadTimer;
}

function queueVideoPreview(video) {
  if (!video.dataset.previewSrc || video.dataset.loading === '1' || video.dataset.loaded === '1') return;
  if (!pendingVideoPreviews.includes(video)) pendingVideoPreviews.push(video);
  pumpVideoPreviewQueue();
}

function pumpVideoPreviewQueue() {
  while (activeVideoPreviewLoads < MAX_VIDEO_PREVIEW_LOADS && pendingVideoPreviews.length) {
    const video = pendingVideoPreviews.shift();
    if (!video.isConnected || !video.dataset.previewSrc || video.dataset.loading === '1' || video.dataset.loaded === '1') continue;
    activeVideoPreviewLoads += 1;
    video.dataset.loading = '1';
    video.onload = () => finishVideoPreview(video, true);
    video.onerror = () => finishVideoPreview(video, false, true);
    video.dataset.previewLoadTimer = String(setTimeout(() => {
      finishVideoPreview(video, false, true);
    }, VIDEO_PREVIEW_LOAD_TIMEOUT_MS));
    video.src = video.dataset.previewSrc;
  }
}

function finishVideoPreview(video, loaded, clearSource = false) {
  if (video.dataset.loading === '1') {
    activeVideoPreviewLoads = Math.max(0, activeVideoPreviewLoads - 1);
  }
  clearVideoPreviewLoadTimer(video);
  delete video.dataset.loading;
  video.onload = null;
  video.onerror = null;
  video.classList.remove('loading');
  if (loaded) {
    video.dataset.loaded = '1';
    video.classList.add('ready');
  }
  if (clearSource) {
    video.removeAttribute('src');
  }
  pumpVideoPreviewQueue();
}

function releaseVideoPreview(video) {
  clearVideoPreviewTimer(video);
  clearVideoPreviewLoadTimer(video);
  const idx = pendingVideoPreviews.indexOf(video);
  if (idx >= 0) pendingVideoPreviews.splice(idx, 1);
  if (video.dataset.loading === '1') {
    activeVideoPreviewLoads = Math.max(0, activeVideoPreviewLoads - 1);
  }
  video.onload = null;
  video.onerror = null;
  delete video.dataset.loading;
  delete video.dataset.loaded;
  video.classList.remove('ready');
  video.classList.add('loading');
  video.removeAttribute('src');
  pumpVideoPreviewQueue();
}

function releaseAllVideoPreviewLoads() {
  if (videoPreviewObserver) {
    videoPreviewObserver.disconnect();
    videoPreviewObserver = null;
  }
  const videos = new Set([
    ...pendingVideoPreviews,
    ...$$('#grid .video-thumb'),
  ]);
  pendingVideoPreviews.splice(0);
  activeVideoPreviewLoads = 0;
  videos.forEach(video => {
    if (!video) return;
    clearVideoPreviewTimer(video);
    clearVideoPreviewLoadTimer(video);
    video.onload = null;
    video.onerror = null;
    delete video.dataset.loading;
    delete video.dataset.previewObserved;
    delete video.dataset.loaded;
    video.classList.remove('ready');
    video.classList.add('loading');
    video.removeAttribute('src');
  });
}

function releaseAllVideoPreviews() {
  releaseAllVideoPreviewLoads();
}

function lightboxPreviewPlaceholderUrl(item) {
  if (!item || item.id === undefined || item.id === null) return '';
  let placeholderUrl = '';
  $$('#grid .card').forEach(card => {
    if (placeholderUrl || card.dataset.id !== String(item.id)) return;
    const thumb = card.querySelector('img.thumb');
    if (!thumb || thumb.classList.contains('failed')) return;
    if (thumb.dataset.imageLoaded !== '1' && !(thumb.complete && thumb.naturalWidth > 0)) return;
    placeholderUrl = thumb.currentSrc || thumb.src || '';
  });
  return placeholderUrl;
}

function clearLightboxVideoFallbackTimer(video) {
  if (!video || !video.dataset.videoFallbackTimer) return;
  clearTimeout(Number(video.dataset.videoFallbackTimer));
  delete video.dataset.videoFallbackTimer;
}

function lightboxVideoLogData(video, extra = {}) {
  return collectUiLogContext(Object.assign({
    item_id: video.dataset.itemId || '',
    file_name: video.dataset.fileName || '',
    file_path: video.dataset.filePath || '',
    current_src: video.currentSrc || video.src || '',
    hls_tried: video.dataset.hlsTried === '1',
    compatible_tried: video.dataset.compatibleTried === '1',
    network_state: video.networkState,
    ready_state: video.readyState,
    error_code: video.error ? video.error.code : 0,
    error_message: video.error ? video.error.message : '',
  }, extra));
}

function logLightboxVideoEvent(event, video, extra = {}) {
  if (!video || !video.dataset.filePath) return;
  logUiAction(event, lightboxVideoLogData(video, extra));
}

function switchLightboxVideoToTranscode(video, reason) {
  if (!video || !video.dataset.filePath) return false;
  if (!video.dataset.transcodeSrc || video.dataset.transcodeTried === '1') return false;
  clearLightboxVideoFallbackTimer(video);
  video.dataset.transcodeTried = '1';
  logLightboxVideoEvent('video_transcode_start', video, {reason});
  startLightboxVideoTranscode(video);
  return true;
}

function switchLightboxVideoToCompatible(video, reason) {
  if (!video || !video.dataset.filePath) return false;
  const compatibleSrc = video.dataset.compatibleSrc || '';
  if (!compatibleSrc || video.dataset.compatibleTried === '1') return false;
  const wasPlaying = !video.paused && !video.ended;
  clearLightboxVideoFallbackTimer(video);
  video.dataset.compatibleTried = '1';
  logLightboxVideoEvent('video_fallback_start', video, {reason});
  video.src = compatibleSrc;
  video.load();
  if (wasPlaying && video.play) {
    video.play().catch(error => {
      logLightboxVideoEvent('video_fallback_play_rejected', video, {message: frontendErrorText(error)});
    });
  }
  return true;
}

function shouldPreferCompatibleVideoStream() {
  const ua = navigator.userAgent || '';
  const vendor = navigator.vendor || '';
  const isIOS = /iPad|iPhone|iPod/.test(ua) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  const isSafari = /Safari/.test(ua) && /Apple/.test(vendor) && !/Chrome|CriOS|FxiOS|Edg|OPR|Android/.test(ua);
  return isIOS || isSafari;
}

function shouldUseVideoTranscode() {
  return shouldPreferCompatibleVideoStream();
}

function shouldUseVideoHls() {
  return shouldPreferCompatibleVideoStream();
}

function setLightboxVideoStatus(text) {
  const info = $('#lightboxInfo');
  if (!info) return;
  const status = info.querySelector('[data-video-status]');
  if (status) status.textContent = text || '';
}

async function startLightboxVideoTranscode(video) {
  if (!video || !video.dataset.filePath) return;
  const token = video.dataset.loadToken || '';
  const transcodeStartedAt = performance.now();
  try {
    setLightboxVideoStatus('正在用 NAS 核显为 Safari 准备视频...');
    let status = await API.get(video.dataset.transcodeStatusSrc);
    if (token !== video.dataset.loadToken) return;
    if (status.status !== 'ready') {
      await API.post(video.dataset.transcodeSrc);
      if (token !== video.dataset.loadToken) return;
      status = await API.get(video.dataset.transcodeStatusSrc);
    }
    if (status.status !== 'ready') {
      throw new Error(status.error || 'video transcode did not finish');
    }
    video.src = video.dataset.transcodedSrc;
    video.load();
    setLightboxVideoStatus('Safari 视频准备完成');
    logLightboxVideoEvent('video_transcode_ready', video, {
      key: status.key || '',
      elapsed_ms: Math.round(performance.now() - transcodeStartedAt)
    });
  } catch (error) {
    if (token !== video.dataset.loadToken) return;
    setLightboxVideoStatus('Safari 视频准备失败，正在尝试兼容流...');
    logLightboxVideoEvent('video_transcode_error', video, {
      message: frontendErrorText(error),
      elapsed_ms: Math.round(performance.now() - transcodeStartedAt)
    });
    switchLightboxVideoToCompatible(video, 'transcode_error');
  }
}

function handleLightboxVideoReadinessFailure(video, reason) {
  if (video && video.dataset.hlsTried === '1' && video.dataset.transcodeTried !== '1') {
    return switchLightboxVideoToTranscode(video, reason);
  }
  if (video && reason !== 'media_error') {
    logLightboxVideoEvent('video_stream_waiting', video, {reason});
    return false;
  }
  return switchLightboxVideoToCompatible(video, reason);
}

function scheduleLightboxVideoFallback(video) {
  clearLightboxVideoFallbackTimer(video);
  if (!video || (!video.dataset.compatibleSrc && !video.dataset.transcodeSrc)) return;
  if (video.dataset.compatibleTried === '1' || video.dataset.transcodeTried === '1') return;
  video.dataset.videoFallbackTimer = String(setTimeout(() => {
    delete video.dataset.videoFallbackTimer;
    if (!video.isConnected || video.style.display === 'none' || !video.dataset.filePath) return;
    if (video.readyState < 3) {
      handleLightboxVideoReadinessFailure(video, 'canplay_timeout');
    }
  }, LIGHTBOX_VIDEO_FALLBACK_DELAY_MS));
}

function bindLightboxVideoDiagnostics() {
  const video = $('#lightboxVideo');
  if (!video || video.dataset.diagnosticsBound === '1') return;
  video.dataset.diagnosticsBound = '1';
  video.addEventListener('loadstart', () => {
    logLightboxVideoEvent('video_loadstart', video);
  });
  video.addEventListener('loadedmetadata', () => {
    logLightboxVideoEvent('video_loadedmetadata', video, {
      duration: Number.isFinite(video.duration) ? Number(video.duration.toFixed(3)) : null,
      video_width: video.videoWidth || 0,
      video_height: video.videoHeight || 0,
    });
  });
  video.addEventListener('canplay', () => {
    clearLightboxVideoFallbackTimer(video);
    logLightboxVideoEvent('video_canplay', video, {
      video_width: video.videoWidth || 0,
      video_height: video.videoHeight || 0,
    });
  });
  video.addEventListener('stalled', () => {
    logLightboxVideoEvent('video_stalled', video);
    if (video.readyState < 1) handleLightboxVideoReadinessFailure(video, 'stalled_before_metadata');
  });
  video.addEventListener('error', () => {
    clearLightboxVideoFallbackTimer(video);
    logLightboxVideoEvent('video_error', video);
    handleLightboxVideoReadinessFailure(video, 'media_error');
  });
}

function openLightbox(idx) {
  const selected = state.allItems[idx];
  const items = lightboxItems();
  if (items.length === 0) return;
  const nextIndex = selected ? items.findIndex(item => item.id === selected.id) : -1;
  if (nextIndex < 0) return;
  state.lastFocusedBeforeLightbox = document.activeElement;
  state.lightboxIndex = nextIndex;
  resetLightboxTransform();
  showLightboxImage(items);
  $('#lightbox').style.display = 'flex';
  document.addEventListener('keydown', onLightboxKey);
}

function lightboxItems() {
  return state.allItems.filter(isLightboxItem);
}

function isTaggableItem(item) {
  const mediaType = item.media_type || (item.is_archive ? 'archive' : 'image');
  return mediaType === 'image' || mediaType === 'video' || mediaType === 'source' || mediaType === 'archive' || item.is_archive;
}

function isLightboxItem(item) {
  const mediaType = item.media_type || (item.is_archive ? 'archive' : 'image');
  return mediaType === 'image' || mediaType === 'video' || mediaType === 'source';
}

function showLightboxImage(items) {
  const item = items[state.lightboxIndex];
  if (!item) return;
  const loadToken = ++state.lightboxLoadToken;
  const mediaType = item.media_type || 'image';
  const fileUrl = API.fileUrl(item.file_path, fileVersionParam(item));
  const displayFileUrl = fileUrl;
  const placeholderUrl = mediaType === 'image' && !isGifItem(item) ? lightboxPreviewPlaceholderUrl(item) : '';
  const img = $('#lightboxImg');
  const video = $('#lightboxVideo');
  const file = $('#lightboxFile');
  img.onload = null;
  img.onerror = null;
  img.style.display = 'none';
  img.classList.remove('ready', 'failed', 'placeholder');
  img.classList.add('loading');
  img.removeAttribute('src');
  img.alt = item.file_name || '';
  delete img.dataset.fallbackSrc;
  video.style.display = 'none';
  file.style.display = 'none';
  clearLightboxVideoFallbackTimer(video);
  video.pause();
  video.removeAttribute('src');
  delete video.dataset.itemId;
  delete video.dataset.fileName;
  delete video.dataset.filePath;
  delete video.dataset.originalSrc;
  delete video.dataset.hlsSrc;
  delete video.dataset.hlsTried;
  delete video.dataset.compatibleSrc;
  delete video.dataset.compatibleTried;
  delete video.dataset.transcodeSrc;
  delete video.dataset.transcodeStatusSrc;
  delete video.dataset.transcodedSrc;
  delete video.dataset.transcodeTried;
  delete video.dataset.loadToken;
  video.load();

  if (mediaType === 'video') {
    img.classList.remove('loading');
    video.dataset.itemId = String(item.id);
    video.dataset.fileName = item.file_name || '';
    video.dataset.filePath = item.file_path || '';
    video.dataset.loadToken = String(loadToken);
    video.dataset.originalSrc = API.streamUrl(item.file_path);
    video.dataset.hlsSrc = API.videoHlsUrl(item.file_path);
    video.dataset.compatibleSrc = API.videoCompatibleUrl(item.file_path);
    video.dataset.transcodeSrc = API.videoTranscodeUrl(item.file_path);
    video.dataset.transcodeStatusSrc = API.videoTranscodeStatusUrl(item.file_path);
    video.dataset.transcodedSrc = API.videoTranscodedUrl(item.file_path);
    const useHlsStream = shouldUseVideoHls();
    video.dataset.hlsTried = '0';
    video.dataset.compatibleTried = '0';
    video.dataset.transcodeTried = '0';
    if (useHlsStream) {
      video.dataset.hlsTried = '1';
      video.src = video.dataset.hlsSrc;
      video.load();
      scheduleLightboxVideoFallback(video);
      logLightboxVideoEvent('video_hls_start', video, {reason: 'apple_webkit'});
    } else {
      video.src = video.dataset.originalSrc;
      video.load();
      scheduleLightboxVideoFallback(video);
    }
    video.style.display = '';
  } else if (mediaType === 'source') {
    img.classList.remove('loading');
    file.innerHTML = `<div class="source-mark">SRC</div><div>${escHtml(item.file_name)}</div><small>${formatSize(item.file_size)}</small>`;
    file.style.display = 'flex';
  } else {
    if (displayFileUrl !== fileUrl) img.dataset.fallbackSrc = fileUrl;
    const revealLoadedImage = (src) => {
      if (loadToken !== state.lightboxLoadToken) return;
      img.src = src;
      const reveal = () => {
        if (loadToken !== state.lightboxLoadToken) return;
        img.classList.remove('loading', 'failed', 'placeholder');
        img.classList.add('ready');
        img.style.display = '';
      };
      if (img.decode) {
        img.decode().then(reveal).catch(reveal);
      } else {
        reveal();
      }
    };
    const loadFallbackImage = (fallbackSrc) => {
      if (loadToken !== state.lightboxLoadToken) return;
      const fallbackLoader = new Image();
      fallbackLoader.decoding = 'async';
      fallbackLoader.onload = () => revealLoadedImage(fallbackSrc);
      fallbackLoader.onerror = () => {
        if (loadToken !== state.lightboxLoadToken) return;
        img.classList.remove('loading', 'ready', 'placeholder');
        img.classList.add('failed');
        if (!placeholderUrl) img.style.display = '';
      };
      fallbackLoader.src = fallbackSrc;
    };
    const loader = new Image();
    loader.decoding = 'async';
    loader.onload = () => revealLoadedImage(displayFileUrl);
    loader.onerror = () => {
      if (loadToken !== state.lightboxLoadToken) return;
      if (img.dataset.fallbackSrc) {
        const fallbackSrc = img.dataset.fallbackSrc;
        delete img.dataset.fallbackSrc;
        loadFallbackImage(fallbackSrc);
        return;
      }
      img.classList.remove('loading', 'ready', 'placeholder');
      img.classList.add('failed');
      if (!placeholderUrl) img.style.display = '';
    };
    if (placeholderUrl) {
      img.src = placeholderUrl;
      img.classList.remove('loading', 'failed');
      img.classList.add('ready', 'placeholder');
      img.style.display = '';
    }
    loader.src = displayFileUrl;
  }
  applyLightboxZoom();
  const download = $('#lightboxDownloadBtn');
  download.href = fileUrl;
  download.download = downloadFileName(item);
  download.setAttribute('aria-label', `下载 ${item.file_name}`);
  const copyPath = item.real_file_path || item.file_path;
  const lightboxMeta = [renderTagNames(item.tags) || '未加标签', item.date, item.file_name];
  $('#lightboxInfo').innerHTML = `
    ${lightboxMeta.filter(part => part).map(part => `<span>${escHtml(part)}</span>`).join('')}
    ${mediaType === 'video' ? '<span data-video-status></span>' : ''}
    <button type="button" class="lightbox-path" data-copy-path="${escHtml(copyPath)}" title="${escHtml(copyPath)}">${escHtml(item.display_file_path || item.real_file_path || item.file_path)}</button>
  `;
  const pathButton = $('#lightboxInfo .lightbox-path');
  if (pathButton) {
    pathButton.addEventListener('click', async e => {
      e.stopPropagation();
      const ok = await copyText(pathButton.dataset.copyPath || '');
      toast(ok ? '真实路径已复制' : '复制路径失败', ok ? 'success' : 'error');
    });
  }
}

function applyLightboxZoom() {
  const img = $('#lightboxImg');
  if (!img) return;
  Object.assign(img.style, {
    transform: `translate(${state.lightboxPanX}px, ${state.lightboxPanY}px) scale(${state.lightboxZoom})`,
    cursor: state.lightboxZoom > 1 ? 'grab' : 'default',
  });
}

function clampLightboxZoom(value) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return 1;
  return Math.max(LIGHTBOX_ZOOM_MIN, Math.min(LIGHTBOX_ZOOM_MAX, Number(parsed.toFixed(2))));
}

function setLightboxZoom(value) {
  state.lightboxZoom = clampLightboxZoom(value);
  if (state.lightboxZoom <= 1) {
    state.lightboxPanX = 0;
    state.lightboxPanY = 0;
    state.lightboxPanActive = false;
  }
  applyLightboxZoom();
}

function resetLightboxTransform() {
  state.lightboxZoom = 1;
  state.lightboxPanX = 0;
  state.lightboxPanY = 0;
  state.lightboxPanActive = false;
  state.lightboxPointers.clear();
  state.lightboxPinchActive = false;
  state.lightboxPinchStartDistance = 0;
  state.lightboxPinchStartZoom = 1;
  state.lightboxTapPointerId = null;
  state.lightboxTapMoved = false;
  state.lightboxLastTapAt = 0;
  applyLightboxZoom();
}

function isTouchLightboxPointer(e) {
  return e && (e.pointerType === 'touch' || e.pointerType === 'pen');
}

function lightboxPointerDistance(a, b) {
  if (!a || !b) return 0;
  return Math.hypot(a.x - b.x, a.y - b.y);
}

function lightboxPointerPoints() {
  return [...state.lightboxPointers.values()];
}

function startLightboxPinch() {
  const points = lightboxPointerPoints();
  if (points.length < 2) return;
  const distance = lightboxPointerDistance(points[0], points[1]);
  if (distance <= 0) return;
  state.lightboxPinchActive = true;
  state.lightboxPinchStartDistance = distance;
  state.lightboxPinchStartZoom = state.lightboxZoom;
  state.lightboxPanActive = false;
  state.lightboxTapPointerId = null;
  state.lightboxTapMoved = true;
}

function updateLightboxPinchZoom(e) {
  if (!state.lightboxPinchActive || state.lightboxPointers.size < 2) return;
  e.preventDefault();
  e.stopPropagation();
  const points = lightboxPointerPoints();
  const distance = lightboxPointerDistance(points[0], points[1]);
  if (distance <= 0 || state.lightboxPinchStartDistance <= 0) return;
  setLightboxZoom(state.lightboxPinchStartZoom * (distance / state.lightboxPinchStartDistance));
}

function handleLightboxDoubleTap(e) {
  if (!isTouchLightboxPointer(e) || state.lightboxTapMoved) return false;
  const now = Date.now();
  const distance = Math.hypot(e.clientX - state.lightboxLastTapX, e.clientY - state.lightboxLastTapY);
  const isDoubleTap = (
    state.lightboxLastTapAt > 0
    && now - state.lightboxLastTapAt <= LIGHTBOX_DOUBLE_TAP_DELAY_MS
    && distance <= LIGHTBOX_DOUBLE_TAP_DISTANCE_PX
  );
  state.lightboxLastTapAt = now;
  state.lightboxLastTapX = e.clientX;
  state.lightboxLastTapY = e.clientY;
  if (!isDoubleTap) return false;
  e.preventDefault();
  e.stopPropagation();
  state.lightboxLastTapAt = 0;
  setLightboxZoom(state.lightboxZoom > 1 ? 1 : LIGHTBOX_DOUBLE_TAP_ZOOM);
  return true;
}

function startLightboxPan(e) {
  if (e.pointerType === 'mouse' && e.button !== 0) return;
  if (e.pointerId != null) {
    state.lightboxPointers.set(e.pointerId, {x: e.clientX, y: e.clientY});
  }
  const img = $('#lightboxImg');
  if (img && img.setPointerCapture && e.pointerId != null) {
    try { img.setPointerCapture(e.pointerId); } catch (err) {}
  }
  if (state.lightboxPointers.size === 2) {
    e.preventDefault();
    e.stopPropagation();
    startLightboxPinch();
    return;
  }
  if (isTouchLightboxPointer(e)) {
    state.lightboxTapPointerId = e.pointerId;
    state.lightboxTapStartX = e.clientX;
    state.lightboxTapStartY = e.clientY;
    state.lightboxTapMoved = false;
  }
  if (state.lightboxZoom <= 1) return;
  e.preventDefault();
  e.stopPropagation();
  state.lightboxPanActive = true;
  state.lightboxPanPointerX = e.clientX;
  state.lightboxPanPointerY = e.clientY;
  state.lightboxPanStartX = state.lightboxPanX;
  state.lightboxPanStartY = state.lightboxPanY;
}

function moveLightboxPan(e) {
  if (e.pointerId != null && state.lightboxPointers.has(e.pointerId)) {
    state.lightboxPointers.set(e.pointerId, {x: e.clientX, y: e.clientY});
  }
  if (state.lightboxTapPointerId === e.pointerId) {
    const tapDistance = Math.hypot(e.clientX - state.lightboxTapStartX, e.clientY - state.lightboxTapStartY);
    if (tapDistance > LIGHTBOX_DOUBLE_TAP_DISTANCE_PX) state.lightboxTapMoved = true;
  }
  if (state.lightboxPointers.size === 2) {
    if (!state.lightboxPinchActive) startLightboxPinch();
    updateLightboxPinchZoom(e);
    return;
  }
  if (!state.lightboxPanActive || state.lightboxZoom <= 1) return;
  e.preventDefault();
  e.stopPropagation();
  state.lightboxPanX = state.lightboxPanStartX + (e.clientX - state.lightboxPanPointerX);
  state.lightboxPanY = state.lightboxPanStartY + (e.clientY - state.lightboxPanPointerY);
  applyLightboxZoom();
}

function stopLightboxPan(e) {
  const wasPinching = state.lightboxPinchActive || state.lightboxPointers.size > 1;
  const img = $('#lightboxImg');
  if (img && img.releasePointerCapture && e && e.pointerId != null) {
    try { img.releasePointerCapture(e.pointerId); } catch (err) {}
  }
  if (e && e.pointerId != null) {
    state.lightboxPointers.delete(e.pointerId);
  } else {
    state.lightboxPointers.clear();
  }
  if (state.lightboxPanActive) state.lightboxPanActive = false;
  if (state.lightboxPointers.size < 2) {
    state.lightboxPinchActive = false;
    state.lightboxPinchStartDistance = 0;
    state.lightboxPinchStartZoom = state.lightboxZoom;
  }
  if (!wasPinching && e && state.lightboxTapPointerId === e.pointerId) {
    handleLightboxDoubleTap(e);
  }
  if (state.lightboxPointers.size === 0) {
    state.lightboxTapPointerId = null;
    state.lightboxTapMoved = false;
  }
}

function moveLightbox(delta) {
  const items = lightboxItems();
  if (!items.length) return;
  const nextIndex = Math.max(0, Math.min(items.length - 1, state.lightboxIndex + delta));
  if (nextIndex === state.lightboxIndex) return;
  state.lightboxIndex = nextIndex;
  resetLightboxTransform();
  showLightboxImage(items);
}

function onLightboxKey(e) {
  if (e.key === 'Escape') closeLightbox();
  if (e.key === 'ArrowLeft') moveLightbox(-1);
  if (e.key === 'ArrowRight') moveLightbox(1);
}

function onLightboxWheel(e) {
  if (Math.abs(e.deltaY) < 1) return;
  e.preventDefault();
  e.stopPropagation();

  if (e.ctrlKey) {
    const direction = e.deltaY < 0 ? 1 : -1;
    const zoom = state.lightboxZoom + direction * LIGHTBOX_ZOOM_STEP;
    setLightboxZoom(zoom);
    return;
  }

  const now = Date.now();
  if (now - state.lightboxWheelLastAt < LIGHTBOX_WHEEL_NAV_DELAY) return;
  state.lightboxWheelLastAt = now;
  moveLightbox(e.deltaY > 0 ? 1 : -1);
}

function closeLightbox() {
  $('#lightbox').style.display = 'none';
  state.lightboxLoadToken += 1;
  resetLightboxTransform();
  const img = $('#lightboxImg');
  img.onload = null;
  img.onerror = null;
  img.removeAttribute('src');
  img.style.display = 'none';
  img.classList.remove('loading', 'ready', 'failed', 'placeholder');
  delete img.dataset.fallbackSrc;
  const video = $('#lightboxVideo');
  clearLightboxVideoFallbackTimer(video);
  video.pause();
  video.removeAttribute('src');
  delete video.dataset.itemId;
  delete video.dataset.fileName;
  delete video.dataset.filePath;
  delete video.dataset.originalSrc;
  delete video.dataset.hlsSrc;
  delete video.dataset.hlsTried;
  delete video.dataset.compatibleSrc;
  delete video.dataset.compatibleTried;
  delete video.dataset.transcodeSrc;
  delete video.dataset.transcodeStatusSrc;
  delete video.dataset.transcodedSrc;
  delete video.dataset.loadToken;
  video.load();
  document.removeEventListener('keydown', onLightboxKey);
  const previousFocus = state.lastFocusedBeforeLightbox;
  state.lastFocusedBeforeLightbox = null;
  if (previousFocus && previousFocus.isConnected && previousFocus.focus) {
    previousFocus.focus();
  }
}
