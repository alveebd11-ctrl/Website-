
/* =========================================================
   OC COLLECTION UNIVERSE
   Telegram Media Synchronization
   Individual Photo / Video Cards
   Premium Dark Neon Glassmorphism Feed
   ========================================================= */

(function () {
    'use strict';

    /* =====================================================
       CONFIGURATION
       ===================================================== */

    const CONFIG = {
        containerId: 'channelPostsContainer',
        channelId: 'ocChannelWindow',
        popupId: 'ocVideoPopup',
        popupWrapper: '.oc-popup-video-wrapper',
        refreshInterval: 30000,
        apiURL: '/api/get_videos'
    };

    let mediaCards = new Map();
    let isFetchingMedia = false;
    let pollingTimer = null;
    let previousBodyOverflow = '';
    let lastFocusedCard = null;

    /* =====================================================
       DOM HELPERS
       ===================================================== */

    function getElement(id) {
        return document.getElementById(id);
    }

    function getMediaContainer() {
        return getElement(CONFIG.containerId);
    }

    function removeChannelStatus() {
        const container = getMediaContainer();
        if (!container) return;

        const status = container.querySelector('.oc-channel-status');

        if (status) {
            status.remove();
        }
    }

    function showChannelStatus(message, state) {
        const container = getMediaContainer();
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

    /* =====================================================
       SAFE MEDIA URL VALIDATION
       ===================================================== */

    function getSafeMediaURL(post) {
        if (!post || typeof post.media_url !== 'string') {
            return null;
        }

        try {
            const url = new URL(
                post.media_url,
                window.location.origin
            );

            if (url.origin !== window.location.origin) {
                return null;
            }

            if (!/^\/api\/channel\/media\/\d+\/?$/.test(url.pathname)) {
                return null;
            }

            return url.href;

        } catch (error) {
            return null;
        }
    }

    /* =====================================================
       MEDIA TYPE DETECTION
       ===================================================== */

    function getMediaType(post) {
        const type = String(
            post.media_type || ''
        ).toLowerCase();

        const mime = String(
            post.mime_type || ''
        ).toLowerCase();

        const isVideo =
            type === 'video' ||
            mime.startsWith('video/') ||
            (
                type === 'animation' &&
                !mime.startsWith('image/')
            );

        const isImage =
            !isVideo &&
            (
                type === 'photo' ||
                mime.startsWith('image/') ||
                type === 'animation'
            );

        if (isVideo) return 'video';
        if (isImage) return 'photo';

        return null;
    }

    /* =====================================================
       CREATE INDIVIDUAL MEDIA CARD
       ===================================================== */

    function createMediaCard(post, mediaURL, mediaType, mediaId) {

        const card = document.createElement('button');

        card.type = 'button';
        card.className = 'media-card';
        card.dataset.mediaId = mediaId;

        card.setAttribute('aria-label',
            mediaType === 'video'
                ? 'Play video'
                : 'View photo'
        );

        /*
         * Caption is kept only for optional searching.
         * It is never displayed inside the card.
         */

        card.dataset.metadata = String(post.text || '');

        if (mediaType === 'video') {

            const video = document.createElement('video');

            video.src = mediaURL;
            video.muted = true;
            video.loop = true;
            video.playsInline = true;
            video.preload = 'metadata';
            video.tabIndex = -1;
            video.setAttribute('aria-hidden', 'true');

            card.appendChild(video);

            const playIcon = document.createElement('span');

            playIcon.className = 'video-overlay-icon';
            playIcon.setAttribute('aria-hidden', 'true');

            card.appendChild(playIcon);

        } else {

            const img = document.createElement('img');

            img.src = mediaURL;
            img.alt = 'OC Collection photo';
            img.loading = 'lazy';
            img.decoding = 'async';

            card.appendChild(img);
        }

        card.addEventListener('click', function () {

            lastFocusedCard = card;

            openPopupPlayer(
                mediaURL,
                mediaType
            );
        });

        return card;
    }

    /* =====================================================
       PROCESS TELEGRAM POSTS
       ===================================================== */

    function renderTelegramPosts(posts) {

        const container = getMediaContainer();

        if (!container || !Array.isArray(posts)) {
            return 0;
        }

        const newItems = [];
        const batchIds = new Set();

        posts.forEach(function (post) {

            if (!post) return;

            const mediaURL = getSafeMediaURL(post);

            if (!mediaURL) return;

            const mediaType = getMediaType(post);

            if (!mediaType) return;

            const mediaId = String(
                post.message_id ||
                post.id ||
                mediaURL
            );

            if (
                mediaCards.has(mediaId) ||
                batchIds.has(mediaId)
            ) {
                return;
            }

            batchIds.add(mediaId);

            const card = createMediaCard(
                post,
                mediaURL,
                mediaType,
                mediaId
            );

            newItems.push({
                id: mediaId,
                card: card
            });
        });

        /*
         * The Telethon API returns newest posts first.
         * Reverse insertion + prepend preserves newest-first order.
         * Every Telegram message gets its own individual card.
         */

        removeChannelStatus();

        for (let i = newItems.length - 1; i >= 0; i--) {

            const item = newItems[i];

            container.prepend(item.card);

            mediaCards.set(
                item.id,
                item.card
            );
        }

        if (newItems.length > 0) {
            filterOCPosts(
                getElement('ocSearchInput')?.value || ''
            );
        }

        return newItems.length;
    }

    /* =====================================================
       FETCH TELEGRAM MEDIA
       ===================================================== */

    async function fetchTelegramMedia() {

        const container = getMediaContainer();

        if (!container || isFetchingMedia) {
            return;
        }

        isFetchingMedia = true;

        container.setAttribute(
            'aria-busy',
            'true'
        );

        if (mediaCards.size === 0) {
            showChannelStatus(
                'Loading photos and videos...',
                'loading'
            );
        }

        try {

            const response = await fetch(
                CONFIG.apiURL,
                {
                    method: 'GET',
                    headers: {
                        'Accept': 'application/json'
                    },
                    credentials: 'same-origin',
                    cache: 'no-store'
                }
            );

            if (!response.ok) {
                throw new Error(
                    'Telegram API HTTP error: ' +
                    response.status
                );
            }

            const data = await response.json();

            if (
                data.status !== 'success' ||
                !Array.isArray(data.posts)
            ) {
                throw new Error(
                    data.error ||
                    'Invalid Telegram API response.'
                );
            }

            renderTelegramPosts(data.posts);

            if (mediaCards.size === 0) {

                showChannelStatus(
                    'No photos or videos have been published yet.',
                    'empty'
                );
            }

        } catch (error) {

            console.error(
                'OC Collection media loading error:',
                error
            );

            if (mediaCards.size === 0) {

                showChannelStatus(
                    'Unable to load media. Please try again later.',
                    'error'
                );
            }

        } finally {

            isFetchingMedia = false;

            container.removeAttribute(
                'aria-busy'
            );
        }
    }

    /*
     * Expose fetch function for manual refresh if required.
     */

    window.fetchTelegramMedia = fetchTelegramMedia;

    /* =====================================================
       AUTOMATIC MEDIA REFRESH
       ===================================================== */

    function startMediaPolling() {

        stopMediaPolling();

        pollingTimer = setInterval(function () {

            const channel = getElement(
                CONFIG.channelId
            );

            if (
                channel &&
                channel.classList.contains('open')
            ) {
                fetchTelegramMedia();
            }

        }, CONFIG.refreshInterval);
    }

    function stopMediaPolling() {

        if (pollingTimer !== null) {

            clearInterval(pollingTimer);

            pollingTimer = null;
        }
    }

    /* =====================================================
       SEARCH / FILTER
       ===================================================== */

    function filterOCPosts(query) {

        const searchText = String(
            query || ''
        ).trim().toLowerCase();

        mediaCards.forEach(function (card) {

            const metadata = String(
                card.dataset.metadata || ''
            ).toLowerCase();

            card.hidden = Boolean(
                searchText &&
                !metadata.includes(searchText)
            );
        });
    }

    /* =====================================================
       OPEN CHANNEL
       ===================================================== */

    window.openOCChannel = function () {

        initializeEvents();

        const channel = getElement(
            CONFIG.channelId
        );

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

        const naughtyWindow = getElement(
            'naughtyWindow'
        );

        const profileModal = getElement(
            'profileModal'
        );

        if (naughtyWindow) {
            naughtyWindow.classList.remove('open');
        }

        if (profileModal) {
            profileModal.style.display = 'none';
        }

        getElement('ocMenuPopover')
            ?.classList.remove('open');

        getElement('ocSearchPanel')
            ?.classList.remove('open');

        const searchInput = getElement(
            'ocSearchInput'
        );

        if (searchInput) {

            searchInput.value = '';

            filterOCPosts('');
        }

        channel.classList.add('open');

        channel.setAttribute(
            'aria-hidden',
            'false'
        );

        document.body.style.overflow = 'hidden';

        closePopupPlayer();

        fetchTelegramMedia();

        startMediaPolling();
    };

    /* =====================================================
       CLOSE CHANNEL
       ===================================================== */

    window.closeOCChannel = function () {

        const channel = getElement(
            CONFIG.channelId
        );

        if (!channel) return;

        channel.classList.remove('open');

        channel.setAttribute(
            'aria-hidden',
            'true'
        );

        getElement('ocMenuPopover')
            ?.classList.remove('open');

        getElement('ocSearchPanel')
            ?.classList.remove('open');

        const searchInput = getElement(
            'ocSearchInput'
        );

        if (searchInput) {

            searchInput.value = '';

            filterOCPosts('');
        }

        closePopupPlayer();

        stopMediaPolling();

        document.body.style.overflow =
            previousBodyOverflow;
    };

    /* =====================================================
       MENU TOGGLE
       ===================================================== */

    window.toggleOCMenu = function () {

        const menu = getElement(
            'ocMenuPopover'
        );

        if (!menu) return;

        const opening =
            !menu.classList.contains('open');

        menu.classList.toggle(
            'open',
            opening
        );

        if (opening) {

            getElement('ocSearchPanel')
                ?.classList.remove('open');
        }
    };

    /* =====================================================
       SEARCH TOGGLE
       ===================================================== */

    window.toggleOCSearch = function () {

        const panel = getElement(
            'ocSearchPanel'
        );

        if (!panel) return;

        const opening =
            !panel.classList.contains('open');

        panel.classList.toggle(
            'open',
            opening
        );

        if (opening) {

            getElement('ocMenuPopover')
                ?.classList.remove('open');

            const input = getElement(
                'ocSearchInput'
            );

            if (input) {
                setTimeout(function () {
                    input.focus();
                }, 50);
            }

        } else {

            const input = getElement(
                'ocSearchInput'
            );

            if (input) {
                input.value = '';
            }

            filterOCPosts('');
        }
    };

    /* =====================================================
       FULLSCREEN POPUP PLAYER
       ===================================================== */

    function openPopupPlayer(src, type) {

        const popup = getElement(
            CONFIG.popupId
        );

        if (!popup) {
            console.error(
                'OC Collection: Popup element not found.'
            );
            return;
        }

        const wrapper = popup.querySelector(
            CONFIG.popupWrapper
        );

        if (!wrapper) {
            console.error(
                'OC Collection: Popup media wrapper not found.'
            );
            return;
        }

        wrapper.replaceChildren();

        if (type === 'video') {

            const video = document.createElement(
                'video'
            );

            video.id = 'ocPopupVideoPlayer';

            video.src = src;
            video.controls = true;
            video.autoplay = true;
            video.playsInline = true;
            video.preload = 'metadata';

            video.className = 'oc-popup-media-video';

            wrapper.appendChild(video);

            popup.classList.add('open');

            popup.setAttribute(
                'aria-hidden',
                'false'
            );

            const playPromise = video.play();

            if (
                playPromise &&
                typeof playPromise.catch === 'function'
            ) {
                playPromise.catch(function () {
                    // Browser autoplay restrictions are normal.
                });
            }

        } else if (type === 'photo') {

            const img = document.createElement(
                'img'
            );

            img.src = src;

            img.alt = 'OC Collection photo';

            img.className = 'oc-popup-media-image';

            wrapper.appendChild(img);

            popup.classList.add('open');

            popup.setAttribute(
                'aria-hidden',
                'false'
            );
        }
    }

    /* =====================================================
       CLOSE POPUP PLAYER
       ===================================================== */

    function closePopupPlayer() {

        const popup = getElement(
            CONFIG.popupId
        );

        if (!popup) return;

        const wrapper = popup.querySelector(
            CONFIG.popupWrapper
        );

        if (wrapper) {

            const video = wrapper.querySelector(
                'video'
            );

            if (video) {

                video.pause();

                video.removeAttribute('src');

                video.load();
            }

            wrapper.replaceChildren();
        }

        popup.classList.remove('open');

        popup.setAttribute(
            'aria-hidden',
            'true'
        );

        if (
            lastFocusedCard &&
            document.contains(lastFocusedCard)
        ) {
            lastFocusedCard.focus();
        }

        lastFocusedCard = null;
    }

    /* =====================================================
       INITIALIZE EVENTS
       ===================================================== */

    function initializeEvents() {

        const input = getElement(
            'ocSearchInput'
        );

        if (
            input &&
            input.dataset.ocInitialized !== 'true'
        ) {

            input.dataset.ocInitialized = 'true';

            input.addEventListener(
                'input',
                function () {
                    filterOCPosts(this.value);
                }
            );
        }

        const channel = getElement(
            CONFIG.channelId
        );

        if (
            !channel ||
            channel.dataset.ocEventsInitialized === 'true'
        ) {
            return;
        }

        channel.dataset.ocEventsInitialized = 'true';

        /* Outside click closes menu */

        document.addEventListener(
            'click',
            function (event) {

                if (!channel.classList.contains('open')) {
                    return;
                }

                const menu = getElement(
                    'ocMenuPopover'
                );

                const menuButton = channel.querySelector(
                    '.oc-menu-btn, .oc-header-action-btn[aria-label="Menu"]'
                );

                if (
                    menu &&
                    menu.classList.contains('open') &&
                    !menu.contains(event.target) &&
                    !menuButton?.contains(event.target)
                ) {
                    menu.classList.remove('open');
                }
            }
        );

        /* Escape key support */

        document.addEventListener(
            'keydown',
            function (event) {

                if (event.key !== 'Escape') {
                    return;
                }

                const popup = getElement(
                    CONFIG.popupId
                );

                if (
                    popup &&
                    popup.classList.contains('open')
                ) {
                    closePopupPlayer();
                    return;
                }

                if (!channel.classList.contains('open')) {
                    return;
                }

                const searchPanel = getElement(
                    'ocSearchPanel'
                );

                const menu = getElement(
                    'ocMenuPopover'
                );

                if (
                    searchPanel &&
                    searchPanel.classList.contains('open')
                ) {
                    window.toggleOCSearch();

                } else if (
                    menu &&
                    menu.classList.contains('open')
                ) {
                    menu.classList.remove('open');

                } else {
                    window.closeOCChannel();
                }
            }
        );

        /* Popup close button */

        const popupCloseBtn = getElement(
            'ocPopupCloseBtn'
        );

        if (popupCloseBtn) {

            popupCloseBtn.addEventListener(
                'click',
                closePopupPlayer
            );
        }

        /* Popup background click */

        const popup = getElement(
            CONFIG.popupId
        );

        if (popup) {

            popup.addEventListener(
                'click',
                function (event) {

                    if (
                        event.target === popup ||
                        event.target.classList.contains(
                            'oc-popup-video-wrapper'
                        )
                    ) {
                        closePopupPlayer();
                    }
                }
            );
        }
    }

    /* =====================================================
       STARTUP
       ===================================================== */

    if (document.readyState === 'loading') {

        document.addEventListener(
            'DOMContentLoaded',
            initializeEvents
        );

    } else {

        initializeEvents();
    }

})();
