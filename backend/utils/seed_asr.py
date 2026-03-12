"""
Seed ASR AUC 客户端 — 录音文件识别标准版（异步 API）

参考实现：auc_websocket_demo.py
流程：submit(音频URL) → 轮询 query → 返回 utterances（含 word 级时间戳）

关键：
- submit 时自定义 X-Api-Request-Id（UUID），该值即为 task_id
- query 时复用同一个 X-Api-Request-Id + submit 返回的 X-Tt-Logid
- 必须带 X-Api-Sequence: "-1"
"""

import json
import logging
import os
import time
import uuid
from typing import Dict, List

import requests

logger = logging.getLogger(__name__)

# 轮询配置
POLL_INTERVAL_S = 15         # 轮询间隔
POLL_TIMEOUT_S = 30 * 60     # 最长等待 30 分钟

# ASR 状态码
STATUS_SUCCESS = "20000000"
STATUS_PROCESSING = {"20000001", "20000002"}


def _base_headers() -> Dict[str, str]:
    """基础 headers（不含 X-Api-Request-Id）"""
    return {
        "X-Api-App-Key": os.getenv("VOLCENGINE_ASR_APP_ID", ""),
        "X-Api-Access-Key": os.getenv("VOLCENGINE_ASR_ACCESS_TOKEN", ""),
        "X-Api-Resource-Id": os.getenv("VOLCENGINE_ASR_CLUSTER", "volc.seedasr.auc"),
        "X-Api-Sequence": "-1",
    }


def _submit(audio_url: str) -> tuple:
    """提交音频 URL 到 Seed ASR，返回 (task_id, x_tt_logid)。"""
    submit_url = os.getenv(
        "VOLCENGINE_ASR_SUBMIT_URL",
        "https://openspeech-direct.zijieapi.com/api/v3/auc/bigmodel/submit",
    )
    app_id = os.getenv("VOLCENGINE_ASR_APP_ID", "")

    task_id = str(uuid.uuid4())

    headers = _base_headers()
    headers["X-Api-Request-Id"] = task_id

    body = {
        "user": {"uid": app_id},
        "audio": {
            "url": audio_url,
            "format": "mp3",
        },
        "request": {
            "model_name": "bigmodel",
            "show_utterances": True,
            "enable_ddc": True,
            "enable_punc": True,
            "enable_itn": True,
            "enable_speaker_info": True,
            "corpus": {
                "boosting_table_name": "chaogesuv",
                "correct_table_name": "chaogesuv",
            },
        },
    }

    logger.info(f"Seed ASR 提交: {audio_url} (task_id={task_id})")
    resp = requests.post(submit_url, data=json.dumps(body), headers=headers, timeout=30)

    status_code = resp.headers.get("X-Api-Status-Code", "")
    if status_code != STATUS_SUCCESS:
        msg = resp.headers.get("X-Api-Message", resp.text[:500])
        raise RuntimeError(f"Seed ASR 提交失败: [{status_code}] {msg}")

    x_tt_logid = resp.headers.get("X-Tt-Logid", "")
    logger.info(f"Seed ASR 任务已提交: task_id={task_id}, X-Tt-Logid={x_tt_logid}")
    return task_id, x_tt_logid


def _query(task_id: str, x_tt_logid: str) -> List[Dict]:
    """轮询 Seed ASR 结果，返回 utterances 列表。"""
    query_url = os.getenv(
        "VOLCENGINE_ASR_QUERY_URL",
        "https://openspeech-direct.zijieapi.com/api/v3/auc/bigmodel/query",
    )

    headers = _base_headers()
    headers["X-Api-Request-Id"] = task_id
    if x_tt_logid:
        headers["X-Tt-Logid"] = x_tt_logid

    deadline = time.time() + POLL_TIMEOUT_S
    poll_n = 0

    while time.time() < deadline:
        poll_n += 1
        logger.info(f"Seed ASR 查询第 {poll_n} 次: task_id={task_id}")

        resp = requests.post(query_url, data=json.dumps({}), headers=headers, timeout=30)
        status_code = resp.headers.get("X-Api-Status-Code", "")

        if status_code == STATUS_SUCCESS:
            result = resp.json() if resp.text.strip() else {}
            utterances = result.get("result", {}).get("utterances", [])
            logger.info(f"Seed ASR 完成: 共 {len(utterances)} 条 utterance")
            return utterances

        if status_code in STATUS_PROCESSING:
            logger.info(f"Seed ASR 处理中（{status_code}），{POLL_INTERVAL_S}s 后重试...")
            time.sleep(POLL_INTERVAL_S)
            continue

        msg = resp.headers.get("X-Api-Message", resp.text[:500])
        raise RuntimeError(f"Seed ASR 查询失败: [{status_code}] {msg}")

    raise RuntimeError(f"Seed ASR 超时（>{POLL_TIMEOUT_S}s），task_id={task_id}")


def transcribe(audio_url: str) -> List[Dict]:
    """
    完整的 ASR 转录流程：submit → poll → utterances。

    Args:
        audio_url: 音频文件的公网 URL

    Returns:
        utterances 列表，每条包含 text / start_time / end_time / words 等字段
    """
    task_id, x_tt_logid = _submit(audio_url)
    return _query(task_id, x_tt_logid)
