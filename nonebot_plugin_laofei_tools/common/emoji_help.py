"""
表情包帮助指令

为外部「表情包」插件提供一份写死的帮助说明，采用与「搜图帮助」一致的图片返回模式：
用 PIL 将帮助文案绘制为图片，再以 base64 单图形式返回（matcher.finish(MessageSegment.image)）。
"""

from nonebot import on_command
from nonebot.adapters.onebot.v11 import MessageSegment
from nonebot.adapters.onebot.v11.event import MessageEvent
from nonebot.matcher import Matcher

emoji_help_cmd = on_command(
    "表情包帮助",
    aliases={"表情帮助", "meme帮助"},
    priority=5,
    block=True,
    force_whitespace=True,
)


@emoji_help_cmd.handle()
async def handle_emoji_help(matcher: Matcher, event: MessageEvent):
    """处理表情包帮助指令：将写死的帮助说明渲染为图片返回"""
    import base64
    from io import BytesIO

    from PIL import Image, ImageDraw, ImageFont

    # ---- 字体（跨平台回退，与搜图帮助一致）----
    def _try_load_font(size: int):
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

    font_h1 = _try_load_font(30)
    font_h2 = _try_load_font(22)
    font_p = _try_load_font(17)
    font_cmd = _try_load_font(17)
    font_tip = _try_load_font(15)

    # 颜色
    BG = (45, 45, 55)
    H1_C = (255, 200, 100)
    H2_C = (100, 200, 255)
    P_C = (235, 235, 240)
    CMD_C = (130, 210, 140)
    TIP_C = (180, 180, 200)
    DIVIDER_C = (80, 80, 95)

    # ---- 帮助内容（外部表情包插件写死的说明）----
    # kind: h1 主标题 | h2 章节标题 | p 正文 | cmd 指令列表项 | tip 示例/备注
    content = [
        ("h1", "表情包帮助"),
        ("h2", "表情列表"),
        ("p", '发送 “表情包制作” 查看表情列表'),
        ("h2", "表情详情"),
        ("p", '发送 “表情详情 + 关键词” 查看表情详细信息和表情预览'),
        ("h2", "表情搜索"),
        ("p", '发送 “表情搜索 + 关键词” 查找相关的表情'),
        ("h2", "表情包开关"),
        ("p", "“超级用户” 和 “管理员” 可以启用或禁用某些表情"),
        ("p", '发送 “启用表情/禁用表情 + 关键词”，如：禁用表情 摸'),
        ("p", "“超级用户” 可以设置某个表情包的管控模式（黑名单/白名单）"),
        ("p", '发送 “全局启用表情 + 关键词” 可将表情设为黑名单模式；'),
        ("p", '发送 “全局禁用表情 + 关键词” 可将表情设为白名单模式；'),
        ("h2", "表情使用"),
        ("p", '发送 “关键词 + 图片/文字” 制作表情'),
        ("p", '可使用 “自己”、“@某人” 获取指定用户的头像作为图片'),
        ("p", '可使用 “@ + 用户 id” 指定任意用户获取头像，如 摸 @114514'),
        ("p", "可将回复中的消息作为文字和图片的输入"),
        ("p", '指定用户时将使用用户昵称作为“图片名”'),
        ("p", '可使用“# + 名字”指定“图片名”，如 小天使 #name 自己'),
        ("h2", "随机表情"),
        ("p", '发送 “随机表情 + 图片/文字” 可随机制作表情'),
        ("p", "随机范围为 图片/文字 数量符合要求的表情"),
        ("h2", "表情调用统计"),
        ("p", '发送 “[我的][全局]<时间段>表情调用统计 [表情名]” 获取表情调用次数统计图'),
        ("p", "“我的”、“全局”、<时间段>、“表情名” 均为可选项"),
        ("p", "<时间段> 可以为：日、本日、周、本周、月、本月、年、本年"),
        ("tip", "如：我的今日表情调用统计 petpet"),
        ("h2", "简单图片操作"),
        ("p", '插件附带了简单图片操作功能，发送 “图片操作” 查看帮助'),
        ("p", "可用的指令有："),
        ("cmd", "水平翻转/左翻/右翻"),
        ("cmd", "竖直翻转/上翻/下翻"),
        ("cmd", "旋转 + 角度"),
        ("cmd", "缩放 + 尺寸或百分比，如：缩放 100x100；缩放 200x；缩放 150%"),
        ("cmd", "裁剪 + 尺寸或比例，如：裁剪 100x100；裁剪 2:1"),
        ("cmd", "灰度图/黑白"),
        ("cmd", "反相/反色"),
        ("cmd", "横向拼接 + 至少两张图片"),
        ("cmd", "纵向拼接 + 至少两张图片"),
        ("cmd", "gif分解"),
        ("cmd", "gif合成 + 至少两张图片"),
        ("cmd", "gif倒放/倒放"),
        ("cmd", "gif变速 + 倍率或fps，如：gif变速 0.5x；gif变速 10fps"),
    ]

    # ---- 文本按像素宽度自动换行（兼容中英文混排，并支持 “指令” 高亮）----
    def wrap_mixed(text: str, base_font, base_color, hl_color, max_w: int):
        """将段落按像素宽度换行；返回 lines，每行是 [(seg, font, color, is_hl), ...]。
        “...” 内的文本标记为 is_hl（指令高亮：橙红 + 加粗描边）。"""
        runs = []  # (text, is_hl)
        in_hl = False
        buf = ""
        for ch in text:
            if ch in ('“', '”'):
                if buf:
                    runs.append((buf, in_hl))
                    buf = ""
                in_hl = not in_hl
                continue
            buf += ch
        if buf:
            runs.append((buf, in_hl))

        lines = []
        cur = []  # [(seg, font, color, is_hl)]

        def cur_width():
            return sum(base_font.getlength(s) for s, _f, _c, _h in cur)

        for rtxt, is_hl in runs:
            color = hl_color if is_hl else base_color
            seg = ""
            for ch in rtxt:
                w = base_font.getlength(ch)
                if cur and cur_width() + base_font.getlength(seg) + w > max_w:
                    if seg:
                        cur.append((seg, base_font, color, is_hl))
                        seg = ""
                    lines.append(cur)
                    cur = []
                seg += ch
            if seg:
                cur.append((seg, base_font, color, is_hl))
        if cur:
            lines.append(cur)
        return lines

    W = 660
    PAD = 26
    USABLE = W - 2 * PAD

    # 指令高亮色（橙红）
    HL_C = (255, 95, 35)

    # 先排版计算总高度
    layout = []  # (kind, lines, lh, gap, indent)
    total_h = PAD
    for kind, text in content:
        if kind == "h1":
            font, color, lh, gap = font_h1, H1_C, 44, 10
            indent = 0
        elif kind == "h2":
            font, color, lh, gap = font_h2, H2_C, 32, 16
            indent = 0
        elif kind == "cmd":
            font, color, lh, gap = font_cmd, CMD_C, 26, 4
            indent = 12
        elif kind == "tip":
            font, color, lh, gap = font_tip, TIP_C, 24, 4
            indent = 12
        else:  # p
            font, color, lh, gap = font_p, P_C, 26, 6
            indent = 0
        max_w = USABLE - indent
        lines = wrap_mixed(text, font, color, HL_C, max_w)
        layout.append((kind, lines, lh, gap, indent))
        if kind == "h2":
            total_h += gap + 14 + len(lines) * lh
        else:
            total_h += gap + len(lines) * lh
    total_h += PAD

    img = Image.new("RGB", (W, total_h), BG)
    draw = ImageDraw.Draw(img)
    y = PAD

    for kind, lines, lh, gap, indent in layout:
        y += gap
        if kind == "h2":
            draw.line([(PAD, y), (W - PAD, y)], fill=DIVIDER_C, width=1)
            y += 14
        for line in lines:
            x = PAD + indent
            if kind == "h1":
                tot = sum(f.getlength(s) for s, f, c, h in line)
                x = (W - tot) // 2
            for seg, font, color, is_hl in line:
                if is_hl:
                    draw.text((x, y), seg, fill=color, font=font,
                              stroke_width=2, stroke_fill=color)
                else:
                    draw.text((x, y), seg, fill=color, font=font)
                x += font.getlength(seg)
            y += lh

    out = BytesIO()
    img.save(out, format="PNG")
    out.seek(0)
    img_b64 = base64.b64encode(out.getvalue()).decode()
    await matcher.finish(MessageSegment.image(f"base64://{img_b64}"))
