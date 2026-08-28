"""
NovelAI 画图模块 — 调用 NovelAI 文生图 API

触发方式：novelai <提示词>   （别名：na画图 / na生图 / ai画图 / novelai画图）
提示词支持用竖线 `|` 分隔负面提示词，例如：novelai 1girl, cat ears | bad hands, blurry
权限：群聊与私聊均可使用
"""

import base64
import io
import random
import zipfile

import httpx
from nonebot import on_command, get_driver
from nonebot.adapters.onebot.v11 import Message, MessageEvent, MessageSegment
from nonebot.log import logger
from nonebot.matcher import Matcher
from nonebot.params import CommandArg

# ========== 配置 ==========

NOVELAI_ENDPOINT = "https://image.novelai.net/ai/generate-image"

# 默认生成参数
DEFAULT_MODEL = "nai-diffusion-4-5-curated"
DEFAULT_WIDTH = 832
DEFAULT_HEIGHT = 1216
DEFAULT_STEPS = 28
DEFAULT_SCALE = 5.0
DEFAULT_SAMPLER = "k_euler_ancestral"
DEFAULT_NOISE_SCHEDULE = "karras"

# 默认负面提示词（质量过滤）
DEFAULT_NEGATIVE = (
    "nsfw, lowres, bad anatomy, bad hands, text, error, missing fingers, "
    "extra digit, fewer digits, cropped, worst quality, low quality, "
    "normal quality, jpeg artifacts, signature, watermark, username, blurry"
)


driver = get_driver()


@driver.on_startup
async def _check_novelai_key():
    """bot 启动时检查 NovelAI API Key 是否已配置"""
    api_key = getattr(driver.config, "novelai_api_key", "")
    if not api_key:
        logger.error("=" * 50)
        logger.error("❌ NovelAI API Key 未配置！novelai 画图功能将不可用。")
        logger.error("请在 .env 文件中添加：NOVELAI_API_KEY=pst-xxxx")
        logger.error("=" * 50)
    else:
        logger.info("NovelAI API Key 已配置（模型默认: "
                    f"{getattr(driver.config, 'novelai_model', DEFAULT_MODEL)}）")


def _get_api_key() -> str:
    return getattr(get_driver().config, "novelai_api_key", "") or ""


def _get_model() -> str:
    return getattr(get_driver().config, "novelai_model", "") or DEFAULT_MODEL


def _build_payload(prompt: str, negative: str) -> dict:
    """构造 NovelAI v4.5 文生图请求体"""
    # 自动追加质量标签（避免重复）
    base_caption = prompt.strip()
    low = base_caption.lower()
    if "masterpiece" not in low:
        base_caption = f"{base_caption}, very aesthetic, masterpiece, no text"

    neg_caption = negative.strip() if negative.strip() else DEFAULT_NEGATIVE

    return {
        "action": "generate",
        "input": base_caption,
        "model": _get_model(),
        "parameters": {
            "v4_prompt": {
                "caption": {
                    "base_caption": base_caption,
                    "char_captions": [],
                },
                "use_coords": False,
                "use_order": True,
            },
            "v4_negative_prompt": {
                "caption": {
                    "base_caption": neg_caption,
                    "char_captions": [],
                },
                "legacy_uc": False,
            },
            "characterPrompts": [],
            "width": DEFAULT_WIDTH,
            "height": DEFAULT_HEIGHT,
            "steps": DEFAULT_STEPS,
            "scale": DEFAULT_SCALE,
            "sampler": DEFAULT_SAMPLER,
            "noise_schedule": DEFAULT_NOISE_SCHEDULE,
            "seed": random.randint(0, 2**31 - 1),
            "n_samples": 1,
            "params_version": 3,
            "qualityToggle": True,
        },
    }


def _extract_image(resp: httpx.Response) -> bytes:
    """从响应中提取 PNG 字节（兼容直接图片 / ZIP 包两种返回形式）"""
    content_type = resp.headers.get("content-type", "")
    content = resp.content

    # 直接返回图片
    if content_type.startswith("image/"):
        return content

    # JSON 错误响应
    if content_type.startswith("application/json"):
        try:
            err = resp.json()
            msg = err.get("message") or err.get("error") or str(err)
        except Exception:
            msg = content[:300].decode(errors="replace")
        raise RuntimeError(f"NovelAI 返回错误：{msg}")

    # 尝试作为 ZIP 解析（多图/单图均可能被打包）
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            pngs = [n for n in zf.namelist() if n.lower().endswith(".png")]
            if not pngs:
                raise RuntimeError("NovelAI 返回包中未找到 PNG 图片")
            return zf.read(pngs[0])
    except zipfile.BadZipFile:
        # 非 ZIP 也非图片类型，按原始内容返回（兜底）
        if content[:4] == b"\x89PNG":
            return content
        raise RuntimeError(f"无法解析 NovelAI 响应（content-type={content_type}）")


# ========== 指令注册 ==========

novelai_cmd = on_command(
    "novelai",
    aliases={"na画图", "na生图", "ai画图", "novelai画图"},
    priority=5,
    block=True,
    force_whitespace=True,
)


@novelai_cmd.handle()
async def handle_novelai(matcher: Matcher, event: MessageEvent, args: Message = CommandArg()):
    """处理 novelai 画图指令"""
    raw = args.extract_plain_text().strip()
    if not raw:
        await matcher.finish(
            Message([
                MessageSegment.reply(event.message_id),
                MessageSegment.text(
                    "用法：novelai <提示词>\n"
                    "示例：novelai 1girl, cat ears, masterpiece\n"
                    "支持用 | 分隔负面提示词：novelai 1girl | bad hands, blurry"
                ),
            ])
        )

    # 解析正面 / 负面提示词
    if "|" in raw:
        positive, negative = raw.split("|", 1)
    else:
        positive, negative = raw, ""

    api_key = _get_api_key()
    if not api_key:
        await matcher.finish(
            Message([
                MessageSegment.reply(event.message_id),
                MessageSegment.text("⚠️ NovelAI API Key 未配置，请联系管理员在 .env 中设置 NOVELAI_API_KEY"),
            ])
        )

    await matcher.send(Message([MessageSegment.text("🎨 正在调用 NovelAI 生成图片，请稍候…")]))

    payload = _build_payload(positive, negative)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            resp = await client.post(NOVELAI_ENDPOINT, json=payload, headers=headers)
    except httpx.HTTPError as e:
        logger.error(f"NovelAI 请求失败：{e}")
        await matcher.finish(
            Message([
                MessageSegment.reply(event.message_id),
                MessageSegment.text(f"❌ 请求 NovelAI 失败：{e}"),
            ])
        )
        return

    if resp.status_code != 200:
        # 尝试以 JSON 读取错误信息
        try:
            err = resp.json()
            msg = err.get("message") or err.get("error") or str(err)
        except Exception:
            msg = resp.text[:300]
        logger.error(f"NovelAI HTTP {resp.status_code}: {msg}")
        await matcher.finish(
            Message([
                MessageSegment.reply(event.message_id),
                MessageSegment.text(f"❌ NovelAI 返回错误（HTTP {resp.status_code}）：{msg}"),
            ])
        )
        return

    try:
        img_bytes = _extract_image(resp)
    except RuntimeError as e:
        logger.error(f"NovelAI 响应解析失败：{e}")
        await matcher.finish(
            Message([
                MessageSegment.reply(event.message_id),
                MessageSegment.text(f"❌ {e}"),
            ])
        )
        return

    if not img_bytes or img_bytes[:4] != b"\x89PNG":
        await matcher.finish(
            Message([
                MessageSegment.reply(event.message_id),
                MessageSegment.text("❌ NovelAI 返回内容不是有效的 PNG 图片"),
            ])
        )
        return

    b64 = base64.b64encode(img_bytes).decode()
    logger.info(f"NovelAI 图片生成成功，大小 {len(img_bytes)} 字节")
    await matcher.finish(Message([MessageSegment.image(f"base64://{b64}")]))
