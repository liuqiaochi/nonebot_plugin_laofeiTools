"""
宠物钓鱼系统 - 指令处理

指令：钓鱼、钓鱼图鉴、钓鱼箱、钓鱼出售
"""

import asyncio
import random

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

from ..config import is_points_enabled
from ..common.points_data import get_user as get_points_user, save_user as save_points_user
from .pet_data import get_pet, save_pet, refresh_stamina_if_needed
from .fishing_data import (
    ALL_FISH,
    FISH_BY_RARITY,
    RARITY_CN_MAP,
    FISHING_STAMINA_COST,
    add_caught_fish,
    get_fish_info,
    get_fish_by_name,
    get_caught_fish_ids,
    get_inventory,
    remove_fish,
    roll_fish,
    get_fishing_delay,
    get_sell_price,
)

# ========== 钓鱼指令 ==========

fishing_cmd = on_command(
    "钓鱼",
    priority=5,
    block=True,
    force_whitespace=True,
)


@fishing_cmd.handle()
async def handle_fishing(
    bot: Bot, matcher: Matcher, event: MessageEvent
):
    """钓鱼指令：消耗10体力，概率出鱼，稀有度越高等待越久"""
    # 仅群聊
    if isinstance(event, PrivateMessageEvent):
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("钓鱼仅在群聊可用")
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

    # 刷新体力
    refresh_stamina_if_needed(pet)
    if pet.stamina < FISHING_STAMINA_COST:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text(f"你的宠物体力不足，钓鱼需要 {FISHING_STAMINA_COST} 点体力（当前 {pet.stamina} 点）")
        ]))
        return

    # 扣除体力
    pet.stamina -= FISHING_STAMINA_COST
    save_pet(user_id)

    # 随机出鱼
    fish = roll_fish()

    # 延迟
    delay = get_fishing_delay(fish["rarity"])

    # 先回复"钓鱼中"
    await matcher.send(Message([
        MessageSegment.reply(event.message_id),
        MessageSegment.text("🎣 钓鱼中...")
    ]))

    await asyncio.sleep(delay)

    # 记录到背包
    add_caught_fish(user_id, fish["id"])

    # 构造结果消息
    rarity_tag = {
        "legendary": "💎💎💎",
        "super_rare": "✨✨✨",
        "rare": "✨✨",
        "common": "",
    }.get(fish["rarity"], "")

    price_range = f"{fish['min_price']}~{fish['max_price']}"

    msg = (
        f"🎣 钓到鱼了！{rarity_tag}\n"
        f"🐟 {fish['rarity_cn']}: {fish['name']}\n"
        f"💰 售价: {price_range} 积分\n"
        f"⚡ 剩余体力: {pet.stamina}"
    )

    # 稀世罕见额外播报
    if fish["rarity"] == "legendary":
        msg = f"💎 传说！！！\n" + msg
    elif fish["rarity"] == "super_rare":
        msg = f"🌟 运气爆棚！\n" + msg

    await matcher.finish(Message([
        MessageSegment.reply(event.message_id),
        MessageSegment.text(msg)
    ]))


# ========== 钓鱼图鉴指令 ==========

fishing_guide_cmd = on_command(
    "钓鱼图鉴",
    aliases={"鱼图鉴"},
    priority=5,
    block=True,
    force_whitespace=True,
)


@fishing_guide_cmd.handle()
async def handle_fishing_guide(
    bot: Bot, matcher: Matcher, event: MessageEvent
):
    """钓鱼图鉴：展示所有鱼及已拥有/未拥有状态（合并转发）"""
    # 仅群聊
    if isinstance(event, PrivateMessageEvent):
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("钓鱼图鉴仅在群聊可用")
        ]))
        return

    if not is_points_enabled(str(event.group_id)):
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("本群积分系统已关闭")
        ]))
        return

    user_id = str(event.user_id)
    caught_ids = set(get_caught_fish_ids(user_id))

    total_count = len(ALL_FISH)
    caught_count = len(caught_ids)

    # 构建合并转发节点：三个稀有度各一个节点
    nodes = []

    for rarity_key in ["legendary", "super_rare", "rare", "common"]:
        rarity_cn = RARITY_CN_MAP[rarity_key]
        fish_list = FISH_BY_RARITY.get(rarity_key, [])

        node_text = f"🐟 钓鱼图鉴（{caught_count}/{total_count}）\n"
        node_text += f"━━ {rarity_cn} ━━\n"
        for fish in fish_list:
            owned = fish["id"] in caught_ids
            status = "✅" if owned else "⬜"
            name = fish["name"]
            if owned:
                price = f"{fish['min_price']}~{fish['max_price']}"
                node_text += f"  {status} {name}（{price}）\n"
            else:
                node_text += f"  {status} {name}（???）\n"

        nodes.append({
            "type": "node",
            "data": {
                "name": f"钓鱼图鉴-{rarity_cn}",
                "uin": str(bot.self_id),
                "content": str(Message(MessageSegment.text(node_text))),
            },
        })

    await bot.send_group_forward_msg(
        group_id=event.group_id,
        messages=nodes,
    )

    await matcher.finish()


# ========== 钓鱼箱指令 ==========

fishing_box_cmd = on_command(
    "钓鱼箱",
    aliases={"鱼箱"},
    priority=5,
    block=True,
    force_whitespace=True,
)


@fishing_box_cmd.handle()
async def handle_fishing_box(
    matcher: Matcher, event: MessageEvent
):
    """钓鱼箱：展示已钓到的鱼和数量，提示可售卖"""
    # 仅群聊
    if isinstance(event, PrivateMessageEvent):
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("钓鱼箱仅在群聊可用")
        ]))
        return

    if not is_points_enabled(str(event.group_id)):
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("本群积分系统已关闭")
        ]))
        return

    user_id = str(event.user_id)
    inventory = get_inventory(user_id)

    if not inventory:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("你的钓鱼箱是空的，发送「钓鱼」去钓鱼吧")
        ]))
        return

    # 按稀有度+名称排序
    rarity_order = {"legendary": 0, "super_rare": 1, "rare": 2, "common": 3}
    sorted_items = sorted(
        inventory.items(),
        key=lambda x: (
            rarity_order.get(ALL_FISH.get(x[0], {}).get("rarity", "common"), 9),
            ALL_FISH.get(x[0], {}).get("name", x[0]),
        )
    )

    msg = "🎒 钓鱼箱\n"
    total_value_min = 0
    total_value_max = 0

    for fish_id, count in sorted_items:
        fish = get_fish_info(fish_id)
        if fish is None:
            continue
        rarity_tag = {
            "legendary": "💎",
            "super_rare": "✨",
            "rare": "⭐",
            "common": "",
        }.get(fish["rarity"], "")

        total_value_min += fish["min_price"] * count
        total_value_max += fish["max_price"] * count
        msg += f"  {rarity_tag} {fish['name']} ×{count}（{fish['min_price']}~{fish['max_price']}）\n"

    msg += f"\n💰 预估总价值: {total_value_min}~{total_value_max} 积分\n"
    msg += "💡 发送「钓鱼出售全部」一键出售所有鱼\n"
    msg += "💡 发送「钓鱼出售 鱼名 数量」指定数量出售\n"
    msg += "💡 发送「钓鱼出售 鱼名1 鱼名2」多品种各卖1条"

    await matcher.finish(Message([
        MessageSegment.reply(event.message_id),
        MessageSegment.text(msg)
    ]))


# ========== 钓鱼出售指令 ==========

fishing_sell_cmd = on_command(
    "钓鱼出售",
    aliases={"卖鱼"},
    priority=5,
    block=True,
    force_whitespace=True,
)


@fishing_sell_cmd.handle()
async def handle_fishing_sell(
    matcher: Matcher, event: MessageEvent, args: Message = CommandArg()
):
    """钓鱼出售：售卖鱼获得积分

    支持四种模式：
    1. 钓鱼出售全部              → 卖出鱼箱所有鱼
    2. 钓鱼出售 全部 <鱼名>      → 卖出该鱼全部（兼容旧版）
    3. 钓鱼出售 <鱼名> <数量>    → 指定数量出售
    4. 钓鱼出售 <鱼名1> <鱼名2>  → 多品种各卖1条
    """
    # 仅群聊
    if isinstance(event, PrivateMessageEvent):
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("钓鱼出售仅在群聊可用")
        ]))
        return

    if not is_points_enabled(str(event.group_id)):
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("本群积分系统已关闭")
        ]))
        return

    user_id = str(event.user_id)
    arg_text = args.extract_plain_text().strip()

    if not arg_text:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text(
                "请指定要出售的鱼名，支持以下方式：\n"
                "· 钓鱼出售全部 — 出售鱼箱所有鱼\n"
                "· 钓鱼出售 鲤鱼 5 — 出售指定数量\n"
                "· 钓鱼出售 鲤鱼 鲫鱼 — 多品种各卖1条\n"
                "· 钓鱼出售 全部 鲤鱼 — 卖出该鱼全部"
            )
        ]))
        return

    rarity_tag_map = {
        "legendary": "💎",
        "super_rare": "✨",
        "rare": "⭐",
        "common": "",
    }

    # ─── 模式1：钓鱼出售全部 → 卖出鱼箱所有鱼 ───
    if arg_text == "全部":
        inventory = get_inventory(user_id)
        if not inventory:
            await matcher.finish(Message([
                MessageSegment.reply(event.message_id),
                MessageSegment.text("你的钓鱼箱是空的，没有鱼可以出售")
            ]))
            return

        total_count = 0
        total_price = 0
        detail_lines = []

        for fish_id, count in sorted(inventory.items(), key=lambda x: x[0]):
            fish = get_fish_info(fish_id)
            if fish is None:
                continue
            sell_price = sum(get_sell_price(fish) for _ in range(count))
            if not remove_fish(user_id, fish_id, count):
                continue
            total_count += count
            total_price += sell_price
            tag = rarity_tag_map.get(fish["rarity"], "")
            detail_lines.append(f"  {tag} {fish['name']} ×{count}  (+{sell_price})")

        if total_count == 0:
            await matcher.finish(Message([
                MessageSegment.reply(event.message_id),
                MessageSegment.text("出售失败，请重试")
            ]))
            return

        # 发放积分
        points_user = get_points_user(user_id)
        points_user.points += total_price
        save_points_user(user_id)

        msg = (
            f"💰 全部出售成功！共 {total_count} 条鱼\n"
            + "\n".join(detail_lines)
            + f"\n💵 合计获得 {total_price} 积分"
        )
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text(msg)
        ]))
        return

    # ─── 模式2：钓鱼出售 全部 <鱼名> → 卖出该鱼全部（兼容旧版） ───
    if arg_text.startswith("全部") and len(arg_text) > 2:
        fish_name = arg_text[2:].strip()
        if not fish_name:
            await matcher.finish(Message([
                MessageSegment.reply(event.message_id),
                MessageSegment.text("请指定鱼名，如「钓鱼出售 全部 鲤鱼」")
            ]))
            return

        fish = get_fish_by_name(fish_name)
        if fish is None:
            await matcher.finish(Message([
                MessageSegment.reply(event.message_id),
                MessageSegment.text(f"没有找到「{fish_name}」这种鱼")
            ]))
            return

        fish_id = fish["id"]
        inventory = get_inventory(user_id)
        owned_count = inventory.get(fish_id, 0)
        if owned_count <= 0:
            await matcher.finish(Message([
                MessageSegment.reply(event.message_id),
                MessageSegment.text(f"你的钓鱼箱里没有「{fish['name']}」")
            ]))
            return

        total_price = sum(get_sell_price(fish) for _ in range(owned_count))
        if not remove_fish(user_id, fish_id, owned_count):
            await matcher.finish(Message([
                MessageSegment.reply(event.message_id),
                MessageSegment.text("出售失败，库存不足")
            ]))
            return

        points_user = get_points_user(user_id)
        points_user.points += total_price
        save_points_user(user_id)

        tag = rarity_tag_map.get(fish["rarity"], "")
        msg = (
            f"💰 出售成功！\n"
            f"🐟 {tag} {fish['name']} ×{owned_count}\n"
            f"💵 获得 {total_price} 积分"
        )
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text(msg)
        ]))
        return

    # ─── Tokenize 参数 ───
    tokens = arg_text.split()

    # ─── 模式3：钓鱼出售 <鱼名> <数量> ───
    if len(tokens) >= 2 and tokens[-1].isdigit():
        quantity = int(tokens[-1])
        fish_name = " ".join(tokens[:-1])
        if quantity <= 0:
            await matcher.finish(Message([
                MessageSegment.reply(event.message_id),
                MessageSegment.text("出售数量必须大于 0")
            ]))
            return

        fish = get_fish_by_name(fish_name)
        if fish is None:
            await matcher.finish(Message([
                MessageSegment.reply(event.message_id),
                MessageSegment.text(f"没有找到「{fish_name}」这种鱼")
            ]))
            return

        fish_id = fish["id"]
        inventory = get_inventory(user_id)
        owned_count = inventory.get(fish_id, 0)
        if owned_count <= 0:
            await matcher.finish(Message([
                MessageSegment.reply(event.message_id),
                MessageSegment.text(f"你的钓鱼箱里没有「{fish['name']}」")
            ]))
            return
        if owned_count < quantity:
            await matcher.finish(Message([
                MessageSegment.reply(event.message_id),
                MessageSegment.text(f"你的钓鱼箱里「{fish['name']}」只有 {owned_count} 条，无法出售 {quantity} 条")
            ]))
            return

        total_price = sum(get_sell_price(fish) for _ in range(quantity))
        if not remove_fish(user_id, fish_id, quantity):
            await matcher.finish(Message([
                MessageSegment.reply(event.message_id),
                MessageSegment.text("出售失败，库存不足")
            ]))
            return

        points_user = get_points_user(user_id)
        points_user.points += total_price
        save_points_user(user_id)

        tag = rarity_tag_map.get(fish["rarity"], "")
        msg = (
            f"💰 出售成功！\n"
            f"🐟 {tag} {fish['name']} ×{quantity}\n"
            f"💵 获得 {total_price} 积分"
        )
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text(msg)
        ]))
        return

    # ─── 模式4：钓鱼出售 <鱼名1> <鱼名2> ... → 多品种各卖1条 ───
    inventory = get_inventory(user_id)
    success_items = []
    fail_items = []
    total_price = 0

    for name in tokens:
        fish = get_fish_by_name(name)
        if fish is None:
            fail_items.append(f"❌ 没有找到「{name}」")
            continue

        fish_id = fish["id"]
        owned_count = inventory.get(fish_id, 0)
        if owned_count <= 0:
            fail_items.append(f"❌ 鱼箱里没有「{fish['name']}」")
            continue

        price = get_sell_price(fish)
        if not remove_fish(user_id, fish_id, 1):
            fail_items.append(f"❌ 出售「{fish['name']}」失败")
            continue

        total_price += price
        inventory[fish_id] = owned_count - 1  # 更新本地计数
        tag = rarity_tag_map.get(fish["rarity"], "")
        success_items.append(f"  {tag} {fish['name']} (+{price})")

    if not success_items:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("没有成功出售任何鱼\n" + "\n".join(fail_items))
        ]))
        return

    # 发放积分
    if total_price > 0:
        points_user = get_points_user(user_id)
        points_user.points += total_price
        save_points_user(user_id)

    msg = f"💰 出售成功！共 {len(success_items)} 种\n" + "\n".join(success_items)
    if fail_items:
        msg += "\n" + "\n".join(fail_items)
    msg += f"\n💵 合计获得 {total_price} 积分"

    await matcher.finish(Message([
        MessageSegment.reply(event.message_id),
        MessageSegment.text(msg)
    ]))


# ========== 钓鱼帮助指令 ==========

fishing_help_cmd = on_command(
    "钓鱼帮助",
    aliases={"鱼帮助"},
    priority=5,
    block=True,
    force_whitespace=True,
)


@fishing_help_cmd.handle()
async def handle_fishing_help(matcher: Matcher, event: MessageEvent):
    """钓鱼帮助：展示所有钓鱼相关指令（图片版）"""
    help_b64 = None
    try:
        from .shop_image import generate_fishing_help_image
        help_b64 = generate_fishing_help_image()
    except Exception as e:
        logger.error(f"生成钓鱼帮助图片失败: {e}")

    if help_b64:
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.image(f"base64://{help_b64}"),
        ]))
    else:
        # 降级为文字版
        msg = (
            "🎣 宠物钓鱼系统\n"
            "━━━━━━━━━━━━━━━\n"
            "🐟 钓鱼     — 消耗10体力抛竿钓鱼，稀有度越高等待越久\n"
            "📖 钓鱼图鉴 — 查看全部36种鱼的收集进度\n"
            "   别名：鱼图鉴\n"
            "🎒 钓鱼箱   — 查看已钓到的鱼，可进行售卖\n"
            "   别名：鱼箱\n"
            "💰 钓鱼出售全部 — 一键出售鱼箱所有鱼\n"
            "💰 钓鱼出售 <鱼名> <数量> — 指定数量出售\n"
            "💰 钓鱼出售 <鱼名1> <鱼名2> — 多品种各卖1条\n"
            "   别名：卖鱼\n"
            "\n"
            "稀有度：💎稀世罕见(1%) ✨超级稀有(4%) ⭐稀有(20%) 普通(75%)"
        )
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text(msg)
        ]))
