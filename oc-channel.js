
/* =========================================
   OC COLLECTION UNIVERSE CHANNEL MODULE
   ========================================= */

(function () {
    'use strict';

    function getElement(id) {
        return document.getElementById(id);
    }

    /* =========================================
       OPEN OC CHANNEL
       ========================================= */
    window.openOCChannel = function () {
        const channel = getElement('ocChannelWindow');

        if (!channel) {
            console.error(
                'OC Channel window was not found in the HTML.'
            );
            return;
        }

        const menu = getElement('ocMenuPopover');
        const searchPanel = getElement('ocSearchPanel');

        if (menu) {
            menu.classList.remove('open');
        }

        if (searchPanel) {
            searchPanel.classList.remove('open');
        }

        channel.classList.add('open');
        channel.setAttribute('aria-hidden', 'false');

        document.body.style.overflow = 'hidden';

        const channelBody = channel.querySelector(
            '.oc-channel-body'
        );

        if (channelBody) {
            requestAnimationFrame(function () {
                channelBody.scrollTop =
                    channelBody.scrollHeight;
            });
        }
    };

    /* =========================================
       CLOSE OC CHANNEL / BACK BUTTON
       ========================================= */
    window.closeOCChannel = function () {
        const channel = getElement('ocChannelWindow');

        if (!channel) {
            return;
        }

        channel.classList.remove('open');
        channel.setAttribute('aria-hidden', 'true');

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
            filterOCPosts('');
        }

        document.body.style.overflow = '';
    };

    /* =========================================
       THREE-DOT MENU
       ========================================= */
    window.toggleOCMenu = function () {
        const menu = getElement('ocMenuPopover');

        if (!menu) {
            return;
        }

        menu.classList.toggle('open');
    };

    /* =========================================
       CHANNEL SEARCH PANEL
       ========================================= */
    window.toggleOCSearch = function () {
        const panel = getElement('ocSearchPanel');

        if (!panel) {
            return;
        }

        const willOpen = !panel.classList.contains('open');

        panel.classList.toggle('open', willOpen);

        const input = getElement('ocSearchInput');

        if (!input) {
            return;
        }

        if (willOpen) {
            setTimeout(function () {
                input.focus();
            }, 0);
        } else {
            input.value = '';
            filterOCPosts('');
        }
    };

    /* =========================================
       SEARCH CHANNEL POSTS
       ========================================= */
    function filterOCPosts(query) {
        const container = getElement(
            'channelPostsContainer'
        );

        if (!container) {
            return;
        }

        const searchText = String(query || '')
            .trim()
            .toLowerCase();

        const posts = container.querySelectorAll(
            '.oc-post-bubble'
        );

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

    const searchInput = getElement('ocSearchInput');

    if (searchInput) {
        searchInput.addEventListener('input', function () {
            filterOCPosts(this.value);
        });
    }

    /* =========================================
       UNMUTE / MUTE CHANNEL MEDIA
       ========================================= */
    window.toggleOCMute = function (button) {
        const channel = getElement('ocChannelWindow');

        if (!channel || !button) {
            return;
        }

        const media = channel.querySelectorAll(
            'video, audio'
        );

        if (!media.length) {
            return;
        }

        // Initially the media is assumed to be muted.
        const shouldUnmute =
            button.dataset.muted !== 'false';

        media.forEach(function (item) {
            item.muted = !shouldUnmute;

            if (
                shouldUnmute &&
                item.tagName === 'VIDEO'
            ) {
                const playResult = item.play();

                if (
                    playResult &&
                    typeof playResult.catch === 'function'
                ) {
                    playResult.catch(function () {
                        // Playback may require another user action.
                    });
                }
            }
        });

        button.dataset.muted = shouldUnmute
            ? 'false'
            : 'true';

        button.textContent = shouldUnmute
            ? 'Mute'
            : 'Unmute';
    };

})();
