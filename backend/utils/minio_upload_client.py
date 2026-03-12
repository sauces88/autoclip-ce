"""
MinIO 分片上传客户端

通过 Java 后端 API 将文件上传到 MinIO，获取公网 URL。

API 流程（参考 SliceUploadTaskController.java）：
  1. GET  /slice/tasks/{identifier}              — 检查任务是否存在（秒传）
  2. POST /slice/tasks                            — 初始化上传任务
  3. GET  /slice/tasks/{identifier}/{partNumber}   — 获取分片预签名上传 URL
  4. PUT  预签名URL                                — 直接上传分片到 MinIO
  5. POST /slice/tasks/merge/{identifier}          — 合并分片

支持断点续传（exitPartList 跳过已上传分片）。
"""

import hashlib
import logging
import os
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

CHUNK_SIZE = 5 * 1024 * 1024  # 5MB per chunk


def _calc_md5(file_path: Path) -> str:
    """计算文件 MD5"""
    md5 = hashlib.md5()
    with open(file_path, "rb") as f:
        while True:
            chunk = f.read(8192)
            if not chunk:
                break
            md5.update(chunk)
    return md5.hexdigest()


def _convert_url(path: str) -> str:
    """将 path 转为完整 URL（参考前端 convertUrl）"""
    if path.startswith("http"):
        return path
    return f"https://{path}"


def upload_file(file_path: Path, timeout: int = 120) -> str:
    """
    上传文件到 MinIO（通过 Java 后端），返回公网 URL。

    Args:
        file_path: 本地文件路径
        timeout: 单次 HTTP 请求超时（秒）

    Returns:
        文件的公网可访问 URL

    Raises:
        RuntimeError: 上传失败
    """
    api_base = os.getenv("MINIO_UPLOAD_API_BASE", "").rstrip("/")
    if not api_base:
        raise RuntimeError("MINIO_UPLOAD_API_BASE 未配置")

    file_path = Path(file_path)
    if not file_path.exists():
        raise RuntimeError(f"文件不存在: {file_path}")

    file_size = file_path.stat().st_size
    file_name = file_path.name
    identifier = _calc_md5(file_path)

    logger.info(f"上传文件: {file_name} ({file_size / 1024 / 1024:.1f}MB) identifier={identifier}")

    # ── 1. 检查任务是否已存在（秒传） ──
    task_info = None
    check_url = f"{api_base}/slice/tasks/{identifier}"
    check_resp = requests.get(check_url, timeout=timeout)
    check_resp.raise_for_status()
    check_data = check_resp.json()
    if check_data.get("code") == 200 and check_data.get("data"):
        task_info = check_data["data"]

    # ── 2. 如果任务不存在，初始化 ──
    if not task_info:
        init_url = f"{api_base}/slice/tasks"
        init_resp = requests.post(
            init_url,
            json={
                "identifier": identifier,
                "fileName": file_name,
                "totalSize": file_size,
                "chunkSize": CHUNK_SIZE,
            },
            timeout=timeout,
        )
        init_resp.raise_for_status()
        init_data = init_resp.json()
        if init_data.get("code") != 200:
            raise RuntimeError(f"初始化上传任务失败: {init_data}")
        task_info = init_data.get("data", {})

    # 秒传：文件已上传完成
    if task_info.get("finished"):
        path = task_info.get("path", "")
        url = _convert_url(path)
        logger.info(f"秒传成功，文件已存在: {url}")
        return url

    task_record = task_info.get("taskRecord", {})
    path = task_info.get("path", "")

    # 已上传的分片编号集合（断点续传）
    exit_part_list = task_record.get("exitPartList") or []
    exit_part_numbers = {p.get("partNumber") for p in exit_part_list if isinstance(p, dict)}
    if exit_part_numbers:
        logger.info(f"断点续传: 跳过已上传的 {len(exit_part_numbers)} 个分片")

    chunk_num = task_record.get("chunkNum", 0)
    if chunk_num <= 0:
        chunk_num = (file_size + CHUNK_SIZE - 1) // CHUNK_SIZE

    # ── 3. 分片上传：获取预签名 URL → PUT 到 MinIO ──
    with open(file_path, "rb") as f:
        for part_number in range(1, chunk_num + 1):
            chunk_data = f.read(CHUNK_SIZE)
            if not chunk_data:
                break

            if part_number in exit_part_numbers:
                logger.debug(f"分片 {part_number}/{chunk_num} 已存在，跳过")
                continue

            # 获取预签名上传 URL
            presign_url = f"{api_base}/slice/tasks/{identifier}/{part_number}"
            presign_resp = requests.get(presign_url, timeout=timeout)
            presign_resp.raise_for_status()
            presign_data = presign_resp.json()
            if presign_data.get("code") != 200 or not presign_data.get("data"):
                raise RuntimeError(
                    f"获取分片 {part_number} 预签名URL失败: {presign_data}"
                )
            upload_url = presign_data["data"]

            # PUT 直接上传到 MinIO
            logger.info(f"上传分片 {part_number}/{chunk_num}")
            put_resp = requests.put(
                upload_url,
                data=chunk_data,
                headers={"Content-Type": "application/octet-stream"},
                timeout=timeout,
            )
            put_resp.raise_for_status()

    # ── 4. 合并分片 ──
    merge_url = f"{api_base}/slice/tasks/merge/{identifier}"
    merge_resp = requests.post(merge_url, timeout=timeout)
    merge_resp.raise_for_status()
    merge_data = merge_resp.json()
    if merge_data.get("code") != 200:
        raise RuntimeError(f"合并失败: {merge_data}")

    url = _convert_url(path)
    logger.info(f"上传完成: {url}")
    return url
