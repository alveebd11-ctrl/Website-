/* =========================================================
   ALVEE PREMIUM MEDIA VIEWER
   Telegram media feed | Vertical scroll | Existing API
   Keeps the existing OC Channel Header (Back / Title /
   Search / Menu) and replaces only the old media display.
   ========================================================= */
(function () {
    'use strict';

    const CONFIG = {
        containerId: 'channelPostsContainer',
        channelId: 'ocChannelWindow',
        apiURL: '/api/channel/posts',
        mediaURLPrefix: '/api/chat/media/',
        refreshInterval: 30000,
        batchSize: 10,
        observerRootMargin: '350px 0px',
        telegramURL: 'https://t.me/ALVEE_BD1',
        brandText: '𝆺𝅥⃝🖤༏ـ꯭ـ꯭🇦𝙇𝙑𝙀𝙀𝆺𝅥♥️⟵꯭'
    };

    const mediaCards = new Map();
    const knownMediaIds = new Set();

    let pendingMedia = [];
    let isFetchingMedia = false;
    let isRenderingBatch = false;
    let pollingTimer = null;
    let feedObserver = null;
    let previousBodyOverflow = '';
    let currentSearchQuery = '';
    let trendingTimer = null;
    let toastTimer = null;

    const byId = id => document.getElementById(id);
    const getChannel = () => byId(CONFIG.channelId);
    const getContainer = () => byId(CONFIG.containerId);

    function safeStorageGet(key) {
        try { return localStorage.getItem(key); }
        catch (error) { return null; }
    }

    function safeStorageSet(key, value) {
        try { localStorage.setItem(key, value); }
        catch (error) { /* Storage may be unavailable; UI still works. */ }
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

    /* -----------------------------------------------------
       TOP BRAND ROW — inserted below the unchanged OC header
       ----------------------------------------------------- */
    function createTopbar() {
        const channel = getChannel();
        if (!channel) return;
        if (channel.querySelector('.alv-topbar')) return;

        const existingHeader = channel.querySelector('.oc-channel-header');
        if (!existingHeader) return;

        const topbar = document.createElement('div');
        topbar.className = 'alv-topbar';
        topbar.setAttribute('aria-label', 'ALVEE media controls');

        const brand = document.createElement('div');
        brand.className = 'alv-brand';
        brand.textContent = CONFIG.brandText;
        brand.title = CONFIG.brandText;

        const subscribe = document.createElement('button');
        subscribe.type = 'button';
        subscribe.className = 'alv-header-btn alv-subscribe-btn';
        subscribe.textContent = 'Subscribe';
        subscribe.setAttribute('aria-pressed', 'false');

        const trending = document.createElement('button');
        trending.type = 'button';
        trending.className = 'alv-header-btn alv-trending-btn';
        trending.textContent = 'Trending';
        trending.setAttribute('aria-pressed', 'false');

        if (safeStorageGet('alvSubscribeActive') === 'true') {
            subscribe.classList.add('is-active');
            subscribe.setAttribute('aria-pressed', 'true');
        }

        if (safeStorageGet('alvTrendingActive') === 'true') {
            trending.classList.add('is-active');
            trending.setAttribute('aria-pressed', 'true');
        }

        subscribe.addEventListener('click', function () {
            subscribe.classList.add('is-active');
            subscribe.setAttribute('aria-pressed', 'true');
            safeStorageSet('alvSubscribeActive', 'true');
        });

        trending.addEventListener('click', function () {
            trending.classList.add('is-active');
            trending.setAttribute('aria-pressed', 'true');
            safeStorageSet('alvTrendingActive', 'true');
            trending.textContent = 'Ok Done';

            if (trendingTimer) window.clearTimeout(trendingTimer);
            trendingTimer = window.setTimeout(() => {
                trending.textContent = 'Trending';
            }, 1500);
        });

        topbar.append(brand, subscribe, trending);
        existingHeader.insertAdjacentElement('afterend', topbar);
    }

    /* Remove old carousel-only elements. The OC header is untouched. */
    function removeLegacyMediaUI() {
        const channel = getChannel();
        if (!channel) return;

        channel.querySelector('#ocPagination')?.remove();
        channel.querySelector('#ocVideoPopup')?.remove();
        channel.querySelector('.oc-video-popup')?.remove();
    }

    /* -----------------------------------------------------
       MEDIA HELPERS — keep the existing backend endpoints
       ----------------------------------------------------- */
    function getSafeMediaURL(post) {
        const postId = post.message_id ?? post.id;
        if (postId === undefined || postId === null || postId === '') return null;
        return CONFIG.mediaURLPrefix + encodeURIComponent(String(postId));
    }

    function getMediaType(post) {
        const type = String(post.media_type || '').toLowerCase();
        const mime = String(post.mime_type || '').toLowerCase();

        const isVideo =
            type === 'video' ||
            mime.startsWith('video/') ||
            (type === 'animation' && !mime.startsWith('image/'));

        const isImage =
            !isVideo &&
            (type === 'photo' || mime.startsWith('image/') || type === 'animation');

        return isVideo ? 'video' : (isImage ? 'photo' : null);
    }

    function getSearchText(item) {
        const post = item.post || {};
        return [
            post.text,
            post.caption,
            post.title,
            post.file_name,
            item.mediaType,
            item.id
        ].map(value => String(value || '')).join(' ').toLowerCase();
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

    function createStatItem(className, iconMarkup, countText, label) {
        const item = document.createElement('div');
        item.className = 'alv-stat-item ' + className;
        item.setAttribute('aria-label', label + ': ' + countText);
        item.appendChild(makeSvg(iconMarkup));

        const count = document.createElement('span');
        count.className = 'alv-stat-count';
        count.textContent = countText;
        item.appendChild(count);
        return item;
    }

    function createStatButton(className, iconMarkup, countText, label) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'alv-stat-item ' + className;
        button.setAttribute('aria-label', label);
        button.appendChild(makeSvg(iconMarkup));

        const count = document.createElement('span');
        count.className = 'alv-stat-count';
        count.textContent = countText;
        button.appendChild(count);
        return { button, count };
    }

    /* -----------------------------------------------------
       BUILD ONE POST: A Tribute + media + 4 stats + Telegram
       ----------------------------------------------------- */
    function createMediaCard(item) {
        const post = item.post || {};
        const card = document.createElement('article');
        card.className = 'alv-post';
        card.dataset.mediaId = item.id;
        card.dataset.metadata = getSearchText(item);

        const postHeader = document.createElement('div');
        postHeader.className = 'alv-post-head';

        const tributeButton = document.createElement('button');
        tributeButton.type = 'button';
        tributeButton.className = 'alv-tribute-btn';
        tributeButton.textContent = '♡  A Tribute';
        tributeButton.addEventListener('click', () => {
            showToast('This media is dedicated to you.');
        });
        postHeader.appendChild(tributeButton);

        const mediaFrame = document.createElement('div');
        mediaFrame.className = 'alv-media-frame';

        if (item.mediaType === 'video') {
            const video = document.createElement('video');
            video.src = item.mediaURL;
            video.controls = true;
            video.playsInline = true;
            video.preload = 'metadata';
            video.setAttribute('controlslist', 'nodownload');
            video.setAttribute('aria-label', 'Media video player');

            if (post.thumbnail_url || post.thumbnail) {
                video.poster = post.thumbnail_url || post.thumbnail;
            }

            video.addEventListener('error', () => {
                mediaFrame.innerHTML = '';
                const error = document.createElement('div');
                error.className = 'alv-media-error';
                error.textContent = 'This video could not be loaded.';
                mediaFrame.appendChild(error);
            }, { once: true });

            mediaFrame.appendChild(video);
        } else {
            const image = document.createElement('img');
            image.src = item.mediaURL;
            image.alt = 'ALVEE media';
            image.loading = 'lazy';
            image.decoding = 'async';
            image.draggable = false;

            image.addEventListener('error', () => {
                mediaFrame.innerHTML = '';
                const error = document.createElement('div');
                error.className = 'alv-media-error';
                error.textContent = 'This photo could not be loaded.';
                mediaFrame.appendChild(error);
            }, { once: true });

            mediaFrame.appendChild(image);
        }

        const statsBar = document.createElement('div');
        statsBar.className = 'alv-stats-bar';

        const views = readCount(post, ['view_count', 'views_count', 'views']);
        const serverLikes = readCount(post, ['like_count', 'likes_count', 'likes']);
        const comments = readCount(post, ['comment_count', 'comments_count', 'comments']);
        const baseLikes = serverLikes === null ? 0 : serverLikes;

        statsBar.appendChild(createStatItem(
            'alv-stat-views',
            '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>',
            formatCount(views),
            'Views'
        ));

        const likeKey = 'alvLiked_' + item.id;
        let isLiked = safeStorageGet(likeKey) === 'true';
        const likeControl = createStatButton(
            'alv-stat-like',
            '<path d="M20.8 8.7c0 5.2-8.8 10.1-8.8 10.1S3.2 13.9 3.2 8.7A4.5 4.5 0 0 1 12 6.5a4.5 4.5 0 0 1 8.8 2.2Z"/>',
            formatCount(baseLikes + (isLiked ? 1 : 0)),
            'Like this media'
        );
        likeControl.button.setAttribute('aria-pressed', String(isLiked));
        if (isLiked) likeControl.button.classList.add('is-active');
        likeControl.button.addEventListener('click', () => {
            isLiked = !isLiked;
            safeStorageSet(likeKey, String(isLiked));
            likeControl.button.classList.toggle('is-active', isLiked);
            likeControl.button.setAttribute('aria-pressed', String(isLiked));
            likeControl.count.textContent = formatCount(baseLikes + (isLiked ? 1 : 0));
        });
        statsBar.appendChild(likeControl.button);

        const commentControl = createStatButton(
            'alv-stat-comment',
            '<path d="M20 11.5a7.5 7.5 0 0 1-8 7.5 8.7 8.7 0 0 1-3.8-.9L4 20l1.3-3.5A7.1 7.1 0 0 1 4 12c0-4.1 3.6-7.5 8-7.5s8 2.9 8 7Z"/>',
            formatCount(comments),
            'Comments'
        );
        commentControl.button.addEventListener('click', () => {
            showToast('Comments are not connected yet.');
        });
        statsBar.appendChild(commentControl.button);

        const bookmarkKey = 'alvBookmarked_' + item.id;
        let isBookmarked = safeStorageGet(bookmarkKey) === 'true';
        const bookmarkControl = createStatButton(
            'alv-stat-bookmark',
            '<path d="M6 4.5A1.5 1.5 0 0 1 7.5 3h9A1.5 1.5 0 0 1 18 4.5V21l-6-4-6 4V4.5Z"/>',
            isBookmarked ? 'Saved' : 'Save',
            'Bookmark this media'
        );
        bookmarkControl.button.setAttribute('aria-pressed', String(isBookmarked));
        if (isBookmarked) bookmarkControl.button.classList.add('is-active');
        bookmarkControl.button.addEventListener('click', () => {
            isBookmarked = !isBookmarked;
            safeStorageSet(bookmarkKey, String(isBookmarked));
            bookmarkControl.button.classList.toggle('is-active', isBookmarked);
            bookmarkControl.button.setAttribute('aria-pressed', String(isBookmarked));
            bookmarkControl.count.textContent = isBookmarked ? 'Saved' : 'Save';
        });
        statsBar.appendChild(bookmarkControl.button);

        const telegramLink = document.createElement('a');
        telegramLink.className = 'alv-telegram-btn';
        telegramLink.href = CONFIG.telegramURL;
        telegramLink.target = '_blank';
        telegramLink.rel = 'noopener noreferrer';
        telegramLink.setAttribute('aria-label', 'Join ALVEE Telegram channel');
        telegramLink.appendChild(makeSvg(
            '<path d="m21 3-7.2 18-3.8-7.1L3 10.1 21 3Z"/><path d="m10 13.9 4.1-4.1"/>',
            '0 0 24 24'
        ));
        const telegramText = document.createElement('span');
        telegramText.textContent = 'Join Telegram';
        telegramLink.appendChild(telegramText);

        card.append(postHeader, mediaFrame, statsBar, telegramLink);
        return card;
    }

    function setCardVisibility(card, query) {
        const visible = !query || String(card.dataset.metadata || '').includes(query);
        card.hidden = !visible;
    }

    function removeStatus() {
        getContainer()?.querySelector('.oc-channel-status')?.remove();
    }

    function showStatus(message, state) {
        const container = getContainer();
        if (!container) return;

        let status = container.querySelector('.oc-channel-status');
        if (!status) {
            status = document.createElement('div');
            status.className = 'oc-channel-status';
            status.setAttribute('role', 'status');
            container.appendChild(status);
        }

        status.dataset.state = state || 'info';
        status.textContent = message;
    }

    function ensureSentinel() {
        const container = getContainer();
        if (!container) return null;

        let sentinel = container.querySelector('.oc-feed-sentinel');
        if (!sentinel) {
            sentinel = document.createElement('div');
            sentinel.className = 'oc-feed-sentinel';
            sentinel.setAttribute('role', 'status');
            sentinel.setAttribute('aria-live', 'polite');
        }

        if (sentinel.parentNode !== container || container.lastElementChild !== sentinel) {
            container.appendChild(sentinel);
        }
        return sentinel;
    }

    function appendBatch(items) {
        const container = getContainer();
        if (!container || !items.length) return;

        const fragment = document.createDocumentFragment();
        items.forEach(item => {
            const card = createMediaCard(item);
            mediaCards.set(item.id, card);
            fragment.appendChild(card);
        });
        container.insertBefore(fragment, ensureSentinel());
    }

    function prependBatch(items) {
        const container = getContainer();
        if (!container || !items.length) return;

        const oldHeight = container.scrollHeight;
        const oldScrollTop = container.scrollTop;
        const fragment = document.createDocumentFragment();

        items.forEach(item => {
            const card = createMediaCard(item);
            mediaCards.set(item.id, card);
            fragment.appendChild(card);
        });

        const firstPost = container.querySelector('.alv-post');
        if (firstPost) container.insertBefore(fragment, firstPost);
        else container.insertBefore(fragment, ensureSentinel());

        if (oldScrollTop > 0) {
            container.scrollTop = oldScrollTop + (container.scrollHeight - oldHeight);
        }
    }

    function setupFeedObserver() {
        const container = getContainer();
        const sentinel = ensureSentinel();
        if (!container || !sentinel) return;

        if ('IntersectionObserver' in window) {
            if (!feedObserver) {
                feedObserver = new IntersectionObserver(entries => {
                    if (entries.some(entry => entry.isIntersecting)) renderNextBatch();
                }, {
                    root: container,
                    rootMargin: CONFIG.observerRootMargin,
                    threshold: 0
                });
            }
            feedObserver.observe(sentinel);
        } else if (container.dataset.alvScrollInitialized !== 'true') {
            container.dataset.alvScrollInitialized = 'true';
            container.addEventListener('scroll', () => {
                const remaining = container.scrollHeight - container.scrollTop - container.clientHeight;
                if (remaining < 450) renderNextBatch();
            }, { passive: true });
        }
    }

    function updateLoadMoreUI() {
        const sentinel = ensureSentinel();
        if (!sentinel) return;

        const hasMore = pendingMedia.some(item => matchesSearch(item, currentSearchQuery));
        sentinel.hidden = !hasMore;
        sentinel.textContent = hasMore ? 'Scroll down to load more media…' : '';
        setupFeedObserver();
    }

    function renderNextBatch() {
        if (isRenderingBatch || !pendingMedia.length) {
            updateLoadMoreUI();
            return 0;
        }
        if (!getContainer()) return 0;

        isRenderingBatch = true;
        try {
            const batch = [];
            const remaining = [];

            pendingMedia.forEach(item => {
                if (batch.length < CONFIG.batchSize && matchesSearch(item, currentSearchQuery)) {
                    batch.push(item);
                } else {
                    remaining.push(item);
                }
            });

            pendingMedia = remaining;
            if (batch.length) {
                removeStatus();
                appendBatch(batch);
            }
            updateLoadMoreUI();
            return batch.length;
        } finally {
            isRenderingBatch = false;
        }
    }

    function filterOCPosts(query) {
        currentSearchQuery = String(query || '').trim().toLowerCase();
        mediaCards.forEach(card => setCardVisibility(card, currentSearchQuery));
        updateLoadMoreUI();
    }

    /* -----------------------------------------------------
       TELEGRAM API — same routes as the supplied old script
       ----------------------------------------------------- */
    function renderTelegramPosts(posts) {
        if (!Array.isArray(posts)) return 0;

        const incoming = [];
        posts.forEach(post => {
            if (!post) return;
            const rawId = post.message_id ?? post.id;
            if (rawId === undefined || rawId === null || rawId === '') return;

            const id = String(rawId);
            if (knownMediaIds.has(id)) return;

            const mediaURL = getSafeMediaURL(post);
            const mediaType = getMediaType(post);
            if (!mediaURL || !mediaType) return;

            knownMediaIds.add(id);
            incoming.push({ id, post, mediaURL, mediaType });
        });

        if (!incoming.length) {
            updateLoadMoreUI();
            return 0;
        }

        removeStatus();
        const firstBatch = incoming.slice(0, CONFIG.batchSize);
        const overflow = incoming.slice(CONFIG.batchSize);

        if (overflow.length) pendingMedia = overflow.concat(pendingMedia);
        prependBatch(firstBatch);
        filterOCPosts(currentSearchQuery);
        updateLoadMoreUI();
        return incoming.length;
    }

    async function fetchTelegramMedia() {
        const container = getContainer();
        if (!container || isFetchingMedia) return;

        isFetchingMedia = true;
        container.setAttribute('aria-busy', 'true');

        if (!mediaCards.size && !pendingMedia.length) {
            showStatus('Loading photos and videos…', 'loading');
        }

        try {
            const response = await fetch(CONFIG.apiURL, {
                method: 'GET',
                headers: { 'Accept': 'application/json' },
                credentials: 'same-origin',
                cache: 'no-store'
            });

            if (!response.ok) {
                throw new Error('Telegram API HTTP error: ' + response.status);
            }

            const data = await response.json();
            if (data.ok !== true || !Array.isArray(data.posts)) {
                throw new Error(data.error || 'Invalid Telegram API response.');
            }

            renderTelegramPosts(data.posts);

            if (!mediaCards.size && !pendingMedia.length) {
                showStatus('No photos or videos have been published yet.', 'empty');
            }
        } catch (error) {
            console.error('ALVEE media loading error:', error);
            if (!mediaCards.size && !pendingMedia.length) {
                showStatus('Unable to load media. Please try again later.', 'error');
            }
        } finally {
            isFetchingMedia = false;
            container.removeAttribute('aria-busy');
        }
    }

    function startMediaPolling() {
        stopMediaPolling();
        pollingTimer = window.setInterval(() => {
            if (getChannel()?.classList.contains('open')) fetchTelegramMedia();
        }, CONFIG.refreshInterval);
    }

    function stopMediaPolling() {
        if (pollingTimer !== null) {
            window.clearInterval(pollingTimer);
            pollingTimer = null;
        }
    }

    /* -----------------------------------------------------
       KEEP EXISTING OC HEADER ACTIONS WORKING
       ----------------------------------------------------- */
    function toggleOCMenu() {
        const menu = byId('ocMenuPopover');
        if (!menu) return;
        const opening = !menu.classList.contains('open');
        menu.classList.toggle('open', opening);
        if (opening) byId('ocSearchPanel')?.classList.remove('open');
    }

    function toggleOCSearch() {
        const panel = byId('ocSearchPanel');
        if (!panel) return;
        const opening = !panel.classList.contains('open');
        panel.classList.toggle('open', opening);
        if (opening) {
            byId('ocMenuPopover')?.classList.remove('open');
            byId('ocSearchInput')?.focus();
        } else {
            const input = byId('ocSearchInput');
            if (input) input.value = '';
            filterOCPosts('');
        }
    }

    function openOCChannel() {
        const channel = getChannel();
        if (!channel) {
            console.error('OC Collection: Channel window not found.');
            return;
        }

        if (!channel.classList.contains('open')) {
            previousBodyOverflow = document.body.style.overflow;
        }

        createTopbar();
        removeLegacyMediaUI();

        byId('naughtyWindow')?.classList.remove('open');
        const profileModal = byId('profileModal');
        if (profileModal) profileModal.style.display = 'none';

        byId('ocMenuPopover')?.classList.remove('open');
        byId('ocSearchPanel')?.classList.remove('open');
        const input = byId('ocSearchInput');
        if (input) input.value = '';

        filterOCPosts('');
        channel.classList.add('open');
        channel.setAttribute('aria-hidden', 'false');
        document.body.style.overflow = 'hidden';

        fetchTelegramMedia();
        startMediaPolling();
    }

    function closeOCChannel() {
        const channel = getChannel();
        if (!channel) return;

        channel.classList.remove('open');
        channel.setAttribute('aria-hidden', 'true');
        byId('ocMenuPopover')?.classList.remove('open');
        byId('ocSearchPanel')?.classList.remove('open');

        const input = byId('ocSearchInput');
        if (input) input.value = '';

        channel.querySelectorAll('#channelPostsContainer video').forEach(video => {
            try { video.pause(); } catch (error) {}
        });

        stopMediaPolling();
        document.body.style.overflow = previousBodyOverflow;
    }

    function initializeEvents() {
        const input = byId('ocSearchInput');
        if (input && input.dataset.alvInitialized !== 'true') {
            input.dataset.alvInitialized = 'true';
            input.addEventListener('input', function () {
                filterOCPosts(this.value);
            });
        }

        const channel = getChannel();
        if (!channel || channel.dataset.alvEventsInitialized === 'true') return;
        channel.dataset.alvEventsInitialized = 'true';

        document.addEventListener('click', event => {
            if (!channel.classList.contains('open')) return;
            const menu = byId('ocMenuPopover');
            const menuButton = channel.querySelector('.oc-header-action-btn[aria-label="Menu"]');
            if (menu?.classList.contains('open') && !menu.contains(event.target) && !menuButton?.contains(event.target)) {
                menu.classList.remove('open');
            }
        });

        /* Capture Escape before the dashboard's older global handler. */
        document.addEventListener('keydown', event => {
            if (event.key !== 'Escape' || !channel.classList.contains('open')) return;
            const search = byId('ocSearchPanel');
            const menu = byId('ocMenuPopover');

            event.preventDefault();
            event.stopImmediatePropagation();

            if (search?.classList.contains('open')) {
                toggleOCSearch();
            } else if (menu?.classList.contains('open')) {
                menu.classList.remove('open');
            } else {
                closeOCChannel();
            }
        }, true);
    }

    /* Public functions used by the existing dashboard onclicks. */
    window.openOCChannel = openOCChannel;
    window.closeOCChannel = closeOCChannel;
    window.toggleOCMenu = toggleOCMenu;
    window.toggleOCSearch = toggleOCSearch;
    window.fetchTelegramMedia = fetchTelegramMedia;
    window.loadMoreOCMedia = renderNextBatch;

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initializeEvents);
    } else {
        initializeEvents();
    }
})();
