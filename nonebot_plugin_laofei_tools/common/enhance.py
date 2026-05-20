"""
图片超分（增强清晰度）功能

指令：
    lg超分 - 引用图片进行高清化处理，让模糊图片变清晰
"""

import os
import uuid
from typing import Optional

import cv2
import numpy as np
from nonebot import on_command
from nonebot.adapters.onebot.v11 import (
    Bot,
    Message,
    MessageEvent,
    MessageSegment,
)
from nonebot.log import logger
from nonebot.matcher import Matcher

from .utils import download_image
from ..config import DATA_DIR

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

        # 5. 超分处理
        output_path = await enhance_image(image_data)
        if not output_path:
            await matcher.finish("图片处理失败，请重试")

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
    使用 OpenCV 进行图像超分处理

    流程：双边滤波去噪 → 细节增强(Edge Enhance) → USM锐化 → 对比度增强

    Args:
        image_data: 原始图片二进制数据

    Returns:
        处理后图片的临时文件路径，失败返回 None
    """
    try:
        # 将二进制数据转为 numpy 数组
        nparr = np.frombuffer(image_data, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

        if img is None:
            logger.error("无法解码图片")
            return None

        orig_h, orig_w = img.shape[:2]
        logger.info(f"原始图片尺寸: {orig_w}x{orig_h}")

        # Step 1: 双边滤波去噪（保留边缘的同时去除噪点）
        denoised = cv2.bilateralFilter(img, d=9, sigmaColor=75, sigmaSpace=75)

        # Step 2: 细节增强（cv2.detailEnhance 需要 opencv-contrib，用替代方案）
        # 使用高斯模糊做低频层，与原图相减得到高频细节层，再叠加增强
        gaussian = cv2.GaussianBlur(denoised, (0, 0), 5)
        detail = cv2.addWeighted(denoised, 1.5, gaussian, -0.5, 0)

        # Step 3: Unsharp Mask 锐化
        blur = cv2.GaussianBlur(detail, (0, 0), 3)
        sharpened = cv2.addWeighted(detail, 1.3, blur, -0.3, 0)

        # Step 4: CLAHE 直方图均衡化增强对比度（转 LAB 空间只处理亮度通道）
        lab = cv2.cvtColor(sharpened, cv2.COLOR_BGR2LAB)
        l, a, b = cv2.split(lab)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        l = clahe.apply(l)
        enhanced = cv2.merge([l, a, b])
        enhanced = cv2.cvtColor(enhanced, cv2.COLOR_LAB2BGR)

        # 确保临时目录存在
        ENHANCE_DIR.mkdir(parents=True, exist_ok=True)

        # 保存到临时文件
        filename = f"{uuid.uuid4().hex[:12]}.jpg"
        output_path = str((ENHANCE_DIR / filename).resolve())
        cv2.imwrite(output_path, enhanced, [cv2.IMWRITE_JPEG_QUALITY, 95])

        logger.info(f"超分图片已保存: {output_path}")

        return output_path

    except Exception as e:
        logger.error(f"图片超分处理失败: {e}")
        return None
