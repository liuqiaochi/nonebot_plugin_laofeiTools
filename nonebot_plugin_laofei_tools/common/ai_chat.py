"""
百度千帆 AI 聊天模块

功能：
- @bot + 发送内容 / 引用消息 → AI 分析并回复
- 默认关闭，超管通过「开启ai」「关闭ai」管理群开关
- 私聊完全禁用
- 需在插件配置中设置 longge_qianfan_api_key 才能使用

鉴权：bce-v3 格式 Key 直接用作 Bearer Token（v2 接口，兼容 OpenAI 协议）
"""

from typing import Optional

from nonebot import get_driver, on_command, on_message
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
from nonebot.permission import SUPERUSER
from nonebot.exception import FinishedException
from nonebot.rule import to_me

from ..config import enable_ai, disable_ai, is_ai_enabled

# 千帆 v2 接口（OpenAI 兼容）
_V2_CHAT_URL = "https://qianfan.baidubce.com/v2/chat/completions"

# 使用的模型
_MODEL = "ernie-speed-pro-128k"

# 系统提示词
_SYSTEM_PROMPT = (
    "你是一个 QQ 群聊中的 AI 助手，名字叫龙哥。"
    "你的回复应当简洁、友好、有帮助。"
    "用中文回复，不要过长，控制在 200 字以内。"
    "如果用户的问题需要较长回复，可以分段但要保持精炼。"
)

# ========== 配置检查 ==========


def _is_qianfan_configured() -> bool:
    """检查千帆 API Key 是否已在插件配置中设置"""
    driver = get_driver()
    key = getattr(driver.config, "longge_qianfan_api_key", "")
    return bool(key and key.strip())


def _get_api_key() -> str:
    """获取千帆 API Key"""
    driver = get_driver()
    return getattr(driver.config, "longge_qianfan_api_key", "").strip()


# ========== AI 对话调用 ==========


async def _chat(prompt: str) -> str:
    """调用千帆 v2 接口（Bearer Token，OpenAI 兼容协议）"""
    import httpx

    api_key = _get_api_key()

    payload = {
        "model": _MODEL,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
    }

    async with httpx.AsyncClient(timeout=60.0, trust_env=False) as client:
        resp = await client.post(
            _V2_CHAT_URL,
            json=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
        )
        data = resp.json()

    if resp.status_code != 200 or "error" in data:
        error_msg = data.get("error", {}).get("message", str(data))
        raise RuntimeError(f"千帆 API 错误 [{resp.status_code}]: {error_msg}")

    result = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    if not result:
        logger.warning(f"千帆 API 返回为空: {data}")
        return "抱歉，AI 没有返回内容，请稍后再试。"

    return result.strip()


# ========== 群开关指令（超管） ==========

enable_ai_cmd = on_command(
    "开启ai",
    permission=SUPERUSER,
    priority=5,
    block=True,
    force_whitespace=True,
)


@enable_ai_cmd.handle()
async def handle_enable_ai(matcher: Matcher, event: MessageEvent):
    if isinstance(event, PrivateMessageEvent):
        await matcher.finish("AI 功能仅在群聊可用，请发送「开启群号」来开启（功能开发中）")

    # 检查 API Key 是否已配置
    if not _is_qianfan_configured():
        await matcher.finish(
            "AI 功能未配置千帆 API Key，无法使用。\n"
            "请在 .env 中设置 LONGGE_QIANFAN_API_KEY=你的Key"
        )

    group_id = str(event.group_id)
    if is_ai_enabled(group_id):
        await matcher.finish(f"本群 AI 功能已开启")
    enable_ai(group_id)
    await matcher.finish("AI 功能已开启！@我 发送内容即可对话")


disable_ai_cmd = on_command(
    "关闭ai",
    permission=SUPERUSER,
    priority=5,
    block=True,
    force_whitespace=True,
)


@disable_ai_cmd.handle()
async def handle_disable_ai(matcher: Matcher, event: MessageEvent):
    if isinstance(event, PrivateMessageEvent):
        await matcher.finish("请在群聊中使用此指令")

    group_id = str(event.group_id)
    if not is_ai_enabled(group_id):
        await matcher.finish("本群 AI 功能未开启")
    disable_ai(group_id)
    await matcher.finish("AI 功能已关闭")


# ========== @bot AI 对话 ==========

ai_chat_matcher = on_message(rule=to_me(), priority=99, block=False)


@ai_chat_matcher.handle()
async def handle_ai_chat(bot: Bot, event: MessageEvent, matcher: Matcher):
    # API Key 未配置则完全静默不处理
    if not _is_qianfan_configured():
        return

    # 私聊完全禁用
    if isinstance(event, PrivateMessageEvent):
        return

    # 仅处理群聊
    if not isinstance(event, GroupMessageEvent):
        return

    group_id = str(event.group_id)

    # 检查群是否开启
    if not is_ai_enabled(group_id):
        return

    # 提取用户输入内容
    prompt = _extract_prompt(event)
    if not prompt:
        return

    logger.info(f"AI 对话 [{group_id}] {event.user_id}: {prompt[:100]}")

    try:
        reply = await _chat(prompt)
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text(reply),
        ]))
    except FinishedException:
        raise
    except Exception as e:
        logger.exception("千帆 AI 调用失败")
        error_msg = str(e)
        if len(error_msg) > 100:
            error_msg = error_msg[:100] + "..."
        await matcher.send(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text(f"AI 暂时无法回复：{error_msg}"),
        ]))


def _extract_prompt(event: GroupMessageEvent) -> Optional[str]:
    """从事件中提取用户输入内容"""
    # 优先使用引用消息的内容
    if event.reply:
        reply_msg = event.reply.message
        # 提取引用消息中的文本
        texts = []
        for seg in reply_msg:
            if seg.type == "text":
                texts.append(seg.data.get("text", ""))
        if texts:
            return "用户引用了以下内容，请分析并回复：\n" + "".join(texts)

    # 否则使用当前消息的文本（排除 @mention 段）
    texts = []
    for seg in event.message:
        if seg.type == "text":
            texts.append(seg.data.get("text", ""))
        elif seg.type == "image":
            # 图片暂不支持分析，给出提示
            texts.append("[用户发送了一张图片]")

    if not texts:
        return None

    content = "".join(texts).strip()
    if not content:
        return None

    return content
