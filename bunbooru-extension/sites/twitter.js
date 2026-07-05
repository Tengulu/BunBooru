'use strict';
// ── twitter.com / x.com module ────────────────────────────────────────────────
// Mirrors twitter_video_assist_client.js but sends to bunbooru instead of downloading.
// inject.js intercepts Twitter API calls and sends media data to background,
// which forwards it here via UPDATE_SESSION_DATA, stored in sessionStorage.

// Listen for session data from background (relayed from inject.js in page context)
browser.runtime.onMessage.addListener((message) => {
  if (message.type === 'UPDATE_SESSION_DATA') {
    const current = JSON.parse(sessionStorage.getItem('rectifying@gmail.com') || '[]');
    const merged = [...current, ...message.data];
    sessionStorage.setItem('rectifying@gmail.com', JSON.stringify(merged));
  }
});

// Clear session data on page show (same as working extension)
window.addEventListener('pageshow', () => {
  sessionStorage.removeItem('rectifying@gmail.com');
});

window.BUNBOORU_SITES = window.BUNBOORU_SITES || [];
window.BUNBOORU_SITES.push({
  name: 'twitter/x',

  matches(hostname) {
    return hostname === 'twitter.com' || hostname === 'x.com';
  },

  isPostPage() { return false; },
  isThumbnailPage() { return true; },

  collectTags() { return null; },
  getMediaUrl() { return null; },
  getImageElement() { return null; },
  injectInterceptors() {},

  extractMainTweetId(article) {
    const links = article.querySelectorAll('a[href*="/status/"]');
    for (const link of links) {
      if (link.querySelector('time')) {
        const m = link.href.match(/status\/(\d+)/);
        if (m) return m[1];
      }
    }
    return null;
  },

  getUsername(article) {
    const links = article.querySelectorAll('a[href*="/status/"]');
    for (const link of links) {
      if (link.querySelector('time')) {
        const m = link.href.match(/\/([^/]+)\/status\//);
        if (m) return m[1];
      }
    }
    return null;
  },

  hasMedia(article) {
    return article.innerHTML.includes('pbs.twimg.com/media') || !!article.querySelector('video');
  },

  injectButton(article, BUNNY_SVG) {
    if (article.querySelector('.bunbooru-twitter-btn')) return;
    if (!this.hasMedia(article)) return;

    const iconsGroups = article.querySelectorAll('div[role="group"]');
    const iconsGroup = iconsGroups[iconsGroups.length - 1];
    if (!iconsGroup) return;

    const lastIcon = iconsGroup.lastElementChild;
    if (!lastIcon) return;

    // clone the last icon's structure exactly like the working extension does
    const btn = lastIcon.cloneNode(true);
    btn.className = (btn.className || '') + ' bunbooru-twitter-btn';
    // replace the svg inside with our bunny
    const svgEl = btn.querySelector('svg');
    if (svgEl) {
      const wrapper = document.createElement('div');
      wrapper.className = 'bunbooru-btn-icon';
      wrapper.style.cssText = 'width:18px;height:18px;display:flex;align-items:center;';
      wrapper.innerHTML = BUNNY_SVG;
      svgEl.parentNode.replaceChild(wrapper, svgEl);
    }

    // remove any existing click handlers by replacing with clone
    const cleanBtn = btn.cloneNode(true);

    cleanBtn.addEventListener('click', async (e) => {
      e.preventDefault();
      e.stopPropagation();

      const bunnyWrap = cleanBtn.querySelector('.bunbooru-btn-icon');

      // show sending state
      if (bunnyWrap) bunnyWrap.innerHTML = '<span style="font-size:11px;font-family:monospace;color:#ea005e">...</span>';

      const tweetId = this.extractMainTweetId(article);
      const username = this.getUsername(article);
      const tags = username ? `cr:${username} m:real m:twitter_post` : 'm:real m:twitter_post';

      if (!tweetId) {
        if (bunnyWrap) bunnyWrap.innerHTML = '<span style="font-size:11px;font-family:monospace;color:#ea005e">✗</span>';
        return;
      }

      const sessionData = JSON.parse(sessionStorage.getItem('rectifying@gmail.com') || '[]');
      const media = sessionData.filter(m => m.tweetId === tweetId || m.referencedBy === tweetId);

      if (!media.length) {
        console.log('[bunbooru-twitter] no media in sessionStorage for tweet', tweetId);
        if (bunnyWrap) bunnyWrap.innerHTML = '<span style="font-size:11px;font-family:monospace;color:#ea005e">✗</span>';
        return;
      }

      let ok = 0;
      for (const item of media) {
        let url = item.url || (item.type === 'video' ? item.videoSource : null);
        if (!url) continue;
        try {
          const result = await browser.runtime.sendMessage({
            type: 'SEND_TO_BUNBOORU',
            mediaUrl: url,
            tags,
            sourceSite: location.hostname,
            blobBase64: null,
            mimeType: null,
          });
          if (result?.status === 'created' || result?.status === 'duplicate') ok++;
        } catch(e) {
          console.error('[bunbooru-twitter] send error:', e);
        }
      }

      console.log('[bunbooru-twitter] sent', ok, 'of', media.length, 'items');

      if (bunnyWrap) {
        if (ok > 0) {
          bunnyWrap.innerHTML = '<span style="font-size:11px;font-family:monospace;color:#4ec97a">✓</span>';
        } else {
          bunnyWrap.innerHTML = '<span style="font-size:11px;font-family:monospace;color:#ea005e">✗</span>';
        }
      }
    });

    lastIcon.after(cleanBtn);
  },

  injectThumbnailButtons(makeThumbnailBtn, BUNNY_SVG) {
    document.querySelectorAll('article').forEach(a => this.injectButton(a, BUNNY_SVG));
  },

  injectPageButton(makePageBtn, BUNNY_SVG) {
    document.querySelectorAll('article').forEach(a => this.injectButton(a, BUNNY_SVG));
  },
});
