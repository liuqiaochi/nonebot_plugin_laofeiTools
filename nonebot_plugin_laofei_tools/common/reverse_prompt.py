"""
图反推提示词（超管专用）

调用 DeepSeek 视觉模型，从用户发送/引用的图片反推可用于 NovelAI 的 AI 绘画提示词。
权限：仅超级管理员可使用。
依赖：deepseek_api_key 已配置，且 deepseek_model 为支持视觉的模型（默认 deepseek-flash）。
"""

import base64

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, MessageEvent, MessageSegment
from nonebot.log import logger
from nonebot.matcher import Matcher
from nonebot.permission import SUPERUSER

from .utils import download_image
from .ai_chat import _get_client, _get_model

# 反推提示词的指令：要求输出 Danbooru 风格、逗号分隔的英文标签，覆盖关键维度
REVERSE_PROMPT_INSTRUCTION = (
    "请用逗号分隔的英文标签，模仿 Danbooru 风格，输出用于 AI 绘画（NovelAI）的提示词。"
    "必须涵盖以下维度：主体/角色、发色与发型、服装与配饰、表情、动作与姿势、"
    "背景与场景、构图与镜头、画质增强 tag（如 masterpiece, best quality）。"
    "只输出提示词本身，不要任何解释、前缀或引号。"
)


reverse_prompt_cmd = on_command(
    "图反推",
    aliases={"反推提示词", "图片反推", "提示词反推"},
    permission=SUPERUSER,
    priority=5,
    block=True,
    force_whitespace=True,
)


@reverse_prompt_cmd.handle()
async def handle_reverse_prompt(
    matcher: Matcher,
    bot: Bot,
    event: MessageEvent,
):
    """超管专用：从消息内或引用消息中的图片反推 AI 绘画提示词"""
    # 1. 提取图片（优先消息内图片，其次引用图片）
    image_url = _extract_image_url(event)
    if not image_url:
        await matcher.finish("请发送一张图片（直接附带或引用图片）后发送「图反推」")
        return

    await matcher.send("正在反推提示词，请稍候...")

    # 2. 下载图片
    try:
        image_data = await download_image(bot, image_url)
    except Exception as e:
        logger.error(f"图反推图片下载失败: {e}")
        await matcher.finish("图片下载失败，请重试")
        return

    if not image_data:
        await matcher.finish("图片下载失败，请重试")
        return

    # 3. 调用 DeepSeek 视觉模型反推提示词
    try:
        client = _get_client()
        model = _get_model()
        mime = _guess_mime(image_url, image_data)
        data_url = f"data:{mime};base64,{base64.b64encode(image_data).decode()}"
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": REVERSE_PROMPT_INSTRUCTION},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
            ],
            temperature=0.5,
            max_tokens=800,
        )
        prompt = (response.choices[0].message.content or "").strip()
    except ValueError as e:
        # DeepSeek API Key 未配置
        await matcher.finish(str(e))
        return
    except Exception as e:
        logger.error(f"图反推调用 DeepSeek 失败: {e}")
        await matcher.finish("反推失败，请稍后再试")
        return

    if not prompt:
        await matcher.finish("未能反推出提示词，请换一张图重试")
        return

    # 4. 返回结果（超管自用，直接返回文本便于复制）
    msg = Message()
    if getattr(event, "message_id", None):
        msg.append(MessageSegment.reply(event.message_id))
    msg.append(
        MessageSegment.text(f"以下为反推提示词（可直接用于 NovelAI）：\n\n{prompt}")
    )
    await matcher.finish(msg)


def _extract_image_url(event: MessageEvent) -> str:
    """从消息内或引用消息中提取第一张图片的 url/file 字段"""
    # 优先消息内图片
    for seg in event.message:
        if seg.type == "image":
            url = seg.data.get("url") or seg.data.get("file")
            if url:
                return url
    # 其次引用消息中的图片
    reply = getattr(event, "reply", None)
    if reply and getattr(reply, "message", None):
        for seg in reply.message:
            if seg.type == "image":
                url = seg.data.get("url") or seg.data.get("file")
                if url:
                    return url
    return ""


def _guess_mime(image_url: str, data: bytes) -> str:
    """根据 url 扩展名或文件头推断图片 mime，默认 image/jpeg"""
    lower = (image_url or "").lower()
    if lower.endswith(".png"):
        return "image/png"
    if lower.endswith(".gif"):
        return "image/gif"
    if lower.endswith(".webp"):
        return "image/webp"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"
