/* ========================================
   OC COLLECTION UNIVERSE - CHANNEL MODULE
   File: oc-channel.js
   Winter Smooth Open Version
   ======================================== */

/**
 * Winter Snow Effect তৈরি
 */
function createWinterSnow() {
    // আগে থেকে থাকা স্নো সরিয়ে ফেলা
    const oldSnow = document.getElementById('oc-winter-snow');
    if (oldSnow) oldSnow.remove();

    const snowContainer = document.createElement('div');
    snowContainer.id = 'oc-winter-snow';
    snowContainer.style.cssText = `
        position: absolute;
        inset: 0;
        pointer-events: none;
        z-index: 1;
        overflow: hidden;
    `;

    // ৩৫টা স্নোফ্লেক
    for (let i = 0; i < 35; i++) {
        const flake = document.createElement('div');
        const size = Math.random() * 5 + 3;
        const left = Math.random() * 100;
        const delay = Math.random() * 5;
        const duration = Math.random() * 8 + 6;

        flake.style.cssText = `
            position: absolute;
            top: -20px;
            left: ${left}%;
            width: ${size}px;
            height: ${size}px;
            background: rgba(255, 255, 255, 0.85);
            border-radius: 50%;
            box-shadow: 0 0 6px rgba(255, 255, 255, 0.6);
            animation: ocSnowFall ${duration}s linear ${delay}s infinite;
            opacity: ${Math.random() * 0.6 + 0.4};
        `;
        snowContainer.appendChild(flake);
    }

    // অ্যানিমেশন কীফ্রেম একবারই যোগ করা
    if (!document.getElementById('oc-winter-style')) {
        const style = document.createElement('style');
        style.id = 'oc-winter-style';
        style.textContent = `
            @keyframes ocSnowFall {
                0% {
                    transform: translateY(0) translateX(0) rotate(0deg);
                    opacity: 0.8;
                }
                100% {
                    transform: translateY(110vh) translateX(30px) rotate(360deg);
                    opacity: 0.2;
                }
            }

            @keyframes ocWinterOpen {
                0% {
                    opacity: 0;
                    transform: scale(0.92) translateY(40px);
                    filter: blur(8px) brightness(1.1);
                }
                60% {
                    opacity: 1;
                    transform: scale(1.02) translateY(-8px);
                    filter: blur(0) brightness(1.05);
                }
                100% {
                    opacity: 1;
                    transform: scale(1) translateY(0);
                    filter: blur(0) brightness(1);
                }
            }

            .oc-channel-window.winter-open {
                animation: ocWinterOpen 0.75s cubic-bezier(0.22, 1, 0.36, 1) forwards;
            }
        `;
        document.head.appendChild(style);
    }

    return snowContainer;
}

/**
 * OC Channel Window খোলা (Winter Smooth)
 */
function openOCChannel() {
    const popup = document.getElementById('ocChannelWindow');
    if (!popup) return;

    // Menu এবং Search বন্ধ
    const menu = document.getElementById('ocMenuPopover');
    const search = document.getElementById('ocSearchPanel');
    if (menu) menu.classList.remove('open');
    if (search) search.classList.remove('open');

    // Winter Snow যোগ করা
    const snow = createWinterSnow();
    popup.appendChild(snow);

    // Smooth Winter Open
    popup.classList.add('open', 'winter-open');
    popup.setAttribute('aria-hidden', 'false');

    // অ্যানিমেশন শেষ হলে ক্লাস পরিষ্কার
    setTimeout(() => {
        popup.classList.remove('winter-open');
    }, 800);

    // পোস্ট ফেচ ব্যাকগ্রাউন্ডে (UI ব্লক করবে না)
    fetchChannelPosts();
}

/**
 * OC Channel Window বন্ধ করা
 */
function closeOCChannel() {
    const popup = document.getElementById('ocChannelWindow');
    if (!popup) return;

    // স্নো সরিয়ে ফেলা
    const snow = document.getElementById('oc-winter-snow');
    if (snow) snow.remove();

    popup.classList.remove('open', 'winter-open');
    popup.setAttribute('aria-hidden', 'true');

    const menu = document.getElementById('ocMenuPopover');
    const search = document.getElementById('ocSearchPanel');
    if (menu) menu.classList.remove('open');
    if (search) search.classList.remove('open');
}

/**
 * সার্ভার থেকে চ্যানেল পোস্ট ফেচ (নন-ব্লকিং)
 */
function fetchChannelPosts() {
    fetch('/api/channel/posts')
        .then(res => res.json())
        .then(data => {
            if (data.ok && Array.isArray(data.posts)) {
                renderChannelPosts(data.posts);
            }
        })
        .catch(err => {
            console.error("Error fetching channel posts:", err);
        });
}

/**
 * পোস্ট রেন্ডার
 */
function renderChannelPosts(posts) {
    const container = document.getElementById('channelPostsContainer');
    if (!container) return;

    container.innerHTML = '';

    posts.forEach(post => {
        const bubble = document.createElement('div');
        bubble.className = 'oc-post-bubble';

        if (post.text) {
            const textEl = document.createElement('div');
            textEl.textContent = post.text;
            bubble.appendChild(textEl);
        }

        if (post.media_type === 'photo') {
            const img = document.createElement('img');
            img.src = `/api/chat/media/${post.id}`;
            img.alt = 'Channel photo';
            img.loading = 'lazy';
            bubble.appendChild(img);
        } else if (post.media_type === 'video') {
            const video = document.createElement('video');
            video.src = `/api/chat/media/${post.id}`;
            video.controls = true;
            video.preload = 'metadata';
            bubble.appendChild(video);
        }

        container.appendChild(bubble);
    });
}

/**
 * Three-dot মেনু টগল
 */
function toggleOCMenu() {
    const menu = document.getElementById('ocMenuPopover');
    const search = document.getElementById('ocSearchPanel');
    if (!menu) return;

    if (search) search.classList.remove('open');
    menu.classList.toggle('open');
}

/**
 * সার্চ প্যানেল টগল
 */
function toggleOCSearch() {
    const panel = document.getElementById('ocSearchPanel');
    const menu = document.getElementById('ocMenuPopover');
    if (!panel) return;

    if (menu) menu.classList.remove('open');
    panel.classList.toggle('open');

    if (panel.classList.contains('open')) {
        const input = document.getElementById('ocSearchInput');
        if (input) input.focus();
    }
}

/**
 * Mute / Unmute বাটন
 */
function toggleOCMute(button) {
    if (!button) return;
    const isUnmute = button.textContent.trim() === 'Unmute';
    button.textContent = isUnmute ? 'Mute' : 'Unmute';
}

/* Escape দিয়ে বন্ধ */
document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape') {
        closeOCChannel();
    }
});
