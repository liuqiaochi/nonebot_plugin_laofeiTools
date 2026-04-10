"""
插件配置
"""

from typing import Set

from pydantic import BaseModel


class Config(BaseModel):
    """插件配置类"""
    
    # 超级用户列表（开启/关闭功能的权限）
    laofei_superusers: Set[str] = set()
    
    # 默认开启搜图的群聊（为空表示默认关闭）
    laofei_search_enabled_groups: Set[str] = set()


# 运行时状态存储
_enabled_groups: Set[str] = set()


def is_group_enabled(group_id: str) -> bool:
    """检查群聊是否开启了搜图功能"""
    return group_id in _enabled_groups


def enable_group(group_id: str) -> None:
    """开启群聊搜图功能"""
    _enabled_groups.add(group_id)


def disable_group(group_id: str) -> None:
    """关闭群聊搜图功能"""
    _enabled_groups.discard(group_id)


def init_enabled_groups(default_groups: Set[str]) -> None:
    """初始化默认开启的群聊"""
    global _enabled_groups
    _enabled_groups = set(default_groups)
