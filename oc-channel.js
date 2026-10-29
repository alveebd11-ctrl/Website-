/* ==========================================
   OC COLLECTION UNIVERSE
   Separate JavaScript Module - Premium Carousel with Telegram API
   ========================================== */

(function () {
    'use strict';

    function getElement(id) {
        return document.getElementById(id);
    }

    /* CAROUSEL & POPUP STATE */
    let videoCards = [];
    const containerId = 'channelPostsContainer';
    const paginationId = 'ocPagination';
    
    /* OPEN CHANNEL */
    window.openOCChannel = function () {
        const channel = getElement('ocChannelWindow');
        if (!channel) return;

        // Close other overlays
        const naughtyWindow = getElement('naughtyWindow');
        const profileModal = getElement('profileModal');
        if (naughtyWindow) naughtyWindow.classList.remove('open');
        if (profileModal) profileModal.style.display = 'none';

        // Reset menu and search
        const menu = getElement('ocMenuPopover');
        const searchPanel = getElement('ocSearchPanel');
        const searchInput = getElement('ocSearchInput');
        if (menu) menu.classList.remove('open');
        if (searchPanel) searchPanel.classList.remove('open');
        if (searchInput) {
            searchInput.value = '';
            filterOCPosts('');
        }

        channel.classList.add('open');
        channel.setAttribute('aria-hidden', 'false');
        document.body.style.overflow = 'hidden';

        // Initialize carousel processing if not done
        initCarouselProcessing();
        
        // Fetch videos from Telegram Backend API
        fetchTelegramVideos();
    };

    /* CLOSE CHANNEL */
    window.closeOCChannel = function () {
        const channel = getElement('ocChannelWindow');
        if (!channel) return;

        channel.classList.remove('open');
        channel.setAttribute('aria-hidden', 'true');

        getElement('ocMenuPopover')?.classList.remove('open');
        getElement('ocSearchPanel')?.classList.remove('open');
        
        const searchInput = getElement('ocSearchInput');
        if (searchInput) {
            searchInput.value = '';
            filterOCPosts('');
        }

        closePopupPlayer(); // Close video if playing
        document.body.style.overflow = '';
    };

    /* THREE-DOT MENU */
    window.toggleOCMenu = function () {
        const menu = getElement('ocMenuPopover');
        if (!menu) return;
        
        const opening = !menu.classList.contains('open');
        menu.classList.toggle('open', opening);
        if (opening) getElement('ocSearchPanel')?.classList.remove('open');
    };

    /* SEARCH PANEL */
    window.toggleOCSearch = function () {
        const searchPanel = getElement('ocSearchPanel');
        if (!searchPanel) return;

        const opening = !searchPanel.classList.contains('open');
        searchPanel.classList.toggle('open', opening);

        if (opening) getElement('ocMenuPopover')?.classList.remove('open');

        const input = getElement('ocSearchInput');
        if (!input) return;

        if (opening) {
            setTimeout(() => input.focus(), 50);
        } else {
            input.value = '';
            filterOCPosts('');
        }
    };

    /* SEARCH POSTS (Using hidden metadata or dataset) */
    function filterOCPosts(query) {
        const searchText = String(query || '').trim().toLowerCase();
        
        videoCards.forEach(card => {
            // Check metadata stored in dataset during processing
            const metadata = (card.dataset.metadata || '').toLowerCase();
            card.style.display = !searchText || metadata.includes(searchText) ? '' : 'none';
        });
        
        setTimeout(updateActiveDot, 100);
    }

    /* TELEGRAM VIDEO FETCHING LOGIC */
    async function fetchTelegramVideos() {
        const container = getElement(containerId);
        if (!container) return;

        // Prevent fetching again if videos are already loaded for this session
        if (container.dataset.videosLoaded === 'true') return;

        try {
            // REPLACE THIS URL with your actual Python/Flask/FastAPI backend endpoint
            const response = await fetch('/api/get_videos'); 
            
            if (!response.ok) {
                throw new Error('API Response was not ok');
            }

            const data = await response.json();

            if (data.status === 'success' && data.videos && data.videos.length > 0) {
                data.videos.forEach(videoUrl => {
                    const videoElement = document.createElement('video');
                    videoElement.src = videoUrl;
                    videoElement.controls = true; 
                    
                    // Appending this will trigger the MutationObserver
                    // which automatically converts it into a premium carousel card
                    container.appendChild(videoElement);
                });
                
                // Mark as loaded so it doesn't fetch repeatedly
                container.dataset.videosLoaded = 'true';
            } else {
                console.log("No videos found in the Telegram channel.");
            }
        } catch (error) {
            console.error("Error fetching videos from backend API:", error);
        }
    }

    /* CAROUSEL PROCESSING */
    let isCarouselObserved = false;
    
    function initCarouselProcessing() {
        const postsContainer = getElement(containerId);
        if (!postsContainer || isCarouselObserved) return;

        const observer = new MutationObserver((mutations) => {
            let hasNewVideo = false;
            mutations.forEach(mutation => {
                mutation.addedNodes.forEach(node => {
                    if (node.nodeType === 1) {
                        if (processPostElement(node)) hasNewVideo = true;
                    }
                });
            });
            if (hasNewVideo) updatePagination();
        });

        observer.observe(postsContainer, { childList: true, subtree: true });
        Array.from(postsContainer.children).forEach(processPostElement);
        
        updatePagination();
        
        postsContainer.addEventListener('scroll', () => {
            requestAnimationFrame(updateActiveDot);
        });
        
        isCarouselObserved = true;
    }

    function processPostElement(element) {
        if (element.classList && element.classList.contains('oc-video-card')) return false;

        const video = element.querySelector('video') || (element.tagName === 'VIDEO' ? element : null);
        const postsContainer = getElement(containerId);

        if (video) {
            const card = document.createElement('div');
            card.className = 'oc-video-card';
            
            // Extract text content for search metadata before wiping it
            const originalText = element.textContent || '';
            card.dataset.metadata = originalText; 

            const clonedVideo = document.createElement('video');
            clonedVideo.src = video.src || video.querySelector('source')?.src;
            clonedVideo.muted = true;
            clonedVideo.loop = true;
            clonedVideo.playsInline = true;
            clonedVideo.preload = "metadata";

            const playIcon = document.createElement('div');
            playIcon.className = 'oc-play-icon';
            playIcon.innerHTML = `<svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>`;

            card.appendChild(clonedVideo);
            card.appendChild(playIcon);
            card.addEventListener('click', () => openPopupPlayer(clonedVideo.src));

            if (element.parentNode === postsContainer) {
                postsContainer.replaceChild(card, element);
            } else {
                postsContainer.appendChild(card);
                if (element.parentNode) element.parentNode.remove();
            }

            videoCards.push(card);
            return true;
        } else {
            // Hide non-video elements to keep carousel clean
            element.style.display = 'none';
            return false;
        }
    }

    /* PAGINATION */
    function updatePagination() {
        const paginationContainer = getElement(paginationId);
        const postsContainer = getElement(containerId);
        if (!paginationContainer || !postsContainer) return;
        
        paginationContainer.innerHTML = '';
        // Only get visible cards (in case search is active)
        const visibleCards = videoCards.filter(c => c.style.display !== 'none');

        visibleCards.forEach((card, index) => {
            const dot = document.createElement('div');
            dot.className = 'oc-dot' + (index === 0 ? ' active' : '');
            dot.addEventListener('click', () => {
                card.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'center' });
            });
            paginationContainer.appendChild(dot);
        });
        updateActiveDot();
    }

    function updateActiveDot() {
        const postsContainer = getElement(containerId);
        const paginationContainer = getElement(paginationId);
        if (!postsContainer || !paginationContainer) return;

        const visibleCards = videoCards.filter(c => c.style.display !== 'none');
        if (visibleCards.length === 0) return;

        const scrollLeft = postsContainer.scrollLeft;
        const cardWidth = visibleCards[0].offsetWidth;
        const gap = 15;
        const totalWidth = cardWidth + gap;

        const currentIndex = Math.max(0, Math.min(
            Math.round(scrollLeft / totalWidth), 
            visibleCards.length - 1
        ));
        
        const dots = paginationContainer.querySelectorAll('.oc-dot');
        dots.forEach((dot, idx) => {
            dot.classList.toggle('active', idx === currentIndex);
        });
    }

    /* POPUP PLAYER */
    function openPopupPlayer(videoSrc) {
        const popup = getElement('ocVideoPopup');
        const popupVideo = getElement('ocPopupVideoPlayer');
        if (!popup || !popupVideo) return;
        
        popupVideo.src = videoSrc;
        popup.classList.add('open');
        
        const playPromise = popupVideo.play();
        if (playPromise !== undefined) {
            playPromise.catch(() => console.log("Auto-play prevented"));
        }
    }

    function closePopupPlayer() {
        const popup = getElement('ocVideoPopup');
        const popupVideo = getElement('ocPopupVideoPlayer');
        if (!popup || !popupVideo) return;
        
        popupVideo.pause();
        popupVideo.src = '';
        popup.classList.remove('open');
    }

    /* INITIALIZATION & EVENTS */
    function initializeEvents() {
        const input = getElement('ocSearchInput');
        if (input && input.dataset.ocInitialized !== 'true') {
            input.dataset.ocInitialized = 'true';
            input.addEventListener('input', function () { filterOCPosts(this.value); });
            input.addEventListener('keydown', function (e) {
                if (e.key === 'Escape') window.toggleOCSearch();
            });
        }

        const channel = getElement('ocChannelWindow');
        if (!channel || channel.dataset.ocEventsInitialized === 'true') return;
        channel.dataset.ocEventsInitialized = 'true';

        // Outside click for menu
        document.addEventListener('click', function (event) {
            const menu = getElement('ocMenuPopover');
            const menuButton = channel.querySelector('.oc-header-action-btn[aria-label="Menu"]');
            if (menu && menuButton && menu.classList.contains('open') &&
                !menu.contains(event.target) && !menuButton.contains(event.target)) {
                menu.classList.remove('open');
            }
        });

        // Global Escape key
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

        // Popup close button & backdrop
        const popupCloseBtn = getElement('ocPopupCloseBtn');
        if (popupCloseBtn) popupCloseBtn.addEventListener('click', closePopupPlayer);
        
        const popup = getElement('ocVideoPopup');
        if (popup) {
            popup.addEventListener('click', (e) => {
                if (e.target === popup || e.target.classList.contains('oc-popup-video-wrapper')) {
                    closePopupPlayer();
                }
            });
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initializeEvents);
    } else {
        initializeEvents();
    }

})();
