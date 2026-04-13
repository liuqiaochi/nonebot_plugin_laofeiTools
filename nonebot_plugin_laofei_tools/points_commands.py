"""
积分系统指令处理

功能：签到、积分查询、转账、银行、抽奖、猜数字
"""

import random
from datetime import datetime
from typing import Union

from nonebot import on_command
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupMessageEvent,
    Message,
    MessageEvent,
    PrivateMessageEvent,
)
from nonebot.matcher import Matcher
from nonebot.params import CommandArg

from .points_data import (
    calculate_level,
    do_sign,
    end_guess_game,
    get_guess_game,
    get_level_title,
    get_user,
    get_user_info,
    save_user,
    start_guess_game,
    calculate_bank_interest,
)


# ========== 签到指令 ==========
sign_cmd = on_command("签到", aliases={"打卡"}, priority=5, block=True)


@sign_cmd.handle()
async def handle_sign(matcher: Matcher, event: MessageEvent):
    """处理签到指令"""
    user_id = str(event.user_id)
    result = do_sign(user_id)
    
    if not result["success"]:
        await matcher.finish("今日已签到，请明天再来~")
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
    
    await matcher.finish(msg)


# ========== 积分查询指令 ==========
points_cmd = on_command("积分", aliases={"查积分", "我的积分"}, priority=5, block=True)


@points_cmd.handle()
async def handle_points(matcher: Matcher, event: MessageEvent):
    """处理积分查询指令"""
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
    
    await matcher.finish(msg)


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
        await matcher.finish("转账功能仅在群聊可用")
        return
    
    # 检查是否@了某人
    if not event.reply and not any(seg.type == "at" for seg in args):
        await matcher.finish("请使用「转账 积分 @某人」格式进行转账")
        return
    
    # 解析积分数量
    args_text = args.extract_plain_text().strip()
    parts = args_text.split()
    
    if not parts or not parts[0].isdigit():
        await matcher.finish("请使用「转账 积分 @某人」格式进行转账")
        return
    
    amount = int(parts[0])
    
    if amount < 1:
        await matcher.finish("转账积分不能少于1")
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
        await matcher.finish("请@要转账的对象")
        return
    
    if target_id == user_id:
        await matcher.finish("不能给自己转账")
        return
    
    # 检查积分是否足够
    user = get_user(user_id)
    if user.points < amount:
        await matcher.finish(f"积分不足，你只有 {user.points} 积分")
        return
    
    # 执行转账
    user.points -= amount
    save_user(user_id)
    
    target_user = get_user(target_id)
    target_user.points += amount
    save_user(target_id)
    
    await matcher.finish(f"成功将 {amount} 积分转账给Ta")


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
    user_id = str(event.user_id)
    
    # 计算银行利息
    calculate_bank_interest()
    
    args_text = args.extract_plain_text().strip()
    if not args_text or not args_text.isdigit():
        await matcher.finish("请使用「存入银行 积分」格式")
        return
    
    amount = int(args_text)
    if amount < 1:
        await matcher.finish("存入积分不能少于1")
        return
    
    user = get_user(user_id)
    if user.points < amount:
        await matcher.finish(f"积分不足，你只有 {user.points} 积分")
        return
    
    user.points -= amount
    user.bank_points += amount
    save_user(user_id)
    
    await matcher.finish(f"""成功将 {amount} 积分存入银行
银行余额: {user.bank_points}""")


@bank_withdraw_cmd.handle()
async def handle_bank_withdraw(
    matcher: Matcher,
    event: MessageEvent,
    args: Message = CommandArg(),
):
    """处理取出银行指令"""
    user_id = str(event.user_id)
    
    # 计算银行利息
    calculate_bank_interest()
    
    args_text = args.extract_plain_text().strip()
    if not args_text or not args_text.isdigit():
        await matcher.finish("请使用「取出银行 积分」格式")
        return
    
    amount = int(args_text)
    if amount < 1:
        await matcher.finish("取出积分不能少于1")
        return
    
    user = get_user(user_id)
    if user.bank_points < amount:
        await matcher.finish(f"银行积分不足，银行余额: {user.bank_points}")
        return
    
    user.bank_points -= amount
    user.points += amount
    save_user(user_id)
    
    await matcher.finish(f"""成功将 {amount} 积分取出银行
银行余额: {user.bank_points}""")


# ========== 抢银行指令 ==========
rob_bank_cmd = on_command("抢银行", priority=5, block=True)


@rob_bank_cmd.handle()
async def handle_rob_bank(matcher: Matcher, event: MessageEvent):
    """处理抢银行指令"""
    user_id = str(event.user_id)
    user = get_user(user_id)
    
    # 检查是否有50积分
    if user.points < 50:
        await matcher.finish("积分不足50，无法抢银行")
        return
    
    # 随机结果
    rand = random.random() * 100
    
    if rand < 5:  # 5% 大成功
        amount = random.randint(100, 300)
        user.points += amount
        save_user(user_id)
        await matcher.finish(f"抢银行大获成功，获得了 {amount} 积分")
    elif rand < 40:  # 35% 成功
        amount = random.randint(1, 100)
        user.points += amount
        save_user(user_id)
        await matcher.finish(f"抢银行成功，获得 {amount} 积分")
    elif rand < 70:  # 30% 不敢去
        await matcher.finish("思来想去，走到半路还是怕了回家了吧")
    else:  # 30% 失败
        amount = random.randint(1, 50)
        user.points -= amount
        save_user(user_id)
        await matcher.finish(f"抢银行失败，你被抓了，赔偿了 {amount} 积分")


# ========== 抽奖指令 ==========
lottery_cmd = on_command("抽奖", priority=5, block=True)


@lottery_cmd.handle()
async def handle_lottery(
    matcher: Matcher,
    event: MessageEvent,
    args: Message = CommandArg(),
):
    """处理抽奖指令"""
    user_id = str(event.user_id)
    
    args_text = args.extract_plain_text().strip()
    if not args_text or not args_text.isdigit():
        await matcher.finish("请使用「抽奖 积分」格式，积分范围 10-500")
        return
    
    amount = int(args_text)
    if amount < 10 or amount > 500:
        await matcher.finish("抽奖积分范围是 10-500")
        return
    
    user = get_user(user_id)
    if user.points < amount:
        await matcher.finish(f"积分不足，你只有 {user.points} 积分")
        return
    
    # 扣除积分
    user.points -= amount
    
    # 随机获得 0-3 倍积分
    multiplier = random.random() * 3
    gained = int(amount * multiplier)
    
    user.points += gained
    save_user(user_id)
    
    await matcher.finish(f"""得分: {gained}
现有积分: {user.points}""")


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
    user_id = str(event.user_id)
    
    # 检查是否已有进行中的游戏
    existing_game = get_guess_game(user_id)
    if existing_game:
        await matcher.finish(f"你已有进行中的猜数字游戏，剩余 {existing_game.chances} 次机会")
        return
    
    args_text = args.extract_plain_text().strip()
    if not args_text or not args_text.isdigit():
        await matcher.finish("请使用「猜数字 积分」格式开始游戏，积分范围 10-500")
        return
    
    amount = int(args_text)
    if amount < 10 or amount > 500:
        await matcher.finish("下注积分范围是 10-500")
        return
    
    user = get_user(user_id)
    if user.points < amount:
        await matcher.finish(f"积分不足，你只有 {user.points} 积分")
        return
    
    # 扣除积分并开始游戏
    user.points -= amount
    save_user(user_id)
    
    game = start_guess_game(user_id, amount)
    
    await matcher.finish(f"""🎈猜数字开始~
积分 -{amount}
你有 {game.chances} 次机会
请发送 我猜 xx""")


@guess_play_cmd.handle()
async def handle_guess_play(
    matcher: Matcher,
    event: MessageEvent,
    args: Message = CommandArg(),
):
    """处理猜数字游戏指令"""
    user_id = str(event.user_id)
    
    # 获取进行中的游戏
    game = get_guess_game(user_id)
    if not game:
        await matcher.finish("你还没有开始猜数字游戏，请先发送「猜数字 积分」")
        return
    
    args_text = args.extract_plain_text().strip()
    if not args_text or not args_text.isdigit():
        await matcher.finish("请发送「我猜 数字」，数字范围 1-100")
        return
    
    guess = int(args_text)
    if guess < 1 or guess > 100:
        await matcher.finish("数字范围是 1-100")
        return
    
    game.chances -= 1
    
    if guess == game.target:
        # 猜对了
        user = get_user(user_id)
        gained = game.bet * 2
        user.points += gained
        save_user(user_id)
        end_guess_game(user_id)
        
        await matcher.finish(f"""🎯猜对啦，游戏结束
得分 {gained}
现有积分 {user.points}""")
        return
    
    if game.chances <= 0:
        # 机会用完
        end_guess_game(user_id)
        await matcher.finish(f"""🎃猜错啦，游戏结束
正确答案是 {game.target}""")
        return
    
    # 继续猜
    hint = "猜小了" if guess < game.target else "猜大了"
    await matcher.finish(f"""{hint}，比 {guess} {'大' if guess < game.target else '小'}
剩余 {game.chances} 次机会""")


# ========== 功能列表指令 ==========
help_cmd = on_command("功能", priority=5, block=True)

# 功能详细描述
FEATURE_HELP = {
    "签到": """【签到】
指令：签到 或 打卡
描述：每日签到，连续签到都有积分加成~""",
    "积分": """【积分】
指令：积分 或 查积分 或 我的积分
描述：查看自己所拥有的积分""",
    "转账": """【转账】
指令：转账 积分 @某人
描述：将n积分转账给某人""",
    "打劫": """【打劫】
指令：打劫 @某人
描述：打劫别人的积分""",
    "存入银行": """【存入银行】
指令：存入银行 积分
描述：将积分存入银行，每天计算利息""",
    "取出银行": """【取出银行】
指令：取出银行 积分
描述：将积分从银行取出""",
    "抢银行": """【抢银行】
指令：抢银行
描述：抢银行，有几率获得积分，也可能被扣分""",
    "抽奖": """【抽奖】
指令：抽奖 积分
描述：消耗积分抽奖，随机获取0-3倍积分""",
    "猜数字": """【猜数字】
指令：猜数字 积分 / 我猜 数字
描述：消耗积分开始猜数字游戏，猜中则积分翻倍""",
    "lf搜图": """【lf搜图】
指令：lf搜图（引用图片）
描述：以图搜图功能""",
    "开启lf搜图": """【开启lf搜图】
指令：开启lf搜图
描述：超级用户开启群聊搜图功能""",
    "关闭lf搜图": """【关闭lf搜图】
指令：关闭lf搜图
描述：超级用户关闭群聊搜图功能""",
}


@help_cmd.handle()
async def handle_help(
    matcher: Matcher,
    args: Message = CommandArg(),
):
    """处理功能列表指令"""
    args_text = args.extract_plain_text().strip()
    
    # 如果有参数，显示对应功能的详细描述
    if args_text:
        if args_text in FEATURE_HELP:
            await matcher.finish(FEATURE_HELP[args_text])
        else:
            # 模糊匹配
            matched = [k for k in FEATURE_HELP.keys() if args_text in k]
            if matched:
                await matcher.finish(FEATURE_HELP[matched[0]])
            else:
                await matcher.finish(f"未找到「{args_text}」相关功能，发送「功能」查看所有功能列表")
        return
    
    # 无参数时显示功能列表
    msg = """老肥工具箱功能列表

【搜图功能】
lf搜图 - 引用图片进行搜索
开启lf搜图 - 开启搜图功能(超管)
关闭lf搜图 - 关闭搜图功能(超管)

【积分系统】
签到/打卡 - 每日签到获取积分
积分/查积分 - 查看积分信息
转账 积分 @某人 - 转账给他人
打劫 @某人 - 打劫他人积分

【银行系统】
存入银行 积分 - 存入银行
取出银行 积分 - 取出银行
抢银行 - 抢银行获取积分

【娱乐功能】
抽奖 积分 - 消耗积分抽奖
猜数字 积分 - 开始猜数字游戏
我猜 数字 - 猜数字(1-100)

发送「功能 功能名称」查看详细说明"""
    await matcher.finish(msg)


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
        await matcher.finish("打劫功能仅在群聊可用")
        return
    
    # 检查是否@了某人
    if not event.reply and not any(seg.type == "at" for seg in args):
        await matcher.finish("请使用「打劫 @某人」格式")
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
        await matcher.finish("请@要打劫的对象")
        return
    
    if target_id == user_id:
        await matcher.finish("不能打劫自己")
        return
    
    user = get_user(user_id)
    
    # 检查是否有50积分
    if user.points < 50:
        await matcher.finish("积分不足50，无法打劫")
        return
    
    target_user = get_user(target_id)
    
    if target_user.points < 1:
        await matcher.finish("对方没有可打劫的积分")
        return
    
    # 随机结果
    rand = random.random() * 100
    
    if rand < 40:  # 40% 成功
        max_rob = min(target_user.points, 50)
        amount = random.randint(1, max_rob)
        target_user.points -= amount
        user.points += amount
        save_user(user_id)
        save_user(target_id)
        await matcher.finish(f"打劫成功，获得 {amount} 积分")
    elif rand < 70:  # 30% 失败扣分
        amount = random.randint(1, 50)
        user.points -= amount
        save_user(user_id)
        await matcher.finish(f"打劫失败，被反杀，损失 {amount} 积分")
    else:  # 30% 什么都没发生
        await matcher.finish("打劫失败，对方跑掉了")