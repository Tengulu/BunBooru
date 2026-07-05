# bunbooru

A local booru-style media cataloguing system. Self-hosted, runs in Docker.

## Requirements

- [Docker Desktop](https://www.docker.com/products/docker-desktop/)
- [Firefox](https://www.firefox.com/) (for the browser extension)

## Setup

1. Clone the repo:
   ```
   git clone https://github.com/yourusername/bunbooru.git
   cd bunbooru
   ```

2. Start the containers:
   ```
   docker compose up -d
   ```

3. Open your browser and go to:
   ```
   http://localhost:8000
   ```

That's it. The database and storage folders are created automatically on first run.

## Browser Extension

The `booru-extension/` folder contains a Firefox extension that lets you send media from supported sites directly to bunbooru.

**Supported sites:**
- rule34.xxx
- paheal.net
- Twitter / X
- Redgifs

**To install:**
1. Open Firefox and go to `about:debugging`
2. Click "This Firefox"
3. Click "Load Temporary Add-on"
4. Select any file inside the `booru-extension/` folder

## Inbox

Drop files into `storage/inbox/` and they will be automatically imported.

## Backups

Manual backups can be triggered from the topbar. Automatic daily backups are saved to `backups/auto/` and the last 7 are kept.
