"""Checksum extraction: find "<md5> <filename>" pairs in delivery mail text.

Delivery mails (Novogene etc.) often embed per-file MD5s — as tables
("xxx.fastq.gz  d41d8cd9..."), "md5: xxx" lines, or "md5(file)=xxx".
"""
from __future__ import annotations

import re

_MD5 = r"[0-9a-fA-F]{32}"

# "d41d8cd98f00b204e9800998ecf8427e  sample_R1.fastq.gz" (md5 then filename)
_MD5_THEN_NAME = re.compile(rf"\b({_MD5})\b[\s:=*]+([\w.\-]+\.(?:fastq|fq|gz|tar|zip|bz2|csv|tsv|txt|pdf|h5|loom|bam|bai))", re.I)
# "sample_R1.fastq.gz  md5: d41d8cd9..." (filename then md5)
_NAME_THEN_MD5 = re.compile(rf"([\w.\-]+\.(?:fastq|fq|gz|tar|zip|bz2|csv|tsv|txt|pdf|h5|loom|bam|bai))[\s:=*]+(?:md5[\s:=]*)?({_MD5})", re.I)
# "md5(sample.tar) = d41d8cd9..." (function-call form)
_MD5_FUNC = re.compile(rf"md5\s*\(\s*([\w.\-]+\.(?:fastq|fq|gz|tar|zip|bz2|csv|tsv|txt|pdf|h5|loom|bam|bai))\s*\)\s*[=:]\s*({_MD5})", re.I)


def extract_checksums(*sources: str | None) -> dict[str, str]:
    """Return {filename: md5_hex_lower} found across the sources."""
    out: dict[str, str] = {}
    for src in sources:
        if not src:
            continue
        for md5, name in _MD5_THEN_NAME.findall(src):
            out.setdefault(name, md5.lower())
        for name, md5 in _NAME_THEN_MD5.findall(src):
            out.setdefault(name, md5.lower())
        for name, md5 in _MD5_FUNC.findall(src):
            out.setdefault(name, md5.lower())
    return out
