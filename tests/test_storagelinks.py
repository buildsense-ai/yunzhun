"""Unit tests for object-storage reference extraction."""
from __future__ import annotations

from app.mail.storagelinks import classify, extract_storage_refs


def test_aliyun_oss_virtual_host():
    ref = classify("https://mybucket.oss-cn-hangzhou.aliyuncs.com/data/file.csv")
    assert (ref.provider, ref.bucket, ref.key, ref.region, ref.presigned) == (
        "aliyun-oss", "mybucket", "data/file.csv", "cn-hangzhou", False,
    )


def test_aliyun_oss_internal_region():
    ref = classify("https://b.oss-cn-beijing-internal.aliyuncs.com/a/b.txt")
    assert (ref.provider, ref.region) == ("aliyun-oss", "cn-beijing")


def test_aliyun_oss_presigned():
    ref = classify(
        "https://bkt.oss-cn-shenzhen.aliyuncs.com/x.xlsx"
        "?Expires=1758000000&OSSAccessKeyId=KEY&Signature=abc"
    )
    assert ref.provider == "aliyun-oss"
    assert ref.presigned is True


def test_tencent_cos():
    ref = classify(
        "https://bkt-1250000000.cos.ap-guangzhou.myqcloud.com/reports/2026/summary.pdf"
        "?sign=q-sign-algorithm%3Dsha1"
    )
    assert (ref.provider, ref.bucket, ref.region, ref.presigned) == (
        "tencent-cos", "bkt-1250000000", "ap-guangzhou", True,
    )


def test_huawei_obs_virtual_and_path_style():
    v = classify("https://bktdir.obs.cn-north-4.myhuaweicloud.com/logs/a.log")
    assert (v.provider, v.bucket, v.region) == ("huawei-obs", "bktdir", "cn-north-4")
    p = classify("https://obs.cn-east-3.myhuaweicloud.com/otherbucket/x.bin")
    assert (p.provider, p.bucket, p.region) == ("huawei-obs", "otherbucket", "cn-east-3")


def test_aws_s3_variants():
    v = classify("https://mybucket.s3.us-west-2.amazonaws.com/a/b.json")
    assert (v.provider, v.bucket, v.region) == ("aws-s3", "mybucket", "us-west-2")
    cn = classify("https://doc-bucket.s3.cn-north-1.amazonaws.com.cn/k")
    assert (cn.provider, cn.region) == ("aws-s3", "cn-north-1")
    p = classify("https://s3.eu-central-1.amazonaws.com/bkt/obj")
    assert (p.provider, p.bucket, p.region) == ("aws-s3", "bkt", "eu-central-1")


def test_gcs_and_azure():
    g1 = classify("https://storage.googleapis.com/bkt/obj.txt")
    g2 = classify("https://bkt.storage.googleapis.com/obj.txt")
    assert g1.provider == g2.provider == "gcs"
    assert g1.bucket == "bkt" and g2.bucket == "bkt"
    a = classify("https://myacct.blob.core.windows.net/container/path/blob.txt")
    assert (a.provider, a.bucket, a.key) == ("azure-blob", "myacct/container", "path/blob.txt")


def test_s3_scheme_and_minio_presigned():
    s = classify("s3://assets/2026/q3.xlsx")
    assert (s.provider, s.bucket, s.key) == ("s3-compatible", "assets", "2026/q3.xlsx")
    m = classify("https://files.internal.example.com/data/r.xlsx?X-Amz-Signature=abc&X-Amz-Expires=3600")
    assert (m.provider, m.presigned) == ("s3-compatible", True)


def test_native_store_schemes():
    ref = classify("oss://novo-china-region/X101SC26023844-Z01/X101SC26023844-Z01-J039")
    assert (ref.provider, ref.bucket, ref.key) == (
        "aliyun-oss", "novo-china-region", "X101SC26023844-Z01/X101SC26023844-Z01-J039",
    )
    assert classify("cos://bkt-125000/path/obj").provider == "tencent-cos"
    assert classify("obs://bkt/dir/file").provider == "huawei-obs"


def test_cloud_drive_share_links():
    ref = classify("https://pan.sysu.edu.cn/link/AAD59C706CF8C5499880C854A7B76F4E11")
    assert (ref.provider, ref.bucket, ref.key) == (
        "cloud-drive", "pan.sysu.edu.cn", "link/AAD59C706CF8C5499880C854A7B76F4E11",
    )
    assert classify("https://pan.baidu.com/s/1abcDEF").provider == "cloud-drive"


def test_rejects_non_storage_urls():
    assert classify("https://example.com/file.pdf") is None
    assert classify("https://mail.163.com/js/main.js?v=1") is None
    assert classify("not a url") is None


def test_extract_from_text_and_html_dedup():
    text = "报表地址 https://bkt-1.cos.ap-shanghai.myqcloud.com/a.pdf?sign=q-sign-algorithm%3Dsha1 请查收."
    html = '<a href="https://bkt.oss-cn-hangzhou.aliyuncs.com/data/file.csv">下载</a>'
    refs = extract_storage_refs(text, html, text)  # duplicate source -> dedup
    assert len(refs) == 2
    assert {r.provider for r in refs} == {"tencent-cos", "aliyun-oss"}
    cos = next(r for r in refs if r.provider == "tencent-cos")
    assert cos.key == "a.pdf"  # trailing punctuation stripped, query excluded
