(function () {
    'use strict';
    if (window.__ocEnhancedClientLoaded) return;
    window.__ocEnhancedClientLoaded = true;

    const CONFIG = {
        containerId: 'channelPostsContainer',
        channelId: 'ocChannelWindow',
        apiURL: '/api/channel/posts',
        mediaURLPrefix: '/api/channel/media/',
        statsURL: '/api/channel/stats',
        refreshInterval: 30000,
        batchSize: 10,
        observerRootMargin: '350px 0px',
        telegramURL: 'https://t.me/ALVEE_BD1',
        brandText: '🫂🖤༏ـ꯭ـ꯭🇦𝙇𝙑𝙀𝙀🫂♥️⟵꯭'
    };

    const mediaCards = new Map();
    const knownMediaIds = new Set();

    let pendingMedia = [];
    let isFetchingMedia = false;
    let isRenderingBatch = false;
    let pollingTimer = null;
    let currentSearchQuery = '';
    let trendingTimer = null;
    let toastTimer = null;

    const byId = id => document.getElementById(id);
    const getChannel = () => byId(CONFIG.channelId);
    const getContainer = () => byId(CONFIG.containerId);

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
        toastTimer = window.setTimeout(() => {
            toast.classList.remove('is-visible');
        }, 2200);
    }

    /* ---------- ৩) টপবার কার্ড স্ক্রল-এরিয়ার ভেতরে নেওয়া ---------- */
    function moveTopbar() {
        const channel = getChannel();
        const body = channel ? channel.querySelector('.oc-channel-body') : null;
        if (!body || body.querySelector('.alv-topbar')) return;
        const existingHeader = channel.querySelector('.oc-channel-header');
        if (!existingHeader) return;

        const topbar = document.createElement('div');
        topbar.className = 'alv-topbar';

        const brand = document.createElement('div');
        brand.className = 'alv-brand';
        brand.textContent = CONFIG.brandText;

        const subscribe = document.createElement('button');
        subscribe.type = 'button';
        subscribe.className = 'alv-header-btn alv-subscribe-btn';
        subscribe.textContent = 'Subscribe';

        const trending = document.createElement('button');
        trending.type = 'button';
        trending.className = 'alv-header-btn alv-trending-btn';
        trending.textContent = 'Trending';

        if (safeStorageGet('alvSubscribeActive') === 'true' || safeStorageGet('oc_subscribed') === '1') {
            subscribe.classList.add('is-active');
        }
        if (safeStorageGet('alvTrendingActive') === 'true') {
            trending.classList.add('is-active');
        }

        subscribe.addEventListener('click', () => {
            setTimeout(() => {
                const isActive = subscribe.classList.contains('is-active');
                safeStorageSet('alvSubscribeActive', isActive ? 'true' : 'false');
                safeStorageSet('oc_subscribed', isActive ? '1' : '0');
            }, 50);
        });

        trending.addEventListener('click', () => {
            trending.classList.add('is-active');
            safeStorageSet('alvTrendingActive', 'true');
            trending.textContent = 'Ok Done';
            if (trendingTimer) window.clearTimeout(trendingTimer);
            trendingTimer = window.setTimeout(() => { trending.textContent = 'Trending'; }, 1500);
        });

        topbar.append(brand, subscribe, trending);
        body.insertBefore(topbar, body.firstChild);
    }

    function getSafeMediaURL(post) {
        const postId = post.message_id ?? post.id;
        if (postId === undefined || postId === null || postId === '') return null;
        return CONFIG.mediaURLPrefix + encodeURIComponent(String(postId));
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

    function getPostDimensions(post) {
        const width = Number(post.width || post.video_width || post.photo_width || post.media_width || 0);
        const height = Number(post.height || post.video_height || post.photo_height || post.media_height || 0);
        return width > 0 && height > 0 ? { width, height } : null;
    }

    function applyNativeAspectRatio(frame, media, width, height) {
        const w = Number(width); const h = Number(height);
        if (!Number.isFinite(w) || !Number.isFinite(h) || w <= 0 || h <= 0) return;
        const ratio = `${w} / ${h}`;
        frame.style.aspectRatio = ratio;
        media.style.aspectRatio = ratio;
    }

    /* ---------- ১) লোডিং ঢেউ + থাম্বনেইল হ্যান্ডলার ---------- */
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
        }

        if (media.tagName === 'IMG') {
            if (media.complete && media.naturalWidth > 0) { finish(); return; }
            frame.classList.add('is-loading');
            media.addEventListener('load', finish);
            media.addEventListener('error', finish);
            setTimeout(finish, 25000);
            return;
        }

        /* ভিডিওর জন্য লোডিং স্টেট */
        if (media.readyState >= 2) { finish(); return; }
        frame.classList.add('is-loading');
        ['loadeddata', 'seeked', 'canplay', 'playing'].forEach(function (ev) {
            media.addEventListener(ev, finish);
        });
        media.addEventListener('error', finish);
        media.addEventListener('loadedmetadata', function () {
            if (!media.poster && media.paused && !media.currentTime) {
                try { media.currentTime = 0.1; } catch (e) { }
            }
        });
        if (media.poster) {
            var im = new Image();
            im.onload = finish;
            im.src = media.poster;
        }
        setTimeout(finish, 30000);
    }

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

        if (item.mediaType === 'video') {
            const video = document.createElement('video');
            video.controls = true; video.playsInline = true; video.preload = 'metadata';
            if (post.thumbnail_url || post.thumbnail) video.poster = post.thumbnail_url || post.thumbnail;
            if (knownDimensions) applyNativeAspectRatio(mediaFrame, video, knownDimensions.width, knownDimensions.height);
            video.addEventListener('loadedmetadata', () => applyNativeAspectRatio(mediaFrame, video, video.videoWidth, video.videoHeight), { once: true });
            video.src = item.mediaURL;
            mediaFrame.appendChild(video);
        } else {
            const image = document.createElement('img');
            image.loading = 'lazy'; image.draggable = false;
            if (knownDimensions) applyNativeAspectRatio(mediaFrame, image, knownDimensions.width, knownDimensions.height);
            image.addEventListener('load', () => applyNativeAspectRatio(mediaFrame, image, image.naturalWidth, image.naturalHeight), { once: true });
            image.src = item.mediaURL;
            mediaFrame.appendChild(image);
        }

        prepareFrame(mediaFrame);

        const statsBar = document.createElement('div');
        statsBar.className = 'alv-stats-bar';

        const views = readCount(post, ['view_count', 'views_count', 'views']);
        const serverLikes = readCount(post, ['like_count', 'likes_count', 'likes']) || 0;

        const viewsItem = createStatItem('alv-stat-item alv-stat-views', '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>', formatCount(views), 'Views');
        statsBar.appendChild(viewsItem);

        const likeKey = 'alvLiked_' + item.id;
        let isLiked = safeStorageGet(likeKey) === 'true';
        const likeControl = createStatButton('alv-stat-like', '<path d="M20.8 8.7c0 5.2-8.8 10.1-8.8 10.1S3.2 13.9 3.2 8.7A4.5 4.5 0 0 1 12 6.5a4.5 4.5 0 0 1 8.8 2.2Z"/>', formatCount(serverLikes + (isLiked ? 1 : 0)));
        if (isLiked) likeControl.button.classList.add('is-active');
        
        likeControl.button.addEventListener('click', () => {
            isLiked = !isLiked;
            safeStorageSet(likeKey, String(isLiked));
            likeControl.button.classList.toggle('is-active', isLiked);
            likeControl.count.textContent = formatCount(serverLikes + (isLiked ? 1 : 0));
            sendAction(item.id, 'like');
        });
        statsBar.appendChild(likeControl.button);

        const commentControl = createStatButton('alv-stat-comment', '<path d="M20 11.5a7.5 7.5 0 0 1-8 7.5 8.7 8.7 0 0 1-3.8-.9L4 20l1.3-3.5A7.1 7.1 0 0 1 4 12c0-4.1 3.6-7.5 8-7.5s8 2.9 8 7Z"/>', formatCount(readCount(post, ['comment_count'])));
        commentControl.button.addEventListener('click', () => showToast('Comments are not connected yet.'));
        statsBar.appendChild(commentControl.button);

        const bookmarkKey = 'alvBookmarked_' + item.id;
        let isBookmarked = safeStorageGet(bookmarkKey) === 'true';
        const bookmarkControl = createStatButton('alv-stat-bookmark', '<path d="M6 4.5A1.5 1.5 0 0 1 7.5 3h9A1.5 1.5 0 0 1 18 4.5V21l-6-4-6 4V4.5Z"/>', isBookmarked ? 'Saved' : 'Save');
        if (isBookmarked) bookmarkControl.button.classList.add('is-active');
        
        bookmarkControl.button.addEventListener('click', () => {
            isBookmarked = !isBookmarked;
            safeStorageSet(bookmarkKey, String(isBookmarked));
            bookmarkControl.button.classList.toggle('is-active', isBookmarked);
            bookmarkControl.count.textContent = isBookmarked ? 'Saved' : 'Save';
            sendAction(item.id, 'save');
        });
        statsBar.appendChild(bookmarkControl.button);

        const telegramLink = document.createElement('a');
        telegramLink.className = 'alv-telegram-btn';
        telegramLink.href = CONFIG.telegramURL;
        telegramLink.target = '_blank';
        telegramLink.innerHTML = `<svg viewBox="0 0 24 24"><path d="m21 3-7.2 18-3.8-7.1L3 10.1 21 3Z"/><path d="m10 13.9 4.1-4.1"/></svg><span>Join Telegram</span>`;

        card.append(postHeader, mediaFrame, statsBar, telegramLink);
        return card;
    }

    /* ---------- ২) স্ক্রলে ভিডিও পজ ও একটাই ভিডিও চলার নিয়ম ---------- */
    document.addEventListener('play', function (e) {
        var t = e.target;
        if (!t || t.tagName !== 'VIDEO') return;
        var all = document.querySelectorAll('video');
        for (var i = 0; i < all.length; i++) {
            if (all[i] !== t && !all[i].paused) { try { all[i].pause(); } catch (err) { } }
        }
    }, true);

    const pauseObserver = 'IntersectionObserver' in window ? new IntersectionObserver(function (entries) {
        entries.forEach(function (en) {
            var v = en.target;
            if (en.intersectionRatio < 0.2 && !v.paused) {
                try { v.pause(); } catch (e) { }
            }
        });
    }, { threshold: [0, 0.2, 0.5] }) : null;

    /* ---------- ৪) সার্ভার স্ট্যাটস সিঙ্ক (View / Like / Save) ---------- */
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
        var posts = document.querySelectorAll('.alv-post[data-oc-id="' + id + '"]');
        posts.forEach(post => {
            var viewsEl = post.querySelector('.alv-stat-views .alv-stat-count');
            var likesEl = post.querySelector('.alv-stat-like .alv-stat-count');
            var savesEl = post.querySelector('.alv-stat-bookmark .alv-stat-count');
            
            if (viewsEl && s.views !== undefined) viewsEl.textContent = formatCount(s.views);
            if (likesEl && s.likes !== undefined) likesEl.textContent = formatCount(s.likes);
            if (savesEl && s.saves !== undefined) savesEl.textContent = s.saves > 0 ? formatCount(s.saves) : 'Save';
            
            if (s.liked !== undefined) post.querySelector('.alv-stat-like')?.classList.toggle('is-active', !!s.liked);
            if (s.saved !== undefined) post.querySelector('.alv-stat-bookmark')?.classList.toggle('is-active', !!s.saved);
        });
    }

    function sendAction(id, action) {
        jpost(`${CONFIG.statsURL}/${id}/${action}`, { client_id: clientId })
            .then(data => { if (data && data.stats) applyStats(id, data.stats); })
            .catch(() => {});
    }

    /* ভিউ কাউন্ট হ্যান্ডলার */
    var viewed = {};
    try { (JSON.parse(safeStorageGet('oc_viewed') || '[]') || []).forEach(i => { viewed[i] = 1; }); } catch (e) {}
    
    function registerView(id) {
        if (!id || viewed[id]) return;
        viewed[id] = 1;
        safeStorageSet('oc_viewed', JSON.stringify(Object.keys(viewed).slice(-500)));
        jpost(`${CONFIG.statsURL}/${id}/view`, { client_id: clientId })
            .then(data => { if (data && data.stats) applyStats(id, data.stats); })
            .catch(() => { delete viewed[id]; });
    }

    const viewObserver = 'IntersectionObserver' in window ? new IntersectionObserver(entries => {
        entries.forEach(en => {
            if (en.isIntersecting && en.intersectionRatio >= 0.35) {
                registerView(en.target.dataset.ocId);
            }
        });
    }, { threshold: [0.35] }) : null;

    function ensureSentinel() {
        const container = getContainer();
        let sentinel = container.querySelector('.oc-feed-sentinel');
        if (!sentinel) {
            sentinel = document.createElement('div');
            sentinel.className = 'oc-feed-sentinel';
            container.appendChild(sentinel);
        }
        return sentinel;
    }

    function appendBatch(items) {
        const container = getContainer();
        const fragment = document.createDocumentFragment();
        items.forEach(item => {
            const card = createMediaCard(item);
            mediaCards.set(item.id, card);
            fragment.appendChild(card);
            
            var video = card.querySelector('video');
            if (video && pauseObserver) pauseObserver.observe(video);
            if (viewObserver) viewObserver.observe(card);
        });
        container.insertBefore(fragment, ensureSentinel());
    }

    function prependBatch(items) {
        const container = getContainer();
        const oldHeight = container.scrollHeight;
        const oldScrollTop = container.scrollTop;
        const fragment = document.createDocumentFragment();
        items.forEach(item => {
            const card = createMediaCard(item);
            mediaCards.set(item.id, card);
            fragment.appendChild(card);

            var video = card.querySelector('video');
            if (video && pauseObserver) pauseObserver.observe(video);
            if (viewObserver) viewObserver.observe(card);
        });
        const firstPost = container.querySelector('.alv-post');
        container.insertBefore(fragment, firstPost || ensureSentinel());
        if (oldScrollTop > 0) container.scrollTop = oldScrollTop + (container.scrollHeight - oldHeight);
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
            mediaCards.forEach(c => c.hidden = false);
        }
    };

    window.toggleOCMenu = function () {
        const menu = byId('ocMenuPopover');
        if (!menu) return;
        menu.classList.toggle('open');
        if (menu.classList.contains('open')) byId('ocSearchPanel')?.classList.remove('open');
    };

    function start() {
        moveTopbar();
        fetchTelegramMedia();
        pollingTimer = window.setInterval(fetchTelegramMedia, CONFIG.refreshInterval);
        
        const obs = new IntersectionObserver(e => { if (e[0].isIntersecting) renderNextBatch(); }, { root: getChannel(), rootMargin: CONFIG.observerRootMargin });
        obs.observe(ensureSentinel());

        byId('ocSearchInput')?.addEventListener('input', e => {
            currentSearchQuery = e.target.value.toLowerCase();
            mediaCards.forEach(c => c.hidden = !c.dataset.metadata.includes(currentSearchQuery));
        });
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
    else start();

})();
