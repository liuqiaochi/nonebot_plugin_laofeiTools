"""
宠物系统指令处理

功能：宠物领养、散步、抚摸、喂食、PK、商店、配饰、背包
"""

from pathlib import Path

from nonebot import on_command, logger
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

from .config import is_points_enabled
from .points_data import get_user as get_points_user, save_user as save_points_user
from .pet_data import (
    PET_TYPES, FOODS, ACCESSORIES, AFFECTION_LEVELS,
    get_pet, create_pet, save_pet,
    get_pet_level, get_affection_level, get_effective_force, get_effective_luck,
    get_inventory, add_item, remove_item, save_inventory,
    equip_accessory, unequip_accessory,
    do_walk, do_pat, do_feed, do_pk,
    refresh_stamina_if_needed,
)

# 宠物图片目录
PET_IMAGE_DIR = Path(__file__).parent / "image"


# ========== 我的宠物指令 ==========
my_pet_cmd = on_command("我的宠物", priority=5, block=True, force_whitespace=True)


@my_pet_cmd.handle()
async def handle_my_pet(matcher: Matcher, event: MessageEvent):
    """查看宠物信息 / 未领养时展示领养列表"""
    # 检查私聊
    if isinstance(event, PrivateMessageEvent):
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("宠物功能仅在群聊可用")
        ]))
        return

    # 检查群聊是否开启积分系统
    if not is_points_enabled(str(event.group_id)):
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("本群积分系统已关闭")
        ]))
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

    msg = f"🐾 {pet_info['name']}\n"

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
adopt_cmd = on_command("领养", priority=5, block=True)


@adopt_cmd.handle()
async def handle_adopt(matcher: Matcher, event: MessageEvent, args: Message = CommandArg()):
    """领养宠物"""
    # 检查私聊
    if isinstance(event, PrivateMessageEvent):
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("宠物功能仅在群聊可用")
        ]))
        return

    # 检查群聊是否开启积分系统
    if not is_points_enabled(str(event.group_id)):
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("本群积分系统已关闭")
        ]))
        return

    user_id = str(event.user_id)

    # 检查是否已有宠物
    if get_pet(user_id) is not None:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("你已经有宠物了")
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
宠物抚摸 - 每日抚摸提升好感度
宠物喂食 食物名 - 喂食恢复体力和好感度
宠物pk @某人 - 与他人宠物PK对战
宠物商店 - 查看商店商品
购买 商品名 - 使用积分购买商品
宠物佩戴 配饰名 - 佩戴配饰
宠物背包 - 查看道具背包
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
    if isinstance(event, PrivateMessageEvent):
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("宠物功能仅在群聊可用")
        ]))
        return

    if not is_points_enabled(str(event.group_id)):
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("本群积分系统已关闭")
        ]))
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
pet_walk_cmd = on_command("宠物散步", priority=5, block=True, force_whitespace=True)


@pet_walk_cmd.handle()
async def handle_walk(matcher: Matcher, event: MessageEvent):
    """宠物散步"""
    if isinstance(event, PrivateMessageEvent):
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("宠物功能仅在群聊可用")
        ]))
        return

    if not is_points_enabled(str(event.group_id)):
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("本群积分系统已关闭")
        ]))
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
    if result["dropped"]:
        msg += f"🎁 捡到了 {result['dropped_item']}！"
    else:
        msg += result["message"]

    await matcher.finish(Message([
        MessageSegment.reply(event.message_id),
        MessageSegment.text(msg)
    ]))


# ========== 宠物抚摸指令 ==========
pet_pat_cmd = on_command("宠物抚摸", priority=5, block=True, force_whitespace=True)


@pet_pat_cmd.handle()
async def handle_pat(matcher: Matcher, event: MessageEvent):
    """抚摸宠物"""
    if isinstance(event, PrivateMessageEvent):
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("宠物功能仅在群聊可用")
        ]))
        return

    if not is_points_enabled(str(event.group_id)):
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("本群积分系统已关闭")
        ]))
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

    await matcher.finish(Message([
        MessageSegment.reply(event.message_id),
        MessageSegment.text(msg)
    ]))


# ========== 宠物喂食指令 ==========
pet_feed_cmd = on_command("宠物喂食", priority=5, block=True)


@pet_feed_cmd.handle()
async def handle_feed(matcher: Matcher, event: MessageEvent, args: Message = CommandArg()):
    """喂食宠物"""
    if isinstance(event, PrivateMessageEvent):
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("宠物功能仅在群聊可用")
        ]))
        return

    if not is_points_enabled(str(event.group_id)):
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("本群积分系统已关闭")
        ]))
        return

    user_id = str(event.user_id)
    pet = get_pet(user_id)
    if pet is None:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("你还没有领养宠物，请先发送「我的宠物」领养一只")
        ]))
        return

    food_name = args.extract_plain_text().strip()
    if not food_name:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("请使用「宠物喂食 食物名」格式，如：宠物喂食 橘子")
        ]))
        return

    result = do_feed(user_id, food_name)

    if not result["success"]:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text(result["message"])
        ]))
        return

    msg = f"🐾 你喂了 {result['pet_name']} 一个 {result['food_name']}~\n"
    msg += f"体力: {result['stamina_before']} → {result['stamina_after']}（+{result['stamina_gain']}）\n"
    msg += f"好感度: {result['affection_before']} → {result['affection_after']}（+{result['affection_gain']}）"
    if result["is_favorite"]:
        msg += f"\n💕 {result['pet_name']} 最爱吃 {result['food_name']}！"

    await matcher.finish(Message([
        MessageSegment.reply(event.message_id),
        MessageSegment.text(msg)
    ]))


# ========== 宠物PK指令 ==========
pet_pk_cmd = on_command("宠物pk", aliases={"宠物PK"}, priority=5, block=True)


@pet_pk_cmd.handle()
async def handle_pk(matcher: Matcher, event: MessageEvent, args: Message = CommandArg()):
    """宠物PK对战"""
    if isinstance(event, PrivateMessageEvent):
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("宠物功能仅在群聊可用")
        ]))
        return

    if not is_points_enabled(str(event.group_id)):
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("本群积分系统已关闭")
        ]))
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

    msg = f"⚔️ 宠物PK\n"
    msg += f"🔴 {result['attacker_name']} 体力-20\n"
    msg += f"🔵 {result['defender_name']} 体力-10\n"
    if result["attacker_won"]:
        msg += f"🎉 {result['attacker_name']} 获胜！\n"
    else:
        msg += f"😢 {result['defender_name']} 获胜！\n"
    msg += f"🎁 胜者奖励: {result['reward_food']}"

    await matcher.finish(Message([
        MessageSegment.reply(event.message_id),
        MessageSegment.text(msg)
    ]))


# ========== 宠物商店指令 ==========
pet_shop_cmd = on_command("宠物商店", priority=5, block=True, force_whitespace=True)


@pet_shop_cmd.handle()
async def handle_shop(matcher: Matcher, event: MessageEvent):
    """查看宠物商店"""
    if isinstance(event, PrivateMessageEvent):
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("宠物功能仅在群聊可用")
        ]))
        return

    if not is_points_enabled(str(event.group_id)):
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("本群积分系统已关闭")
        ]))
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
            msg += f"  {food_name} - {food_info['price']} 积分\n"

        msg += "\n【普通配饰】\n"
        for acc_name, acc_info in ACCESSORIES.items():
            if acc_info["droppable"]:
                effects = []
                if acc_info["force"] > 0:
                    effects.append(f"武力+{acc_info['force']}")
                if acc_info["luck"] > 0:
                    effects.append(f"幸运+{acc_info['luck']}")
                if acc_info["stamina"] > 0:
                    effects.append(f"体力+{acc_info['stamina']}")
                if acc_info["special"] == "pat_bonus_10":
                    effects.append("抚摸好感+10")
                effect_str = "、".join(effects) if effects else "无"
                msg += f"  {acc_name}（{effect_str}）- {acc_info['price']} 积分\n"

        msg += "\n【特殊配饰】\n"
        for acc_name, acc_info in ACCESSORIES.items():
            if not acc_info["droppable"]:
                effects = []
                if acc_info["force"] > 0:
                    effects.append(f"武力+{acc_info['force']}")
                if acc_info["luck"] > 0:
                    effects.append(f"幸运+{acc_info['luck']}")
                if acc_info["stamina"] > 0:
                    effects.append(f"体力+{acc_info['stamina']}")
                if acc_info["special"] == "affection_1.2x":
                    effects.append("好感提升1.2倍")
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
    if isinstance(event, PrivateMessageEvent):
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("宠物功能仅在群聊可用")
        ]))
        return

    if not is_points_enabled(str(event.group_id)):
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("本群积分系统已关闭")
        ]))
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
            MessageSegment.text("请使用「购买 商品名」格式，如：购买 小刀")
        ]))
        return

    # 查找商品及价格
    item_type = None
    price = 0
    if item_name in FOODS:
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

    # 检查积分
    points_user = get_points_user(user_id)
    if points_user.points < price:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text(f"积分不足，{item_name} 需要 {price} 积分，你只有 {points_user.points} 积分")
        ]))
        return

    # 扣除积分
    points_user.points -= price
    save_points_user(user_id)

    # 添加到背包
    add_item(user_id, item_type, item_name)

    msg = f"✅ 成功购买 {item_name}！\n"
    msg += f"消耗 {price} 积分，剩余 {points_user.points} 积分"

    await matcher.finish(Message([
        MessageSegment.reply(event.message_id),
        MessageSegment.text(msg)
    ]))


# ========== 宠物佩戴指令 ==========
pet_equip_cmd = on_command("宠物佩戴", priority=5, block=True)


@pet_equip_cmd.handle()
async def handle_equip(matcher: Matcher, event: MessageEvent, args: Message = CommandArg()):
    """佩戴配饰"""
    if isinstance(event, PrivateMessageEvent):
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("宠物功能仅在群聊可用")
        ]))
        return

    if not is_points_enabled(str(event.group_id)):
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("本群积分系统已关闭")
        ]))
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
    if isinstance(event, PrivateMessageEvent):
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("宠物功能仅在群聊可用")
        ]))
        return

    if not is_points_enabled(str(event.group_id)):
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("本群积分系统已关闭")
        ]))
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
            MessageSegment.text("请使用「出售 物品名」格式，如：出售 小刀")
        ]))
        return

    item_name = args_text
    inv = get_inventory(user_id)

    # 查找物品和价格
    sell_price = 0
    item_type = ""

    if item_name in FOODS and inv.foods.get(item_name, 0) > 0:
        item_type = "food"
        sell_price = FOODS[item_name]["price"] // 4
    elif item_name in ACCESSORIES and inv.accessories.get(item_name, 0) > 0:
        # 不能出售正在佩戴的配饰
        if pet.accessory == item_name:
            await matcher.finish(Message([
                MessageSegment.reply(event.message_id),
                MessageSegment.text(f"请先卸下「{item_name}」再出售")
            ]))
            return
        item_type = "accessory"
        sell_price = ACCESSORIES[item_name]["price"] // 4
    else:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text(f"背包中没有「{item_name}」")
        ]))
        return

    # 移除物品
    remove_item(user_id, item_type, item_name)

    # 增加积分
    points_user = get_points_user(user_id)
    points_user.points += sell_price
    save_points_user(user_id)

    await matcher.finish(Message([
        MessageSegment.reply(event.message_id),
        MessageSegment.text(f"成功出售「{item_name}」，获得 {sell_price} 积分")
    ]))