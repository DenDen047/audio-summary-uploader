"""citation モジュールの単体テスト (出典抽出・サニタイズ)."""
from __future__ import annotations

from automator.citation import (
    EmailCitation,
    format_source_line,
    is_spark_share_url,
    parse_email_metadata,
    sanitize_public_text,
    strip_citation_markers,
)


class TestStripCitationMarkers:
    def test_removes_single_marker(self) -> None:
        assert strip_citation_markers("新拡散モデル [1]") == "新拡散モデル"

    def test_removes_multi_marker(self) -> None:
        assert strip_citation_markers("A [1, 2] B [3]") == "A B"

    def test_keeps_fullwidth_brackets(self) -> None:
        title = "なぜ爆速化したのか？【論文解説】"
        assert strip_citation_markers(title) == title

    def test_no_markers_unchanged(self) -> None:
        assert strip_citation_markers("タイトル") == "タイトル"


class TestIsSparkShareUrl:
    def test_detects_spark(self) -> None:
        assert is_spark_share_url("https://app.sparkmailapp.com/web-share/AbC123")

    def test_case_insensitive(self) -> None:
        assert is_spark_share_url("HTTPS://APP.SPARKMAILAPP.COM/WEB-SHARE/X")

    def test_non_spark(self) -> None:
        assert not is_spark_share_url("https://arxiv.org/abs/2511.15059")


class TestParseEmailMetadata:
    def test_parses_fenced_json_with_null_domain(self) -> None:
        answer = (
            "```json\n"
            '{\n  "title": "Are Anthropic Bills Accurate?",\n'
            '  "sender": "Applied AI",\n  "domain": null,\n'
            '  "date": "2026-06-25"\n}\n```'
        )
        assert parse_email_metadata(answer) == EmailCitation(
            title="Are Anthropic Bills Accurate?",
            sender="Applied AI",
            date="2026-06-25",
            domain=None,
        )

    def test_strips_citation_marker_before_parsing(self) -> None:
        answer = (
            '{"title": "T", "sender": "S", "domain": "ex.com", '
            '"date": "2026-01-01"} [1]'
        )
        c = parse_email_metadata(answer)
        assert c is not None
        assert c.sender == "S"
        assert c.domain == "ex.com"

    def test_returns_none_on_no_json(self) -> None:
        assert parse_email_metadata("すみません、わかりません。") is None

    def test_treats_string_null_and_empty_as_none(self) -> None:
        answer = '{"title": "T", "sender": "null", "domain": "", "date": "不明"}'
        c = parse_email_metadata(answer)
        assert c is not None
        assert c.sender is None
        assert c.domain is None
        assert c.date is None


class TestFormatSourceLine:
    def test_with_domain_and_date(self) -> None:
        c = EmailCitation(
            sender="The Batch", domain="deeplearning.ai", date="2026-06-20"
        )
        expected = "出典: The Batch（deeplearning.ai） - 2026-06-20"
        assert format_source_line(c) == expected

    def test_without_domain(self) -> None:
        c = EmailCitation(sender="Applied AI", date="2026-06-25")
        assert format_source_line(c) == "出典: Applied AI - 2026-06-25"

    def test_without_date(self) -> None:
        c = EmailCitation(sender="X", domain="x.com")
        assert format_source_line(c) == "出典: X（x.com）"


class TestSanitizePublicText:
    def test_removes_email(self) -> None:
        out = sanitize_public_text("連絡先 foo.bar@example.com です")
        assert "foo.bar@example.com" not in out

    def test_removes_spark_url(self) -> None:
        out = sanitize_public_text(
            "元: https://app.sparkmailapp.com/web-share/AbC123xyz 参照"
        )
        assert "sparkmailapp.com" not in out

    def test_keeps_clean_text(self) -> None:
        assert sanitize_public_text("普通の説明文です") == "普通の説明文です"
