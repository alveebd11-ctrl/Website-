<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="theme-color" content="#09090B">
<title>ALVEE Premium Media Viewer</title>
<link href="https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
    /* ============================================================
       ALVEE PREMIUM MEDIA VIEWER EXACT DESIGN CSS
       ============================================================ */
    :root {
        --oc-bg: #09090B;
        --oc-panel: #171719;
        --oc-accent: #f59e0b;
        --oc-subscribe: #ef4444;
        --oc-telegram: linear-gradient(135deg, #0088cc, #00bfff);
        --oc-text: #f8fafc;
        --oc-muted: #94a3b8;
        --oc-border: rgba(255, 255, 255, 0.12);
    }
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    
    body {
        background: var(--oc-bg);
        color: var(--oc-text);
        font-family: 'Poppins', sans-serif;
        display: flex;
        flex-direction: column;
        align-items: center;
        padding: 20px 12px;
        min-height: 100vh;
        overflow-x: hidden;
    }

    #feedContainer {
        width: 100%;
        display: flex;
        flex-direction: column;
        align-items: center;
        gap: 25px; /* Space between multiple posts */
    }

    .oc-viewer-container {
        width: 100%;
        max-width: 420px;
        background: var(--oc-panel);
        border: 1px solid var(--oc-border);
        border-radius: 20px;
        padding: 14px;
        display: flex;
        flex-direction: column;
        gap: 12px;
        box-shadow: 0 20px 40px rgba(0,0,0,0.8);
    }

    .oc-viewer-header { display: flex; align-items: center; gap: 6px; width: 100%; }
    
    .oc-brand-btn {
        background: #ffffff; color: #000000; font-size: 11px; font-weight: 700;
        padding: 7px 10px; border-radius: 10px; border: none; cursor: pointer;
        text-decoration: none; white-space: nowrap; transition: transform 0.2s;
    }
    .oc-brand-btn:active { transform: scale(0.95); }

    .oc-subscribe-btn {
        background: var(--oc-subscribe); color: #ffffff; font-size: 12px;
        font-weight: 600; padding: 7px 14px; border-radius: 10px; border: none; cursor: pointer;
        transition: background 0.3s ease;
    }
    .oc-subscribe-btn.subscribed { background: #10b981; }

    .oc-trending-btn {
        background: #27272a; color: #ffffff; font-size: 12px; font-weight: 600;
        padding: 7px 12px; border-radius: 10px; border: 1px solid var(--oc-border);
        cursor: pointer; margin-left: auto; transition: all 0.3s ease;
    }
    .oc-trending-btn.done { background: rgba(245, 158, 11, 0.2); border-color: var(--oc-accent); color: var(--oc-accent); }

    .oc-media-box {
        position: relative; width: 100%; background: #000000; border: 1px solid var(--oc-border);
        border-radius: 14px; overflow: hidden; display: flex; align-items: center; justify-content: center; min-height: 320px;
    }

    .oc-tribute-badge {
        position: absolute; top: 10px; left: 10px; background: rgba(23, 23, 25, 0.9);
        border: 1px solid var(--oc-border); color: var(--oc-accent); font-size: 11px;
        font-weight: 600; padding: 5px 10px; border-radius: 8px; display: inline-flex;
        align-items: center; gap: 5px; cursor: pointer; z-index: 10;
    }

    .oc-toast-notification {
        position: absolute; top: 48px; left: 10px; background: rgba(15, 23, 42, 0.95);
        border: 1px solid var(--oc-accent); color: #ffffff; font-size: 10px;
        padding: 5px 8px; border-radius: 6px; z-index: 20; opacity: 0;
        transition: opacity 0.3s ease; pointer-events: none;
    }
    .oc-toast-notification.show { opacity: 1; }

    .oc-popup-video-wrapper { width: 100%; display: flex; align-items: center; justify-content: center; }
    .oc-popup-video-wrapper img, .oc-popup-video-wrapper video {
        width: 100% !important; height: auto !important; max-height: 65vh !important;
        object-fit: contain !important; display: block !important;
    }

    .oc-stats-bar { display: grid; grid-template-columns: repeat(4, 1fr); gap: 6px; width: 100%; }
    .oc-stat-item {
        background: rgba(255, 255, 255, 0.04); border: 1px solid var(--oc-border);
        border-radius: 10px; padding: 8px 2px; display: flex; flex-direction: column;
        align-items: center; justify-content: center; gap: 3px; color: var(--oc-muted);
        font-size: 10px; font-weight: 500; cursor: pointer; background: transparent; transition: background 0.2s;
    }
    .oc-stat-item:hover { background: rgba(255, 255, 255, 0.08); }
    .oc-stat-item svg { width: 16px; height: 16px; stroke: currentColor; fill: none; stroke-width: 2; }
    .oc-stat-item.liked svg { stroke: #ef4444; fill: #ef4444; }
    .oc-stat-item.bookmarked svg { stroke: var(--oc-accent); fill: var(--oc-accent); }
    .oc-stat-item.views-item { color: #38bdf8; }

    .oc-telegram-join-btn {
        width: 100%; background: var(--oc-telegram); color: #ffffff; font-size: 14px;
        font-weight: 700; padding: 12px; border-radius: 12px; border: none; display: flex;
        align-items: center; justify-content: center; gap: 8px; cursor: pointer;
        box-shadow: 0 4px 15px rgba(0, 136, 204, 0.4); text-decoration: none;
    }
    .oc-telegram-join-btn svg { width: 18px; height: 18px; fill: #ffffff; }
    
    .loader { margin: auto; color: var(--oc-muted); font-size: 14px; margin-top: 50px; }
</style>
</head>
<body>

    <!-- This is where all media posts will be loaded vertically -->
    <div id="feedContainer">
        <div class="loader">Loading premium media...</div>
    </div>

    <script>
        'use strict';

        // UI Interactive Methods
        window.toggleSubscribe = function(btn) {
            btn.classList.toggle('subscribed');
            btn.textContent = btn.classList.contains('subscribed') ? 'Subscribed' : 'Subscribe';
        };
        
        window.triggerTrending = function(btn) {
            btn.textContent = 'Ok Done';
            btn.classList.add('done');
            setTimeout(() => {
                btn.textContent = 'Trending';
                btn.classList.remove('done');
            }, 1500);
        };
        
        window.showTributeToast = function(btn) {
            const toast = btn.nextElementSibling;
            if(toast) {
                toast.classList.add('show');
                setTimeout(() => { toast.classList.remove('show'); }, 2000);
            }
        };
        
        window.toggleLike = function(btn) {
            btn.classList.toggle('liked');
            const span = btn.querySelector('.likes-count');
            let count = parseInt(span.textContent) || 0;
            span.textContent = btn.classList.contains('liked') ? count + 1 : count - 1;
        };

        window.toggleBookmark = function(btn) {
            btn.classList.toggle('bookmarked');
        };

        window.showCommentNotice = function() {
            alert("Comments are synced with live channel updates.");
        };

        // Fetch and Render Media
        async function loadMediaFeed() {
            try {
                const response = await fetch('/api/channel/posts');
                const data = await response.json();
                
                const container = document.getElementById('feedContainer');
                container.innerHTML = ''; // Remove loader

                if (data.ok && Array.isArray(data.posts) && data.posts.length > 0) {
                    data.posts.forEach(post => {
                        const postId = post.message_id ?? post.id;
                        const mediaURL = `/api/chat/media/${postId}`;
                        const isVideo = String(post.media_type || '').toLowerCase() === 'video' || String(post.mime_type || '').startsWith('video/');
                        
                        let mediaContent = '';
                        if (isVideo) {
                            mediaContent = `<video src="${mediaURL}" controls playsinline preload="metadata"></video>`;
                        } else {
                            mediaContent = `<img src="${mediaURL}" alt="ALVEE Media">`;
                        }

                        const cardHTML = `
                        <div class="oc-viewer-container">
                            <div class="oc-viewer-header">
                                <!-- The Brand button acts as a Back Button to Dashboard -->
                                <a href="/dashboard.html" class="oc-brand-btn" title="Back to Dashboard">𝆺𝅥⃝🖤༏ـ꯭ـ꯭🇦𝙇𝙑𝙀𝙀𝆺𝅥♥️⟵꯭</a>
                                <button type="button" class="oc-subscribe-btn" onclick="toggleSubscribe(this)">Subscribe</button>
                                <button type="button" class="oc-trending-btn" onclick="triggerTrending(this)">Trending</button>
                            </div>

                            <div class="oc-media-box">
                                <button type="button" class="oc-tribute-badge" onclick="showTributeToast(this)">
                                    <span>❤️</span> A Tribute
                                </button>
                                <div class="oc-toast-notification">This video is dedicated to you.</div>
                                <div class="oc-popup-video-wrapper">
                                    ${mediaContent}
                                </div>
                            </div>

                            <div class="oc-stats-bar">
                                <div class="oc-stat-item views-item">
                                    <svg viewBox="0 0 24 24"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path><circle cx="12" cy="12" r="3"></circle></svg>
                                    <span>3K</span>
                                </div>
                                <button type="button" class="oc-stat-item" onclick="toggleLike(this)">
                                    <svg viewBox="0 0 24 24"><path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"></path></svg>
                                    <span class="likes-count">0</span>
                                </button>
                                <button type="button" class="oc-stat-item" onclick="showCommentNotice()">
                                    <svg viewBox="0 0 24 24"><path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"></path></svg>
                                    <span>0</span>
                                </button>
                                <button type="button" class="oc-stat-item" onclick="toggleBookmark(this)">
                                    <svg viewBox="0 0 24 24"><path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"></path></svg>
                                    <span>Save</span>
                                </button>
                            </div>

                            <a href="https://t.me/ALVEE_BD1" target="_blank" rel="noopener noreferrer" class="oc-telegram-join-btn">
                                <svg viewBox="0 0 24 24"><path d="M21.5 2.5L2 10.5l6.5 2.5L18 6l-8 9.5 7 5.5 3.5-16.5z"/></svg>
                                Join Telegram
                            </a>
                        </div>`;
                        
                        container.insertAdjacentHTML('beforeend', cardHTML);
                    });
                } else {
                    container.innerHTML = '<div class="loader">No media available at the moment.</div>';
                }
            } catch (err) {
                console.error('Error fetching media feed:', err);
                document.getElementById('feedContainer').innerHTML = '<div class="loader">Failed to load media.</div>';
            }
        }

        // Initialize Feed
        document.addEventListener('DOMContentLoaded', loadMediaFeed);
    </script>
</body>
</html>
