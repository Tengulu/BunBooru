'use strict';
// ── rule34.paheal.net module ──────────────────────────────────────────────────

window.BUNBOORU_SITES = window.BUNBOORU_SITES || [];
window.BUNBOORU_SITES.push({
  name: 'rule34.paheal.net',

  matches(hostname) {
    return hostname === 'rule34.paheal.net';
  },

  isPostPage() {
    return location.pathname.includes('/post/view/');
  },

  isThumbnailPage() {
    return !!location.pathname.match(/\/post\/list\//);
  },

  collectTags() {
    const sidebar = document.querySelector('#Tagsleft');
    if (!sidebar) return null;
    const parts = [];
    sidebar.querySelectorAll('td a.tag_name').forEach(a => {
      const tag = a.textContent.trim().replace(/ /g, '_');
      if (tag) parts.push(tag);
    });
    return parts.join(' ') || '';
  },

  getMediaUrl() {
    const img = document.querySelector('#main_image');
    if (img) return img.src;
    const vid = document.querySelector('#main_video source');
    if (vid) return vid.src;
    return null;
  },

  getImageElement() {
    return document.querySelector('#main_image');
  },

  injectThumbnailButtons(makeThumbnailBtn) {
    document.querySelectorAll('#image-list .shm-thumb.thumb').forEach(thumb => {
      if (thumb.querySelector('.bunbooru-btn')) return;
      const link = thumb.querySelector('a.shm-thumb-link');
      if (!link) return;
      thumb.style.position = 'relative';
      thumb.appendChild(makeThumbnailBtn(link.href));
    });
  },

  injectPageButton(makePageBtn) {
    if (document.querySelector('.bunbooru-page-btn')) return;
    const target = document.querySelector('#main_image') || document.querySelector('#main_video');
    if (!target) return;
    target.parentElement.insertBefore(makePageBtn(), target);
  },
});
