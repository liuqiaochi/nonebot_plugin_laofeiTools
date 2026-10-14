"""
图反推提示词

调用 DeepSeek 视觉模型，从用户发送/引用的图片，或 @群友 的头像反推可用于 NovelAI 的 AI 绘画提示词。
权限：普通用户可用，但仅「已开启 AI 功能（ai_chat 模块）」的群生效；私聊不受群开关限制。
依赖：deepseek_api_key 已配置。
注意：视觉模型与对话模型（config.deepseek_model）解耦，视觉模型固定使用 VISION_MODEL 常量（DeepSeek-V4.1-Flash，当前官方唯一视觉 ID）。
"""

import asyncio
import base64
from io import BytesIO

from nonebot import on_message
from nonebot.adapters.onebot.v11 import (
    Bot,
    MessageEvent,
    Message,
    MessageSegment,
    PrivateMessageEvent,
)
from nonebot.log import logger
from nonebot.matcher import Matcher
from nonebot.permission import SUPERUSER
from nonebot.rule import Rule
from PIL import Image

from ..config import (
    is_ai_group_enabled,
    is_reverse_polish_enabled,
    is_novelai_group_enabled,
    enable_reverse_polish,
    disable_reverse_polish,
)
from .utils import download_image
from .ai_chat import _get_client, _get_model
from .novelai_draw import draw_prompt_to_image

# 视觉模型固定 slug，与对话模型（config.deepseek_model）分离。
# DeepSeek-V4.1 起视觉能力已原生并入 deepseek-flash（即 V4.1-Flash），不再有独立 vision slug；
# 旧的 deepseek-v4-flash-vision-exp 已于 2026-09-10 退役，仅作兼容别名路由到 4.1-Flash。
# 故视觉模型固定为 deepseek-flash（当前官方视觉 ID），对话模型即使配 deepseek-chat 纯文本也不影响图反推。
VISION_MODEL = "deepseek-flash"

# 图反推全局串行队列：同一时刻仅一个任务访问视觉模型，其余并发请求排队（避免打爆 DeepSeek API）
_reverse_lock = asyncio.Lock()

# 反推提示词的指令：要求输出 Danbooru 风格、逗号分隔的英文标签，覆盖关键维度
REVERSE_PROMPT_INSTRUCTION = (
    "请用逗号分隔的英文标签，模仿 Danbooru 风格，输出用于 AI 绘画（NovelAI）的提示词。"
    "必须涵盖以下维度：主体/角色、发色与发型、服装与配饰、表情、动作与姿势、"
    "背景与场景、构图与镜头、画质增强 tag（如 masterpiece, best quality）。"
    "只输出提示词本身，不要任何解释、前缀或引号。"
)

# 触发关键字（任意位置出现即可，支持 @群友 前/后、引用等任意消息结构）
REVERSE_KEYWORDS = ("图反推", "反推提示词", "图片反推", "提示词反推")

# 优化触发关键字：消息文本段含其一即对反推结果再做一次 DS 文本模型润色（先跑通，后续再做群开关）
OPTIMIZE_KEYWORDS = ("优化", "美化", "优化一下", "润色")

# DS 文本模型（复用 config.deepseek_model，与 AI 聊天同一模型）的润色指令：保留英文 Danbooru tag，只整理不臆造
OPTIMIZE_INSTRUCTION = (
    "你是一名 NovelAI 绘画提示词优化助手。下面是一段由视觉模型从图片反推出的 Danbooru 风格英文提示词。"
    "请在不改变原意与原 tag 的前提下进行润色整理："
    "1) 去除重复 tag；2) 修正明显拼写/语法错误；"
    "3) 按「画质tag → 主体/角色 → 外观(发色发型) → 服装配饰 → 表情 → 动作姿势 → 背景场景 → 构图镜头」顺序整理；"
    "4) 用半角逗号分隔，保持英文小写 tag 风格，不要翻译成中文，不要新增原图未体现的内容；"
    "5) 如原提示词已较完善则小幅整理即可。只输出优化后的提示词本身，不要任何解释或引号。"
)


async def _is_reverse_request(event: MessageEvent) -> bool:
    """自定义规则：消息任意文本段包含反推关键字即触发。

    不用 on_command 的原因：on_command 要求命令位于消息开头且 force_whitespace，
    导致「@群友 图反推」或「图反推@群友」（At 段不在首位/无空白）匹配失败；
    改用 on_message + 本规则，关键字可在任意位置触发，图片/@群友 提取在 handler 内完成。
    注意：需排除管理指令「开启图反推润色 / 关闭图反推润色」（文本含「图反推」），
    否则会和同 priority 的开关指令互相触发。
    """
    for seg in event.message:
        if seg.type == "text":
            text = seg.data.get("text", "") or ""
            if any(kw in text for kw in REVERSE_KEYWORDS):
                if (
                    "开启图反推" in text
                    or "关闭图反推" in text
                    or "反推AI图" in text
                ):
                    continue
                return True
    return False


reverse_prompt_cmd = on_message(
    rule=Rule(_is_reverse_request),
    priority=5,
    block=True,
)


@reverse_prompt_cmd.handle()
async def handle_reverse_prompt(
    matcher: Matcher,
    bot: Bot,
    event: MessageEvent,
):
    """从消息内/引用图片，或 @群友 头像反推 AI 绘画提示词（仅已开启 AI 功能的群可用）"""
    # 0. 群聊开关守卫：仅「已开启 AI 功能（ai_chat 模块）」的群可用；私聊放行
    gid = getattr(event, "group_id", None)
    if gid is not None and not is_ai_group_enabled(str(gid)):
        # 统一开关层约定：模块关闭时静默拦截，不回复
        await matcher.finish()
        return

    # 1. 提取图片（优先消息内图片，其次引用图片，最后 @群友 头像）
    image_url, source_desc = _extract_image(event)
    if not image_url:
        await matcher.finish(
            "请发送一张图片、引用含图片的消息，或 @一位群友后发送「图反推」"
        )
        return

    # 检测是否要求 DS 文本模型优化：消息含优化关键字，或本群已开启图反推润色开关（默认关）
    need_optimize = any(kw in _message_text(event) for kw in OPTIMIZE_KEYWORDS)
    if gid is not None and is_reverse_polish_enabled(str(gid)):
        need_optimize = True

    await matcher.send("正在反推提示词，请稍候...")

    # 进入队列：同一时刻仅一个图反推任务访问视觉模型，其余并发请求排队串行
    async with _reverse_lock:
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

        # 统一转码为 PNG（DeepSeek 明确支持的格式），并校验字节确为可解码图片
        try:
            image_data = _normalize_image(image_data)
        except Exception as e:
            logger.error(f"图反推图片解码失败: {e}")
            await matcher.finish("图片无法解码，请换一张图重试")
            return

        # 3. 调用 DeepSeek 视觉模型反推提示词
        try:
            client = _get_client()
            data_url = f"data:image/png;base64,{base64.b64encode(image_data).decode()}"
            # 注意：OpenAI SDK 的 chat.completions.create 是**同步阻塞**调用，
            # 必须放进线程池（asyncio.to_thread）以免冻结整个事件循环；
            # 视觉请求比纯文本慢，且不设 timeout 会永久挂起，故显式 timeout=60。
            # 关键：DeepSeek 视觉模型（deepseek-flash = V4.1-Flash）默认开启“思考模式”，
            # 会把整段 max_tokens 预算花在 reasoning_content 上，
            # 导致最终 content 为空（finish_reason=length）。显式关闭思考模式，
            # 确保直接输出提示词（已确认是官方视觉模型的已知行为，非本插件问题）。
            response = await asyncio.to_thread(
                client.chat.completions.create,
                model=VISION_MODEL,
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
                timeout=60,
                extra_body={"thinking": {"type": "disabled"}},
            )
            prompt = (response.choices[0].message.content or "").strip()
        except ValueError as e:
            # DeepSeek API Key 未配置
            await matcher.finish(f"图反推配置错误：{e}")
            return
        except Exception as e:
            logger.error(f"图反推调用 DeepSeek 失败: {e}")
            # 直接回显异常类型与信息便于排查，而非一律“请稍后再试”
            await matcher.finish(f"反推失败：{type(e).__name__} - {e}")
            return

        if not prompt:
            # 诊断：记录并向用户回显 finish_reason / 思考模式状态，便于排查
            try:
                choice = response.choices[0]
                fr = getattr(choice, "finish_reason", None)
                has_reason = bool(getattr(choice.message, "reasoning_content", None))
                logger.warning(
                    f"图反推返回空内容: model={VISION_MODEL}, finish_reason={fr}, "
                    f"有reasoning_content={has_reason}（多为视觉模型思考模式耗尽预算）"
                )
                detail = f"（finish_reason={fr}，思考模式={has_reason}）"
            except Exception:
                detail = ""
            await matcher.finish(f"未能反推出提示词，请换一张图重试{detail}")
            return

        # 3.5 可选：DS 文本模型润色（消息含优化关键字时触发，仍在串行锁内避免打爆 API）
        optimized_flag = False
        if need_optimize:
            try:
                optimized = await _optimize_prompt(prompt)
                if optimized:
                    prompt = optimized
                    optimized_flag = True
                else:
                    logger.warning("图反推优化返回空，回退原始反推词")
            except Exception as e:
                logger.error(f"图反推优化失败: {e}")
                # 优化失败不阻断主流程，回退原始反推词并提示
                await matcher.send(f"提示词优化失败（{type(e).__name__}），返回原始反推词")

        # 4. 返回结果（直接返回文本便于复制）
        msg = Message()
        if getattr(event, "message_id", None):
            msg.append(MessageSegment.reply(event.message_id))
        parts = ["以下为反推提示词"]
        if source_desc:
            parts.append(f"（来自{source_desc}）")
        if optimized_flag:
            parts.append("（已用 DeepSeek 润色）")
        parts.append("，可直接用于 NovelAI：")
        prefix = "".join(parts)
        msg.append(MessageSegment.text(f"{prefix}\n\n{prompt}"))
        await matcher.finish(msg)


def _extract_image(event: MessageEvent):
    """提取反推目标图片的 url，以及来源说明（用于回复文案）。

    优先级：消息内图片 > 引用消息中的图片 > @群友 头像。

    Returns:
        (image_url, source_desc)：无图片时两者均为空字符串。
    """
    # 1) 消息内图片
    url = _first_image_url(event.message)
    if url:
        return url, ""

    # 2) 引用（回复）消息中的图片
    reply = getattr(event, "reply", None)
    if reply and getattr(reply, "message", None):
        url = _first_image_url(reply.message)
        if url:
            return url, "引用的图片"

    # 3) @群友 头像
    at_qq = _extract_at_qq(event)
    if at_qq:
        return _avatar_url(at_qq), f"QQ {at_qq} 的头像"

    return "", ""


def _first_image_url(segments) -> str:
    """从消息段列表中取第一张图片的 url/file 字段"""
    for seg in segments:
        if seg.type == "image":
            url = seg.data.get("url") or seg.data.get("file")
            if url:
                return url
    return ""


def _extract_at_qq(event: MessageEvent) -> str:
    """从消息中提取第一个 @群友 的 QQ 号（忽略 @all）"""
    for seg in event.message:
        if seg.type == "at":
            qq = seg.data.get("qq")
            if qq and str(qq) != "all":
                return str(qq)
    return ""


def _avatar_url(qq: str) -> str:
    """根据 QQ 号构造群友头像 URL（spec=640 取最大可用尺寸）"""
    return f"https://q.qlogo.cn/headimg_dl?dst_uin={qq}&spec=640&img_type=jpg"


def _message_text(event: MessageEvent) -> str:
    """拼接消息内所有文本段的纯文本（用于关键字检测，忽略图片/At 等）"""
    return "".join(
        seg.data.get("text", "")
        for seg in event.message
        if seg.type == "text"
    )


async def _optimize_prompt(raw: str) -> str:
    """用 DS 文本模型（_get_model()，即 config.deepseek_model）对原始反推词做润色整理。

    复用 _get_client（共用 DEEPSEEK_API_KEY 与 base_url）；同步阻塞调用必须丢进线程池并设 timeout，
    避免冻结事件循环或永久挂起。返回空字符串表示优化失败/空结果。
    """
    client = _get_client()
    response = await asyncio.to_thread(
        client.chat.completions.create,
        model=_get_model(),
        messages=[
            {"role": "system", "content": OPTIMIZE_INSTRUCTION},
            {"role": "user", "content": raw},
        ],
        temperature=0.3,
        max_tokens=800,
        timeout=60,
    )
    return (response.choices[0].message.content or "").strip()


def _normalize_image(data: bytes, max_side: int = 1280) -> bytes:
    """将任意 PIL 可解码的图片解码并统一重编码为 PNG（DeepSeek 明确支持的格式）。

    作用：
    - 不再依赖扩展名/文件头“猜测” MIME，从根本上避免“实际字节非 webp/png/jpeg/gif
      却标成 image/jpeg 被 DeepSeek 拒图”的问题；
    - 同时校验字节确为可解码图片（若下载到的是错误 HTML 页等，会在这一步抛异常）；
    - 超长边超过 max_side 时等比缩图，降低 base64 体积与视觉 token 消耗。
    """
    img = Image.open(BytesIO(data))
    img = img.convert("RGB")  # 丢弃 alpha / 动画帧，统一为静态 RGB
    w, h = img.size
    if max(w, h) > max_side:
        scale = max_side / float(max(w, h))
        img = img.resize(
            (max(1, int(w * scale)), max(1, int(h * scale))),
            Image.LANCZOS,
        )
    out = BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


# ========== 开启图反推润色 / 关闭图反推润色（超级用户，按群）==========
# 与「开启AI / 关闭AI」同构：仅超管可在群聊中开关，按群记录到 reverse_polish_groups.json。
# 开启后该群图反推自动用 DeepSeek 文本模型润色（默认关；关键字「优化」仍可作临时触发）。
# 注意：不用 on_command（否则命令前缀/开头匹配规则导致超管在群里发「开启图反推润色」静默不触发），
# 改用 on_message + 自定义规则，消息任意文本段含对应短语即触发，与图反推触发方式一致。

async def _is_polish_enable_request(event: MessageEvent) -> bool:
    """消息文本段含「开启图反推润色/优化/美化」其一即视为开启请求"""
    text = "".join(
        seg.data.get("text", "")
        for seg in event.message
        if seg.type == "text"
    )
    return any(kw in text for kw in ("开启图反推润色", "开启图反推优化", "开启图反推美化"))


async def _is_polish_disable_request(event: MessageEvent) -> bool:
    """消息文本段含「关闭图反推润色/优化/美化」其一即视为关闭请求"""
    text = "".join(
        seg.data.get("text", "")
        for seg in event.message
        if seg.type == "text"
    )
    return any(kw in text for kw in ("关闭图反推润色", "关闭图反推优化", "关闭图反推美化"))


enable_polish_cmd = on_message(
    rule=Rule(_is_polish_enable_request),
    permission=SUPERUSER,
    priority=5,
    block=True,
)


@enable_polish_cmd.handle()
async def handle_enable_polish(matcher: Matcher, event: MessageEvent):
    """超级用户开启本群图反推自动润色"""
    if isinstance(event, PrivateMessageEvent):
        await matcher.finish("请在群聊中发送此指令。")
    gid = str(event.group_id)
    if is_reverse_polish_enabled(gid):
        await matcher.finish("本群图反推润色已经开启。")
    enable_reverse_polish(gid)
    await matcher.finish(
        "✅ 已开启本群图反推润色：图反推将自动用 DeepSeek 润色提示词"
        "（未开启的群仍可用「优化/美化」关键字临时触发）。"
    )


disable_polish_cmd = on_message(
    rule=Rule(_is_polish_disable_request),
    permission=SUPERUSER,
    priority=5,
    block=True,
)


@disable_polish_cmd.handle()
async def handle_disable_polish(matcher: Matcher, event: MessageEvent):
    """超级用户关闭本群图反推自动润色"""
    if isinstance(event, PrivateMessageEvent):
        await matcher.finish("请在群聊中发送此指令。")
    gid = str(event.group_id)
    if not is_reverse_polish_enabled(gid):
        await matcher.finish("本群图反推润色已经关闭。")
    disable_reverse_polish(gid)
    await matcher.finish(
        "❌ 已关闭本群图反推润色（仍可用「优化/美化」关键字临时触发）。"
    )


# ========== 反推AI图（图反推 + AI 画图，普通用户可用，需本群开启 AI 画图）==========
# 触发方式与图反推完全一致（消息内图片 / 引用含图消息 / @群友 头像），
# 反推出提示词后再直接调用 NovelAI 出图，返回形式与 ai画图 完全一致（合并转发图片）。

REVERSE_AI_DRAW_KEYWORDS = ("反推AI图", "反推ai图", "反推生图")


async def _is_reverse_ai_draw_request(event: MessageEvent) -> bool:
    """消息任意文本段含「反推AI图」等关键字即触发（与图反推触发方式一致）"""
    text = "".join(
        seg.data.get("text", "")
        for seg in event.message
        if seg.type == "text"
    )
    return any(kw in text for kw in REVERSE_AI_DRAW_KEYWORDS)


reverse_ai_draw_cmd = on_message(
    rule=Rule(_is_reverse_ai_draw_request),
    priority=5,
    block=True,
)


@reverse_ai_draw_cmd.handle()
async def handle_reverse_ai_draw(
    matcher: Matcher,
    bot: Bot,
    event: MessageEvent,
):
    """图反推 + AI 画图：从图片反推提示词后直接调 NovelAI 出图（返回与 ai画图 一致）"""
    # 仅在群聊可用（最终以合并转发形式发图，需 group_id）
    if isinstance(event, PrivateMessageEvent):
        await matcher.finish("⚠️ 反推AI图 仅支持在群聊中使用")
        return

    # 群聊守卫：需本群开启 AI 画图（ai_draw 模块）；未开启静默拦截（与统一开关层一致）
    gid = str(event.group_id)
    if not is_novelai_group_enabled(gid):
        await matcher.finish()
        return

    # 1. 提取图片（与图反推同优先级：消息内 > 引用 > @群友 头像）
    image_url, source_desc = _extract_image(event)
    if not image_url:
        await matcher.finish(
            "请发送一张图片、引用含图片的消息，或 @一位群友后发送「反推AI图」"
        )
        return

    # 2. 是否润色（与图反推一致：消息含优化关键字，或本群已开启图反推润色开关）
    need_optimize = any(kw in _message_text(event) for kw in OPTIMIZE_KEYWORDS)
    if is_reverse_polish_enabled(gid):
        need_optimize = True

    await matcher.send("正在反推提示词，请稍候...")

    # 3. 反推 + 可选润色（复用图反推的串行锁，避免并发打爆视觉模型）
    async with _reverse_lock:
        try:
            image_data = await download_image(bot, image_url)
        except Exception as e:
            logger.error(f"反推AI图 图片下载失败: {e}")
            await matcher.finish("图片下载失败，请重试")
            return
        if not image_data:
            await matcher.finish("图片下载失败，请重试")
            return
        try:
            image_data = _normalize_image(image_data)
        except Exception as e:
            logger.error(f"反推AI图 图片解码失败: {e}")
            await matcher.finish("图片无法解码，请换一张图重试")
            return

        # 3.1 视觉模型反推
        try:
            client = _get_client()
            data_url = f"data:image/png;base64,{base64.b64encode(image_data).decode()}"
            response = await asyncio.to_thread(
                client.chat.completions.create,
                model=VISION_MODEL,
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
                timeout=60,
                extra_body={"thinking": {"type": "disabled"}},
            )
            prompt = (response.choices[0].message.content or "").strip()
        except ValueError as e:
            await matcher.finish(f"反推AI图 配置错误：{e}")
            return
        except Exception as e:
            logger.error(f"反推AI图 视觉调用失败: {e}")
            await matcher.finish(f"反推失败：{type(e).__name__} - {e}")
            return

        if not prompt:
            try:
                choice = response.choices[0]
                fr = getattr(choice, "finish_reason", None)
                has_reason = bool(getattr(choice.message, "reasoning_content", None))
                detail = f"（finish_reason={fr}，思考模式={has_reason}）"
            except Exception:
                detail = ""
            await matcher.finish(f"未能反推出提示词，请换一张图重试{detail}")
            return

        # 3.2 可选 DS 文本润色（失败不阻断，回退原始反推词）
        if need_optimize:
            try:
                optimized = await _optimize_prompt(prompt)
                if optimized:
                    prompt = optimized
            except Exception as e:
                logger.error(f"反推AI图 优化失败: {e}")
                await matcher.send(f"提示词优化失败（{type(e).__name__}），使用原始反推词出图")

    # 4. 调 NovelAI 出图并合并转发发送（与 ai画图 完全一致）
    await draw_prompt_to_image(bot, event, matcher, prompt)
