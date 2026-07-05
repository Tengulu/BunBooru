'use strict';

const pendingRequests = new Map();

// ── Intercept wimg.rule34.xxx requests and fix headers ────────────────────────
browser.webRequest.onBeforeSendHeaders.addListener(
  (details) => {
    const skip = new Set(['origin', 'sec-fetch-mode', 'sec-fetch-site', 'sec-fetch-dest', 'accept']);
    const headers = details.requestHeaders.filter(h => !skip.has(h.name.toLowerCase()));
    const refererIdx = headers.findIndex(h => h.name.toLowerCase() === 'referer');
    if (refererIdx >= 0) {
      headers[refererIdx].value = 'https://rule34.xxx/';
    } else {
      headers.push({ name: 'Referer', value: 'https://rule34.xxx/' });
    }
    headers.push({ name: 'Accept', value: 'image/avif,image/webp,image/png,image/svg+xml,image/*;q=0.8,*/*;q=0.5' });
    headers.push({ name: 'Sec-Fetch-Dest', value: 'image' });
    headers.push({ name: 'Sec-Fetch-Mode', value: 'no-cors' });
    headers.push({ name: 'Sec-Fetch-Site', value: 'same-site' });
    return { requestHeaders: headers };
  },
  { urls: ['*://wimg.rule34.xxx/*'] },
  ['blocking', 'requestHeaders']
);

// ── Message handler ───────────────────────────────────────────────────────────

browser.runtime.onMessage.addListener((msg, sender) => {
  // relay inject.js session data to the content script on the same tab
  if (msg.type === 'UPDATE_SESSION_DATA') {
    if (sender.tab?.id) {
      browser.tabs.sendMessage(sender.tab.id, {
        type: 'UPDATE_SESSION_DATA',
        data: msg.data
      }).catch(() => {});
    }
    return;
  }


  if (msg.type === 'SCRAPE_RESULT') {
    const pending = pendingRequests.get(sender.tab.id);
    if (pending) {
      console.log('[bunbooru] SCRAPE_RESULT from tab:', sender.tab.id, 'hasBlob:', !!msg.blobBase64);
      pendingRequests.delete(sender.tab.id);
      pending.resolve(msg);
      browser.tabs.remove(sender.tab.id);
    }
    return;
  }

  if (msg.type === 'SCRAPE_ERROR') {
    const pending = pendingRequests.get(sender.tab.id);
    if (pending) {
      pendingRequests.delete(sender.tab.id);
      pending.reject(new Error(msg.error));
    }
    browser.tabs.remove(sender.tab.id);
    return;
  }

  if (msg.type === 'SEND_TO_BUNBOORU') {
    console.log('[bunbooru] SEND_TO_BUNBOORU direct:', msg.mediaUrl);
    return sendToBunbooru(msg.mediaUrl, msg.tags, msg.sourceSite, msg.blobBase64, msg.mimeType);
  }

  if (msg.type === 'OPEN_AND_SCRAPE') {
    console.log('[bunbooru] OPEN_AND_SCRAPE:', msg.postUrl);
    return openAndScrape(msg.postUrl, msg.sourceSite);
  }
});

// ── Core functions ────────────────────────────────────────────────────────────

async function openAndScrape(postUrl, sourceSite) {
  try {
    const scrapeUrl = postUrl + (postUrl.includes('?') ? '&' : '?') + 'bunbooru_scrape=1';
    const tab = await browser.tabs.create({ url: scrapeUrl, active: false });
    console.log('[bunbooru] opened background tab:', tab.id);
    const result = await new Promise((resolve, reject) => {
      pendingRequests.set(tab.id, { resolve, reject });
      setTimeout(() => {
        if (pendingRequests.has(tab.id)) {
          pendingRequests.delete(tab.id);
          browser.tabs.remove(tab.id).catch(() => {});
          reject(new Error('timeout'));
        }
      }, 15000);
    });
    await sendToBunbooru(result.mediaUrl, result.tags, sourceSite, result.blobBase64, result.mimeType);
    return { ok: true };
  } catch (e) {
    console.error('[bunbooru] openAndScrape failed:', e.message);
    return { ok: false, error: e.message };
  }
}

const MIME_TO_EXT = {
  'image/jpeg': '.jpg', 'image/png': '.png', 'image/gif': '.gif',
  'image/webp': '.webp', 'video/mp4': '.mp4', 'video/webm': '.webm',
  'video/x-matroska': '.mkv', 'video/quicktime': '.mov',
};

async function sendToBunbooru(mediaUrl, tags, sourceSite, blobBase64, mimeType) {
  let blob;

  if (blobBase64) {
    // use canvas-captured blob from content script — no network needed
    console.log('[bunbooru] using canvas blob, mime:', mimeType, 'base64 length:', blobBase64.length);
    const binary = atob(blobBase64);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    blob = new Blob([bytes], { type: mimeType || 'image/png' });
    console.log('[bunbooru] blob size:', blob.size);
  } else {
    // fetch from background (works for videos and non-restricted domains)
    console.log('[bunbooru] fetching from background:', mediaUrl);
    const fileResp = await fetch(mediaUrl, {
      headers: { 'Referer': 'https://' + sourceSite + '/' }
    });
    const ct = (fileResp.headers.get('content-type') || '').split(';')[0].trim();
    if (!fileResp.ok || ct.startsWith('text/')) {
      throw new Error('fetch failed: ' + fileResp.status + ' ' + ct);
    }
    blob = await fileResp.blob();
    mimeType = ct;
    console.log('[bunbooru] fetched blob size:', blob.size);
  }

  // determine filename
  let filename = mediaUrl.split('/').pop().split('?')[0] || 'media';
  const knownExts = new Set(['.jpg','.jpeg','.png','.gif','.webp','.mp4','.webm','.mkv','.mov']);
  const currentExt = filename.includes('.') ? ('.' + filename.split('.').pop().toLowerCase()) : '';
  if (!knownExts.has(currentExt)) {
    const ext = MIME_TO_EXT[mimeType || ''] || '';
    if (ext) filename = 'media' + ext;
  }
  // canvas always gives png, so fix extension
  if (blobBase64 && mimeType === 'image/png' && !filename.endsWith('.png')) {
    filename = filename.replace(/\.[^.]+$/, '') + '.png';
  }

  // only add m:drawn if neither m:real nor m:drawn is already present
  const hasRealOrDrawn = tags && (tags.includes('m:real') || tags.includes('m:drawn'));
  const tagsWithDrawn = hasRealOrDrawn ? tags.trim() : (tags ? tags + ' m:drawn' : 'm:drawn').trim();
  console.log('[bunbooru] posting — filename:', filename, 'size:', blob.size, 'tags:', tagsWithDrawn.slice(0, 50) + '...');

  const fd = new FormData();
  fd.append('file', blob, filename);
  fd.append('tags', tagsWithDrawn);
  fd.append('source_url', mediaUrl);
  fd.append('source_site', sourceSite);
  const res = await fetch('http://localhost:8000/posts', { method: 'POST', body: fd });
  const data = await res.json();
  console.log('[bunbooru] POST response:', res.status, data);
  return data;
}
