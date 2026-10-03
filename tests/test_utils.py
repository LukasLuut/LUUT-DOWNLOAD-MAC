from pathlib import Path

import pytest

from app.utils.filenames import safe_join, sanitize_filename, unique_path
from app.utils.formatters import (
    NOT_AVAILABLE, format_bytes, format_datetime, format_duration, format_eta, format_speed,
)
from app.utils.urls import is_valid_url, normalize_url, parse_url_lines


class TestUrls:
    @pytest.mark.parametrize("url", [
        "https://www.youtube.com/watch?v=abc",
        "http://example.com/video.mp4",
        "  https://vimeo.com/123  ",
        "https://sub.domain.co.uk/path?q=1#frag",
        "http://127.0.0.1:8080/file.mp4",
    ])
    def test_valid(self, url):
        assert is_valid_url(url)

    @pytest.mark.parametrize("url", [
        "", None, "   ", "not a url", "ftp://example.com/file", "javascript:alert(1)",
        "file:///C:/Windows/system32", "https://", "https://exa mple.com", "example",
        "https://example.com:99999/", "calc.exe", "https://" + "a" * 3000 + ".com",
    ])
    def test_invalid(self, url):
        assert not is_valid_url(url)

    def test_www_prefix_gets_scheme(self):
        assert normalize_url("www.example.com/v") == "https://www.example.com/v"

    def test_parse_lines_marks_invalid_individually(self):
        text = "https://a.com/1\n\n# comment\nnot-a-url\nhttps://a.com/1\nhttps://b.com/2"
        lines = parse_url_lines(text)
        assert [line.raw for line in lines] == ["https://a.com/1", "not-a-url", "https://b.com/2"]
        assert [line.is_valid for line in lines] == [True, False, True]
        assert lines[1].line_number == 4


class TestFilenames:
    def test_removes_invalid_characters(self):
        assert sanitize_filename('a\\b/c:d*e?f"g<h>i|j') == "a b c d e f g h i j"

    def test_collapses_spaces_and_trailing_dots(self):
        assert sanitize_filename("  hello    world ...  ") == "hello world"

    def test_keeps_accents_and_unicode(self):
        assert sanitize_filename("Canção de ação — 日本") == "Canção de ação — 日本"

    def test_reserved_names(self):
        assert sanitize_filename("CON") == "_CON"
        assert sanitize_filename("nul.txt") == "_nul.txt"

    def test_empty_uses_fallback(self):
        assert sanitize_filename("") == "video"
        assert sanitize_filename(None) == "video"
        assert sanitize_filename("???") == "video"

    def test_max_length(self):
        assert len(sanitize_filename("x" * 500)) == 150

    def test_control_characters(self):
        assert sanitize_filename("a\x00b\nc\td") == "a b c d"

    def test_unique_path_generates_numbered_names(self, tmp_path: Path):
        first = unique_path(tmp_path, "video", ".mp4")
        assert first.name == "video.mp4"
        first.write_text("1")
        second = unique_path(tmp_path, "video", "mp4")
        assert second.name == "video (1).mp4"
        second.write_text("2")
        assert unique_path(tmp_path, "video", ".mp4").name == "video (2).mp4"

    def test_unique_path_with_spaces_and_accents(self, tmp_path: Path):
        folder = tmp_path / "Pasta com espaços e acentuação"
        folder.mkdir()
        (folder / "Vídeo.mp4").write_text("x")
        assert unique_path(folder, "Vídeo", ".mp4").name == "Vídeo (1).mp4"

    def test_safe_join_blocks_traversal(self, tmp_path: Path):
        joined = safe_join(tmp_path, "..", "2026")
        assert tmp_path.resolve() in joined.parents


class TestFormatters:
    def test_bytes(self):
        assert format_bytes(None) == NOT_AVAILABLE
        assert format_bytes(512) == "512 B"
        assert format_bytes(1536) == "1,5 KB"
        assert format_bytes(438 * 1024 * 1024) == "438 MB"

    def test_speed(self):
        assert format_speed(None) == "—"
        assert format_speed(12.4 * 1024 * 1024) == "12,4 MB/s"

    def test_duration(self):
        assert format_duration(522) == "08:42"
        assert format_duration(3723) == "1:02:03"
        assert format_duration(None) == NOT_AVAILABLE
        assert format_eta(8) == "00:08"

    def test_datetime(self):
        assert format_datetime("2026-09-22T14:03:00") == "22/09/2026 14:03"
        assert format_datetime("garbage") == NOT_AVAILABLE
