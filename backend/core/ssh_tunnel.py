"""
共享 SSH 隧道模块
同时转发 MySQL 和 Redis，复用同一个 SSH 连接
"""

import os
import logging
import atexit
from pathlib import Path
from typing import Optional

# 确保 .env 被加载
try:
    from dotenv import load_dotenv
    _env_path = Path(__file__).parent.parent.parent / ".env"
    if _env_path.exists():
        load_dotenv(_env_path, override=False)
except ImportError:
    pass

logger = logging.getLogger(__name__)

_tunnel = None  # 全局隧道实例
_mysql_local_port: Optional[int] = None
_redis_local_port: Optional[int] = None
_initialized = False


def _start_tunnel():
    """
    如果配置了 SSH 环境变量，启动 SSH 隧道。
    支持同时转发 MySQL 和 Redis（共用一个 SSHTunnelForwarder）。

    环境变量:
        SSH_HOST: SSH 服务器地址
        SSH_PORT: SSH 端口（默认 22）
        SSH_USER: SSH 用户名
        SSH_PASSWORD: SSH 密码（与 SSH_KEY_FILE 二选一）
        SSH_KEY_FILE: SSH 私钥文件路径（与 SSH_PASSWORD 二选一）
        SSH_REMOTE_MYSQL_HOST: 远端 MySQL 地址（默认 127.0.0.1）
        SSH_REMOTE_MYSQL_PORT: 远端 MySQL 端口（默认 3306）
        SSH_REMOTE_REDIS_HOST: 远端 Redis 地址（默认 127.0.0.1）
        SSH_REMOTE_REDIS_PORT: 远端 Redis 端口（默认 6379）
    """
    global _tunnel, _mysql_local_port, _redis_local_port, _initialized
    _initialized = True

    ssh_host = os.getenv("SSH_HOST", "")
    if not ssh_host:
        return

    ssh_port = int(os.getenv("SSH_PORT", "22"))
    ssh_user = os.getenv("SSH_USER", "")
    ssh_password = os.getenv("SSH_PASSWORD", "")
    ssh_key_file = os.getenv("SSH_KEY_FILE", "")

    remote_mysql_host = os.getenv("SSH_REMOTE_MYSQL_HOST", "127.0.0.1")
    remote_mysql_port = int(os.getenv("SSH_REMOTE_MYSQL_PORT", "3306"))
    remote_redis_host = os.getenv("SSH_REMOTE_REDIS_HOST", "127.0.0.1")
    remote_redis_port = int(os.getenv("SSH_REMOTE_REDIS_PORT", "6379"))

    if not ssh_user:
        logger.warning("SSH_HOST 已配置但 SSH_USER 为空，跳过 SSH 隧道")
        return

    # 构建 remote_bind_addresses：MySQL 必选，Redis 仅在配置了时才加入
    remote_binds = [(remote_mysql_host, remote_mysql_port)]
    local_binds = [("127.0.0.1",)]  # 自动分配端口

    has_redis_tunnel = bool(os.getenv("SSH_REMOTE_REDIS_HOST") or os.getenv("SSH_REMOTE_REDIS_PORT"))
    if has_redis_tunnel:
        remote_binds.append((remote_redis_host, remote_redis_port))
        local_binds.append(("127.0.0.1",))

    try:
        from sshtunnel import SSHTunnelForwarder

        tunnel_kwargs = {
            "ssh_address_or_host": (ssh_host, ssh_port),
            "ssh_username": ssh_user,
            "remote_bind_addresses": remote_binds,
            "local_bind_addresses": local_binds,
        }

        if ssh_key_file and os.path.exists(ssh_key_file):
            tunnel_kwargs["ssh_pkey"] = ssh_key_file
            if ssh_password:
                tunnel_kwargs["ssh_private_key_password"] = ssh_password
        elif ssh_password:
            tunnel_kwargs["ssh_password"] = ssh_password
        else:
            logger.warning("SSH_PASSWORD 和 SSH_KEY_FILE 都未配置，跳过 SSH 隧道")
            return

        _tunnel = SSHTunnelForwarder(**tunnel_kwargs)
        _tunnel.start()

        # 读取分配到的本地端口
        _mysql_local_port = _tunnel.local_bind_ports[0]
        logger.info(
            f"SSH 隧道(MySQL): localhost:{_mysql_local_port} -> "
            f"{ssh_host}:{ssh_port} -> {remote_mysql_host}:{remote_mysql_port}"
        )

        if has_redis_tunnel:
            _redis_local_port = _tunnel.local_bind_ports[1]
            logger.info(
                f"SSH 隧道(Redis): localhost:{_redis_local_port} -> "
                f"{ssh_host}:{ssh_port} -> {remote_redis_host}:{remote_redis_port}"
            )

    except ImportError:
        logger.error("sshtunnel 未安装，请运行: pip install sshtunnel")
    except Exception as e:
        logger.error(f"SSH 隧道建立失败: {e}")


def _stop_tunnel():
    """关闭 SSH 隧道"""
    global _tunnel, _mysql_local_port, _redis_local_port
    if _tunnel:
        try:
            _tunnel.stop()
            logger.info("SSH 隧道已关闭")
        except Exception:
            pass
        _tunnel = None
        _mysql_local_port = None
        _redis_local_port = None


atexit.register(_stop_tunnel)


def _ensure_initialized():
    """确保隧道已初始化（懒初始化，只执行一次）"""
    global _initialized
    if not _initialized:
        _start_tunnel()


def get_mysql_local_port() -> Optional[int]:
    """获取 MySQL 隧道的本地端口，未配置 SSH 时返回 None"""
    _ensure_initialized()
    return _mysql_local_port


def get_redis_local_port() -> Optional[int]:
    """获取 Redis 隧道的本地端口，未配置 SSH 时返回 None"""
    _ensure_initialized()
    return _redis_local_port
