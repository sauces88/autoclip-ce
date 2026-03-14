"""
数据库配置
包含数据库连接、会话管理和依赖注入
SSH 隧道逻辑已抽取到 ssh_tunnel.py
"""

import os
import logging
from pathlib import Path
from urllib.parse import urlparse, urlunparse

# 确保 .env 被加载
try:
    from dotenv import load_dotenv
    _env_path = Path(__file__).parent.parent.parent / ".env"
    if _env_path.exists():
        load_dotenv(_env_path, override=False)
except ImportError:
    pass
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool
from typing import Generator
from backend.models.base import Base

logger = logging.getLogger(__name__)


def _rewrite_database_url(url: str, local_port: int) -> str:
    """
    将 DATABASE_URL 中的 host:port 替换为 localhost:local_port（SSH 隧道端口）。
    """
    parsed = urlparse(url)
    new_netloc = f"{parsed.username}"
    if parsed.password:
        new_netloc = f"{parsed.username}:{parsed.password}"
    new_netloc = f"{new_netloc}@127.0.0.1:{local_port}"

    new_url = urlunparse((
        parsed.scheme, new_netloc, parsed.path,
        parsed.params, parsed.query, parsed.fragment,
    ))
    return new_url


# ─────────────────── 数据库配置 ───────────────────

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "sqlite:///autoclip.db"
)

# 如果没有设置环境变量，使用配置函数获取数据库URL
if DATABASE_URL == "sqlite:///autoclip.db":
    try:
        from .config import get_database_url
        DATABASE_URL = get_database_url()
    except ImportError:
        pass

# 如果配置了 SSH 隧道，重写 DATABASE_URL
from .ssh_tunnel import get_mysql_local_port
_tunnel_port = get_mysql_local_port()
if _tunnel_port and "sqlite" not in DATABASE_URL:
    DATABASE_URL = _rewrite_database_url(DATABASE_URL, _tunnel_port)
    logger.info(f"DATABASE_URL 已通过 SSH 隧道重写: host -> 127.0.0.1:{_tunnel_port}")

# 创建数据库引擎
if "sqlite" in DATABASE_URL:
    engine = create_engine(
        DATABASE_URL,
        connect_args={
            "check_same_thread": False,
            "timeout": 30
        },
        poolclass=StaticPool,
        pool_pre_ping=True,
        echo=False
    )
else:
    engine = create_engine(
        DATABASE_URL,
        pool_pre_ping=True,
        pool_recycle=300,
        pool_size=20,
        max_overflow=100,
        echo=False
    )

# 创建会话工厂
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)


def get_db() -> Generator[Session, None, None]:
    """数据库会话依赖注入"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_tables():
    """创建所有数据库表"""
    Base.metadata.create_all(bind=engine)


def drop_tables():
    """删除所有数据库表"""
    Base.metadata.drop_all(bind=engine)


def reset_database():
    """重置数据库"""
    drop_tables()
    create_tables()


def test_connection() -> bool:
    """测试数据库连接"""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1")).fetchone()
        return True
    except Exception as e:
        print(f"数据库连接测试失败: {e}")
        return False


def init_database():
    """初始化数据库"""
    print("正在初始化数据库...")

    if not test_connection():
        print("数据库连接失败")
        return False

    try:
        create_tables()
        print("数据库表创建成功")
        return True
    except Exception as e:
        print(f"数据库表创建失败: {e}")
        return False


if __name__ == "__main__":
    init_database()
