"""
图片超分（增强清晰度）功能

指令：
    lg超分 - 引用图片进行高清化处理，让模糊图片变清晰

使用 DeepAI API（torch-srgan 模型）进行超分辨率处理
"""

import os
import uuid
from io import BytesIO
from typing import Optional

import httpx
from nonebot import get_driver, on_command
from nonebot.adapters.onebot.v11 import (
    Bot,
    Message,
    MessageEvent,
    MessageSegment,
)
from nonebot.log import logger
from nonebot.matcher import Matcher

from ..config import DATA_DIR
from .utils import download_image

DEEPAI_API_KEY = getattr(get_driver().config, "longge_deepai_api_key", "")
DEEPAI_API_URL = "https://api.deepai.org/api/torch-srgan"

# 临时文件目录
ENHANCE_DIR = DATA_DIR / "enhance"

# ========== lg超分指令 ==========
enhance_cmd = on_command(
    "lg超分",
    priority=5,
    block=True,
    force_whitespace=True,
)


@enhance_cmd.handle()
async def handle_enhance(
    matcher: Matcher,
    bot: Bot,
    event: MessageEvent,
):
    """处理图片超分指令"""

    # 检查 API Key
    if not DEEPAI_API_KEY:
        await matcher.finish("超分功能未配置 API Key，请联系管理员")

    # 1. 检查是否引用了消息
    if not event.reply:
        await matcher.finish("请引用一张图片后发送「lg超分」")

    # 2. 获取被引用消息中的图片
    image_url: Optional[str] = None
    for seg in event.reply.message:
        if seg.type == "image":
            image_url = seg.data.get("url") or seg.data.get("file")
            break

    if not image_url:
        await matcher.finish("引用的消息中没有图片，请引用一张图片后发送「lg超分」")

    # 3. 发送等待提示
    await matcher.send("正在处理图片，请稍候...")

    try:
        # 4. 下载图片
        image_data = await download_image(bot, image_url)
        if not image_data:
            await matcher.finish("图片下载失败，请重试")

        logger.info(f"超分图片下载成功，大小: {len(image_data)} bytes")

        # 5. 调用 DeepAI API 超分
        output_path = await enhance_image(image_data)
        if not output_path:
            await matcher.finish("图片处理失败，可能是 API 限流，请稍后重试")

        # 6. 发送处理后的图片
        try:
            await matcher.send(Message([
                MessageSegment.reply(event.message_id),
                MessageSegment.image(f"file://{output_path}"),
                MessageSegment.text("\n已超分处理，图片更清晰了"),
            ]))
        finally:
            # 7. 清理临时文件
            try:
                if os.path.exists(output_path):
                    os.remove(output_path)
            except Exception as e:
                logger.warning(f"清理临时文件失败: {e}")

    except Exception as e:
        logger.exception("图片超分处理失败")
        await matcher.finish(f"处理失败：{str(e)}")


async def enhance_image(image_data: bytes) -> Optional[str]:
    """
    调用 DeepAI API 进行超分辨率处理

    Args:
        image_data: 原始图片二进制数据

    Returns:
        处理后图片的本地临时文件路径，失败返回 None
    """
    try:
        async with httpx.AsyncClient(timeout=120.0, trust_env=False) as client:
            resp = await client.post(
                DEEPAI_API_URL,
                headers={"api-key": DEEPAI_API_KEY},
                files={"image": ("image.jpg", image_data, "image/jpeg")},
            )
            resp.raise_for_status()
            result = resp.json()

        output_url = result.get("output_url")
        if not output_url:
            logger.error(f"DeepAI 返回无 output_url: {result}")
            return None

        logger.info(f"DeepAI 超分成功，结果URL: {output_url}")

        # 下载超分结果图
        async with httpx.AsyncClient(timeout=120.0, trust_env=False) as client:
            resp = await client.get(output_url)
            resp.raise_for_status()
            enhanced_data = resp.content

        # 保存到临时文件
        ENHANCE_DIR.mkdir(parents=True, exist_ok=True)
        filename = f"{uuid.uuid4().hex[:12]}.jpg"
        output_path = str((ENHANCE_DIR / filename).resolve())

        with open(output_path, "wb") as f:
            f.write(enhanced_data)

        logger.info(f"超分图片已保存: {output_path}，大小: {len(enhanced_data)} bytes")
        return output_path

    except httpx.TimeoutException:
        logger.error("DeepAI API 请求超时（120秒），服务器可能繁忙")
        return None
    except httpx.HTTPStatusError as e:
        logger.error(f"DeepAI API 请求失败，状态码: {e.response.status_code}")
        return None
    except Exception as e:
        logger.error(f"图片超分处理失败: {type(e).__name__}: {e}")
        return None
