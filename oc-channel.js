
/* OC COLLECTION UNIVERSE — Telegram Media Feed
   Original aspect ratios | 10-item batches | Infinite scroll */
(function () {
    'use strict';

    const CONFIG = {
        containerId: 'channelPostsContainer',
        channelId: 'ocChannelWindow',
        popupId: 'ocVideoPopup',
        popupWrapper: '.oc-popup-video-wrapper',
        apiURL: '/api/get_videos',
        refreshInterval: 30000,
        batchSize: 10,
        observerRootMargin: '400px 0px'
    };

    const mediaCards = new Map();
    const knownMediaIds = new Set();
    let pendingMedia = [];
    let isFetchingMedia = false;
    let isRenderingBatch = false;
    let pollingTimer = null;
    let feedObserver = null;
    let previousBodyOverflow = '';
    let lastFocusedCard = null;
    let currentSearchQuery = '';

    const byId = id => document.getElementById(id);
    const getContainer = () => byId(CONFIG.containerId);

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

    /* SAFE MEDIA URL */

    function getSafeMediaURL(post) {
        if (!post || typeof post.media_url !== 'string') return null;

        try {
            const url = new URL(post.media_url, window.location.origin);

            if (url.origin !== window.location.origin) return null;

            if (!/^\/api\/channel\/media\/\d+\/?$/.test(url.pathname)) {
                return null;
            }

            return url.href;
        } catch (_) {
            return null;
        }
    }

    /* MEDIA TYPE */

    function getMediaType(post) {
        const type = String(post.media_type || '').toLowerCase();
        const mime = String(post.mime_type || '').toLowerCase();

        const video =
            type === 'video' ||
            mime.startsWith('video/') ||
            (type === 'animation' && !mime.startsWith('image/'));

        const image =
            !video &&
            (type === 'photo' || mime.startsWith('image/') || type === 'animation');

        return video ? 'video' : image ? 'photo' : null;
    }

    /* SEARCH */

    function getSearchText(item) {
        const post = item.post || {};

        return [
            post.text,
            post.caption,
            post.title,
            item.mediaType,
            item.id
        ].map(value => String(value || '')).join(' ').toLowerCase();
    }

    function matchesSearch(item, query) {
        return !query || getSearchText(item).includes(query);
    }

    function setCardVisibility(card, query) {
        const visible =
            !query ||
            String(card.dataset.metadata || '').includes(query);

        card.hidden = !visible;

        if (visible) {
            card.style.removeProperty('display');
        } else {
            card.style.setProperty('display', 'none', 'important');
        }
    }

    /* CREATE MEDIA CARD */

    function createMediaCard(item) {
        const card = document.createElement('button');

        card.type = 'button';
        card.className = 'media-card';
        card.dataset.mediaId = item.id;
        card.dataset.metadata = getSearchText(item);

        card.setAttribute(
            'aria-label',
            item.mediaType === 'video' ? 'Play video' : 'View photo'
        );

        if (item.mediaType === 'video') {
            const video = document.createElement('video');

            video.src = item.mediaURL;
            video.muted = true;
            video.loop = true;
            video.playsInline = true;
            video.preload = 'metadata';
            video.tabIndex = -1;

            video.setAttribute('aria-hidden', 'true');

            video.addEventListener('loadedmetadata', function () {
                if (video.videoWidth && video.videoHeight) {
                    video.setAttribute('width', video.videoWidth);
                    video.setAttribute('height', video.videoHeight);

                    card.dataset.aspectRatio =
                        video.videoWidth + ':' + video.videoHeight;
                }
            }, { once: true });

            video.addEventListener(
                'error',
                () => card.classList.add('media-load-error'),
                { once: true }
            );

            card.appendChild(video);

            const playIcon = document.createElement('span');

            playIcon.className = 'video-overlay-icon';
            playIcon.setAttribute('aria-hidden', 'true');

            card.appendChild(playIcon);

        } else {
            const img = document.createElement('img');

            img.src = item.mediaURL;
            img.alt = 'OC Collection photo';
            img.loading = 'lazy';
            img.decoding = 'async';

            img.addEventListener('load', function () {
                if (img.naturalWidth && img.naturalHeight) {
                    img.setAttribute('width', img.naturalWidth);
                    img.setAttribute('height', img.naturalHeight);

                    card.dataset.aspectRatio =
                        img.naturalWidth + ':' + img.naturalHeight;
                }
            }, { once: true });

            img.addEventListener(
                'error',
                () => card.classList.add('media-load-error'),
                { once: true }
            );

            card.appendChild(img);
        }

        card.addEventListener('click', function () {
            lastFocusedCard = card;
            openPopupPlayer(item.mediaURL, item.mediaType);
        });

        setCardVisibility(card, currentSearchQuery);

        return card;
    }

    /* INFINITE SCROLL SENTINEL */

    function ensureSentinel() {
        const container = getContainer();
        if (!container) return null;

        let sentinel = container.querySelector('.oc-feed-sentinel');

        if (!sentinel) {
            sentinel = document.createElement('div');

            sentinel.className = 'oc-feed-sentinel';

            sentinel.setAttribute('role', 'status');
            sentinel.setAttribute('aria-live', 'polite');

            sentinel.style.cssText =
                'width:100%;padding:16px 10px;text-align:center;' +
                'font-size:12px;color:#b6a2d4;flex:0 0 auto;';
        }

        if (
            sentinel.parentNode !== container ||
            container.lastElementChild !== sentinel
        ) {
            container.appendChild(sentinel);
        }

        return sentinel;
    }

    function setupFeedObserver() {
        const container = getContainer();
        const sentinel = ensureSentinel();

        if (!container || !sentinel) return;

        if ('IntersectionObserver' in window) {
            if (!feedObserver) {
                feedObserver = new IntersectionObserver(entries => {
                    if (entries.some(entry => entry.isIntersecting)) {
                        renderNextBatch();
                    }
                }, {
                    root: container,
                    rootMargin: CONFIG.observerRootMargin,
                    threshold: 0
                });
            }

            feedObserver.observe(sentinel);

        } else if (
            container.dataset.ocScrollInitialized !== 'true'
        ) {
            container.dataset.ocScrollInitialized = 'true';

            container.addEventListener(
                'scroll',
                handleFeedScroll,
                { passive: true }
            );
        }
    }

    function handleFeedScroll() {
        const container = getContainer();

        if (!container || !pendingMedia.length) return;

        const distance =
            container.scrollHeight -
            container.scrollTop -
            container.clientHeight;

        if (distance < 500) {
            renderNextBatch();
        }
    }

    function updateLoadMoreUI() {
        const sentinel = ensureSentinel();

        if (!sentinel) return;

        const hasMoreMatches = pendingMedia.some(item =>
            matchesSearch(item, currentSearchQuery)
        );

        sentinel.hidden = !hasMoreMatches;

        sentinel.style.display =
            hasMoreMatches ? 'block' : 'none';

        sentinel.textContent = hasMoreMatches
            ? 'Scroll down to load more media...'
            : '';

        setupFeedObserver();
    }

    /* INSERT MEDIA CARDS */

    function prependBatch(items) {
        const container = getContainer();

        if (!container || !items.length) return;

        const wasScrolled = container.scrollTop > 0;
        const oldHeight = container.scrollHeight;

        const fragment = document.createDocumentFragment();

        items.forEach(item => {
            const card = createMediaCard(item);

            mediaCards.set(item.id, card);

            fragment.appendChild(card);
        });

        container.prepend(fragment);

        // Preserve the reader's scroll position when new posts arrive.
        if (wasScrolled) {
            container.scrollTop +=
                container.scrollHeight - oldHeight;
        }
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

    /* LOAD NEXT 10 ITEMS */

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
                if (
                    batch.length < CONFIG.batchSize &&
                    matchesSearch(item, currentSearchQuery)
                ) {
                    batch.push(item);
                } else {
                    remaining.push(item);
                }
            });

            pendingMedia = remaining;

            if (batch.length) {
                removeStatus();

                appendBatch(batch);

                filterOCPosts(currentSearchQuery);
            }

            updateLoadMoreUI();

            return batch.length;

        } finally {
            isRenderingBatch = false;
        }
    }

    /* PROCESS TELEGRAM POSTS */

    function renderTelegramPosts(posts) {
        if (!Array.isArray(posts)) return 0;

        const incoming = [];

        posts.forEach(post => {
            if (!post) return;

            const mediaURL = getSafeMediaURL(post);
            const mediaType = getMediaType(post);

            if (!mediaURL || !mediaType) return;

            const id = String(
                post.message_id ?? post.id ?? mediaURL
            );

            if (knownMediaIds.has(id)) return;

            knownMediaIds.add(id);

            incoming.push({
                id,
                post,
                mediaURL,
                mediaType
            });
        });

        if (!incoming.length) {
            updateLoadMoreUI();
            return 0;
        }

        removeStatus();

        const firstBatch =
            incoming.slice(0, CONFIG.batchSize);

        const overflow =
            incoming.slice(CONFIG.batchSize);

        if (overflow.length) {
            pendingMedia = overflow.concat(pendingMedia);
        }

        // API posts must be ordered newest first.
        prependBatch(firstBatch);

        filterOCPosts(currentSearchQuery);
        updateLoadMoreUI();

        return incoming.length;
    }

    /* FETCH TELEGRAM MEDIA */

    async function fetchTelegramMedia() {
        const container = getContainer();

        if (!container || isFetchingMedia) return;

        isFetchingMedia = true;

        container.setAttribute('aria-busy', 'true');

        if (!mediaCards.size && !pendingMedia.length) {
            showStatus(
                'Loading photos and videos...',
                'loading'
            );
        }

        try {
            const response = await fetch(CONFIG.apiURL, {
                method: 'GET',
                headers: {
                    'Accept': 'application/json'
                },
                credentials: 'same-origin',
                cache: 'no-store'
            });

            if (!response.ok) {
                throw new Error(
                    'Telegram API HTTP error: ' + response.status
                );
            }

            const data = await response.json();

            if (
                data.status !== 'success' ||
                !Array.isArray(data.posts)
            ) {
                throw new Error(
                    data.error || 'Invalid Telegram API response.'
                );
            }

            renderTelegramPosts(data.posts);

            if (!mediaCards.size && !pendingMedia.length) {
                showStatus(
                    'No photos or videos have been published yet.',
                    'empty'
                );
            }

        } catch (error) {
            console.error(
                'OC Collection media loading error:',
                error
            );

            if (!mediaCards.size && !pendingMedia.length) {
                showStatus(
                    'Unable to load media. Please try again later.',
                    'error'
                );
            }

        } finally {
            isFetchingMedia = false;
            container.removeAttribute('aria-busy');
        }
    }

    window.fetchTelegramMedia = fetchTelegramMedia;
    window.loadMoreOCMedia = renderNextBatch;

    /* AUTOMATIC REFRESH */

    function startMediaPolling() {
        stopMediaPolling();

        pollingTimer = window.setInterval(() => {
            const channel = byId(CONFIG.channelId);

            if (channel?.classList.contains('open')) {
                fetchTelegramMedia();
            }
        }, CONFIG.refreshInterval);
    }

    function stopMediaPolling() {
        if (pollingTimer !== null) {
            window.clearInterval(pollingTimer);
            pollingTimer = null;
        }
    }

    /* SEARCH FILTER */

    function filterOCPosts(query) {
        currentSearchQuery =
            String(query || '').trim().toLowerCase();

        mediaCards.forEach(card => {
            setCardVisibility(card, currentSearchQuery);
        });

        updateLoadMoreUI();
    }

    /* OPEN CHANNEL */

    window.openOCChannel = function () {
        initializeEvents();

        const channel = byId(CONFIG.channelId);

        if (!channel) {
            console.error(
                'OC Collection: Channel window not found.'
            );
            return;
        }

        if (!channel.classList.contains('open')) {
            previousBodyOverflow =
                document.body.style.overflow;
        }

        byId('naughtyWindow')?.classList.remove('open');

        const profileModal = byId('profileModal');

        if (profileModal) {
            profileModal.style.display = 'none';
        }

        byId('ocMenuPopover')?.classList.remove('open');
        byId('ocSearchPanel')?.classList.remove('open');

        const input = byId('ocSearchInput');

        if (input) input.value = '';

        filterOCPosts('');

        channel.classList.add('open');

        channel.setAttribute('aria-hidden', 'false');

        document.body.style.overflow = 'hidden';

        closePopupPlayer();
        fetchTelegramMedia();
        startMediaPolling();
    };

    /* CLOSE CHANNEL */

    window.closeOCChannel = function () {
        const channel = byId(CONFIG.channelId);

        if (!channel) return;

        channel.classList.remove('open');

        channel.setAttribute('aria-hidden', 'true');

        byId('ocMenuPopover')?.classList.remove('open');
        byId('ocSearchPanel')?.classList.remove('open');

        const input = byId('ocSearchInput');

        if (input) input.value = '';

        filterOCPosts('');

        closePopupPlayer();
        stopMediaPolling();

        document.body.style.overflow =
            previousBodyOverflow;
    };

    /* MENU */

    window.toggleOCMenu = function () {
        const menu = byId('ocMenuPopover');

        if (!menu) return;

        const opening = !menu.classList.contains('open');

        menu.classList.toggle('open', opening);

        if (opening) {
            byId('ocSearchPanel')?.classList.remove('open');
        }
    };

    /* SEARCH PANEL */

    window.toggleOCSearch = function () {
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
    };

    /* FULLSCREEN PLAYER */

    function openPopupPlayer(src, type) {
        const popup = byId(CONFIG.popupId);

        if (!popup) {
            console.error(
                'OC Collection: Popup element not found.'
            );
            return;
        }

        const wrapper =
            popup.querySelector(CONFIG.popupWrapper);

        if (!wrapper) {
            console.error(
                'OC Collection: Popup media wrapper not found.'
            );
            return;
        }

        wrapper.replaceChildren();

        if (type === 'video') {
            const video = document.createElement('video');

            video.id = 'ocPopupVideoPlayer';
            video.src = src;
            video.controls = true;
            video.autoplay = true;
            video.playsInline = true;
            video.preload = 'metadata';
            video.className = 'oc-popup-media-video';

            wrapper.appendChild(video);

            popup.classList.add('open');

            popup.setAttribute('aria-hidden', 'false');

            const playPromise = video.play();

            if (playPromise?.catch) {
                playPromise.catch(() => {});
            }

        } else if (type === 'photo') {
            const img = document.createElement('img');

            img.src = src;
            img.alt = 'OC Collection photo';
            img.className = 'oc-popup-media-image';

            wrapper.appendChild(img);

            popup.classList.add('open');

            popup.setAttribute('aria-hidden', 'false');
        }
    }

    /* CLOSE PLAYER */

    function closePopupPlayer() {
        const popup = byId(CONFIG.popupId);

        if (!popup) return;

        const wrapper =
            popup.querySelector(CONFIG.popupWrapper);

        if (wrapper) {
            const video = wrapper.querySelector('video');

            if (video) {
                video.pause();
                video.removeAttribute('src');
                video.load();
            }

            wrapper.replaceChildren();
        }

        popup.classList.remove('open');

        popup.setAttribute('aria-hidden', 'true');

        if (
            lastFocusedCard &&
            document.contains(lastFocusedCard)
        ) {
            lastFocusedCard.focus();
        }

        lastFocusedCard = null;
    }

    /* INITIALIZE EVENTS */

    function initializeEvents() {
        const input = byId('ocSearchInput');

        if (
            input &&
            input.dataset.ocInitialized !== 'true'
        ) {
            input.dataset.ocInitialized = 'true';

            input.addEventListener('input', function () {
                filterOCPosts(this.value);
            });
        }

        const closeButton = byId('ocPopupCloseBtn');

        if (
            closeButton &&
            closeButton.dataset.ocInitialized !== 'true'
        ) {
            closeButton.dataset.ocInitialized = 'true';

            closeButton.addEventListener(
                'click',
                closePopupPlayer
            );
        }

        const popup = byId(CONFIG.popupId);

        if (
            popup &&
            popup.dataset.ocInitialized !== 'true'
        ) {
            popup.dataset.ocInitialized = 'true';

            popup.addEventListener('click', event => {
                if (event.target === popup) {
                    closePopupPlayer();
                }
            });
        }

        const channel = byId(CONFIG.channelId);

        if (
            !channel ||
            channel.dataset.ocEventsInitialized === 'true'
        ) {
            return;
        }

        channel.dataset.ocEventsInitialized = 'true';

        document.addEventListener('click', event => {
            if (!channel.classList.contains('open')) return;

            const menu = byId('ocMenuPopover');

            const menuButton = channel.querySelector(
                '.oc-menu-btn, .oc-header-action-btn[aria-label="Menu"]'
            );

            if (
                menu?.classList.contains('open') &&
                !menu.contains(event.target) &&
                !menuButton?.contains(event.target)
            ) {
                menu.classList.remove('open');
            }
        });

        document.addEventListener('keydown', event => {
            if (event.key !== 'Escape') return;

            const popupElement = byId(CONFIG.popupId);

            if (popupElement?.classList.contains('open')) {
                closePopupPlayer();
                return;
            }

            if (!channel.classList.contains('open')) return;

            const search = byId('ocSearchPanel');
            const menu = byId('ocMenuPopover');

            if (search?.classList.contains('open')) {
                window.toggleOCSearch();

            } else if (menu?.classList.contains('open')) {
                menu.classList.remove('open');

            } else {
                window.closeOCChannel();
            }
        });
    }

    /* STARTUP */

    if (document.readyState === 'loading') {
        document.addEventListener(
            'DOMContentLoaded',
            initializeEvents
        );
    } else {
        initializeEvents();
    }

})();
