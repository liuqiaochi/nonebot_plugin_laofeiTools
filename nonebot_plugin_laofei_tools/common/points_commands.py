"""
积分系统指令处理

功能：签到、积分查询、转账、抽签、积分排行、新手大礼包
"""

from pathlib import Path

from nonebot import on_command
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
    do_sign,
    draw_fortune,
    get_all_user_ids,
    get_level_title,
    get_points_ranking,
    get_user,
    get_user_info,
    save_user,
)
from ..config import is_points_enabled, enable_points, disable_points

# 抽签图片目录
FORTUNE_IMAGE_DIR = Path(__file__).parent.parent / "image"


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
sign_cmd = on_command("签到", aliases={"打卡", "积分签到", "日常签到"}, priority=5, block=True, force_whitespace=True)


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
fortune_cmd = on_command("抽签", aliases={"今日运气", "今日抽签", "今日气运"}, priority=5, block=True, force_whitespace=True)


@fortune_cmd.handle()
async def handle_fortune(matcher: Matcher, event: MessageEvent):
    """处理每日抽签指令（独立功能，不受积分系统开关影响）"""
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
累计签到: {info['total_sign_days']} 天
连续签到: {info['continuous_sign_days']} 天
{sign_status}"""
    
    await matcher.finish(Message([
        MessageSegment.reply(event.message_id),
        MessageSegment.text(msg)
    ]))


# ========== 转账指令 ==========
transfer_cmd = on_command("转账", aliases={"转积分", "给积分"}, priority=5, block=True)


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


# ========== 积分排行榜指令 ==========
ranking_cmd = on_command("积分排行", aliases={"排行榜", "今日排行"}, priority=5, block=True, force_whitespace=True)


@ranking_cmd.handle()
async def handle_ranking(matcher: Matcher, bot: Bot, event: MessageEvent):
    """查看积分排行榜"""
    ranking = get_points_ranking(10)
    
    if not ranking:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("暂无排行数据")
        ]))
        return
    
    msg = "🏆 积分排行榜 TOP10\n"
    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    for i, (user_id, total, points, bank) in enumerate(ranking):
        medal = medals[i] if i < len(medals) else f"{i+1}."
        msg += f"{medal} {user_id}  {total}分\n"
    
    await matcher.finish(Message([
        MessageSegment.reply(event.message_id),
        MessageSegment.text(msg)
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


# ========== 新手大礼包指令 ==========

newbie_cmd = on_command("新手大礼包", aliases={"领取新手大礼包"}, priority=5, block=True, force_whitespace=True)


@newbie_cmd.handle()
async def handle_newbie(matcher: Matcher, event: MessageEvent):
    """领取新手大礼包"""
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
    
    # 检查是否已领取
    if user.newbie_claimed:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("你已经领取过新手大礼包了")
        ]))
        return
    
    # 发放500积分
    user.points += 500
    user.newbie_claimed = True
    save_user(user_id)
    
    # 发放每种食物各一个
    from ..pet.pet_data import FOODS as PET_FOODS, add_item
    food_names = list(PET_FOODS.keys())
    for food in food_names:
        add_item(user_id, "food", food)
    
    food_list = "、".join(food_names)
    await matcher.finish(Message([
        MessageSegment.reply(event.message_id),
        MessageSegment.text(f"🎁 新手大礼包领取成功！\n获得 500 积分\n获得食物：{food_list} 各1个")
    ]))

