"""
AI 对话模块 — 基于 DeepSeek API

触发方式：@机器人 + 问题文本（支持引用消息）
权限：仅群聊可用，默认关闭，超级管理员可开启/关闭
黑名单：超级管理员可管理用户黑名单
"""

import re
import time
from collections import defaultdict

from openai import OpenAI
from nonebot import on_command, on_message, get_driver, get_bots
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupMessageEvent,
    MessageEvent,
    MessageSegment,
    PrivateMessageEvent,
)
from nonebot.log import logger
from nonebot.matcher import Matcher
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.rule import Rule

from ..config import (
    is_ai_group_enabled,
    enable_ai_group,
    disable_ai_group,
    is_ai_blacklisted,
    add_ai_blacklist,
    remove_ai_blacklist,
)

# ========== 启动时检查 API Key ==========

driver = get_driver()


@driver.on_startup
async def _check_api_key():
    """bot 启动时检查 DeepSeek API Key 是否已配置"""
    api_key = getattr(driver.config, "deepseek_api_key", "")
    if not api_key:
        logger.error("=" * 50)
        logger.error("❌ DeepSeek API Key 未配置！AI 功能将不可用。")
        logger.error("请在 .env 文件中添加：DEEPSEEK_API_KEY=sk-xxx")
        logger.error("=" * 50)
    else:
        logger.info(f"DeepSeek API Key 已配置 (模型: {getattr(driver.config, 'deepseek_model', 'deepseek-v4-flash')})")

# ========== 聊天记忆 ==========

_MAX_HISTORY = 10          # 每个用户最多缓存多少轮
_MAX_HISTORY_AGE = 600     # 历史最大保留秒数（10分钟）

_chat_histories: dict[str, list[dict]] = defaultdict(list)

_SYSTEM_PROMPT = (
    "你是一个名叫「龙哥」的QQ群机器人助手，性格幽默、热情、乐于助人。"
    "回答问题时尽量简洁清晰，使用中文。"
    "如果用户问你是谁，告诉他们你是龙哥工具箱内置的 DeepSeek AI 助手。"
    "\n"
    "【重要】你是群机器人，没有实时联网能力。以下是机器人已经内置的功能，"
    "当用户的需求对应这些功能时，直接引导他们使用对应指令，不要自己编造回答：\n"
    "- 天气查询 → 告知用户使用「lg天气 城市名」，如 lg天气 深圳\n"
    "- 汇率换算 → 告知用户使用「lg换算 金额 币种1 币种2」，如 lg换算 100 人民币 美元\n"
    "- 抽签占卜 → 告知用户使用「抽签」\n"
    "- 宠物系统 → 告知用户使用「我的宠物」或「宠物帮助」\n"
    "- 积分系统 → 告知用户使用「积分」查看\n"
    "\n"
    "对于编程、常识、翻译、闲聊等知识类问题，你可以直接回答。"
    "对于需要实时数据的问题（天气温度、股价、新闻等），承认你无法获取，并引导用户使用内置命令或自行查询。"
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
    """将长文本拆成多段"""
    chunks = []
    current = ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > max_len:
            chunks.append(current)
            current = line
        else:
            current = (current + "\n" + line) if current else line
    if current:
        chunks.append(current)
    return chunks


def _get_client() -> OpenAI:
    """获取 OpenAI 客户端（指向 DeepSeek），如未配置 API Key 则直接报错"""
    try:
        driver = get_driver()
        config = driver.config
        api_key = getattr(config, "deepseek_api_key", "")
    except Exception:
        api_key = ""
    if not api_key:
        raise ValueError(
            "DeepSeek API Key 未配置！请在 .env 中设置 DEEPSEEK_API_KEY=sk-xxx"
        )
    return OpenAI(api_key=api_key, base_url="https://api.deepseek.com")


def _get_model() -> str:
    try:
        return getattr(get_driver().config, "deepseek_model", "deepseek-v4-flash")
    except Exception:
        return "deepseek-v4-flash"


def _extract_at_users(message) -> list[str]:
    """提取消息中 @ 的 QQ 号列表"""
    at_users = []
    for seg in message:
        if seg.type == "at" and seg.data.get("qq"):
            at_users.append(seg.data["qq"])
    return at_users


def _strip_at_segments(message) -> str:
    """去掉 @ 段落后返回纯文本"""
    parts = []
    for seg in message:
        if seg.type == "at":
            continue
        if seg.type == "text":
            parts.append(seg.data.get("text", ""))
    return "".join(parts).strip()


# ========== @bot 触发规则 ==========

async def _at_bot_rule(event: MessageEvent) -> bool:
    """检查消息是否 @了机器人"""
    if isinstance(event, PrivateMessageEvent):
        return False

    # 优先用 NoneBot 内置方法
    if hasattr(event, "is_tome") and callable(event.is_tome):
        result = event.is_tome()
        if result:
            logger.debug(f"AI @bot 匹配 (is_tome): group={getattr(event, 'group_id', 'N/A')}")
        return result

    # 兜底：手动遍历 @ 段
    try:
        bots = get_bots()
        if not bots:
            logger.warning("AI @bot: get_bots() 返回空，跳过")
            return False
        bot_self_id = list(bots.keys())[0]
        logger.debug(f"AI @bot: bot_self_id={bot_self_id}, checking message segments")
    except Exception as e:
        logger.error(f"AI @bot: get_bots() 异常: {e}")
        return False

    for seg in event.message:
        seg_qq = seg.data.get("qq", "") if seg.type == "at" else ""
        if seg.type == "at" and str(seg_qq) == str(bot_self_id):
            logger.debug(f"AI @bot 匹配 (手动): group={getattr(event, 'group_id', 'N/A')}")
            return True

    logger.debug(f"AI @bot 未匹配: group={getattr(event, 'group_id', 'N/A')}")
    return False


# ========== @bot AI 对话 ==========

ai_chat_matcher = on_message(
    rule=Rule(_at_bot_rule),
    priority=10,
    block=False,
)


@ai_chat_matcher.handle()
async def handle_at_bot_chat(matcher: Matcher, bot: Bot, event: GroupMessageEvent):
    """处理 @机器人的 AI 对话"""
    group_id = str(event.group_id)
    user_id = str(event.user_id)

    # 1. 检查群聊是否开启了 AI
    if not is_ai_group_enabled(group_id):
        logger.debug(f"AI @bot: 群 {group_id} 未开启 AI，忽略")
        return

    # 2. 检查用户是否在黑名单
    if is_ai_blacklisted(user_id):
        logger.debug(f"AI @bot: 用户 {user_id} 在黑名单，忽略")
        return

    # 3. 构建 prompt
    prompt_parts = []

    # 如果有引用消息，把引用内容带上
    if event.reply:
        reply_text = event.reply.message.extract_plain_text().strip()
        if reply_text:
            prompt_parts.append(f"（用户引用了以下消息作为上下文）\n{reply_text}\n（以下是用户的问题）")

    # 提取 @bot 后的文本（去掉 @bot 本身和空白）
    text = _strip_at_segments(event.message)
    if text:
        prompt_parts.append(text)

    if not prompt_parts:
        await matcher.send("有什么事吗？@我然后说你想问的就好啦～", reply_message=True)
        return

    prompt = "\n".join(prompt_parts).strip()
    if not prompt:
        return

    # 4. 检查 API 是否可用
    try:
        client = _get_client()
    except ValueError as e:
        logger.error(f"AI API Key 未配置: {e}")
        await matcher.send(f"AI 功能未配置 API Key，请联系管理员。\n请在 .env 中设置 DEEPSEEK_API_KEY", reply_message=True)
        return

    model = _get_model()

    # 5. 保存 + 构建消息
    _add_history(user_id, "user", prompt)
    messages = _build_messages(user_id, prompt)

    # 6. 调用 API
    try:
        logger.info(f"AI @bot: user={user_id} group={group_id}, prompt={prompt[:50]}...")
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.7,
            max_tokens=2000,
        )
        reply = response.choices[0].message.content.strip()
        logger.info(f"AI 回复: {reply[:50]}...")
    except Exception as e:
        logger.error(f"AI 调用失败: {e}")
        await matcher.send(f"AI 暂时无法回复，请稍后再试。", reply_message=True)
        return

    _add_history(user_id, "assistant", reply)

    # 回复：超过 100 字用合并转发，否则直接引用回复
    if len(reply) > 100:
        # 合并转发消息格式
        bot_name = "龙哥"
        try:
            bot_info = await bot.get_login_info()
            bot_name = bot_info.get("nickname", "龙哥")
        except Exception:
            pass

        forward_msgs = []
        chunks = _split_long_message(reply)
        for chunk in chunks:
            forward_msgs.append({
                "type": "node",
                "data": {
                    "name": bot_name,
                    "uin": bot.self_id,
                    "content": chunk,
                },
            })

        try:
            await bot.call_api(
                "send_group_forward_msg",
                group_id=event.group_id,
                messages=forward_msgs,
            )
        except Exception as e:
            logger.warning(f"合并转发失败，降级为普通回复: {e}")
            for chunk in chunks:
                await matcher.send(chunk, reply_message=True)
    else:
        await matcher.send(reply, reply_message=True)


# ========== lg清记忆 指令 ==========

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


# ========== 开启AI（超级用户）==========

enable_ai_cmd = on_command(
    "开启AI",
    aliases={"开启ai", "开启lgai", "启用AI", "启用ai"},
    permission=SUPERUSER,
    priority=5,
    block=True,
    force_whitespace=True,
)


@enable_ai_cmd.handle()
async def handle_enable_ai(matcher: Matcher, event: MessageEvent):
    """超级用户开启群聊 AI 功能"""
    if isinstance(event, PrivateMessageEvent):
        await matcher.finish("请在群聊中发送此指令。")
    group_id = str(event.group_id)
    if is_ai_group_enabled(group_id):
        await matcher.finish("AI 功能已经开启。")
    enable_ai_group(group_id)
    await matcher.finish("✅ 已开启本群 AI 功能，@机器人 + 问题 即可使用！")


# ========== 关闭AI（超级用户）==========

disable_ai_cmd = on_command(
    "关闭AI",
    aliases={"关闭ai", "关闭lgai", "禁用AI", "禁用ai"},
    permission=SUPERUSER,
    priority=5,
    block=True,
    force_whitespace=True,
)


@disable_ai_cmd.handle()
async def handle_disable_ai(matcher: Matcher, event: MessageEvent):
    """超级用户关闭群聊 AI 功能"""
    if isinstance(event, PrivateMessageEvent):
        await matcher.finish("请在群聊中发送此指令。")
    group_id = str(event.group_id)
    if not is_ai_group_enabled(group_id):
        await matcher.finish("AI 功能已经关闭。")
    disable_ai_group(group_id)
    await matcher.finish("❌ 已关闭本群 AI 功能。")


# ========== AI拉黑（超级用户）==========

ai_blacklist_cmd = on_command(
    "AI拉黑",
    aliases={"ai拉黑", "lgai拉黑"},
    permission=SUPERUSER,
    priority=5,
    block=True,
)


@ai_blacklist_cmd.handle()
async def handle_ai_blacklist(matcher: Matcher, event: MessageEvent):
    """超级用户将用户加入 AI 黑名单
    用法：AI拉黑 @用户  或  AI拉黑 QQ号
    """
    # 优先从 @ 提取
    at_users = _extract_at_users(event.message)
    if at_users:
        target_id = at_users[0]
    else:
        text = event.get_plaintext().strip()
        # 去掉命令名，提取 QQ 号
        parts = text.split()
        if len(parts) >= 2 and parts[-1].isdigit():
            target_id = parts[-1]
        else:
            await matcher.finish("请 @要拉黑的用户，或输入 QQ 号。\n例如：AI拉黑 @某人  或  AI拉黑 123456789")
            return

    if is_ai_blacklisted(target_id):
        await matcher.finish(f"用户 {target_id} 已在黑名单中。")

    add_ai_blacklist(target_id)
    await matcher.finish(f"✅ 已将用户 {target_id} 加入 AI 黑名单，该用户无法使用 AI 功能。")


# ========== AI解除（超级用户）==========

ai_unblacklist_cmd = on_command(
    "AI解除",
    aliases={"ai解除", "lgai解除", "AI取消拉黑", "ai取消拉黑"},
    permission=SUPERUSER,
    priority=5,
    block=True,
)


@ai_unblacklist_cmd.handle()
async def handle_ai_unblacklist(matcher: Matcher, event: MessageEvent):
    """超级用户将用户移出 AI 黑名单
    用法：AI解除 @用户  或  AI解除 QQ号
    """
    at_users = _extract_at_users(event.message)
    if at_users:
        target_id = at_users[0]
    else:
        text = event.get_plaintext().strip()
        parts = text.split()
        if len(parts) >= 2 and parts[-1].isdigit():
            target_id = parts[-1]
        else:
            await matcher.finish("请 @要解除的用户，或输入 QQ 号。\n例如：AI解除 @某人  或  AI解除 123456789")
            return

    if not is_ai_blacklisted(target_id):
        await matcher.finish(f"用户 {target_id} 不在黑名单中。")

    remove_ai_blacklist(target_id)
    await matcher.finish(f"✅ 已将用户 {target_id} 移出 AI 黑名单，恢复 AI 使用权限。")
