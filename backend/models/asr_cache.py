"""ASR 缓存模型 — video_md5 → utterances_url 映射"""

from sqlalchemy import Column, String
from .base import BaseModel


class ASRCache(BaseModel):
    __tablename__ = "asr_cache"

    video_md5 = Column(String(32), unique=True, index=True, nullable=False, comment="源视频文件 MD5")
    utterances_url = Column(String(500), nullable=False, comment="ASR 结果 JSON 的 MinIO URL")
