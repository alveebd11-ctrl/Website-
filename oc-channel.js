/* =========================================================
   OC COLLECTION UNIVERSE
   Telegram Media Synchronization
   Photos + Videos + Animations
   Dark Neon Glassmorphism Feed Layout
   ========================================================= */

(function () {
    'use strict';

    /* =====================================================
       CONFIGURATION
       ===================================================== */

    const containerId = 'channelPostsContainer';
    let mediaCards = [];
    let isFetchingMedia = false;


    /* =====================================================
       DOM HELPERS
       ===================================================== */

    function getElement(id) {
        return document.getElementById(id);
    }


    /* =====================================================
       OPEN OC CHANNEL
       ===================================================== */

    window.openOCChannel = function () {
        const channel = getElement('ocChannelWindow');

        if (!channel) {
            console.error(
                'OC Collection: #ocChannelWindow was not found.'
            );
            return;
        }

        const naughtyWindow = getElement('naughtyWindow');
        const profileModal = getElement('profileModal');

        if (naughtyWindow) {
            naughtyWindow.classList.remove('open');
        }

        if (profileModal) {
            profileModal.style.display = 'none';
        }

        const menu = getElement('ocMenuPopover');
        const searchPanel = getElement('ocSearchPanel');
        const searchInput = getElement('ocSearchInput');

        if (menu) {
            menu.classList.remove('open');
        }

        if (searchPanel) {
            searchPanel.classList.remove('open');
        }

        if (searchInput) {
            searchInput.value = '';
        }

        channel.classList.add('open');
        channel.setAttribute('aria-hidden', 'false');

        document.body.style.overflow = 'hidden';

        // Fetch and render the Telegram media feed as individual cards.
        fetchTelegramMedia();
    };


    /* =====================================================
       CLOSE OC CHANNEL
       ===================================================== */

    window.closeOCChannel = function () {
        const channel = getElement('ocChannelWindow');

        if (!channel) {
            return;
        }

        channel.classList.remove('open');
        channel.setAttribute('aria-hidden', 'true');

        getElement('ocMenuPopover')?.classList.remove('open');
        getElement('ocSearchPanel')?.classList.remove('open');

        const searchInput = getElement('ocSearchInput');

        if (searchInput) {
            searchInput.value = '';
            filterOCPosts('');
        }

        closePopupPlayer();

        document.body.style.overflow = '';
    };


    /* =====================================================
       MENU & SEARCH
       ===================================================== */

    window.toggleOCMenu = function () {
        const menu = getElement('ocMenuPopover');
        if (!menu) return;
        const opening = !menu.classList.contains('open');
        menu.classList.toggle('open', opening);
        if (opening) {
            getElement('ocSearchPanel')?.classList.remove('open');
        }
    };

    window.toggleOCSearch = function () {
        const searchPanel = getElement('ocSearchPanel');
        if (!searchPanel) return;
        const opening = !searchPanel.classList.contains('open');
        searchPanel.classList.toggle('open', opening);
        if (opening) {
            getElement('ocMenuPopover')?.classList.remove('open');
        }
    };


    /* =====================================================
       FILTER MEDIA CARDS
       ===================================================== */

    function filterOCPosts(query) {
        const searchText = String(query || '').trim().toLowerCase();

        mediaCards.forEach(function (card) {
            const metadata = (card.dataset.metadata || '').toLowerCase();
            card.style.display =
                !searchText || metadata.includes(searchText) ? '' : 'none';
        });
    }


    /* =====================================================
       STATUS MESSAGE
       ===================================================== */

    function showChannelStatus(message) {
        const container = getElement(containerId);
        if (!container) return;

        container.replaceChildren();
        mediaCards = [];

        const status = document.createElement('div');
        status.className = 'oc-channel-status';
        status.setAttribute('role', 'status');
        status.textContent = message;
        status.style.cssText = [
            'width: 100%',
            'padding: 40px 16px',
            'text-align: center',
            'color: #94a3b8',
            'font-size: 15px'
        ].join(';');

        container.appendChild(status);
    }


    /* =====================================================
       FETCH TELEGRAM MEDIA & RENDER INDIVIDUAL CARDS
       ===================================================== */

    async function fetchTelegramMedia() {
        const container = getElement(containerId);

        if (!container || isFetchingMedia) {
            return;
        }

        isFetchingMedia = true;
        container.setAttribute('aria-busy', 'true');

        try {
            const response = await fetch('/api/get_videos', {
                method: 'GET',
                headers: {
                    'Accept': 'application/json'
                },
                cache: 'no-store'
            });

            if (!response.ok) {
                throw new Error('Telegram API returned HTTP ' + response.status);
            }

            const data = await response.json();

            if (data.status !== 'success' || !Array.isArray(data.posts)) {
                throw new Error('Invalid Telegram media API response.');
            }

            container.replaceChildren();
            mediaCards = [];

            let processedCount = 0;

            // Render each media item as an individual compact card in the vertical feed
            data.posts.forEach(function (post) {
                if (!post || typeof post.media_url !== 'string') {
                    return;
                }

                let mediaURL;
                try {
                    mediaURL = new URL(post.media_url, window.location.origin);
                } catch (error) {
                    return;
                }

                if (
                    mediaURL.origin !== window.location.origin ||
                    !mediaURL.pathname.startsWith('/api/channel/media/')
                ) {
                    return;
                }

                const mediaType = String(post.media_type || '').toLowerCase();
                const mimeType = String(post.mime_type || '').toLowerCase();

                const isImage = mediaType === 'photo' || mimeType.startsWith('image/');
                const isVideo = mediaType === 'video' || mediaType === 'animation' || mimeType.startsWith('video/');

                if (!isImage && !isVideo) {
                    return;
                }

                const card = document.createElement('div');
                card.className = 'media-card';
                card.tabIndex = 0;
                card.setAttribute('role', 'button');

                const metadata = String(post.text || '');
                card.dataset.metadata = metadata;
                card.setAttribute('aria-label', metadata ? 'Open media: ' + metadata : 'Open media item');

                if (isVideo) {
                    const video = document.createElement('video');
                    video.src = mediaURL.href;
                    video.muted = true;
                    video.loop = true;
                    video.playsInline = true;
                    video.preload = 'metadata';

                    const playIcon = document.createElement('div');
                    playIcon.className = 'video-overlay-icon';
                    playIcon.setAttribute('aria-hidden', 'true');

                    card.appendChild(video);
                    card.appendChild(playIcon);

                    card.addEventListener('click', function () {
                        openPopupPlayer(mediaURL.href, 'video');
                    });
                } else {
                    const img = document.createElement('img');
                    img.src = mediaURL.href;
                    img.alt = metadata || 'OC Collection photo';
                    img.loading = 'lazy';
                    img.decoding = 'async';

                    card.appendChild(img);

                    card.addEventListener('click', function () {
                        openPopupPlayer(mediaURL.href, 'photo');
                    });
                }

                card.addEventListener('keydown', function (event) {
                    if (event.key === 'Enter' || event.key === ' ') {
                        event.preventDefault();
                        card.click();
                    }
                });

                container.appendChild(card);
                mediaCards.push(card);
                processedCount++;
            });

            if (processedCount === 0) {
                showChannelStatus('No photos or videos have been published yet.');
            }

        } catch (error) {
            console.error('OC Collection: failed to fetch Telegram media.', error);
            if (mediaCards.length === 0) {
                showChannelStatus('Unable to load media. Please try again later.');
            }
        } finally {
            isFetchingMedia = false;
            container.removeAttribute('aria-busy');
        }
    }


    /* =====================================================
       FULLSCREEN POPUP PLAYER
       ===================================================== */

    function openPopupPlayer(src, type) {
        const popup = getElement('ocVideoPopup');
        if (!popup) return;

        const wrapper = popup.querySelector('.oc-popup-video-wrapper');
        if (!wrapper) return;

        wrapper.replaceChildren();

        if (type === 'video') {
            const video = document.createElement('video');
            video.id = 'ocPopupVideoPlayer';
            video.src = src;
            video.controls = true;
            video.autoplay = true;
            video.playsInline = true;
            video.preload = 'metadata';
            video.style.maxWidth = '100%';
            video.style.maxHeight = '100vh';
            video.style.objectFit = 'contain';

            wrapper.appendChild(video);
            popup.classList.add('open');

            const playPromise = video.play();
            if (playPromise && typeof playPromise.catch === 'function') {
                playPromise.catch(function () {});
            }
        } else if (type === 'photo') {
            const img = document.createElement('img');
            img.src = src;
            img.alt = 'OC Collection photo';
            img.style.width = '100%';
            img.style.maxWidth = '100%';
            img.style.maxHeight = '100vh';
            img.style.objectFit = 'contain';

            wrapper.appendChild(img);
            popup.classList.add('open');
        }
    }


    function closePopupPlayer() {
        const popup = getElement('ocVideoPopup');
        if (!popup) return;

        const wrapper = popup.querySelector('.oc-popup-video-wrapper');
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
    }


    /* =====================================================
       INITIALIZE EVENTS
       ===================================================== */

    function initializeEvents() {
        const input = getElement('ocSearchInput');

        if (input && input.dataset.ocInitialized !== 'true') {
            input.dataset.ocInitialized = 'true';
            input.addEventListener('input', function () {
                filterOCPosts(this.value);
            });
            input.addEventListener('keydown', function (event) {
                if (event.key === 'Escape') {
                    window.toggleOCSearch();
                }
            });
        }

        const channel = getElement('ocChannelWindow');
        if (!channel || channel.dataset.ocEventsInitialized === 'true') {
            return;
        }

        channel.dataset.ocEventsInitialized = 'true';

        document.addEventListener('keydown', function (event) {
            if (event.key !== 'Escape') return;

            const popup = getElement('ocVideoPopup');
            if (popup && popup.classList.contains('open')) {
                closePopupPlayer();
                return;
            }

            if (!channel.classList.contains('open')) return;

            const searchPanel = getElement('ocSearchPanel');
            const menu = getElement('ocMenuPopover');

            if (searchPanel && searchPanel.classList.contains('open')) {
                window.toggleOCSearch();
            } else if (menu && menu.classList.contains('open')) {
                menu.classList.remove('open');
            } else {
                window.closeOCChannel();
            }
        });

        const popupCloseBtn = getElement('ocPopupCloseBtn');
        if (popupCloseBtn) {
            popupCloseBtn.addEventListener('click', closePopupPlayer);
        }

        const popup = getElement('ocVideoPopup');
        if (popup) {
            popup.addEventListener('click', function (event) {
                if (
                    event.target === popup ||
                    event.target.classList.contains('oc-popup-video-wrapper')
                ) {
                    closePopupPlayer();
                }
            });
        }
    }


    /* =====================================================
       STARTUP
       ===================================================== */

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initializeEvents);
    } else {
        initializeEvents();
    }

})();
