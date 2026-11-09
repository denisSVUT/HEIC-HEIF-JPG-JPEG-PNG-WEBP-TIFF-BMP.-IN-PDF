"""Offline photo-to-PDF engine. HEIC/HEIF support via pillow-heif."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence
import io
import os
import re

import fitz
from PIL import Image, ImageOps
from pillow_heif import register_heif_opener

register_heif_opener()

EXTENSIONS = {'.heic', '.heif', '.jpg', '.jpeg', '.png', '.webp', '.tif', '.tiff', '.bmp'}


@dataclass(eq=False)
class Photo:
    path: Path
    rotate: int = 0  # clockwise degrees: 0, 90, 180, 270


def natural_key(path: Path):
    pieces = re.split(r'(\d+)', str(path).casefold())
    return tuple((0, int(p)) if p.isdigit() else (1, p) for p in pieces)


def scan_folder(folder: Path, sort_mode: str = 'name_reverse') -> list[Photo]:
    folder = Path(folder)
    if not folder.is_dir():
        raise ValueError(f'Папка не существует: {folder}')
    files = [p for p in folder.rglob('*') if p.is_file() and p.suffix.lower() in EXTENSIONS]
    if sort_mode.startswith('date_'):
        files.sort(key=lambda p: (p.stat().st_mtime_ns, natural_key(p)))
    else:
        files.sort(key=lambda p: natural_key(p.relative_to(folder)))
    if sort_mode.endswith('reverse'):
        files.reverse()
    return [Photo(p) for p in files]


def load_photo(path: Path, clockwise_rotation: int = 0, max_side: int | None = None) -> Image.Image:
    """Load, orient and convert one image. Return detached RGB image."""
    with Image.open(path) as source:
        corrected = ImageOps.exif_transpose(source)
        if clockwise_rotation % 360:
            corrected = corrected.rotate(-(clockwise_rotation % 360), expand=True)
        if corrected.mode == 'RGB':
            result = corrected.copy()
        elif 'A' in corrected.getbands() or 'transparency' in corrected.info:
            overlay = corrected.convert('RGBA')
            result = Image.new('RGB', overlay.size, 'white')
            result.paste(overlay, mask=overlay.getchannel('A'))
        else:
            result = corrected.convert('RGB')
    if max_side and max(result.size) > max_side:
        result.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    return result


def _pdf_page_for_photo(document: fitz.Document, photo: Photo, max_side: int, quality: int) -> None:
    img = load_photo(photo.path, photo.rotate, max_side if max_side > 0 else None)
    try:
        width, height = img.size
        if width > height:
            page_w, page_h = 842.0, 595.0  # A4 landscape
        else:
            page_w, page_h = 595.0, 842.0  # A4 portrait
        buffer = io.BytesIO()
        # JPEG's 4:4:4 chroma sampling keeps small text and colored stamps legible.
        img.save(buffer, 'JPEG', quality=quality, subsampling=0)
        page = document.new_page(width=page_w, height=page_h)
        margin = 10.0
        available_w, available_h = page_w - 2 * margin, page_h - 2 * margin
        ratio = min(available_w / width, available_h / height)
        display_w, display_h = width * ratio, height * ratio
        left, top = (page_w - display_w) / 2.0, (page_h - display_h) / 2.0
        page.insert_image(fitz.Rect(left, top, left + display_w, top + display_h), stream=buffer.getvalue())
    finally:
        img.close()


def create_pdfs(
    photos: Sequence[Photo],
    start_tom2: int,
    start_tom3: int,
    output_dir: Path,
    max_side: int = 0,
    quality: int = 90,
    combine: bool = True,
    progress: Callable[[int, int, str], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> list[Path]:
    """Create three volume PDFs, plus optionally one combined document.

    A PDF per volume is committed atomically. A cancelled volume has no partial PDF.
    """
    count = len(photos)
    if not (0 < start_tom2 < start_tom3 < count):
        raise ValueError('Укажите начало томов 2 и 3 (оба должны идти после предыдущего тома).')
    if quality < 60 or quality > 98:
        raise ValueError('Качество JPEG должно быть от 60 до 98.')
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    chunks = [photos[:start_tom2], photos[start_tom2:start_tom3], photos[start_tom3:]]
    volume_paths = [output_dir / f'Материалы_дела_Том_{i}.pdf' for i in (1, 2, 3)]
    combined_path = output_dir / 'Материалы_дела_Все_3_тома.pdf'
    ready = []
    done = 0
    for n, (chunk, destination) in enumerate(zip(chunks, volume_paths), start=1):
        if is_cancelled and is_cancelled():
            raise InterruptedError('Операция отменена. Готовые тома сохранены.')
        part_path = destination.with_name(destination.name + '.partial')
        doc = fitz.open()
        try:
            for photo in chunk:
                if is_cancelled and is_cancelled():
                    raise InterruptedError('Операция отменена. Готовые тома сохранены.')
                try:
                    _pdf_page_for_photo(doc, photo, max_side, quality)
                except Exception as exc:
                    raise RuntimeError(f'Не удалось прочитать фотографию {photo.path.name}: {exc}') from exc
                done += 1
                if progress:
                    progress(done, count, f'Том {n}: {photo.path.name}')
            doc.set_metadata({'title': f'Материалы дела - Том {n}', 'creator': 'HEIC PDF Тома'})
            # JPEG images remain JPEG; avoid unintended raster re-compression.
            doc.save(str(part_path), garbage=3, deflate=True)
        except BaseException:
            if part_path.exists():
                part_path.unlink()
            raise
        finally:
            doc.close()
        try:
            with fitz.open(part_path) as check:
                if len(check) != len(chunk):
                    raise RuntimeError(f'Неверное количество страниц тома {n}.')
            os.replace(part_path, destination)
            ready.append(destination)
        finally:
            if part_path.exists():
                part_path.unlink()

    if combine:
        if is_cancelled and is_cancelled():
            raise InterruptedError('Операция отменена. Три PDF по томам сохранены.')
        if progress:
            progress(count, count, 'Объединение трёх томов (может занять время)...')
        part_path = combined_path.with_name(combined_path.name + '.partial')
        combined = fitz.open()
        try:
            for source_path in volume_paths:
                if is_cancelled and is_cancelled():
                    raise InterruptedError('Операция отменена. Три PDF по томам сохранены.')
                with fitz.open(source_path) as source:
                    combined.insert_pdf(source, links=False, annots=False)
            combined.set_metadata({'title': 'Материалы дела - Все три тома', 'creator': 'HEIC PDF Тома'})
            combined.save(str(part_path), garbage=3, deflate=True)
        except BaseException:
            if part_path.exists():
                part_path.unlink()
            raise
        finally:
            combined.close()
        try:
            with fitz.open(part_path) as check:
                if len(check) != count:
                    raise RuntimeError('Количество страниц объединённого PDF не совпадает с количеством фотографий.')
            os.replace(part_path, combined_path)
            ready.append(combined_path)
        finally:
            if part_path.exists():
                part_path.unlink()
    return ready
