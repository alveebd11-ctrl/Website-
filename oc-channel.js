/* ============================================================
   ALVEE PREMIUM MEDIA VIEWER — OC-CHANNEL.JS
   ============================================================ */
(function () {
    'use strict';

    let currentMediaList = [];
    let currentIndex = 0;

    // Fetch media from backend API matching old implementation structure
    window.openOCChannel = async function () {
        const popup = document.getElementById('ocVideoPopup');
        if (!popup) return;

        // Ensure popup structure exists or build it dynamically if missing
        if (!popup.querySelector('.oc-viewer-container')) {
            popup.innerHTML = `
                <div class="oc-viewer-container">
                    <div class="oc-viewer-header">
                        <button type="button" class="oc-brand-btn">𝆺𝅥⃝🖤༏ـ꯭ـ꯭🇦𝙇𝙑𝙀𝙀𝆺𝅥♥️⟵꯭</button>
                        <button type="button" class="oc-subscribe-btn" id="ocSubscribeBtn" onclick="toggleSubscribe(this)">Subscribe</button>
                        <button type="button" class="oc-trending-btn" id="ocTrendingBtn" onclick="triggerTrending(this)">Trending</button>
                    </div>

                    <div class="oc-media-box">
                        <button type="button" class="oc-tribute-badge" onclick="showTributeToast()">
                            <span>❤️</span> A Tribute
                        </button>
                        <div id="ocToast" class="oc-toast-notification">This video is dedicated to you.</div>
                        <div class="oc-popup-video-wrapper"></div>
                    </div>

                    <div class="oc-stats-bar">
                        <div class="oc-stat-item views-item">
                            <svg viewBox="0 0 24 24"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path><circle cx="12" cy="12" r="3"></circle></svg>
                            <span id="statViews">3K</span>
                        </div>
                        <button type="button" class="oc-stat-item" id="statLikeBtn" onclick="toggleLike(this)">
                            <svg viewBox="0 0 24 24"><path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"></path></svg>
                            <span id="statLikesCount">0</span>
                        </button>
                        <button type="button" class="oc-stat-item" onclick="showCommentNotice()">
                            <svg viewBox="0 0 24 24"><path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"></path></svg>
                            <span>0</span>
                        </button>
                        <button type="button" class="oc-stat-item" id="statBookmarkBtn" onclick="toggleBookmark(this)">
                            <svg viewBox="0 0 24 24"><path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"></path></svg>
                            <span>Save</span>
                        </button>
                    </div>

                    <a href="https://t.me/ALVEE_BD1" target="_blank" rel="noopener noreferrer" class="oc-telegram-join-btn">
                        <svg viewBox="0 0 24 24"><path d="M21.5 2.5L2 10.5l6.5 2.5L18 6l-8 9.5 7 5.5 3.5-16.5z"/></svg>
                        Join Telegram
                    </a>
                </div>
            `;
        }

        popup.classList.add('open');
        popup.setAttribute('aria-hidden', 'false');
        document.body.style.overflow = 'hidden';

        try {
            const response = await fetch('/api/channel/posts');
            const data = await response.json();
            if (data.ok && Array.isArray(data.posts) && data.posts.length > 0) {
                currentMediaList = data.posts;
                currentIndex = 0;
                renderMediaItem(currentMediaList[currentIndex]);
            }
        } catch (err) {
            console.error('Error fetching media feed:', err);
        }
    };

    function renderMediaItem(post) {
        const wrapper = document.querySelector('.oc-popup-video-wrapper');
        if (!wrapper) return;
        wrapper.replaceChildren();

        const postId = post.message_id ?? post.id;
        const mediaURL = `/api/chat/media/${postId}`;
        const isVideo = String(post.media_type || '').toLowerCase() === 'video' || String(post.mime_type || '').startsWith('video/');

        if (isVideo) {
            const video = document.createElement('video');
            video.src = mediaURL;
            video.controls = true;
            video.autoplay = true;
            video.playsInline = true;
            wrapper.appendChild(video);
        } else {
            const img = document.createElement('img');
            img.src = mediaURL;
            img.alt = 'ALVEE Media Item';
            wrapper.appendChild(img);
        }
    }

    window.closeOCChannel = function () {
        const popup = document.getElementById('ocVideoPopup');
        if (!popup) return;

        const wrapper = popup.querySelector('.oc-popup-video-wrapper');
        if (wrapper) {
            const video = wrapper.querySelector('video');
            if (video) { video.pause(); video.removeAttribute('src'); }
            wrapper.replaceChildren();
        }

        popup.classList.remove('open');
        popup.setAttribute('aria-hidden', 'true');
        document.body.style.overflow = '';
    };

    // UI Interactive Methods
    window.toggleSubscribe = function (btn) {
        btn.classList.toggle('subscribed');
        btn.textContent = btn.classList.contains('subscribed') ? 'Subscribed' : 'Subscribe';
    };

    window.triggerTrending = function (btn) {
        btn.textContent = 'Ok Done';
        btn.classList.add('done');
        setTimeout(() => {
            btn.textContent = 'Trending';
        }, 1500);
    };

    window.showTributeToast = function () {
        const toast = document.getElementById('ocToast');
        if (toast) {
            toast.classList.add('show');
            setTimeout(() => { toast.classList.remove('show'); }, 2000);
        }
    };

    window.toggleLike = function (btn) {
        btn.classList.toggle('liked');
        const countSpan = document.getElementById('statLikesCount');
        let count = parseInt(countSpan.textContent) || 0;
        countSpan.textContent = btn.classList.contains('liked') ? count + 1 : count - 1;
    };

    window.toggleBookmark = function (btn) {
        btn.classList.toggle('bookmarked');
    };

    window.showCommentNotice = function () {
        alert("Comments are synced with live channel updates.");
    };

    // Close on background click or ESC key
    document.addEventListener('click', (e) => {
        const popup = document.getElementById('ocVideoPopup');
        if (e.target === popup) {
            window.closeOCChannel();
        }
    });

    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            window.closeOCChannel();
        }
    });

})();
