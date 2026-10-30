"""
LLM 服务商切换指令（超管，按群）

- 对话模型 <deepseek|grok>  /  反推模型 <deepseek|grok>  ：设置本群「对话」/「图反推」的服务商
- 重置对话模型  /  重置反推模型                      ：清除本群该项 override，回退全局默认
- 模型设置  /  查看模型                              ：查看本群当前配置 + 全局默认（群成员可用）

仅超管可切换；查看指令所有群成员可用。底层逻辑见 llm_providers.py。
"""

from nonebot import on_message
from nonebot.adapters.onebot.v11 import (
    MessageEvent,
    MessageSegment,
    PrivateMessageEvent,
)
from nonebot.permission import SUPERUSER
from nonebot.rule import Rule

from . import llm_providers


def _msg(event: MessageEvent) -> str:
    return "".join(
        seg.data.get("text", "") for seg in event.message if seg.type == "text"
    )


def _parse_provider(text: str):
    t = text.lower()
    if "grok" in t:
        return "grok"
    if "deepseek" in t or " ds" in t or t.startswith("ds"):
        return "deepseek"
    return None


def _is_set_chat(event: MessageEvent) -> bool:
    t = _msg(event)
    # 排除「重置对话模型」，避免与重置指令冲突
    return "对话模型" in t and "重置" not in t


def _is_set_reverse(event: MessageEvent) -> bool:
    t = _msg(event)
    return "反推模型" in t and "重置" not in t


def _is_reset_chat(event: MessageEvent) -> bool:
    return "重置对话模型" in _msg(event)


def _is_reset_reverse(event: MessageEvent) -> bool:
    return "重置反推模型" in _msg(event)


def _is_view(event: MessageEvent) -> bool:
    return ("模型设置" in _msg(event)) or ("查看模型" in _msg(event))


# ---------------- 设置对话服务商 ----------------

set_chat_cmd = on_message(rule=Rule(_is_set_chat), permission=SUPERUSER, priority=5, block=True)


@set_chat_cmd.handle()
async def handle_set_chat(matcher, event: MessageEvent):
    """超管设置本群「对话」使用的 LLM 服务商"""
    if isinstance(event, PrivateMessageEvent):
        await matcher.finish("请在群聊中发送此指令。")
    gid = str(event.group_id)
    p = _parse_provider(_msg(event))
    if not p:
        await matcher.finish("请指定服务商：对话模型 deepseek 或 对话模型 grok")
    ok, msg = llm_providers.set_provider(gid, "ai_chat", p)
    await matcher.finish(("✅ " if ok else "❌ ") + msg)


# ---------------- 设置图反推服务商 ----------------

set_reverse_cmd = on_message(rule=Rule(_is_set_reverse), permission=SUPERUSER, priority=5, block=True)


@set_reverse_cmd.handle()
async def handle_set_reverse(matcher, event: MessageEvent):
    """超管设置本群「图反推」使用的 LLM 服务商"""
    if isinstance(event, PrivateMessageEvent):
        await matcher.finish("请在群聊中发送此指令。")
    gid = str(event.group_id)
    p = _parse_provider(_msg(event))
    if not p:
        await matcher.finish("请指定服务商：反推模型 deepseek 或 反推模型 grok")
    ok, msg = llm_providers.set_provider(gid, "reverse", p)
    await matcher.finish(("✅ " if ok else "❌ ") + msg)


# ---------------- 重置对话服务商 ----------------

reset_chat_cmd = on_message(rule=Rule(_is_reset_chat), permission=SUPERUSER, priority=5, block=True)


@reset_chat_cmd.handle()
async def handle_reset_chat(matcher, event: MessageEvent):
    """超管清除本群「对话」的服务商 override，回退全局默认"""
    if isinstance(event, PrivateMessageEvent):
        await matcher.finish("请在群聊中发送此指令。")
    gid = str(event.group_id)
    ok, msg = llm_providers.reset_provider(gid, "ai_chat")
    await matcher.finish(("✅ " if ok else "ℹ️ ") + msg)


# ---------------- 重置图反推服务商 ----------------

reset_reverse_cmd = on_message(rule=Rule(_is_reset_reverse), permission=SUPERUSER, priority=5, block=True)


@reset_reverse_cmd.handle()
async def handle_reset_reverse(matcher, event: MessageEvent):
    """超管清除本群「图反推」的服务商 override，回退全局默认"""
    if isinstance(event, PrivateMessageEvent):
        await matcher.finish("请在群聊中发送此指令。")
    gid = str(event.group_id)
    ok, msg = llm_providers.reset_provider(gid, "reverse")
    await matcher.finish(("✅ " if ok else "ℹ️ ") + msg)


# ---------------- 查看本群模型设置 ----------------

view_cmd = on_message(rule=Rule(_is_view), priority=5, block=True)


@view_cmd.handle()
async def handle_view(matcher, event: MessageEvent):
    """查看本群「对话 / 图反推」当前所选服务商及全局默认（群成员可用）"""
    if isinstance(event, PrivateMessageEvent):
        await matcher.finish("请在群聊中发送此指令。")
    gid = str(event.group_id)
    chat = llm_providers.resolve_provider(gid, "ai_chat")
    rev = llm_providers.resolve_provider(gid, "reverse")
    dchat = llm_providers.get_default_provider("ai_chat")
    drev = llm_providers.get_default_provider("reverse")
    label = llm_providers.provider_label
    lines = [
        "本群 LLM 模型设置：",
        f"· 对话：{label(chat)}" + ("" if chat == dchat else f"（默认：{label(dchat)}）"),
        f"· 图反推：{label(rev)}" + ("" if rev == drev else f"（默认：{label(drev)}）"),
        "",
        "切换：超管发送「对话模型 deepseek/grok」「反推模型 deepseek/grok」",
        "重置：超管发送「重置对话模型」「重置反推模型」",
    ]
    await matcher.finish(MessageSegment.text("\n".join(lines)))
