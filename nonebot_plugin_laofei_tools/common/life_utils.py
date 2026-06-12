"""
生活实用工具：天气查询、汇率换算
"""

import base64
import re
from io import BytesIO
from pathlib import Path
from datetime import datetime, timedelta

import httpx
from PIL import Image, ImageDraw, ImageFont
from nonebot import on_command
from nonebot.adapters.onebot.v11 import MessageEvent, MessageSegment
from nonebot.log import logger
from nonebot.matcher import Matcher
from nonebot.permission import SUPERUSER


# ========== 字体加载 ==========

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


# ========== 天气查询 ==========

# 中文城市名到 wttr.in 城市名的映射（部分常用城市）
_CITY_MAP = {
    "北京": "Beijing",
    "上海": "Shanghai",
    "广州": "Guangzhou",
    "深圳": "Shenzhen",
    "杭州": "Hangzhou",
    "成都": "Chengdu",
    "武汉": "Wuhan",
    "南京": "Nanjing",
    "天津": "Tianjin",
    "重庆": "Chongqing",
    "西安": "Xian",
    "长沙": "Changsha",
    "苏州": "Suzhou",
    "郑州": "Zhengzhou",
    "东莞": "Dongguan",
    "青岛": "Qingdao",
    "厦门": "Xiamen",
    "合肥": "Hefei",
    "福州": "Fuzhou",
    "昆明": "Kunming",
    "大连": "Dalian",
    "济南": "Jinan",
    "沈阳": "Shenyang",
    "南宁": "Nanning",
    "贵阳": "Guiyang",
    "石家庄": "Shijiazhuang",
    "哈尔滨": "Harbin",
    "太原": "Taiyuan",
    "南昌": "Nanchang",
    "长春": "Changchun",
    "海口": "Haikou",
    "台北": "Taipei",
    "香港": "Hong Kong",
    "澳门": "Macau",
}

# 天气代码转中文
_WEATHER_CODE_MAP = {
    "113": "晴", "116": "多云", "119": "阴", "122": "阴",
    "143": "雾", "176": "阵雨", "179": "雪", "182": "雨夹雪",
    "185": "冻雨", "200": "雷阵雨", "227": "暴风雪",
    "230": "暴风雪", "248": "雾", "260": "雾",
    "248": "雾", "260": "大雾",
    "263": "小雨", "266": "小雨", "281": "冻雨",
    "284": "冻雨", "293": "小雨", "296": "小雨",
    "299": "中雨", "302": "中雨", "305": "大雨",
    "308": "大雨", "311": "冻雨", "314": "冻雨",
    "317": "雨夹雪", "320": "雨夹雪", "323": "小雪",
    "326": "小雪", "329": "大雪", "332": "中雪",
    "335": "暴雪", "338": "暴雪", "350": "冰雹",
    "353": "阵雨", "356": "中雨", "359": "大雨",
    "362": "雨夹雪", "365": "雨夹雪", "368": "小雪",
    "371": "大雪", "374": "冰雹", "377": "冰雹",
    "386": "雷阵雨", "389": "雷阵雨", "392": "雷阵雪",
    "395": "暴雪",
}

# 天气背景色
_WEATHER_BG = (40, 45, 60)
_WEATHER_TITLE = (255, 210, 100)
_WEATHER_LABEL = (140, 180, 220)
_WEATHER_VALUE = (255, 255, 255)
_WEATHER_TEMP_HIGH = (255, 140, 100)
_WEATHER_TEMP_LOW = (100, 180, 255)
_WEATHER_DESC = (180, 200, 220)


weather_cmd = on_command("lg天气", aliases={"天气", "天气查询", "lg weather"}, priority=5, block=True, force_whitespace=True)


@weather_cmd.handle()
async def handle_weather(matcher: Matcher, event: MessageEvent):
    """处理天气查询"""
    # on_command handler 的 get_message() 返回完整消息含命令前缀，需剥离
    full_text = event.get_plaintext().strip()
    if " " in full_text:
        args = full_text.split(" ", 1)[1].strip()
    else:
        args = ""
    if not args:
        await matcher.finish("请提供城市名，例如：lg天气 深圳")

    # 解析参数：城市 [日期]，支持日期放前面的情况
    parts = args.split()
    _DATE_KEYWORDS = {
        "今天", "今日", "today",
        "明天", "明日", "tomorrow",
        "后天", "后日",
    }
    if len(parts) == 1:
        city_raw = parts[0]
        date_str = ""
    elif len(parts) >= 2:
        if parts[0] in _DATE_KEYWORDS:
            # 日期放前面了，自动交换：lg天气 明天 深圳
            date_str = parts[0]
            city_raw = parts[1]
        else:
            # 正常顺序：lg天气 深圳 明天
            city_raw = parts[0]
            date_str = parts[1]
    else:
        # len(parts) >= 2，但第一个参数不是日期关键词（如日期字符串放前面），暂不支持
        await matcher.finish("参数顺序有误，请使用：lg天气 深圳 [日期]，日期支持：今天/明天/后天 或 YYYY-MM-DD")

    # 映射城市名
    city_en = _CITY_MAP.get(city_raw, city_raw)

    # 确定查询日期
    today = datetime.now()
    query_date = today
    display_date = today.strftime("%Y-%m-%d")

    if date_str:
        date_map = {
            "今天": 0, "今日": 0, "today": 0,
            "明天": 1, "明日": 1, "tomorrow": 1,
            "后天": 2, "后日": 2,
        }
        offset = date_map.get(date_str, None)
        if offset is not None:
            query_date = today + timedelta(days=offset)
            display_date = query_date.strftime("%Y-%m-%d")
        else:
            # 尝试解析具体日期 YYYY-MM-DD
            try:
                query_date = datetime.strptime(date_str, "%Y-%m-%d")
                display_date = query_date.strftime("%Y-%m-%d")
            except ValueError:
                try:
                    query_date = datetime.strptime(date_str, "%m-%d")
                    display_date = query_date.strftime("%m-%d")
                except ValueError:
                    await matcher.finish(f"无法解析日期「{date_str}」，请使用 今天/明天/后天 或 YYYY-MM-DD 格式")

    # 判断是否查询未来日期：wttr.in 免费版只支持当天+2天
    day_offset = (query_date.date() - today.date()).days
    if day_offset < 0:
        await matcher.finish("不支持查询过去的天气哦")
    if day_offset > 2:
        await matcher.finish("暂只支持查询今明后三天的天气")

    # 请求 wttr.in API
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"https://wttr.in/{city_en}",
                params={"format": "j1", "lang": "zh"},
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as e:
        logger.warning(f"天气API请求失败: {e}")
        await matcher.finish("天气服务暂不可用，请稍后再试")
    except Exception as e:
        logger.error(f"天气API解析失败: {e}")
        await matcher.finish(f"查询失败: {str(e)[:50]}")

    # 提取天气数据
    weather_data = data.get("weather", [])
    if not weather_data:
        await matcher.finish(f"未找到「{city_raw}」的天气数据")

    # 今天 weather_data[0]，+1天 weather_data[1]，+2天 weather_data[2]
    if day_offset >= len(weather_data):
        await matcher.finish(f"暂无「{city_raw}」{display_date}的天气预报")

    day_info = weather_data[day_offset]
    date_actual = day_info.get("date", display_date)

    # 小时数据
    hourly = day_info.get("hourly", [])
    # 取 12:00 或最中间的时段作为代表
    target_hourly = None
    for h in hourly:
        if h.get("time") == "1200":
            target_hourly = h
            break
    if target_hourly is None and hourly:
        mid = len(hourly) // 2
        target_hourly = hourly[mid]
    if target_hourly is None:
        target_hourly = {}

    weather_code = target_hourly.get("weatherCode", "113")
    weather_desc = _WEATHER_CODE_MAP.get(weather_code, target_hourly.get("weatherDesc", [{}])[0].get("value", "未知"))
    temp_c = target_hourly.get("tempC", "--")
    humidity = target_hourly.get("humidity", "--")
    wind_speed = target_hourly.get("windspeedKmph", "--")
    wind_dir = target_hourly.get("winddir16Point", "--")
    feels_like = target_hourly.get("FeelsLikeC", "--")
    visibility = target_hourly.get("visibility", "--")

    # 最高/最低温度
    max_temp = day_info.get("maxtempC", "--")
    min_temp = day_info.get("mintempC", "--")

    # 日出日落
    astronomy = day_info.get("astronomy", [{}])[0] if day_info.get("astronomy") else {}
    sunrise = astronomy.get("sunrise", "--")
    sunset = astronomy.get("sunset", "--")

    # 生成图片
    img_b64 = _generate_weather_image(
        city=city_raw,
        city_en=city_en,
        date=date_actual,
        weather=weather_desc,
        temp=temp_c,
        max_temp=max_temp,
        min_temp=min_temp,
        humidity=humidity,
        wind_speed=wind_speed,
        wind_dir=wind_dir,
        feels_like=feels_like,
        visibility=visibility,
        sunrise=sunrise,
        sunset=sunset,
    )
    await matcher.finish(MessageSegment.image(f"base64://{img_b64}"))


def _generate_weather_image(
    city, city_en, date, weather, temp, max_temp, min_temp,
    humidity, wind_speed, wind_dir, feels_like, visibility,
    sunrise, sunset,
) -> str:
    """生成天气信息图片，返回 base64"""
    font_title = _try_load_font(28)
    font_main = _try_load_font(22)
    font_info = _try_load_font(16)
    font_big = _try_load_font(48)

    width, height = 420, 380
    padding = 24

    img = Image.new("RGB", (width, height), _WEATHER_BG)
    draw = ImageDraw.Draw(img)

    y = padding

    # 标题行：城市 + 日期
    title = f"{city} ({city_en})"
    draw.text((padding, y), title, fill=_WEATHER_TITLE, font=font_title)
    draw.text((padding, y + 36), date, fill=_WEATHER_LABEL, font=font_info)
    y += 66

    # 天气状况 + 温度（大字）
    draw.text((padding, y), weather, fill=_WEATHER_VALUE, font=font_big)
    temp_text = f"{temp}°C"
    temp_bbox = draw.textbbox((0, 0), temp_text, font=font_big)
    temp_w = temp_bbox[2] - temp_bbox[0]
    draw.text((width - padding - temp_w, y), temp_text, fill=_WEATHER_TITLE, font=font_big)
    y += 56

    # 体感
    feels_text = f"体感 {feels_like}°C"
    draw.text((padding, y), feels_text, fill=_WEATHER_DESC, font=font_info)
    y += 28

    # 最高/最低
    hi_lo = f"最高 {max_temp}°C  /  最低 {min_temp}°C"
    hi_bbox = draw.textbbox((0, 0), hi_lo, font=font_main)
    hi_w = hi_bbox[2] - hi_bbox[0]
    draw.text(((width - hi_w) // 2, y), hi_lo, fill=_WEATHER_VALUE, font=font_main)
    y += 38

    # 分隔线
    draw.line([(padding, y), (width - padding, y)], fill=(80, 85, 100), width=1)
    y += 18

    # 详细信息（两列布局）
    col1_x = padding
    col2_x = width // 2 + 10
    line_h = 28

    details = [
        ("湿度", f"{humidity}%", "风向", f"{wind_dir}"),
        ("风速", f"{wind_speed} km/h", "能见度", f"{visibility} km"),
        ("日出", sunrise, "日落", sunset),
    ]

    for label1, val1, label2, val2 in details:
        draw.text((col1_x, y), label1, fill=_WEATHER_LABEL, font=font_info)
        draw.text((col1_x + 52, y), val1, fill=_WEATHER_VALUE, font=font_info)
        draw.text((col2_x, y), label2, fill=_WEATHER_LABEL, font=font_info)
        draw.text((col2_x + 52, y), val2, fill=_WEATHER_VALUE, font=font_info)
        y += line_h

    output = BytesIO()
    img.save(output, format="PNG")
    output.seek(0)
    return base64.b64encode(output.getvalue()).decode()


# ========== 汇率换算 ==========

exchange_cmd = on_command("lg换算", aliases={"汇率", "汇率换算", "lg exchange"}, priority=5, block=True, force_whitespace=True)

# 常用货币名称映射
_CURRENCY_MAP = {
    "人民币": "CNY", "rmb": "CNY", "RMB": "CNY", "cny": "CNY", "CNY": "CNY",
    "元": "CNY",
    "美元": "USD", "美金": "USD", "usd": "USD", "USD": "USD", "美刀": "USD", "刀乐": "USD",
    "欧元": "EUR", "eur": "EUR", "EUR": "EUR",
    "日元": "JPY", "jpy": "JPY", "JPY": "JPY", "日币": "JPY",
    "英镑": "GBP", "gbp": "GBP", "GBP": "GBP",
    "港币": "HKD", "hkd": "HKD", "HKD": "HKD",
    "韩元": "KRW", "krw": "KRW", "KRW": "KRW", "韩币": "KRW", "棒子币": "KRW",
    "澳元": "AUD", "aud": "AUD", "AUD": "AUD",
    "加元": "CAD", "cad": "CAD", "CAD": "CAD",
    "新加坡元": "SGD", "新币": "SGD", "sgd": "SGD", "SGD": "SGD",
    "泰铢": "THB", "thb": "THB", "THB": "THB",
    "卢布": "RUB", "rub": "RUB", "RUB": "RUB",
    "印度卢比": "INR", "inr": "INR", "INR": "INR",
    "瑞士法郎": "CHF", "chf": "CHF", "CHF": "CHF",
    "新台币": "TWD", "twd": "TWD", "TWD": "TWD",
    "澳门元": "MOP", "mop": "MOP", "MOP": "MOP",
}


@exchange_cmd.handle()
async def handle_exchange(matcher: Matcher, event: MessageEvent):
    """处理汇率换算"""
    full_text = event.get_plaintext().strip()
    if " " in full_text:
        args = full_text.split(" ", 1)[1].strip()
    else:
        args = ""
    if not args:
        await matcher.finish("用法：lg换算 金额 来源货币 目标货币\n例如：lg换算 100 人民币 美元")

    parts = args.split()
    if len(parts) < 3:
        await matcher.finish("参数不足，用法：lg换算 100 人民币 美元")

    # 解析
    try:
        amount = float(parts[0])
    except ValueError:
        await matcher.finish(f"无效金额: {parts[0]}")

    from_currency_raw = parts[1]
    to_currency_raw = parts[2]

    from_currency = _CURRENCY_MAP.get(from_currency_raw, from_currency_raw.upper())
    to_currency = _CURRENCY_MAP.get(to_currency_raw, to_currency_raw.upper())

    if from_currency == to_currency:
        await matcher.finish(f"同种货币无需换算：{amount} {from_currency_raw} = {amount} {to_currency_raw}")

    # 请求汇率
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(f"https://api.exchangerate-api.com/v4/latest/{from_currency}")
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as e:
        logger.warning(f"汇率API请求失败: {e}")
        await matcher.finish("汇率服务暂不可用，请稍后再试")
    except Exception as e:
        logger.error(f"汇率API解析失败: {e}")
        await matcher.finish(f"查询失败: {str(e)[:50]}")

    rates = data.get("rates", {})
    if to_currency not in rates:
        await matcher.finish(f"不支持的货币: {to_currency_raw}")

    rate = rates[to_currency]
    result = amount * rate

    # 生成图片
    img_b64 = _generate_exchange_image(
        amount=amount,
        from_name=from_currency_raw,
        from_code=from_currency,
        to_name=to_currency_raw,
        to_code=to_currency,
        rate=rate,
        result=result,
    )
    await matcher.finish(MessageSegment.image(f"base64://{img_b64}"))


_EXCHANGE_BG = (40, 45, 60)
_EXCHANGE_TITLE = (255, 210, 100)
_EXCHANGE_VALUE = (255, 255, 255)
_EXCHANGE_RATE = (140, 180, 220)
_EXCHANGE_SUB = (160, 160, 180)


def _generate_exchange_image(amount, from_name, from_code, to_name, to_code, rate, result):
    """生成汇率换算图片"""
    font_title = _try_load_font(26)
    font_big = _try_load_font(36)
    font_main = _try_load_font(18)
    font_small = _try_load_font(14)

    width, height = 420, 280
    padding = 28

    img = Image.new("RGB", (width, height), _EXCHANGE_BG)
    draw = ImageDraw.Draw(img)

    y = padding + 10

    # 标题
    draw.text((padding, y), "汇率换算", fill=_EXCHANGE_TITLE, font=font_title)
    y += 44

    # 来源金额
    amount_text = f"{amount:,.2f} {from_name}"
    amount_bbox = draw.textbbox((0, 0), amount_text, font=font_big)
    amount_w = amount_bbox[2] - amount_bbox[0]
    draw.text((padding, y), amount_text, fill=_EXCHANGE_VALUE, font=font_big)
    draw.text((padding + amount_w + 8, y + 6), from_code, fill=_EXCHANGE_SUB, font=font_small)
    y += 50

    # = 号
    draw.text((padding, y), "=", fill=_EXCHANGE_RATE, font=font_big)
    y += 48

    # 目标金额
    result_text = f"{result:,.2f} {to_name}"
    result_bbox = draw.textbbox((0, 0), result_text, font=font_big)
    result_w = result_bbox[2] - result_bbox[0]
    draw.text((padding, y), result_text, fill=_EXCHANGE_VALUE, font=font_big)
    draw.text((padding + result_w + 8, y + 6), to_code, fill=_EXCHANGE_SUB, font=font_small)
    y += 50

    # 分隔线
    draw.line([(padding, y), (width - padding, y)], fill=(80, 85, 100), width=1)
    y += 16

    # 汇率说明
    rate_text = f"1 {from_code} = {rate:.4f} {to_code}"
    draw.text((padding, y), rate_text, fill=_EXCHANGE_RATE, font=font_main)

    output = BytesIO()
    img.save(output, format="PNG")
    output.seek(0)
    return base64.b64encode(output.getvalue()).decode()


# ========== 公告 ==========

announce_cmd = on_command("lg公告", aliases={"插件公告", "更新公告"}, permission=SUPERUSER, priority=5, block=True, force_whitespace=True)

_ANNOUNCE_BG = (35, 40, 50)
_ANNOUNCE_TITLE = (255, 200, 100)
_ANNOUNCE_ITEM = (255, 255, 255)
_ANNOUNCE_NUM = (100, 200, 255)
_ANNOUNCE_DATE = (140, 150, 170)
_ANNOUNCE_DIV = (60, 65, 75)

# commit message 前缀 → 公告文案映射
def _get_changelog() -> list[tuple[str, list]]:
    """从 CHANGELOG.txt 读取变更列表，按日期分组

    CHANGELOG.txt 格式：
        ## 2026-06-12（日期标题）
        1.新增xxx功能（序号. 开头 = 新条目）
           缩进行 = 附属于上一条的子行（如示例），合并为一条
        # 或 // 开头 = 注释，忽略

    Returns:
        [(date_str, [item1, item2, ...]), ...]
    """
    changelog = None

    # 策略1：从插件包目录找
    plugin_dir = Path(__file__).resolve().parent.parent
    candidate = plugin_dir / "CHANGELOG.txt"
    if candidate.exists():
        changelog = candidate
        logger.debug(f"公告: 在插件包中找到 CHANGELOG.txt: {candidate}")

    # 策略2：从工作目录兜底
    if changelog is None:
        p = Path.cwd()
        for _ in range(8):
            candidate = p / "CHANGELOG.txt"
            if candidate.exists():
                changelog = candidate
                logger.debug(f"公告: 在工作目录中找到 CHANGELOG.txt: {candidate}")
                break
            p = p.parent

    if changelog is None:
        changelog = plugin_dir / "CHANGELOG.txt"
        changelog.write_text(
            "## 待填写日期\n"
            "1.在此填写第一条更新内容\n"
            "   在此填写示例或补充说明\n",
            encoding="utf-8",
        )
        logger.info(f"公告: 自动创建 CHANGELOG.txt → {changelog}")

    sections: list[tuple[str, list]] = []
    current_date = ""
    current_items: list = []

    for line in changelog.read_text(encoding="utf-8").split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") and not stripped.startswith("##"):
            continue
        if stripped.startswith("//"):
            continue

        # 日期标题：## 2026-06-12
        if stripped.startswith("## ") or stripped.startswith("##"):
            date_str = stripped.lstrip("#").strip()
            # 保存上一个 section
            if current_items:
                sections.append((current_date, current_items))
                current_items = []
            current_date = date_str
            continue

        if line.startswith((" ", "\t")):
            # 缩进行 → 附属于上一条
            if current_items:
                current_items[-1] = current_items[-1] + "\n" + stripped
        else:
            # 新条目
            content = re.sub(r"^\d+\.\s*", "", stripped).strip()
            if content:
                current_items.append(content)

    # 最后一个 section
    if current_items:
        sections.append((current_date, current_items))

    return sections


@announce_cmd.handle()
async def handle_announce(matcher: Matcher, event: MessageEvent):
    """生成并发送插件更新公告图片"""
    full_text = event.get_plaintext().strip()
    if " " in full_text:
        content = full_text.split(" ", 1)[1].strip()
    else:
        content = ""

    if content:
        # 手动模式：解析用户输入，按单个日期分组
        items = re.split(r"\d+\.\s*", content)
        items = [item.strip() for item in items if item.strip()]
        sections = [(datetime.now().strftime("%Y-%m-%d"), items)]
    else:
        # 自动模式：从 CHANGELOG.txt 读取（带日期分组）
        sections = _get_changelog()

    if not sections:
        await matcher.finish("暂无新的更新内容，当前已是最新版本。")

    img_b64 = _generate_announce_image(sections)
    await matcher.finish(MessageSegment.image(f"base64://{img_b64}"))


def _generate_announce_image(sections: list[tuple[str, list]]) -> str:
    """生成公告图片，按日期分组显示"""
    font_title = _try_load_font(30)
    font_item = _try_load_font(18)
    font_date = _try_load_font(14)
    font_section_date = _try_load_font(16)

    width = 520
    padding = 28
    title_h = 60
    item_gap = 12
    section_gap = 18
    max_text_width = width - padding * 2 - 40

    # 预处理：每个 section 的条目拆成 (date, [(num, lines), ...])
    global_num = 0
    section_data: list[tuple[str, list]] = []
    for date_str, items in sections:
        item_lines = []
        for item in items:
            global_num += 1
            sub_lines = item.split("\n")
            all_wrapped = []
            for sub in sub_lines:
                if font_item:
                    all_wrapped.extend(_wrap_text(sub, font_item, max_text_width))
                else:
                    all_wrapped.append(sub)
            item_lines.append((global_num, all_wrapped))
        section_data.append((date_str, item_lines))

    # 计算总高度
    total_height = padding + title_h + 40  # 标题 + 发布日期
    for date_str, item_lines in section_data:
        total_height += 26  # 日期标签
        for _, lines in item_lines:
            total_height += len(lines) * 28
        total_height += (len(item_lines) - 1) * item_gap
        total_height += section_gap
    total_height += 30 + padding  # 底线 + 结尾文字

    img = Image.new("RGB", (width, total_height), _ANNOUNCE_BG)
    draw = ImageDraw.Draw(img)

    y = padding

    # 标题
    draw.text((padding, y), "龙哥工具箱 - 更新公告", fill=_ANNOUNCE_TITLE, font=font_title)
    y += title_h

    date_str = datetime.now().strftime("%Y-%m-%d")
    draw.text((padding, y + 5), f"发布日期: {date_str}", fill=_ANNOUNCE_DATE, font=font_date)
    y += 40

    draw.line([(padding, y - 10), (width - padding, y - 10)], fill=_ANNOUNCE_DIV, width=1)

    for section_date, item_lines in section_data:
        # 日期标题
        if section_date:
            y += 14
            draw.line([(padding, y - 5), (width - padding, y - 5)], fill=_ANNOUNCE_DIV, width=1)
            draw.text((padding, y), f"▎{section_date}", fill=_ANNOUNCE_DATE, font=font_section_date)
            y += 22

        for num, lines in item_lines:
            draw.text((padding + 4, y), str(num), fill=_ANNOUNCE_NUM, font=font_item)
            for j, line in enumerate(lines):
                if j == 0:
                    draw.text((padding + 30, y), line, fill=_ANNOUNCE_ITEM, font=font_item)
                else:
                    draw.text((padding + 44, y), line, fill=_ANNOUNCE_DATE, font=font_item)
                y += 28
            y += item_gap
        y += section_gap

    y -= section_gap
    draw.line([(padding, y + 5), (width - padding, y + 5)], fill=_ANNOUNCE_DIV, width=1)
    y += 20

    draw.text((padding, y), "以上为本次更新内容，如有问题请联系管理员", fill=_ANNOUNCE_DATE, font=font_date)

    output = BytesIO()
    img.save(output, format="PNG")
    output.seek(0)
    return base64.b64encode(output.getvalue()).decode()


def _wrap_text(text: str, font, max_width: int) -> list:
    """对文本按宽度自动换行"""
    lines = []
    current = ""
    for char in text:
        test = current + char
        bbox = font.getbbox(test)[2] - font.getbbox(test)[0]
        if bbox <= max_width:
            current = test
        else:
            lines.append(current)
            current = char
    if current:
        lines.append(current)
    return lines
