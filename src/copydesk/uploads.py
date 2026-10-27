"""Image uploads: covers, pictures in articles, avatars and the logo.

Every image is opened and re-saved with Pillow. That checks it really is an
image, applies the camera's rotation, shrinks oversized photos, and removes
EXIF metadata, which can include the GPS location where a photo was taken.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from flask import url_for
from PIL import Image, ImageOps, ImageSequence, UnidentifiedImageError
from werkzeug.datastructures import FileStorage

from copydesk.db import db
from copydesk.helpers import state
from copydesk.models import Upload
from copydesk.utils import random_token, utcnow

FORMATS = {
    "JPEG": ("image/jpeg", "jpg"),
    "PNG": ("image/png", "png"),
    "GIF": ("image/gif", "gif"),
    "WEBP": ("image/webp", "webp"),
}
MAX_PIXELS = 40_000_000
MAX_EDGE = 2400
Image.MAX_IMAGE_PIXELS = MAX_PIXELS


class UploadError(Exception):
    pass


def uploads_dir() -> Path:
    return state().config.uploads_dir


def url(path: str) -> str:
    return url_for("public.upload", path=path) if path else ""


def prepare_image(file: FileStorage | None, *, kind: str = "image") -> tuple[bytes, str, str, int, int]:
    """Validate and re-encode without writing a file or a database row."""
    if file is None or not file.filename:
        raise UploadError("Choose an image to upload.")
    limit = state().config.max_upload_mb * 1024 * 1024
    data = file.read(limit + 1)
    if len(data) > limit:
        raise UploadError(f"That image is larger than {state().config.max_upload_mb} MB. Try a smaller one.")
    try:
        probe = Image.open(BytesIO(data))
        probe.verify()
        image = Image.open(BytesIO(data))
        image_format = image.format
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError, ValueError):
        raise UploadError("That file isn't an image we can use. Try a JPEG, PNG, GIF or WebP.") from None
    if image_format not in FORMATS:
        raise UploadError("Please upload a JPEG, PNG, GIF or WebP image.")
    if image.width * image.height > MAX_PIXELS:
        raise UploadError("That image has too many pixels. Please resize it and try again.")
    content_type, extension = FORMATS[image_format]

    try:
        animated = image_format in ("GIF", "WEBP") and getattr(image, "is_animated", False)
        buffer = BytesIO()
        if animated and kind == "image":
            frames, durations = [], []
            loop = image.info.get("loop", 0)
            pixels = 0
            for frame in ImageSequence.Iterator(image):
                pixels += frame.width * frame.height
                if pixels > MAX_PIXELS or len(frames) >= 500:
                    raise UploadError("That animation is too large to process. Please shorten or resize it.")
                durations.append(frame.info.get("duration", 100))
                clean = frame.convert("RGBA")
                clean.thumbnail((MAX_EDGE, MAX_EDGE))
                clean.info.clear()
                frames.append(clean)
            options = {"disposal": 2} if image_format == "GIF" else {}
            frames[0].save(buffer, image_format, save_all=True, append_images=frames[1:],
                           duration=durations, loop=loop, **options)
            width, height = frames[0].size
        else:
            image = ImageOps.exif_transpose(image)
            if image_format == "PNG" and "transparency" in image.info:
                image = image.convert("RGBA")
            if kind == "avatar":
                image = ImageOps.fit(image, (400, 400))
            elif kind == "logo":
                image.thumbnail((800, 240))
            elif max(image.size) > MAX_EDGE:
                image.thumbnail((MAX_EDGE, MAX_EDGE))
            image.info.clear()
            if image_format == "JPEG":
                image.convert("RGB").save(buffer, "JPEG", quality=85, optimize=True, progressive=True)
            elif image_format == "PNG":
                image.save(buffer, "PNG", optimize=True)
            elif image_format == "WEBP":
                image.save(buffer, "WEBP", quality=85)
            else:
                image.save(buffer, "GIF")
            width, height = image.size
        output = buffer.getvalue()
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):
        raise UploadError("That image couldn't be decoded. Try another JPEG, PNG, GIF or WebP.") from None

    return output, content_type, extension, width, height


def save_image(file: FileStorage | None, user_id: int | None, *, kind: str = "image") -> Upload:
    """Validate, clean and store an uploaded image. ``kind`` is image, avatar or logo."""
    prepared = prepare_image(file, kind=kind)
    return store_image(prepared, file.filename or "", user_id)


def store_image(prepared: tuple[bytes, str, str, int, int], name: str, user_id: int | None) -> Upload:
    output, content_type, extension, width, height = prepared
    now = utcnow()
    relative = f"{now:%Y}/{now:%m}/{random_token(12)}.{extension}"
    target = uploads_dir() / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(output)
    upload = Upload(
        path=relative,
        original_name=name[:255],
        content_type=content_type,
        size=len(output),
        width=width,
        height=height,
        user_id=user_id,
    )
    db.session.add(upload)
    return upload
