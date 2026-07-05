'use strict';
// ── Bunbooru content coordinator ──────────────────────────────────────────────
// Site modules live in sites/*.js and register in window.BUNBOORU_SITES.

const BUNNY_SVG = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 67.34 50.08"><path fill="#ea005e" d="M15.43,0c-3.2-.04-5.73,1.79-5.63,5.25.08,2.58.95,3.57,7.02,7.94,2.24,1.61,4.54,3.51,5.12,4.21q1.05,1.28-3.98-.4c-7.51-2.5-12.44-2.97-15.01-1.41C.12,17.31-.92,20.91.92,22.63c1.88,1.76,4.6,2.06,13.27,1.47q4.14-.28,3.48.65c-2.38,3.34-3.35,10.33-1.86,13.27,1.6,3.15,3.73,4.79,6.23,4.79q1.29,0,1.29,1.93c0,3.43,1.21,5.11,3.7,5.11,2.67,0,4.37-4.44,2.84-7.4-.42-.81-.43-1.06-.08-1.54.23-.32,1.04-1.46,1.8-2.55,1.39-1.99,2.26-2.33,3.02-1.18.58.88.24,4.7-.59,6.68-1.3,3.1-.71,5.3,1.61,5.98,3.72,1.08,6.35-.98,9.59-7.53,2.14-4.32,4.22-7.49,4.92-7.49.39,0,.45.47.32,2.8-.19,3.5.11,4.4,1.7,5.21,2.17,1.11,3.57.57,6.43-2.45,2.96-3.13,5.55-10.19,5.88-16.03.11-2.01.28-2.53,1.25-3.94,2.75-3.99,1.91-8.81-1.91-10.95-1.91-1.07-5.52-1.02-7.45.11-.73.43-1.23.53-1.6.34-.3-.16-1.65-.47-3-.7-5.71-.96-12.84,2.23-17.22,7.7-.97,1.21-1.87,2.21-2,2.23-.13.01-.75-1.02-1.39-2.3-2.75-5.53-6.44-11.65-7.98-13.21C20.81,1.19,17.92.03,15.44,0h-.01ZM28.1,29.41c1.25.04,1.42,3.08.15,4.48-.65.72-1.99.79-2.25.1-.51-1.34.74-4.34,1.91-4.57.07-.01.13-.02.19-.02h0Z"/></svg>`;

const site = (window.BUNBOORU_SITES || []).find(s => s.matches(location.hostname));
if (!site) {
  // no module for this hostname — do nothing
} else {
  init(site);
}

// ── Canvas capture ────────────────────────────────────────────────────────────

function captureCanvas(imgEl) {
  if (!imgEl || !imgEl.complete || imgEl.naturalWidth === 0) return null;
  try {
    const canvas = document.createElement('canvas');
    canvas.width = imgEl.naturalWidth;
    canvas.height = imgEl.naturalHeight;
    canvas.getContext('2d').drawImage(imgEl, 0, 0);
    return { blobBase64: canvas.toDataURL('image/png').split(',')[1], mimeType: 'image/png' };
  } catch(e) {
    return null;
  }
}

// ── Twitter media sender ──────────────────────────────────────────────────────
// Sends a media item (from sessionStorage) to bunbooru via background script.

async function sendTwitterMediaToBunbooru(item, tags) {
  if (item.url) {
    // image
    await browser.runtime.sendMessage({
      type: 'SEND_TO_BUNBOORU',
      mediaUrl: item.url + ':orig', // request full resolution
      tags,
      sourceSite: location.hostname,
      blobBase64: null,
      mimeType: null,
    });
  } else if (item.video) {
    // video — pick highest bitrate variant
    const variants = (item.video.variants || [])
      .filter(v => v.content_type === 'video/mp4')
      .sort((a, b) => (b.bitrate || 0) - (a.bitrate || 0));
    const best = variants[0];
    if (best) {
      await browser.runtime.sendMessage({
        type: 'SEND_TO_BUNBOORU',
        mediaUrl: best.url,
        tags,
        sourceSite: location.hostname,
        blobBase64: null,
        mimeType: null,
      });
    }
  }
}

// ── Button factories ──────────────────────────────────────────────────────────

function makeThumbnailBtn(postUrl) {
  const btn = document.createElement('div');
  btn.className = 'bunbooru-btn';
  btn.innerHTML = BUNNY_SVG;
  btn.title = 'send to bunbooru';
  btn.addEventListener('click', async e => {
    e.preventDefault();
    e.stopPropagation();
    btn.innerHTML = '<span class="bunbooru-label">...</span>';
    const result = await browser.runtime.sendMessage({
      type: 'OPEN_AND_SCRAPE',
      postUrl,
      sourceSite: location.hostname,
    });
    if (result && result.ok) {
      btn.innerHTML = '<span class="bunbooru-label">✓</span>';
      btn.classList.add('success');
    } else {
      btn.innerHTML = BUNNY_SVG;
      btn.classList.add('error');
      btn.title = result ? result.error : 'failed';
    }
  });
  return btn;
}

function makePageBtn(site) {
  const btn = document.createElement('div');
  btn.className = 'bunbooru-page-btn';
  btn.innerHTML = BUNNY_SVG + '<span>send to bunbooru</span>';
  btn.addEventListener('click', async () => {
    const span = btn.querySelector('span');
    span.textContent = 'sending...';
    const tags = site.collectTags();
    const mediaUrl = site.getMediaUrl();
    if (!mediaUrl) { span.textContent = 'no media found'; btn.classList.add('error'); return; }
    const captured = captureCanvas(site.getImageElement());
    const result = await browser.runtime.sendMessage({
      type: 'SEND_TO_BUNBOORU',
      mediaUrl, tags,
      sourceSite: location.hostname,
      blobBase64: captured ? captured.blobBase64 : null,
      mimeType: captured ? captured.mimeType : null,
    });
    if (result && (result.status === 'created' || result.status === 'duplicate')) {
      span.textContent = result.status === 'created' ? '✓ saved' : '✓ already in booru';
      btn.classList.add('success');
    } else {
      span.textContent = '✗ failed';
      btn.classList.add('error');
    }
  });
  return btn;
}

// ── Init ──────────────────────────────────────────────────────────────────────

function init(site) {
  // Twitter needs interceptors injected first
  if (site.injectInterceptors) {
    site.injectInterceptors();
  }

  if (site.isPostPage()) {
    const isScrapeTab = new URLSearchParams(location.search).get('bunbooru_scrape') === '1';

    async function tryInit() {
      // Twitter handles its own page button injection
      if (site.injectPageButton.length > 1) {
        site.injectPageButton(makePageBtn, BUNNY_SVG, sendTwitterMediaToBunbooru);
        return;
      }

      const tags = site.collectTags();
      const mediaUrl = site.getMediaUrl();
      if (tags && mediaUrl) {
        if (isScrapeTab) {
          const captured = captureCanvas(site.getImageElement());
          browser.runtime.sendMessage({
            type: 'SCRAPE_RESULT',
            tags, mediaUrl,
            blobBase64: captured ? captured.blobBase64 : null,
            mimeType: captured ? captured.mimeType : null,
          });
        } else {
          site.injectPageButton(() => makePageBtn(site));
        }
      } else {
        setTimeout(tryInit, 500);
      }
    }
    setTimeout(tryInit, 800);

  } else if (site.isThumbnailPage()) {
    // Twitter uses extended inject signature
    if (site.injectThumbnailButtons.length > 1) {
      const run = () => site.injectThumbnailButtons(makeThumbnailBtn, BUNNY_SVG, sendTwitterMediaToBunbooru);
      run();
      const observer = new MutationObserver(run);
      observer.observe(document.body, { childList: true, subtree: true });
    } else {
      site.injectThumbnailButtons(makeThumbnailBtn);
      const observer = new MutationObserver(() => site.injectThumbnailButtons(makeThumbnailBtn));
      observer.observe(document.body, { childList: true, subtree: true });
    }
  }
}
