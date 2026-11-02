"""File-aware format detection.

Never trusts extensions alone: magic-byte signatures, OOXML internal members and
content heuristics decide the actual format. Pure stdlib; unit-testable.
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass

SUPPORTED_FORMATS = {
    "pdf", "docx", "xlsx", "pptx", "csv", "txt", "md", "png", "jpeg", "gif", "tiff", "bmp", "webp"
}


@dataclass(frozen=True)
class DetectedFile:
    fmt: str  # canonical format id, "unknown" if undetectable
    mime: str
    method: str  # signature | ooxml-member | content-heuristic
    confidence: float
    notes: tuple[str, ...] = ()


def _magic(header: bytes) -> tuple[str, str] | None:
    table = [
        (b"%PDF-", "pdf", "application/pdf"),
        (b"\x89PNG\r\n\x1a\n", "png", "image/png"),
        (b"\xff\xd8\xff", "jpeg", "image/jpeg"),
        (b"GIF87a", "gif", "image/gif"),
        (b"GIF89a", "gif", "image/gif"),
        (b"II*\x00", "tiff", "image/tiff"),
        (b"MM\x00*", "tiff", "image/tiff"),
        (b"BM", "bmp", "image/bmp"),
    ]
    for magic, fmt, mime in table:
        if header.startswith(magic):
            return fmt, mime
    if header.startswith(b"RIFF") and header[8:12] == b"WEBP":
        return "webp", "image/webp"
    return None


def _ooxml_member(header: bytes) -> tuple[str, str] | None:
    """Identify OOXML flavour by peeking at the zip central directory members."""
    if not header.startswith(b"PK"):
        return None
    # A signature alone is ambiguous (docx/xlsx/pptx all zip); members decide.
    try:
        with zipfile.ZipFile(_BytesView(header)) as zf:
            names = set(zf.namelist())
    except Exception:
        # Not seekable/complete here; callers pass the full object via `members`.
        return None
    return _classify_ooxml(names)


def _classify_ooxml(names: set[str]) -> tuple[str, str] | None:
    if any(n.startswith("word/") for n in names):
        return "docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    if any(n.startswith("xl/") for n in names):
        return "xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    if any(n.startswith("ppt/") for n in names):
        return "pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    return None


class _BytesView:
    """Minimal file-like wrapper so zipfile can read from a bytes buffer."""

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._pos = 0

    def read(self, n: int = -1) -> bytes:
        if n < 0:
            chunk, self._pos = self._data[self._pos :], len(self._data)
        else:
            chunk = self._data[self._pos : self._pos + n]
            self._pos += len(chunk)
        return chunk

    def seek(self, offset: int, whence: int = 0) -> int:
        if whence == 0:
            self._pos = offset
        elif whence == 1:
            self._pos += offset
        else:
            self._pos = len(self._data) + offset
        return self._pos

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._pos


_MD_MARKERS = re.compile(r"^(#{1,6}\s|\s*[-*+]\s|>\s|```)", re.MULTILINE)


def _looks_binary(sample: bytes) -> bool:
    if b"\x00" in sample:
        return True
    controls = sum(1 for b in sample[:2048] if b < 9 or 13 < b < 32)
    return len(sample) > 0 and controls / min(len(sample), 2048) > 0.30


def _delimiter_consistency(sample: str) -> tuple[str | None, float]:
    best_delim, best_ratio = None, 0.0
    for delim in (",", ";", "\t", "|"):
        counts = [line.count(delim) for line in sample.splitlines()[:50] if line.strip()]
        if not counts:
            continue
        consistent = sum(1 for c in counts if c == counts[0]) / len(counts)
        if counts[0] >= 1 and consistent > best_ratio:
            best_delim, best_ratio = delim, consistent
    return best_delim, best_ratio


def detect_format(
    filename: str,
    header: bytes,
    sample: bytes | None = None,
    *,
    ooxml_members: set[str] | None = None,
) -> DetectedFile:
    """Detect the real format from signature + content. Extension is a hint only."""
    notes: list[str] = []
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    hit = _magic(header)
    if hit:
        fmt, mime = hit
        if ext and ext not in (fmt, {"jpg": "jpeg"}.get(ext, ext)):
            notes.append(f"extension '{ext}' disagrees with signature; using signature")
        return DetectedFile(fmt, mime, "signature", 1.0, tuple(notes))

    if header.startswith(b"PK"):
        if ooxml_members is not None:
            hit = _classify_ooxml(ooxml_members)
            if hit:
                return DetectedFile(hit[0], hit[1], "ooxml-member", 1.0, tuple(notes))
        hit = _ooxml_member(header)
        if hit:
            return DetectedFile(hit[0], hit[1], "ooxml-member", 1.0, tuple(notes))
        return DetectedFile("unknown", "application/zip", "ooxml-member", 0.2, ("zip without OOXML members",))

    sample = sample if sample is not None else header
    if _looks_binary(sample):
        return DetectedFile("unknown", "application/octet-stream", "content-heuristic", 0.1, ("binary content",))
    text = sample.decode("utf-8", errors="ignore")

    delim, ratio = _delimiter_consistency(text)
    if delim and ratio >= 0.8:
        return DetectedFile("csv", "text/csv", "content-heuristic", min(0.95, 0.6 + ratio / 2), (f"delimiter={delim!r}",))

    if _MD_MARKERS.search(text) and len(_MD_MARKERS.findall(text)) >= 2:
        return DetectedFile("md", "text/markdown", "content-heuristic", 0.8, tuple(notes))

    if ext in ("md", "markdown"):
        notes.append("no markdown markers found; treating as markdown by extension")
        return DetectedFile("md", "text/markdown", "content-heuristic", 0.5, tuple(notes))
    return DetectedFile("txt", "text/plain", "content-heuristic", 0.6, tuple(notes))


def validate_upload(detected: DetectedFile, size_bytes: int, max_bytes: int) -> list[str]:
    """Return rejection reasons (empty list means the upload is acceptable)."""
    reasons: list[str] = []
    if detected.fmt not in SUPPORTED_FORMATS:
        reasons.append(f"unsupported format: {detected.fmt}")
    if size_bytes <= 0:
        reasons.append("empty file")
    if size_bytes > max_bytes:
        reasons.append(f"file exceeds {max_bytes} bytes")
    return reasons
