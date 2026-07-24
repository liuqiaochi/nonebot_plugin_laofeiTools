"""
数据文件安全写入工具

提供 safe_json_save 函数，所有持久化保存必须经过此函数：
1. 先写 .tmp 再 rename（原子操作），防止写入中途崩溃导致文件损坏
2. 可选空缓存保护，防止内存缓存为空时覆盖已有数据文件
"""

import json
from pathlib import Path
from typing import Any

from nonebot.log import logger


def safe_json_save(
    file_path: Path,
    data: Any,
    *,
    cache_is_empty: bool = False,
    cache_name: str = "",
) -> None:
    """安全保存 JSON 数据，替代直接 open().write(json.dump())

    Args:
        file_path: 目标文件路径
        data: 要保存的数据
        cache_is_empty: 内存缓存是否为空。为 True 时绝不写入，防止空缓存覆盖已有数据
        cache_name: 缓存名称，用于日志输出（可选）
    """
    file_path = Path(file_path)
    file_path.parent.mkdir(parents=True, exist_ok=True)

    fname = file_path.name
    label = f"{cache_name} " if cache_name else ""

    # --- 空缓存保护 ---
    # 这是预防数据丢失的最后一道防线。任何情况下，如果内存缓存为空，
    # 就绝不写入文件。无论是文件存在（防止误清空）还是不存在（路径
    # 变更导致加载失败，此时写入空数据会永久丢失原数据），都拒绝写入。
    if cache_is_empty:
        if file_path.exists():
            logger.warning(
                f"[数据保护] {label}缓存为空但 {fname} 已存在，"
                f"跳过保存以防误清空"
            )
        else:
            logger.warning(
                f"[数据保护] {label}缓存为空且 {fname} 不存在，"
                f"跳过写入空数据（可能是路径变更导致加载失败）"
            )
        return

    # --- 原子写入：先写 .tmp，成功后再 rename ---
    tmp_path = file_path.with_name(fname + ".tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        tmp_path.replace(file_path)
    except Exception as e:
        logger.error(f"[数据保护] 写入 {fname} 失败: {e}")
        # 清理残留的 tmp 文件
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass
