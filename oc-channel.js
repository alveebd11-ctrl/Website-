
/* =========================================================
   OC COLLECTION UNIVERSE
   Telegram Media Synchronization
   Photos + Videos + Animations
   Carousel + Search + Pagination + Popup Player
   ========================================================= */

(function () {
    'use strict';

    /* =====================================================
       CONFIGURATION
       ===================================================== */

    const containerId = 'channelPostsContainer';
    const paginationId = 'ocPagination';

    let mediaCards = [];
    let isFetchingMedia = false;

    let postsObserver = null;
    let observedContainer = null;


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

        initCarouselProcessing();

        // Refresh the Telegram media whenever the channel opens.
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
       MENU
       ===================================================== */

    window.toggleOCMenu = function () {
        const menu = getElement('ocMenuPopover');

        if (!menu) {
            return;
        }

        const opening = !menu.classList.contains('open');

        menu.classList.toggle('open', opening);

        if (opening) {
            getElement('ocSearchPanel')?.classList.remove('open');
        }
    };


    /* =====================================================
       SEARCH PANEL
       ===================================================== */

    window.toggleOCSearch = function () {
        const searchPanel = getElement('ocSearchPanel');

        if (!searchPanel) {
            return;
        }

        const opening = !searchPanel.classList.contains('open');

        searchPanel.classList.toggle('open', opening);

        if (opening) {
            getElement('ocMenuPopover')?.classList.remove('open');
        }

        const input = getElement('ocSearchInput');

        if (!input) {
            return;
        }

        if (opening) {
            window.setTimeout(function () {
                input.focus();
            }, 50);
        } else {
            input.value = '';
            filterOCPosts('');
        }
    };


    /* =====================================================
       FILTER MEDIA CARDS
       ===================================================== */

    function filterOCPosts(query) {
        const searchText = String(query || '')
            .trim()
            .toLowerCase();

        mediaCards.forEach(function (card) {
            const metadata = (
                card.dataset.metadata || ''
            ).toLowerCase();

            card.style.display =
                !searchText || metadata.includes(searchText)
                    ? ''
                    : 'none';
        });

        updatePagination();
    }


    /* =====================================================
       STATUS MESSAGE
       ===================================================== */

    function showChannelStatus(message) {
        const container = getElement(containerId);

        if (!container) {
            return;
        }

        container.replaceChildren();

        mediaCards = [];

        const status = document.createElement('div');

        status.className = 'oc-channel-status';
        status.setAttribute('role', 'status');
        status.textContent = message;

        status.style.cssText = [
            'flex: 0 0 100%',
            'width: 100%',
            'box-sizing: border-box',
            'padding: 28px 16px',
            'text-align: center',
            'color: #cbd5e1',
            'font-size: 14px'
        ].join(';');

        container.appendChild(status);

        updatePagination();
    }


    /* =====================================================
       FETCH TELEGRAM MEDIA
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
                throw new Error(
                    'Telegram API returned HTTP ' + response.status
                );
            }

            const data = await response.json();

            if (
                data.status !== 'success' ||
                !Array.isArray(data.posts)
            ) {
                throw new Error(
                    'Invalid Telegram media API response.'
                );
            }

            // Do not reverse this array.
            // The backend already returns newest media first.

            container.replaceChildren();
            mediaCards = [];

            let processedCount = 0;

            data.posts.forEach(function (post) {
                if (
                    !post ||
                    typeof post.media_url !== 'string'
                ) {
                    return;
                }

                let mediaURL;

                try {
                    mediaURL = new URL(
                        post.media_url,
                        window.location.origin
                    );
                } catch (error) {
                    return;
                }

                // Accept only same-origin URLs belonging to our
                // own Telegram media endpoint.
                if (
                    mediaURL.origin !== window.location.origin ||
                    !mediaURL.pathname.startsWith(
                        '/api/channel/media/'
                    )
                ) {
                    return;
                }

                const mediaType = String(
                    post.media_type || ''
                ).toLowerCase();

                const mimeType = String(
                    post.mime_type || ''
                ).toLowerCase();

                const isImage =
                    mediaType === 'photo' ||
                    mimeType.startsWith('image/');

                const isVideo =
                    mediaType === 'video' ||
                    mediaType === 'animation';

                if (!isImage && !isVideo) {
                    return;
                }

                // Temporary wrapper. MutationObserver also
                // supports processing existing HTML media.
                const wrapper = document.createElement('div');

                wrapper.className = 'temp-media-wrapper';

                wrapper.dataset.type = mediaType;
                wrapper.dataset.text = String(
                    post.text || ''
                );

                if (isImage) {
                    const img = document.createElement('img');

                    img.src = mediaURL.href;
                    img.alt = String(
                        post.text || 'OC Collection photo'
                    );

                    img.loading = 'lazy';
                    img.decoding = 'async';

                    wrapper.appendChild(img);

                } else {
                    const video = document.createElement('video');

                    video.src = mediaURL.href;

                    video.muted = true;
                    video.loop = true;
                    video.playsInline = true;
                    video.preload = 'metadata';

                    video.setAttribute('aria-label', 'Open video');

                    wrapper.appendChild(video);
                }

                container.appendChild(wrapper);

                // Process immediately so pagination is correct
                // without waiting for MutationObserver.
                if (processPostElement(wrapper)) {
                    processedCount++;
                }
            });

            if (processedCount === 0) {
                showChannelStatus(
                    'No photos or videos have been published yet.'
                );

                return;
            }

            updatePagination();

        } catch (error) {
            console.error(
                'OC Collection: failed to fetch Telegram media.',
                error
            );

            // Preserve existing cards if a refresh fails.
            if (mediaCards.length === 0) {
                showChannelStatus(
                    'Unable to load media. Please try again later.'
                );
            }

        } finally {
            isFetchingMedia = false;

            container.removeAttribute('aria-busy');
        }
    }


    /* =====================================================
       CAROUSEL OBSERVER
       ===================================================== */

    function initCarouselProcessing() {
        const container = getElement(containerId);

        if (!container) {
            return;
        }

        if (
            postsObserver &&
            observedContainer === container
        ) {
            return;
        }

        if (postsObserver) {
            postsObserver.disconnect();
        }

        observedContainer = container;

        postsObserver = new MutationObserver(function (mutations) {
            let hasNewMedia = false;

            mutations.forEach(function (mutation) {
                mutation.addedNodes.forEach(function (node) {
                    if (node.nodeType !== 1) {
                        return;
                    }

                    if (processPostElement(node)) {
                        hasNewMedia = true;
                    }
                });
            });

            if (hasNewMedia) {
                updatePagination();
            }
        });

        postsObserver.observe(container, {
            childList: true,
            subtree: true
        });

        // Also process elements already present in the HTML.
        Array.from(container.children).forEach(function (element) {
            processPostElement(element);
        });

        updatePagination();

        // Install the scroll listener only once.
        if (container.dataset.ocScrollInitialized !== 'true') {
            container.dataset.ocScrollInitialized = 'true';

            container.addEventListener(
                'scroll',
                function () {
                    window.requestAnimationFrame(
                        updateActiveDot
                    );
                },
                { passive: true }
            );
        }
    }


    /* =====================================================
       CONVERT MEDIA INTO CAROUSEL CARDS
       ===================================================== */

    function processPostElement(element) {
        const container = getElement(containerId);

        if (
            !container ||
            !element ||
            element.nodeType !== 1 ||
            !element.parentNode
        ) {
            return false;
        }

        if (
            element.classList.contains('oc-video-card') ||
            element.classList.contains('oc-channel-status')
        ) {
            return false;
        }

        const img = element.matches('img')
            ? element
            : element.querySelector('img');

        const video = element.matches('video')
            ? element
            : element.querySelector('video');

        if (!img && !video) {
            element.style.display = 'none';
            return false;
        }

        const source = img
            ? (img.currentSrc || img.src)
            : (
                video.currentSrc ||
                video.src ||
                video.querySelector('source')?.src ||
                ''
            );

        if (!source) {
            return false;
        }

        const card = document.createElement('div');

        card.className = 'oc-video-card';
        card.tabIndex = 0;
        card.setAttribute('role', 'button');

        const metadata = String(
            element.dataset.text ||
            element.dataset.metadata ||
            element.textContent ||
            ''
        );

        card.dataset.metadata = metadata;

        const label = metadata
            ? 'Open media: ' + metadata
            : 'Open photo or video';

        card.setAttribute('aria-label', label);

        if (video) {
            const clonedVideo = document.createElement('video');

            clonedVideo.src = source;

            clonedVideo.muted = true;
            clonedVideo.loop = true;
            clonedVideo.playsInline = true;
            clonedVideo.preload = 'metadata';

            clonedVideo.controls = false;
            clonedVideo.tabIndex = -1;

            const playIcon = document.createElement('div');

            playIcon.className = 'oc-play-icon';
            playIcon.setAttribute('aria-hidden', 'true');

            // Static SVG markup; no external content is inserted.
            playIcon.innerHTML =
                '<svg viewBox="0 0 24 24" aria-hidden="true">' +
                    '<path d="M8 5v14l11-7z"></path>' +
                '</svg>';

            card.appendChild(clonedVideo);
            card.appendChild(playIcon);

            card.addEventListener('click', function () {
                openPopupPlayer(source, 'video');
            });

        } else {
            const clonedImg = document.createElement('img');

            clonedImg.src = source;

            clonedImg.alt = img.alt || metadata || 'OC Collection photo';
            clonedImg.loading = 'lazy';
            clonedImg.decoding = 'async';

            clonedImg.style.width = '100%';
            clonedImg.style.height = '100%';
            clonedImg.style.objectFit = 'cover';
            clonedImg.style.pointerEvents = 'none';

            card.appendChild(clonedImg);

            card.addEventListener('click', function () {
                openPopupPlayer(source, 'photo');
            });
        }

        // Keyboard accessibility.
        card.addEventListener('keydown', function (event) {
            if (
                event.key === 'Enter' ||
                event.key === ' '
            ) {
                event.preventDefault();
                card.click();
            }
        });

        const parent = element.parentNode;

        if (parent === container) {
            parent.replaceChild(card, element);
        } else {
            parent.replaceWith(card);
        }

        mediaCards.push(card);

        return true;
    }


    /* =====================================================
       PAGINATION DOTS
       ===================================================== */

    function updatePagination() {
        const pagination = getElement(paginationId);

        if (!pagination) {
            return;
        }

        pagination.replaceChildren();

        const visibleCards = mediaCards.filter(function (card) {
            return card.style.display !== 'none';
        });

        visibleCards.forEach(function (card, index) {
            const dot = document.createElement('div');

            dot.className =
                'oc-dot' + (index === 0 ? ' active' : '');

            dot.setAttribute('role', 'button');
            dot.setAttribute('tabindex', '0');

            dot.setAttribute(
                'aria-label',
                'Go to media ' + (index + 1)
            );

            dot.addEventListener('click', function () {
                scrollToCard(card);
            });

            dot.addEventListener('keydown', function (event) {
                if (
                    event.key === 'Enter' ||
                    event.key === ' '
                ) {
                    event.preventDefault();
                    scrollToCard(card);
                }
            });

            pagination.appendChild(dot);
        });

        updateActiveDot();
    }


    function scrollToCard(card) {
        const container = getElement(containerId);

        if (!container || !card) {
            return;
        }

        const targetLeft =
            card.offsetLeft -
            (container.clientWidth - card.offsetWidth) / 2;

        container.scrollTo({
            left: Math.max(0, targetLeft),
            behavior: 'smooth'
        });
    }


    /* =====================================================
       UPDATE ACTIVE PAGINATION DOT
       ===================================================== */

    function updateActiveDot() {
        const container = getElement(containerId);
        const pagination = getElement(paginationId);

        if (!container || !pagination) {
            return;
        }

        const visibleCards = mediaCards.filter(function (card) {
            return card.style.display !== 'none';
        });

        const dots = pagination.querySelectorAll('.oc-dot');

        if (visibleCards.length === 0) {
            dots.forEach(function (dot) {
                dot.classList.remove('active');
            });

            return;
        }

        const containerRect = container.getBoundingClientRect();

        const center =
            containerRect.left + container.clientWidth / 2;

        let activeIndex = 0;
        let smallestDistance = Infinity;

        visibleCards.forEach(function (card, index) {
            const rect = card.getBoundingClientRect();

            const cardCenter = rect.left + rect.width / 2;
            const distance = Math.abs(cardCenter - center);

            if (distance < smallestDistance) {
                smallestDistance = distance;
                activeIndex = index;
            }
        });

        dots.forEach(function (dot, index) {
            dot.classList.toggle(
                'active',
                index === activeIndex
            );
        });
    }


    /* =====================================================
       FULLSCREEN POPUP PLAYER
       ===================================================== */

    function openPopupPlayer(src, type) {
        const popup = getElement('ocVideoPopup');

        if (!popup) {
            console.error(
                'OC Collection: #ocVideoPopup was not found.'
            );
            return;
        }

        const wrapper = popup.querySelector(
            '.oc-popup-video-wrapper'
        );

        if (!wrapper) {
            console.error(
                'OC Collection: popup media wrapper was not found.'
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

            video.style.maxWidth = '100%';
            video.style.maxHeight = '100vh';
            video.style.objectFit = 'contain';

            wrapper.appendChild(video);

            popup.classList.add('open');

            const playPromise = video.play();

            if (playPromise && typeof playPromise.catch === 'function') {
                playPromise.catch(function () {
                    // Browser autoplay restrictions are normal.
                    // The user can press the video play control.
                });
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


    /* =====================================================
       CLOSE POPUP PLAYER
       ===================================================== */

    function closePopupPlayer() {
        const popup = getElement('ocVideoPopup');

        if (!popup) {
            return;
        }

        const wrapper = popup.querySelector(
            '.oc-popup-video-wrapper'
        );

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

        if (
            input &&
            input.dataset.ocInitialized !== 'true'
        ) {
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

        if (
            !channel ||
            channel.dataset.ocEventsInitialized === 'true'
        ) {
            return;
        }

        channel.dataset.ocEventsInitialized = 'true';

        // Close the menu when clicking outside it.
        document.addEventListener('click', function (event) {
            const menu = getElement('ocMenuPopover');

            const menuButton = channel.querySelector(
                '.oc-header-action-btn[aria-label="Menu"]'
            );

            if (
                menu &&
                menuButton &&
                menu.classList.contains('open') &&
                !menu.contains(event.target) &&
                !menuButton.contains(event.target)
            ) {
                menu.classList.remove('open');
            }
        });

        // Escape key handling.
        document.addEventListener('keydown', function (event) {
            if (event.key !== 'Escape') {
                return;
            }

            const popup = getElement('ocVideoPopup');

            if (popup && popup.classList.contains('open')) {
                closePopupPlayer();
                return;
            }

            if (!channel.classList.contains('open')) {
                return;
            }

            const searchPanel = getElement('ocSearchPanel');
            const menu = getElement('ocMenuPopover');

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
        });

        const popupCloseBtn = getElement('ocPopupCloseBtn');

        if (popupCloseBtn) {
            popupCloseBtn.addEventListener(
                'click',
                closePopupPlayer
            );
        }

        const popup = getElement('ocVideoPopup');

        if (popup) {
            popup.addEventListener('click', function (event) {
                if (
                    event.target === popup ||
                    event.target.classList.contains(
                        'oc-popup-video-wrapper'
                    )
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
        document.addEventListener(
            'DOMContentLoaded',
            initializeEvents
        );
    } else {
        initializeEvents();
    }

})();
