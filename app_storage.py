# -*- coding: utf-8 -*-
"""app_storage.py — 站点功能规划第 050 项 · 素材存储抽象层。

设计：
  - 默认**本地后端**（行为与现在完全一致，零风险）；
  - 配齐 S3 兼容环境变量即切对象存储（Cloudflare R2 免备案，海外节点）：
        S3_ENDPOINT      https://<account>.r2.cloudflarestorage.com
        S3_KEY           Access Key ID
        S3_SECRET        Secret Access Key
        S3_BUCKET        桶名
        S3_PUBLIC_URL    桶的公开访问域名（r2.dev 或自定义域）
  - **双落**：put() 本地照写（留档 + 备份链路不断），配了 R2 再上传一份，
    返回 R2 公开 URL；读路径无需任何改动（拿到的就是外网 URL）。
  - 铁律：R2 任何一步失败都只回退本地 URL，绝不影响上传本身。

依赖：boto3 未安装时自动禁用 R2（pip install boto3 后重启生效）。
"""
import os

_local_only = True
_client = None
_bucket = None
_public = None


def _try_init():
    global _local_only, _client, _bucket, _public
    endpoint = (os.environ.get("S3_ENDPOINT") or "").strip()
    key = (os.environ.get("S3_KEY") or "").strip()
    secret = (os.environ.get("S3_SECRET") or "").strip()
    bucket = (os.environ.get("S3_BUCKET") or "").strip()
    public = (os.environ.get("S3_PUBLIC_URL") or "").strip().rstrip("/")
    if not (endpoint and key and secret and bucket):
        return
    try:
        import boto3  # noqa
        from botocore.config import Config
        _client = boto3.client(
            "s3", endpoint_url=endpoint,
            aws_access_key_id=key, aws_secret_access_key=secret,
            config=Config(connect_timeout=3, read_timeout=8, retries={"max_attempts": 1}),
            region_name="auto")
        _bucket = bucket
        _public = public or endpoint
        _local_only = False
    except Exception:
        _local_only = True


_try_init()


def enabled():
    """对象存储是否就绪。"""
    return not _local_only


def put(local_path, key, content_type=None):
    """本地文件已落盘后调用：配了 R3/R2 就同步上传，返回公开 URL；否则 None。

    任何失败都只返回 None（调用方继续用本地 URL）—— 上传永不因对象存储挂掉。
    """
    if _local_only or not _client:
        return None
    try:
        extra = {"ContentType": content_type} if content_type else {}
        _client.upload_file(local_path, _bucket, key, ExtraArgs=extra)
        return _public.rstrip("/") + "/" + key.lstrip("/")
    except Exception:
        return None
