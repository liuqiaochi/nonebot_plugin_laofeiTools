"""
NovelAI 画图模块 — 调用 NovelAI 文生图 API

触发方式：ai画图 <提示词>   （别名：ai生图 / ai绘画 / ai绘图）
提示词支持用竖线 `|` 分隔负面提示词，例如：ai画图 1girl, cat ears | bad hands, blurry
权限：仅群聊可用，需超级管理员开启
"""

import asyncio
import base64
import io
import os
import random
import tempfile
import zipfile

import httpx
from nonebot import on_command, get_driver
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupMessageEvent,
    Message,
    MessageEvent,
    MessageSegment,
    PrivateMessageEvent,
)
from nonebot.log import logger
from nonebot.matcher import Matcher
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER

from ..config import (
    is_novelai_group_enabled,
    enable_novelai_group,
    disable_novelai_group,
    get_novelai_model,
    set_novelai_model,
)
from .help_image import render_help_image

# ========== 配置 ==========

NOVELAI_ENDPOINT = "https://image.novelai.net/ai/generate-image"
# 账户 / 额度查询端点（注意：同为 image.novelai.net，api.novelai.net 已废弃）
NOVELAI_ACCOUNT_ENDPOINT = "https://image.novelai.net/user/subscription"

# tier 数字 -> 订阅档位名称（/user/subscription 仅返回 tier 数字）
_TIER_NAMES = {0: "Paper", 1: "Tablet", 2: "Scroll", 3: "Opus"}
# Opus 免费 V5 世代满额估算张数（标准 V5 正常分辨率 832×1216、28 步下的经验值，用于把剩余百分比折算为张数）
_FREE_V5_FULL_IMAGES = 1730

# 浏览器 UA，规避 NovelAI 反爬拦截（Cloudflare 403）
_NOVELAI_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# 全局单任务锁：NovelAI 同账号仅允许一个生成任务，避免并发请求导致失败
ai_draw_lock = asyncio.Lock()

# 默认生成参数
DEFAULT_MODEL = "nai-diffusion-4-5-curated"
DEFAULT_WIDTH = 832
DEFAULT_HEIGHT = 1216
DEFAULT_STEPS = 28
DEFAULT_SCALE = 5.0
DEFAULT_SAMPLER = "k_euler_ancestral"
DEFAULT_NOISE_SCHEDULE = "karras"

# 模型别名 -> 原始模型名
NOVELAI_MODEL_ALIASES = {
    "v4.5": "nai-diffusion-4-5-curated",
    "v4.5-full": "nai-diffusion-4-5",
    "v4": "nai-diffusion-4-curated-preview",
    "v4-full": "nai-diffusion-4-full",
    "v5": "nai-diffusion-5-full",
    "v5-full": "nai-diffusion-5-full",
    "v5-curated": "nai-diffusion-5-curated",
    "v3": "nai-diffusion-3",
    "v2": "nai-diffusion-2",
    "furry": "nai-diffusion-furry-3",
}
_VALID_MODELS = set(NOVELAI_MODEL_ALIASES.values())

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


def _get_model(group_id: str) -> str:
    """解析本群当前模型（群级设置优先，否则 config 默认）"""
    raw = get_novelai_model(group_id)
    if raw:
        return raw
    return getattr(get_driver().config, "novelai_model", "") or DEFAULT_MODEL


def _resolve_model(key: str):
    """将用户输入的别名/原始名解析为原始模型名，无法识别返回 None"""
    key = (key or "").strip().lower()
    if key in NOVELAI_MODEL_ALIASES:
        return NOVELAI_MODEL_ALIASES[key]
    if key in _VALID_MODELS:
        return key
    return None


def _is_v4_model(model: str) -> bool:
    """v4 / v4.5 / v5 系列使用 v4_prompt 结构，其余（v3/v2/furry）使用旧 prompt/uc 结构"""
    return model.startswith("nai-diffusion-4") or model.startswith("nai-diffusion-5")


def _build_payload(prompt: str, negative: str, model: str) -> dict:
    """构造 NovelAI 文生图请求体（按模型版本自动切换 payload 结构）"""
    # 自动追加质量标签（避免重复）
    base_caption = prompt.strip()
    low = base_caption.lower()
    if "masterpiece" not in low:
        base_caption = f"{base_caption}, very aesthetic, masterpiece, no text"

    neg_caption = negative.strip() if negative.strip() else DEFAULT_NEGATIVE

    params = {
        "characterPrompts": [],
        "width": DEFAULT_WIDTH,
        "height": DEFAULT_HEIGHT,
        "steps": DEFAULT_STEPS,
        "scale": DEFAULT_SCALE,
        "sampler": DEFAULT_SAMPLER,
        "noise_schedule": DEFAULT_NOISE_SCHEDULE,
        "seed": random.randint(0, 2**31 - 1),
        "n_samples": 1,
        "qualityToggle": True,
    }

    if _is_v4_model(model):
        # v4 / v4.5：结构化 v4_prompt
        params["params_version"] = 3
        params["v4_prompt"] = {
            "caption": {
                "base_caption": base_caption,
                "char_captions": [],
            },
            "use_coords": False,
            "use_order": True,
        }
        params["v4_negative_prompt"] = {
            "caption": {
                "base_caption": neg_caption,
                "char_captions": [],
            },
            "legacy_uc": False,
        }
    else:
        # v3 / v2 / furry：旧式 prompt / uc
        params["params_version"] = 1
        params["prompt"] = base_caption
        params["uc"] = neg_caption

    return {
        "action": "generate",
        "input": base_caption,
        "model": model,
        "parameters": params,
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
    "ai画图",
    aliases={"ai生图", "ai绘画", "ai绘图"},
    priority=5,
    block=True,
    force_whitespace=True,
)


@novelai_cmd.handle()
async def handle_novelai(matcher: Matcher, bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    """处理 novelai 画图指令（仅群聊可用，需超管开启）"""
    # 仅群聊可用
    if isinstance(event, PrivateMessageEvent):
        await matcher.finish(
            Message([
                MessageSegment.reply(event.message_id),
                MessageSegment.text("⚠️ ai画图 仅支持在群聊中使用"),
            ])
        )

    group_id = str(event.group_id)
    if not is_novelai_group_enabled(group_id):
        await matcher.finish(
            Message([
                MessageSegment.reply(event.message_id),
                MessageSegment.text("⚠️ 本群未开启 ai画图 功能（需超级管理员发送「开启ai画图」开启）"),
            ])
        )

    raw = args.extract_plain_text().strip()
    if not raw:
        await matcher.finish(
            Message([
                MessageSegment.reply(event.message_id),
                MessageSegment.text(
                    "用法：ai画图 <提示词>\n"
                    "示例：ai画图 1girl, cat ears, masterpiece\n"
                    "支持用 | 分隔负面提示词：ai画图 1girl | bad hands, blurry"
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

    # === 并发互斥：当前已有 ai画图 任务在进行，直接提示等待 ===
    if ai_draw_lock.locked():
        await matcher.finish(
            Message([
                MessageSegment.reply(event.message_id),
                MessageSegment.text("⚠️ 当前有 ai画图 任务正在进行，请等待其完成后再试～"),
            ])
        )

    async with ai_draw_lock:
        await matcher.send(Message([MessageSegment.text("🎨 正在调用 NovelAI 生成图片，请稍候…")]))

        payload = _build_payload(positive, negative, _get_model(group_id))
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

        # === 发送图片：以合并转发方式发出，失败则直接提示 ===
        img_seg = MessageSegment.image(f"base64://{b64}")
        try:
            bot_name = "蓝色大肥鱼"
            try:
                bot_info = await bot.get_login_info()
                bot_name = bot_info.get("nickname", "蓝色大肥鱼")
            except Exception:
                pass
            group_id = getattr(event, "group_id", None)
            forward_msgs = [{
                "type": "node",
                "data": {
                    "name": bot_name,
                    "uin": bot.self_id,
                    "content": str(img_seg),
                },
            }]
            await bot.call_api(
                "send_group_forward_msg",
                group_id=group_id,
                messages=forward_msgs,
            )
        except Exception as e:
            logger.error(f"ai画图 合并转发发送失败：{e}")
            await matcher.send(
                Message([
                    MessageSegment.reply(event.message_id),
                    MessageSegment.text("❌ 图片发送失败，可能是图片过大或网络异常，请稍后重试。"),
                ])
            )
        await matcher.finish()


# ========== 开启 / 关闭 群聊 NovelAI 画图（仅超级用户） ==========

novelai_on_cmd = on_command(
    "开启ai画图",
    aliases={"开启ai生图", "开启ai绘画", "开启ai绘图"},
    permission=SUPERUSER,
    priority=5,
    block=True,
    force_whitespace=True,
)


@novelai_on_cmd.handle()
async def handle_enable_novelai(matcher: Matcher, event: MessageEvent):
    """超级用户开启本群 ai画图 功能"""
    if isinstance(event, PrivateMessageEvent):
        await matcher.finish("请在群聊中发送此指令。")
    group_id = str(event.group_id)
    if is_novelai_group_enabled(group_id):
        await matcher.finish("ai画图 功能已经开启。")
    enable_novelai_group(group_id)
    await matcher.finish("✅ 已开启本群 ai画图 功能，发送 ai画图 <提示词> 即可使用！")


novelai_off_cmd = on_command(
    "关闭ai画图",
    aliases={"关闭ai生图", "关闭ai绘画", "关闭ai绘图"},
    permission=SUPERUSER,
    priority=5,
    block=True,
    force_whitespace=True,
)


@novelai_off_cmd.handle()
async def handle_disable_novelai(matcher: Matcher, event: MessageEvent):
    """超级用户关闭本群 ai画图 功能"""
    if isinstance(event, PrivateMessageEvent):
        await matcher.finish("请在群聊中发送此指令。")
    group_id = str(event.group_id)
    if not is_novelai_group_enabled(group_id):
        await matcher.finish("ai画图 功能已经关闭。")
    disable_novelai_group(group_id)
    await matcher.finish("❌ 已关闭本群 ai画图 功能。")


# ========== ai画图 独立帮助指令 ==========

novelai_help_cmd = on_command(
    "ai画图帮助",
    aliases={"ai生图帮助", "ai绘画帮助", "ai绘图帮助"},
    priority=5,
    block=True,
    force_whitespace=True,
)


@novelai_help_cmd.handle()
async def handle_novelai_help(matcher: Matcher, event: MessageEvent):
    """返回 ai画图 完整使用帮助（图片形式，与 lg帮助 风格一致）"""
    sections = [
        ("__text__", "仅群聊可用，默认关闭，需超级管理员开启。同一时间仅支持一个生成任务，进行中发送会提示等待。"),
        ("使用", [
            ("ai画图 <提示词>", "根据提示词生成图片"),
            ("ai生图 / ai绘画 / ai绘图", "ai画图 的别名"),
            ("ai画图 提示词 | 负面词", "用 | 分隔负面提示词"),
        ]),
        ("开启 / 关闭（仅超级用户）", [
            ("开启ai画图 / 关闭ai画图", "别名：开启ai生图…、关闭ai生图…"),
        ]),
        ("切换模型（仅超级用户）", [
            ("ai画图模型", "查看当前模型与全部可用列表"),
            ("ai画图模型 v4.5 / v4 / v5 / v5-curated", "切换模型（v4.5 / v4 / v5 / v5-curated 等）"),
            ("ai画图模型 v3 / v2 / furry", "切换至旧版 / 福瑞模型"),
        ]),
        ("查询额度（仅超级用户）", [
            ("ai画图额度 / ai画图余额", "查询账户剩余 Anlas 与 Opus 免费 V5 额度"),
        ]),
        ("__text__", "默认尺寸 832×1216，默认模型 nai-diffusion-4-5-curated。"),
    ]
    img_b64 = render_help_image("AI 画图帮助", sections, footer="AI 画图 · NovelAI")
    await matcher.finish(MessageSegment.image(f"base64://{img_b64}"))


# ========== ai画图模型 切换指令（仅超级用户） ==========

novelai_model_cmd = on_command(
    "ai画图模型",
    aliases={"ai绘画模型", "ai绘图模型", "切换ai画图模型"},
    permission=SUPERUSER,
    priority=5,
    block=True,
    force_whitespace=True,
)


def _model_alias(model: str) -> str:
    """返回模型名对应的别名（找不到则原样返回）"""
    for alias, raw in NOVELAI_MODEL_ALIASES.items():
        if raw == model:
            return alias
    return model


@novelai_model_cmd.handle()
async def handle_set_model(matcher: Matcher, event: MessageEvent, args: Message = CommandArg()):
    """超级用户切换本群 ai画图 模型；不带参数时列出当前模型与可用列表"""
    if isinstance(event, PrivateMessageEvent):
        await matcher.finish("请在群聊中发送此指令。")
    group_id = str(event.group_id)

    key = args.extract_plain_text().strip()
    if not key:
        cur = _get_model(group_id)
        lines = [
            f"当前本群 ai画图 模型：{cur}（别名 {_model_alias(cur)}）",
            "",
            "可用模型（发送「ai画图模型 <别名>」切换）：",
        ]
        for alias, raw in NOVELAI_MODEL_ALIASES.items():
            lines.append(f"  {alias}  ->  {raw}")
        lines.append("")
        lines.append("也可直接发送原始模型名，如：ai画图模型 nai-diffusion-3")
        await matcher.finish(Message([MessageSegment.text("\n".join(lines))]))

    resolved = _resolve_model(key)
    if not resolved:
        await matcher.finish(
            Message([MessageSegment.text(f"❌ 未知模型「{key}」，发送「ai画图模型」查看可用列表")])
        )

    set_novelai_model(group_id, resolved)
    await matcher.finish(
        Message([MessageSegment.text(f"✅ 已切换本群 ai画图 模型为 {resolved}（别名 {_model_alias(resolved)}）")])
    )


# ========== ai画图额度 查询指令（仅超级用户） ==========

novelai_balance_cmd = on_command(
    "ai画图额度",
    aliases={"ai生图额度", "ai绘画额度", "ai绘图额度", "ai画图余额",
             "ai生图限制", "ai绘画限制", "ai绘图限制", "ai画图免费额度"},
    permission=SUPERUSER,
    priority=5,
    block=True,
    force_whitespace=True,
)


def _fmt_ts(ms) -> str:
    """将毫秒时间戳转为可读日期，无法解析则返回原值字符串"""
    try:
        ts = float(ms)
        if ts > 1e12:  # 毫秒级
            ts /= 1000
        import datetime
        return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(ms)


def _fmt_duration(ms) -> str:
    """将毫秒时长转为可读字符串"""
    try:
        s = float(ms) / 1000.0
    except Exception:
        return str(ms)
    if s < 60:
        return f"{s:.0f} 秒"
    if s < 3600:
        return f"{s / 60:.1f} 分钟"
    if s < 86400:
        return f"{s / 3600:.1f} 小时"
    return f"{s / 86400:.1f} 天"


async def _fetch_subscription() -> dict:
    """拉取 NovelAI 订阅信息，失败抛 RuntimeError"""
    api_key = _get_api_key()
    if not api_key:
        raise RuntimeError("NovelAI API Key 未配置，无法查询额度")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "User-Agent": _NOVELAI_UA,
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(NOVELAI_ACCOUNT_ENDPOINT, headers=headers)
    except httpx.HTTPError as e:
        logger.error(f"NovelAI 额度查询请求失败：{e}")
        raise RuntimeError(f"请求 NovelAI 失败：{e}")
    if resp.status_code != 200:
        try:
            err = resp.json()
            msg = err.get("message") or err.get("error") or str(err)
        except Exception:
            msg = resp.text[:300]
        logger.error(f"NovelAI 额度查询 HTTP {resp.status_code}: {msg}")
        raise RuntimeError(f"额度查询失败（HTTP {resp.status_code}）：{msg}")
    try:
        return resp.json()
    except Exception:
        raise RuntimeError("无法解析 NovelAI 返回的订阅数据")


@novelai_balance_cmd.handle()
async def handle_novelai_balance(matcher: Matcher, event: MessageEvent):
    """超级用户查询 NovelAI 账户剩余 Anlas 额度与 Opus 免费 V5 世代额度"""
    try:
        data = await _fetch_subscription()
    except RuntimeError as e:
        await matcher.finish(Message([MessageSegment.text(f"❌ {e}")]))
        return

    tier = data.get("tier", 0)
    tier_name = (data.get("tier_name") or data.get("tierName")
                 or _TIER_NAMES.get(tier) or f"tier{tier}")
    training = data.get("trainingStepsLeft") or data.get("training_steps_left") or {}
    fixed = int(training.get("fixedTrainingStepsLeft") or training.get("fixed_training_steps_left") or 0)
    purchased = int(training.get("purchasedTrainingSteps") or training.get("purchased_training_steps") or 0)
    total = fixed + purchased
    expires = data.get("expiresAt") or data.get("expires_at")

    usage = data.get("usage") or {}
    percent = usage.get("percent")
    is_negative = usage.get("isNegative", False)
    next_ms = usage.get("timeUntilNextPercent")

    # 组装为图片返回（与 ai画图帮助 风格一致）
    remaining = round(percent / 100.0 * _FREE_V5_FULL_IMAGES) if percent is not None else None

    account_block = (
        "💰 NovelAI 账户额度\n"
        f"订阅档位：{tier_name}（tier {tier}）\n"
        f"剩余 Anlas：{total}\n"
        f"  · 包月固定：{fixed}\n"
        f"  · 额外购买：{purchased}"
    )
    if expires is not None:
        account_block += f"\n订阅到期：{_fmt_ts(expires)}"

    free_lines = ["🎯 Opus 免费 V5 世代额度"]
    if remaining is not None:
        free_lines.append(f"免费额度剩余：{percent}%（约 {remaining} 张图片）")
    else:
        free_lines.append("免费额度：暂无数据")
    if is_negative:
        free_lines.append("⚠️ 已透支，超出部分将消耗 Anlas 生成")
    if next_ms is not None:
        free_lines.append(f"距离下次自动补足：{_fmt_duration(next_ms)}后")
    free_lines.append(
        f"（张数按标准 V5 正常分辨率 28 步估算，满额约 {_FREE_V5_FULL_IMAGES} 张；实际随分辨率/步数变化）"
    )
    free_block = "\n".join(free_lines)

    sections = [
        ("__text__", account_block),
        ("__text__", free_block),
    ]
    # 只返回图片：渲染/发送失败仅记录日志，不发送任何文本（无失败保底）
    try:
        img_b64 = render_help_image("AI 画图额度", sections, footer="AI 画图 · NovelAI")
        # 写入临时文件后用本地路径发送，避免超长 base64 在部分 OneBot 实现下
        # 被拆分为「图片 + 一段 base64 文本」两条消息
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".png", prefix="ai_quota_")
        tmp.write(base64.b64decode(img_b64))
        tmp.close()
        await matcher.finish(MessageSegment.image(tmp.name))
    except Exception as e:
        logger.error(f"ai画图额度 图片渲染/发送失败：{e}")
        await matcher.finish()
