"""
通用工具函数
"""

from typing import Optional

from nonebot.adapters.onebot.v11 import Bot
from nonebot.log import logger


async def download_image(bot: Bot, image_url: str) -> Optional[bytes]:
    """
    下载图片

    Args:
        bot: NoneBot 实例
        image_url: 图片 URL 或文件 ID

    Returns:
        图片二进制数据，失败返回 None
    """
    import httpx

    try:
        # 如果是 HTTP URL，直接下载
        if image_url.startswith(("http://", "https://")):
            async with httpx.AsyncClient(timeout=30.0, trust_env=False) as client:
                resp = await client.get(image_url)
                resp.raise_for_status()
                return resp.content

        # 否则尝试通过 OneBot API 获取图片
        try:
            file_info = await bot.get_image(file=image_url)
            if file_info and file_info.get("url"):
                async with httpx.AsyncClient(timeout=30.0, trust_env=False) as client:
                    resp = await client.get(file_info["url"])
                    resp.raise_for_status()
                    return resp.content
        except Exception:
            pass

        # 尝试下载文件
        try:
            file_info = await bot.get_file(file_id=image_url)
            if file_info and file_info.get("file"):
                import aiofiles
                async with aiofiles.open(file_info["file"], "rb") as f:
                    return await f.read()
        except Exception:
            pass

        return None

    except Exception as e:
        logger.error(f"下载图片失败: {e}")
        return None
