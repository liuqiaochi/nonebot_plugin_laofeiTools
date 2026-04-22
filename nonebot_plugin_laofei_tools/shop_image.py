"""
商店图片生成

生成宠物商店的展示图片，包含食物和配饰的图片、名称、价格、属性
"""

import base64
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .pet_data import FOODS, ACCESSORIES

# 图片目录
IMAGE_DIR = Path(__file__).parent / "image"

# 颜色定义
BG_COLOR = (45, 45, 55)
CARD_COLOR = (60, 60, 75)
CARD_FOOD_COLOR = (55, 70, 60)
CARD_ACC_COLOR = (55, 55, 75)
CARD_SPECIAL_COLOR = (75, 55, 65)
TEXT_COLOR = (255, 255, 255)
PRICE_COLOR = (255, 215, 0)
EFFECT_COLOR = (150, 220, 255)
TITLE_COLOR = (255, 200, 100)
DIVIDER_COLOR = (80, 80, 95)

# 布局常量
CARD_WIDTH = 160
CARD_HEIGHT = 200
ICON_SIZE = 80
PADDING = 20
COLS = 5
SECTION_GAP = 30


def _try_load_font(size: int):
    """尝试加载字体"""
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


def _load_icon(image_name: str) -> Image.Image:
    """加载并缩放图标"""
    path = IMAGE_DIR / image_name
    if not path.exists():
        # 生成占位图
        img = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), (100, 100, 100, 255))
        return img
    img = Image.open(path).convert("RGBA")
    # 如果是gif取第一帧
    img.thumbnail((ICON_SIZE, ICON_SIZE), Image.LANCZOS)
    return img


def _get_effect_text(acc_info: dict) -> str:
    """获取配饰效果文本"""
    effects = []
    if acc_info["force"] > 0:
        effects.append(f"武+{acc_info['force']}")
    if acc_info["luck"] > 0:
        effects.append(f"运+{acc_info['luck']}")
    if acc_info["stamina"] > 0:
        effects.append(f"体+{acc_info['stamina']}")
    if acc_info["special"] == "pat_bonus_10":
        effects.append("摸+10")
    if acc_info["special"] == "affection_1.2x":
        effects.append("好感1.2x")
    return " ".join(effects) if effects else "无"


def generate_shop_image() -> str:
    """
    生成商店图片

    Returns:
        base64编码的PNG图片数据
    """
    font_title = _try_load_font(24)
    font_name = _try_load_font(16)
    font_price = _try_load_font(14)
    font_effect = _try_load_font(12)

    food_items = list(FOODS.items())
    normal_acc = [(n, i) for n, i in ACCESSORIES.items() if i["droppable"]]
    special_acc = [(n, i) for n, i in ACCESSORIES.items() if not i["droppable"]]

    # 计算各区域行数
    food_rows = (len(food_items) + COLS - 1) // COLS
    normal_rows = (len(normal_acc) + COLS - 1) // COLS
    special_rows = (len(special_acc) + COLS - 1) // COLS

    # 计算总高度
    section_title_h = 40
    total_height = PADDING  # 顶部
    total_height += section_title_h + food_rows * (CARD_HEIGHT + PADDING)  # 食物区
    total_height += SECTION_GAP
    total_height += section_title_h + normal_rows * (CARD_HEIGHT + PADDING)  # 普通配饰
    total_height += SECTION_GAP
    total_height += section_title_h + special_rows * (CARD_HEIGHT + PADDING)  # 特殊配饰
    total_height += PADDING + 30  # 底部提示

    total_width = PADDING + COLS * (CARD_WIDTH + PADDING)

    # 创建画布
    img = Image.new("RGB", (total_width, total_height), BG_COLOR)
    draw = ImageDraw.Draw(img)

    y = PADDING

    def draw_section(title: str, items: list, card_color: tuple, is_accessory: bool):
        nonlocal y
        # 标题
        draw.text((PADDING, y), f"🏪 {title}", fill=TITLE_COLOR, font=font_title)
        y += section_title_h

        for idx, (name, info) in enumerate(items):
            col = idx % COLS
            row = idx // COLS
            x = PADDING + col * (CARD_WIDTH + PADDING)
            card_y = y + row * (CARD_HEIGHT + PADDING)

            # 卡片背景（圆角矩形）
            draw.rounded_rectangle(
                [x, card_y, x + CARD_WIDTH, card_y + CARD_HEIGHT],
                radius=10,
                fill=card_color,
            )

            # 图标
            icon_name = info.get("image", "")
            if icon_name:
                icon = _load_icon(icon_name)
                icon_x = x + (CARD_WIDTH - icon.size[0]) // 2
                icon_y = card_y + 10
                img.paste(icon, (icon_x, icon_y), icon)

            # 名称
            name_bbox = draw.textbbox((0, 0), name, font=font_name)
            name_w = name_bbox[2] - name_bbox[0]
            draw.text(
                (x + (CARD_WIDTH - name_w) // 2, card_y + 10 + ICON_SIZE + 8),
                name,
                fill=TEXT_COLOR,
                font=font_name,
            )

            # 价格
            price_text = f"💰 {info['price']}"
            price_bbox = draw.textbbox((0, 0), price_text, font=font_price)
            price_w = price_bbox[2] - price_bbox[0]
            draw.text(
                (x + (CARD_WIDTH - price_w) // 2, card_y + 10 + ICON_SIZE + 30),
                price_text,
                fill=PRICE_COLOR,
                font=font_price,
            )

            # 配饰效果
            if is_accessory:
                effect_text = _get_effect_text(info)
                eff_bbox = draw.textbbox((0, 0), effect_text, font=font_effect)
                eff_w = eff_bbox[2] - eff_bbox[0]
                draw.text(
                    (x + (CARD_WIDTH - eff_w) // 2, card_y + 10 + ICON_SIZE + 50),
                    effect_text,
                    fill=EFFECT_COLOR,
                    font=font_effect,
                )

        rows = (len(items) + COLS - 1) // COLS
        y += rows * (CARD_HEIGHT + PADDING)

    # 绘制三个区域
    draw_section("食物", food_items, CARD_FOOD_COLOR, False)
    y += SECTION_GAP
    draw_section("普通配饰", normal_acc, CARD_ACC_COLOR, True)
    y += SECTION_GAP
    draw_section("特殊配饰", special_acc, CARD_SPECIAL_COLOR, True)

    # 底部提示
    tip = "发送「购买 商品名」购买"
    tip_bbox = draw.textbbox((0, 0), tip, font=font_name)
    tip_w = tip_bbox[2] - tip_bbox[0]
    draw.text(
        ((total_width - tip_w) // 2, total_height - PADDING - 20),
        tip,
        fill=(180, 180, 180),
        font=font_name,
    )

    # 输出为base64
    output = BytesIO()
    img.save(output, format="PNG")
    output.seek(0)
    return base64.b64encode(output.getvalue()).decode()


# ========== 帮助指令定义 ==========
HELP_ITEMS = [
    ("我的宠物", "查看宠物信息或领养宠物"),
    ("领养 宠物名", "领养指定宠物"),
    ("宠物散步", "消耗体力散步获取经验和道具"),
    ("宠物抚摸", "每日抚摸提升好感度"),
    ("宠物喂食 食物名 [数量]", "喂食恢复体力和好感度"),
    ("宠物pk @某人", "与他人宠物PK对战"),
    ("宠物商店", "查看商店商品"),
    ("购买 商品名 [数量]", "使用积分购买商品"),
    ("出售 物品名 [数量]", "出售背包物品获得积分"),
    ("宠物佩戴 配饰名", "佩戴配饰提升属性"),
    ("宠物背包", "查看道具背包"),
    ("宠物弃养", "弃养宠物（需二次确认）"),
    ("宠物帮助", "查看本帮助信息"),
]


def generate_help_image() -> str:
    """
    生成宠物帮助图片

    Returns:
        base64编码的PNG图片数据
    """
    font_header = _try_load_font(28)
    font_cmd = _try_load_font(20)
    font_desc = _try_load_font(14)

    # 布局
    width = 500
    item_height = 55
    header_height = 60
    padding = 25
    total_height = padding + header_height + len(HELP_ITEMS) * item_height + padding

    img = Image.new("RGB", (width, total_height), BG_COLOR)
    draw = ImageDraw.Draw(img)

    y = padding

    # 标题
    title = "🐾 宠物系统帮助"
    title_bbox = draw.textbbox((0, 0), title, font=font_header)
    title_w = title_bbox[2] - title_bbox[0]
    draw.text(((width - title_w) // 2, y), title, fill=TITLE_COLOR, font=font_header)
    y += header_height

    # 分隔线
    draw.line([(padding, y - 10), (width - padding, y - 10)], fill=DIVIDER_COLOR, width=1)

    # 指令列表
    for cmd, desc in HELP_ITEMS:
        # 指令名（大字体）
        draw.text((padding, y), cmd, fill=TEXT_COLOR, font=font_cmd)
        # 说明（小字体，次行）
        draw.text((padding + 10, y + 26), desc, fill=(160, 160, 180), font=font_desc)
        y += item_height

    # 输出为base64
    output = BytesIO()
    img.save(output, format="PNG")
    output.seek(0)
    return base64.b64encode(output.getvalue()).decode()