"""
LLM 服务商切换指令（超管，按群）

- 对话模型 <deepseek|grok|gemini>  /  反推模型 <deepseek|grok|gemini>  ：设置本群「对话」/「图反推」的服务商
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
import re

from . import llm_providers


def _msg(event: MessageEvent) -> str:
    return "".join(
        seg.data.get("text", "") for seg in event.message if seg.type == "text"
    )


def _parse_set(text: str):
    """从「<用途>模型 <服务商> [模型名]」指令解析 (provider, model)。

    模型名可选；未指定时为 None（仅切服务商，清除模型名 override）。
    """
    cleaned = re.sub(r"(反推模型|对话模型|重置)", "", text, flags=re.I)
    toks = cleaned.split()
    provider = None
    idx = -1
    for i, tok in enumerate(toks):
        tl = tok.lower()
        if "gemini" in tl:
            provider = "gemini"; idx = i; break
        if "grok" in tl:
            provider = "grok"; idx = i; break
        if "deepseek" in tl or tl == "ds" or tl.startswith("ds"):
            provider = "deepseek"; idx = i; break
    if provider is None:
        return None, None
    model = " ".join(toks[idx + 1:]).strip() or None
    return provider, model


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


def _is_help(event: MessageEvent) -> bool:
    t = _msg(event)
    return any(k in t for k in ("模型帮助", "支持模型", "模型列表", "切换模型帮助", "模型切换帮助"))


# ---------------- 设置对话服务商 ----------------

set_chat_cmd = on_message(rule=Rule(_is_set_chat), permission=SUPERUSER, priority=5, block=True)


@set_chat_cmd.handle()
async def handle_set_chat(matcher, event: MessageEvent):
    """超管设置本群「对话」使用的 LLM 服务商"""
    if isinstance(event, PrivateMessageEvent):
        await matcher.finish("请在群聊中发送此指令。")
    gid = str(event.group_id)
    p, model = _parse_set(_msg(event))
    if not p:
        await matcher.finish("请指定服务商：对话模型 deepseek / grok / gemini [模型名]")
    ok, msg = llm_providers.set_provider(gid, "ai_chat", p, model if model else "")
    await matcher.finish(("✅ " if ok else "❌ ") + msg)


# ---------------- 设置图反推服务商 ----------------

set_reverse_cmd = on_message(rule=Rule(_is_set_reverse), permission=SUPERUSER, priority=5, block=True)


@set_reverse_cmd.handle()
async def handle_set_reverse(matcher, event: MessageEvent):
    """超管设置本群「图反推」使用的 LLM 服务商"""
    if isinstance(event, PrivateMessageEvent):
        await matcher.finish("请在群聊中发送此指令。")
    gid = str(event.group_id)
    p, model = _parse_set(_msg(event))
    if not p:
        await matcher.finish("请指定服务商：反推模型 deepseek / grok / gemini [模型名]")
    ok, msg = llm_providers.set_provider(gid, "reverse", p, model if model else "")
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
    chat_model = llm_providers.get_chat_model(gid, "ai_chat")
    rev_model = llm_providers.get_vision_model(gid, "reverse")
    lines = [
        "本群 LLM 模型设置：",
        f"· 对话：{label(chat)}（{chat_model}）" + ("" if chat == dchat else f"（默认：{label(dchat)}）"),
        f"· 图反推：{label(rev)}（{rev_model}）" + ("" if rev == drev else f"（默认：{label(drev)}）"),
        "",
        "切换：超管发送「对话模型 deepseek/grok/gemini [模型名]」「反推模型 deepseek/grok/gemini [模型名]」",
        "重置：超管发送「重置对话模型」「重置反推模型」",
        "全部可切换模型：发送「模型帮助」",
    ]
    await matcher.finish(MessageSegment.text("\n".join(lines)))


# ---------------- 模型切换帮助（群成员可用） ----------------

help_cmd = on_message(rule=Rule(_is_help), priority=5, block=True)


@help_cmd.handle()
async def handle_help(matcher, event: MessageEvent):
    """列出可切换的服务商、默认/实时模型及切换指令（群成员可用）"""
    if isinstance(event, PrivateMessageEvent):
        await matcher.finish("请在群聊中发送此指令。")
    force = "刷新" in _msg(event)
    live = await llm_providers.get_all_available_models(force_refresh=force)
    text = llm_providers.format_model_help(live_models=live)
    if force:
        text = "（已强制刷新可用模型列表）\n" + text
    await matcher.finish(MessageSegment.text(text))
