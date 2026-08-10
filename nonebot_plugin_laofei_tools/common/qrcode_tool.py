"""
二维码工具

两种用法（均为「引用 + 发送指令」或「直接发送图片/文本 + 指令」）：

1. 识别二维码
   - 引用一张图片（或直接在消息里发图片），并发送「二维码识别」
   - 自动识别图片中的二维码内容并回复

2. 生成二维码
   - 引用一段文本（或直接发送「生成二维码 内容」），并发送「生成二维码」
   - 自动根据文本生成二维码图片并回复

依赖：
   - 生成：qrcode（pip install qrcode，底层用 Pillow，已在项目依赖中）
   - 识别：pyzbar + 系统 zbar 库
       · macOS:  brew install zbar
       · Linux:  apt-get install libzbar0
       · pip install pyzbar
"""

import base64
import io
import os
from typing import Optional

import httpx
from nonebot import on_message
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageEvent, MessageSegment
from nonebot.log import logger
from nonebot.matcher import Matcher
from nonebot.rule import Rule
from PIL import Image

# 识别库（pyzbar）按需导入：缺失时识别功能给出友好提示，不影响生成功能
try:
    from pyzbar.pyzbar import decode as _qr_decode

    _HAS_DECODE = True
except Exception:  # pragma: no cover - 取决于环境是否装了 zbar
    _HAS_DECODE = False

# 生成库（qrcode）按需导入
try:
    import qrcode

    _HAS_GEN = True
except Exception:  # pragma: no cover
    _HAS_GEN = False


# ============ 触发词 ============

DECODE_KEYWORDS = {"二维码识别", "识别二维码", "读二维码", "二维码读取"}
GEN_KEYWORDS = {"生成二维码", "二维码生成", "做二维码", "创建二维码"}

# 生成二维码时文本长度上限，防止生成超大图
MAX_QR_TEXT_LEN = 2000


def _plain_text(message) -> str:
    """跨 nonebot 版本获取消息纯文本（新版 get_plaintext / 旧版 extract_plain_text）"""
    if hasattr(message, "get_plaintext"):
        return message.get_plaintext()
    if hasattr(message, "extract_plain_text"):
        return message.extract_plain_text()
    return str(message)


def _make_text_rule(keywords: set) -> Rule:
    """构造一个 on_message 规则：消息纯文本（忽略前置 @机器人）命中关键字集合"""

    def _rule(event: MessageEvent) -> bool:
        text = _plain_text(event.message).strip()
        return any(text == kw or text.startswith(kw + " ") for kw in keywords)

    return Rule(_rule)


qr_decode_cmd = on_message(rule=_make_text_rule(DECODE_KEYWORDS), priority=5, block=True)
qr_gen_cmd = on_message(rule=_make_text_rule(GEN_KEYWORDS), priority=5, block=True)


# ============ 图片/文本提取 ============


def _find_image_seg(event: MessageEvent):
    """优先取当前消息的图片，其次取被引用(reply)消息里的图片"""
    for seg in event.message:
        if seg.type == "image":
            return seg
    reply = getattr(event, "reply", None)
    if reply is not None:
        rmsg = getattr(reply, "message", None)
        if rmsg is not None:
            for seg in rmsg:
                if seg.type == "image":
                    return seg
    return None


def _get_qr_text(event: MessageEvent, matched_kw: str) -> str:
    """生成二维码用的文本：优先取被引用消息的纯文本，其次取当前消息去掉触发词后的文本"""
    reply = getattr(event, "reply", None)
    if reply is not None:
        rmsg = getattr(reply, "message", None)
        if rmsg is not None:
            t = _plain_text(rmsg).strip()
            if t:
                return t
    text = _plain_text(event.message).strip()
    if text == matched_kw:
        return ""
    if text.startswith(matched_kw + " "):
        return text[len(matched_kw) + 1 :].strip()
    return ""


async def _get_image_bytes(seg) -> Optional[bytes]:
    """从 image 段取出图片二进制：支持 base64://、http(s) 下载、file:// 本地路径"""
    url = seg.data.get("url")
    file = seg.data.get("file")

    # base64 内嵌
    for raw in (url, file):
        if raw and raw.startswith("base64://"):
            try:
                return base64.b64decode(raw.split("base64://", 1)[1])
            except Exception:
                return None

    # http(s) 下载
    dl = url or file
    if dl and (dl.startswith("http://") or dl.startswith("https://")):
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(dl)
                resp.raise_for_status()
                return resp.content
        except Exception as e:
            logger.warning(f"二维码: 图片下载失败 {dl}: {e}")
            return None

    # 本地文件
    if file and file.startswith("file://"):
        path = file[len("file://") :]
        try:
            if os.path.exists(path):
                with open(path, "rb") as f:
                    return f.read()
        except Exception as e:
            logger.warning(f"二维码: 本地图片读取失败 {path}: {e}")
            return None

    return None


# ============ 识别二维码 ============


@qr_decode_cmd.handle()
async def handle_qr_decode(matcher: Matcher, event: MessageEvent) -> None:
    seg = _find_image_seg(event)
    if seg is None:
        await matcher.finish("没有找到图片哦~ 请引用或发送一张含二维码的图片，再发「二维码识别」")
        return

    data = await _get_image_bytes(seg)
    if not data:
        await matcher.finish("图片获取失败，无法识别~")
        return

    if not _HAS_DECODE:
        await matcher.finish(
            "二维码识别功能未启用：请在 bot 环境安装 zbar 系统库与 pyzbar\n"
            "· macOS: brew install zbar\n"
            "· Linux: apt-get install libzbar0\n"
            "· pip install pyzbar"
        )
        return

    try:
        img = Image.open(io.BytesIO(data))
        results = _qr_decode(img)
    except Exception as e:
        logger.error(f"二维码: 识别失败: {e}")
        await matcher.finish("二维码识别失败：图片无法解析~")
        return

    if not results:
        await matcher.finish("这张图片里没有识别到二维码~")
        return

    texts = []
    for r in results:
        try:
            texts.append(r.data.decode("utf-8"))
        except Exception:
            texts.append(str(r.data))

    lines = [f"🔍 识别到 {len(texts)} 个二维码："]
    for i, t in enumerate(texts, 1):
        lines.append(f"{i}. {t}")
    await matcher.finish("\n".join(lines))


# ============ 生成二维码 ============


@qr_gen_cmd.handle()
async def handle_qr_gen(matcher: Matcher, event: MessageEvent) -> None:
    text = _plain_text(event.message).strip()
    matched_kw = next(
        (kw for kw in GEN_KEYWORDS if text == kw or text.startswith(kw + " ")),
        None,
    )
    content = _get_qr_text(event, matched_kw or "")

    if not content:
        await matcher.finish(
            "没有找到要生成二维码的文本~ 请引用一条文本消息后发「生成二维码」，\n"
            "或直接发送「生成二维码 你想写的内容」"
        )
        return

    if len(content) > MAX_QR_TEXT_LEN:
        await matcher.finish(f"文本太长了（{len(content)} 字符），上限 {MAX_QR_TEXT_LEN} 字符~")
        return

    if not _HAS_GEN:
        await matcher.finish("二维码生成功能未启用：请 pip install qrcode")
        return

    try:
        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=10,
            border=4,
        )
        qr.add_data(content)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode()
    except Exception as e:
        logger.error(f"二维码: 生成失败: {e}")
        await matcher.finish("二维码生成失败~")
        return

    await matcher.finish(MessageSegment.image(f"base64://{b64}"))
