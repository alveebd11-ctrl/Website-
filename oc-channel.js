
/* ==========================================
   OC COLLECTION UNIVERSE
   Separate JavaScript Module
   ========================================== */

(function () {
    'use strict';

    function getElement(id) {
        return document.getElementById(id);
    }

    /* OPEN CHANNEL */
    window.openOCChannel = function () {
        const channel = getElement('ocChannelWindow');

        if (!channel) {
            console.error(
                'OC Collection Universe: channel element not found.'
            );
            return;
        }

        // Close other dashboard overlays.
        const naughtyWindow = getElement('naughtyWindow');
        const profileModal = getElement('profileModal');

        if (naughtyWindow) {
            naughtyWindow.classList.remove('open');
        }

        if (profileModal) {
            profileModal.style.display = 'none';
        }

        // Reset menu and search.
        const menu = getElement('ocMenuPopover');
        const searchPanel = getElement('ocSearchPanel');
        const searchInput = getElement('ocSearchInput');

        if (menu) menu.classList.remove('open');
        if (searchPanel) searchPanel.classList.remove('open');

        if (searchInput) {
            searchInput.value = '';
            filterOCPosts('');
        }

        // Open channel popup.
        channel.classList.add('open');
        channel.setAttribute('aria-hidden', 'false');

        document.body.style.overflow = 'hidden';

        const channelBody = channel.querySelector('.oc-channel-body');

        if (channelBody) {
            requestAnimationFrame(function () {
                channelBody.scrollTop = channelBody.scrollHeight;
            });
        }
    };

    /* CLOSE CHANNEL */
    window.closeOCChannel = function () {
        const channel = getElement('ocChannelWindow');

        if (!channel) return;

        channel.classList.remove('open');
        channel.setAttribute('aria-hidden', 'true');

        const menu = getElement('ocMenuPopover');
        const searchPanel = getElement('ocSearchPanel');
        const searchInput = getElement('ocSearchInput');

        if (menu) menu.classList.remove('open');
        if (searchPanel) searchPanel.classList.remove('open');

        if (searchInput) {
            searchInput.value = '';
            filterOCPosts('');
        }

        document.body.style.overflow = '';
    };

    /* THREE-DOT MENU */
    window.toggleOCMenu = function () {
        const menu = getElement('ocMenuPopover');

        if (!menu) return;

        const opening = !menu.classList.contains('open');

        menu.classList.toggle('open', opening);

        if (opening) {
            const searchPanel = getElement('ocSearchPanel');

            if (searchPanel) {
                searchPanel.classList.remove('open');
            }
        }
    };

    /* SEARCH PANEL */
    window.toggleOCSearch = function () {
        const searchPanel = getElement('ocSearchPanel');

        if (!searchPanel) return;

        const opening = !searchPanel.classList.contains('open');

        searchPanel.classList.toggle('open', opening);

        const menu = getElement('ocMenuPopover');

        if (opening && menu) {
            menu.classList.remove('open');
        }

        const input = getElement('ocSearchInput');

        if (!input) return;

        if (opening) {
            setTimeout(function () {
                input.focus();
            }, 50);
        } else {
            input.value = '';
            filterOCPosts('');
        }
    };

    /* SEARCH POSTS */
    function filterOCPosts(query) {
        const container = getElement('channelPostsContainer');

        if (!container) return;

        const searchText = String(query || '')
            .trim()
            .toLowerCase();

        const posts = container.querySelectorAll('.oc-post-bubble');

        posts.forEach(function (post) {
            const postText = (
                post.textContent || ''
            ).toLowerCase();

            post.style.display =
                !searchText || postText.includes(searchText)
                    ? ''
                    : 'none';
        });
    }

    /* MUTE / UNMUTE MEDIA */
    window.toggleOCMute = function (button) {
        const channel = getElement('ocChannelWindow');

        if (!channel || !button) return;

        const media = channel.querySelectorAll('video, audio');

        if (!media.length) {
            button.textContent = 'No media available';
            return;
        }

        const currentlyMuted = button.dataset.muted !== 'false';
        const shouldUnmute = currentlyMuted;

        media.forEach(function (item) {
            item.muted = !shouldUnmute;

            if (shouldUnmute && item.tagName === 'VIDEO') {
                const result = item.play();

                if (result && typeof result.catch === 'function') {
                    result.catch(function () {
                        // Browser playback restrictions may apply.
                    });
                }
            }
        });

        button.dataset.muted = shouldUnmute ? 'false' : 'true';
        button.textContent = shouldUnmute ? 'Mute' : 'Unmute';
    };

    /* INITIALIZE SEARCH EVENTS */
    function initializeOCChannel() {
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

        if (!channel || channel.dataset.ocInitialized === 'true') {
            return;
        }

        channel.dataset.ocInitialized = 'true';

        // Close the menu when clicking outside it.
        document.addEventListener('click', function (event) {
            const menu = getElement('ocMenuPopover');
            const menuButton = channel.querySelector('.oc-menu-btn');

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

        // Escape closes search, menu, or channel.
        document.addEventListener('keydown', function (event) {
            if (event.key !== 'Escape') return;
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
    }

    if (document.readyState === 'loading') {
        document.addEventListener(
            'DOMContentLoaded',
            initializeOCChannel
        );
    } else {
        initializeOCChannel();
    }

})();
