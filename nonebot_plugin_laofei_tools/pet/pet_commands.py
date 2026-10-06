"""
宠物系统指令处理

功能：宠物领养、散步、抚摸、喂食、PK、商店、配饰、背包
"""

from pathlib import Path
import random
import time

from nonebot import on_command, logger
from nonebot.permission import SUPERUSER
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

from ..config import is_points_enabled
from ..common.points_data import get_user as get_points_user, save_user as save_points_user, do_sign
from .pet_data import (
    PET_TYPES, FOODS, ACCESSORIES, AFFECTION_LEVELS,
    get_pet, create_pet, save_pet, abandon_pet, reincarnate_pet,
    get_pet_level, get_affection_level, get_effective_force, get_effective_luck,
    get_pet_max_hp,
    get_display_name,
    get_inventory, add_item, remove_item, save_inventory,
    equip_accessory, unequip_accessory,
    do_walk, do_pat, do_feed, do_pk,
    refresh_stamina_if_needed,
    do_work, do_steal, get_item_by_id,
    get_all_pet_owners,
    FEED_STAMINA_CAP,
)
from .fishing_data import (
    roll_fish, add_caught_fish, FISHING_STAMINA_COST,
    DAILY_FISHING_LIMIT, record_fishing, get_fishing_remaining,
)

# 宠物图片目录（插件根目录下的 image 文件夹）
PET_IMAGE_DIR = Path(__file__).parent.parent / "image"

# 弃养确认缓存：user_id -> True（等待确认中）
_abandon_confirm: dict = {}


def make_hp_bar(current: int, max_hp: int, bar_length: int = 5) -> str:
    """生成文本血量条，格式 HP:[█████] 200

    Args:
        current: 当前血量
        max_hp: 最大血量
        bar_length: 血量条长度（方块数），默认 5

    Returns:
        血量条字符串
    """
    ratio = current / max_hp if max_hp > 0 else 0
    filled = round(ratio * bar_length)
    if current > 0 and filled == 0:
        filled = 1
    bar = "█" * filled + "░" * (bar_length - filled)
    return f"HP:[{bar}] {current}"


# ========== 我的宠物指令 ==========
my_pet_cmd = on_command("我的宠物", aliases={"宠物"}, priority=5, block=True, force_whitespace=True)


@my_pet_cmd.handle()
async def handle_my_pet(matcher: Matcher, event: MessageEvent):
    """查看宠物信息 / 未领养时展示领养列表"""
    # 检查群聊是否开启积分系统
    if isinstance(event, GroupMessageEvent):
        if not is_points_enabled(str(event.group_id)):
            await matcher.finish()
            return

    user_id = str(event.user_id)
    pet = get_pet(user_id)

    if pet is None:
        # 未领养：展示宠物选择列表
        msg = "你还没有领养宠物，请从以下宠物中选择一只：\n\n"
        for pet_type, info in PET_TYPES.items():
            msg += f"🐾 {info['name']}\n"
            msg += f"   幸运: {info['luck']} | 武力: {info['force']}\n"
            msg += f"   天赋「{info['talent']}」: {info['talent_desc']}\n\n"
        msg += "发送「领养 宠物名」来领养，如：领养 Doro"
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text(msg)
        ]))
        return

    # 已领养：展示宠物完整信息
    refresh_stamina_if_needed(user_id)
    pet_info = PET_TYPES[pet.pet_type]
    level = get_pet_level(pet.exp)
    aff_level = get_affection_level(pet.affection)
    eff_force = get_effective_force(pet)
    eff_luck = get_effective_luck(pet)

    acc_text = pet.accessory if pet.accessory else "无"

    # 使用昵称或默认名
    display_name = pet.nickname if pet.nickname else pet_info['name']

    max_hp = get_pet_max_hp(pet)
    msg = f"🐾 {display_name}\n"
    msg += f"  {make_hp_bar(max_hp, max_hp)}\n"

    # 计算当前经验和升级所需经验
    remaining_exp = pet.exp
    for lv in range(1, level):
        remaining_exp -= lv * 50
    next_level_exp = level * 50
    msg += f"等级: Lv.{level}（{remaining_exp}/{next_level_exp}）\n"

    msg += f"好感度: Lv.{aff_level}（{pet.affection}点）\n"
    msg += f"体力: {pet.stamina}/{pet.max_stamina}\n"
    msg += f"幸运: {eff_luck}\n"
    msg += f"武力: {eff_force}\n"
    msg += f"配饰: {acc_text}\n"
    msg += f"天赋「{pet_info['talent']}」: {pet_info['talent_desc']}"

    # 构建消息链（图片 + 文字）
    msg_chain = [MessageSegment.reply(event.message_id)]

    # 尝试发送宠物图片
    image_path = PET_IMAGE_DIR / pet_info["image"]
    if image_path.exists():
        msg_chain.append(MessageSegment.image(f"file://{image_path}"))

    msg_chain.append(MessageSegment.text(msg))

    await matcher.finish(Message(msg_chain))


# ========== 领养指令 ==========
adopt_cmd = on_command("领养", aliases={"宠物领养", "领养宠物"}, priority=5, block=True)


@adopt_cmd.handle()
async def handle_adopt(matcher: Matcher, event: MessageEvent, args: Message = CommandArg()):
    """领养宠物"""
    # 检查群聊是否开启积分系统
    if isinstance(event, GroupMessageEvent):
        if not is_points_enabled(str(event.group_id)):
            await matcher.finish()
            return

    user_id = str(event.user_id)

    # 检查是否已有宠物
    existing = get_pet(user_id)
    if existing is not None:
        pet_info = PET_TYPES[existing.pet_type]
        current_name = existing.nickname if existing.nickname else pet_info['name']
        msg = f"你已领养「{current_name}」啦～\n"
        msg += "想换宠物？先发送「弃养」解除当前宠物，再「领养 宠物名」即可更换。\n\n"
        msg += "当前所有可领养宠物：\n\n"
        for pet_type, info in PET_TYPES.items():
            msg += f"🐾 {info['name']}\n"
            msg += f"   幸运: {info['luck']} | 武力: {info['force']}\n"
            msg += f"   天赋「{info['talent']}」: {info['talent_desc']}\n\n"
        msg += "发送「领养 宠物名」来领养，如：领养 Doro"
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text(msg)
        ]))
        return

    # 解析宠物名称参数
    pet_name = args.extract_plain_text().strip()
    if not pet_name:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("请使用「领养 宠物名」格式，如：领养 Doro")
        ]))
        return

    # 匹配宠物名称到宠物种类（支持中文名和英文 key）
    target_type = None
    for pet_type, info in PET_TYPES.items():
        if pet_name == info["name"] or pet_name.lower() == pet_type:
            target_type = pet_type
            break

    if target_type is None:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("请选择有效的宠物种类，发送「我的宠物」查看可选列表")
        ]))
        return

    # 创建宠物
    pet = create_pet(user_id, target_type)
    pet_info = PET_TYPES[target_type]

    msg = f"🎉 成功领养了 {pet_info['name']}！\n"
    msg += f"幸运: {pet_info['luck']} | 武力: {pet_info['force']}\n"
    msg += f"体力: {pet.stamina}/{pet.max_stamina}\n"
    msg += f"天赋「{pet_info['talent']}」: {pet_info['talent_desc']}\n"
    msg += "发送「宠物帮助」查看所有宠物指令"

    # 构建消息链（图片 + 文字）
    msg_chain = [MessageSegment.reply(event.message_id)]
    image_path = PET_IMAGE_DIR / pet_info["image"]
    if image_path.exists():
        msg_chain.append(MessageSegment.image(f"file://{image_path}"))
    msg_chain.append(MessageSegment.text(msg))

    await matcher.finish(Message(msg_chain))


# ========== 宠物转移（转生）指令 ==========
TRANSFER_COST = 5000
# 待二次确认状态：user_id -> {"new_type": str, "ts": float}
_transfer_pending: dict = {}


pet_transfer_cmd = on_command("宠物转移", aliases={"转移宠物", "宠物转生", "宠物转型"}, priority=5, block=True, force_whitespace=True)


@pet_transfer_cmd.handle()
async def handle_transfer(matcher: Matcher, event: MessageEvent, args: Message = CommandArg()):
    """宠物转移（转生）：消耗 5000 积分，将当前宠物转换为其他种类并继承全部属性"""
    if isinstance(event, GroupMessageEvent):
        if not is_points_enabled(str(event.group_id)):
            await matcher.finish()
            return

    user_id = str(event.user_id)
    pet = get_pet(user_id)
    if pet is None:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("你还没有领养宠物，请先发送「我的宠物」领养一只")
        ]))
        return

    # 解析目标宠物种类
    pet_name = args.extract_plain_text().strip()
    if not pet_name:
        msg = "请使用「宠物转移 宠物名」格式，如：宠物转移 Doro\n\n当前所有可转移为的种类：\n\n"
        for pet_type, info in PET_TYPES.items():
            msg += f"🐾 {info['name']}\n"
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text(msg)
        ]))
        return

    target_type = None
    for pet_type, info in PET_TYPES.items():
        if pet_name == info["name"] or pet_name.lower() == pet_type:
            target_type = pet_type
            break
    if target_type is None:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("请选择有效的宠物种类，发送「宠物转移」查看可选列表")
        ]))
        return

    if target_type == pet.pet_type:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text(f"你当前已经是「{PET_TYPES[pet.pet_type]['name']}」了，无法转移成同种宠物")
        ]))
        return

    # 检查积分
    points_user = get_points_user(user_id)
    if points_user.points < TRANSFER_COST:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text(f"积分不足，宠物转移需要 {TRANSFER_COST} 积分，你只有 {points_user.points} 积分")
        ]))
        return

    # 存入待确认状态（二次确认）
    _transfer_pending[user_id] = {"new_type": target_type, "ts": time.time()}
    old_name = pet.nickname if pet.nickname else PET_TYPES[pet.pet_type]['name']
    new_name = PET_TYPES[target_type]['name']
    await matcher.finish(Message([
        MessageSegment.reply(event.message_id),
        MessageSegment.text(
            f"⚠️ 确认将你的宠物「{old_name}」转移为「{new_name}」？\n"
            f"将消耗 {TRANSFER_COST} 积分，且不可撤销（原宠物将被替换，新宠物继承全部属性）。\n"
            f"回复「确认转移」完成，或回复「取消转移」取消。"
        )
    ]))
    return


pet_transfer_confirm_cmd = on_command("确认转移", aliases={"确认宠物转移"}, priority=5, block=True, force_whitespace=True)


@pet_transfer_confirm_cmd.handle()
async def handle_transfer_confirm(matcher: Matcher, event: MessageEvent):
    """二次确认：完成宠物转移"""
    if isinstance(event, GroupMessageEvent):
        if not is_points_enabled(str(event.group_id)):
            await matcher.finish()
            return

    user_id = str(event.user_id)
    pending = _transfer_pending.get(user_id)
    if pending is None:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("你当前没有待确认的宠物转移，请先发送「宠物转移 宠物名」")
        ]))
        return

    # 超时校验（5 分钟）
    if time.time() - pending.get("ts", 0) > 300:
        _transfer_pending.pop(user_id, None)
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("宠物转移确认已超时（5 分钟），请重新发起「宠物转移 宠物名」")
        ]))
        return

    new_type = pending["new_type"]
    # 重新校验宠物与积分（防止期间状态变化）
    pet = get_pet(user_id)
    if pet is None:
        _transfer_pending.pop(user_id, None)
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("你已没有宠物，无法完成转移")
        ]))
        return
    points_user = get_points_user(user_id)
    if points_user.points < TRANSFER_COST:
        _transfer_pending.pop(user_id, None)
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text(f"积分不足，转移需要 {TRANSFER_COST} 积分，你只有 {points_user.points} 积分")
        ]))
        return

    # 扣积分 + 转生
    points_user.points -= TRANSFER_COST
    save_points_user(user_id)
    old_name = pet.nickname if pet.nickname else PET_TYPES[pet.pet_type]['name']
    new_pet = reincarnate_pet(user_id, new_type)
    _transfer_pending.pop(user_id, None)

    new_info = PET_TYPES[new_type]
    msg = f"✅ 转移成功！你的宠物已从「{old_name}」变为「{new_info['name']}」\n"
    msg += f"消耗 {TRANSFER_COST} 积分，剩余 {points_user.points} 积分\n"
    msg += f"已继承全部属性：经验 {new_pet.exp} | 体力 {new_pet.stamina}/{new_pet.max_stamina} | 好感 {new_pet.affection}\n"
    msg += f"天赋「{new_info['talent']}」: {new_info['talent_desc']}"

    msg_chain = [MessageSegment.reply(event.message_id)]
    image_path = PET_IMAGE_DIR / new_info["image"]
    if image_path.exists():
        msg_chain.append(MessageSegment.image(f"file://{image_path}"))
    msg_chain.append(MessageSegment.text(msg))
    await matcher.finish(Message(msg_chain))


pet_transfer_cancel_cmd = on_command("取消转移", aliases={"取消宠物转移"}, priority=5, block=True, force_whitespace=True)


@pet_transfer_cancel_cmd.handle()
async def handle_transfer_cancel(matcher: Matcher, event: MessageEvent):
    """取消宠物转移"""
    user_id = str(event.user_id)
    if _transfer_pending.pop(user_id, None) is not None:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("已取消宠物转移")
        ]))
        return
    await matcher.finish(Message([
        MessageSegment.reply(event.message_id),
        MessageSegment.text("你当前没有待确认的宠物转移")
    ]))


# ========== 宠物帮助指令 ==========
pet_help_cmd = on_command("宠物帮助", priority=5, block=True, force_whitespace=True)


@pet_help_cmd.handle()
async def handle_pet_help(matcher: Matcher, event: MessageEvent):
    """展示宠物系统帮助信息（图片版）"""
    help_b64 = None
    try:
        from .shop_image import generate_help_image
        help_b64 = generate_help_image()
    except Exception as e:
        logger.error(f"生成帮助图片失败: {e}")

    if help_b64:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.image(f"base64://{help_b64}"),
        ]))
    else:
        # 降级为文字版
        msg = """【宠物系统指令】
我的宠物 - 查看宠物信息/领养宠物
领养 宠物名 - 领养指定宠物
宠物散步 - 消耗体力散步获取经验和道具
宠物打工 - 消耗体力打工赚取积分（4小时间隔）
快速打工 - 自动打工至体力耗尽，合并转发结果（别名：一键打工）
快速散步 - 自动散步至体力耗尽，合并转发结果（别名：一键散步）
一键日常 / 宠物日常 - 签到+抚摸+打工+散步+钓鱼+偷取 一键完成
一键日常1 - 一键日常 + 快速打工（打工至体力耗尽）
一键日常2 - 一键日常 + 快速散步（散步至体力耗尽）
一键日常3 - 一键日常 + 快速钓鱼（钓鱼至体力耗尽）
宠物抚摸 - 每日抚摸提升好感度
宠物喂食 食物名1 食物名2 ... - 多食物空格分隔喂食（或 食物名 数量），体力可突破上限累加至 9999
宠物pk @某人 - 与他人宠物PK对战
宠物商店 - 查看商店商品
购买 商品名 [数量] - 使用积分购买商品
出售 物品名 [数量] - 出售背包物品获得积分
宠物佩戴 配饰名 - 佩戴配饰提升属性
宠物改名 新名字 - 给宠物改名（500积分）
宠物排行榜 [武力/幸运] - 展示等级/武力/幸运 TOP10
宠物背包 - 查看道具背包
宠物弃养 - 弃养宠物（需二次确认）
宠物转移 宠物名 - 消耗 5000 积分将当前宠物转生为目标种类并继承全部属性（需二次确认）
宠物帮助 - 查看本帮助信息"""

        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text(msg)
        ]))


# ========== 宠物背包指令 ==========
pet_inventory_cmd = on_command("宠物背包", priority=5, block=True, force_whitespace=True)


@pet_inventory_cmd.handle()
async def handle_inventory(matcher: Matcher, event: MessageEvent):
    """查看道具背包"""
    # 检查群聊是否开启积分系统
    if isinstance(event, GroupMessageEvent):
        if not is_points_enabled(str(event.group_id)):
            await matcher.finish()
            return

    user_id = str(event.user_id)
    pet = get_pet(user_id)
    if pet is None:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("你还没有领养宠物，请先发送「我的宠物」领养一只")
        ]))
        return

    inv = get_inventory(user_id)

    # Check if inventory is empty
    if not inv.foods and not inv.accessories:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("背包中没有任何道具")
        ]))
        return

    msg = "🎒 宠物背包\n"

    # Foods section
    if inv.foods:
        msg += "\n【食物】\n"
        for food_name, count in inv.foods.items():
            msg += f"  {food_name} × {count}\n"

    # Accessories section
    if inv.accessories:
        msg += "\n【配饰】\n"
        for acc_name, count in inv.accessories.items():
            equipped_mark = " 👈已佩戴" if acc_name == pet.accessory else ""
            msg += f"  {acc_name} × {count}{equipped_mark}\n"

    # Show currently equipped accessory if not in inventory
    if pet.accessory and pet.accessory not in inv.accessories:
        if not inv.accessories:
            msg += "\n【配饰】\n"
        msg += f"  {pet.accessory} 👈已佩戴\n"

    await matcher.finish(Message([
        MessageSegment.reply(event.message_id),
        MessageSegment.text(msg.rstrip())
    ]))


# ========== 宠物散步指令 ==========
pet_walk_cmd = on_command("宠物散步", aliases={"宠物溜达", "宠物出门"}, priority=5, block=True, force_whitespace=True)


@pet_walk_cmd.handle()
async def handle_walk(matcher: Matcher, event: MessageEvent):
    """宠物散步"""
    # 检查群聊是否开启积分系统
    if isinstance(event, GroupMessageEvent):
        if not is_points_enabled(str(event.group_id)):
            await matcher.finish()
            return

    user_id = str(event.user_id)
    pet = get_pet(user_id)
    if pet is None:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("你还没有领养宠物，请先发送「我的宠物」领养一只")
        ]))
        return

    result = do_walk(user_id)

    if not result["success"]:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text(result["message"])
        ]))
        return

    msg = f"🐾 {result['pet_name']} 散步归来~\n"
    msg += f"体力: {result['stamina_before']} → {result['stamina_after']}\n"
    msg += f"经验: +20\n"
    if result.get("phoebe_stamina_restore", 0) > 0:
        msg += f"✨ 卖萌成功！恢复了 {result['phoebe_stamina_restore']} 体力\n"
    if result["dropped"]:
        msg += f"🎁 捡到了 {result['dropped_item']}！"
    else:
        msg += result["message"]

    if result.get("bonus_points", 0) > 0:
        msg += f"\n🍀 祥子吃瓜触发！额外获得 {result['bonus_points']} 积分"

    await matcher.finish(Message([
        MessageSegment.reply(event.message_id),
        MessageSegment.text(msg)
    ]))


# ========== 宠物抚摸指令 ==========
pet_pat_cmd = on_command("宠物抚摸", aliases={"摸宠物", "疼爱宠物"}, priority=5, block=True, force_whitespace=True)


@pet_pat_cmd.handle()
async def handle_pat(matcher: Matcher, event: MessageEvent):
    """抚摸宠物"""
    # 检查群聊是否开启积分系统
    if isinstance(event, GroupMessageEvent):
        if not is_points_enabled(str(event.group_id)):
            await matcher.finish()
            return

    user_id = str(event.user_id)
    pet = get_pet(user_id)
    if pet is None:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("你还没有领养宠物，请先发送「我的宠物」领养一只")
        ]))
        return

    result = do_pat(user_id)

    if not result["success"]:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text(result["message"])
        ]))
        return

    gain = result["affection_gain"]
    msg = f"🐾 你抚摸了 {result['pet_name']}~\n"
    msg += f"好感度: {result['affection_before']} → {result['affection_after']}（+{gain}）"

    if result.get("bonus_points", 0) > 0:
        msg += f"\n🍀 祥子吃瓜触发！额外获得 {result['bonus_points']} 积分"

    await matcher.finish(Message([
        MessageSegment.reply(event.message_id),
        MessageSegment.text(msg)
    ]))


# ========== 宠物喂食指令 ==========
pet_feed_cmd = on_command("宠物喂食", aliases={"宠物喂养", "喂养宠物"}, priority=5, block=True)


@pet_feed_cmd.handle()
async def handle_feed(matcher: Matcher, event: MessageEvent, args: Message = CommandArg()):
    """喂食宠物"""
    # 检查群聊是否开启积分系统
    if isinstance(event, GroupMessageEvent):
        if not is_points_enabled(str(event.group_id)):
            await matcher.finish()
            return

    user_id = str(event.user_id)
    pet = get_pet(user_id)
    if pet is None:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("你还没有领养宠物，请先发送「我的宠物」领养一只")
        ]))
        return

    raw = args.extract_plain_text().strip()
    if not raw:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("请使用「宠物喂食 食物名1 食物名2 ...」（空格分隔多选）或「宠物喂食 食物名 数量」格式")
        ]))
        return

    tokens = raw.split()
    # 解析数量：仅当「单个食物 + 末尾纯数字」时视为该食物×数量；
    # 多个食物用空格分隔，每种各喂 1 次（多选）
    per_count = 1
    if len(tokens) == 2 and tokens[1].isdigit():
        food_list = [tokens[0]]
        per_count = int(tokens[1])
    else:
        food_list = tokens

    # 逐个食物喂食，累计结果
    total_stamina_gain = 0
    total_affection_gain = 0
    fed_details = []   # (food_name, stamina_gain, affection_gain, is_favorite, pet_name)
    failed = []        # (food_name, reason)
    penguin_triggered = False

    for food_name in food_list:
        for _ in range(per_count):
            # 体力达到硬上限（喂食上限 9999）则停止后续喂食
            current_pet = get_pet(user_id)
            if current_pet and current_pet.stamina >= FEED_STAMINA_CAP:
                if not fed_details:
                    await matcher.finish(Message([
                        MessageSegment.reply(event.message_id),
                        MessageSegment.text(f"宠物体力已达上限（{FEED_STAMINA_CAP}），不需要喂食")
                    ]))
                    return
                break
            result = do_feed(user_id, food_name)
            if not result["success"]:
                failed.append((food_name, result["message"]))
                break
            fed_details.append((
                result["food_name"],
                result["stamina_gain"],
                result["affection_gain"],
                result["is_favorite"],
                result["pet_name"],
            ))
            if result.get("penguin_bonus", False):
                penguin_triggered = True
            total_stamina_gain += result["stamina_gain"]
            total_affection_gain += result["affection_gain"]

    # 一种都没喂成功（如全部背包缺货 / 无此食物）
    if not fed_details:
        reason = failed[0][1] if failed else "喂食失败"
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text(reason)
        ]))
        return

    pet_name = fed_details[0][4]
    distinct_foods = {d[0] for d in fed_details}
    if len(distinct_foods) == 1 and len(fed_details) > 1:
        # 单一食物喂多次：一行明细（保留💕最爱）+ 总数 + 合计，避免一长串
        fn, sg, ag, fav, _ = fed_details[0]
        tag = " 💕最爱" if fav else ""
        msg = (f"🐾 你喂了 {pet_name}：\n"
               f"{fn}　+{sg}体力 +{ag}好感{tag}\n"
               f"总共 {len(fed_details)}个\n"
               f"合计 体力 +{total_stamina_gain}　好感 +{total_affection_gain}")
    elif len(distinct_foods) == 1:
        # 单一食物喂 1 次：仅明细行
        fn, sg, ag, fav, _ = fed_details[0]
        tag = " 💕最爱" if fav else ""
        msg = f"🐾 你喂了 {pet_name}：{fn}　+{sg}体力 +{ag}好感{tag}"
    else:
        # 混搭多种食物：逐条明细 + 合计（旧版展示方式）
        msg = f"🐾 你喂了 {pet_name}：\n"
        for fn, sg, ag, fav, _ in fed_details:
            tag = " 💕最爱" if fav else ""
            msg += f"  {fn}　+{sg}体力 +{ag}好感{tag}\n"
        msg += f"——————————\n合计 体力 +{total_stamina_gain}　好感 +{total_affection_gain}"
    if failed:
        uniq = {}
        for fn, r in failed:
            uniq[fn] = r
        msg += "\n未喂食：" + "、".join(f"{fn}({r})" for fn, r in uniq.items())
    if penguin_triggered:
        msg += "\n🐧 咕咕嘎嘎触发！体力收益 +30%"

    await matcher.finish(Message([
        MessageSegment.reply(event.message_id),
        MessageSegment.text(msg)
    ]))


# ========== 宠物PK指令 ==========
pet_pk_cmd = on_command("宠物pk", aliases={"宠物PK", "宠物Pk", "宠物pK"}, priority=5, block=True)


@pet_pk_cmd.handle()
async def handle_pk(matcher: Matcher, bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    """宠物PK对战"""
    # PK需要@人，仅群聊可用
    if isinstance(event, PrivateMessageEvent):
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("宠物PK仅在群聊可用")
        ]))
        return

    # 检查群聊是否开启积分系统
    if isinstance(event, GroupMessageEvent):
        if not is_points_enabled(str(event.group_id)):
            await matcher.finish()
            return

    user_id = str(event.user_id)
    pet = get_pet(user_id)
    if pet is None:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("你还没有领养宠物，请先发送「我的宠物」领养一只")
        ]))
        return

    # 解析 @ 目标用户
    target_id = None
    for seg in args:
        if seg.type == "at":
            target_id = seg.data.get("qq")
            break

    if not target_id:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("请使用「宠物pk @某人」格式")
        ]))
        return

    target_id = str(target_id)

    if target_id == user_id:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("不能和自己的宠物 PK")
        ]))
        return

    result = do_pk(user_id, target_id)

    if not result["success"]:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text(result["message"])
        ]))
        return

    # 构建合并转发消息
    battle_text = ""
    # battle_log 已包含空行分隔
    battle_text += "\n".join(result["battle_log"])

    # 以合并转发方式发送
    node_content = Message(MessageSegment.text(battle_text))
    await bot.send_group_forward_msg(
        group_id=event.group_id,
        messages=[{
            "type": "node",
            "data": {
                "name": "宠物对战",
                "uin": str(bot.self_id),
                "content": node_content,
            },
        }],
    )

    await matcher.finish()


# ========== 宠物商店指令 ==========
pet_shop_cmd = on_command("宠物商店", aliases={"宠物店", "宠物店铺"}, priority=5, block=True, force_whitespace=True)


@pet_shop_cmd.handle()
async def handle_shop(matcher: Matcher, event: MessageEvent):
    """查看宠物商店"""
    # 检查群聊是否开启积分系统
    if isinstance(event, GroupMessageEvent):
        if not is_points_enabled(str(event.group_id)):
            await matcher.finish()
            return

    shop_b64 = None
    try:
        from .shop_image import generate_shop_image
        shop_b64 = generate_shop_image()
    except Exception as e:
        logger.error(f"生成商店图片失败: {e}")

    if shop_b64:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.image(f"base64://{shop_b64}"),
        ]))
    else:
        # 降级为文字版
        msg = "🏪 宠物商店\n"

        msg += "\n【食物】\n"
        for food_name, food_info in FOODS.items():
            stamina = food_info.get("stamina", 20)
            affection = food_info.get("affection", 5)
            msg += f"  {food_name}（+{stamina}体力+{affection}好感）- {food_info['price']} 积分\n"

        msg += "\n【普通配饰】\n"
        for acc_name, acc_info in ACCESSORIES.items():
            if acc_info["droppable"]:
                effects = []
                if acc_info.get("force", 0) > 0:
                    effects.append(f"武力+{acc_info['force']}")
                if acc_info.get("luck", 0) > 0:
                    effects.append(f"幸运+{acc_info['luck']}")
                if acc_info.get("stamina", 0) > 0:
                    effects.append(f"体力+{acc_info['stamina']}")
                if acc_info.get("hp", 0) > 0:
                    effects.append(f"血量+{acc_info['hp']}%")
                if acc_info.get("special") == "pat_bonus_10":
                    effects.append("抚摸好感+10")
                effect_str = "、".join(effects) if effects else "无"
                msg += f"  {acc_name}（{effect_str}）- {acc_info['price']} 积分\n"

        msg += "\n【特殊配饰】\n"
        for acc_name, acc_info in ACCESSORIES.items():
            if not acc_info["droppable"]:
                effects = []
                if acc_info.get("force", 0) > 0:
                    effects.append(f"武力+{acc_info['force']}")
                if acc_info.get("luck", 0) > 0:
                    effects.append(f"幸运+{acc_info['luck']}")
                if acc_info.get("stamina", 0) > 0:
                    effects.append(f"体力+{acc_info['stamina']}")
                if acc_info.get("hp", 0) > 0:
                    effects.append(f"血量+{acc_info['hp']}%")
                if acc_info.get("special") == "affection_1.5x":
                    effects.append("好感提升1.5倍")
                effect_str = "、".join(effects) if effects else "无"
                msg += f"  {acc_name}（{effect_str}）- {acc_info['price']} 积分\n"

        msg += "\n发送「购买 商品名」购买"

        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text(msg)
        ]))


# ========== 购买指令 ==========
buy_cmd = on_command("购买", priority=5, block=True)


@buy_cmd.handle()
async def handle_buy(matcher: Matcher, event: MessageEvent, args: Message = CommandArg()):
    """购买商品"""
    # 检查群聊是否开启积分系统
    if isinstance(event, GroupMessageEvent):
        if not is_points_enabled(str(event.group_id)):
            await matcher.finish()
            return

    user_id = str(event.user_id)
    pet = get_pet(user_id)
    if pet is None:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("你还没有领养宠物，请先发送「我的宠物」领养一只")
        ]))
        return

    item_name = args.extract_plain_text().strip()
    if not item_name:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("请使用「购买 商品名」或「购买 商品名 数量」格式")
        ]))
        return

    # 解析名称和数量
    parts = item_name.rsplit(None, 1)
    count = 1
    if len(parts) == 2 and parts[1].isdigit():
        item_name = parts[0]
        count = int(parts[1])
    if count < 1:
        count = 1

    # 查找商品及价格（支持编号或名称）
    item_type = None
    price = 0
    # 先尝试编号匹配
    resolved_name, resolved_type = get_item_by_id(item_name)
    if resolved_name:
        item_name = resolved_name
        item_type = resolved_type
        if item_type == "food":
            price = FOODS[item_name]["price"]
        else:
            price = ACCESSORIES[item_name]["price"]
    elif item_name in FOODS:
        item_type = "food"
        price = FOODS[item_name]["price"]
    elif item_name in ACCESSORIES:
        item_type = "accessory"
        price = ACCESSORIES[item_name]["price"]
    else:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text(f"商店中没有 {item_name}")
        ]))
        return

    total_price = price * count

    # 检查积分
    points_user = get_points_user(user_id)
    if points_user.points < total_price:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text(f"积分不足，{item_name}×{count} 需要 {total_price} 积分，你只有 {points_user.points} 积分")
        ]))
        return

    # 扣除积分
    points_user.points -= total_price
    save_points_user(user_id)

    # 添加到背包
    add_item(user_id, item_type, item_name, count)

    msg = f"✅ 成功购买 {item_name}×{count}！\n"
    msg += f"消耗 {total_price} 积分，剩余 {points_user.points} 积分"

    await matcher.finish(Message([
        MessageSegment.reply(event.message_id),
        MessageSegment.text(msg)
    ]))


# ========== 宠物佩戴指令 ==========
pet_equip_cmd = on_command("宠物佩戴", aliases={"宠物穿戴", "宠物装备", "使用配饰", "穿配饰"}, priority=5, block=True)


@pet_equip_cmd.handle()
async def handle_equip(matcher: Matcher, event: MessageEvent, args: Message = CommandArg()):
    """佩戴配饰"""
    # 检查群聊是否开启积分系统
    if isinstance(event, GroupMessageEvent):
        if not is_points_enabled(str(event.group_id)):
            await matcher.finish()
            return

    user_id = str(event.user_id)
    pet = get_pet(user_id)
    if pet is None:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("你还没有领养宠物，请先发送「我的宠物」领养一只")
        ]))
        return

    acc_name = args.extract_plain_text().strip()
    if not acc_name:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("请使用「宠物佩戴 配饰名」格式，如：宠物佩戴 小刀")
        ]))
        return

    result = equip_accessory(user_id, acc_name)

    if not result["success"]:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text(result["message"])
        ]))
        return

    msg = f"✅ {result['message']}"
    if result["old_accessory"]:
        msg += f"\n（已将 {result['old_accessory']} 放回背包）"

    await matcher.finish(Message([
        MessageSegment.reply(event.message_id),
        MessageSegment.text(msg)
    ]))

# ========== 出售指令 ==========
sell_cmd = on_command("出售", priority=5, block=True)


@sell_cmd.handle()
async def handle_sell(matcher: Matcher, event: MessageEvent, args: Message = CommandArg()):
    """出售背包中的物品"""
    # 检查群聊是否开启积分系统
    if isinstance(event, GroupMessageEvent):
        if not is_points_enabled(str(event.group_id)):
            await matcher.finish()
            return

    user_id = str(event.user_id)
    pet = get_pet(user_id)
    if pet is None:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("你还没有领养宠物，请先发送「我的宠物」领养一只")
        ]))
        return

    args_text = args.extract_plain_text().strip()
    if not args_text:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("请使用「出售 物品名」或「出售 物品名 数量」格式")
        ]))
        return

    # 解析名称和数量
    parts = args_text.rsplit(None, 1)
    count = 1
    if len(parts) == 2 and parts[1].isdigit():
        item_name = parts[0]
        count = int(parts[1])
    else:
        item_name = args_text
    if count < 1:
        count = 1

    inv = get_inventory(user_id)

    # 支持编号匹配
    resolved_name, _ = get_item_by_id(item_name)
    if resolved_name:
        item_name = resolved_name

    # 查找物品和价格
    sell_price = 0
    item_type = ""

    if item_name in FOODS and inv.foods.get(item_name, 0) > 0:
        item_type = "food"
        sell_price = FOODS[item_name]["price"] // 4
        available = inv.foods.get(item_name, 0)
    elif item_name in ACCESSORIES and inv.accessories.get(item_name, 0) > 0:
        item_type = "accessory"
        sell_price = ACCESSORIES[item_name]["price"] // 4
        available = inv.accessories.get(item_name, 0)
    else:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text(f"背包中没有「{item_name}」")
        ]))
        return

    # 限制数量不超过拥有数
    if count > available:
        count = available

    total_sell_price = sell_price * count

    # 移除物品
    remove_item(user_id, item_type, item_name, count)

    # 增加积分
    points_user = get_points_user(user_id)
    points_user.points += total_sell_price
    save_points_user(user_id)

    await matcher.finish(Message([
        MessageSegment.reply(event.message_id),
        MessageSegment.text(f"成功出售「{item_name}」×{count}，获得 {total_sell_price} 积分")
    ]))


# ========== 宠物打工指令 ==========
pet_work_cmd = on_command("宠物打工", priority=5, block=True, force_whitespace=True)


@pet_work_cmd.handle()
async def handle_work(matcher: Matcher, event: MessageEvent):
    """宠物打工"""
    # 检查群聊是否开启积分系统
    if isinstance(event, GroupMessageEvent):
        if not is_points_enabled(str(event.group_id)):
            await matcher.finish()
            return

    user_id = str(event.user_id)
    pet = get_pet(user_id)
    if pet is None:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("你还没有领养宠物，请先发送「我的宠物」领养一只")
        ]))
        return

    result = do_work(user_id)

    if not result["success"]:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text(result["message"])
        ]))
        return

    # 发放积分
    points_user = get_points_user(user_id)
    points_user.points += result["points_earned"]
    save_points_user(user_id)

    msg = f"💼 {result['pet_name']} 打工归来~\n"
    msg += f"获得 {result['points_earned']} 积分\n"
    msg += f"体力: {result['stamina_after']}"
    if result["dropped_items"]:
        msg += f"\n🎁 额外获得: {'、'.join(result['dropped_items'])}"
    if result.get("dfy_bonus", False):
        msg += f"\n🐟 米饭管够触发！积分收益 +30%"
    if result.get("bonus_points", 0) > 0:
        msg += f"\n🍀 祥子吃瓜触发！额外获得 {result['bonus_points']} 积分"

    await matcher.finish(Message([
        MessageSegment.reply(event.message_id),
        MessageSegment.text(msg)
    ]))


# ========== 快速打工指令 ==========
pet_quick_work_cmd = on_command("快速打工", aliases={"连续打工", "一键打工"}, priority=5, block=True, force_whitespace=True)


@pet_quick_work_cmd.handle()
async def handle_quick_work(bot: Bot, matcher: Matcher, event: MessageEvent):
    """快速打工：自动消耗体力打工直到体力不足，结果合并转发返回"""
    # 群聊需开启积分系统；私聊不受群开关限制
    if isinstance(event, GroupMessageEvent):
        if not is_points_enabled(str(event.group_id)):
            await matcher.finish()
            return

    user_id = str(event.user_id)
    pet = get_pet(user_id)
    if pet is None:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("你还没有领养宠物，请先发送「我的宠物」领养一只")
        ]))
        return

    refresh_stamina_if_needed(user_id)

    nodes = []
    texts = []
    total_points = 0
    total_drops = []
    work_count = 0

    while work_count < 30:
        result = do_work(user_id)
        if not result["success"]:
            break
        work_count += 1
        total_points += result["points_earned"]
        total_drops.extend(result["dropped_items"])

        # 发放积分
        points_user = get_points_user(user_id)
        points_user.points += result["points_earned"]
        save_points_user(user_id)

        # 构建节点
        node_text = f"💼 第{work_count}次打工\n获得 {result['points_earned']} 积分\n体力: {result['stamina_after']}"
        if result["dropped_items"]:
            node_text += f"\n🎁 额外获得: {'、'.join(result['dropped_items'])}"
        if result.get("dfy_bonus", False):
            node_text += f"\n🐟 米饭管够触发！积分 +30%"
        nodes.append({
            "type": "node",
            "data": {
                "name": result["pet_name"],
                "uin": str(bot.self_id),
                "content": str(Message(MessageSegment.text(node_text))),
            },
        })
        texts.append(node_text)

    if work_count == 0:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text(result["message"] if result is not None else f"体力不足，无法打工（当前体力: {get_pet(user_id).stamina}，需要30）")
        ]))
        return

    # 汇总节点
    pet_after = get_pet(user_id)
    summary = (
        f"⚡ 快速打工完成！\n"
        f"💼 共打工 {work_count} 次\n"
        f"💰 共获得 {total_points} 积分\n"
        f"⚡ 剩余体力: {pet_after.stamina}/{pet_after.max_stamina}"
    )
    if total_drops:
        summary += f"\n🎁 额外掉落: {'、'.join(total_drops)}"

    if isinstance(event, GroupMessageEvent):
        nodes.insert(0, {
            "type": "node",
            "data": {
                "name": "快速打工汇总",
                "uin": str(bot.self_id),
                "content": str(Message(MessageSegment.text(summary))),
            },
        })
        await bot.send_group_forward_msg(
            group_id=event.group_id,
            messages=nodes,
        )
        await matcher.finish()
    else:
        # 私聊：合并为单条文本返回
        full = summary + "\n\n" + "\n\n".join(texts)
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text(full)
        ]))


# ========== 快速散步指令 ==========
pet_quick_walk_cmd = on_command("快速散步", aliases={"连续散步", "一键散步"}, priority=5, block=True, force_whitespace=True)


@pet_quick_walk_cmd.handle()
async def handle_quick_walk(bot: Bot, matcher: Matcher, event: MessageEvent):
    """快速散步：自动消耗体力散步直到体力不足，结果合并转发返回"""
    # 群聊需开启积分系统；私聊不受群开关限制
    if isinstance(event, GroupMessageEvent):
        if not is_points_enabled(str(event.group_id)):
            await matcher.finish()
            return

    user_id = str(event.user_id)
    pet = get_pet(user_id)
    if pet is None:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("你还没有领养宠物，请先发送「我的宠物」领养一只")
        ]))
        return

    refresh_stamina_if_needed(user_id)

    nodes = []
    texts = []
    total_exp = 0
    total_drops = []
    walk_count = 0

    while walk_count < 30:
        result = do_walk(user_id)
        if not result["success"]:
            break
        walk_count += 1
        total_exp += 20
        if result["dropped"]:
            total_drops.append(result["dropped_item"])

        # 构建节点
        node_text = f"🐾 第{walk_count}次散步\n经验: +20\n体力: {result['stamina_after']}"
        if result.get("phoebe_stamina_restore", 0) > 0:
            node_text += f"\n✨ 卖萌成功！恢复 {result['phoebe_stamina_restore']} 体力"
        if result["dropped"]:
            node_text += f"\n🎁 捡到: {result['dropped_item']}"
        nodes.append({
            "type": "node",
            "data": {
                "name": result["pet_name"],
                "uin": str(bot.self_id),
                "content": str(Message(MessageSegment.text(node_text))),
            },
        })
        texts.append(node_text)

    if walk_count == 0:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text(result["message"] if result is not None else f"体力不足，无法散步（当前体力: {get_pet(user_id).stamina}，需要20）")
        ]))
        return

    # 汇总节点
    pet_after = get_pet(user_id)
    summary = (
        f"⚡ 快速散步完成！\n"
        f"🐾 共散步 {walk_count} 次\n"
        f"✨ 共获得 {total_exp} 经验\n"
        f"⚡ 剩余体力: {pet_after.stamina}/{pet_after.max_stamina}"
    )
    if total_drops:
        summary += f"\n🎁 捡到道具: {'、'.join(total_drops)}"

    if isinstance(event, GroupMessageEvent):
        nodes.insert(0, {
            "type": "node",
            "data": {
                "name": "快速散步汇总",
                "uin": str(bot.self_id),
                "content": str(Message(MessageSegment.text(summary))),
            },
        })
        await bot.send_group_forward_msg(
            group_id=event.group_id,
            messages=nodes,
        )
        await matcher.finish()
    else:
        # 私聊：合并为单条文本返回
        full = summary + "\n\n" + "\n\n".join(texts)
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text(full)
        ]))


# ========== 飞龙探云手（偷窃技能）指令 ==========
pet_steal_cmd = on_command("飞龙探云手", aliases={"宠物偷窃", "我偷"}, priority=5, block=True)


@pet_steal_cmd.handle()
async def handle_steal(matcher: Matcher, event: MessageEvent, args: Message = CommandArg()):
    """飞龙探云手：每日一次，25%概率偷取对方物品"""
    if isinstance(event, PrivateMessageEvent):
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("飞龙探云手仅在群聊可用")
        ]))
        return

    if not is_points_enabled(str(event.group_id)):
        await matcher.finish()
        return

    user_id = str(event.user_id)

    # 解析@目标
    target_id = None
    for seg in args:
        if seg.type == "at":
            target_id = seg.data.get("qq")
            break

    if not target_id:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("请使用「飞龙探云手 @某人」格式")
        ]))
        return

    target_id = str(target_id)
    if target_id == user_id:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("不能对自己使用飞龙探云手")
        ]))
        return

    result = do_steal(user_id, target_id)

    if not result["success"]:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text(result["message"])
        ]))
        return

    if result["stolen"]:
        msg = f"🤫 {result['pet_name']} 使出飞龙探云手！\n成功从 {result['target_pet_name']} 那里偷到了「{result['item_name']}」！"
    else:
        msg = f"🤫 {result['pet_name']} 使出飞龙探云手！\n{result['message']}"

    await matcher.finish(Message([
        MessageSegment.reply(event.message_id),
        MessageSegment.text(msg)
    ]))


# ========== 宠物弃养指令 ==========
pet_abandon_cmd = on_command("宠物弃养", priority=5, block=True, force_whitespace=True)
pet_abandon_confirm_cmd = on_command("确认弃养", priority=5, block=True, force_whitespace=True)


@pet_abandon_cmd.handle()
async def handle_abandon(matcher: Matcher, event: MessageEvent):
    """宠物弃养（第一步：发起确认）"""
    # 检查群聊是否开启积分系统
    if isinstance(event, GroupMessageEvent):
        if not is_points_enabled(str(event.group_id)):
            await matcher.finish()
            return

    user_id = str(event.user_id)
    pet = get_pet(user_id)
    if pet is None:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("你还没有领养宠物")
        ]))
        return

    pet_info = PET_TYPES[pet.pet_type]
    level = get_pet_level(pet.exp)

    # 标记等待确认
    _abandon_confirm[user_id] = True

    await matcher.finish(Message([
        MessageSegment.reply(event.message_id),
        MessageSegment.text(
            f"⚠️ 你确定要弃养 {pet_info['name']}（Lv.{level}）吗？\n"
            f"弃养后宠物数据将被清除，食物和配饰会保留。\n"
            f"请发送「确认弃养」确认，或忽略取消。"
        )
    ]))


@pet_abandon_confirm_cmd.handle()
async def handle_abandon_confirm(matcher: Matcher, event: MessageEvent):
    """宠物弃养（第二步：确认执行）"""
    user_id = str(event.user_id)

    # 检查是否有待确认的弃养
    if user_id not in _abandon_confirm:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("没有待确认的弃养操作，请先发送「宠物弃养」")
        ]))
        return

    # 移除确认标记
    del _abandon_confirm[user_id]

    pet = get_pet(user_id)
    if pet is None:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("你还没有领养宠物")
        ]))
        return

    pet_name = PET_TYPES[pet.pet_type]["name"]

    # 执行弃养
    abandon_pet(user_id)

    await matcher.finish(Message([
        MessageSegment.reply(event.message_id),
        MessageSegment.text(f"😢 你弃养了 {pet_name}，它独自离开了...\n发送「我的宠物」可以重新领养一只新宠物")
    ]))


# ========== 宠物改名指令 ==========
pet_rename_cmd = on_command("宠物改名", priority=5, block=True)


@pet_rename_cmd.handle()
async def handle_rename(matcher: Matcher, event: MessageEvent, args: Message = CommandArg()):
    """宠物改名（消耗500积分）"""
    # 检查群聊是否开启积分系统
    if isinstance(event, GroupMessageEvent):
        if not is_points_enabled(str(event.group_id)):
            await matcher.finish()
            return

    user_id = str(event.user_id)
    pet = get_pet(user_id)
    if pet is None:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("你还没有领养宠物，请先发送「我的宠物」领养一只")
        ]))
        return

    new_name = args.extract_plain_text().strip()
    if not new_name:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("请使用「宠物改名 新名字」格式，消耗500积分")
        ]))
        return

    if len(new_name) > 10:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("名字最长10个字")
        ]))
        return

    # 检查积分
    points_user = get_points_user(user_id)
    if points_user.points < 500:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text(f"积分不足，改名需要500积分，你只有 {points_user.points} 积分")
        ]))
        return

    # 扣除积分
    points_user.points -= 500
    save_points_user(user_id)

    old_name = pet.nickname if pet.nickname else PET_TYPES[pet.pet_type]["name"]
    pet.nickname = new_name
    save_pet(user_id)

    await matcher.finish(Message([
        MessageSegment.reply(event.message_id),
        MessageSegment.text(f"✅ 改名成功！{old_name} → {new_name}\n消耗500积分")
    ]))


# ========== 随机对手选择辅助函数 ==========

def _get_random_targets(user_id: str, n: int = 10) -> list:
    """返回最多 n 个其他养宠玩家的随机 ID 列表（打乱顺序）"""
    candidates = [u for u in get_all_pet_owners() if u != user_id]
    random.shuffle(candidates)
    return candidates[:n]


# ========== 宠物排行榜指令 ==========
pet_rank_cmd = on_command(
    "宠物排行榜",
    aliases={"宠物排行", "宠物前十", "宠物榜", "宠物榜单"},
    priority=5,
    block=True,
)


@pet_rank_cmd.handle()
async def handle_pet_rank(matcher: Matcher, event: MessageEvent, args: Message = CommandArg()):
    """宠物排行榜：展示等级/武力/幸运前十

    宠物排行榜       → 按等级排名
    宠物排行榜 武力  → 按有效武力排名
    宠物排行榜 幸运  → 按有效幸运排名
    """
    arg = args.extract_plain_text().strip()

    if "武力" in arg or "力量" in arg or "force" in arg.lower():
        mode = "force"
        title = "🏆 宠物武力排行榜 TOP10"
        unit = "武力"
        get_value = get_effective_force
    elif "幸运" in arg or "luck" in arg.lower():
        mode = "luck"
        title = "🏆 宠物幸运排行榜 TOP10"
        unit = "幸运"
        get_value = get_effective_luck
    else:
        mode = "level"
        title = "🏆 宠物等级排行榜 TOP10"
        unit = "等级"
        get_value = None

    owners = get_all_pet_owners()
    entries = []
    for uid in owners:
        pet = get_pet(uid)
        if pet is None or not pet.pet_type:
            continue
        value = get_pet_level(pet.exp) if mode == "level" else get_value(pet)
        entries.append((uid, pet, value, pet.exp))

    if not entries:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("还没有人领养宠物哦～快去「我的宠物」领养一只吧！")
        ]))
        return

    # 排序：主指标降序 → 经验降序 → QQ号升序（确定性顺序）
    entries.sort(key=lambda x: (-x[2], -x[3], x[0]))
    top = entries[:10]

    medals = ["🥇", "🥈", "🥉"]
    lines = [title, "━━━━━━━━━━"]
    for i, (uid, pet, value, _exp) in enumerate(top):
        rank = medals[i] if i < 3 else f"{i + 1}️⃣"
        name = get_display_name(pet)
        metric = f"Lv.{value}" if mode == "level" else f"{unit} {value}"
        lines.append(f"{rank} {name}（{uid}）  {metric}")
    lines.append("━━━━━━━━━━")
    lines.append(f"共 {len(entries)} 只宠物参与排名")
    lines.append("切换榜单：宠物排行榜 武力 / 宠物排行榜 幸运")

    await matcher.finish(Message([
        MessageSegment.reply(event.message_id),
        MessageSegment.text("\n".join(lines))
    ]))


# ========== 一键宠物日常指令 ==========
pet_daily_cmd = on_command(
    "宠物日常",
    aliases={"一键日常", "宠物打卡"},
    priority=5,
    block=True,
    force_whitespace=True,
)


async def _run_daily_steps(user_id: str) -> list:
    """执行日常六步（签到/抚摸/打工/散步/钓鱼/偷取），返回明细行；积分发放在内部完成"""
    # 先确保体力已按日刷新
    refresh_stamina_if_needed(user_id)

    lines = []

    # 1. 签到
    sign = do_sign(user_id)
    if sign["success"]:
        lines.append(
            f"📝 签到：+{sign['points_gained']} 积分 +{sign['exp_gained']} 经验（连续 {sign['continuous_sign_days']} 天）"
        )
    else:
        lines.append(f"📝 签到：{sign['message']}")

    # 2. 宠物抚摸
    pat = do_pat(user_id)
    if pat["success"]:
        lines.append(
            f"🤚 抚摸：好感 {pat['affection_before']} → {pat['affection_after']}（+{pat['affection_gain']}）"
        )
        if pat.get("bonus_points", 0) > 0:
            lines.append(f"🍀 祥子吃瓜：+{pat['bonus_points']} 积分")
    else:
        lines.append(f"🤚 抚摸：{pat['message']}")

    # 3. 宠物打工 x1
    work = do_work(user_id)
    if work["success"]:
        # 发放积分（do_work 只计算结果不入账，由调用方发放，与独立打工指令一致）
        points_user = get_points_user(user_id)
        points_user.points += work["points_earned"]
        save_points_user(user_id)
        extra = "，额外获得 " + "、".join(work["dropped_items"]) if work["dropped_items"] else ""
        lines.append(f"💼 打工：+{work['points_earned']} 积分{extra}")
        if work.get("bonus_points", 0) > 0:
            lines.append(f"🍀 祥子吃瓜：+{work['bonus_points']} 积分")
    else:
        lines.append(f"💼 打工：{work['message']}")

    # 4. 宠物散步 x1
    walk = do_walk(user_id)
    if walk["success"]:
        drop_text = f"，捡到 {walk['dropped_item']}" if walk["dropped"] else "，未掉落道具"
        lines.append(f"🐾 散步：{drop_text}")
        if walk.get("bonus_points", 0) > 0:
            lines.append(f"🍀 祥子吃瓜：+{walk['bonus_points']} 积分")
    else:
        lines.append(f"🐾 散步：{walk['message']}")

    # 5. 钓鱼 x1（与单钓一致，需扣 10 体力，计入每日钓鱼上限）
    pet = get_pet(user_id)
    if get_fishing_remaining(user_id) > 0 and pet.stamina >= FISHING_STAMINA_COST:
        fish = roll_fish()
        pet.stamina -= FISHING_STAMINA_COST
        save_pet(user_id)
        record_fishing(user_id)
        if fish.get("rarity") == "junk":
            lines.append(f"🎣 钓鱼：钓到了「{fish['name']}」，不值钱扔掉了（-{FISHING_STAMINA_COST} 体力）")
        else:
            add_caught_fish(user_id, fish["id"])
            lines.append(f"🎣 钓鱼：钓到 [{fish.get('name', '?')}]（{fish.get('rarity', '?')}）（-{FISHING_STAMINA_COST} 体力）")
    else:
        lines.append(f"🎣 钓鱼：今日次数已用完或体力不足（需 {FISHING_STAMINA_COST} 体力）")

    # 6. 随机偷取一个玩家
    steal_targets = _get_random_targets(user_id, n=5)
    st_text = "未找到其他玩家"
    for t in steal_targets:
        st = do_steal(user_id, t)
        if st["success"]:
            if st["stolen"]:
                st_text = f"从 {st['target_pet_name']} 处偷到「{st['item_name']}」"
            else:
                st_text = st["message"]
            break
        else:
            st_text = st["message"]
            # 每日已使用则无需再尝试其他目标
            if "今日已使用" in st["message"]:
                break
    lines.append(f"🤫 偷取：{st_text}")

    return lines


def _daily_header(user_id: str) -> str:
    """日常汇总头部（展示最终宠物状态，需在全部步骤+额外活动之后调用）"""
    pet = get_pet(user_id)
    level = get_pet_level(pet.exp)
    aff = get_affection_level(pet.affection)
    return (
        f"📋 {get_display_name(pet)} 日常完成\n"
        f"等级 Lv.{level} | 好感 Lv.{aff} | 体力 {pet.stamina}/{pet.max_stamina}\n"
        f"————————————\n"
    )


def _grind_work(user_id: str) -> dict:
    """快速打工循环（打工至体力耗尽），积分在内部发放"""
    total_points = 0
    drops = []
    count = 0
    while count < 30:
        result = do_work(user_id)
        if not result["success"]:
            break
        count += 1
        total_points += result["points_earned"]
        drops.extend(result["dropped_items"])
        points_user = get_points_user(user_id)
        points_user.points += result["points_earned"]
        save_points_user(user_id)
    return {"count": count, "total_points": total_points, "drops": drops}


def _grind_walk(user_id: str) -> dict:
    """快速散步循环（散步至体力耗尽）"""
    drops = []
    count = 0
    while count < 30:
        result = do_walk(user_id)
        if not result["success"]:
            break
        count += 1
        if result["dropped"]:
            drops.append(result["dropped_item"])
    return {"count": count, "drops": drops}


def _grind_fish(user_id: str) -> dict:
    """快速钓鱼循环（钓鱼至体力耗尽，且计入每日钓鱼上限）"""
    pet = get_pet(user_id)
    count = 0
    while pet.stamina >= FISHING_STAMINA_COST and count < DAILY_FISHING_LIMIT and get_fishing_remaining(user_id) > 0:
        fish = roll_fish()
        pet.stamina -= FISHING_STAMINA_COST
        if fish.get("rarity") != "junk":
            add_caught_fish(user_id, fish["id"])
        record_fishing(user_id)
        count += 1
    save_pet(user_id)
    return {"count": count}


def _append_grind_result(lines: list, label: str, g: dict):
    """把额外快速活动的统计追加到日常明细行"""
    lines.append("————————————")
    if label == "打工":
        if g["count"] > 0:
            lines.append(f"💼 额外快速打工 ×{g['count']}：共 +{g['total_points']} 积分")
            if g["drops"]:
                lines.append(f"🎁 掉落: {'、'.join(g['drops'])}")
        else:
            lines.append("💼 额外快速打工：体力不足，未打工")
    elif label == "散步":
        if g["count"] > 0:
            lines.append(f"🐾 额外快速散步 ×{g['count']}：共 +{g['count'] * 20} 经验")
            if g["drops"]:
                lines.append(f"🎁 捡到: {'、'.join(g['drops'])}")
        else:
            lines.append("🐾 额外快速散步：体力不足，未散步")
    else:  # 钓鱼
        if g["count"] > 0:
            lines.append(f"🎣 额外快速钓鱼 ×{g['count']}：清空剩余体力")
        else:
            lines.append("🎣 额外快速钓鱼：体力不足，未钓鱼")


@pet_daily_cmd.handle()
async def handle_pet_daily(matcher: Matcher, event: MessageEvent):
    """一键完成宠物日常：签到 → 抚摸 → 打工 → 散步 → 钓鱼 → 随机偷取"""
    # 群聊检查积分系统
    if isinstance(event, GroupMessageEvent):
        if not is_points_enabled(str(event.group_id)):
            await matcher.finish()
            return

    user_id = str(event.user_id)
    pet = get_pet(user_id)
    if pet is None:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("你还没有领养宠物，请先发送「我的宠物」领养一只")
        ]))
        return

    lines = await _run_daily_steps(user_id)
    await matcher.finish(Message([
        MessageSegment.reply(event.message_id),
        MessageSegment.text(_daily_header(user_id) + "\n".join(lines))
    ]))


# ========== 一键日常变体（日常 + 额外快速单项） ==========
# 一键日常1 = 日常 + 快速打工；一键日常2 = 日常 + 快速散步；一键日常3 = 日常 + 快速钓鱼
pet_daily1_cmd = on_command("一键日常1", aliases={"日常1"}, priority=5, block=True, force_whitespace=True)


@pet_daily1_cmd.handle()
async def handle_pet_daily1(matcher: Matcher, event: MessageEvent):
    if isinstance(event, GroupMessageEvent):
        if not is_points_enabled(str(event.group_id)):
            await matcher.finish()
            return
    user_id = str(event.user_id)
    pet = get_pet(user_id)
    if pet is None:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("你还没有领养宠物，请先发送「我的宠物」领养一只")
        ]))
        return
    lines = await _run_daily_steps(user_id)
    _append_grind_result(lines, "打工", _grind_work(user_id))
    await matcher.finish(Message([
        MessageSegment.reply(event.message_id),
        MessageSegment.text(_daily_header(user_id) + "\n".join(lines))
    ]))


pet_daily2_cmd = on_command("一键日常2", aliases={"日常2"}, priority=5, block=True, force_whitespace=True)


@pet_daily2_cmd.handle()
async def handle_pet_daily2(matcher: Matcher, event: MessageEvent):
    if isinstance(event, GroupMessageEvent):
        if not is_points_enabled(str(event.group_id)):
            await matcher.finish()
            return
    user_id = str(event.user_id)
    pet = get_pet(user_id)
    if pet is None:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("你还没有领养宠物，请先发送「我的宠物」领养一只")
        ]))
        return
    lines = await _run_daily_steps(user_id)
    _append_grind_result(lines, "散步", _grind_walk(user_id))
    await matcher.finish(Message([
        MessageSegment.reply(event.message_id),
        MessageSegment.text(_daily_header(user_id) + "\n".join(lines))
    ]))


pet_daily3_cmd = on_command("一键日常3", aliases={"日常3"}, priority=5, block=True, force_whitespace=True)


@pet_daily3_cmd.handle()
async def handle_pet_daily3(matcher: Matcher, event: MessageEvent):
    if isinstance(event, GroupMessageEvent):
        if not is_points_enabled(str(event.group_id)):
            await matcher.finish()
            return
    user_id = str(event.user_id)
    pet = get_pet(user_id)
    if pet is None:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("你还没有领养宠物，请先发送「我的宠物」领养一只")
        ]))
        return
    lines = await _run_daily_steps(user_id)
    _append_grind_result(lines, "钓鱼", _grind_fish(user_id))
    await matcher.finish(Message([
        MessageSegment.reply(event.message_id),
        MessageSegment.text(_daily_header(user_id) + "\n".join(lines))
    ]))


# ========== 一键喂养指令 ==========
pet_feed_all_cmd = on_command("一键喂养", aliases={"一键喂食", "全部喂养", "全部喂食", "吃光食物"}, priority=5, block=True)


@pet_feed_all_cmd.handle()
async def handle_feed_all(matcher: Matcher, event: MessageEvent):
    """一键喂养：把背包里所有食物全部喂给宠物"""
    # 检查群聊是否开启积分系统
    if isinstance(event, GroupMessageEvent):
        if not is_points_enabled(str(event.group_id)):
            await matcher.finish()
            return

    user_id = str(event.user_id)
    pet = get_pet(user_id)
    if pet is None:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("你还没有领养宠物，请先发送「我的宠物」领养一只")
        ]))
        return

    inv = get_inventory(user_id)
    food_items = [(name, cnt) for name, cnt in inv.foods.items() if cnt > 0]
    if not food_items:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("背包里没有任何食物可以喂~")
        ]))
        return

    # 逐个食物喂光；体力达到硬上限（9999）则停止后续喂食
    agg = {}  # food_name -> {"count", "stamina", "affection", "fav"}
    total_stamina = 0
    total_affection = 0
    failed = []
    stop = False
    penguin_triggered = False
    for name, cnt in food_items:
        if stop:
            break
        for _ in range(cnt):
            cur = get_pet(user_id)
            if cur and cur.stamina >= FEED_STAMINA_CAP:
                stop = True
                break
            result = do_feed(user_id, name)
            if not result["success"]:
                failed.append((name, result["message"]))
                stop = True
                break
            a = agg.setdefault(name, {"count": 0, "stamina": 0, "affection": 0, "fav": False})
            a["count"] += 1
            a["stamina"] += result["stamina_gain"]
            a["affection"] += result["affection_gain"]
            a["fav"] = a["fav"] or result["is_favorite"]
            total_stamina += result["stamina_gain"]
            total_affection += result["affection_gain"]
            if result.get("penguin_bonus", False):
                penguin_triggered = True

    if not agg:
        reason = failed[0][1] if failed else f"宠物体力已达上限（{FEED_STAMINA_CAP}），不需要喂食"
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text(reason)
        ]))
        return

    pet_name = get_display_name(get_pet(user_id))
    msg = f"🐾 你喂了 {pet_name}：\n"
    for name, a in agg.items():
        tag = " 💕最爱" if a["fav"] else ""
        msg += f"  {name} ×{a['count']}　+{a['stamina']}体力 +{a['affection']}好感{tag}\n"
    msg += f"——————————\n合计 体力 +{total_stamina}　好感 +{total_affection}"
    if failed:
        uniq = {}
        for fn, r in failed:
            uniq[fn] = r
        msg += "\n未喂食：" + "、".join(f"{fn}({r})" for fn, r in uniq.items())
    if penguin_triggered:
        msg += "\n🐧 咕咕嘎嘎触发！体力收益 +30%"

    await matcher.finish(Message([
        MessageSegment.reply(event.message_id),
        MessageSegment.text(msg)
    ]))


# ========== 一键出售指令 ==========
pet_sell_all_cmd = on_command("一键出售", aliases={"全部出售", "批量出售", "清仓"}, priority=5, block=True)


@pet_sell_all_cmd.handle()
async def handle_sell_all(matcher: Matcher, event: MessageEvent):
    """一键出售：出售背包里除已装备配饰和食物外的所有道具"""
    # 检查群聊是否开启积分系统
    if isinstance(event, GroupMessageEvent):
        if not is_points_enabled(str(event.group_id)):
            await matcher.finish()
            return

    user_id = str(event.user_id)
    pet = get_pet(user_id)
    if pet is None:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("你还没有领养宠物，请先发送「我的宠物」领养一只")
        ]))
        return

    inv = get_inventory(user_id)
    equipped = pet.accessory  # 已装备配饰保留不卖

    total_points = 0
    sold = []  # (name, count, total)
    # 遍历副本，避免 remove_item 删除键时影响迭代
    for name, cnt in list(inv.accessories.items()):
        if cnt <= 0 or name == equipped:
            continue
        price = ACCESSORIES[name]["price"] // 4
        total = price * cnt
        total_points += total
        remove_item(user_id, "accessory", name, cnt)
        sold.append((name, cnt, total))

    if not sold:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("没有可出售的道具（已保留食物和已装备配饰）")
        ]))
        return

    # 发放积分
    points_user = get_points_user(user_id)
    points_user.points += total_points
    save_points_user(user_id)

    msg = "🧹 一键出售完成！\n"
    for name, cnt, total in sold:
        msg += f"  {name} ×{cnt}  (+{total})\n"
    msg += f"——————————\n共获得 {total_points} 积分"

    await matcher.finish(Message([
        MessageSegment.reply(event.message_id),
        MessageSegment.text(msg)
    ]))