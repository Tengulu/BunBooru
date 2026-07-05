// Mirrors the working extension's content.js exactly.
// Loads twitter_video_downloader.js then inject.js into page context,
// and relays session data messages to the background script.

const downloaderScript = document.createElement('script');
downloaderScript.src = browser.runtime.getURL('twitter_video_downloader.js');
downloaderScript.onload = () => {
  downloaderScript.remove();

  const interceptorScript = document.createElement('script');
  interceptorScript.src = browser.runtime.getURL('inject.js');
  interceptorScript.onload = () => interceptorScript.remove();
  (document.head || document.documentElement).appendChild(interceptorScript);
};
(document.head || document.documentElement).appendChild(downloaderScript);

// relay session data from page context to background
window.addEventListener('message', (event) => {
  if (event.source !== window) return;
  if (event.data?.source === 'rectifying@gmail.com' && event.data.type === 'UPDATE_SESSION_DATA') {
    browser.runtime.sendMessage({
      type: 'UPDATE_SESSION_DATA',
      data: event.data.data
    });
  }
});
