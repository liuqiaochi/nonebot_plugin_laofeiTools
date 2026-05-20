"""
图片超分（增强清晰度）功能

指令：
    lg超分 - 引用图片进行高清化处理，让模糊图片变清晰

使用 Real-ESRGAN 本地推理（realesr-general-x4v3 轻量模型）
"""

import asyncio
import os
import uuid
from io import BytesIO
from typing import Optional

import numpy as np
from PIL import Image
from nonebot import on_command
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

# 临时文件目录
ENHANCE_DIR = DATA_DIR / "enhance"

# 模型配置
MODEL_URL = "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesr-general-x4v3.pth"

# 延迟加载的模型实例（首次调用时初始化）
_upsampler = None


def _get_upsampler():
    """延迟初始化 Real-ESRGAN 模型"""
    global _upsampler
    if _upsampler is not None:
        return _upsampler

    try:
        from basicsr.archs.arch_util import SRVGGNetCompact
        from realesrgan import RealESRGANer
    except ImportError:
        raise ImportError(
            "缺少 realesrgan 依赖，请安装：pip install realesrgan\n"
            "CPU-only 系统建议先安装轻量 torch："
            "pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu"
        )

    model = SRVGGNetCompact(
        num_in_ch=3, num_out_ch=3, num_feat=64,
        num_conv=16, upscale=4, act_type="prelu",
    )

    _upsampler = RealESRGANer(
        scale=4,
        model_path=MODEL_URL,
        model=model,
        tile=400,       # 分块处理，避免内存溢出
        tile_pad=10,
        pre_pad=0,
        half=False,     # CPU 不支持 fp16
    )

    logger.info("Real-ESRGAN 模型加载完成")
    return _upsampler


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
            await matcher.finish("图片处理失败，请检查日志")

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
    使用 Real-ESRGAN 进行超分辨率处理

    Args:
        image_data: 原始图片二进制数据

    Returns:
        处理后图片的本地临时文件路径，失败返回 None
    """
    try:
        # 将图片转为 numpy 数组
        img = Image.open(BytesIO(image_data)).convert("RGB")
        img_np = np.array(img)

        logger.info(f"原始图片尺寸: {img.width}x{img.height}")

        # 在线程池中运行 CPU 密集型推理，避免阻塞事件循环
        loop = asyncio.get_event_loop()

        def _do_enhance():
            upsampler = _get_upsampler()
            output, _ = upsampler.enhance(img_np, outscale=2)
            return output

        output_np = await loop.run_in_executor(None, _do_enhance)

        # 保存结果
        result_img = Image.fromarray(output_np)
        ENHANCE_DIR.mkdir(parents=True, exist_ok=True)
        filename = f"{uuid.uuid4().hex[:12]}.png"
        output_path = str((ENHANCE_DIR / filename).resolve())
        result_img.save(output_path, "PNG")

        logger.info(
            f"超分完成: {img.width}x{img.height} → {result_img.width}x{result_img.height}，"
            f"保存至: {output_path}"
        )
        return output_path

    except ImportError as e:
        logger.error(f"超分依赖缺失: {e}")
        return None
    except Exception as e:
        logger.error(f"图片超分处理失败: {type(e).__name__}: {e}")
        return None
