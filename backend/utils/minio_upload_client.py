"""
MinIO 直连上传客户端

直接使用 MinIO SDK 上传文件。
路径规则: {public|private}/年/月/日/{时间戳}_{原文件名}

环境变量:
    MINIO_ENDPOINT: MinIO 地址（如 minio-us.gealam.com）
    MINIO_ACCESS_KEY: Access Key
    MINIO_SECRET_KEY: Secret Key
    MINIO_SECURE: 是否使用 HTTPS（默认 true）
    MINIO_BUCKET: 存储桶名称（默认 data）
"""

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path

from minio import Minio

logger = logging.getLogger(__name__)

_client = None
_bucket_ready = False


def _get_client() -> Minio:
    """获取 MinIO 客户端（单例）"""
    global _client
    if _client is not None:
        return _client

    raw_endpoint = os.getenv("MINIO_ENDPOINT", "")
    access_key = os.getenv("MINIO_ACCESS_KEY", "")
    secret_key = os.getenv("MINIO_SECRET_KEY", "")
    secure = os.getenv("MINIO_SECURE", "true").lower() == "true"

    if not raw_endpoint or not access_key or not secret_key:
        raise RuntimeError(
            "MinIO 配置不完整，请检查 MINIO_ENDPOINT, MINIO_ACCESS_KEY, MINIO_SECRET_KEY"
        )

    endpoint = raw_endpoint.replace("https://", "").replace("http://", "").rstrip("/")
    _client = Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=secure)
    return _client


def _get_bucket() -> str:
    return os.getenv("MINIO_BUCKET", "data")


def _ensure_bucket(client: Minio, bucket: str):
    """确保存储桶存在，public/ 前缀设为公开读"""
    global _bucket_ready
    if _bucket_ready:
        return

    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)
        logger.info(f"已创建存储桶 {bucket}")

    # 仅 public/ 前缀公开读，private/ 不公开
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"AWS": ["*"]},
                "Action": ["s3:GetObject"],
                "Resource": [f"arn:aws:s3:::{bucket}/public/*"],
            }
        ],
    }
    client.set_bucket_policy(bucket, json.dumps(policy))
    _bucket_ready = True


def _build_object_name(file_path: Path, access: str) -> str:
    """
    构建对象路径: {access}/年/月/日/{时间戳}_{原文件名}
    """
    now = datetime.now()
    ts = int(time.time() * 1000)
    date_path = now.strftime("%Y/%m/%d")
    filename = file_path.name
    return f"{access}/{date_path}/{ts}_{filename}"


def _build_url(endpoint: str, secure: bool, bucket: str, object_name: str) -> str:
    scheme = "https" if secure else "http"
    return f"{scheme}://{endpoint}/{bucket}/{object_name}"


def upload_file(file_path: Path, timeout: int = 120, public: bool = True) -> str:
    """
    上传文件到 MinIO，返回 URL。

    Args:
        file_path: 本地文件路径
        timeout: 未使用（保留接口兼容）
        public: True 上传到 public/（可直接访问），False 上传到 private/

    Returns:
        文件的 URL

    Raises:
        RuntimeError: 上传失败
    """
    file_path = Path(file_path)
    if not file_path.exists():
        raise RuntimeError(f"文件不存在: {file_path}")

    client = _get_client()
    bucket = _get_bucket()
    _ensure_bucket(client, bucket)

    raw_endpoint = os.getenv("MINIO_ENDPOINT", "")
    endpoint = raw_endpoint.replace("https://", "").replace("http://", "").rstrip("/")
    secure = os.getenv("MINIO_SECURE", "true").lower() == "true"

    access = "public" if public else "private"
    object_name = _build_object_name(file_path, access)

    file_size = file_path.stat().st_size
    logger.info(f"上传文件: {file_path.name} ({file_size / 1024 / 1024:.1f}MB) -> {object_name}")

    client.fput_object(bucket, object_name, str(file_path))

    url = _build_url(endpoint, secure, bucket, object_name)
    logger.info(f"上传完成: {url}")
    return url
