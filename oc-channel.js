
/* ==========================================
   OC COLLECTION UNIVERSE
   All channel interactions in this file
   ========================================== */

(function () {
    'use strict';

    function get(id) {
        return document.getElementById(id);
    }

    /* OPEN CHANNEL */
    window.openOCChannel = function () {
        const channel = get('ocChannelWindow');

        if (!channel) {
            console.error(
                'OC Collection Universe: channel HTML not found.'
            );
            return;
        }

        // Close other dashboard overlays.
        const naughty = get('naughtyWindow');
        const profileModal = get('profileModal');

        if (naughty) naughty.classList.remove('open');

        if (profileModal) {
            profileModal.style.display = 'none';
        }

        // Close menu and search before opening.
        const menu = get('ocMenuPopover');
        const search = get('ocSearchPanel');

        if (menu) menu.classList.remove('open');
        if (search) search.classList.remove('open');

        channel.classList.add('open');
        channel.setAttribute('aria-hidden', 'false');

        document.body.style.overflow = 'hidden';

        const body = channel.querySelector('.oc-channel-body');

        if (body) {
            requestAnimationFrame(function () {
                body.scrollTop = body.scrollHeight;
            });
        }
    };

    /* CLOSE CHANNEL / BACK BUTTON */
    window.closeOCChannel = function () {
        const channel = get('ocChannelWindow');

        if (!channel) return;

        channel.classList.remove('open');
        channel.setAttribute('aria-hidden', 'true');

        const menu = get('ocMenuPopover');
        const search = get('ocSearchPanel');
        const input = get('ocSearchInput');

        if (menu) menu.classList.remove('open');
        if (search) search.classList.remove('open');

        if (input) {
            input.value = '';
            filterPosts('');
        }

        document.body.style.overflow = '';
    };

    /* THREE-DOT MENU */
    window.toggleOCMenu = function () {
        const menu = get('ocMenuPopover');

        if (!menu) return;

        const opening = !menu.classList.contains('open');

        menu.classList.toggle('open', opening);

        if (opening) {
            const search = get('ocSearchPanel');

            if (search) search.classList.remove('open');
        }
    };

    /* OPEN / CLOSE SEARCH */
    window.toggleOCSearch = function () {
        const panel = get('ocSearchPanel');

        if (!panel) return;

        const opening = !panel.classList.contains('open');

        panel.classList.toggle('open', opening);

        const menu = get('ocMenuPopover');

        if (opening && menu) {
            menu.classList.remove('open');
        }

        const input = get('ocSearchInput');

        if (!input) return;

        if (opening) {
            setTimeout(function () {
                input.focus();
            }, 50);
        } else {
            input.value = '';
            filterPosts('');
        }
    };

    /* SEARCH CHANNEL POSTS */
    function filterPosts(query) {
        const container = get('channelPostsContainer');

        if (!container) return;

        const term = String(query || '')
            .trim()
            .toLocaleLowerCase();

        container.querySelectorAll('.oc-post-bubble')
            .forEach(function (post) {
                const content = (
                    post.textContent || ''
                ).toLocaleLowerCase();

                post.style.display =
                    !term || content.includes(term)
                        ? ''
                        : 'none';
            });
    }

    /* INITIALIZE CHANNEL EVENTS */
    function initializeOCChannel() {
        const input = get('ocSearchInput');

        if (input && input.dataset.ocInitialized !== 'true') {
            input.dataset.ocInitialized = 'true';

            input.addEventListener('input', function () {
                filterPosts(this.value);
            });
        }

        const channel = get('ocChannelWindow');

        if (
            channel &&
            channel.dataset.ocInitialized !== 'true'
        ) {
            channel.dataset.ocInitialized = 'true';

            // Close the menu when clicking outside it.
            document.addEventListener('click', function (event) {
                const menu = get('ocMenuPopover');
                const menuButton = channel.querySelector('.oc-menu-btn');

                if (
                    menu &&
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

                const search = get('ocSearchPanel');
                const menu = get('ocMenuPopover');

                if (search && search.classList.contains('open')) {
                    window.toggleOCSearch();
                } else if (menu && menu.classList.contains('open')) {
                    menu.classList.remove('open');
                } else {
                    window.closeOCChannel();
                }
            });
        }
    }

    /* MUTE / UNMUTE CHANNEL MEDIA */
    window.toggleOCMute = function (button) {
        const channel = get('ocChannelWindow');

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
                        // Playback may require another user action.
                    });
                }
            }
        });

        button.dataset.muted = shouldUnmute ? 'false' : 'true';
        button.textContent = shouldUnmute ? 'Mute' : 'Unmute';
    };

    // Initialize after the HTML is available.
    if (document.readyState === 'loading') {
        document.addEventListener(
            'DOMContentLoaded',
            initializeOCChannel
        );
    } else {
        initializeOCChannel();
    }

})();
