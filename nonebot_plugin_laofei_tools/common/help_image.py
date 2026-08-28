"""
通用帮助图片渲染模块

提供 render_help_image()，供各功能帮助指令复用同一套卡片式风格
（与 lg帮助 完全一致：深色背景 + 分区标题 + 指令/说明卡片）。

sections 支持两种元素形式：
  - (section_name, [(cmd, desc), ...], is_admin=False)   卡片区（指令列表）
  - ("__text__", "多行自由文本")                           段落区（说明文字）
"""

from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

# 颜色定义（与 lg帮助 一致）
_BG_COLOR = (45, 45, 55)
_TEXT_COLOR = (255, 255, 255)
_TITLE_COLOR = (255, 200, 100)
_SECTION_COLOR = (100, 200, 255)
_DESC_COLOR = (160, 160, 180)
_DIVIDER_COLOR = (80, 80, 95)
_ADMIN_COLOR = (255, 160, 100)


def _try_load_font(size: int):
    """尝试加载中文字体"""
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


def _wrap_text(draw, text: str, font, max_width: int):
    """按像素宽度折行，返回行列表"""
    lines = []
    for raw in text.split("\n"):
        if not raw:
            lines.append("")
            continue
        cur = ""
        for ch in raw:
            test = cur + ch
            if draw.textlength(test, font=font) > max_width and cur:
                lines.append(cur)
                cur = ch
            else:
                cur = test
        lines.append(cur)
    return lines


def render_help_image(title: str, sections: list, footer: str = "") -> str:
    """生成帮助图片，返回 base64 PNG 字符串"""
    font_title = _try_load_font(30)
    font_section = _try_load_font(22)
    font_cmd = _try_load_font(20)
    font_desc = _try_load_font(14)
    font_body = _try_load_font(15)

    width = 520
    padding = 25
    header_height = 70
    section_gap = 15
    item_height = 50
    inner_w = width - 2 * padding

    # 预计算总高度
    total_height = padding + header_height
    tmp = ImageDraw.Draw(Image.new("RGB", (width, 10)))
    for sec in sections:
        if sec[0] == "__text__":
            for ln in _wrap_text(tmp, sec[1], font_body, inner_w):
                total_height += 26
            total_height += section_gap
        else:
            _, items = sec[0], sec[1]
            total_height += 35 + len(items) * item_height + section_gap
    total_height += padding

    img = Image.new("RGB", (width, total_height), _BG_COLOR)
    draw = ImageDraw.Draw(img)
    y = padding

    # 标题
    title_bbox = draw.textbbox((0, 0), title, font=font_title)
    title_w = title_bbox[2] - title_bbox[0]
    draw.text(((width - title_w) // 2, y), title, fill=_TITLE_COLOR, font=font_title)
    y += header_height

    for sec in sections:
        if sec[0] == "__text__":
            draw.line([(padding, y - 5), (width - padding, y - 5)], fill=_DIVIDER_COLOR, width=1)
            for ln in _wrap_text(draw, sec[1], font_body, inner_w):
                draw.text((padding + 10, y), ln, fill=_TEXT_COLOR, font=font_body)
                y += 26
            y += section_gap
            continue

        section_name, items, *rest = sec
        is_admin = rest[0] if rest else False
        section_color = _ADMIN_COLOR if is_admin else _SECTION_COLOR

        draw.line([(padding, y - 5), (width - padding, y - 5)], fill=_DIVIDER_COLOR, width=1)
        draw.text((padding, y), f"【{section_name}】", fill=section_color, font=font_section)
        y += 35

        for cmd, desc in items:
            draw.text((padding + 10, y), cmd, fill=_TEXT_COLOR, font=font_cmd)
            draw.text((padding + 20, y + 25), desc, fill=_DESC_COLOR, font=font_desc)
            y += item_height

        y += section_gap

    if footer:
        foot_bbox = draw.textbbox((0, 0), footer, font=font_desc)
        foot_w = foot_bbox[2] - foot_bbox[0]
        draw.text(((width - foot_w) // 2, total_height - padding - 5), footer, fill=(100, 100, 110), font=font_desc)

    output = BytesIO()
    img.save(output, format="PNG")
    output.seek(0)
    return __import__("base64").b64encode(output.getvalue()).decode()
