"""
插件配置
"""

import json
import os
from pathlib import Path
from typing import Set

from nonebot import get_driver
from pydantic import BaseModel


class Config(BaseModel):
    """插件配置类"""
    
    # 超级用户列表（开启/关闭功能的权限）
    laofei_superusers: Set[str] = set()
    
    # 默认开启搜图的群聊（为空表示默认关闭）
    laofei_search_enabled_groups: Set[str] = set()


# 数据文件路径
DATA_DIR = Path("data/laofei_tools")
DATA_FILE = DATA_DIR / "enabled_groups.json"
POINTS_DISABLED_FILE = DATA_DIR / "points_disabled_groups.json"


def _ensure_data_dir():
    """确保数据目录存在"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def _load_enabled_groups() -> Set[str]:
    """从文件加载已开启的群聊列表"""
    _ensure_data_dir()
    if DATA_FILE.exists():
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return set(data.get("enabled_groups", []))
        except Exception:
            return set()
    return set()


def _save_enabled_groups(groups: Set[str]):
    """保存已开启的群聊列表到文件"""
    _ensure_data_dir()
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump({"enabled_groups": list(groups)}, f, ensure_ascii=False, indent=2)
    except Exception as e:
        from nonebot.log import logger
        logger.error(f"保存群聊配置失败: {e}")


# ========== 积分系统开关 ==========

def _load_points_disabled_groups() -> Set[str]:
    """从文件加载已关闭积分系统的群聊列表"""
    _ensure_data_dir()
    if POINTS_DISABLED_FILE.exists():
        try:
            with open(POINTS_DISABLED_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return set(data.get("disabled_groups", []))
        except Exception:
            return set()
    return set()


def _save_points_disabled_groups(groups: Set[str]):
    """保存已关闭积分系统的群聊列表到文件"""
    _ensure_data_dir()
    try:
        with open(POINTS_DISABLED_FILE, "w", encoding="utf-8") as f:
            json.dump({"disabled_groups": list(groups)}, f, ensure_ascii=False, indent=2)
    except Exception as e:
        from nonebot.log import logger
        logger.error(f"保存积分系统配置失败: {e}")


# 运行时状态存储（从文件加载）
_enabled_groups: Set[str] = _load_enabled_groups()
_points_disabled_groups: Set[str] = _load_points_disabled_groups()


def is_group_enabled(group_id: str) -> bool:
    """检查群聊是否开启了搜图功能"""
    return group_id in _enabled_groups


def enable_group(group_id: str) -> None:
    """开启群聊搜图功能"""
    _enabled_groups.add(group_id)
    _save_enabled_groups(_enabled_groups)


def disable_group(group_id: str) -> None:
    """关闭群聊搜图功能"""
    _enabled_groups.discard(group_id)
    _save_enabled_groups(_enabled_groups)


def is_points_enabled(group_id: str) -> bool:
    """检查群聊是否开启了积分系统（默认开启）"""
    return group_id not in _points_disabled_groups


def enable_points(group_id: str) -> None:
    """开启群聊积分系统"""
    _points_disabled_groups.discard(group_id)
    _save_points_disabled_groups(_points_disabled_groups)


def disable_points(group_id: str) -> None:
    """关闭群聊积分系统"""
    _points_disabled_groups.add(group_id)
    _save_points_disabled_groups(_points_disabled_groups)


def init_enabled_groups(default_groups: Set[str]) -> None:
    """初始化默认开启的群聊（仅在数据文件不存在时生效）"""
    global _enabled_groups
    # 如果文件已存在，不覆盖已有数据
    if DATA_FILE.exists():
        _enabled_groups = _load_enabled_groups()
    else:
        _enabled_groups = set(default_groups)
        _save_enabled_groups(_enabled_groups)
