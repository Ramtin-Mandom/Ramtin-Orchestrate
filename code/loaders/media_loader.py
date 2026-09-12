"""Discover local media metadata without reading or interpreting contents."""

import json
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Literal, Optional, Tuple, Union

from .requests import LoadedRequest

_TYPES = {
    ".txt": ("text", "text/plain"),
    ".md": ("text", "text/markdown"),
    ".json": ("text", "application/json"),
    ".png": ("image", "image/png"),
    ".jpg": ("image", "image/jpeg"),
    ".jpeg": ("image", "image/jpeg"),
    ".webp": ("image", "image/webp"),
}


@dataclass(frozen=True)
class MediaFile:
    path: Path
    category: Literal["text", "image", "unsupported"]
    extension: str
    mime_type: str
    size_bytes: int


@dataclass(frozen=True)
class MediaIssue:
    reference: str
    reason: str


@dataclass(frozen=True)
class RequestMedia:
    request_id: Optional[str]
    files: Tuple[MediaFile, ...] = ()
    unsupported_files: Tuple[MediaFile, ...] = ()
    missing_paths: Tuple[Path, ...] = ()
    issues: Tuple[MediaIssue, ...] = ()


def _references(source):
    references = []
    for field in ("media_path", "message_path", "image_path"):
        value = source.get(field, "")
        if value and value.strip():
            references.append(value.strip())
    value = source.get("media_paths", "")
    if value and value.strip():
        try:
            paths = json.loads(value)
        except ValueError as exc:
            raise ValueError(
                "media_paths must be a JSON array of path strings"
            ) from exc
        if not isinstance(paths, list) or any(
            not isinstance(path, str) or not path.strip() for path in paths
        ):
            raise ValueError(
                "media_paths must be a JSON array of nonempty path strings"
            )
        references.extend(path.strip() for path in paths)
    return references


def _safe_path(reference, root):
    path = Path(reference)
    windows = PureWindowsPath(reference)
    if ".." in path.parts or ".." in windows.parts:
        raise ValueError("path traversal is not allowed")
    if windows.drive and not path.is_absolute():
        raise ValueError("external or drive-relative path is not allowed")
    if any(":" in part for part in windows.parts[1:] if windows.drive) or (
        not windows.drive and ":" in reference
    ):
        raise ValueError("alternate streams or URI paths are not allowed")
    resolved = (path if path.is_absolute() else root / path).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("resolved path escapes media_root") from exc
    return resolved


def _inspect_paths(paths, root):
    found = {}
    missing = set()
    issues = set()
    pending = list(paths)
    visited = set()
    while pending:
        reference = pending.pop()
        try:
            resolved = _safe_path(reference, root)
            if resolved in visited:
                continue
            visited.add(resolved)
            if not resolved.exists():
                missing.add(resolved)
            elif resolved.is_dir():
                pending.extend(str(child) for child in resolved.iterdir())
            elif resolved.is_file():
                extension = resolved.suffix.lower()
                category, mime = _TYPES.get(
                    extension, ("unsupported", "application/octet-stream")
                )
                found[resolved] = MediaFile(
                    resolved, category, extension, mime, resolved.stat().st_size
                )
            else:
                issues.add(
                    MediaIssue(str(reference), "not a regular file or directory")
                )
        except (OSError, ValueError, RuntimeError) as exc:
            issues.add(MediaIssue(str(reference), str(exc)))
    ordered = sorted(found.values(), key=lambda item: str(item.path))
    return (
        tuple(item for item in ordered if item.category != "unsupported"),
        tuple(item for item in ordered if item.category == "unsupported"),
        tuple(sorted(missing, key=str)),
        tuple(sorted(issues, key=lambda issue: (issue.reference, issue.reason))),
    )


def discover_request_media(
    request_or_id: Union[str, LoadedRequest], media_root: Union[str, Path]
) -> RequestMedia:
    """Prefer explicit references, else exact root-level ID directory/stems.

    Supported source fields are media_path, message_path, image_path and
    media_paths (JSON array). Normalized objects with source_fields also work.
    Explicit directories are traversed recursively; resolved paths must stay
    under media_root. Symlink cycles are deduplicated by resolved directory.
    No contents are opened. Without a supplied dataset schema these are the
    documented fallback associations, not inferred challenge-specific rules.
    """
    source = {} if isinstance(request_or_id, str) else request_or_id.source_fields
    request_id = (
        request_or_id if isinstance(request_or_id, str) else source.get("request_id")
    )
    request_id = request_id.strip() or None if request_id is not None else None
    issues = []
    try:
        references = _references(source)
    except ValueError as exc:
        return RequestMedia(request_id, issues=(MediaIssue("media_paths", str(exc)),))
    try:
        root = Path(media_root).resolve()
        if not references:
            if request_id is None:
                return RequestMedia(request_id)
            if request_id in {".", ".."} or any(char in request_id for char in "/\\:"):
                return RequestMedia(
                    request_id,
                    issues=(
                        MediaIssue(
                            request_id, "request ID must be a single path component"
                        ),
                    ),
                )
            if not root.exists():
                return RequestMedia(request_id)
            references = [
                str(child)
                for child in root.iterdir()
                if (child.is_dir() and child.name == request_id)
                or (not child.is_dir() and child.stem == request_id)
            ]
        files, unsupported, missing, path_issues = _inspect_paths(references, root)
        return RequestMedia(request_id, files, unsupported, missing, path_issues)
    except (OSError, ValueError, RuntimeError) as exc:
        issues.append(MediaIssue(str(media_root), str(exc)))
        return RequestMedia(request_id, issues=tuple(issues))
