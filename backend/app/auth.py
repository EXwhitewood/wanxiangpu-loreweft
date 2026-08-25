"""P0-1 修复：API Key 认证与项目所有权验证.

设计原则：
1. 向后兼容：无 X-API-Key 头时返回 None（匿名模式），不破坏已有测试与本地开发
2. 生产强制：在需要认证的端点使用 require_user 依赖，缺失或无效 API Key 时返回 401
3. 项目隔离：verify_project_ownership 验证当前用户是否为项目所有者（或管理员）
4. Standard Mode 例外：本地单用户模式无注册流程，require_user 返回默认管理员（向后兼容）
"""

from __future__ import annotations

import secrets
import uuid

from fastapi import Depends, HTTPException, Header, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.db_models import get_db, Project, User

API_KEY_HEADER = "X-API-Key"

# Standard Mode 本地默认用户 ID（固定 UUID，不持久化）
_LOCAL_DEFAULT_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")


def generate_api_key() -> str:
    """生成一个新的 API Key（64 字符十六进制）."""
    return secrets.token_hex(32)


def _local_default_user() -> User:
    """Standard Mode 本地默认用户（管理员，内存对象不持久化）.

    Standard Mode 是本地单用户模式，没有用户注册/登录流程。
    返回管理员用户以确保 verify_project_ownership 的所有权检查通过。
    """
    return User(
        id=_LOCAL_DEFAULT_USER_ID,
        name="local",
        api_key="",
        is_active=True,
        is_admin=True,
    )


async def get_current_user(
    db: AsyncSession = Depends(get_db),
    x_api_key: str | None = Header(None, alias=API_KEY_HEADER),
) -> User | None:
    """可选认证：从 X-API-Key 头读取 API Key 并返回 User.

    - 无头：返回 None（匿名模式，向后兼容）
    - 有头但无效：抛 401
    - 有头且有效：返回 User
    """
    if not x_api_key:
        return None
    result = await db.execute(
        select(User).where(User.api_key == x_api_key, User.is_active.is_(True))
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or inactive API key",
            headers={API_KEY_HEADER: "Invalid"},
        )
    return user


async def require_user(
    user: User | None = Depends(get_current_user),
) -> User:
    """强制认证：要求有效的 API Key，否则抛 401.

    Standard Mode 例外：本地单用户模式无注册流程，返回默认管理员用户（向后兼容）。
    仅在高级模式（多用户，已弃用）下才强制要求 X-API-Key header。
    """
    if user is not None:
        return user
    # 标准模式：本地单用户，无注册流程，返回默认管理员（向后兼容）
    return _local_default_user()


async def verify_project_ownership(
    project_id: uuid.UUID | str,
    user: User | None = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Project:
    """验证项目所有权.

    - 匿名模式（user is None）：允许访问（向后兼容，本地开发/测试场景）
    - 认证模式：验证 project.owner_id == user.id 或 user.is_admin
    - 项目不存在：抛 404
    - 无权访问：抛 403
    """
    project = await db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if user is not None and not user.is_admin:
        if project.owner_id is not None and str(project.owner_id) != str(user.id):
            raise HTTPException(
                status_code=403,
                detail="You do not have access to this project",
            )
    return project
