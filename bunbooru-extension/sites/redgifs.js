'use strict';
// ── redgifs.com module ─────────────────────────────────────────────────────────

// ── Token management ──────────────────────────────────────────────────────────

const TOKEN_KEY = 'bunbooru_redgifs_token';
const TOKEN_EXPIRY_KEY = 'bunbooru_redgifs_token_expiry';

function getStorageItem(key) {
  return new Promise(resolve => browser.storage.local.get([key], r => resolve(r[key])));
}
function setStorageItem(key, value) {
  return new Promise(resolve => browser.storage.local.set({ [key]: value }, resolve));
}

async function fetchNewToken() {
  const response = await fetch('https://api.redgifs.com/v2/auth/temporary', {
    headers: {
      'accept': 'application/json, text/plain, */*',
      'accept-language': 'en-GB,en-US;q=0.9,en;q=0.8',
      'cache-control': 'no-cache',
      'dnt': '1',
      'origin': 'https://www.redgifs.com',
      'pragma': 'no-cache',
      'referer': 'https://www.redgifs.com/',
      'user-agent': navigator.userAgent,
    }
  });
  const data = await response.json();
  await setStorageItem(TOKEN_KEY, data.token);
  await setStorageItem(TOKEN_EXPIRY_KEY, Date.now() + 23 * 3600 * 1000);
  return data.token;
}

async function getToken() {
  const token = await getStorageItem(TOKEN_KEY);
  const expiry = await getStorageItem(TOKEN_EXPIRY_KEY);
  if (!token || Date.now() >= expiry) return fetchNewToken();
  return token;
}

// ── API fetch ─────────────────────────────────────────────────────────────────

async function fetchGifData(gifName) {
  let authToken = await getToken();
  const url = `https://api.redgifs.com/v2/gifs/${encodeURIComponent(gifName)}`;

  const makeHeaders = token => ({
    'accept': 'application/json, text/plain, */*',
    'accept-language': 'en-GB,en-US;q=0.9,en;q=0.8',
    'authorization': `Bearer ${token}`,
    'cache-control': 'no-cache',
    'dnt': '1',
    'origin': 'https://www.redgifs.com',
    'pragma': 'no-cache',
    'referer': 'https://www.redgifs.com/',
    'user-agent': navigator.userAgent,
  });

  let response = await fetch(url, { headers: makeHeaders(authToken) });
  if (response.status === 401) {
    authToken = await fetchNewToken();
    response = await fetch(url, { headers: makeHeaders(authToken) });
  }
  if (!response.ok) throw new Error(`API error: ${response.status}`);

  const data = await response.json();
  const gif = data.gif;
  const mediaUrl = gif.urls.hd || gif.urls.sd || gif.urls.gif;

  const tags = [];
  if (gif.userName) tags.push(`cr:${gif.userName.toLowerCase()}`);
  tags.push('m:real', 'm:redgifs_post');
  if (gif.tags && gif.tags.length) {
    for (const t of gif.tags) {
      const clean = t.toLowerCase().replace(/\s+/g, '_');
      if (clean) tags.push(clean);
    }
  }

  return { mediaUrl, tags: tags.join(' ') };
}

// ── Extract gif name ──────────────────────────────────────────────────────────

function gifNameFromUrl(url) {
  const m = (url || '').match(/\/watch\/([^/?#]+)/i);
  return m ? m[1] : null;
}

function gifNameFromEl(el) {
  // new: data-feed-item-id on the GifPreview ancestor
  const preview = el.closest('[data-feed-item-id]') || el.querySelector('[data-feed-item-id]');
  if (preview) return preview.getAttribute('data-feed-item-id');
  // old: UserInfo-Date link
  const dateLink = el.querySelector('.UserInfo-Text .UserInfo-Date');
  if (dateLink) return gifNameFromUrl(dateLink.href);
  // watch page
  return gifNameFromUrl(location.href);
}

function gifNameFromMeta() {
  // og:video:iframe contains the watch URL on watch pages
  const meta = document.querySelector('meta[property="og:video:iframe"]');
  if (meta) return gifNameFromUrl(meta.content);
  return gifNameFromUrl(location.href);
}

// ── Button creation ───────────────────────────────────────────────────────────

function makeBtn(BUNNY_SVG) {
  const btn = document.createElement('button');
  btn.className = 'bunbooru-redgifs-btn rg-button icon';
  btn.setAttribute('aria-label', 'Send to Bunbooru');
  const wrap = document.createElement('div');
  wrap.className = 'bunbooru-btn-icon';
  wrap.style.cssText = 'width:24px;height:24px;display:flex;align-items:center;justify-content:center;';
  wrap.innerHTML = BUNNY_SVG;
  btn.appendChild(wrap);
  return btn;
}

async function handleClick(btn, gifName) {
  const wrap = btn.querySelector('.bunbooru-btn-icon');
  if (wrap) wrap.innerHTML = '<span style="font-size:11px;font-family:monospace;color:#ea005e">...</span>';
  btn.disabled = true;
  try {
    const { mediaUrl, tags } = await fetchGifData(gifName);
    if (!mediaUrl) throw new Error('no media url');
    const result = await browser.runtime.sendMessage({
      type: 'SEND_TO_BUNBOORU',
      mediaUrl, tags,
      sourceSite: 'www.redgifs.com',
      blobBase64: null,
      mimeType: null,
    });
    const ok = result?.status === 'created' || result?.status === 'duplicate';
    if (wrap) wrap.innerHTML = ok
      ? '<span style="font-size:11px;font-family:monospace;color:#4ec97a">✓</span>'
      : '<span style="font-size:11px;font-family:monospace;color:#ea005e">✗</span>';
  } catch(e) {
    console.error('[bunbooru-redgifs] error:', e);
    if (wrap) wrap.innerHTML = '<span style="font-size:11px;font-family:monospace;color:#ea005e">✗</span>';
  } finally {
    btn.disabled = false;
  }
}

// ── Inject into a sidebar container ──────────────────────────────────────────

function injectIntoContainer(container, BUNNY_SVG) {
  if (container.querySelector('.bunbooru-redgifs-btn')) return;

  const gifName = gifNameFromEl(container);
  if (!gifName) return;

  const btn = makeBtn(BUNNY_SVG);
  btn.addEventListener('click', e => { e.preventDefault(); e.stopPropagation(); handleClick(btn, gifName); });

  // try new class names first, then old ones
  const sideBar = container.querySelector('.sideBar') || container.querySelector('.SideBar');
  if (sideBar) {
    const items = sideBar.querySelectorAll('.sideBarItem, .SideBar-Item');
    const li = document.createElement('li');
    li.className = items.length ? items[0].className : 'sideBarItem';
    li.appendChild(btn);
    if (items.length > 1) {
      sideBar.insertBefore(li, items[items.length - 1]);
    } else {
      sideBar.appendChild(li);
    }
  } else {
    // fallback: just append the button directly
    container.appendChild(btn);
  }
}

// ── Module ────────────────────────────────────────────────────────────────────

window.BUNBOORU_SITES = window.BUNBOORU_SITES || [];
window.BUNBOORU_SITES.push({
  name: 'redgifs',

  matches(hostname) {
    return hostname === 'www.redgifs.com' || hostname === 'redgifs.com';
  },

  isPostPage() { return location.pathname.startsWith('/watch/'); },
  isThumbnailPage() { return !this.isPostPage(); },

  collectTags() { return null; },
  getMediaUrl() { return null; },
  getImageElement() { return null; },
  injectInterceptors() {},

  _injectAll(BUNNY_SVG) {
    // try both old and new container class names
    document.querySelectorAll('.GifPreview-InfoAndSidebar, .GifPreview-SideBarWrap').forEach(el => {
      injectIntoContainer(el, BUNNY_SVG);
    });
  },

  _injectWatchButton(BUNNY_SVG) {
    if (document.querySelector('.bunbooru-redgifs-watch-btn')) return;
    const gifName = gifNameFromMeta();
    if (!gifName) return;

    // try sidebar first
    const container = document.querySelector('.GifPreview-InfoAndSidebar, .GifPreview-SideBarWrap');
    if (container) {
      injectIntoContainer(container, BUNNY_SVG);
      return;
    }

    // floating button fallback
    const btn = makeBtn(BUNNY_SVG);
    btn.classList.add('bunbooru-redgifs-watch-btn');
    btn.style.cssText = 'position:fixed;bottom:20px;right:20px;z-index:9999;background:#222;border:none;border-radius:50%;width:44px;height:44px;cursor:pointer;display:flex;align-items:center;justify-content:center;';
    document.body.appendChild(btn);
    btn.addEventListener('click', e => { e.preventDefault(); e.stopPropagation(); handleClick(btn, gifName); });
  },

  _startObserver(BUNNY_SVG) {
    if (this._observer) return;
    this._observer = new MutationObserver(mutations => {
      for (const mutation of mutations) {
        for (const node of mutation.addedNodes) {
          if (node.nodeType !== 1) continue;
          if (node.matches?.('.GifPreview-InfoAndSidebar, .GifPreview-SideBarWrap')) {
            injectIntoContainer(node, BUNNY_SVG);
          }
          node.querySelectorAll?.('.GifPreview-InfoAndSidebar, .GifPreview-SideBarWrap').forEach(el => {
            injectIntoContainer(el, BUNNY_SVG);
          });
        }
      }
    });
    this._observer.observe(document.body, { childList: true, subtree: true });
  },

  injectThumbnailButtons(makeThumbnailBtn, BUNNY_SVG) {
    this._injectAll(BUNNY_SVG);
    this._startObserver(BUNNY_SVG);
  },

  injectPageButton(makePageBtn, BUNNY_SVG) {
    this._injectWatchButton(BUNNY_SVG);
    this._startObserver(BUNNY_SVG);
  },
});
