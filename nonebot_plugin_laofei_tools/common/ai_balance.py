# ========== ai余额（查询 DeepSeek API Key 余额，超管专用，需 AI 功能群或私聊）==========
# 通过图片方式返回当前 DeepSeek API Key 的余额（官方 GET /user/balance）。
# 权限：仅超级用户（SUPERUSER）；群聊需本群已开启 AI 功能（ai_chat 模块），私聊放行。
# 返回：成功 -> 余额卡片图；失败 -> 文字回显错误原因（含 HTTP 状态 / 超时 / 异常）。

from nonebot import on_message, get_driver
from nonebot.adapters.onebot.v11 import MessageEvent, Message, MessageSegment
from nonebot.matcher import Matcher
from nonebot.permission import SUPERUSER
from nonebot.rule import Rule
from PIL import Image, ImageDraw, ImageFont
import httpx
import io
import base64
import datetime

from ..config import is_ai_group_enabled

AI_BALANCE_KEYWORD = "ai余额"


def _try_load_font(size: int):
    """尝试加载中文字体，失败回退默认（与功能开关图一致）"""
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


async def _is_ai_balance_request(event: MessageEvent) -> bool:
    """自定义规则：消息任意文本段含「ai余额」即触发（不受消息段顺序限制）"""
    for seg in event.message:
        if seg.type == "text":
            text = seg.data.get("text", "") or ""
            if AI_BALANCE_KEYWORD in text:
                return True
    return False


ai_balance_cmd = on_message(
    rule=Rule(_is_ai_balance_request),
    permission=SUPERUSER,
    priority=5,
    block=True,
)


async def _fetch_balance(api_key: str) -> tuple[bool, object]:
    """查询 DeepSeek 余额，返回 (ok, data_or_error_str)"""
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(
                "https://api.deepseek.com/user/balance",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Accept": "application/json",
                },
            )
        if resp.status_code != 200:
            return False, f"DeepSeek 返回 HTTP {resp.status_code}：{resp.text[:200]}"
        return True, resp.json()
    except httpx.TimeoutException:
        return False, "查询超时（20s 无响应，可能是网络或 DeepSeek 服务端异常）"
    except Exception as e:  # noqa: BLE001
        return False, f"查询失败：{e}"


def _generate_balance_image(data: dict) -> str:
    """把余额数据渲染为卡片图，返回 base64 字符串（用于 MessageSegment.image）"""
    font_title = _try_load_font(26)
    font_label = _try_load_font(18)
    font_big = _try_load_font(42)
    font_small = _try_load_font(13)

    width = 460
    padding = 28

    is_available = bool(data.get("is_available", False))
    infos = data.get("balance_infos", []) or []
    # 主币种优先 CNY，否则取第一个
    main = next((b for b in infos if b.get("currency") == "CNY"), infos[0] if infos else {})
    currency = main.get("currency", "CNY")
    total = str(main.get("total_balance", "0.00"))
    granted = str(main.get("granted_balance", "0.00"))
    topped = str(main.get("topped_up_balance", "0.00"))
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    sym = "¥" if currency == "CNY" else ("$" if currency == "USD" else "")
    accent = (120, 230, 160) if is_available else (230, 120, 120)

    top_h = 92
    mid_h = 76
    line_h = 40
    rows = 3  # 赠金 / 充值 / 货币
    footer_h = 46
    total_h = top_h + mid_h + rows * line_h + footer_h

    img = Image.new("RGB", (width, total_h), (36, 38, 48))
    draw = ImageDraw.Draw(img)

    # 标题
    draw.rounded_rectangle([padding, padding + 3, padding + 5, padding + 31], radius=2, fill=(255, 200, 100))
    draw.text((padding + 18, padding), "DeepSeek 余额查询", fill=(255, 200, 100), font=font_title)
    draw.text((padding + 18, padding + 38), now, fill=(150, 150, 160), font=font_small)

    y = top_h
    # 可用余额（大号）
    draw.text((padding, y), "可用余额", fill=(220, 220, 230), font=font_label)
    draw.text((padding, y + 20), f"{sym}{total}", fill=accent, font=font_big)
    status_txt = "可调用" if is_available else "余额不足"
    sb = draw.textbbox((0, 0), status_txt, font=font_label)
    draw.text((width - padding - (sb[2] - sb[0]), y + 14), status_txt, fill=accent, font=font_label)

    y += mid_h
    draw.line([(padding, y), (width - padding, y)], fill=(70, 70, 85), width=1)
    y += 14

    rows_data = [
        ("赠金余额", f"{sym}{granted}"),
        ("充值余额", f"{sym}{topped}"),
        ("货币", currency),
    ]
    for label, val in rows_data:
        draw.text((padding, y), label, fill=(200, 200, 210), font=font_label)
        vb = draw.textbbox((0, 0), val, font=font_label)
        draw.text((width - padding - (vb[2] - vb[0]), y), val, fill=(235, 235, 245), font=font_label)
        y += line_h

    draw.text(
        (padding, total_h - footer_h + 12),
        "仅超管可见 · 数据来自 DeepSeek 官方 API",
        fill=(130, 130, 140),
        font=font_small,
    )

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")


@ai_balance_cmd.handle()
async def _handle_ai_balance(event: MessageEvent, matcher: Matcher):
    # 权限已由 SUPERUSER 拦非超管；此处补群聊 AI 功能开关守卫（私聊放行）
    gid = getattr(event, "group_id", None)
    if gid is not None and not is_ai_group_enabled(str(gid)):
        await matcher.finish()
        return

    api_key = getattr(get_driver().config, "deepseek_api_key", "")
    if not api_key:
        await matcher.finish("⚠️ DeepSeek API Key 未配置，请在 .env 中设置 DEEPSEEK_API_KEY")
        return

    await matcher.send("正在查询 DeepSeek 余额，请稍候...")
    ok, payload = await _fetch_balance(api_key)
    if not ok:
        await matcher.finish(f"❌ {payload}")
        return

    img_b64 = _generate_balance_image(payload)
    await matcher.finish(MessageSegment.image(f"base64://{img_b64}"))
