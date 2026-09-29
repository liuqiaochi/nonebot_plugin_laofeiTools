"""
功能模块独立开关

- 统一数据源在 config.py（module_switches.json，单一数据源）
- 本文件负责：
  1. 全局守卫 matcher：群聊中拦截「已关闭模块」的使用类命令（私聊放行）
  2. 超管指令「开启 / 关闭 <模块名>」（含依赖链提示）
  3. 「功能开关」指令：以图片返回本群各模块开关状态（✅/❌）
"""

import base64
from io import BytesIO

from nonebot import on_message, on_command
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageEvent, MessageSegment
from nonebot.matcher import Matcher
from nonebot.permission import SUPERUSER
from nonebot.rule import Rule

from PIL import Image, ImageDraw, ImageFont

from ..config import (
    FEATURE_MODULES,
    is_module_enabled,
    set_module_enabled,
    resolve_module_key,
    get_module_switches,
)


# ========== 工具 ==========

def _plain_text(event) -> str:
    """兼容旧版 nonebot（缺 get_plaintext）的文本提取"""
    try:
        return event.get_plaintext().strip()
    except AttributeError:
        return "".join(
            seg.data.get("text", "") for seg in event.message if seg.type == "text"
        ).strip()


def _try_load_font(size: int):
    """尝试加载中文字体，失败回退默认"""
    font_paths = [
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
    ]
    for fp in font_paths:
        try:
            return ImageFont.truetype(fp, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


# ========== 使用类命令词 -> 模块（不含超管开关/设置/额度类） ==========
# 守卫仅拦截「使用功能」的命令；开启/关闭/设置/额度等管理命令不拦。

MODULE_USE_COMMANDS = {
    # 积分系统
    "签到": "points", "打卡": "points", "积分签到": "points", "日常签到": "points",
    "积分": "points", "查积分": "points", "我的积分": "points",
    "抽签": "points", "今日运气": "points", "今日抽签": "points", "今日气运": "points",
    "新手大礼包": "points", "领取新手大礼包": "points",
    "转账": "points", "转积分": "points", "给积分": "points",
    "积分排行": "points", "排行榜": "points", "今日排行": "points",
    # 宠物系统
    "我的宠物": "pet", "宠物": "pet", "领养": "pet", "宠物领养": "pet", "领养宠物": "pet",
    "宠物帮助": "pet", "宠物背包": "pet",
    "宠物散步": "pet", "宠物溜达": "pet", "宠物出门": "pet",
    "宠物抚摸": "pet", "摸宠物": "pet", "疼爱宠物": "pet",
    "宠物喂食": "pet", "宠物喂养": "pet", "喂养宠物": "pet",
    "宠物pk": "pet", "宠物PK": "pet", "宠物Pk": "pet", "宠物pK": "pet",
    "宠物商店": "pet", "宠物店": "pet", "宠物店铺": "pet",
    "购买": "pet", "宠物佩戴": "pet", "宠物穿戴": "pet", "宠物装备": "pet",
    "使用配饰": "pet", "穿配饰": "pet", "出售": "pet",
    "宠物打工": "pet", "快速打工": "pet", "连续打工": "pet", "一键打工": "pet",
    "快速散步": "pet", "连续散步": "pet", "一键散步": "pet",
    "飞龙探云手": "pet", "宠物偷窃": "pet", "我偷": "pet",
    "宠物弃养": "pet", "确认弃养": "pet", "宠物改名": "pet",
    "宠物排行": "pet", "宠物前十": "pet", "宠物榜": "pet", "宠物榜单": "pet",
    "宠物日常": "pet", "一键日常": "pet", "宠物打卡": "pet",
    "一键日常1": "pet", "日常1": "pet", "一键日常2": "pet", "日常2": "pet",
    "一键日常3": "pet", "日常3": "pet",
    "一键喂养": "pet", "一键喂食": "pet", "全部喂养": "pet", "全部喂食": "pet", "吃光食物": "pet",
    "一键出售": "pet", "全部出售": "pet", "批量出售": "pet", "清仓": "pet",
    # 钓鱼系统
    "钓鱼": "fishing", "快速钓鱼": "fishing", "连续钓鱼": "fishing", "一键钓鱼": "fishing",
    "钓鱼图鉴": "fishing", "鱼图鉴": "fishing",
    "钓鱼箱": "fishing", "鱼箱": "fishing",
    "钓鱼出售": "fishing", "卖鱼": "fishing", "钓鱼出售全部": "fishing",
    "钓鱼帮助": "fishing", "鱼帮助": "fishing",
    # 搜图功能（仅使用类）
    "lg搜图": "search",
    # AI 画图（不含开启/关闭/模型/额度管理）
    "ai画图": "ai_draw", "ai生图": "ai_draw", "ai绘画": "ai_draw", "ai绘图": "ai_draw",
    "ai画图帮助": "ai_draw", "ai生图帮助": "ai_draw", "ai绘画帮助": "ai_draw", "ai绘图帮助": "ai_draw",
    "ai画图设置": "ai_draw", "ai生图设置": "ai_draw", "ai绘画设置": "ai_draw", "ai绘图设置": "ai_draw",
    "ai画图重置设置": "ai_draw", "ai生图重置设置": "ai_draw",
    "ai绘画重置设置": "ai_draw", "ai绘图重置设置": "ai_draw",
    # 二维码工具
    "二维码识别": "qrcode", "识别二维码": "qrcode", "读二维码": "qrcode", "二维码读取": "qrcode",
    "生成二维码": "qrcode", "二维码生成": "qrcode", "做二维码": "qrcode", "创建二维码": "qrcode",
    # 生活工具（不含 lg公告 超管管理）
    "lg天气": "life", "天气": "life", "天气查询": "life", "lg weather": "life",
    "lg换算": "life", "汇率": "life", "汇率换算": "life", "lg exchange": "life",
}


# ========== 全局守卫 ==========

async def _guard_rule(event: MessageEvent) -> bool:
    """仅当「群聊 + 命中模块使用命令 + 该模块当前关闭」时才触发守卫。

    这样 block=True 只在真正需要拦截时生效，已开启模块 / 普通聊天不受影响。
    """
    if not isinstance(event, GroupMessageEvent):
        return False
    text = _plain_text(event)
    if not text:
        return False
    cmd = text.split()[0] if text.split() else text
    module = MODULE_USE_COMMANDS.get(cmd)
    if not module:
        return False
    return not is_module_enabled(str(event.group_id), module)


module_switch_guard = on_message(rule=Rule(_guard_rule), priority=1, block=True)


@module_switch_guard.handle()
async def _handle_guard(event: MessageEvent):
    cmd = _plain_text(event).split()[0]
    module = MODULE_USE_COMMANDS.get(cmd)
    name = FEATURE_MODULES[module]["name"]
    await module_switch_guard.finish(Message([
        MessageSegment.reply(event.message_id),
        MessageSegment.text(
            f"⚠️ 本群未开启{name}，超管发送「开启 {name}」即可启用"
        ),
    ]))


# ========== 开启 / 关闭 模块 ==========

_enable_module_cmd = on_command(
    "开启", aliases={"打开", "启用"}, permission=SUPERUSER,
    priority=5, block=True, force_whitespace=True,
)
_disable_module_cmd = on_command(
    "关闭", aliases={"关掉", "禁用"}, permission=SUPERUSER,
    priority=5, block=True, force_whitespace=True,
)


def _parse_module_name(raw: str, op: str) -> str:
    """从「开启/关闭 <模块名>」中解析模块名（去除操作前缀与空白）"""
    name = raw.strip()
    for p in ("开启", "打开", "启用", "关闭", "关掉", "禁用"):
        if name.startswith(p):
            name = name[len(p):].strip()
            break
    return name


@_enable_module_cmd.handle()
async def _handle_enable(event: MessageEvent, matcher: Matcher):
    raw = _plain_text(event)
    name = _parse_module_name(raw, "开启")
    key = resolve_module_key(name)
    if not key:
        available = "、".join(m["name"] for m in FEATURE_MODULES.values())
        await matcher.finish(Message([
            MessageSegment.text(f"❓ 未知模块「{name}」，可用：{available}")
        ]))
    ok, msg = set_module_enabled(str(event.group_id), key, True)
    await matcher.finish(Message([MessageSegment.text(msg)]))


@_disable_module_cmd.handle()
async def _handle_disable(event: MessageEvent, matcher: Matcher):
    raw = _plain_text(event)
    name = _parse_module_name(raw, "关闭")
    key = resolve_module_key(name)
    if not key:
        available = "、".join(m["name"] for m in FEATURE_MODULES.values())
        await matcher.finish(Message([
            MessageSegment.text(f"❓ 未知模块「{name}」，可用：{available}")
        ]))
    ok, msg = set_module_enabled(str(event.group_id), key, False)
    await matcher.finish(Message([MessageSegment.text(msg)]))


# ========== 功能开关列表（图片） ==========

_module_list_cmd = on_command(
    "功能开关", aliases={"模块开关", "功能列表"},
    priority=5, block=True, force_whitespace=True,
)


@_module_list_cmd.handle()
async def _handle_module_list(event: MessageEvent, matcher: Matcher):
    if not isinstance(event, GroupMessageEvent):
        await matcher.finish(Message([
            MessageSegment.text("⚠️ 功能开关为群聊设置，请在群聊中发送此指令查看")
        ]))
        return
    img_b64 = generate_module_switch_image(str(event.group_id))
    await matcher.finish(MessageSegment.image(f"base64://{img_b64}"))


def _draw_status_icon(draw, cx: int, cy: int, enabled: bool, r: int = 11):
    """矢量绘制状态图标：绿圆白勾（开） / 红圆白叉（关）。

    不用 emoji 字符——中文字体普遍缺 emoji 字形，会渲染成方块。
    """
    if enabled:
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(86, 196, 122))
        draw.line([(cx - 5, cy + 0), (cx - 1, cy + 5), (cx + 5, cy - 5)],
                  fill=(255, 255, 255), width=3, joint="curve")
    else:
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(214, 92, 92))
        draw.line([(cx - 4, cy - 4), (cx + 4, cy + 4)], fill=(255, 255, 255), width=3)
        draw.line([(cx - 4, cy + 4), (cx + 4, cy - 4)], fill=(255, 255, 255), width=3)


def generate_module_switch_image(group_id: str) -> str:
    """生成本群功能开关状态图片（勾=可用 / 叉=不可用）"""
    font_title = _try_load_font(26)
    font_name = _try_load_font(20)
    font_sub = _try_load_font(13)

    switches = get_module_switches(group_id)
    order = ["points", "pet", "fishing", "qrcode", "life", "search", "ai_chat", "ai_draw"]

    width = 480
    row_h = 62
    header_h = 72
    padding = 26
    footer_h = 44
    total_height = padding + header_h + len(order) * row_h + footer_h

    img = Image.new("RGB", (width, total_height), (45, 45, 55))
    draw = ImageDraw.Draw(img)

    # 标题：左侧金色竖条 + 文字
    draw.rounded_rectangle(
        [padding, padding + 3, padding + 5, padding + 31], radius=2, fill=(255, 200, 100)
    )
    draw.text((padding + 18, padding), "本群功能开关", fill=(255, 200, 100), font=font_title)
    draw.line([(padding, padding + header_h - 14), (width - padding, padding + header_h - 14)],
              fill=(80, 80, 95), width=1)

    for i, key in enumerate(order):
        meta = FEATURE_MODULES[key]
        eff = is_module_enabled(group_id, key)        # 级联推导后的真实可用状态
        self_on = switches.get(key, meta["default"])  # 自身开关（未含依赖）
        row_top = padding + header_h + i * row_h

        if i % 2 == 1:  # 隔行浅底，提升可读性
            draw.rounded_rectangle(
                [padding - 8, row_top + 2, width - padding + 8, row_top + row_h - 6],
                radius=6, fill=(52, 52, 64)
            )

        _draw_status_icon(draw, padding + 12, row_top + 22, eff)
        draw.text((padding + 38, row_top + 10), meta["name"], fill=(245, 245, 250), font=font_name)

        # 副行说明：默认状态 · 依赖 · 不可用原因
        sub = ["默认开" if meta["default"] else "默认关"]
        if meta["requires"]:
            sub.append(f"依赖{FEATURE_MODULES[meta['requires']]['name']}")
        if not self_on:
            # 默认开却自身关 => 必是本群手动关；默认关则本就是默认态
            sub.append("（本群已关闭）" if meta["default"] else "（默认未开启）")
        elif not meta["default"]:
            sub.append("（本群已开启）")
        elif not eff:
            sub.append("（依赖未开启，暂不可用）")
        draw.text((padding + 40, row_top + 37), "  ·  ".join(sub),
                  fill=(158, 158, 178), font=font_sub)

    tip = "超管发送「开启 / 关闭 模块名」调整"
    bbox = draw.textbbox((0, 0), tip, font=font_sub)
    tip_w = bbox[2] - bbox[0]
    draw.text(((width - tip_w) // 2, total_height - padding - 4), tip,
              fill=(136, 136, 158), font=font_sub)

    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return base64.b64encode(buf.getvalue()).decode()
