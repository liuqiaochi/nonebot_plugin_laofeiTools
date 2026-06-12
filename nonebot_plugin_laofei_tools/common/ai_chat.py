"""
AI 对话模块 — 基于 DeepSeek API
"""

import time
from collections import defaultdict

from openai import OpenAI
from nonebot import on_command, get_driver
from nonebot.adapters.onebot.v11 import MessageEvent, MessageSegment
from nonebot.log import logger
from nonebot.matcher import Matcher

from ..config import Config

# 聊天记录缓存：{user_id: [(role, content, timestamp), ...]}
_MAX_HISTORY = 10          # 每个用户最多缓存多少轮
_MAX_HISTORY_AGE = 600     # 历史最大保留秒数（10分钟）

_chat_histories: dict[str, list[dict]] = defaultdict(list)

# 系统提示词
_SYSTEM_PROMPT = (
    "你是一个名叫「龙哥」的QQ群机器人助手，性格幽默、热情、乐于助人。"
    "回答问题时尽量简洁清晰，使用中文。"
    "如果用户问你是谁，告诉他们你是龙哥工具箱内置的 DeepSeek AI 助手。"
)


def _get_client() -> OpenAI | None:
    """获取 OpenAI 客户端（指向 DeepSeek）"""
    try:
        driver = get_driver()
        config = driver.config
        api_key = getattr(config, "deepseek_api_key", "")
        model = getattr(config, "deepseek_model", "deepseek-v4-flash")
    except Exception:
        api_key = ""
        model = "deepseek-v4-flash"

    if not api_key:
        return None

    return OpenAI(
        api_key=api_key,
        base_url="https://api.deepseek.com",
    )


def _clean_history(user_id: str):
    """清理过期的聊天记录"""
    now = time.time()
    history = _chat_histories[user_id]
    _chat_histories[user_id] = [
        h for h in history if now - h.get("ts", 0) < _MAX_HISTORY_AGE
    ]


def _add_history(user_id: str, role: str, content: str):
    """添加一条聊天记录"""
    _clean_history(user_id)
    _chat_histories[user_id].append({
        "role": role,
        "content": content,
        "ts": time.time(),
    })
    # 只保留最近 N 轮（每轮 user+assistant 两条）
    if len(_chat_histories[user_id]) > _MAX_HISTORY * 2:
        _chat_histories[user_id] = _chat_histories[user_id][-_MAX_HISTORY * 2:]


def _build_messages(user_id: str, prompt: str) -> list[dict]:
    """构建发送给 API 的消息列表"""
    messages = [{"role": "system", "content": _SYSTEM_PROMPT}]
    _clean_history(user_id)
    for h in _chat_histories[user_id]:
        messages.append({"role": h["role"], "content": h["content"]})
    messages.append({"role": "user", "content": prompt})
    return messages


def _split_long_message(text: str, max_len: int = 4000) -> list[str]:
    """将长文本拆成多段（QQ 消息有长度限制）"""
    chunks = []
    current = ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > max_len:
            chunks.append(current)
            current = line
        else:
            if current:
                current += "\n" + line
            else:
                current = line
    if current:
        chunks.append(current)
    return chunks


# ========== lg问 指令 ==========

ai_cmd = on_command(
    "lg问",
    aliases={"lgai", "lg AI", "lg问ai", "问ai", "龙哥问答", "lg chat"},
    priority=5,
    block=True,
    force_whitespace=True,
)


@ai_cmd.handle()
async def handle_ai_chat(matcher: Matcher, event: MessageEvent):
    """处理 AI 对话"""
    # 解析用户问题
    full_text = event.get_plaintext().strip()
    if " " in full_text:
        prompt = full_text.split(" ", 1)[1].strip()
    else:
        prompt = ""

    if not prompt:
        await matcher.finish(
            "请告诉我你想问什么，例如：\n"
            "lg问 今天天气怎么样\n"
            "lg问 帮我写一段Python代码"
        )

    user_id = str(event.user_id)

    client = _get_client()
    if client is None:
        await matcher.finish("AI 功能未配置 API Key，请联系管理员设置 deepseek_api_key。")

    # 获取模型名
    try:
        config = get_driver().config
        model = getattr(config, "deepseek_model", "deepseek-v4-flash")
    except Exception:
        model = "deepseek-v4-flash"

    # 保存用户问题到历史
    _add_history(user_id, "user", prompt)

    # 构建消息列表（含历史）
    messages = _build_messages(user_id, prompt)

    # 调用 DeepSeek API
    try:
        logger.info(f"AI 对话: user={user_id}, prompt={prompt[:50]}...")
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.7,
            max_tokens=2000,
        )
        reply = response.choices[0].message.content.strip()
        logger.info(f"AI 回复: {reply[:50]}...")

    except Exception as e:
        logger.error(f"AI 对话失败: {e}")
        await matcher.finish(f"AI 暂时无法回复，请稍后再试。\n错误：{e}")

    # 保存回复到历史
    _add_history(user_id, "assistant", reply)

    # 发送回复（超长时拆分）
    chunks = _split_long_message(reply)
    for i, chunk in enumerate(chunks):
        if i == 0:
            await matcher.send(chunk)
        else:
            await matcher.send(chunk)


# ========== lg清记忆 指令（清除对话历史）==========

clear_cmd = on_command(
    "lg清记忆",
    aliases={"lg清空记忆", "lg清除记忆", "lg forget"},
    priority=5,
    block=True,
    force_whitespace=True,
)


@clear_cmd.handle()
async def handle_clear_history(matcher: Matcher, event: MessageEvent):
    """清除当前用户的对话历史"""
    user_id = str(event.user_id)
    if user_id in _chat_histories:
        del _chat_histories[user_id]
        await matcher.finish("已清除你的对话记忆，下次对话将重新开始。")
    else:
        await matcher.finish("当前没有对话记忆，无需清除。")
