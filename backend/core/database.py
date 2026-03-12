"""
数据库配置
包含数据库连接、会话管理、SSH 隧道和依赖注入
"""

import os
import logging
import atexit
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
from typing import Generator, Optional
from backend.models.base import Base

logger = logging.getLogger(__name__)

# ─────────────────── SSH 隧道 ───────────────────

_ssh_tunnel = None  # 全局隧道实例


def _start_ssh_tunnel() -> Optional[int]:
    """
    如果配置了 SSH 环境变量，启动 SSH 隧道并返回本地端口。
    否则返回 None。

    环境变量:
        SSH_HOST: SSH 服务器地址
        SSH_PORT: SSH 端口（默认 22）
        SSH_USER: SSH 用户名
        SSH_PASSWORD: SSH 密码（与 SSH_KEY_FILE 二选一）
        SSH_KEY_FILE: SSH 私钥文件路径（与 SSH_PASSWORD 二选一）
        SSH_REMOTE_MYSQL_HOST: 远端 MySQL 地址（默认 127.0.0.1）
        SSH_REMOTE_MYSQL_PORT: 远端 MySQL 端口（默认 3306）
    """
    global _ssh_tunnel

    ssh_host = os.getenv("SSH_HOST", "")
    if not ssh_host:
        return None

    ssh_port = int(os.getenv("SSH_PORT", "22"))
    ssh_user = os.getenv("SSH_USER", "")
    ssh_password = os.getenv("SSH_PASSWORD", "")
    ssh_key_file = os.getenv("SSH_KEY_FILE", "")
    remote_mysql_host = os.getenv("SSH_REMOTE_MYSQL_HOST", "127.0.0.1")
    remote_mysql_port = int(os.getenv("SSH_REMOTE_MYSQL_PORT", "3306"))

    if not ssh_user:
        logger.warning("SSH_HOST 已配置但 SSH_USER 为空，跳过 SSH 隧道")
        return None

    try:
        from sshtunnel import SSHTunnelForwarder

        tunnel_kwargs = {
            "ssh_address_or_host": (ssh_host, ssh_port),
            "ssh_username": ssh_user,
            "remote_bind_address": (remote_mysql_host, remote_mysql_port),
            "local_bind_address": ("127.0.0.1",),  # 自动分配本地端口
        }

        if ssh_key_file and os.path.exists(ssh_key_file):
            tunnel_kwargs["ssh_pkey"] = ssh_key_file
            if ssh_password:
                tunnel_kwargs["ssh_private_key_password"] = ssh_password
        elif ssh_password:
            tunnel_kwargs["ssh_password"] = ssh_password
        else:
            logger.warning("SSH_PASSWORD 和 SSH_KEY_FILE 都未配置，跳过 SSH 隧道")
            return None

        _ssh_tunnel = SSHTunnelForwarder(**tunnel_kwargs)
        _ssh_tunnel.start()

        local_port = _ssh_tunnel.local_bind_port
        logger.info(
            f"SSH 隧道已建立: localhost:{local_port} -> "
            f"{ssh_host}:{ssh_port} -> {remote_mysql_host}:{remote_mysql_port}"
        )
        return local_port

    except ImportError:
        logger.error("sshtunnel 未安装，请运行: pip install sshtunnel")
        return None
    except Exception as e:
        logger.error(f"SSH 隧道建立失败: {e}")
        return None


def _stop_ssh_tunnel():
    """关闭 SSH 隧道"""
    global _ssh_tunnel
    if _ssh_tunnel:
        try:
            _ssh_tunnel.stop()
            logger.info("SSH 隧道已关闭")
        except Exception:
            pass
        _ssh_tunnel = None


# 进程退出时自动关闭隧道
atexit.register(_stop_ssh_tunnel)


def _rewrite_database_url(url: str, local_port: int) -> str:
    """
    将 DATABASE_URL 中的 host:port 替换为 localhost:local_port（SSH 隧道端口）。
    """
    parsed = urlparse(url)
    # 替换 host 和 port
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
_tunnel_port = _start_ssh_tunnel()
if _tunnel_port and "sqlite" not in DATABASE_URL:
    _original_url = DATABASE_URL
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
