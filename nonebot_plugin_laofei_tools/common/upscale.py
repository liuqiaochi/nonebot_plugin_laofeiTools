"""
图片超分辨率（高分）功能

指令：
    lg高分 - 引用图片进行高分辨率放大（默认2x）
    lg高分 3 - 引用图片进行3x放大（支持2/3/4）
"""

import os
import uuid
from typing import Optional

from nonebot import on_command
from nonebot.adapters.onebot.v11 import (
    Bot,
    Message,
    MessageEvent,
    MessageSegment,
)
from nonebot.log import logger
from nonebot.matcher import Matcher
from nonebot.params import CommandArg

from .utils import download_image
from ..config import DATA_DIR

# 临时文件目录
UPSCALE_DIR = DATA_DIR / "upscale"

# ========== lg高分指令 ==========
upscale_cmd = on_command(
    "lg高分",
    priority=5,
    block=True,
    force_whitespace=True,
)


@upscale_cmd.handle()
async def handle_upscale(
    matcher: Matcher,
    bot: Bot,
    event: MessageEvent,
    args: Message = CommandArg(),
):
    """处理图片高分指令"""

    # 1. 解析放大倍数
    scale = 2
    if args:
        arg_text = args.extract_plain_text().strip()
        if arg_text:
            try:
                scale = int(arg_text)
            except ValueError:
                await matcher.finish("放大倍数请输入数字，如：lg高分 3")
            if scale < 2:
                scale = 2
            elif scale > 4:
                await matcher.finish("最大支持4倍放大，请输入 2、3 或 4")

    # 2. 检查是否引用了消息
    if not event.reply:
        await matcher.finish("请引用一张图片后发送「lg高分」")

    # 3. 获取被引用消息中的图片
    image_url: Optional[str] = None
    for seg in event.reply.message:
        if seg.type == "image":
            image_url = seg.data.get("url") or seg.data.get("file")
            break

    if not image_url:
        await matcher.finish("引用的消息中没有图片，请引用一张图片后发送「lg高分」")

    # 4. 发送等待提示
    await matcher.send(f"正在处理 {scale}x 放大，请稍候...")

    try:
        # 5. 下载图片
        image_data = await download_image(bot, image_url)
        if not image_data:
            await matcher.finish("图片下载失败，请重试")

        logger.info(f"高分图片下载成功，大小: {len(image_data)} bytes")

        # 6. 放大图片
        output_path = await upscale_image(image_data, scale)
        if not output_path:
            await matcher.finish("图片处理失败，请重试")

        # 7. 发送放大后的图片
        try:
            await matcher.send(Message([
                MessageSegment.reply(event.message_id),
                MessageSegment.image(f"file://{output_path}"),
                MessageSegment.text(f"\n已完成 {scale}x 放大"),
            ]))
        finally:
            # 8. 清理临时文件
            try:
                if os.path.exists(output_path):
                    os.remove(output_path)
            except Exception as e:
                logger.warning(f"清理临时文件失败: {e}")

    except Exception as e:
        logger.exception("图片高分处理失败")
        await matcher.finish(f"处理失败：{str(e)}")


async def upscale_image(image_data: bytes, scale: int = 2) -> Optional[str]:
    """
    使用 Pillow LANCZOS 滤波器放大图片

    Args:
        image_data: 原始图片二进制数据
        scale: 放大倍数（2/3/4）

    Returns:
        处理后图片的临时文件路径，失败返回 None
    """
    from PIL import Image
    from io import BytesIO

    try:
        # 打开图片
        img = Image.open(BytesIO(image_data))

        # 记录原始尺寸
        orig_w, orig_h = img.size
        logger.info(f"原始图片尺寸: {orig_w}x{orig_h}")

        # 转换为 RGB 模式（处理 RGBA/P 等）
        if img.mode in ("RGBA", "P", "LA"):
            img = img.convert("RGB")

        # 计算新尺寸
        new_w = orig_w * scale
        new_h = orig_h * scale

        # LANCZOS 高质量放大
        resampling = getattr(Image, "Resampling", Image).LANCZOS
        img = img.resize((new_w, new_h), resampling)

        logger.info(f"放大后尺寸: {new_w}x{new_h}")

        # 确保临时目录存在
        UPSCALE_DIR.mkdir(parents=True, exist_ok=True)

        # 保存到临时文件
        filename = f"{uuid.uuid4().hex[:12]}_{scale}x.jpg"
        output_path = str(UPSCALE_DIR / filename)
        img.save(output_path, format="JPEG", quality=92, optimize=True)

        logger.info(f"图片已保存: {output_path}")

        return output_path

    except Exception as e:
        logger.error(f"图片放大处理失败: {e}")
        return None
