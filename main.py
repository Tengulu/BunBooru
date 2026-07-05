import os
import traceback
import hashlib
import subprocess
import time
import threading
import asyncio
import json
from pathlib import Path
import zipfile
import tempfile
import py7zr
import rarfile
from datetime import datetime, timezone

from fastapi import FastAPI, UploadFile, File, Form, Depends, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from PIL import Image
import aiofiles
import httpx


from watchdog.observers.polling import PollingObserver
from watchdog.events import FileSystemEventHandler

from database import (
    get_db, create_tables, SessionLocal,
    Post, Tag, PostTag, TagAlias, BogusTag,
    CreatorAutotag
)

app = FastAPI(title="Bunbooru")

# ── Debug: catch any tag_me! creation ────────────────────────────────────────
import traceback
from sqlalchemy import event

@event.listens_for(PostTag, 'before_insert')
def catch_lowercase_tagme_posttag(mapper, connection, target):
    # Look up the tag name via the ORM relationship if loaded
    try:
        if target.tag and target.tag.name and target.tag.name.lower() == 'tag_me!' and target.tag.name != 'TAG_ME!':
            import logging
            logging.error(f"[TAGME DEBUG] lowercase tag_me! being linked to post {target.post_id}!\n{''.join(traceback.format_stack())}")
    except Exception:
        pass

@event.listens_for(Tag, 'before_insert')
def catch_tagme_creation(mapper, connection, target):
    if target.name and target.name.lower() == 'tag_me!' and target.name != 'TAG_ME!':
        import logging
        logging.error(f"[TAGME DEBUG] lowercase tag_me! being CREATED in tags table!\n{''.join(traceback.format_stack())}")

@event.listens_for(Tag, 'before_update')
def catch_tagme_rename(mapper, connection, target):
    if target.name and target.name.lower() == 'tag_me!' and target.name != 'TAG_ME!':
        import logging
        logging.error(f"[TAGME DEBUG] tag being RENAMED to tag_me!\n{''.join(traceback.format_stack())}")


@app.get("/")
def root():
    return FileResponse("/app/splash.html")

@app.get("/topbar")
def topbar():
    return FileResponse("/app/topbar.html")

@app.get("/browse")
def browse():
    return FileResponse("/app/browse.html")

@app.get("/browse/{post_id}")
def browse_post(post_id: int):
    return FileResponse("/app/detail.html")

@app.get("/upload")
def upload_page():
    return FileResponse("/app/upload.html")

@app.get("/aliases")
def aliases_page():
    return FileResponse("/app/aliases.html")

app.mount("/static", StaticFiles(directory="/app/static"), name="static")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

STORAGE_PATH = Path(os.environ.get("STORAGE_PATH", "./storage"))
ORIGINALS_PATH = STORAGE_PATH / "originals"
THUMBS_PATH = STORAGE_PATH / "thumbs"
INBOX_PATH = STORAGE_PATH / "inbox"

IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
VIDEO_TYPES = {"video/mp4", "video/webm", "video/x-matroska", "video/quicktime", "video/x-msvideo", "video/x-flv", "video/x-ms-wmv", "video/x-m4v", "video/3gpp", "video/mp2t"}
SUPPORTED_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp",
    ".mp4", ".webm", ".mkv", ".mov"
}


@app.on_event("startup")
def startup():
    for i in range(10):
        try:
            create_tables()
            break
        except Exception as e:
            print(f"Database not ready yet, retrying in 3s... ({i+1}/10)")
            time.sleep(3)

    ORIGINALS_PATH.mkdir(parents=True, exist_ok=True)
    THUMBS_PATH.mkdir(parents=True, exist_ok=True)
    INBOX_PATH.mkdir(parents=True, exist_ok=True)
    Path('/app/static/counter').mkdir(parents=True, exist_ok=True)

    # Import anything already in inbox at startup
    for file_path in INBOX_PATH.iterdir():
        if file_path.suffix.lower() in SUPPORTED_EXTENSIONS:
            process_inbox_file(file_path)

    # Watchdog inbox observer
    observer = PollingObserver()
    observer.schedule(InboxHandler(), str(INBOX_PATH), recursive=False)
    observer.start()
    print(f"Inbox watchdog (PollingObserver) watching {INBOX_PATH}")



# ── Helpers ───────────────────────────────────────────────────────────────────

def hash_file(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def storage_path_for(hash: str, base: Path, ext: str) -> Path:
    subdir = base / hash[:2] / hash[2:4]
    subdir.mkdir(parents=True, exist_ok=True)
    return subdir / f"{hash}.{ext}"


def detect_mime(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    mime_map = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
        ".gif": "image/gif", ".webp": "image/webp",
        ".mp4": "video/mp4", ".webm": "video/webm",
        ".mkv": "video/x-matroska", ".mov": "video/quicktime",
    }
    return mime_map.get(ext, "application/octet-stream")


def get_image_dimensions(path: Path):
    try:
        with Image.open(path) as img:
            return img.width, img.height
    except Exception:
        return None, None


def get_video_info(path: Path):
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height,duration",
             "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=30
        )
        parts = result.stdout.strip().split(",")
        width = int(parts[0]) if len(parts) > 0 else None
        height = int(parts[1]) if len(parts) > 1 else None
        duration = int(float(parts[2])) if len(parts) > 2 else None
        return width, height, duration
    except Exception:
        return None, None, None


def make_image_thumbnail(src: Path, dst: Path):
    try:
        with Image.open(src) as img:
            img.thumbnail((512, 512))
            img.convert("RGB").save(dst, "JPEG", quality=85)
    except Exception as e:
        print(f"Thumbnail error: {e}")


def make_video_thumbnail(src: Path, dst: Path):
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(src), "-ss", "00:00:01",
             "-vframes", "1", "-vf", "scale=512:512:force_original_aspect_ratio=decrease",
             str(dst)],
            capture_output=True, timeout=60
        )
    except Exception as e:
        print(f"Video thumbnail error: {e}")


def resolve_tag(name: str, db: Session) -> str:
    stripped = name.strip().replace(" ", "_")
    lowered = stripped.lower()
    # check alias on lowercase version
    alias = db.query(TagAlias).filter(TagAlias.alias == lowered).first()
    if alias:
        return alias.canonical
    # if exact case exists in DB, use that
    exact = db.query(Tag).filter(Tag.name == stripped).first()
    if exact:
        return stripped
    # if lowercase version exists in DB, use that
    lower_match = db.query(Tag).filter(Tag.name == lowered).first()
    if lower_match:
        return lowered
    # new tag — preserve original case
    return stripped


# ── Tag prefix parsing ───────────────────────────────────────────────────────

TAG_PREFIXES = {
    "creator:": "creator",
    "cr:": "creator",
    "character:": "character",
    "ch:": "character",
    "copyright:": "copyright",
    "co:": "copyright",
    "meta:": "meta",
    "m:": "meta",
}

# Meta tags that are auto-generated — excluded from tag browser grid
AUTO_META_TAGS = {
    "image", "video", "animated", "landscape", "portrait", "square",
    "hd", "fullhd", "4k", "short_video", "long_video",
}

def parse_tag_input(raw: str) -> tuple[str, str]:
    """Parse a raw tag string into (name, category). Handles prefixes."""
    raw = raw.strip().replace(" ", "_")
    if raw == "TAG_ME!":
        return "TAG_ME!", "meta"
    raw_lower = raw.lower()
    # handle m:TAG_ME! — strip prefix and preserve casing
    if raw_lower == "m:tag_me!" or raw_lower == "meta:tag_me!":
        return "TAG_ME!", "meta"
    for prefix, category in TAG_PREFIXES.items():
        if raw_lower.startswith(prefix):
            name = raw[len(prefix):].strip("_")
            return name, category
    return raw, "general"


def generate_meta_tags(mime_type: str, width: int, height: int, duration: int) -> list[str]:
    """Auto-generate meta tags from file metadata."""
    tags = []
    if mime_type:
        if mime_type.startswith("image/"):
            tags.append("image")
            if mime_type == "image/gif":
                tags.append("animated")
        elif mime_type.startswith("video/"):
            tags.append("video")
            if duration:
                if duration < 60:
                    tags.append("short_video")
                elif duration > 300:
                    tags.append("long_video")
    if width and height:
        if width > height:
            tags.append("landscape")
        elif height > width:
            tags.append("portrait")
        else:
            tags.append("square")
        mp = width * height
        if mp >= 3840 * 2160:
            tags.append("4k")
        elif mp >= 1920 * 1080:
            tags.append("fullhd")
        elif mp >= 1280 * 720:
            tags.append("hd")
    return tags


def get_or_create_tag(name: str, db: Session) -> Tag:
    tag = db.query(Tag).filter(Tag.name == name).first()
    if not tag:
        if name == 'tag_me!':
            print(f'[DEBUG] WARNING: creating lowercase tag_me! tag!')
            print('[DEBUG] call stack:')
            traceback.print_stack()
        tag = Tag(name=name)
        db.add(tag)
        db.flush()
    return tag


def get_post_tags(post_id: int, db: Session) -> list:
    """Always fetch fresh tags directly from DB — never use cached ORM relationship."""
    return db.query(Tag).join(PostTag).filter(PostTag.post_id == post_id).all()


def is_fully_tagged(post_id: int, db: Session) -> bool:
    """Return True if post has at least 3 general tags, 1 creator tag, and real or drawn."""
    tags = get_post_tags(post_id, db)
    meta_names = {t.name for t in tags if t.category == "meta"}
    general_count = sum(1 for t in tags if t.category == "general")
    has_creator = any(t.category == "creator" for t in tags)
    has_real_or_drawn = "real" in meta_names or "drawn" in meta_names
    return general_count >= 3 and has_creator and has_real_or_drawn


def sync_tag_me(post: Post, db: Session):
    """Add or remove TAG_ME! based on whether the post is fully tagged."""
    # always query fresh from DB — never trust ORM relationship cache
    tags = get_post_tags(post.id, db)
    tag_names = {t.name for t in tags}

    # clean up any lowercase tag_me! from old bugs
    tag_me_lower = db.query(Tag).filter(Tag.name == "tag_me!").first()
    if tag_me_lower:
        db.query(PostTag).filter(
            PostTag.post_id == post.id,
            PostTag.tag_id == tag_me_lower.id
        ).delete(synchronize_session='fetch')

    tag_me = db.query(Tag).filter(Tag.name == "TAG_ME!").first()
    has_tag_me = "TAG_ME!" in tag_names
    fully_tagged = is_fully_tagged(post.id, db)

    if fully_tagged and has_tag_me:
        if not tag_me:
            return
        db.query(PostTag).filter(
            PostTag.post_id == post.id,
            PostTag.tag_id == tag_me.id
        ).delete(synchronize_session='fetch')
        tag_me.post_count = max(0, (tag_me.post_count or 0) - 1)

    elif not fully_tagged and not has_tag_me:
        if not tag_me:
            tag_me = Tag(name="TAG_ME!", category="meta", post_count=0)
            db.add(tag_me)
            db.flush()
        db.add(PostTag(post_id=post.id, tag_id=tag_me.id))
        tag_me.post_count = (tag_me.post_count or 0) + 1


def apply_tags_to_post(post: Post, tag_names: list[str], db: Session):
    tag_names = [t.lower() if t != 'TAG_ME!' else t for t in tag_names]  # normalise to lowercase, preserve TAG_ME!
    # DEBUG: detect lowercase tag_me! being passed in
    if 'tag_me!' in tag_names:
        print(f'[DEBUG] WARNING: lowercase tag_me! detected in apply_tags_to_post for post {post.id}')
        print(f'[DEBUG] tag_names: {tag_names}')
        print('[DEBUG] call stack:')
        traceback.print_stack()
    db.query(PostTag).filter(PostTag.post_id == post.id).delete()
    seen = set()
    for raw in tag_names:
        # parse prefix first to get the name and category
        name_raw, category = parse_tag_input(raw)
        # then resolve aliases on the name only
        name = resolve_tag(name_raw, db)
        if not name or name in seen:
            continue
        seen.add(name)
        tag = get_or_create_tag(name, db)
        # update category if it changed
        if tag.category != category:
            tag.category = category
        db.add(PostTag(post_id=post.id, tag_id=tag.id))
    for tag_name in seen:
        tag = db.query(Tag).filter(Tag.name == tag_name).first()
        if tag:
            tag.post_count = db.query(PostTag).filter(PostTag.tag_id == tag.id).count()


# ── Video transcoding ────────────────────────────────────────────────────────

# Codecs that browsers can play natively (no transcoding needed)
BROWSER_SAFE_CODECS = {"h264", "avc1"}

def get_video_codec(path: Path) -> str:
    """Return the video codec name for a file using ffprobe."""
    try:
        result = subprocess.run([
            "ffprobe", "-v", "quiet",
            "-select_streams", "v:0",
            "-show_entries", "stream=codec_name",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path)
        ], capture_output=True, text=True, timeout=30)
        return result.stdout.strip().lower()
    except Exception as e:
        print(f"[transcode] ffprobe error: {e}")
        return ""


def needs_transcode(path: Path, mime: str) -> bool:
    """Return True if the file needs transcoding for browser compatibility."""
    if mime == "video/webm":
        return True
    if mime in VIDEO_TYPES:
        codec = get_video_codec(path)
        return codec not in BROWSER_SAFE_CODECS
    return False


def transcode_to_h264(src: Path, dst: Path) -> bool:
    """Transcode src to H.264/AAC MP4 at dst. Returns True on success."""
    try:
        print(f"[transcode] transcoding {src.name} → {dst.name}")
        result = subprocess.run([
            "ffmpeg", "-y",
            "-i", str(src),
            "-c:v", "libx264",
            "-crf", "18",
            "-preset", "medium",
            "-c:a", "aac",
            "-b:a", "192k",
            "-movflags", "+faststart",
            str(dst)
        ], capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            print(f"[transcode] ffmpeg error: {result.stderr[-500:]}")
            return False
        print(f"[transcode] done, output size: {dst.stat().st_size} bytes")
        return True
    except Exception as e:
        print(f"[transcode] error: {e}")
        return False


def ingest_file(data: bytes, filename: str, rating: str = "safe",
                source_url: str = None, source_site: str = None,
                initial_tags: list[str] = None, db: Session = None) -> dict:
    mime = detect_mime(filename)
    if mime not in IMAGE_TYPES and mime not in VIDEO_TYPES:
        return {"status": "unsupported"}

    # transcode before hashing so duplicate check uses the final H.264 hash
    if mime in VIDEO_TYPES:
        import tempfile
        suffix = Path(filename).suffix
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(data)
            tmp_path = Path(tmp.name)
        try:
            if needs_transcode(tmp_path, mime):
                tc_path = tmp_path.parent / (tmp_path.stem + "_tc.mp4")
                if transcode_to_h264(tmp_path, tc_path):
                    data = tc_path.read_bytes()
                    tc_path.unlink()
                    mime = "video/mp4"
                    filename = Path(filename).stem + ".mp4"
                else:
                    print(f"[transcode] failed for {filename}, keeping original")
                    if tc_path.exists():
                        tc_path.unlink()
        finally:
            if tmp_path.exists():
                tmp_path.unlink()

    file_hash = hash_file(data)
    existing = db.query(Post).filter(Post.hash == file_hash).first()
    if existing:
        return {"id": existing.id, "status": "duplicate"}

    ext = Path(filename).suffix.lstrip(".")
    original_path = storage_path_for(file_hash, ORIGINALS_PATH, ext)
    thumb_path = storage_path_for(file_hash, THUMBS_PATH, "jpg")

    with open(original_path, "wb") as f:
        f.write(data)

    width = height = duration = None
    if mime in IMAGE_TYPES:
        width, height = get_image_dimensions(original_path)
        make_image_thumbnail(original_path, thumb_path)
    elif mime in VIDEO_TYPES:
        width, height, duration = get_video_info(original_path)
        make_video_thumbnail(original_path, thumb_path)

    post = Post(
        hash=file_hash,
        filename=filename,
        mime_type=mime,
        file_size=len(data),
        width=width,
        height=height,
        duration=duration,
        rating=rating,
        source_url=source_url,
        source_site=source_site,
    )
    db.add(post)
    db.flush()

    # auto-generate meta tags
    meta_tags = [f"meta:{t}" for t in generate_meta_tags(mime, width, height, duration)]

    # strip bogus tags at ingest
    bogus_names = {b.name.lower() for b in db.query(BogusTag).all()}
    base_tags = [t for t in (initial_tags or []) if t.lower() not in bogus_names]
    all_tags = base_tags + meta_tags
    apply_tags_to_post(post, all_tags, db)
    db.flush()
    apply_creator_autotags(post, db)
    db.flush()
    sync_tag_me(post, db)
    db.commit()
    return {"id": post.id, "hash": post.hash, "status": "created"}


# ── Inbox watchdog ────────────────────────────────────────────────────────────

def wait_for_file_stable(file_path: Path, interval: float = 1.0, retries: int = 30) -> bool:
    last_size = -1
    for _ in range(retries):
        try:
            current_size = file_path.stat().st_size
        except FileNotFoundError:
            return False
        if current_size == last_size and current_size > 0:
            return True
        last_size = current_size
        time.sleep(interval)
    return False


def process_inbox_file(file_path: Path):
    if file_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        return
    if file_path.name.startswith("."):
        return
    try:
        if not wait_for_file_stable(file_path):
            print(f"Inbox: {file_path.name} did not stabilize, skipping")
            return
        data = file_path.read_bytes()
        db = SessionLocal()
        print(f"[inbox] ingesting {file_path.name} with source_url={file_path.name}")
        result = ingest_file(data=data, filename=file_path.name, source_url=file_path.name, source_site="inbox", initial_tags=[], db=db)
        print(f"[inbox] result: {result}")
        db.close()
        if result["status"] in ("created", "duplicate"):
            file_path.unlink()
            print(f"Inbox: {file_path.name} → post #{result.get('id')} ({result['status']})")
        else:
            print(f"Inbox: skipped {file_path.name} ({result['status']})")
    except Exception as e:
        print(f"Inbox: error processing {file_path.name}: {e}")


class InboxHandler(FileSystemEventHandler):
    def on_created(self, event):
        if not event.is_directory:
            process_inbox_file(Path(event.src_path))


# ── Posts ─────────────────────────────────────────────────────────────────────

@app.post("/posts")
async def upload_post(
    file: UploadFile = File(...),
    tags: str = Form(""),
    rating: str = Form("safe"),
    source_url: str = Form(""),
    source_site: str = Form(""),
    db: Session = Depends(get_db)
):
    data = await file.read()
    print(f"[upload] tags received: {repr(tags)}")
    tag_list = [t for t in tags.split() if t]
    print(f"[upload] tag_list: {tag_list}")
    print(f"[upload] source_url received: {repr(source_url)}, file.filename: {repr(file.filename)}")
    result = ingest_file(
        data=data, filename=file.filename, rating=rating,
        source_url=source_url or file.filename or None, source_site=source_site or None,
        initial_tags=tag_list, db=db,
    )
    if result["status"] == "unsupported":
        raise HTTPException(400, "Unsupported file type")
    return result


@app.get("/posts")
def search_posts(
    tags: str = "", rating: str = "", source_site: str = "",
    page: int = 1, limit: int = 40, db: Session = Depends(get_db)
):
    query = db.query(Post)
    tag_list = [t for t in tags.split() if t]
    exclude_tags = [t[1:] for t in tag_list if t.startswith("-")]
    include_tags = [t for t in tag_list if not t.startswith("-")]

    for tag_name in include_tags:
        canonical = resolve_tag(tag_name, db)
        tag = db.query(Tag).filter(Tag.name == canonical).first()
        if not tag:
            return {"posts": [], "total": 0, "page": page}
        query = query.filter(Post.tags.any(PostTag.tag_id == tag.id))

    for tag_name in exclude_tags:
        canonical = resolve_tag(tag_name, db)
        # strip category prefix for DB lookup (e.g. m:shitpost -> shitpost)
        lookup_name = canonical
        for prefix in ["cr:", "ch:", "co:", "m:"]:
            if canonical.startswith(prefix):
                lookup_name = canonical[len(prefix):]
                break
        tag = db.query(Tag).filter(Tag.name == lookup_name).first()
        if tag:
            query = query.filter(~Post.tags.any(PostTag.tag_id == tag.id))

    if rating:
        query = query.filter(Post.rating == rating)
    if source_site:
        query = query.filter(Post.source_site == source_site)

    total = query.count()
    posts = query.order_by(Post.id.desc()).offset((page - 1) * limit).limit(limit).all()

    return {
        "total": total, "page": page, "limit": limit,
        "posts": [
            {
                "id": p.id, "hash": p.hash, "mime_type": p.mime_type,
                "width": p.width, "height": p.height, "duration": p.duration,
                "rating": p.rating, "source_url": p.source_url,
                "source_site": p.source_site,
                "created_at": p.created_at.isoformat(),
                "tags": [pt.tag.name for pt in p.tags],
            }
            for p in posts
        ],
    }


@app.get("/posts/{post_id}")
def get_post(post_id: int, db: Session = Depends(get_db)):
    post = db.query(Post).filter(Post.id == post_id).first()
    if not post:
        raise HTTPException(404, "Post not found")
    return {
        "id": post.id, "hash": post.hash, "mime_type": post.mime_type,
        "width": post.width, "height": post.height, "duration": post.duration,
        "rating": post.rating, "source_url": post.source_url,
        "source_site": post.source_site, "file_size": post.file_size,
        "created_at": post.created_at.isoformat(),
        "tags": [{"name": pt.tag.name, "category": pt.tag.category} for pt in post.tags],
    }


@app.put("/posts/{post_id}/tags")
def update_post_tags(post_id: int, tags: str, db: Session = Depends(get_db)):
    post = db.query(Post).filter(Post.id == post_id).first()
    if not post:
        raise HTTPException(404, "Post not found")
    tag_list = [t for t in tags.split() if t]
    # preserve existing meta tags
    existing_meta = [
        pt.tag.name for pt in post.tags
        if pt.tag.category == "meta"
    ]
    # merge incoming tags with existing meta (incoming may also add new meta tags)
    # exclude TAG_ME! from existing meta — sync_tag_me handles it exclusively
    all_tags = tag_list + [f"meta:{t}" for t in existing_meta if t != "TAG_ME!" and f"meta:{t}" not in tag_list and t not in tag_list]
    # determine if post is fully tagged: has non-meta tag AND has real or drawn
    final_tags = [t for t in all_tags if t != "TAG_ME!"]
    apply_tags_to_post(post, final_tags, db)
    db.flush()
    db.refresh(post)
    apply_creator_autotags(post, db)
    db.flush()
    db.refresh(post)
    sync_tag_me(post, db)
    db.commit()
    db.refresh(post)
    return {"status": "ok", "tags": [{"name": pt.tag.name, "category": pt.tag.category} for pt in post.tags]}


@app.post("/upload/archive")
async def upload_archive(
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    # Read archive filename and convert ~ prefixes to :
    archive_name = Path(file.filename).stem
    tags_str = archive_name.replace("cr~", "cr:").replace("ch~", "ch:").replace("co~", "co:").replace("m~", "m:")
    tag_list = [t for t in tags_str.split() if t]

    data = await file.read()
    suffix = Path(file.filename).suffix.lower()

    MEDIA_TYPES = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.mp4', '.webm', '.mov', '.mkv'}

    results = {"created": 0, "duplicates": 0, "errors": 0, "skipped": 0}

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        archive_path = tmpdir / file.filename
        archive_path.write_bytes(data)

        try:
            if suffix == ".zip":
                with zipfile.ZipFile(archive_path) as zf:
                    zf.extractall(tmpdir / "extracted")
            elif suffix == ".7z":
                with py7zr.SevenZipFile(archive_path) as zf:
                    zf.extractall(tmpdir / "extracted")
            elif suffix in (".rar",):
                with rarfile.RarFile(archive_path) as rf:
                    rf.extractall(tmpdir / "extracted")
            else:
                raise HTTPException(400, "Unsupported archive format")
        except Exception as e:
            raise HTTPException(400, f"Failed to extract archive: {e}")

        extracted = tmpdir / "extracted"
        for f in sorted(extracted.rglob("*")):
            if not f.is_file():
                continue
            if f.suffix.lower() not in MEDIA_TYPES:
                results["skipped"] += 1
                continue
            try:
                # build cumulative tags from archive root name + subfolder names
                rel = f.relative_to(extracted)
                parts = list(rel.parts[:-1])  # all path components except filename
                all_parts = [tags_str] + [
                    p.replace("cr~", "cr:").replace("ch~", "ch:").replace("co~", "co:").replace("m~", "m:")
                    for p in parts
                ]
                file_tags_str = " ".join(p for p in all_parts if p)
                file_tag_list = [t for t in file_tags_str.split() if t]
                file_data = f.read_bytes()
                result = ingest_file(
                    data=file_data, filename=f.name, rating="safe",
                    source_url=None, source_site=None,
                    initial_tags=file_tag_list, db=db,
                )
                if result["status"] == "created":
                    results["created"] += 1
                elif result["status"] == "duplicate":
                    results["duplicates"] += 1
                else:
                    results["errors"] += 1
            except Exception:
                results["errors"] += 1

    return results


@app.delete("/posts/{post_id}")
def delete_post(post_id: int, db: Session = Depends(get_db)):
    post = db.query(Post).filter(Post.id == post_id).first()
    if not post:
        raise HTTPException(404, "Post not found")
    # delete from DB first, then files
    ext = Path(post.filename).suffix.lstrip(".")
    orig = storage_path_for(post.hash, ORIGINALS_PATH, ext)
    thumb = storage_path_for(post.hash, THUMBS_PATH, "jpg")
    db.query(PostTag).filter(PostTag.post_id == post.id).delete()
    db.delete(post)
    db.commit()
    if orig.exists(): orig.unlink()
    if thumb.exists(): thumb.unlink()
    return {"status": "deleted"}


# ── Backup ───────────────────────────────────────────────────────────────────

@app.post("/backup")
def create_backup():
    import subprocess
    from datetime import datetime
    backup_dir = Path("/app/backups/manual")
    backup_dir.mkdir(parents=True, exist_ok=True)
    filename = backup_dir / f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.sql"
    result = subprocess.run(
        ["pg_dump", "-h", "db", "-U", "booru", "booru"],
        capture_output=True,
        env={**__import__('os').environ, "PGPASSWORD": "booru"}
    )
    if result.returncode != 0:
        raise HTTPException(500, f"Backup failed: {result.stderr.decode()}")
    filename.write_bytes(result.stdout)
    # keep only last 3 manual backups
    backups = sorted(backup_dir.glob("backup_*.sql"))
    for old_backup in backups[:-3]:
        old_backup.unlink()
    return {"status": "ok", "filename": filename.name}


# ── Files ─────────────────────────────────────────────────────────────────────

@app.get("/files/{file_hash}")
def serve_file(file_hash: str, db: Session = Depends(get_db)):
    post = db.query(Post).filter(Post.hash == file_hash).first()
    if not post:
        raise HTTPException(404, "Not found")
    ext = Path(post.filename).suffix.lstrip(".")
    path = storage_path_for(file_hash, ORIGINALS_PATH, ext)
    if not path.exists():
        raise HTTPException(404, "File missing from storage")
    return FileResponse(path, media_type=post.mime_type)


@app.get("/thumbs/{file_hash}")
def serve_thumb(file_hash: str):
    path = storage_path_for(file_hash, THUMBS_PATH, "jpg")
    if not path.exists():
        raise HTTPException(404, "Thumbnail not found")
    return FileResponse(path, media_type="image/jpeg")


# ── Tags ──────────────────────────────────────────────────────────────────────

@app.get("/tags")
def list_tags(search: str = "", category: str = "", limit: int = 20, db: Session = Depends(get_db)):
    bogus_names = [b.name for b in db.query(BogusTag).all()]
    query = db.query(Tag).filter(~Tag.name.in_(bogus_names))
    if search:
        query = query.filter(Tag.name.ilike(f"%{search}%"))
    if category:
        query = query.filter(Tag.category == category)
    tags = query.order_by(Tag.post_count.desc()).limit(limit).all()
    return [{"name": t.name, "category": t.category, "post_count": t.post_count} for t in tags]


@app.get("/taglist")
def taglist_page():
    return FileResponse("taglist.html")


@app.get("/api/tags/all")
def get_all_tags(search: str = "", category: str = "", sort: str = "count", db: Session = Depends(get_db)):
    query = db.query(Tag)
    if search:
        query = query.filter(Tag.name.ilike(f"%{search}%"))
    if category:
        query = query.filter(Tag.category == category)
    if sort == "name":
        query = query.order_by(Tag.name.asc())
    else:
        query = query.order_by(Tag.post_count.desc(), Tag.name.asc())
    tags = query.all()
    return [{"name": t.name, "category": t.category, "post_count": t.post_count} for t in tags]


@app.delete("/api/tags/{tag_name}")
def delete_tag(tag_name: str, db: Session = Depends(get_db)):
    tag = db.query(Tag).filter(Tag.name == tag_name).first()
    if not tag:
        raise HTTPException(404, "tag not found")
    # remove all post_tag associations
    db.query(PostTag).filter(PostTag.tag_id == tag.id).delete(synchronize_session='fetch')
    db.delete(tag)
    db.commit()
    return {"status": "deleted"}


@app.put("/tags/{tag_name}")
def update_tag(tag_name: str, category: str, db: Session = Depends(get_db)):
    tag = db.query(Tag).filter(Tag.name == tag_name).first()
    if not tag:
        raise HTTPException(404, "Tag not found")
    tag.category = category
    db.commit()
    return {"name": tag.name, "category": tag.category}


# ── Tag Aliases ───────────────────────────────────────────────────────────────

@app.get("/api/aliases")
def list_aliases(db: Session = Depends(get_db)):
    aliases = db.query(TagAlias).all()
    result = []
    for a in aliases:
        alias_tag = db.query(Tag).filter(Tag.name == a.alias).first()
        canonical_tag = db.query(Tag).filter(Tag.name == a.canonical).first()
        result.append({
            "alias": a.alias,
            "canonical": a.canonical,
            "alias_category": alias_tag.category if alias_tag else "general",
            "canonical_category": canonical_tag.category if canonical_tag else "general",
        })
    return result


@app.post("/api/aliases")
def create_alias(alias: str, canonical: str, db: Session = Depends(get_db)):
    alias = alias.strip().lower().replace(" ", "_")
    # parse prefix from canonical to get category
    canonical_name, category = parse_tag_input(canonical)
    canonical_name = canonical_name.strip().lower().replace(" ", "_")
    # update or create alias
    existing = db.query(TagAlias).filter(TagAlias.alias == alias).first()
    if existing:
        existing.canonical = canonical_name
    else:
        db.add(TagAlias(alias=alias, canonical=canonical_name))
    # update or create the canonical tag with correct category
    tag = db.query(Tag).filter(Tag.name == canonical_name).first()
    if tag:
        if category != "general":
            tag.category = category
    else:
        # create the tag with the correct category so it exists with right type
        new_tag = Tag(name=canonical_name, category=category, post_count=0)
        db.add(new_tag)
    db.commit()
    return {"alias": alias, "canonical": canonical_name, "category": category}


@app.post("/api/aliases/apply")
def apply_aliases_to_all(db: Session = Depends(get_db)):
    """Apply all aliases to existing posts, renaming matching tags."""
    aliases = db.query(TagAlias).all()
    if not aliases:
        return {"updated_posts": 0, "updated_tags": 0}

    alias_map = {a.alias: a.canonical for a in aliases}
    updated_posts = 0
    updated_tags = 0

    for alias_name, canonical_name in alias_map.items():
        alias_tag = db.query(Tag).filter(Tag.name == alias_name).first()
        if not alias_tag:
            continue

        canonical_tag = get_or_create_tag(canonical_name, db)
        db.flush()  # ensure canonical_tag has an id

        # collect post_ids that have the alias tag
        post_ids = [pt.post_id for pt in db.query(PostTag).filter(PostTag.tag_id == alias_tag.id).all()]

        for post_id in post_ids:
            # add canonical tag if not already present
            already_has = db.query(PostTag).filter(
                PostTag.post_id == post_id,
                PostTag.tag_id == canonical_tag.id
            ).first()
            if not already_has:
                db.add(PostTag(post_id=post_id, tag_id=canonical_tag.id))
                updated_posts += 1

            # remove alias tag link using delete by filter (avoids ORM cascade issues)
            db.query(PostTag).filter(
                PostTag.post_id == post_id,
                PostTag.tag_id == alias_tag.id
            ).delete(synchronize_session='fetch')
            updated_tags += 1

        db.flush()

        # update post counts
        alias_tag.post_count = db.query(PostTag).filter(PostTag.tag_id == alias_tag.id).count()
        canonical_tag.post_count = db.query(PostTag).filter(PostTag.tag_id == canonical_tag.id).count()

    db.commit()
    return {"updated_posts": updated_posts, "updated_tags": updated_tags}


@app.get("/bogus")
def bogus_page():
    return FileResponse("/app/bogus.html")


@app.get("/api/bogus")
def list_bogus(db: Session = Depends(get_db)):
    return [{"name": b.name} for b in db.query(BogusTag).order_by(BogusTag.id.desc()).all()]


@app.post("/api/bogus")
def create_bogus(name: str, db: Session = Depends(get_db)):
    name = name.strip().lower().replace(" ", "_")
    if not name:
        raise HTTPException(400, "Name required")
    existing = db.query(BogusTag).filter(BogusTag.name == name).first()
    if existing:
        raise HTTPException(409, "Already exists")
    db.add(BogusTag(name=name))
    db.commit()
    return {"status": "created"}


@app.delete("/api/bogus/{name}")
def delete_bogus(name: str, db: Session = Depends(get_db)):
    bogus = db.query(BogusTag).filter(BogusTag.name == name).first()
    if not bogus:
        raise HTTPException(404, "Not found")
    db.delete(bogus)
    db.commit()
    return {"status": "deleted"}


@app.post("/api/bogus/apply")
def apply_bogus_to_all(db: Session = Depends(get_db)):
    bogus_list = db.query(BogusTag).all()
    if not bogus_list:
        return {"removed": 0}
    removed = 0
    for bogus in bogus_list:
        tag = db.query(Tag).filter(Tag.name == bogus.name).first()
        if not tag:
            continue
        count = db.query(PostTag).filter(PostTag.tag_id == tag.id).delete(synchronize_session='fetch')
        removed += count
        tag.post_count = 0
    db.commit()
    return {"removed": removed}


@app.delete("/api/aliases/{alias}")
def delete_alias(alias: str, db: Session = Depends(get_db)):
    obj = db.query(TagAlias).filter(TagAlias.alias == alias).first()
    if not obj:
        raise HTTPException(404, "Alias not found")
    db.delete(obj)
    db.commit()
    return {"status": "deleted"}



# ── File path ─────────────────────────────────────────────────────────────────

@app.get("/posts/{post_id}/filepath")
def get_filepath(post_id: int, db: Session = Depends(get_db)):
    """Returns the Windows host path to the file for copying to clipboard."""
    post = db.query(Post).filter(Post.id == post_id).first()
    if not post:
        raise HTTPException(404, "Post not found")
    ext = Path(post.filename).suffix.lstrip(".")
    path = storage_path_for(post.hash, ORIGINALS_PATH, ext)
    if not path.exists():
        raise HTTPException(404, "File missing from storage")
    host_base = os.environ.get("HOST_PATH", "X:\\bunbooru\\")
    windows_path = str(path).replace("/app/", host_base).replace("/", "\\")
    return {"path": windows_path}


# ── Stats ─────────────────────────────────────────────────────────────────────

@app.get("/stats")
def get_stats(db: Session = Depends(get_db)):
    total = db.query(Post).count()
    tag_me = db.query(Tag).filter(Tag.name == "TAG_ME!").first()
    untagged = db.query(PostTag).filter(PostTag.tag_id == tag_me.id).count() if tag_me else 0
    images = db.query(Post).filter(Post.mime_type.like("image/%")).count()
    videos = db.query(Post).filter(Post.mime_type.like("video/%")).count()
    tag_count = db.query(Tag).count()
    return {"total": total, "untagged": untagged, "images": images, "videos": videos, "tags": tag_count}


# ── Creator autotags ─────────────────────────────────────────────────────────

def apply_creator_autotags(post: Post, db: Session):
    """Apply autotags for any cr: tags on this post."""
    creator_tags = [pt.tag.name.lower() for pt in post.tags if pt.tag.category == "creator"]
    if not creator_tags:
        return
    for creator in creator_tags:
        rule = db.query(CreatorAutotag).filter(CreatorAutotag.creator == creator.lower()).first()
        if not rule:
            continue
        autotag_names = [t for t in rule.tags.split() if t]
        current_tags = [pt.tag.name for pt in post.tags]
        for tag_str in autotag_names:
            if tag_str.startswith("cr:"):
                name, cat = tag_str[3:], "creator"
            elif tag_str.startswith("ch:"):
                name, cat = tag_str[3:], "character"
            elif tag_str.startswith("co:"):
                name, cat = tag_str[3:], "copyright"
            elif tag_str.startswith("m:"):
                name, cat = tag_str[2:], "meta"
            else:
                name, cat = tag_str, "general"
            if name in current_tags:
                continue
            tag = db.query(Tag).filter(Tag.name == name).first()
            if not tag:
                tag = Tag(name=name, category=cat, post_count=0)
                db.add(tag)
                db.flush()
            existing = db.query(PostTag).filter(PostTag.post_id == post.id, PostTag.tag_id == tag.id).first()
            if not existing:
                db.add(PostTag(post_id=post.id, tag_id=tag.id))
                tag.post_count = (tag.post_count or 0) + 1


@app.get("/autotags")
def autotags_page():
    return FileResponse("autotags.html")


@app.get("/api/autotags")
def get_autotags(db: Session = Depends(get_db)):
    rules = db.query(CreatorAutotag).order_by(CreatorAutotag.creator).all()
    return [{"id": r.id, "creator": r.creator, "tags": r.tags} for r in rules]


@app.post("/api/autotags")
def create_autotag(creator: str, tags: str, db: Session = Depends(get_db)):
    creator = creator.strip().lower()
    if creator.startswith("cr:"):
        creator = creator[3:]
    tags = tags.strip()
    if not creator or not tags:
        raise HTTPException(status_code=400, detail="creator and tags required")
    existing = db.query(CreatorAutotag).filter(CreatorAutotag.creator == creator).first()
    if existing:
        raise HTTPException(status_code=409, detail="rule already exists")
    rule = CreatorAutotag(creator=creator, tags=tags)
    db.add(rule)
    db.commit()
    db.refresh(rule)

    # apply to all existing posts with this creator tag
    creator_tag = db.query(Tag).filter(Tag.name == creator, Tag.category == "creator").first()
    if creator_tag:
        posts = db.query(Post).join(PostTag).filter(PostTag.tag_id == creator_tag.id).all()
        for post in posts:
            apply_creator_autotags(post, db)
            sync_tag_me(post, db)
        db.commit()

    return {"id": rule.id, "creator": rule.creator, "tags": rule.tags}


@app.put("/api/autotags/{rule_id}")
def update_autotag(rule_id: int, tags: str, db: Session = Depends(get_db)):
    rule = db.query(CreatorAutotag).filter(CreatorAutotag.id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="not found")
    rule.tags = tags.strip()
    db.commit()
    return {"id": rule.id, "creator": rule.creator, "tags": rule.tags}


@app.delete("/api/autotags/{rule_id}")
def delete_autotag(rule_id: int, db: Session = Depends(get_db)):
    rule = db.query(CreatorAutotag).filter(CreatorAutotag.id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="not found")
    db.delete(rule)
    db.commit()
    return {"status": "deleted"}


@app.post("/api/autotags/{rule_id}/apply")
def apply_autotag_rule(rule_id: int, db: Session = Depends(get_db)):
    rule = db.query(CreatorAutotag).filter(CreatorAutotag.id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="not found")
    creator_tag = db.query(Tag).filter(Tag.name == rule.creator, Tag.category == "creator").first()
    if not creator_tag:
        return {"applied": 0}
    posts = db.query(Post).join(PostTag).filter(PostTag.tag_id == creator_tag.id).all()
    for post in posts:
        apply_creator_autotags(post, db)
        db.flush()
        sync_tag_me(post, db)
        db.flush()
    db.commit()
    return {"applied": len(posts)}


# ── Sync TAG_ME! for all posts ────────────────────────────────────────────────

@app.post("/api/sync-tagme")
def sync_all_tagme(db: Session = Depends(get_db)):
    """Add/remove TAG_ME! for all posts based on current tagging rules."""
    posts = db.query(Post).all()
    added = 0
    removed = 0
    for post in posts:
        had = any(pt.tag.name == "TAG_ME!" for pt in post.tags)
        sync_tag_me(post, db)
        db.flush()
        has_now = "TAG_ME!" in {t.name for t in get_post_tags(post.id, db)}
        if had and not has_now:
            removed += 1
        elif not had and has_now:
            added += 1
    db.commit()
    return {"added": added, "removed": removed}


# ── Untagged queue endpoint ───────────────────────────────────────────────────

@app.get("/posts/untagged")
def get_untagged_posts(db: Session = Depends(get_db)):
    """Return all posts with TAG_ME! tag, ordered oldest first."""
    tag_me = db.query(Tag).filter(Tag.name == "TAG_ME!").first()
    if not tag_me:
        return {"posts": []}
    posts = db.query(Post).join(PostTag).filter(PostTag.tag_id == tag_me.id).order_by(Post.id.asc()).all()
    return {"posts": [{"id": p.id} for p in posts]}


# ── Tagger endpoints ─────────────────────────────────────────────────────────

@app.get("/tagger/next")
def tagger_next(db: Session = Depends(get_db)):
    """Get the next post that needs tagging (has TAG_ME!)."""
    tag_me = db.query(Tag).filter(Tag.name == "TAG_ME!").first()
    if not tag_me:
        return None
    post = db.query(Post).join(PostTag).filter(PostTag.tag_id == tag_me.id).order_by(Post.id.asc()).first()
    if not post:
        return None
    return {
        "id": post.id,
        "hash": post.hash,
        "mime_type": post.mime_type,
        "width": post.width,
        "height": post.height,
        "duration": post.duration,
        "source_url": post.source_url,
        "source_site": post.source_site,
        "filename": post.filename,
        "created_at": post.created_at.isoformat(),
        "tags": [{"name": pt.tag.name, "category": pt.tag.category} for pt in post.tags],
    }


@app.get("/tags/browser")
def tag_browser(search: str = "", limit: int = 500, db: Session = Depends(get_db)):
    """All non-auto-meta tags for the tag browser grid."""
    query = db.query(Tag).filter(~Tag.name.in_(AUTO_META_TAGS))
    if search:
        query = query.filter(Tag.name.ilike(f"%{search}%"))
    tags = query.order_by(Tag.post_count.desc()).limit(limit).all()
    return [{"name": t.name, "category": t.category, "post_count": t.post_count} for t in tags]


@app.get("/tagger/page")
def tagger_page():
    return FileResponse("/app/tagger.html")

@app.get("/tagger")
def tagger():
    return FileResponse("/app/tagger.html")

