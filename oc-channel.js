/* oc-enhanced-client v2.3 — অ্যালবামে ক্লিক করলে Photos আগে খোলে; ট্যাবের নাম Videos;
   হেডারের ব্যাক বাটন অ্যালবাম থেকে লোডিং ছাড়া লিস্টে ফেরায়; হেডার নিচে স্ক্রলে সরে যায় */
(function () {
    'use strict';
    if (window.__ocEnhancedV2Loaded) return;
    window.__ocEnhancedV2Loaded = true;

    /* ================= ANTI-DOWNLOAD PROTECTIONS ================= */
    (function antiDownload() {
        try {
            var st = document.createElement('style');
            st.setAttribute('data-oc-protect', '1');
            st.textContent =
                'img, video, .alv-media-frame, .alv-thumb-bg, .oc-channel-avatar {' +
                '-webkit-user-select:none; user-select:none; -webkit-touch-callout:none; -webkit-user-drag:none; }' +
                '.alv-media-frame img, .alv-thumb-bg, .oc-channel-avatar img { pointer-events:none; }';
            (document.head || document.documentElement).appendChild(st);
        } catch (e) { }

        document.addEventListener('contextmenu', function (e) {
            if (e.target && e.target.closest && e.target.closest('img, video, .alv-media-frame, .alv-thumb-bg, .alv-post, .oc-al-card, .oc-al-player, .oc-lb')) {
                e.preventDefault();
            }
        });
        document.addEventListener('dragstart', function (e) {
            if (e.target && (e.target.tagName === 'IMG' || e.target.tagName === 'VIDEO')) e.preventDefault();
        });
        document.addEventListener('keydown', function (e) {
            if ((e.ctrlKey || e.metaKey) && (e.key === 's' || e.key === 'S')) e.preventDefault();
        });
    })();

    const CONFIG = {
        containerId: 'channelPostsContainer',
        channelId: 'ocChannelWindow',
        apiURL: '/api/channel/posts',
        albumsURL: '/api/albums',
        albumDetailURL: '/api/albums/',
        mediaURLPrefix: '/api/channel/media/',
        thumbURLPrefix: '/api/channel/thumb/',
        statsURL: '/api/channel/stats',
        refreshInterval: 30000,
        statsPollInterval: 60000,
        batchSize: 10,
        observerRootMargin: '350px 0px',
        telegramURL: 'https://t.me/ALVEE_BD1',
        brandLinkURL: '/oc-collection.html',
        /* স্ক্রল করলে উপরের হেডার সরে যাবে (false দিলে হেডার সবসময় থাকবে) */
        hideHeaderOnScroll: true,
        /* true = উপরে স্ক্রল করলে হেডার আবার আসবে; false = শুধু একদম উপরে গেলে আসবে */
        headerShowOnScrollUp: false,
        adLabel: 'ADVERTISEMENT',
        /* false = পুরনো চ্যানেল-পোস্ট ফিড বন্ধ; শুধু অ্যালবাম দেখাবে */
        showChannelFeed: false,
        homeAlbumLimit: 10,
        brandText: '🫂🖤༏ـ꯭ـ꯭🇦𝙇𝙑𝙀𝙀🫂♥️⟵꯭'
    };

    const HAS_IO = 'IntersectionObserver' in window;
    const mediaCards = new Map();
    const knownMediaIds = new Set();

    let pendingMedia = [];
    let isFetchingMedia = false;
    let isRenderingBatch = false;
    let pollingTimer = null;
    let statsTimer = null;
    let currentSearchQuery = '';
    let trendingTimer = null;
    let toastTimer = null;

    const albumCards = new Map();
    let albumsList = [];
    let showAllHome = false;
    let albumsSig = '';
    let isFetchingAlbums = false;
    let albumsTimer = null;
    let albumsLoadedOnce = false;
    let originalTitle = document.title;
    let savedScrollTop = 0;
    let albumReqToken = 0;
    let albumSlug = '';
    let albumData = null;
    let albumVideos = [];
    let albumPhotos = [];
    let activeTab = 'photo';
    let currentItems = [];
    let lbIndex = 0;

    const byId = id => document.getElementById(id);
    const getChannel = () => byId(CONFIG.channelId);
    const getContainer = () => byId(CONFIG.containerId);
    const getScroller = () => {
        const ch = getChannel();
        return (ch && ch.querySelector('.oc-channel-body')) || getContainer();
    };

    /* ---------- Client ID & Storage ---------- */
    function safeStorageGet(key) {
        try { return localStorage.getItem(key); }
        catch (e) { return null; }
    }
    function safeStorageSet(key, value) {
        try { localStorage.setItem(key, value); }
        catch (e) { }
    }
    function makeClientId() {
        var s = '';
        try {
            var a = new Uint8Array(12);
            (window.crypto || window.msCrypto).getRandomValues(a);
            for (var i = 0; i < a.length; i++) s += ('0' + a[i].toString(16)).slice(-2);
        } catch (e) {
            s = Math.random().toString(16).slice(2) + Math.random().toString(16).slice(2);
        }
        return 'c' + Date.now().toString(36) + s;
    }
    var clientId = safeStorageGet('oc_client_id');
    if (!clientId || !/^[A-Za-z0-9_-]{8,64}$/.test(clientId)) {
        clientId = makeClientId().slice(0, 60);
        safeStorageSet('oc_client_id', clientId);
    }

    function makeSvg(pathMarkup, viewBox) {
        const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        svg.setAttribute('viewBox', viewBox || '0 0 24 24');
        svg.setAttribute('aria-hidden', 'true');
        svg.innerHTML = pathMarkup;
        return svg;
    }

    function showToast(message) {
        const channel = getChannel();
        if (!channel) return;
        let toast = channel.querySelector('.alv-toast');
        if (!toast) {
            toast = document.createElement('div');
            toast.className = 'alv-toast';
            toast.setAttribute('role', 'status');
            toast.setAttribute('aria-live', 'polite');
            channel.appendChild(toast);
        }
        toast.textContent = message;
        toast.classList.add('is-visible');
        if (toastTimer) window.clearTimeout(toastTimer);
        toastTimer = window.setTimeout(() => { toast.classList.remove('is-visible'); }, 2200);
    }

    /* ---------- ALVEE / Subscribe / Trending কার্ড ---------- */
    function bindTopbar(topbar) {
        if (topbar.dataset.ocBound) return;
        topbar.dataset.ocBound = '1';

        const subscribe = topbar.querySelector('.alv-subscribe-btn');
        const trending = topbar.querySelector('.alv-trending-btn');

        if (subscribe) {
            if (safeStorageGet('alvSubscribeActive') === 'true' || safeStorageGet('oc_subscribed') === '1') {
                subscribe.classList.add('is-active');
            }
            subscribe.addEventListener('click', () => {
                const isActive = subscribe.classList.toggle('is-active');
                safeStorageSet('alvSubscribeActive', isActive ? 'true' : 'false');
                safeStorageSet('oc_subscribed', isActive ? '1' : '0');
            });
        }

        if (trending) {
            if (safeStorageGet('alvTrendingActive') === 'true') trending.classList.add('is-active');
            trending.addEventListener('click', () => {
                trending.classList.add('is-active');
                safeStorageSet('alvTrendingActive', 'true');
                trending.textContent = 'Ok Done';
                if (trendingTimer) window.clearTimeout(trendingTimer);
                trendingTimer = window.setTimeout(() => { trending.textContent = 'Trending'; }, 1500);
            });
        }
    }

    function moveTopbar() {
        const channel = getChannel();
        const body = channel ? channel.querySelector('.oc-channel-body') : null;
        if (!body) return;

        const existing = channel.querySelector('.alv-topbar');
        if (existing) {
            if (!body.contains(existing)) body.insertBefore(existing, body.firstChild);
            bindTopbar(existing);
            return;
        }

        const topbar = document.createElement('div');
        topbar.className = 'alv-topbar';

        const brand = document.createElement('div');
        brand.className = 'alv-brand';
        brand.setAttribute('role', 'link');
        brand.setAttribute('aria-label', 'ALVEE');
        brand.style.cursor = 'pointer';
        brand.addEventListener('click', () => { window.location.href = CONFIG.brandLinkURL; });

        const subscribe = document.createElement('button');
        subscribe.type = 'button';
        subscribe.className = 'alv-header-btn alv-subscribe-btn';
        subscribe.textContent = 'Subscribe';

        const trending = document.createElement('button');
        trending.type = 'button';
        trending.className = 'alv-header-btn alv-trending-btn';
        trending.textContent = 'Trending';

        topbar.append(brand, subscribe, trending);
        body.insertBefore(topbar, body.firstChild);
        bindTopbar(topbar);
    }

    function getSafeMediaURL(post) {
        const postId = post.message_id ?? post.id;
        if (postId === undefined || postId === null || postId === '') return null;
        return CONFIG.mediaURLPrefix + encodeURIComponent(String(postId));
    }

    function getThumbURL(post) {
        const direct = post.thumbnail_url || post.thumbnail;
        if (direct) return direct;
        const postId = post.message_id ?? post.id;
        if (postId === undefined || postId === null || postId === '') return null;
        return CONFIG.thumbURLPrefix + encodeURIComponent(String(postId));
    }

    function getMediaType(post) {
        const type = String(post.media_type || '').toLowerCase();
        const mime = String(post.mime_type || post.mime || '').toLowerCase();
        const isVideo = type === 'video' || mime.startsWith('video/') || (type === 'animation' && !mime.startsWith('image/'));
        const isImage = !isVideo && (type === 'photo' || mime.startsWith('image/') || type === 'animation');
        return isVideo ? 'video' : (isImage ? 'photo' : null);
    }

    function getSearchText(item) {
        const post = item.post || {};
        return [post.text, post.caption, post.title, post.file_name, item.mediaType, item.id].map(v => String(v || '')).join(' ').toLowerCase();
    }

    function matchesSearch(item, query) {
        return !query || getSearchText(item).includes(query);
    }

    function readCount(post, keys) {
        for (const key of keys) {
            const value = post[key];
            if (value !== undefined && value !== null && value !== '') {
                const number = Number(value);
                if (Number.isFinite(number) && number >= 0) return number;
            }
        }
        return null;
    }

    function formatCount(value) {
        if (value === null || value === undefined) return '—';
        if (value >= 1000000) return (value / 1000000).toFixed(value >= 10000000 ? 0 : 1).replace(/\.0$/, '') + 'M';
        if (value >= 1000) return (value / 1000).toFixed(value >= 10000 ? 0 : 1).replace(/\.0$/, '') + 'K';
        return String(value);
    }

    function createStatButton(className, iconMarkup, countText) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'alv-stat-item ' + className;
        button.appendChild(makeSvg(iconMarkup));
        const count = document.createElement('span');
        count.className = 'alv-stat-count';
        count.textContent = countText;
        button.appendChild(count);
        return { button, count };
    }

    function createStatItem(className, iconMarkup, countText, title) {
        const item = document.createElement('div');
        item.className = 'alv-stat-item ' + className;
        if (title) item.title = title;
        item.appendChild(makeSvg(iconMarkup));
        const count = document.createElement('span');
        count.className = 'alv-stat-count';
        count.textContent = countText;
        item.appendChild(count);
        return item;
    }

    /* ---------- মিডিয়ার আসল রেশিও ---------- */
    function getPostDimensions(post) {
        const width = Number(post.width || post.video_width || post.photo_width || post.media_width || 0);
        const height = Number(post.height || post.video_height || post.photo_height || post.media_height || 0);
        return width > 0 && height > 0 ? { width, height } : null;
    }

    function applyNativeAspectRatio(card, frame, width, height, isReal) {
        const w = Number(width); const h = Number(height);
        if (!Number.isFinite(w) || !Number.isFinite(h) || w <= 0 || h <= 0) return;
        if (!isReal && frame.dataset.ocAr) return;
        if (isReal) frame.dataset.ocAr = '1';
        card.style.setProperty('--ar-w', String(w));
        card.style.setProperty('--ar-h', String(h));
    }

    const frameObserver = HAS_IO ? new IntersectionObserver(entries => {
        entries.forEach(en => {
            if (!en.isIntersecting) return;
            frameObserver.unobserve(en.target);
            const v = en.target.querySelector('video');
            if (v) activateVideo(v);
        });
    }, { root: getScroller(), rootMargin: '900px 0px' }) : null;

    function activateVideo(video) {
        try {
            if (video.getAttribute('preload') !== 'metadata') video.setAttribute('preload', 'metadata');
            if (video.readyState === 0 && video.networkState !== 2) video.load();
        } catch (e) { }
    }

    function setupRetry(frame, media, finish) {
        var tries = 0;
        media.addEventListener('error', function () {
            if (frame.classList.contains('is-loaded')) return;
            if (tries >= 5) { finish(); return; }
            tries++;
            var base = media.dataset.ocSrc || media.getAttribute('src');
            media.dataset.ocSrc = base;
            window.setTimeout(function () {
                media.src = base + (base.indexOf('?') > -1 ? '&' : '?') + 'r=' + Date.now();
                if (media.tagName === 'VIDEO') { try { media.load(); } catch (e) { } }
            }, 2500 * tries);
        });
    }

    function loadThumb(frame, thumbBg, url, onSize) {
        var tries = 0;
        (function attempt() {
            var im = new Image();
            im.onload = function () {
                if (onSize && im.naturalWidth > 0 && im.naturalHeight > 0) onSize(im.naturalWidth, im.naturalHeight);
                thumbBg.style.backgroundImage = 'url("' + im.src + '")';
                thumbBg.classList.add('is-ready');
                frame.classList.add('has-thumb');
            };
            im.onerror = function () {
                tries++;
                if (tries <= 5 && thumbBg.parentNode) window.setTimeout(attempt, 2500 * tries);
            };
            im.src = url + (tries ? (url.indexOf('?') > -1 ? '&' : '?') + 'r=' + Date.now() : '');
        })();
    }

    function prepareFrame(frame) {
        var media = frame.querySelector('video, img');
        if (!media || frame.dataset.ocLoad) return;
        frame.dataset.ocLoad = '1';

        var done = false;
        function finish() {
            if (done) return;
            done = true;
            frame.classList.remove('is-loading');
            frame.classList.add('is-loaded');
            var layers = frame.querySelectorAll('.alv-thumb-bg, .alv-blur-wave');
            window.setTimeout(function () {
                for (var i = 0; i < layers.length; i++) {
                    if (layers[i].parentNode) layers[i].parentNode.removeChild(layers[i]);
                }
            }, 600);
        }
        frame._ocFinish = finish;

        if (media.tagName === 'IMG') {
            if (media.complete && media.naturalWidth > 0) { finish(); return; }
            frame.classList.add('is-loading');
            media.addEventListener('load', finish);
            setupRetry(frame, media, finish);
            return;
        }

        if (media.readyState >= 2) { finish(); return; }
        frame.classList.add('is-loading');
        ['loadeddata', 'seeked', 'canplay', 'playing'].forEach(function (ev) {
            media.addEventListener(ev, finish);
        });
        setupRetry(frame, media, finish);
        media.addEventListener('loadedmetadata', function () {
            if (!media.poster && media.paused && !media.currentTime) {
                try { media.currentTime = 0.1; } catch (e) { }
            }
            window.setTimeout(finish, 600);
        });
        if (frameObserver) frameObserver.observe(frame); else activateVideo(media);
    }

    /* ---------- একটাই ভিডিও চলার নিয়ম ---------- */
    document.addEventListener('play', function (e) {
        var t = e.target;
        if (!t || t.tagName !== 'VIDEO') return;
        var all = document.querySelectorAll('video');
        for (var i = 0; i < all.length; i++) {
            if (all[i] !== t && !all[i].paused) { try { all[i].pause(); } catch (err) { } }
        }
    }, true);

    const pauseObserver = HAS_IO ? new IntersectionObserver(function (entries) {
        entries.forEach(function (en) {
            var v = en.target;
            if (en.intersectionRatio < 0.2 && !v.paused) {
                try { v.pause(); } catch (e) { }
            }
        });
    }, { threshold: [0, 0.2, 0.5] }) : null;

    /* ---------- View / Like / Save ---------- */
    function jget(url) {
        return fetch(url, { credentials: 'same-origin' }).then(r => {
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        });
    }

    function jpost(url, body) {
        return fetch(url, {
            method: 'POST', credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body || {})
        }).then(r => {
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        });
    }

    function applyStats(id, s) {
        if (!s) return;
        document.querySelectorAll('.alv-post[data-oc-id="' + id + '"]').forEach(post => {
            const viewsEl = post.querySelector('.alv-stat-views .alv-stat-count');
            const likeBtn = post.querySelector('.alv-stat-like');
            const saveBtn = post.querySelector('.alv-stat-bookmark');

            if (viewsEl && s.views !== undefined) viewsEl.textContent = formatCount(s.views);

            if (likeBtn && s.likes !== undefined) {
                const likeCount = likeBtn.querySelector('.alv-stat-count');
                if (likeCount) {
                    likeCount.dataset.n = String(s.likes);
                    likeCount.textContent = formatCount(s.likes);
                }
                likeBtn.classList.toggle('is-active', !!s.liked);
                safeStorageSet('alvLiked_' + id, String(!!s.liked));
            }

            if (saveBtn && s.saves !== undefined) {
                const saveCount = saveBtn.querySelector('.alv-stat-count');
                if (saveCount) {
                    saveCount.dataset.n = String(s.saves);
                    saveCount.textContent = s.saves > 0 ? formatCount(s.saves) : 'Save';
                }
                saveBtn.classList.toggle('is-active', !!s.saved);
                safeStorageSet('alvBookmarked_' + id, String(!!s.saved));
            }
        });
    }

    let pendingStatIds = {};
    let statsFetchTimer = null;

    function queueStats(id) {
        pendingStatIds[id] = 1;
        if (!statsFetchTimer) statsFetchTimer = window.setTimeout(flushStats, 150);
    }

    function fetchStatsFor(ids) {
        for (let i = 0; i < ids.length; i += 100) {
            const chunk = ids.slice(i, i + 100);
            jget(CONFIG.statsURL + '?client_id=' + encodeURIComponent(clientId) + '&ids=' + chunk.join(','))
                .then(data => {
                    const stats = (data && data.stats) || {};
                    Object.keys(stats).forEach(id => applyStats(id, stats[id]));
                })
                .catch(() => { });
        }
    }

    function flushStats() {
        statsFetchTimer = null;
        const ids = Object.keys(pendingStatIds);
        pendingStatIds = {};
        if (ids.length) fetchStatsFor(ids);
    }

    function refreshAllStats() {
        const ids = Array.from(mediaCards.keys()).slice(0, 150);
        if (ids.length) fetchStatsFor(ids);
    }

    function sendAction(id, action) {
        jpost(`${CONFIG.statsURL}/${id}/${action}`, { client_id: clientId })
            .then(data => { if (data && data.stats) applyStats(id, data.stats); })
            .catch(() => { });
    }

    var viewed = {};
    try { (JSON.parse(safeStorageGet('oc_viewed') || '[]') || []).forEach(i => { viewed[i] = 1; }); } catch (e) { }

    function registerView(id) {
        if (!id || viewed[id]) return;
        viewed[id] = 1;
        safeStorageSet('oc_viewed', JSON.stringify(Object.keys(viewed).slice(-500)));
        jpost(`${CONFIG.statsURL}/${id}/view`, { client_id: clientId })
            .then(data => { if (data && data.stats) applyStats(id, data.stats); })
            .catch(() => { delete viewed[id]; });
    }

    const viewTimers = new Map();
    const viewObserver = HAS_IO ? new IntersectionObserver(entries => {
        entries.forEach(en => {
            const card = en.target;
            if (en.isIntersecting && en.intersectionRatio >= 0.35) {
                if (viewTimers.has(card)) return;
                viewTimers.set(card, window.setTimeout(() => {
                    viewTimers.delete(card);
                    registerView(card.dataset.ocId);
                }, 800));
            } else if (viewTimers.has(card)) {
                window.clearTimeout(viewTimers.get(card));
                viewTimers.delete(card);
            }
        });
    }, { threshold: [0, 0.35, 0.6] }) : null;

    function createMediaCard(item) {
        const post = item.post || {};
        const card = document.createElement('article');
        card.className = 'alv-post';
        card.dataset.ocId = item.id;
        card.dataset.metadata = getSearchText(item);

        const postHeader = document.createElement('div');
        postHeader.className = 'alv-post-head';

        const tributeButton = document.createElement('button');
        tributeButton.type = 'button';
        tributeButton.className = 'alv-tribute-btn';
        tributeButton.innerHTML = `<svg class="alv-tribute-icon" viewBox="0 0 24 24"><path d="M20.8 8.7c0 5.2-8.8 10.1-8.8 10.1S3.2 13.9 3.2 8.7A4.5 4.5 0 0 1 12 6.5a4.5 4.5 0 0 1 8.8 2.2Z"/></svg><span>A Tribute</span>`;
        tributeButton.addEventListener('click', () => showToast('This video is dedicated to you.'));
        postHeader.appendChild(tributeButton);

        const mediaFrame = document.createElement('div');
        mediaFrame.className = 'alv-media-frame';
        const knownDimensions = getPostDimensions(post);
        const thumbURL = getThumbURL(post);

        if (knownDimensions) applyNativeAspectRatio(card, mediaFrame, knownDimensions.width, knownDimensions.height, true);

        if (thumbURL) {
            const thumbBg = document.createElement('div');
            thumbBg.className = 'alv-thumb-bg';
            const blurWave = document.createElement('div');
            blurWave.className = 'alv-blur-wave';
            mediaFrame.appendChild(thumbBg);
            mediaFrame.appendChild(blurWave);
            loadThumb(mediaFrame, thumbBg, thumbURL, (w, h) => applyNativeAspectRatio(card, mediaFrame, w, h, false));
        }

        if (item.mediaType === 'video') {
            const video = document.createElement('video');
            video.controls = true; video.playsInline = true;
            video.setAttribute('controlsList', 'nodownload noplaybackrate');
            video.disablePictureInPicture = true;
            video.oncontextmenu = function (e) { e.preventDefault(); return false; };
            video.preload = HAS_IO ? 'none' : 'metadata';
            if (thumbURL) video.poster = thumbURL;
            video.addEventListener('loadedmetadata', () => applyNativeAspectRatio(card, mediaFrame, video.videoWidth, video.videoHeight, true), { once: true });
            video.src = item.mediaURL;
            mediaFrame.appendChild(video);
        } else {
            const image = document.createElement('img');
            image.loading = 'lazy'; image.draggable = false;
            image.addEventListener('load', () => applyNativeAspectRatio(card, mediaFrame, image.naturalWidth, image.naturalHeight, true), { once: true });
            image.src = item.mediaURL;
            mediaFrame.appendChild(image);
        }

        prepareFrame(mediaFrame);

        const statsBar = document.createElement('div');
        statsBar.className = 'alv-stats-bar';

        const views = readCount(post, ['view_count', 'views_count', 'views']);
        const serverLikes = readCount(post, ['like_count', 'likes_count', 'likes']) || 0;

        const viewsItem = createStatItem('alv-stat-views', '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>', formatCount(views), 'Views');
        statsBar.appendChild(viewsItem);

        const likeKey = 'alvLiked_' + item.id;
        const likeControl = createStatButton('alv-stat-like', '<path d="M20.8 8.7c0 5.2-8.8 10.1-8.8 10.1S3.2 13.9 3.2 8.7A4.5 4.5 0 0 1 12 6.5a4.5 4.5 0 0 1 8.8 2.2Z"/>', formatCount(serverLikes));
        likeControl.count.dataset.n = String(serverLikes);
        likeControl.button.title = 'Likes';
        if (safeStorageGet(likeKey) === 'true') likeControl.button.classList.add('is-active');

        likeControl.button.addEventListener('click', () => {
            const active = !likeControl.button.classList.contains('is-active');
            likeControl.button.classList.toggle('is-active', active);
            const n = Math.max(0, (Number(likeControl.count.dataset.n) || 0) + (active ? 1 : -1));
            likeControl.count.dataset.n = String(n);
            likeControl.count.textContent = formatCount(n);
            safeStorageSet(likeKey, String(active));
            sendAction(item.id, 'like');
        });
        statsBar.appendChild(likeControl.button);

        const commentControl = createStatButton('alv-stat-comment', '<path d="M20 11.5a7.5 7.5 0 0 1-8 7.5 8.7 8.7 0 0 1-3.8-.9L4 20l1.3-3.5A7.1 7.1 0 0 1 4 12c0-4.1 3.6-7.5 8-7.5s8 2.9 8 7Z"/>', formatCount(readCount(post, ['comment_count'])));
        commentControl.button.addEventListener('click', () => showToast('Comments are not connected yet.'));
        statsBar.appendChild(commentControl.button);

        const bookmarkKey = 'alvBookmarked_' + item.id;
        const wasSaved = safeStorageGet(bookmarkKey) === 'true';
        const bookmarkControl = createStatButton('alv-stat-bookmark', '<path d="M6 4.5A1.5 1.5 0 0 1 7.5 3h9A1.5 1.5 0 0 1 18 4.5V21l-6-4-6 4V4.5Z"/>', wasSaved ? 'Saved' : 'Save');
        bookmarkControl.count.dataset.n = '0';
        bookmarkControl.button.title = 'Saves';
        if (wasSaved) bookmarkControl.button.classList.add('is-active');

        bookmarkControl.button.addEventListener('click', () => {
            const active = !bookmarkControl.button.classList.contains('is-active');
            bookmarkControl.button.classList.toggle('is-active', active);
            const n = Math.max(0, (Number(bookmarkControl.count.dataset.n) || 0) + (active ? 1 : -1));
            bookmarkControl.count.dataset.n = String(n);
            bookmarkControl.count.textContent = n > 0 ? formatCount(n) : 'Save';
            safeStorageSet(bookmarkKey, String(active));
            sendAction(item.id, 'save');
        });
        statsBar.appendChild(bookmarkControl.button);

        const telegramLink = document.createElement('a');
        telegramLink.className = 'alv-telegram-btn';
        telegramLink.href = CONFIG.telegramURL;
        telegramLink.target = '_blank';
        telegramLink.rel = 'noopener';
        telegramLink.innerHTML = `<svg viewBox="0 0 24 24"><path d="m21 3-7.2 18-3.8-7.1L3 10.1 21 3Z"/><path d="m10 13.9 4.1-4.1"/></svg><span>Join Telegram</span>`;

        card.append(postHeader, mediaFrame, statsBar, telegramLink);
        return card;
    }

    function registerCard(card, item) {
        mediaCards.set(item.id, card);
        const video = card.querySelector('video');
        if (video && pauseObserver) pauseObserver.observe(video);
        if (viewObserver) viewObserver.observe(card);
        queueStats(item.id);
        updateFeedSep();
    }

    function ensureSentinel() {
        const container = getContainer();
        let sentinel = document.createElement('div');
        const existing = container.querySelector('.oc-feed-sentinel');
        if (existing) return existing;
        sentinel.className = 'oc-feed-sentinel';
        container.appendChild(sentinel);
        return sentinel;
    }

    function appendBatch(items) {
        const container = getContainer();
        const fragment = document.createDocumentFragment();
        items.forEach(item => {
            const card = createMediaCard(item);
            fragment.appendChild(card);
            registerCard(card, item);
        });
        container.insertBefore(fragment, ensureSentinel());
    }

    function prependBatch(items) {
        const container = getContainer();
        const scroller = getScroller();
        const oldHeight = scroller.scrollHeight;
        const oldScrollTop = scroller.scrollTop;
        const fragment = document.createDocumentFragment();
        items.forEach(item => {
            const card = createMediaCard(item);
            fragment.appendChild(card);
            registerCard(card, item);
        });
        const firstPost = container.querySelector('.alv-post');
        container.insertBefore(fragment, firstPost || ensureSentinel());
        if (oldScrollTop > 0 && !isAlbumOpen()) scroller.scrollTop = oldScrollTop + (scroller.scrollHeight - oldHeight);
    }

    function renderNextBatch() {
        if (isRenderingBatch || !pendingMedia.length) return;
        isRenderingBatch = true;
        const batch = [], remaining = [];
        pendingMedia.forEach(item => matchesSearch(item, currentSearchQuery) ? (batch.length < CONFIG.batchSize ? batch.push(item) : remaining.push(item)) : remaining.push(item));
        pendingMedia = remaining;
        if (batch.length) appendBatch(batch);
        isRenderingBatch = false;
        ensureSentinel().textContent = pendingMedia.length ? 'Scroll down to load more...' : '';
    }

    function renderTelegramPosts(posts) {
        if (!Array.isArray(posts)) return;
        const incoming = [];
        posts.forEach(post => {
            const id = String(post.message_id ?? post.id);
            if (knownMediaIds.has(id)) return;
            const mediaURL = getSafeMediaURL(post);
            const mediaType = getMediaType(post);
            if (mediaURL && mediaType) {
                knownMediaIds.add(id);
                incoming.push({ id, post, mediaURL, mediaType });
            }
        });
        if (!incoming.length) return;
        const firstBatch = incoming.slice(0, CONFIG.batchSize);
        pendingMedia = incoming.slice(CONFIG.batchSize).concat(pendingMedia);
        prependBatch(firstBatch);
        ensureSentinel().textContent = pendingMedia.length ? 'Scroll down to load more...' : '';
    }

    async function fetchTelegramMedia() {
        if (isFetchingMedia) return;
        isFetchingMedia = true;
        try {
            const response = await fetch(CONFIG.apiURL, { method: 'GET', headers: { 'Accept': 'application/json' } });
            const data = await response.json();
            if (data.ok) renderTelegramPosts(data.posts);
        } catch (error) { console.error('Media fetch error:', error); }
        isFetchingMedia = false;
    }

    /* ================= ALBUMS ================= */
    function injectAlbumStyles() {
        try {
            var st = document.createElement('style');
            st.setAttribute('data-oc-albums', '1');
            st.textContent = `
.alv-post[hidden]{display:none !important;}
.oc-al-wrap{width:100%;max-width:420px;flex-shrink:0;}
#ocAlbumsHome [hidden], #ocAlbumView [hidden]{display:none !important;}
.oc-stats{display:grid;grid-template-columns:repeat(3,1fr);width:100%;max-width:420px;margin:-6px auto 16px;padding:8px 6px;flex-shrink:0;
background:linear-gradient(135deg,rgba(255,255,255,.13),rgba(255,255,255,.04));backdrop-filter:blur(2px);-webkit-backdrop-filter:blur(2px);
border:1px solid rgba(255,255,255,.18);border-radius:18px;box-shadow:0 10px 30px rgba(0,0,0,.45),inset 0 1px 0 rgba(255,255,255,.14);}
.oc-stat{display:flex;flex-direction:column;align-items:center;gap:1px;text-align:center;min-width:0;}
.oc-stat + .oc-stat{border-left:1px solid rgba(255,255,255,.1);}
.oc-stat-top{display:flex;align-items:center;justify-content:center;gap:6px;}
.oc-stat svg{width:16px;height:16px;color:#f5b82e;flex:0 0 auto;}
.oc-stat.views svg{color:#a78bfa;}
.oc-stat-value{font-size:15px;font-weight:700;color:#fff;line-height:1.2;}
.oc-stat-label{font-size:10px;color:#9ca3af;letter-spacing:.3px;line-height:1.2;}
.oc-al-head{margin:4px 0 12px;font-size:22px;font-weight:800;letter-spacing:.2px;color:#fff;text-align:left;}
.oc-al-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:10px;}
.oc-al-state{grid-column:1 / -1;text-align:center;color:#9ca3af;font-size:14px;padding:30px 10px;}
.oc-al-state.error{color:#f87171;}
.oc-al-card{min-width:0;border-radius:20px;overflow:hidden;background:#121214;border:1px solid rgba(255,255,255,.08);
box-shadow:0 8px 20px rgba(0,0,0,.35);transition:transform .25s ease,border-color .25s ease;cursor:pointer;}
.oc-al-card[hidden]{display:none !important;}
.oc-al-card:hover{transform:translateY(-3px);border-color:rgba(255,255,255,.18);}
.oc-al-thumb{position:relative;width:100%;aspect-ratio:9 / 14;
background:linear-gradient(110deg,#1a1a1e 30%,#26262c 50%,#1a1a1e 70%);background-size:200% 100%;animation:ocAlShimmer 1.4s infinite linear;}
.oc-al-thumb.loaded{animation:none;background:#1a1a1e;}
@keyframes ocAlShimmer{to{background-position:-200% 0;}}
.oc-al-thumb img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;opacity:0;transition:opacity .35s ease;pointer-events:none;}
.oc-al-thumb img.ok{opacity:1;}
.oc-al-item .oc-al-thumb{aspect-ratio:3 / 4;}
.oc-al-caption{padding:12px 12px 14px;font-size:14px;font-weight:600;color:#e5e7eb;line-height:1.35;
display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;min-height:calc(2 * 1.35em + 26px);}
.oc-al-badge{position:absolute;left:10px;bottom:10px;z-index:2;display:inline-flex;align-items:center;gap:6px;padding:5px 11px;
border-radius:999px;background:rgba(10,10,12,.6);backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);
border:1px solid rgba(255,255,255,.18);font-size:12px;font-weight:600;color:#fff;line-height:1.2;pointer-events:none;}
.oc-al-play{position:absolute;z-index:2;right:8px;top:8px;width:28px;height:28px;border-radius:50%;display:grid;place-items:center;
background:rgba(10,10,12,.6);border:1px solid rgba(255,255,255,.2);pointer-events:none;}
.oc-al-play svg{width:13px;height:13px;fill:#fff;}
.oc-al-bar{display:flex;align-items:center;gap:12px;margin-top:4px;}
.oc-al-meta{min-width:0;flex:1;}
.oc-al-title{font-size:17px;font-weight:700;line-height:1.3;color:#fff;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;}
.oc-al-sub{font-size:12px;color:#9ca3af;margin-top:2px;}
.oc-al-tabs{margin-top:16px;display:grid;grid-template-columns:1fr 1fr;gap:8px;}
.oc-al-tab{padding:10px;border-radius:14px;cursor:pointer;font-size:13px;font-weight:600;color:#d1d5db;text-align:center;
background:rgba(255,255,255,.05);backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);
border:1px solid rgba(255,255,255,.1);transition:border-color .2s ease;}
.oc-al-tab:hover{border-color:rgba(255,255,255,.28);}
.oc-al-tab.active{background:#f5b82e;color:#1a1204;border-color:#f5b82e;}
.oc-al-playerwrap{margin-top:16px;}
.oc-al-player{border-radius:20px;overflow:hidden;background:#000;border:1px solid rgba(255,255,255,.1);box-shadow:0 10px 26px rgba(0,0,0,.45);}
.oc-al-player video{display:block;width:100%;max-height:72vh;background:#000;object-fit:contain;}
.oc-al-items{margin-top:16px;}
.oc-al-item.active{border-color:#f5b82e;}
.oc-al-stats{margin-top:18px;display:flex;flex-wrap:wrap;gap:8px;padding:8px;border-radius:20px;
background:linear-gradient(145deg,rgba(255,255,255,.06),rgba(255,255,255,.02));backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px);
border:1px solid rgba(255,255,255,.09);box-shadow:0 10px 26px rgba(0,0,0,.4);}
.oc-al-ws{flex:1 1 88px;display:flex;align-items:center;justify-content:center;gap:7px;padding:11px 8px;border-radius:14px;
background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.08);color:#e5e7eb;font-size:13px;font-weight:600;
text-decoration:none;cursor:pointer;font-family:inherit;transition:border-color .2s ease;}
.oc-al-ws svg{width:16px;height:16px;flex:0 0 auto;}
button.oc-al-ws:hover{border-color:rgba(255,255,255,.3);}
.oc-al-ws.views{cursor:default;}
.oc-al-ws.views svg{color:#a78bfa;}
.oc-al-ws.like[aria-pressed="true"]{color:#fb7185;border-color:rgba(251,113,133,.6);}
.oc-al-tg{margin-top:16px;display:flex;align-items:center;justify-content:center;gap:10px;width:100%;padding:15px 18px;border-radius:999px;
text-decoration:none;color:#fff;font-size:17px;font-weight:700;background:linear-gradient(90deg,#00a3ff,#1a5cff);
box-shadow:0 8px 26px rgba(0,120,255,.45);transition:transform .2s ease,box-shadow .2s ease;}
.oc-al-tg:hover{transform:translateY(-2px);box-shadow:0 12px 30px rgba(0,120,255,.6);}
.oc-al-tg svg{width:22px;height:22px;}
.oc-al-ad{margin-top:16px;text-align:center;}
.oc-al-ad-label{display:inline-block;padding:7px 26px;border-radius:10px;background:#151518;border:1px solid rgba(255,255,255,.1);font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:13px;letter-spacing:3px;color:#8b8b93;}
.oc-al-ad-box{margin-top:14px;width:100%;aspect-ratio:6 / 5;border-radius:22px;background:#0a0a0c;border:1px solid rgba(255,255,255,.1);overflow:hidden;}
.oc-channel-header{transition:margin-top .3s ease,opacity .25s ease,transform .3s ease;}
.oc-channel-header.oc-hdr-hidden{margin-top:calc(-1 * var(--oc-hdr-h,80px)) !important;opacity:0 !important;transform:translateY(-100%) !important;pointer-events:none !important;}
.oc-al-related-head{margin-top:30px;padding-top:22px;border-top:1px solid rgba(255,255,255,.1);display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap;}
.oc-al-related-title{display:flex;align-items:center;gap:10px;font-size:20px;font-weight:800;line-height:1.2;color:#fff;}
.oc-al-related-title::before{content:"";width:5px;height:28px;border-radius:3px;background:#ff9500;flex:0 0 auto;}
.oc-al-rel-actions{display:flex;gap:8px;}
.oc-al-pill{display:inline-flex;align-items:center;gap:8px;padding:10px 16px;border-radius:999px;cursor:pointer;background:#1c1c20;color:#fff;font-family:inherit;font-size:14px;font-weight:600;border:1px solid rgba(255,255,255,.14);transition:border-color .2s ease,background .2s ease;}
.oc-al-pill:hover{border-color:rgba(255,255,255,.35);}
.oc-al-pill.active{background:#f5b82e;color:#1a1204;border-color:#f5b82e;}
.oc-al-pill svg{width:17px;height:17px;}
.oc-al-pill.spin svg{animation:ocAlSpin .5s ease;}
@keyframes ocAlSpin{to{transform:rotate(360deg);}}
#ocAllAlbumsGrid{margin-top:16px;}
.oc-al-footer{margin-top:30px;padding:22px 4px 40px;border-top:1px solid rgba(255,255,255,.08);color:#9ca3af;font-size:12.5px;display:flex;flex-wrap:wrap;gap:14px;justify-content:space-between;align-items:center;}
.oc-al-fbrand{font-weight:800;font-style:italic;letter-spacing:.5px;color:#fff;}
.oc-al-footer nav{display:flex;gap:14px;flex-wrap:wrap;}
.oc-al-footer a{color:#9ca3af;text-decoration:none;}
.oc-al-footer a:hover{color:#fff;}
.oc-al-sep{margin:22px 0 4px;padding-top:18px;border-top:1px solid rgba(255,255,255,.1);display:flex;align-items:center;gap:10px;
font-size:20px;font-weight:800;line-height:1.2;color:#fff;}
.oc-al-sep::before{content:"";width:5px;height:28px;border-radius:3px;background:#ff9500;flex:0 0 auto;}
.oc-lb{position:fixed;inset:0;z-index:200;display:none;background:rgba(0,0,0,.94);align-items:center;justify-content:center;}
.oc-lb.open{display:flex;}
.oc-lb-stage{width:100%;height:100%;display:flex;align-items:center;justify-content:center;padding:56px 0;}
.oc-lb-stage img{max-width:100%;max-height:100%;object-fit:contain;background:#000;-webkit-touch-callout:none;}
.oc-lb-btn{position:absolute;z-index:2;width:42px;height:42px;border-radius:50%;display:grid;place-items:center;color:#fff;cursor:pointer;
background:rgba(255,255,255,.12);border:1px solid rgba(255,255,255,.2);backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);}
.oc-lb-btn svg{width:20px;height:20px;}
.oc-lb-close{top:calc(env(safe-area-inset-top,0px) + 12px);right:12px;}
.oc-lb-prev{left:10px;top:50%;transform:translateY(-50%);}
.oc-lb-next{right:10px;top:50%;transform:translateY(-50%);}
.oc-lb-count{position:absolute;top:calc(env(safe-area-inset-top,0px) + 22px);left:0;right:0;text-align:center;font-size:13px;font-weight:600;color:#e5e7eb;pointer-events:none;}
.oc-al-more{margin-top:18px;}
.oc-al-more-row{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:12px;}
.oc-al-ghost{display:inline-flex;align-items:center;gap:7px;padding:8px 14px;border-radius:999px;cursor:pointer;
background:rgba(255,255,255,.05);backdrop-filter:blur(8px);-webkit-backdrop-filter:blur(8px);
border:1px solid rgba(255,255,255,.18);color:#fff;font-family:inherit;font-size:13px;font-weight:600;transition:border-color .2s ease;}
.oc-al-ghost:hover{border-color:rgba(255,255,255,.4);}
.oc-al-ghost[hidden]{display:none !important;}
.oc-al-ghost svg{width:16px;height:16px;animation:ocAlFloat 1.6s ease-in-out infinite;}
@keyframes ocAlFloat{50%{transform:translateY(-3px);}}
.oc-al-flag{display:flex;width:100%;height:10px;border-radius:2px;overflow:hidden;background:transparent;}
.oc-al-flag i{flex:1;display:block;}
.oc-al-flag i:nth-child(1){background:#008C45;}
.oc-al-flag i:nth-child(2){background:#F4F5F0;}
.oc-al-flag i:nth-child(3){background:#CD212A;}
@media (min-width:720px){.oc-al-grid{gap:14px;}}
@media (prefers-reduced-motion: reduce){.oc-al-thumb{animation:none !important;}.oc-al-card{transition:none !important;}}
`;
            (document.head || document.documentElement).appendChild(st);
        } catch (e) { }
    }

    const PLAY_SVG = '<svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>';

    function fmtN(n) {
        n = Number(n) || 0;
        if (n >= 1000000) return (n / 1000000).toFixed(1).replace(/\.0$/, '') + 'M';
        if (n >= 1000) return (n / 1000).toFixed(1).replace(/\.0$/, '') + 'K';
        return String(n);
    }

    function albumCountLabel(a) {
        const parts = [];
        if (a.videos) parts.push(a.videos + (a.videos === 1 ? ' Video' : ' Videos'));
        if (a.photos) parts.push(a.photos + (a.photos === 1 ? ' Photo' : ' Photos'));
        return parts.join(' • ');
    }

    function setText(id, value) { const el = byId(id); if (el) el.textContent = value; }

    function stateBox(parent, text, isError) {
        parent.innerHTML = '';
        const d = document.createElement('div');
        d.className = 'oc-al-state' + (isError ? ' error' : '');
        d.textContent = text;
        parent.appendChild(d);
    }

    function makeAlThumb(src, eager) {
        const thumb = document.createElement('div');
        thumb.className = 'oc-al-thumb';
        const img = new Image();
        img.loading = eager ? 'eager' : 'lazy';
        img.decoding = 'async';
        img.alt = '';
        img.draggable = false;
        img.onload = function () { img.classList.add('ok'); thumb.classList.add('loaded'); };
        img.onerror = function () { thumb.classList.add('loaded'); };
        if (src) img.src = src; else thumb.classList.add('loaded');
        thumb.appendChild(img);
        return thumb;
    }

    function isAlbumOpen() {
        const v = byId('ocAlbumView');
        return !!(v && !v.hidden);
    }

    function pauseAllVideos() {
        document.querySelectorAll('video').forEach(v => { try { if (!v.paused) v.pause(); } catch (e) { } });
    }

    function buildAlbumUI() {
        const container = getContainer();
        if (!container || !container.parentNode || byId('ocAlbumsHome')) return;
        const parent = container.parentNode;

        const home = document.createElement('section');
        home.id = 'ocAlbumsHome';
        home.className = 'oc-al-wrap';
        home.setAttribute('aria-label', 'Albums');
        home.innerHTML =
            '<h2 class="oc-al-head">Videos &amp; Photos</h2>' +
            '<div class="oc-al-grid" id="ocAlbumsGrid"><div class="oc-al-state">Loading...</div></div>' +
            '<div class="oc-al-more" id="ocAlMore">' +
                '<div class="oc-al-more-row">' +
                    '<button class="oc-al-ghost" id="ocAlNewDesi" type="button">' +
                        '<svg viewBox="0 0 24 24"><defs><linearGradient id="ocNdG" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#ffd35a"/><stop offset="1" stop-color="#ff4d8d"/></linearGradient></defs><path fill="url(#ocNdG)" d="M12 2l2.4 6.2L21 9l-5 4.3L17.5 20 12 16.6 6.5 20 8 13.3 3 9l6.6-.8z"/></svg>New Desi Collection' +
                    '</button>' +
                    '<button class="oc-al-ghost" id="ocAlSeeAll" type="button">See More All</button>' +
                '</div>' +
                '<div class="oc-al-flag"><i></i><i></i><i></i></div>' +
            '</div>' +
            '<div class="oc-al-sep" id="ocFeedSep" hidden>Latest Posts</div>';

        const view = document.createElement('section');
        view.id = 'ocAlbumView';
        view.className = 'oc-al-wrap';
        view.hidden = true;
        view.innerHTML =
            '<div class="oc-al-bar">' +
                '<div class="oc-al-meta"><div class="oc-al-title" id="ocAlTitle"></div><div class="oc-al-sub" id="ocAlSub"></div></div>' +
            '</div>' +
            /* ট্যাব: বামে Photos, ডানে Videos */
            '<div class="oc-al-tabs" id="ocAlTabs" hidden>' +
                '<div class="oc-al-tab" id="ocAlTabP" role="button">Photos</div>' +
                '<div class="oc-al-tab" id="ocAlTabV" role="button">Videos</div>' +
            '</div>' +
            '<div class="oc-al-playerwrap" id="ocAlPlayer" hidden></div>' +
            '<div class="oc-al-grid oc-al-items" id="ocAlItems"></div>' +
            '<div class="oc-al-stats" id="ocAlStats" hidden>' +
                '<div class="oc-al-ws views" title="Views">' +
                    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/></svg>' +
                    '<span id="ocAlViews">—</span>' +
                '</div>' +
                '<button class="oc-al-ws like" id="ocAlLike" type="button" aria-pressed="false">' +
                    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 14c1.49-1.46 3-3.21 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.76 0-3 .5-4.5 2-1.5-1.5-2.74-2-4.5-2A5.5 5.5 0 0 0 2 8.5c0 2.3 1.5 4.05 3 5.5l7 7Z"/></svg>' +
                    '<span id="ocAlLikeN">0</span>' +
                '</button>' +
                '<button class="oc-al-ws" id="ocAlShare" type="button">' +
                    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><path d="M8.59 13.51l6.83 3.98M15.41 6.51l-6.82 3.98"/></svg>' +
                    '<span id="ocAlShareT">Share</span>' +
                '</button>' +
            '</div>' +
            '<a class="oc-al-tg" href="' + CONFIG.telegramURL + '" target="_blank" rel="noopener noreferrer">' +
                '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21.5 2.5 10.2 13.8M21.5 2.5l-6.7 19-4.6-8.4-8.4-4.6z"/></svg>' +
                'Join Telegram' +
            '</a>' +
            '<section class="oc-al-ad" aria-label="Advertisement">' +
                (CONFIG.adLabel ? '<div class="oc-al-ad-label">' + CONFIG.adLabel + '</div>' : '') +
                '<div class="oc-al-ad-box" id="ocAdSlot"></div>' +
            '</section>' +
            '<section aria-label="All albums">' +
                '<div class="oc-al-related-head">' +
                    '<div class="oc-al-related-title">All Albums</div>' +
                    '<div class="oc-al-rel-actions">' +
                        '<button class="oc-al-pill" id="ocAlShuffle" type="button">' +
                            '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 1 1-3-6.7"/><path d="M21 3v6h-6"/></svg>Shuffle' +
                        '</button>' +
                        '<button class="oc-al-pill active" id="ocAlRecommended" type="button">Recommended</button>' +
                    '</div>' +
                '</div>' +
                '<div class="oc-al-grid" id="ocAllAlbumsGrid"><div class="oc-al-state">Loading...</div></div>' +
            '</section>' +
            '<footer class="oc-al-footer">' +
                '<div><span class="oc-al-fbrand">AlveeCommunity</span> &copy; ' + new Date().getFullYear() + '</div>' +
                '<nav aria-label="Footer"><a href="/oc-collection.html">Collection</a><a href="/contact">Contact</a><a href="/privacy">Privacy</a><a href="/terms">Terms</a></nav>' +
            '</footer>';

        const stats = document.createElement('section');
        stats.id = 'ocStats';
        stats.className = 'oc-stats';
        stats.setAttribute('aria-label', 'Statistics');
        stats.innerHTML =
            '<div class="oc-stat">' +
                '<div class="oc-stat-top">' +
                    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 4v16M17 4v16M3 9h4M3 15h4M17 9h4M17 15h4"/></svg>' +
                    '<span class="oc-stat-value" id="ocStatVideos">—</span>' +
                '</div><div class="oc-stat-label">Video</div>' +
            '</div>' +
            '<div class="oc-stat">' +
                '<div class="oc-stat-top">' +
                    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="2"/><path d="M21 16l-5-5-8 8"/></svg>' +
                    '<span class="oc-stat-value" id="ocStatPhotos">—</span>' +
                '</div><div class="oc-stat-label">Photo</div>' +
            '</div>' +
            '<div class="oc-stat views">' +
                '<div class="oc-stat-top">' +
                    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/></svg>' +
                    '<span class="oc-stat-value" id="ocStatViews">—</span>' +
                '</div><div class="oc-stat-label">Views</div>' +
            '</div>';

        parent.insertBefore(stats, container);
        parent.insertBefore(home, container);
        parent.insertBefore(view, container);

        fetch('/api/collection/counts?scope=all', { cache: 'no-store' })
            .then(function (r) { return r.json(); })
            .then(function (d) { setText('ocStatVideos', fmtN(d.videos)); setText('ocStatPhotos', fmtN(d.photos)); })
            .catch(function () { });
        fetch('/api/site/views', { method: 'POST', cache: 'no-store' })
            .then(function (r) { return r.json(); })
            .then(function (d) { setText('ocStatViews', fmtN(d.total)); })
            .catch(function () { });

        if (!byId('ocLb')) {
            const lb = document.createElement('div');
            lb.className = 'oc-lb';
            lb.id = 'ocLb';
            lb.setAttribute('role', 'dialog');
            lb.setAttribute('aria-modal', 'true');
            lb.innerHTML =
                '<div class="oc-lb-count" id="ocLbCount"></div>' +
                '<div class="oc-lb-btn oc-lb-close" id="ocLbClose" role="button" aria-label="Close"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M18 6 6 18M6 6l12 12"/></svg></div>' +
                '<div class="oc-lb-btn oc-lb-prev" id="ocLbPrev" role="button" aria-label="Previous"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 18l-6-6 6-6"/></svg></div>' +
                '<div class="oc-lb-btn oc-lb-next" id="ocLbNext" role="button" aria-label="Next"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 6l6 6-6 6"/></svg></div>' +
                '<div class="oc-lb-stage" id="ocLbStage"></div>';
            (getChannel() || document.body).appendChild(lb);
        }

        byId('ocAlTabV').addEventListener('click', function () { setTab('video'); });
        byId('ocAlTabP').addEventListener('click', function () { setTab('photo'); });
        bindShuffle();
        byId('ocAlSeeAll').addEventListener('click', function () {
            showAllHome = true;
            applySearch();
            updateSeeAll();
        });

        byId('ocLbClose').addEventListener('click', closeLightbox);
        byId('ocLbPrev').addEventListener('click', function () { stepLightbox(-1); });
        byId('ocLbNext').addEventListener('click', function () { stepLightbox(1); });
        document.addEventListener('keydown', function (e) {
            const lb = byId('ocLb');
            if (!lb || !lb.classList.contains('open')) return;
            if (e.key === 'Escape') closeLightbox();
            else if (e.key === 'ArrowLeft') stepLightbox(-1);
            else if (e.key === 'ArrowRight') stepLightbox(1);
        });
        let touchX = null;
        const lbEl = byId('ocLb');
        lbEl.addEventListener('touchstart', function (e) { touchX = e.touches.length === 1 ? e.touches[0].clientX : null; }, { passive: true });
        lbEl.addEventListener('touchend', function (e) {
            if (touchX === null) return;
            const dx = e.changedTouches[0].clientX - touchX;
            touchX = null;
            if (Math.abs(dx) > 60) stepLightbox(dx < 0 ? 1 : -1);
        }, { passive: true });
    }

    /* ---------------- All Albums + Shuffle ---------------- */
    let orderedAlbums = [];

    function renderAllAlbums() {
        const grid = byId('ocAllAlbumsGrid');
        if (!grid) return;
        grid.innerHTML = '';
        if (!orderedAlbums.length) { stateBox(grid, 'No albums published yet.'); return; }
        const frag = document.createDocumentFragment();
        orderedAlbums.forEach(function (a, i) {
            const card = document.createElement('article');
            card.className = 'oc-al-card';
            const thumb = makeAlThumb(a.cover, i < 6);
            const label = albumCountLabel(a);
            if (label) {
                const badge = document.createElement('div');
                badge.className = 'oc-al-badge';
                badge.textContent = label;
                thumb.appendChild(badge);
            }
            const cap = document.createElement('div');
            cap.className = 'oc-al-caption';
            cap.textContent = a.title || a.slug;
            card.appendChild(thumb);
            card.appendChild(cap);
            card.addEventListener('click', function () { location.hash = 'album/' + encodeURIComponent(a.slug); });
            frag.appendChild(card);
        });
        grid.appendChild(frag);
    }

    function shuffleArray(arr) {
        const a = arr.slice();
        for (let i = a.length - 1; i > 0; i--) {
            const j = Math.floor(Math.random() * (i + 1));
            const t = a[i]; a[i] = a[j]; a[j] = t;
        }
        return a;
    }

    function bindShuffle() {
        const shuffleBtn = byId('ocAlShuffle');
        const recBtn = byId('ocAlRecommended');
        if (!shuffleBtn || !recBtn) return;
        shuffleBtn.addEventListener('click', function () {
            if (albumsList.length < 2) return;
            let next = shuffleArray(albumsList);
            let tries = 0;
            while (tries < 5 && next.every(function (a, i) { return a === orderedAlbums[i]; })) { next = shuffleArray(albumsList); tries++; }
            orderedAlbums = next;
            shuffleBtn.classList.add('spin');
            window.setTimeout(function () { shuffleBtn.classList.remove('spin'); }, 520);
            recBtn.classList.remove('active');
            renderAllAlbums();
        });
        recBtn.addEventListener('click', function () {
            orderedAlbums = albumsList.slice();
            recBtn.classList.add('active');
            renderAllAlbums();
        });
    }

    /* ---------------- অ্যালবাম লিস্ট (গ্রিড) ---------------- */
    function renderAlbumGrid() {
        const grid = byId('ocAlbumsGrid');
        if (!grid) return;
        grid.innerHTML = '';
        albumCards.clear();
        if (!albumsList.length) {
            stateBox(grid, 'No albums published yet.');
            updateFeedSep();
            return;
        }
        const frag = document.createDocumentFragment();
        albumsList.forEach(function (a, i) {
            const card = document.createElement('article');
            card.className = 'oc-al-card';
            card.dataset.metadata = String(a.title || '').toLowerCase();
            if (i >= CONFIG.homeAlbumLimit) card.classList.add('oc-al-extra');
            const thumb = makeAlThumb(a.cover, i < 6);
            const label = albumCountLabel(a);
            if (label) {
                const badge = document.createElement('div');
                badge.className = 'oc-al-badge';
                badge.textContent = label;
                thumb.appendChild(badge);
            }
            const cap = document.createElement('div');
            cap.className = 'oc-al-caption';
            cap.textContent = a.title || a.slug;
            card.appendChild(thumb);
            card.appendChild(cap);
            card.addEventListener('click', function () { location.hash = 'album/' + encodeURIComponent(a.slug); });
            albumCards.set(a.slug, card);
            frag.appendChild(card);
        });
        grid.appendChild(frag);
        applySearch();
        updateFeedSep();
        updateSeeAll();
    }

    function updateFeedSep() {
        const sep = byId('ocFeedSep');
        if (!sep) return;
        sep.hidden = !(albumsList.length && mediaCards.size);
    }

    function updateSeeAll() {
        const btn = byId('ocAlSeeAll');
        if (btn) btn.hidden = showAllHome || albumsList.length <= CONFIG.homeAlbumLimit;
    }

    async function fetchAlbums(silent) {
        if (isFetchingAlbums) return;
        isFetchingAlbums = true;
        try {
            const response = await fetch(CONFIG.albumsURL, { headers: { 'Accept': 'application/json' }, cache: 'no-store' });
            const data = await response.json();
            if (!data || !data.ok) throw new Error('bad');
            const list = (data.albums || []).filter(a => a && a.slug);
            const sig = JSON.stringify(list.map(a => [a.slug, a.title, a.cover, a.videos, a.photos]));
            albumsLoadedOnce = true;
            if (sig !== albumsSig) {
                albumsSig = sig;
                albumsList = list;
                orderedAlbums = list.slice();
                const rec = byId('ocAlRecommended');
                if (rec) rec.classList.add('active');
                renderAlbumGrid();
                renderAllAlbums();
            }
        } catch (error) {
            console.error('Albums fetch error:', error);
            if (!silent && !albumsLoadedOnce) {
                const grid = byId('ocAlbumsGrid');
                if (grid) stateBox(grid, 'Could not load albums. Please refresh.', true);
            }
        }
        isFetchingAlbums = false;
    }

    /* ---------------- সিঙ্গেল অ্যালবাম ---------------- */
    function playVideo(i) {
        const v = albumVideos[i];
        const wrap = byId('ocAlPlayer');
        if (!v || !wrap) return;
        wrap.hidden = false;
        wrap.innerHTML = '';
        const box = document.createElement('div');
        box.className = 'oc-al-player';
        const el = document.createElement('video');
        el.controls = true;
        el.playsInline = true;
        el.preload = 'metadata';
        el.setAttribute('controlsList', 'nodownload noplaybackrate');
        el.disablePictureInPicture = true;
        el.oncontextmenu = function (e) { e.preventDefault(); return false; };
        if (v.thumb) el.poster = v.thumb;
        el.src = v.media;
        box.appendChild(el);
        wrap.appendChild(box);
        const cards = byId('ocAlItems').querySelectorAll('.oc-al-item');
        for (let k = 0; k < cards.length; k++) cards[k].classList.toggle('active', k === i);
    }

    function renderItems(items, type) {
        const grid = byId('ocAlItems');
        grid.innerHTML = '';
        if (!items.length) { stateBox(grid, 'This album is empty.'); return; }
        const frag = document.createDocumentFragment();
        items.forEach(function (item, idx) {
            const card = document.createElement('article');
            card.className = 'oc-al-card oc-al-item';
            const thumb = makeAlThumb(item.thumb, false);
            if (type === 'video') {
                const p = document.createElement('div');
                p.className = 'oc-al-play';
                p.innerHTML = PLAY_SVG;
                thumb.appendChild(p);
            }
            card.appendChild(thumb);
            card.addEventListener('click', function () {
                if (type === 'video') {
                    playVideo(idx);
                    byId('ocAlPlayer').scrollIntoView({ behavior: 'smooth', block: 'start' });
                } else {
                    openLightbox(idx);
                }
            });
            frag.appendChild(card);
        });
        grid.appendChild(frag);
    }

    function setTab(t) {
        activeTab = t;
        byId('ocAlTabV').classList.toggle('active', t === 'video');
        byId('ocAlTabP').classList.toggle('active', t === 'photo');
        const wrap = byId('ocAlPlayer');
        if (t === 'video') {
            currentItems = [];
            renderItems(albumVideos, 'video');
            playVideo(0);
        } else {
            pauseAllVideos();
            currentItems = albumPhotos;
            wrap.hidden = true;
            wrap.innerHTML = '';
            renderItems(albumPhotos, 'photo');
        }
    }

    function setupAlbumStats(d) {
        byId('ocAlStats').hidden = false;
        setText('ocAlViews', (d.views !== undefined && d.views !== null) ? fmtN(d.views) : '—');

        const baseLikes = Number(d.likes) || 0;
        const key = 'alvee:liked:' + albumSlug;
        let liked = safeStorageGet(key) === '1';
        const likeBtn = byId('ocAlLike');
        function paintLike() {
            likeBtn.setAttribute('aria-pressed', liked ? 'true' : 'false');
            setText('ocAlLikeN', fmtN(baseLikes + (liked ? 1 : 0)));
        }
        likeBtn.onclick = function () {
            liked = !liked;
            safeStorageSet(key, liked ? '1' : '0');
            paintLike();
        };
        paintLike();

        setText('ocAlShareT', 'Share');
        byId('ocAlShare').onclick = function () {
            const data = { title: d.title || document.title, url: location.href };
            if (navigator.share) { navigator.share(data).catch(function () { }); return; }
            if (navigator.clipboard && navigator.clipboard.writeText) {
                navigator.clipboard.writeText(data.url).then(function () {
                    setText('ocAlShareT', 'Link copied');
                    window.setTimeout(function () { setText('ocAlShareT', 'Share'); }, 1800);
                }).catch(function () { });
            }
        };
    }

    function showAlbum(slug) {
        const scroller = getScroller();
        const container = getContainer();
        const home = byId('ocAlbumsHome');
        const view = byId('ocAlbumView');
        if (!view || !home) return;

        if (!isAlbumOpen() && scroller) savedScrollTop = scroller.scrollTop;
        pauseAllVideos();

        albumSlug = slug;
        const token = ++albumReqToken;
        home.hidden = true;
        if (container) container.style.display = 'none';
        view.hidden = false;
        byId('ocAlTitle').textContent = '';
        byId('ocAlSub').textContent = '';
        byId('ocAlTabs').hidden = true;
        byId('ocAlPlayer').hidden = true;
        byId('ocAlPlayer').innerHTML = '';
        byId('ocAlStats').hidden = true;
        stateBox(byId('ocAlItems'), 'Loading...');
        if (scroller) scroller.scrollTop = 0;

        fetch(CONFIG.albumDetailURL + encodeURIComponent(slug), { cache: 'no-store' })
            .then(function (r) { return r.json(); })
            .then(function (d) {
                if (token !== albumReqToken) return;
                if (!d || !d.ok) throw new Error('bad');
                albumData = d;
                const items = d.items || [];
                albumVideos = items.filter(function (i) { return i.type === 'video'; });
                albumPhotos = items.filter(function (i) { return i.type !== 'video'; });
                byId('ocAlTitle').textContent = d.title || '';
                byId('ocAlSub').textContent = albumCountLabel({ videos: albumVideos.length, photos: albumPhotos.length });
                document.title = (d.title ? d.title + ' | ' : '') + 'AlveeCommunity';
                setupAlbumStats(d);
                if (!items.length) { stateBox(byId('ocAlItems'), 'This album is empty.'); return; }
                if (albumVideos.length && albumPhotos.length) {
                    byId('ocAlTabs').hidden = false;
                    setText('ocAlTabV', 'Videos (' + albumVideos.length + ')');
                    setText('ocAlTabP', 'Photos (' + albumPhotos.length + ')');
                }
                /* ডিফল্ট ট্যাব: Photos (ফটো না থাকলে Videos) */
                setTab(albumPhotos.length ? 'photo' : 'video');
            })
            .catch(function () {
                if (token !== albumReqToken) return;
                stateBox(byId('ocAlItems'), 'Could not load this album.', true);
            });
    }

    function showHome() {
        closeLightbox();
        albumReqToken++;
        const home = byId('ocAlbumsHome');
        const view = byId('ocAlbumView');
        const container = getContainer();
        const wasOpen = isAlbumOpen();
        if (view) view.hidden = true;
        if (home) home.hidden = false;
        if (container) container.style.display = CONFIG.showChannelFeed ? '' : 'none';
        if (wasOpen) {
            pauseAllVideos();
            const player = byId('ocAlPlayer');
            if (player) player.innerHTML = '';
            document.title = originalTitle;
            const scroller = getScroller();
            if (scroller) scroller.scrollTop = savedScrollTop;
        }
        currentItems = []; albumVideos = []; albumPhotos = [];
        if (albumsLoadedOnce) fetchAlbums(true);
    }

    function route() {
        const m = /^#album\/(.+)$/.exec(location.hash);
        if (m) {
            let slug;
            try { slug = decodeURIComponent(m[1]); } catch (e) { slug = m[1]; }
            showAlbum(slug);
        } else {
            showHome();
        }
    }

    /* ---------- হেডারের ব্যাক বাটন ----------
       অ্যালবাম খোলা থাকলে: কোনো লোডিং/রিলোড ছাড়া সঙ্গে সঙ্গে অ্যালবাম লিস্টে (oc-collection.html) ফেরে।
       অ্যালবাম খোলা না থাকলে: আগের মতোই বাটনের লিংকে (/dashboard.html) যায়। */
    function bindHeaderBack() {
        const back = document.querySelector('.oc-back-btn');
        if (!back || back.dataset.ocBound) return;
        back.dataset.ocBound = '1';
        back.addEventListener('click', function (e) {
            if (!isAlbumOpen()) return;
            e.preventDefault();
            history.replaceState(null, '', location.pathname + location.search);
            showHome();
        });
    }

    /* ---------------- লাইটবক্স ---------------- */
    function renderLightbox() {
        const item = currentItems[lbIndex];
        if (!item) return;
        const stage = byId('ocLbStage');
        stage.innerHTML = '';
        const el = new Image();
        el.alt = '';
        el.draggable = false;
        el.src = item.media;
        stage.appendChild(el);
        byId('ocLbCount').textContent = (lbIndex + 1) + ' / ' + currentItems.length;
    }
    function openLightbox(i) {
        lbIndex = i;
        byId('ocLb').classList.add('open');
        renderLightbox();
    }
    function closeLightbox() {
        const lb = byId('ocLb');
        if (!lb || !lb.classList.contains('open')) return;
        lb.classList.remove('open');
        byId('ocLbStage').innerHTML = '';
    }
    function stepLightbox(delta) {
        if (!currentItems.length) return;
        lbIndex = (lbIndex + delta + currentItems.length) % currentItems.length;
        renderLightbox();
    }

    function applySearch() {
        const q = currentSearchQuery;
        mediaCards.forEach(c => { c.hidden = q ? !c.dataset.metadata.includes(q) : false; });
        let visible = 0;
        albumCards.forEach(c => {
            const hide = q ? !c.dataset.metadata.includes(q) : (c.classList.contains('oc-al-extra') && !showAllHome);
            c.hidden = hide;
            if (!hide) visible++;
        });
        const grid = byId('ocAlbumsGrid');
        if (grid) {
            let note = grid.querySelector('.oc-al-nomatch');
            if (q && albumCards.size && !visible) {
                if (!note) {
                    note = document.createElement('div');
                    note.className = 'oc-al-state oc-al-nomatch';
                    note.textContent = 'No albums match your search.';
                    grid.appendChild(note);
                }
            } else if (note) {
                note.remove();
            }
        }
    }

    window.toggleOCSearch = function () {
        const panel = byId('ocSearchPanel');
        if (!panel) return;
        panel.classList.toggle('open');
        if (panel.classList.contains('open')) {
            byId('ocMenuPopover')?.classList.remove('open');
            byId('ocSearchInput')?.focus();
        } else {
            if (byId('ocSearchInput')) byId('ocSearchInput').value = '';
            currentSearchQuery = '';
            applySearch();
        }
    };

    window.toggleOCMenu = function () {
        const menu = byId('ocMenuPopover');
        if (!menu) return;
        menu.classList.toggle('open');
        if (menu.classList.contains('open')) byId('ocSearchPanel')?.classList.remove('open');
    };

    /* ---------------- স্ক্রল করলে হেডার সরে যাওয়া ---------------- */
    function setupHeaderAutoHide() {
        if (!CONFIG.hideHeaderOnScroll) return;
        const channel = getChannel();
        const header = channel ? channel.querySelector('.oc-channel-header') : null;
        const scroller = getScroller();
        if (!header || !scroller) return;

        let hidden = false;
        let lastTop = scroller.scrollTop;
        let lockUntil = 0;
        let ticking = false;

        function setHidden(h) {
            if (h === hidden) return;
            hidden = h;
            lockUntil = Date.now() + 450;
            if (h) {
                header.style.setProperty('--oc-hdr-h', header.offsetHeight + 'px');
                const menu = byId('ocMenuPopover');
                if (menu) menu.classList.remove('open');
            }
            header.classList.toggle('oc-hdr-hidden', h);
        }

        scroller.addEventListener('scroll', function () {
            if (ticking) return;
            ticking = true;
            window.requestAnimationFrame(function () {
                ticking = false;
                const top = scroller.scrollTop;
                if (top <= 10) { setHidden(false); lastTop = top; return; }
                if (Date.now() < lockUntil) { lastTop = top; return; }
                const diff = top - lastTop;
                if (Math.abs(diff) < 6) return;
                lastTop = top;
                const sp = byId('ocSearchPanel');
                const searchOpen = !!(sp && sp.classList.contains('open'));
                if (diff > 0 && !searchOpen) setHidden(true);
                else if (diff < 0 && CONFIG.headerShowOnScrollUp) setHidden(false);
            });
        }, { passive: true });
    }

    function start() {
        originalTitle = document.title;
        moveTopbar();
        injectAlbumStyles();
        buildAlbumUI();
        bindHeaderBack();
        setupHeaderAutoHide();
        fetchAlbums(false);
        albumsTimer = window.setInterval(() => { if (!document.hidden && !isAlbumOpen()) fetchAlbums(true); }, CONFIG.refreshInterval);
        if (CONFIG.showChannelFeed) {
            fetchTelegramMedia();
            pollingTimer = window.setInterval(fetchTelegramMedia, CONFIG.refreshInterval);
            statsTimer = window.setInterval(() => { if (!document.hidden) refreshAllStats(); }, CONFIG.statsPollInterval);
        } else {
            const feed = getContainer();
            if (feed) feed.style.display = 'none';
        }

        if (HAS_IO && CONFIG.showChannelFeed) {
            const obs = new IntersectionObserver(e => { if (e[0].isIntersecting) renderNextBatch(); }, { root: getScroller(), rootMargin: CONFIG.observerRootMargin });
            obs.observe(ensureSentinel());
        }

        byId('ocSearchInput')?.addEventListener('input', e => {
            currentSearchQuery = e.target.value.toLowerCase();
            applySearch();
        });

        window.addEventListener('hashchange', route);
        document.addEventListener('visibilitychange', () => {
            if (!document.hidden && !isAlbumOpen()) fetchAlbums(true);
        });
        route();
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
    else start();

})();
