"""Unit tests: IMAP modified UTF-7 codec and MIME parsing."""
from __future__ import annotations

from app.mail import utf7
from app.mail.imap import parse_internal_date
from app.mail.parse import make_snippet, parse_message
from tests.fakes import MSG_2_FULL, MSG_3_FULL


def test_utf7_roundtrip_chinese():
    for name in ("已发送", "草稿箱", "垃圾邮件", "INBOX", "Notes&More", "收件箱/子文件夹"):
        assert utf7.decode(utf7.encode(name)) == name


def test_utf7_decode_known_vector():
    # "已发送" in modified UTF-7
    assert utf7.decode("&XfJT0ZAB-") == "已发送"


def test_parse_internal_date():
    dt = parse_internal_date(b'"17-Sep-2026 10:00:00 +0800"')
    assert dt is not None
    assert (dt.year, dt.month, dt.day) == (2026, 9, 17)
    assert dt.utcoffset().total_seconds() == 8 * 3600


def test_parse_multipart_gbk_with_attachment():
    parsed = parse_message(MSG_2_FULL)
    assert parsed["subject"] == "Report"
    assert "GBK 编码的邮件" in parsed["text"]
    assert parsed["from"][0]["email"] == "bob@example.com"
    assert parsed["from"][0]["name"] == "默默"
    assert parsed["cc"][0]["email"] == "carol@example.com"
    assert len(parsed["attachments"]) == 1
    att = parsed["attachments"][0]
    assert att["filename"] == "report.pdf"
    assert att["content_type"] == "application/pdf"
    assert att["part_index"] >= 1
    assert parsed["snippet"] and "GBK" in parsed["snippet"]


def test_parse_html_only():
    parsed = parse_message(MSG_3_FULL)
    assert parsed["text"] is None
    assert "Hello" in parsed["html"]
    assert parsed["snippet"] == "Hello World"


def test_snippet_from_html_strips_tags():
    assert make_snippet(None, "<b>a</b>  c") == "a c"
