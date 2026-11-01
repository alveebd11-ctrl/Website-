(function () {
    'use strict';
    if (window.__ocEnhancedClientLoaded) return;
    window.__ocEnhancedClientLoaded = true;

    const CONFIG = {
        containerId: 'channelPostsContainer',
        channelId: 'ocChannelWindow',
        apiURL: '/api/channel/posts',
        mediaURLPrefix: '/api/channel/media/',
        thumbURLPrefix: '/api/channel/thumb/',
        statsURL: '/api/channel/stats',
        refreshInterval: 30000,
        statsPollInterval: 60000,
        batchSize: 10,
        observerRootMargin: '350px 0px',
        telegramURL: 'https://t.me/ALVEE_BD1',
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

    const byId = id => document.getElementById(id);
    const getChannel = () => byId(CONFIG.channelId);
    const getContainer = () => byId(CONFIG.containerId);
    /* আসল স্ক্রল হয় .oc-channel-body এ */
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
        toastTimer = window.setTimeout(() => {
            toast.classList.remove('is-visible');
        }, 2200);
    }

    /* ---------- ALVEE / Subscribe / Trending কার্ড ----------
       কার্ডটি স্ক্রল-এরিয়ার (.oc-channel-body) ভেতরে থাকে, তাই নিচে স্ক্রল করলে
       কনটেন্টের সাথে উপরে উঠে হেডারের নিচে হারিয়ে যায়। উপরের হেডার আগের মতোই থাকে। */
    function bindTopbar(topbar) {
        if (topbar.dataset.ocBound) return;
        topbar.dataset.ocBound = '1';

        const subscribe = topbar.querySelector('.alv-subscribe-btn');
        const trending = topbar.querySelector('.alv-trending-btn');

        if (subscribe) {
            if (safeStorageGet('alvSubscribeActive') === 'true' || safeStorageGet('oc_subscribed') === '1') {
                subscribe.classList.add('is-active');
            }
            /* ক্লিক করলে রং বদলায় (লাল ↔ সবুজ) এবং মনে রাখে */
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

        /* HTML-এ আগে থেকে টপবার থাকলে সেটাকেই স্ক্রল-এরিয়ায় আনা (ডুপ্লিকেট হবে না) */
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
        brand.textContent = CONFIG.brandText;

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

    /* ---------- ১) লোডিং: থাম্বনেইল (ব্লার + ঢেউ খাওয়া) + সাগরের ঢেউ ----------
       মিডিয়া লোড হওয়ার সময় ফ্রেমে .is-loading থাকে। থাম্বনেইল থাকলে সেটা ব্লার হয়ে
       একটু একটু ঢেউ খায় এবং তার উপরে পানির ঢেউ চলে। আসল ভিডিও/ছবি তৈরি হলেই
       ব্লার ধীরে ধীরে মিলিয়ে যায়। */
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

    /* সার্ভার ঘুম থেকে উঠছে বা নেটওয়ার্ক ধীর হলে ছবি/ভিডিও নিজে নিজে আবার চেষ্টা করবে */
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

    /* থাম্বনেইল লোড হলে তবেই দেখানো হয়; না এলে কয়েকবার আবার চেষ্টা */
    function loadThumb(frame, thumbBg, url) {
        var tries = 0;
        (function attempt() {
            var im = new Image();
            im.onload = function () {
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
            /* ব্লার থাম্বনেইল ধীরে মিলিয়ে গিয়ে সরে যাবে */
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

        /* ভিডিও */
        if (media.readyState >= 2) { finish(); return; }
        frame.classList.add('is-loading');
        ['loadeddata', 'seeked', 'canplay', 'playing'].forEach(function (ev) {
            media.addEventListener(ev, finish);
        });
        setupRetry(frame, media, finish);
        media.addEventListener('loadedmetadata', function () {
            /* poster না থাকলে ০.১ সেকেন্ডের ফ্রেমকে থাম্বনেইল হিসেবে দেখানো */
            if (!media.poster && media.paused && !media.currentTime) {
                try { media.currentTime = 0.1; } catch (e) { }
            }
            window.setTimeout(finish, 600);   /* কিছু ফোনে ফ্রেম ইভেন্ট আসে না — তখনও ভিডিও দেখাবে */
        });
        if (frameObserver) frameObserver.observe(frame); else activateVideo(media);
    }

    /* ---------- ২) স্ক্রলে ভিডিও পজ + একটাই ভিডিও চলার নিয়ম ---------- */
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

    /* ---------- ৩) View / Like / Save — সব ইউজারের মোট সংখ্যা (সার্ভার থেকে) ---------- */
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
                .catch(() => { /* সার্ভার না পেলে আগের সংখ্যাই থাকবে */ });
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

    /* ভিউ: কার্ড অন্তত ০.৮ সেকেন্ড স্ক্রিনে থাকলে প্রতি ডিভাইসে একবার গোনা হয় */
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

        /* ব্লার থাম্বনেইল স্তর (লোডিং চলাকালীন দেখা যায়) */
        if (thumbURL) {
            const thumbBg = document.createElement('div');
            thumbBg.className = 'alv-thumb-bg';
            const blurWave = document.createElement('div');
            blurWave.className = 'alv-blur-wave';
            mediaFrame.appendChild(thumbBg);
            mediaFrame.appendChild(blurWave);
            loadThumb(mediaFrame, thumbBg, thumbURL);
        }

        if (item.mediaType === 'video') {
            const video = document.createElement('video');
            video.controls = true; video.playsInline = true;
            /* কাছাকাছি এলে (বা ফোনে ট্যাপ করলে) লোড হয় — একসাথে সব ভিডিও লোড হয় না */
            video.preload = HAS_IO ? 'none' : 'metadata';
            if (thumbURL) video.poster = thumbURL;
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

        const viewsItem = createStatItem('alv-stat-views', '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>', formatCount(views), 'Views');
        statsBar.appendChild(viewsItem);

        /* Like — ক্লিকে সাথে সাথে বদলায়, তারপর সার্ভারের আসল সংখ্যা বসে */
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

        /* Save — কতজন সেভ করেছে তার সংখ্যা; ০ হলে "Save" লেখা */
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
    }

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
        /* নিচে স্ক্রল করা থাকলে নতুন কার্ড এলে পেজ যেন লাফিয়ে না যায় */
        if (oldScrollTop > 0) scroller.scrollTop = oldScrollTop + (scroller.scrollHeight - oldHeight);
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
        statsTimer = window.setInterval(() => { if (!document.hidden) refreshAllStats(); }, CONFIG.statsPollInterval);

        if (HAS_IO) {
            const obs = new IntersectionObserver(e => { if (e[0].isIntersecting) renderNextBatch(); }, { root: getScroller(), rootMargin: CONFIG.observerRootMargin });
            obs.observe(ensureSentinel());
        }

        byId('ocSearchInput')?.addEventListener('input', e => {
            currentSearchQuery = e.target.value.toLowerCase();
            mediaCards.forEach(c => c.hidden = !c.dataset.metadata.includes(currentSearchQuery));
        });
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
    else start();

})();
