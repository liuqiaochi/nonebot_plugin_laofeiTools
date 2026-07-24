"""
休息/静默模式 - 超管指令

指令（仅超管）：
- lg休息 分钟数：进入静默模式，期间不回复任何消息（逻辑正常处理）
- lg结束休息：提前结束静默模式
"""

import re
import time
from typing import Any, Dict

from nonebot import get_driver, on_command
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent, MessageSegment
from nonebot.log import logger
from nonebot.matcher import Matcher
from nonebot.permission import SUPERUSER

# ========== 全局状态 ==========

rest_until: float = 0.0  # 休息截止的 Unix 时间戳，0 表示不在休息模式

# 需要拦截的消息发送 API
_MSG_APIS = frozenset({
    "send_msg",
    "send_group_msg",
    "send_private_msg",
    "send_group_forward_msg",
})


# ========== API 拦截钩子 ==========

driver = get_driver()


@driver.on_bot_connect
async def _patch_bot_for_rest_mode(bot: Bot):
    """在每个 Bot 连接后，为其 call_api 添加休息模式拦截。

    使用 monkey-patch 方式替代 on_calling_api 钩子，
    兼容所有 NoneBot2 版本（包括 CombinedDriver）。
    """
    _original_call_api = bot.call_api

    async def _patched_call_api(api: str, **data: Any) -> Any:
        if api in _MSG_APIS and rest_until and time.time() < rest_until:
            remaining = int(rest_until - time.time())
            logger.debug(f"[休息模式] 拦截消息发送 ({api})，剩余 {remaining}s")
            if api == "send_group_forward_msg":
                return {}
            return {"message_id": 0}
        return await _original_call_api(api=api, **data)

    bot.call_api = _patched_call_api  # type: ignore[method-assign]
    logger.info(f"[休息模式] Bot 消息拦截已就绪: {bot.self_id}")


# ========== lg休息 指令 ==========

rest_cmd = on_command(
    "lg休息",
    aliases={"休息"},
    permission=SUPERUSER,
    priority=1,
    block=True,
    force_whitespace=True,
)


@rest_cmd.handle()
async def handle_rest(matcher: Matcher, event: MessageEvent):
    """处理 lg休息 指令：进入静默模式"""
    global rest_until
    msg = event.get_plaintext().strip()

    # 如果已在休息模式
    if rest_until and time.time() < rest_until:
        remaining = int(rest_until - time.time())
        await matcher.finish(f"已经在休息中啦～还剩 {remaining // 60} 分 {remaining % 60} 秒 💤")
        return

    nums = re.findall(r"\d+", msg)
    if not nums:
        await matcher.finish("用法: lg休息 分钟数（如 lg休息 60 表示休息60分钟）")
        return

    minutes = int(nums[0])
    if minutes <= 0:
        await matcher.finish("请输入大于0的分钟数")
        return

    # 先发送立即回复（此时 rest_until 仍为 0/过期，不会被拦截）
    await matcher.send(Message([
        MessageSegment.reply(event.message_id),
        MessageSegment.text("龍哥我要小憩一会了！💤"),
    ]))
    # 再设置静默状态，之后所有消息都会被拦截
    rest_until = time.time() + minutes * 60
    logger.info(f"[休息模式] 进入静默 {minutes} 分钟，截止时间戳: {rest_until}")


# ========== lg结束休息 指令 ==========

end_rest_cmd = on_command(
    "lg结束休息",
    aliases={"结束休息"},
    permission=SUPERUSER,
    priority=1,
    block=True,
    force_whitespace=True,
)


@end_rest_cmd.handle()
async def handle_end_rest(matcher: Matcher, event: MessageEvent):
    """处理 lg结束休息 指令：提前结束静默模式"""
    global rest_until

    if not rest_until or time.time() >= rest_until:
        await matcher.finish("当前没有在休息哦～")
        return

    # 先结束休息状态
    rest_until = 0.0
    # 再发送回复（此时已不在休息模式，不会被拦截）
    await matcher.send(Message([
        MessageSegment.reply(event.message_id),
        MessageSegment.text("龍哥我休息好啦！💪"),
    ]))
    logger.info("[休息模式] 提前结束静默")
