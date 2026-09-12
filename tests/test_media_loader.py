"""Local association and containment checks without interpreting media."""

import json
from decimal import Decimal

import pytest
from extraction import normalize_request
from loaders import LoadedRequest, discover_request_media


def put(root, relative, content=b"test"):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path.resolve()


def test_one_exact_id_file(tmp_path):
    path = put(tmp_path, "0007.txt", b"hello")
    result = discover_request_media("0007", tmp_path)
    (media,) = result.files
    assert result.request_id == "0007"
    assert media.path == path
    assert media.category == "text"
    assert media.extension == ".txt"
    assert media.mime_type == "text/plain"
    assert media.size_bytes == len(b"hello")


def test_multiple_files_sorted_with_directory_and_exact_stem(tmp_path):
    paths = [
        put(tmp_path, "7/z.PNG"),
        put(tmp_path, "7/nested/a.md"),
        put(tmp_path, "7.json"),
    ]
    result = discover_request_media("7", tmp_path)
    assert [media.path for media in result.files] == sorted(paths, key=str)
    assert {media.category for media in result.files} == {"text", "image"}
    assert result.issues == ()


@pytest.mark.parametrize(
    "extension, mime",
    [
        ("txt", "text/plain"),
        ("md", "text/markdown"),
        ("json", "application/json"),
        ("png", "image/png"),
        ("jpg", "image/jpeg"),
        ("jpeg", "image/jpeg"),
        ("webp", "image/webp"),
    ],
)
def test_all_supported_types_without_parsing_content(tmp_path, extension, mime):
    put(tmp_path, f"7.{extension}", b"not valid image or JSON data")
    result = discover_request_media("7", tmp_path)
    assert result.files[0].mime_type == mime


def test_no_media_and_missing_root_are_empty_successes(tmp_path):
    assert discover_request_media("7", tmp_path).files == ()
    result = discover_request_media("7", tmp_path / "absent")
    assert result.files == result.missing_paths == result.issues == ()


def test_missing_explicit_reference(tmp_path):
    request = LoadedRequest(
        Decimal(1), source_fields={"request_id": "7", "image_path": "missing.png"}
    )
    result = discover_request_media(request, tmp_path)
    assert result.files == ()
    assert result.missing_paths == ((tmp_path / "missing.png").resolve(),)


def test_explicit_references_prefer_and_deduplicate(tmp_path):
    explicit = put(tmp_path, "shared/image.jpg")
    put(tmp_path, "7.txt")
    source = {
        "request_id": " 7 ",
        "media_path": "shared/image.jpg",
        "media_paths": json.dumps(["shared/image.jpg", "shared"]),
    }
    request = normalize_request(LoadedRequest(Decimal(1), source_fields=source))
    result = discover_request_media(request, tmp_path)
    assert result.request_id == "7"
    assert [media.path for media in result.files] == [explicit]
    assert request.source_fields == source


def test_unsupported_associated_file(tmp_path):
    path = put(tmp_path, "7.pdf")
    result = discover_request_media("7", tmp_path)
    assert result.files == ()
    (media,) = result.unsupported_files
    assert media.path == path
    assert media.category == "unsupported"
    assert media.extension == ".pdf"


def test_exact_matching_excludes_other_requests(tmp_path):
    correct = put(tmp_path, "7.txt")
    put(tmp_path, "17.txt")
    put(tmp_path, "70/a.png")
    put(tmp_path, "request7.jpg")
    put(tmp_path, "7_extra.md")
    assert [media.path for media in discover_request_media("7", tmp_path).files] == [
        correct
    ]


@pytest.mark.parametrize(
    "reference",
    [
        "../outside.txt",
        "nested/../../outside.txt",
        "https://example.com/a.png",
        "file.txt:secret",
    ],
)
def test_unsafe_paths_reported(tmp_path, reference):
    request = LoadedRequest(Decimal(1), source_fields={"media_path": reference})
    result = discover_request_media(request, tmp_path)
    assert result.files == result.missing_paths == ()
    assert result.issues


def test_external_absolute_path_reported(tmp_path):
    root = tmp_path / "media"
    root.mkdir()
    outside = put(tmp_path, "outside.txt")
    request = LoadedRequest(Decimal(1), source_fields={"media_path": str(outside)})
    result = discover_request_media(request, root)
    assert result.files == ()
    assert "escapes media_root" in result.issues[0].reason


def test_symlink_escape_not_followed(tmp_path):
    root = tmp_path / "media"
    root.mkdir()
    outside = tmp_path / "external"
    put(outside, "secret.txt")
    try:
        (root / "7").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("OS does not permit creating symbolic links")
    result = discover_request_media("7", root)
    assert result.files == ()
    assert result.issues


def test_invalid_media_paths_reported(tmp_path):
    request = LoadedRequest(Decimal(1), source_fields={"media_paths": "not JSON"})
    result = discover_request_media(request, tmp_path)
    assert result.files == ()
    assert result.issues[0].reference == "media_paths"


def test_unsafe_request_id_reported(tmp_path):
    result = discover_request_media("../7", tmp_path)
    assert result.files == ()
    assert result.issues
