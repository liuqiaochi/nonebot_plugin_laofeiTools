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

    # 超级稀有额外播报
    if fish["rarity"] == "super_rare":
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
    matcher: Matcher, event: MessageEvent
):
    """钓鱼图鉴：展示所有鱼及已拥有/未拥有状态"""
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

    msg = f"🐟 钓鱼图鉴（{caught_count}/{total_count}）\n"

    # 按稀有度层级排列
    for rarity_key in ["super_rare", "rare", "common"]:
        rarity_cn = RARITY_CN_MAP[rarity_key]
        fish_list = FISH_BY_RARITY.get(rarity_key, [])

        msg += f"\n━━ {rarity_cn} ━━\n"
        for fish in fish_list:
            owned = fish["id"] in caught_ids
            status = "✅" if owned else "⬜"
            name = fish["name"]
            # 已拥有的显示售价范围
            if owned:
                price = f"{fish['min_price']}~{fish['max_price']}"
                msg += f"  {status} {name}（{price}）\n"
            else:
                msg += f"  {status} {name}（???）\n"

    await matcher.finish(Message([
        MessageSegment.reply(event.message_id),
        MessageSegment.text(msg)
    ]))


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
    rarity_order = {"super_rare": 0, "rare": 1, "common": 2}
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
            "super_rare": "✨",
            "rare": "⭐",
            "common": "",
        }.get(fish["rarity"], "")

        total_value_min += fish["min_price"] * count
        total_value_max += fish["max_price"] * count
        msg += f"  {rarity_tag} {fish['name']} ×{count}（{fish['min_price']}~{fish['max_price']}）\n"

    msg += f"\n💰 预估总价值: {total_value_min}~{total_value_max} 积分\n"
    msg += "💡 发送「钓鱼出售 鱼名」售卖\n"
    msg += "💡 发送「钓鱼出售全部 鱼名」卖出该鱼全部"

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
    """钓鱼出售：售卖鱼获得积分"""
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
            MessageSegment.text("请指定要出售的鱼名，如「钓鱼出售 鲤鱼」\n"
                                "或「钓鱼出售全部 鲤鱼」卖出全部")
        ]))
        return

    # 解析：是否"全部"出售
    sell_all = False
    fish_name = arg_text
    if arg_text.startswith("全部") or arg_text.startswith("所有"):
        sell_all = True
        fish_name = arg_text[2:].strip() if arg_text.startswith("全部") else arg_text[2:].strip()

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

    # 确定卖出数量
    sell_count = owned_count if sell_all else 1

    # 计算总售价（每条独立随机）
    total_price = sum(get_sell_price(fish) for _ in range(sell_count))

    # 执行移除
    if not remove_fish(user_id, fish_id, sell_count):
        await matcher.finish(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("出售失败，库存不足")
        ]))
        return

    # 发放积分
    points_user = get_points_user(user_id)
    points_user.points += total_price
    save_points_user(user_id)

    rarity_tag = {
        "super_rare": "✨",
        "rare": "⭐",
        "common": "",
    }.get(fish["rarity"], "")

    msg = (
        f"💰 出售成功！\n"
        f"🐟 {rarity_tag} {fish['name']} ×{sell_count}\n"
        f"💵 获得 {total_price} 积分"
    )

    await matcher.finish(Message([
        MessageSegment.reply(event.message_id),
        MessageSegment.text(msg)
    ]))
