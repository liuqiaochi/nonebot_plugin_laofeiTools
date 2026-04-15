"""
积分系统指令处理

功能：签到、积分查询、转账、银行、抽奖、猜数字、PK对战
"""

import asyncio
import random
from datetime import datetime
from pathlib import Path
from typing import Union

from nonebot import on_command, logger
from nonebot.adapters import Event
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupMessageEvent,
    Message,
    MessageEvent,
    MessageSegment,
    PrivateMessageEvent,
)
from nonebot.matcher import Matcher
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER

from .points_data import (
    calculate_level,
    consume_game_count,
    create_pk_session,
    do_sign,
    draw_fortune,
    end_guess_game,
    get_all_user_ids,
    get_game_remaining,
    get_guess_game,
    get_level_title,
    get_pk_session_by_bot_msg,
    get_pk_session_by_invitee,
    get_pk_session_by_inviter,
    get_user,
    get_user_info,
    remove_pk_session,
    save_user,
    start_guess_game,
)
from .config import is_points_enabled, enable_points, disable_points

# 抽签图片目录
FORTUNE_IMAGE_DIR = Path(__file__).parent / "image"


# ========== 积分系统开关指令（超级用户） ==========
enable_points_cmd = on_command("开启积分", permission=SUPERUSER, priority=5, block=True, force_whitespace=True)
disable_points_cmd = on_command("关闭积分", permission=SUPERUSER, priority=5, block=True, force_whitespace=True)


@enable_points_cmd.handle()
async def handle_enable_points(matcher: Matcher, event: MessageEvent):
    """超级用户开启群聊积分系统"""
    if isinstance(event, PrivateMessageEvent):
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("请在群聊中发送此指令")
        ]))
        return
    
    group_id = str(event.group_id)
    
    if is_points_enabled(group_id):
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("积分系统已开启")
        ]))
        return
    
    enable_points(group_id)
    await matcher.finish(Message([
        MessageSegment.reply(event.message_id),
        MessageSegment.text("✅ 已开启本群积分系统")
    ]))


@disable_points_cmd.handle()
async def handle_disable_points(matcher: Matcher, event: MessageEvent):
    """超级用户关闭群聊积分系统"""
    if isinstance(event, PrivateMessageEvent):
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("请在群聊中发送此指令")
        ]))
        return
    
    group_id = str(event.group_id)
    
    if not is_points_enabled(group_id):
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("积分系统已关闭")
        ]))
        return
    
    disable_points(group_id)
    await matcher.finish(Message([
        MessageSegment.reply(event.message_id),
        MessageSegment.text("❌ 已关闭本群积分系统")
    ]))


# ========== 签到指令 ==========
sign_cmd = on_command("签到", aliases={"打卡"}, priority=5, block=True, force_whitespace=True)


@sign_cmd.handle()
async def handle_sign(matcher: Matcher, event: MessageEvent):
    """处理签到指令"""
    # 检查群聊是否开启了积分系统
    if isinstance(event, GroupMessageEvent):
        if not is_points_enabled(str(event.group_id)):
            await matcher.finish(Message([
                MessageSegment.reply(event.message_id),
                MessageSegment.text("本群积分系统已关闭")
            ]))
            return
    
    user_id = str(event.user_id)
    result = do_sign(user_id)
    
    if not result["success"]:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("今日已签到，请明天再来~")
        ]))
        return
    
    # 构建回复消息
    level = result["level"]
    title = get_level_title(level)
    
    msg = f"""签到成功✅
签到获得 {result['points_gained']} 积分
[Lv.{level}] {title}
现有积分: {result['points']}
累计签到: {result['total_sign_days']} 天
连续签到: {result['continuous_sign_days']} 天
{result['date']}"""
    
    await matcher.finish(Message([
        MessageSegment.reply(event.message_id),
        MessageSegment.text(msg)
    ]))


# ========== 抽签指令 ==========
fortune_cmd = on_command("抽签", aliases={"今日运气"}, priority=5, block=True, force_whitespace=True)


@fortune_cmd.handle()
async def handle_fortune(matcher: Matcher, event: MessageEvent):
    """处理每日抽签指令"""
    # 检查群聊是否开启了积分系统
    if isinstance(event, GroupMessageEvent):
        if not is_points_enabled(str(event.group_id)):
            await matcher.finish(Message([
                MessageSegment.reply(event.message_id),
                MessageSegment.text("本群积分系统已关闭")
            ]))
            return

    user_id = str(event.user_id)
    result = draw_fortune(user_id)

    if not result["success"]:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text(result["message"])
        ]))
        return

    fortune = result["fortune"]
    text_msg = f"『{fortune['level']}』{fortune['text']}"

    # 构建消息：文本 + 图片
    msg_chain = [MessageSegment.reply(event.message_id), MessageSegment.text(text_msg)]

    # 查找对应签的图片
    image_path = FORTUNE_IMAGE_DIR / f"{fortune['level']}.png"
    if image_path.exists():
        msg_chain.append(MessageSegment.image(f"file://{image_path}"))

    await matcher.finish(Message(msg_chain))


# ========== 积分查询指令 ==========
points_cmd = on_command("积分", aliases={"查积分", "我的积分"}, priority=5, block=True, force_whitespace=True)


@points_cmd.handle()
async def handle_points(matcher: Matcher, event: MessageEvent):
    """处理积分查询指令"""
    # 检查群聊是否开启了积分系统
    if isinstance(event, GroupMessageEvent):
        if not is_points_enabled(str(event.group_id)):
            await matcher.finish(Message([
                MessageSegment.reply(event.message_id),
                MessageSegment.text("本群积分系统已关闭")
            ]))
            return
    
    user_id = str(event.user_id)
    info = get_user_info(user_id)
    
    sign_status = "今日已签到✅" if info["signed_today"] else "今日未签到❌"
    
    msg = f"""[Lv.{info['level']}] {info['title']}
Exp: {info['current_exp']} / {info['exp_needed']}
现有积分: {info['points']}
银行积分: {info['bank_points']}
累计签到: {info['total_sign_days']} 天
连续签到: {info['continuous_sign_days']} 天
{sign_status}"""
    
    await matcher.finish(Message([
        MessageSegment.reply(event.message_id),
        MessageSegment.text(msg)
    ]))


# ========== 转账指令 ==========
transfer_cmd = on_command("转账", priority=5, block=True)


@transfer_cmd.handle()
async def handle_transfer(
    matcher: Matcher,
    event: MessageEvent,
    args: Message = CommandArg(),
):
    """处理转账指令"""
    user_id = str(event.user_id)
    
    # 检查是否在群聊
    if isinstance(event, PrivateMessageEvent):
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("转账功能仅在群聊可用")
        ]))
        return
    
    # 检查群聊是否开启了积分系统
    if not is_points_enabled(str(event.group_id)):
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("本群积分系统已关闭")
        ]))
        return
    
    # 检查是否@了某人
    if not event.reply and not any(seg.type == "at" for seg in args):
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("请使用「转账 积分 @某人」格式进行转账")
        ]))
        return
    
    # 解析积分数量
    args_text = args.extract_plain_text().strip()
    parts = args_text.split()
    
    if not parts or not parts[0].isdigit():
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("请使用「转账 积分 @某人」格式进行转账")
        ]))
        return
    
    amount = int(parts[0])
    
    if amount < 1:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("转账积分不能少于1")
        ]))
        return
    
    # 获取被@的用户
    target_id = None
    for seg in args:
        if seg.type == "at":
            target_id = seg.data.get("qq")
            break
    
    # 如果没有在参数中找到，检查回复消息
    if not target_id and event.reply:
        target_id = str(event.reply.sender.user_id)
    
    if not target_id:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("请@要转账的对象")
        ]))
        return
    
    if target_id == user_id:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("不能给自己转账")
        ]))
        return
    
    # 检查积分是否足够
    user = get_user(user_id)
    if user.points < amount:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text(f"积分不足，你只有 {user.points} 积分")
        ]))
        return
    
    # 执行转账
    user.points -= amount
    save_user(user_id)
    
    target_user = get_user(target_id)
    target_user.points += amount
    save_user(target_id)
    
    await matcher.finish(Message([
        MessageSegment.reply(event.message_id),
        MessageSegment.text(f"成功将 {amount} 积分转账给Ta")
    ]))


# ========== 银行指令 ==========
bank_deposit_cmd = on_command("存入银行", priority=5, block=True)
bank_withdraw_cmd = on_command("取出银行", priority=5, block=True)


@bank_deposit_cmd.handle()
async def handle_bank_deposit(
    matcher: Matcher,
    event: MessageEvent,
    args: Message = CommandArg(),
):
    """处理存入银行指令"""
    # 检查群聊是否开启了积分系统
    if isinstance(event, GroupMessageEvent):
        if not is_points_enabled(str(event.group_id)):
            await matcher.finish(Message([
                MessageSegment.reply(event.message_id),
                MessageSegment.text("本群积分系统已关闭")
            ]))
            return
    
    user_id = str(event.user_id)
    
    args_text = args.extract_plain_text().strip()
    if not args_text or not args_text.isdigit():
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("请使用「存入银行 积分」格式")
        ]))
        return
    
    amount = int(args_text)
    if amount < 1:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("存入积分不能少于1")
        ]))
        return
    
    user = get_user(user_id)
    if user.points < amount:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text(f"积分不足，你只有 {user.points} 积分")
        ]))
        return
    
    user.points -= amount
    user.bank_points += amount
    save_user(user_id)
    
    await matcher.finish(Message([
        MessageSegment.reply(event.message_id),
        MessageSegment.text(f"成功将 {amount} 积分存入银行\n银行余额: {user.bank_points}")
    ]))


@bank_withdraw_cmd.handle()
async def handle_bank_withdraw(
    matcher: Matcher,
    event: MessageEvent,
    args: Message = CommandArg(),
):
    """处理取出银行指令"""
    # 检查群聊是否开启了积分系统
    if isinstance(event, GroupMessageEvent):
        if not is_points_enabled(str(event.group_id)):
            await matcher.finish(Message([
                MessageSegment.reply(event.message_id),
                MessageSegment.text("本群积分系统已关闭")
            ]))
            return
    
    user_id = str(event.user_id)
    
    args_text = args.extract_plain_text().strip()
    if not args_text or not args_text.isdigit():
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("请使用「取出银行 积分」格式")
        ]))
        return
    
    amount = int(args_text)
    if amount < 1:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("取出积分不能少于1")
        ]))
        return
    
    user = get_user(user_id)
    if user.bank_points < amount:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text(f"银行积分不足，银行余额: {user.bank_points}")
        ]))
        return
    
    user.bank_points -= amount
    user.points += amount
    save_user(user_id)
    
    await matcher.finish(Message([
        MessageSegment.reply(event.message_id),
        MessageSegment.text(f"成功将 {amount} 积分取出银行\n银行余额: {user.bank_points}")
    ]))


# ========== 抢银行指令 ==========
rob_bank_cmd = on_command("抢银行", priority=5, block=True, force_whitespace=True)


@rob_bank_cmd.handle()
async def handle_rob_bank(matcher: Matcher, event: MessageEvent):
    """处理抢银行指令"""
    # 检查群聊是否开启了积分系统
    if isinstance(event, GroupMessageEvent):
        if not is_points_enabled(str(event.group_id)):
            await matcher.finish(Message([
                MessageSegment.reply(event.message_id),
                MessageSegment.text("本群积分系统已关闭")
            ]))
            return
    
    user_id = str(event.user_id)
    user = get_user(user_id)
    
    # 检查是否有50积分
    if user.points < 50:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("积分不足50，无法抢银行")
        ]))
        return
    
    # 随机结果
    rand = random.random() * 100
    
    if rand < 3:  # 3% 大成功
        amount = random.randint(100, 300)
        user.points += amount
        save_user(user_id)
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text(f"抢银行大获成功，获得了 {amount} 积分")
        ]))
    elif rand < 23:  # 20% 成功
        amount = random.randint(1, 100)
        user.points += amount
        save_user(user_id)
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text(f"抢银行成功，获得 {amount} 积分")
        ]))
    elif rand < 70:  # 47% 不敢去
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("思来想去，走到半路还是怕了回家了吧")
        ]))
    else:  # 30% 失败
        amount = random.randint(1, 50)
        user.points -= amount
        save_user(user_id)
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text(f"抢银行失败，你被抓了，赔偿了 {amount} 积分")
        ]))


# ========== 抽奖指令 ==========
lottery_cmd = on_command("抽奖", priority=5, block=True)


@lottery_cmd.handle()
async def handle_lottery(
    matcher: Matcher,
    event: MessageEvent,
    args: Message = CommandArg(),
):
    """处理抽奖指令"""
    # 检查群聊是否开启了积分系统
    if isinstance(event, GroupMessageEvent):
        if not is_points_enabled(str(event.group_id)):
            await matcher.finish(Message([
                MessageSegment.reply(event.message_id),
                MessageSegment.text("本群积分系统已关闭")
            ]))
            return
    
    user_id = str(event.user_id)
    
    args_text = args.extract_plain_text().strip()
    if not args_text or not args_text.isdigit():
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("请使用「抽奖 积分」格式，积分范围 10-500")
        ]))
        return
    
    amount = int(args_text)
    if amount < 10 or amount > 500:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("抽奖积分范围是 10-500")
        ]))
        return
    
    user = get_user(user_id)
    if user.points < amount:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text(f"积分不足，你只有 {user.points} 积分")
        ]))
        return
    
    # 检查每日次数
    remaining = get_game_remaining(user_id, "lottery")
    if remaining <= 0:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("今日抽奖次数已用完（每天10次），明天再来~")
        ]))
        return
    
    # 扣除积分
    user.points -= amount
    
    # 抽奖概率：0-1倍50%，1-2倍40%，2-3倍10%
    rand = random.random() * 100
    if rand < 50:  # 50% 概率 0-1倍
        multiplier = random.random()  # 0-1
    elif rand < 90:  # 40% 概率 1-2倍
        multiplier = 1 + random.random()  # 1-2
    else:  # 10% 概率 2-3倍
        multiplier = 2 + random.random()  # 2-3
    
    gained = int(amount * multiplier)
    
    user.points += gained
    save_user(user_id)
    
    # 消耗次数并获取剩余
    left = consume_game_count(user_id, "lottery")
    
    await matcher.finish(Message([
        MessageSegment.reply(event.message_id),
        MessageSegment.text(f"得分: {gained}\n现有积分: {user.points}\n今日抽奖剩余 {left} 次")
    ]))


# ========== 猜数字指令 ==========
guess_start_cmd = on_command("猜数字", priority=5, block=True)
guess_play_cmd = on_command("我猜", priority=5, block=True)


@guess_start_cmd.handle()
async def handle_guess_start(
    matcher: Matcher,
    event: MessageEvent,
    args: Message = CommandArg(),
):
    """处理猜数字开始指令"""
    # 检查群聊是否开启了积分系统
    if isinstance(event, GroupMessageEvent):
        if not is_points_enabled(str(event.group_id)):
            await matcher.finish(Message([
                MessageSegment.reply(event.message_id),
                MessageSegment.text("本群积分系统已关闭")
            ]))
            return
    
    user_id = str(event.user_id)
    
    # 检查是否已有进行中的游戏
    existing_game = get_guess_game(user_id)
    if existing_game:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text(f"你已有进行中的猜数字游戏，剩余 {existing_game.chances} 次机会")
        ]))
        return
    
    args_text = args.extract_plain_text().strip()
    if not args_text or not args_text.isdigit():
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("请使用「猜数字 积分」格式开始游戏，积分范围 10-500")
        ]))
        return
    
    amount = int(args_text)
    if amount < 10 or amount > 500:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("下注积分范围是 10-500")
        ]))
        return
    
    user = get_user(user_id)
    if user.points < amount:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text(f"积分不足，你只有 {user.points} 积分")
        ]))
        return
    
    # 检查每日次数
    remaining = get_game_remaining(user_id, "guess")
    if remaining <= 0:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("今日猜数字次数已用完（每天10次），明天再来~")
        ]))
        return
    
    # 扣除积分并开始游戏
    user.points -= amount
    save_user(user_id)
    
    game = start_guess_game(user_id, amount)
    
    # 消耗次数并获取剩余
    left = consume_game_count(user_id, "guess")
    
    await matcher.finish(Message([
        MessageSegment.reply(event.message_id),
        MessageSegment.text(f"🎈猜数字开始~\n积分 -{amount}\n你有 {game.chances} 次机会\n今日猜数字剩余 {left} 次\n请发送 我猜 xx")
    ]))


@guess_play_cmd.handle()
async def handle_guess_play(
    matcher: Matcher,
    event: MessageEvent,
    args: Message = CommandArg(),
):
    """处理猜数字游戏指令"""
    # 检查群聊是否开启了积分系统
    if isinstance(event, GroupMessageEvent):
        if not is_points_enabled(str(event.group_id)):
            await matcher.finish(Message([
                MessageSegment.reply(event.message_id),
                MessageSegment.text("本群积分系统已关闭")
            ]))
            return
    
    user_id = str(event.user_id)
    
    # 获取进行中的游戏
    game = get_guess_game(user_id)
    if not game:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("你还没有开始猜数字游戏，请先发送「猜数字 积分」")
        ]))
        return
    
    args_text = args.extract_plain_text().strip()
    if not args_text or not args_text.isdigit():
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("请发送「我猜 数字」，数字范围 1-100")
        ]))
        return
    
    guess = int(args_text)
    if guess < 1 or guess > 100:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("数字范围是 1-100")
        ]))
        return
    
    game.chances -= 1
    
    if guess == game.target:
        # 猜对了
        user = get_user(user_id)
        gained = game.bet * 2
        user.points += gained
        save_user(user_id)
        end_guess_game(user_id)
        
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text(f"🎯猜对啦，游戏结束\n得分 {gained}\n现有积分 {user.points}")
        ]))
        return
    
    if game.chances <= 0:
        # 机会用完
        end_guess_game(user_id)
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text(f"🎃猜错啦，游戏结束\n正确答案是 {game.target}")
        ]))
        return
    
    # 继续猜
    hint = "猜小了" if guess < game.target else "猜大了"
    await matcher.finish(Message([
        MessageSegment.reply(event.message_id),
        MessageSegment.text(f"{hint}，比 {guess} {'大' if guess < game.target else '小'}\n剩余 {game.chances} 次机会")
    ]))


# ========== 功能列表指令 ==========
help_cmd = on_command("功能", priority=5, block=True, force_whitespace=True)

# 功能详细描述
FEATURE_HELP = {
    "签到": """【签到】
指令：签到 或 打卡
描述：每日签到，连续签到都有积分加成~""",
    "抽签": """【抽签】
指令：抽签 或 今日运气
描述：每日抽签一次，获取今日气运信息""",
    "积分": """【积分】
指令：积分 或 查积分 或 我的积分
描述：查看自己所拥有的积分""",
    "转账": """【转账】
指令：转账 积分 @某人
描述：将n积分转账给某人""",
    "打劫": """【打劫】
指令：打劫 @某人
描述：打劫别人的积分""",
    "银行": """【银行】
指令：存入银行 积分/取出银行 积分
描述：1.存入银行:将积分存入银行，每天计算利息;
	  2.取出银行:将积分从银行取出""",
    "抢银行": """【抢银行】
指令：抢银行
描述：抢银行，有几率获得积分，也可能被扣分""",
    "抽奖": """【抽奖】
指令：抽奖 积分
描述：消耗积分抽奖，随机获取0-3倍积分""",
    "猜数字": """【猜数字】
指令：猜数字 积分 / 我猜 数字
描述：消耗积分开始猜数字游戏，猜中则积分翻倍""",
    "PK": """【PK对战】
指令：PK 积分 @某人
描述：邀请某人摇骰子PK，双方各下注相同积分
      对方点击✊🏻 /🖕🏻接受或拒绝挑战
      或发送「同意PK」/「拒绝PK」
      双方各摇2颗骰子（2-12），点数大的获得全部积分
      平局则退回双方积分""",
}


@help_cmd.handle()
async def handle_help(
    matcher: Matcher,
    event: MessageEvent,
    args: Message = CommandArg(),
):
    """处理功能列表指令"""
    args_text = args.extract_plain_text().strip()
    
    # 如果有参数，显示对应功能的详细描述
    if args_text:
        if args_text in FEATURE_HELP:
            await matcher.finish(Message([
                MessageSegment.reply(event.message_id),
                MessageSegment.text(FEATURE_HELP[args_text])
            ]))
        else:
            # 模糊匹配
            matched = [k for k in FEATURE_HELP.keys() if args_text in k]
            if matched:
                await matcher.finish(Message([
                    MessageSegment.reply(event.message_id),
                    MessageSegment.text(FEATURE_HELP[matched[0]])
                ]))
            else:
                await matcher.finish(Message([
                    MessageSegment.reply(event.message_id),
                    MessageSegment.text(f"未找到「{args_text}」相关功能，发送「功能」查看所有功能列表")
                ]))
        return
    
    # 无参数时显示功能列表
    msg = """【积分系统功能列表】
签到   积分   转账   打劫 
银行   抢银行  抽奖  猜数字  
PK    抽签   

发送「功能 功能名称」查看使用方法"""
    await matcher.finish(Message([
        MessageSegment.reply(event.message_id),
        MessageSegment.text(msg)
    ]))





# ========== 打劫指令 ==========
rob_cmd = on_command("打劫", priority=5, block=True)


@rob_cmd.handle()
async def handle_rob(
    matcher: Matcher,
    event: MessageEvent,
    args: Message = CommandArg(),
):
    """处理打劫指令"""
    user_id = str(event.user_id)
    
    # 检查是否在群聊
    if isinstance(event, PrivateMessageEvent):
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("打劫功能仅在群聊可用")
        ]))
        return
    
    # 检查群聊是否开启了积分系统
    if not is_points_enabled(str(event.group_id)):
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("本群积分系统已关闭")
        ]))
        return
    
    # 检查是否@了某人
    if not event.reply and not any(seg.type == "at" for seg in args):
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("请使用「打劫 @某人」格式")
        ]))
        return
    
    # 获取被@的用户
    target_id = None
    for seg in args:
        if seg.type == "at":
            target_id = seg.data.get("qq")
            break
    
    if not target_id and event.reply:
        target_id = str(event.reply.sender.user_id)
    
    if not target_id:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("请@要打劫的对象")
        ]))
        return
    
    if target_id == user_id:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("不能打劫自己")
        ]))
        return
    
    user = get_user(user_id)
    
    # 检查是否有50积分
    if user.points < 50:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("积分不足50，无法打劫")
        ]))
        return
    
    target_user = get_user(target_id)
    
    # 检查对方是否有积分账户或积分不足
    total_points = target_user.points + target_user.bank_points
    if total_points < 50:
        if total_points < 1:
            await matcher.finish(Message([
                MessageSegment.reply(event.message_id),
                MessageSegment.text("对方还没有积分账户，无法打劫")
            ]))
            return
        else:
            await matcher.finish(Message([
                MessageSegment.reply(event.message_id),
                MessageSegment.text("对方已经穷的吃不起饭了，你还打劫别人！")
            ]))
            return
    
    if target_user.points < 1:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("对方身上没有积分，无法打劫")
        ]))
        return
    
    # 随机结果
    rand = random.random() * 100
    
    if rand < 30:  # 30% 成功
        max_rob = min(target_user.points, 50)
        amount = random.randint(1, max_rob)
        target_user.points -= amount
        user.points += amount
        save_user(user_id)
        save_user(target_id)
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text(f"打劫成功，获得 {amount} 积分")
        ]))
    elif rand < 70:  # 40% 逃跑
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("打劫失败，对方跑掉了并对你竖了个中指")
        ]))
    else:  # 30% 失败扣分
        amount = random.randint(1, 50)
        user.points -= amount
        target_user.points += amount
        save_user(user_id)
        save_user(target_id)
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text(f"打劫失败，被反杀，损失 {amount} 积分")
        ]))
# ========== 发积分指令（超级用户隐藏指令） ==========
give_points_cmd = on_command("发积分", permission=SUPERUSER, priority=5, block=True)


@give_points_cmd.handle()
async def handle_give_points(
    matcher: Matcher,
    event: MessageEvent,
    args: Message = CommandArg(),
):
    """超级用户发放积分，支持单人或全体"""
    args_text = args.extract_plain_text().strip()

    # 解析参数：发积分 数量 [全体] [@某人]
    parts = args_text.split()
    if not parts or not parts[0].isdigit():
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("用法：发积分 数量 @某人\n      发积分 数量 全体")
        ]))
        return

    amount = int(parts[0])
    if amount < 1 or amount > 99999:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("积分范围是 1-99999")
        ]))
        return

    # 检查是否为全体发放
    is_all = len(parts) >= 2 and "全体" in parts[1]

    if is_all:
        # 全体发放
        all_ids = get_all_user_ids()
        if not all_ids:
            await matcher.finish(Message([
                MessageSegment.reply(event.message_id),
                MessageSegment.text("暂无已注册用户")
            ]))
            return

        for uid in all_ids:
            user = get_user(uid)
            user.points += amount
            save_user(uid)

        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text(f"已向全体 {len(all_ids)} 位用户发放 {amount} 积分")
        ]))
        return

    # 单人发放：获取目标用户
    target_id = None
    for seg in args:
        if seg.type == "at":
            target_id = seg.data.get("qq")
            break

    # 如果没有@任何人，则发给自己
    if not target_id:
        target_id = str(event.user_id)

    # 发放积分
    target_user = get_user(target_id)
    target_user.points += amount
    save_user(target_id)

    await matcher.finish(Message([
        MessageSegment.reply(event.message_id),
        MessageSegment.text(f"已发放 {amount} 积分")
    ]))


# ========== PK 对战指令 ==========
pk_cmd = on_command("PK", aliases={"pk"}, priority=5, block=True)
pk_accept_cmd = on_command("同意PK", aliases={"同意pk", "接受PK", "接受pk"}, priority=5, block=True, force_whitespace=True)
pk_reject_cmd = on_command("拒绝PK", aliases={"拒绝pk", "拒绝对战"}, priority=5, block=True, force_whitespace=True)

# PK 表情回应 emoji_id（QQ NT 表情编号）
PK_ACCEPT_EMOJI_ID = "120"   # 拳头（表示同意）
PK_REJECT_EMOJI_ID = "123"    # NO（表示拒绝）

# ========== 调试：测试 emoji ID（仅 SUPERUSER）==========
test_emoji_cmd = on_command("测试emoji", aliases={"test_emoji", "测试表情"}, priority=5, block=True, permission=SUPERUSER)


@test_emoji_cmd.handle()
async def handle_test_emoji(
    matcher: Matcher,
    bot: Bot,
    event: GroupMessageEvent,
    arg: Message = CommandArg(),
):
    """调试指令：在当前群发送一条消息并贴上指定 ID 的 emoji，用于获取真实 emoji_id"""
    raw_text = arg.extract_plain_text().strip()
    if not raw_text:
        await matcher.finish("用法：测试emoji <emoji_id>\n示例：测试emoji 1\n示例：测试emoji 112")

    # 发送一条测试消息
    test_msg = await bot.send_group_msg(
        group_id=event.group_id,
        message=Message([
            MessageSegment.text(f"🔧 Emoji 测试 - ID: {raw_text}"),
        ]),
    )

    msg_id = int(test_msg["message_id"])

    # 尝试给这条消息贴上指定的 emoji
    try:
        ret = await bot.call_api(
            "set_msg_emoji_like",
            message_id=msg_id,
            emoji_id=raw_text,
            set=True,
        )
        result_info = f"✅ 成功！已为消息设置 emoji_id={raw_text}\n返回值: {ret}"
    except Exception as e:
        result_info = f"❌ 失败！emoji_id={raw_text} 无效或 API 不支持\n错误: {e}"

    await matcher.finish(result_info)


@test_emoji_cmd.handle()


@pk_cmd.handle()
async def handle_pk(
    matcher: Matcher,
    bot: Bot,
    event: MessageEvent,
    args: Message = CommandArg(),
):
    """处理 PK 发起指令：PK 积分 @某人"""
    # 仅限群聊
    if isinstance(event, PrivateMessageEvent):
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("PK 功能仅在群聊可用")
        ]))
        return

    group_id = str(event.group_id)

    if not is_points_enabled(group_id):
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("本群积分系统已关闭")
        ]))
        return

    inviter_id = str(event.user_id)

    # 检查发起人是否已有待确认 PK
    if get_pk_session_by_inviter(inviter_id):
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("你已有一场 PK 邀请等待对方确认，请稍候")
        ]))
        return

    # 解析积分参数
    args_text = args.extract_plain_text().strip()
    parts = args_text.split()

    if not parts or not parts[0].isdigit():
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("请使用「PK 积分 @某人」格式发起挑战")
        ]))
        return

    bet = int(parts[0])
    if bet < 1:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("下注积分不能少于 1")
        ]))
        return

    # 获取目标用户
    invitee_id = None
    for seg in args:
        if seg.type == "at":
            invitee_id = seg.data.get("qq")
            break

    if not invitee_id:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("请@要挑战的对象")
        ]))
        return

    if invitee_id == inviter_id:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("不能和自己 PK")
        ]))
        return

    # 检查发起人积分
    inviter = get_user(inviter_id)
    if inviter.points < bet:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text(f"积分不足，你只有 {inviter.points} 积分")
        ]))
        return

    # 检查被邀请人积分
    invitee = get_user(invitee_id)
    if invitee.points < bet:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text(f"对方积分不足（需要 {bet} 积分），无法发起挑战")
        ]))
        return

    # 检查对方是否已有待确认的 PK
    if get_pk_session_by_invitee(invitee_id):
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("对方已有一场待确认的 PK 邀请，请稍后再试")
        ]))
        return

    # 先冻结发起人积分（预扣）
    inviter.points -= bet
    save_user(inviter_id)

    # 创建会话
    session = create_pk_session(inviter_id, invitee_id, bet, group_id)
    session.inviter_message_id = event.message_id

    # 发送邀请消息
    pk_msg = await bot.send(event, Message([
        MessageSegment.at(invitee_id),
        MessageSegment.text(
            f" 你被 {event.sender.nickname or inviter_id} 挑战了！\n"
            f"下注积分：{bet}\n"
            f"（点击下方 emoji 或发送「同意PK」/「拒绝PK」，60秒内有效）"
        )
    ]))

    # 保存机器人发出的消息 message_id（用于 emoji 回应匹配）
    session.bot_message_id = int(pk_msg["message_id"])

    # 预置 👍(同意) 和 👎(拒绝) emoji 回应选项
    # 注意：emoji_id 取值因 QQ 客户端版本而异，以下为常见值
    #   如果贴出的表情不对，请用「测试emoji」指令获取真实 ID 后修改此处常量
    try:
        await bot.call_api("set_msg_emoji_like", message_id=int(pk_msg["message_id"]), emoji_id=PK_ACCEPT_EMOJI_ID, set=True)
        await bot.call_api("set_msg_emoji_like", message_id=int(pk_msg["message_id"]), emoji_id=PK_REJECT_EMOJI_ID, set=True)
    except Exception:
        pass  # 非 NapCat 端或不支持此 API 时静默忽略

    # 60 秒超时自动取消
    async def _timeout():
        await asyncio.sleep(60)
        removed = remove_pk_session(invitee_id)
        if removed:
            # 退回发起人积分
            inv = get_user(inviter_id)
            inv.points += bet
            save_user(inviter_id)
            try:
                await bot.send_group_msg(
                    group_id=int(group_id),
                    message=Message([
                        MessageSegment.at(inviter_id),
                        MessageSegment.text(f" PK 邀请超时未被接受，已退回 {bet} 积分")
                    ])
                )
            except Exception:
                pass

    task = asyncio.create_task(_timeout())
    session.cancel_task = task


@pk_accept_cmd.handle()
async def handle_pk_accept(
    matcher: Matcher,
    bot: Bot,
    event: MessageEvent,
):
    """处理同意 PK 指令"""
    # 仅限群聊
    if isinstance(event, PrivateMessageEvent):
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("PK 功能仅在群聊可用")
        ]))
        return

    group_id = str(event.group_id)

    if not is_points_enabled(group_id):
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("本群积分系统已关闭")
        ]))
        return

    invitee_id = str(event.user_id)
    session = get_pk_session_by_invitee(invitee_id)

    if not session:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("你当前没有待接受的 PK 邀请")
        ]))
        return

    # 校验群组一致
    if session.group_id != group_id:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("请在发起 PK 的群聊中接受挑战")
        ]))
        return

    # 移除会话（取消超时任务）
    remove_pk_session(invitee_id)

    inviter_id = session.inviter_id
    bet = session.bet

    # 扣除被邀请人积分
    invitee = get_user(invitee_id)
    if invitee.points < bet:
        # 退回发起人积分
        inv = get_user(inviter_id)
        inv.points += bet
        save_user(inviter_id)
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text(f"你的积分不足（需要 {bet} 积分），PK 取消，已退回对方积分")
        ]))
        return

    invitee.points -= bet
    save_user(invitee_id)

    # 摇骰子（各摇两颗，2-12）
    inviter_roll = random.randint(1, 6) + random.randint(1, 6)
    invitee_roll = random.randint(1, 6) + random.randint(1, 6)

    inviter = get_user(inviter_id)
    invitee_user = get_user(invitee_id)

    # 获取昵称（尽量用群昵称）
    try:
        inviter_info = await bot.get_group_member_info(group_id=int(group_id), user_id=int(inviter_id))
        inviter_name = inviter_info.get("card") or inviter_info.get("nickname") or inviter_id
    except Exception:
        inviter_name = inviter_id

    try:
        invitee_info = await bot.get_group_member_info(group_id=int(group_id), user_id=int(invitee_id))
        invitee_name = invitee_info.get("card") or invitee_info.get("nickname") or invitee_id
    except Exception:
        invitee_name = invitee_id

    total_pot = bet * 2

    if inviter_roll > invitee_roll:
        # 发起人胜
        inviter.points += total_pot
        save_user(inviter_id)
        result_line = f"{inviter_name} 胜！获得 {total_pot} 积分"
    elif invitee_roll > inviter_roll:
        # 被邀请人胜
        invitee_user.points += total_pot
        save_user(invitee_id)
        result_line = f"{invitee_name} 胜！获得 {total_pot} 积分"
    else:
        # 平局退回
        inviter.points += bet
        invitee_user.points += bet
        save_user(inviter_id)
        save_user(invitee_id)
        result_line = "平局！积分已退回双方"

    await matcher.finish(Message([
        MessageSegment.reply(session.inviter_message_id),
        MessageSegment.at(inviter_id),
        MessageSegment.text(
            f" PK结果（下注 {bet}）\n"
            f"🎲 {inviter_name} 摇出了 {inviter_roll} 点\n"
            f"🎲 {invitee_name} 摇出了 {invitee_roll} 点\n"
            f"{'━' * 10}\n"
            f"{result_line}"
        )
    ]))


@pk_reject_cmd.handle()
async def handle_pk_reject(
    matcher: Matcher,
    bot: Bot,
    event: MessageEvent,
):
    """处理拒绝 PK 指令"""
    if isinstance(event, PrivateMessageEvent):
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("PK 功能仅在群聊可用")
        ]))
        return

    group_id = str(event.group_id)

    if not is_points_enabled(group_id):
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("本群积分系统已关闭")
        ]))
        return

    invitee_id = str(event.user_id)
    session = get_pk_session_by_invitee(invitee_id)

    if not session:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("你当前没有待接受的 PK 邀请")
        ]))
        return

    # 校验群组一致
    if session.group_id != group_id:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("请在发起 PK 的群聊中操作")
        ]))
        return

    inviter_id = session.inviter_id
    bet = session.bet

    # 移除会话（取消超时任务）
    remove_pk_session(invitee_id)

    # 退回发起人积分
    inviter = get_user(inviter_id)
    inviter.points += bet
    save_user(inviter_id)

    # 获取拒绝者昵称
    try:
        invitee_info = await bot.get_group_member_info(group_id=int(group_id), user_id=int(invitee_id))
        invitee_name = invitee_info.get("card") or invitee_info.get("nickname") or invitee_id
    except Exception:
        invitee_name = invitee_id

    await matcher.finish(Message([
        MessageSegment.reply(session.inviter_message_id),
        MessageSegment.at(inviter_id),
        MessageSegment.text(f" {invitee_name} 拒绝了你的 PK 邀请，已退回 {bet} 积分")
    ]))


# ========== PK emoji 回应事件监听（NapCat 扩展） ==========
# 事件类型：notice.group_msg_emoji_like
# 注意：
#   1. 此事件不在 OneBot V11 标准中，是 NapCat 的扩展事件
#   2. NapCat 只上报「自己（机器人）发出的消息被别人点 emoji」的事件
#   3. 别人之间点的 emoji 不会报上来，所以这里只能收到被邀请人对机器人消息的操作
#   4. 如果 NapCat 版本不支持此事件或配置未开启，文字指令「同意PK」/「拒绝PK」仍可正常使用

from nonebot import on_notice


pk_emoji_notice = on_notice(priority=5, block=True)


@pk_emoji_notice.handle()
async def handle_pk_emoji_like(
    matcher: Matcher,
    bot: Bot,
    event: Event,
):
    """处理群消息 emoji 回应事件：用于 PK 同意/拒绝

    NapCat 上报的事件数据（通过 Event 原始属性访问）：
    {
        "post_type": "notice",
        "notice_type": "group_msg_emoji_like",
        "group_id": 群号,
        "user_id": 操作者QQ,
        "message_id": 被回应的消息ID,
        "likes": [{"emoji_id": "120", "count": N}],
        "is_add": True/False
    }
    """
    from nonebot.adapters.onebot.v11 import NoticeEvent

    # 仅处理 notice 类型的通知事件
    if not isinstance(event, NoticeEvent):
        return

    # 仅处理 group_msg_emoji_like 子类型
    if getattr(event, 'notice_type', '') != 'group_msg_emoji_like':
        return

    # 忽略机器人自己的操作
    user_id_str = str(event.user_id)
    if user_id_str == str(bot.self_id):
        return

    # 查找是否有匹配的 PK 会话
    message_id = int(getattr(event, 'message_id', 0) or 0)
    if message_id <= 0:
        return

    session = get_pk_session_by_bot_msg(message_id)
    if not session:
        logger.debug(f"[PK-Emoji] message_id={message_id} 没有匹配的 PK 会话")
        return

    invitee_id = user_id_str
    inviter_id = session.inviter_id
    bet = session.bet
    group_id = session.group_id

    # 校验：只有被邀请人可以操作
    if invitee_id != session.invitee_id:
        logger.debug(f"[PK-Emoji] 操作者 {invitee_id} 不是被邀请人 {session.invitee_id}, 忽略")
        return

    # 校验群组一致
    event_group_id = str(getattr(event, 'group_id', None) or "")
    if event_group_id != group_id:
        logger.debug(f"[PK-Emoji] 群组不匹配 {event_group_id} vs {group_id}")
        return

    # ---- 安全过滤：只处理拳头(120) / NO(123) ----
    likes = getattr(event, 'likes', None) or []
    if not likes:
        return

    raw_emoji_id = str(likes[0].get("emoji_id", "") if isinstance(likes[0], dict) else "")

    is_accept = (raw_emoji_id == PK_ACCEPT_EMOJI_ID)
    is_reject = (raw_emoji_id == PK_REJECT_EMOJI_ID)

    # 非 拳头/NO 的 emoji 一律忽略（别人点的 😂👍 等不触发任何操作）
    if not is_accept and not is_reject:
        logger.debug(f"[PK-Emoji] 未识别的 emoji_id={raw_emoji_id}, 忽略")
        return

    # 移除会话（取消超时任务），防止重复处理
    remove_pk_session(invitee_id)

    if is_accept:
        # ---- 执行同意逻辑（复用 pk_accept 的核心流程）----
        invitee = get_user(invitee_id)
        if invitee.points < bet:
            inv = get_user(inviter_id)
            inv.points += bet
            save_user(inviter_id)
            try:
                await bot.send_group_msg(
                    group_id=int(group_id),
                    message=Message([
                        MessageSegment.at(invitee_id),
                        MessageSegment.text(
                            f" 你的积分不足（需要 {bet} 积分），PK 取消，已退回对方积分"
                        ),
                    ]),
                )
            except Exception:
                pass
            await matcher.finish()

        invitee.points -= bet
        save_user(invitee_id)

        # 摇骰子
        inviter_roll = random.randint(1, 6) + random.randint(1, 6)
        invitee_roll = random.randint(1, 6) + random.randint(1, 6)

        inviter = get_user(inviter_id)
        invitee_user = get_user(invitee_id)

        try:
            inviter_info = await bot.get_group_member_info(
                group_id=int(group_id), user_id=int(inviter_id)
            )
            inviter_name = (
                inviter_info.get("card")
                or inviter_info.get("nickname")
                or inviter_id
            )
        except Exception:
            inviter_name = inviter_id

        try:
            invitee_info = await bot.get_group_member_info(
                group_id=int(group_id), user_id=int(invitee_id)
            )
            invitee_name = (
                invitee_info.get("card")
                or invitee_info.get("nickname")
                or invitee_id
            )
        except Exception:
            invitee_name = invitee_id

        total_pot = bet * 2

        if inviter_roll > invitee_roll:
            inviter.points += total_pot
            save_user(inviter_id)
            result_line = f"{inviter_name} 胜！获得 {total_pot} 积分"
        elif invitee_roll > inviter_roll:
            invitee_user.points += total_pot
            save_user(invitee_id)
            result_line = f"{invitee_name} 胜！获得 {total_pot} 积分"
        else:
            inviter.points += bet
            invitee_user.points += bet
            save_user(inviter_id)
            save_user(invitee_id)
            result_line = "平局！积分已退回双方"

        msg = (
            f" PK结果（下注 {bet}）\n"
            f"🎲 {inviter_name} 摇出了 {inviter_roll} 点\n"
            f"🎲 {invitee_name} 摇出了 {invitee_roll} 点\n"
            f"{'━' * 10}\n"
            f"{result_line}"
        )

        # 引用发起人的 PK 指令消息回复结果
        reply_segments = [MessageSegment.reply(session.inviter_message_id), MessageSegment.at(inviter_id), MessageSegment.text(msg)] if session.inviter_message_id else [MessageSegment.at(inviter_id), MessageSegment.text(msg)]

        try:
            await bot.send_group_msg(
                group_id=int(group_id),
                message=Message(reply_segments),
            )
        except Exception:
            pass
        await matcher.finish()

    elif is_reject:
        # ---- 执行拒绝逻辑 ----
        inviter = get_user(inviter_id)
        inviter.points += bet
        save_user(inviter_id)

        try:
            invitee_info = await bot.get_group_member_info(
                group_id=int(group_id), user_id=int(invitee_id)
            )
            invitee_name = (
                invitee_info.get("card")
                or invitee_info.get("nickname")
                or invitee_id
            )
        except Exception:
            invitee_name = invitee_id

        reject_msg = (
            f"{invitee_name} 拒绝了 PK 邀请，已退回 {bet} 积分"
        )
        reject_segments = [
            MessageSegment.reply(session.inviter_message_id),
            MessageSegment.at(inviter_id),
            MessageSegment.text(f" {reject_msg}"),
        ] if session.inviter_message_id else [
            MessageSegment.at(inviter_id),
            MessageSegment.text(f" {reject_msg}"),
        ]

        try:
            await bot.send_group_msg(
                group_id=int(group_id),
                message=Message(reject_segments),
            )
        except Exception:
            pass
        await matcher.finish()
