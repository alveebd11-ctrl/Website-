/* ==========================================
   OC COLLECTION UNIVERSE
   Separate JavaScript Module - Premium Carousel with Telegram API (Updated for Photo & Video)
   ========================================== */

(function () {
    'use strict';

    function getElement(id) {
        return document.getElementById(id);
    }

    /* CAROUSEL & POPUP STATE */
    let mediaCards = [];
    const containerId = 'channelPostsContainer';
    const paginationId = 'ocPagination';
    
    /* OPEN CHANNEL */
    window.openOCChannel = function () {
        const channel = getElement('ocChannelWindow');
        if (!channel) return;

        const naughtyWindow = getElement('naughtyWindow');
        const profileModal = getElement('profileModal');
        if (naughtyWindow) naughtyWindow.classList.remove('open');
        if (profileModal) profileModal.style.display = 'none';

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

        initCarouselProcessing();
        fetchTelegramMedia();
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

        closePopupPlayer();
        document.body.style.overflow = '';
    };

    window.toggleOCMenu = function () {
        const menu = getElement('ocMenuPopover');
        if (!menu) return;
        
        const opening = !menu.classList.contains('open');
        menu.classList.toggle('open', opening);
        if (opening) getElement('ocSearchPanel')?.classList.remove('open');
    };

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

    function filterOCPosts(query) {
        const searchText = String(query || '').trim().toLowerCase();
        
        mediaCards.forEach(card => {
            const metadata = (card.dataset.metadata || '').toLowerCase();
            card.style.display = !searchText || metadata.includes(searchText) ? '' : 'none';
        });
        
        setTimeout(updateActiveDot, 100);
    }

    /* TELEGRAM MEDIA FETCHING LOGIC (Photos & Videos) */
    async function fetchTelegramMedia() {
        const container = getElement(containerId);
        if (!container) return;

        if (container.dataset.mediaLoaded === 'true') return;

        try {
            const response = await fetch('/api/get_videos'); 
            
            if (!response.ok) throw new Error('API Response was not ok');

            const data = await response.json();

            if (data.status === 'success' && data.posts && data.posts.length > 0) {
                // Reverse the posts so the newest comes first (or change backend order)
                const posts = data.posts.reverse();
                
                posts.forEach(post => {
                    if (!post.media_url) return;

                    const wrapper = document.createElement('div');
                    wrapper.className = 'temp-media-wrapper';
                    wrapper.dataset.type = post.media_type;
                    wrapper.dataset.text = post.text || '';

                    if (post.media_type === 'photo') {
                        const img = document.createElement('img');
                        img.src = post.media_url;
                        wrapper.appendChild(img);
                    } else if (post.media_type === 'video' || post.media_type === 'animation') {
                        const vid = document.createElement('video');
                        vid.src = post.media_url;
                        wrapper.appendChild(vid);
                    }
                    
                    container.appendChild(wrapper);
                });
                
                container.dataset.mediaLoaded = 'true';
            }
        } catch (error) {
            console.error("Error fetching media from backend:", error);
        }
    }

    /* CAROUSEL PROCESSING */
    let isCarouselObserved = false;
    
    function initCarouselProcessing() {
        const postsContainer = getElement(containerId);
        if (!postsContainer || isCarouselObserved) return;

        const observer = new MutationObserver((mutations) => {
            let hasNewMedia = false;
            mutations.forEach(mutation => {
                mutation.addedNodes.forEach(node => {
                    if (node.nodeType === 1) {
                        if (processPostElement(node)) hasNewMedia = true;
                    }
                });
            });
            if (hasNewMedia) updatePagination();
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

        const img = element.querySelector('img') || (element.tagName === 'IMG' ? element : null);
        const video = element.querySelector('video') || (element.tagName === 'VIDEO' ? element : null);
        const postsContainer = getElement(containerId);

        if (img || video) {
            const card = document.createElement('div');
            card.className = 'oc-video-card';
            
            card.dataset.metadata = element.dataset.text || element.textContent || ''; 

            if (video) {
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
                card.addEventListener('click', () => openPopupPlayer(clonedVideo.src, 'video'));
            } else if (img) {
                const clonedImg = document.createElement('img');
                clonedImg.src = img.src;
                clonedImg.style.width = '100%';
                clonedImg.style.height = '100%';
                clonedImg.style.objectFit = 'cover';
                clonedImg.style.pointerEvents = 'none';

                card.appendChild(clonedImg);
                card.addEventListener('click', () => openPopupPlayer(clonedImg.src, 'photo'));
            }

            if (element.parentNode === postsContainer) {
                postsContainer.replaceChild(card, element);
            } else {
                postsContainer.appendChild(card);
                if (element.parentNode) element.parentNode.remove();
            }

            mediaCards.push(card);
            return true;
        } else {
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
        const visibleCards = mediaCards.filter(c => c.style.display !== 'none');

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

        const visibleCards = mediaCards.filter(c => c.style.display !== 'none');
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

    /* POPUP PLAYER (Handles both Photo & Video) */
    function openPopupPlayer(src, type) {
        const popup = getElement('ocVideoPopup');
        const wrapper = popup.querySelector('.oc-popup-video-wrapper');
        if (!popup || !wrapper) return;
        
        wrapper.innerHTML = ''; // Clear previous media

        if (type === 'video') {
            const vid = document.createElement('video');
            vid.src = src;
            vid.controls = true;
            vid.playsInline = true;
            vid.id = 'ocPopupVideoPlayer';
            wrapper.appendChild(vid);
            
            const playPromise = vid.play();
            if (playPromise !== undefined) {
                playPromise.catch(() => console.log("Auto-play prevented"));
            }
        } else if (type === 'photo') {
            const img = document.createElement('img');
            img.src = src;
            img.style.width = '100%';
            img.style.maxHeight = '100vh';
            img.style.objectFit = 'contain';
            wrapper.appendChild(img);
        }

        popup.classList.add('open');
    }

    function closePopupPlayer() {
        const popup = getElement('ocVideoPopup');
        const wrapper = popup?.querySelector('.oc-popup-video-wrapper');
        if (!popup || !wrapper) return;
        
        const vid = wrapper.querySelector('video');
        if (vid) {
            vid.pause();
            vid.src = '';
        }
        wrapper.innerHTML = '';
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

        document.addEventListener('click', function (event) {
            const menu = getElement('ocMenuPopover');
            const menuButton = channel.querySelector('.oc-header-action-btn[aria-label="Menu"]');
            if (menu && menuButton && menu.classList.contains('open') &&
                !menu.contains(event.target) && !menuButton.contains(event.target)) {
                menu.classList.remove('open');
            }
        });

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
