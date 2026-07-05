'use strict';
// ── rule34.xxx module ─────────────────────────────────────────────────────────

window.BUNBOORU_SITES = window.BUNBOORU_SITES || [];
window.BUNBOORU_SITES.push({
  name: 'rule34.xxx',

  matches(hostname) {
    return hostname === 'rule34.xxx';
  },

  isPostPage() {
    const p = new URLSearchParams(location.search);
    return p.get('page') === 'post' && p.get('s') === 'view';
  },

  isThumbnailPage() {
    const p = new URLSearchParams(location.search);
    return p.get('page') === 'post' && p.get('s') === 'list';
  },

  collectTags() {
    const sidebar = document.querySelector('#tag-sidebar');
    if (!sidebar) return null;
    const parts = [];
    const categoryMap = {
      'tag-type-artist':    'cr:',
      'tag-type-character': 'ch:',
      'tag-type-copyright': 'co:',
      'tag-type-general':   '',
      'tag-type-metadata':  'm:',
    };
    for (const [cls, prefix] of Object.entries(categoryMap)) {
      sidebar.querySelectorAll(`li.${cls}`).forEach(li => {
        const a = li.querySelectorAll('a')[1];
        if (!a || !a.textContent) return;
        const tag = a.textContent.trim().replace(/ /g, '_');
        if (tag) parts.push(prefix + tag);
      });
    }
    return parts.join(' ') || '';
  },

  getMediaUrl() {
    const img = document.querySelector('#image');
    if (img) return img.src;
    const vid = document.querySelector('#gelcomVideoContainer video source, #gelcomVideoPlayer source');
    if (vid) return vid.src;
    return null;
  },

  getImageElement() {
    return document.querySelector('#image');
  },

  injectThumbnailButtons(makeThumbnailBtn) {
    document.querySelectorAll('#post-list .thumb').forEach(thumb => {
      if (thumb.querySelector('.bunbooru-btn')) return;
      const link = thumb.querySelector('a');
      if (!link) return;
      thumb.style.position = 'relative';
      thumb.appendChild(makeThumbnailBtn(link.href));
    });
  },

  injectPageButton(makePageBtn) {
    if (document.querySelector('.bunbooru-page-btn')) return;
    const target = document.querySelector('#image') || document.querySelector('#gelcomVideoContainer');
    if (!target) return;
    target.parentElement.insertBefore(makePageBtn(), target);
  },
});
