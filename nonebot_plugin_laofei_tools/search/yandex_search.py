"""
Yandex Images 反向搜图（以图搜图）

纯 HTTP 爬取，无官方 API：
  1. 把图片转成 JPEG 上传到 Yandex，获得反搜结果页（含 cbir_id）
  2. 解析结果页 div.serp-item 的 data-bem，提取缩略图 / 原图直链 / 来源页
  3. 返回前 N 条（默认 10）

注意：
  - Yandex 对 NSFW 容忍度高于 Google，且反爬强度低于 Google，适合通用以图搜图。
  - 仍可能偶发验证码页面（返回空结果），属正常风控，重试或换图可缓解。
  - 部署机需能直连 yandex.com；若在国内无法访问，需为 httpx 配置代理
    （本客户端 trust_env=False 以与项目一致，需要时改为 True 或注入代理）。
"""
from __future__ import annotations

import asyncio
import base64
import json
from dataclasses import dataclass
from io import BytesIO
from typing import List, Optional
from urllib.parse import parse_qs, urlparse

import logging
import httpx
from bs4 import BeautifulSoup
from PIL import Image

logger = logging.getLogger(__name__)

_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

_HEADERS = {
    "User-Agent": _DEFAULT_UA,
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://yandex.com/images/",
}

YANDEX_BASE = "https://yandex.com"


@dataclass
class YandexResult:
    thumb: str = ""        # 缩略图：裸 base64 字符串（用于 CQ base64://）
    image_url: str = ""    # 原图直链（尽量）
    source: str = ""       # 来源页 URL
    title: str = ""        # 描述/标题


class YandexReverseSearch:
    """Yandex Images 反向搜图客户端（免费、无需 Key）"""

    def __init__(self, timeout: float = 30.0):
        self._client = httpx.AsyncClient(
            headers=_HEADERS,
            timeout=httpx.Timeout(timeout, connect=10.0),
            trust_env=False,
            follow_redirects=True,
        )

    async def __aenter__(self) -> "YandexReverseSearch":
        return self

    async def __aexit__(self, *exc) -> None:
        await self._client.aclose()

    async def search(self, image_data: bytes, limit: int = 10) -> List[YandexResult]:
        jpeg = _to_jpeg(image_data)
        logger.info("[Yandex反搜] 开始：jpeg=%d bytes", len(jpeg))
        try:
            # 1. 先访问首页建立会话 cookie（规避部分风控）
            try:
                await self._client.get(YANDEX_BASE + "/images/", timeout=10.0)
                logger.info("[Yandex反搜] 首页 GET 完成")
            except Exception as e:
                logger.warning("[Yandex反搜] 首页 GET 失败（忽略）: %s", e)

            # 2. 上传图片触发反搜，服务端 302 跳转到带 cbir_id 的结果页
            resp = await self._client.post(
                YANDEX_BASE + "/images/search",
                files={"upfile": ("image.jpg", jpeg, "image/jpeg")},
                data={"rpt": "imageview", "srv": "yandex"},
            )
            logger.info(
                "[Yandex反搜] 上传响应 status=%s, html长度=%d, Location=%r",
                resp.status_code, len(resp.text), resp.headers.get("location"),
            )
            resp.raise_for_status()
            results = self._parse(resp.text)
            logger.info("[Yandex反搜] 初次解析得到 %d 条", len(results))

            # 3. 若仍在上传页（无结果），尝试跟随 Location 跳转
            if not results:
                loc = resp.headers.get("location")
                if loc:
                    if loc.startswith("//"):
                        loc = "https:" + loc
                    logger.info("[Yandex反搜] 跟随 Location 跳转: %s", loc)
                    r2 = await self._client.get(loc, timeout=15.0)
                    results = self._parse(r2.text)
                    logger.info("[Yandex反搜] 跳转后解析得到 %d 条", len(results))

            # 4. 空结果诊断（区分验证码页 / 结构变化 / 真无结果）
            if not results:
                self._diagnose(resp.text)

            # 5. 把缩略图统一解析为内联 base64，便于直接发送
            await asyncio.gather(*[self._resolve_thumb(r) for r in results])

            logger.info("[Yandex反搜] 最终返回 %d 条", len(results))
            return results[:limit]
        except Exception:
            logger.exception("[Yandex反搜] 搜索过程异常")
            return [YandexResult(title="搜索失败，详情见日志")]

    def _diagnose(self, html: str) -> None:
        """结果数为 0 时打印诊断信息，便于定位原因。"""
        soup = BeautifulSoup(html, "html.parser")
        serp = soup.select("div.serp-item")
        bem = soup.select("[data-bem]")
        low = html.lower()
        logger.warning(
            "[Yandex诊断] serp-item 计数=%d, data-bem 元素=%d, 含'img_href'=%d, "
            "含'captcha'=%s, 含'robot'=%s, 含'just moment'=%s, 含'are you a robot'=%s",
            len(serp), len(bem), html.count("img_href"),
            "captcha" in low, "robot" in low, "just moment" in low,
            "are you a robot" in low,
        )
        snippet = " ".join(html[:1000].split())
        logger.warning("[Yandex诊断] HTML 前 1000 字符: %s", snippet)

    # ---------- 解析 ----------

    def _parse(self, html: str) -> List[YandexResult]:
        soup = BeautifulSoup(html, "html.parser")
        results: List[YandexResult] = []

        for item in soup.select("div.serp-item"):
            bem_raw = item.get("data-bem")
            if not bem_raw:
                continue
            try:
                bem = json.loads(bem_raw)
            except Exception:
                continue

            node = bem.get("serp-item", bem)
            if not isinstance(node, dict):
                continue

            # 缩略图：data-bem 内嵌，可能是 base64 data uri 或 CDN url
            thumb = node.get("thumb", {}) or {}
            thumb_url = thumb.get("url", "") if isinstance(thumb, dict) else ""

            # 原图直链：藏在 img_href 的 img_url 查询参数里
            img_href = node.get("img_href", "")
            orig = _extract_img_url(img_href)

            # 来源页：serp-item 内的 a.link
            link = item.select_one("a.link")
            source_url = link.get("href", "") if link else (orig or img_href)
            if source_url.startswith("//"):
                source_url = "https:" + source_url

            snippet = node.get("snippet", {}) or {}
            title = snippet.get("title", "") if isinstance(snippet, dict) else ""
            domain = node.get("domain", "") or ""

            r = YandexResult(
                thumb=thumb_url,
                image_url=orig or source_url,
                source=source_url,
                title=(title or domain),
            )
            if r.thumb or r.image_url:
                results.append(r)

        return results

    async def _resolve_thumb(self, r: YandexResult) -> None:
        """把缩略图规范为裸 base64 字符串，便于拼 CQ 码发送。"""
        t = r.thumb
        if not t:
            return
        if t.startswith("data:image"):
            # 内联 base64，提取裸串
            r.thumb = t.split("base64,", 1)[-1]
            return
        if t.startswith("http") or t.startswith("//"):
            b64 = await self._download_b64(t)
            r.thumb = b64  # 失败则为 ""，命令侧退化为纯文字
            return
        # 其它情况（已是裸 base64 等）保留

    async def _download_b64(self, url: str) -> str:
        """下载缩略图并压缩为 ≤500KB 的 JPEG base64（裸串）。"""
        try:
            if url.startswith("//"):
                url = "https:" + url
            resp = await self._client.get(url, timeout=15.0)
            resp.raise_for_status()
            img = Image.open(BytesIO(resp.content))
            if img.mode in ("RGBA", "P", "LA"):
                img = img.convert("RGB")
            # 限制尺寸，避免 base64 过大
            max_side = 600
            if max(img.width, img.height) > max_side:
                ratio = max_side / max(img.width, img.height)
                img = img.resize(
                    (int(img.width * ratio), int(img.height * ratio)),
                    Image.LANCZOS,
                )
            q = 85
            while True:
                out = BytesIO()
                img.save(out, format="JPEG", quality=q, optimize=True)
                data = out.getvalue()
                if len(data) <= 500 * 1024 or q <= 20:
                    break
                q -= 10
            return base64.b64encode(data).decode()
        except Exception:
            return ""


# ---------- 工具函数 ----------

def _extract_img_url(img_href: str) -> str:
    """从 Yandex 图片查看页链接中解析出原图直链。"""
    if not img_href:
        return ""
    try:
        qs = parse_qs(urlparse(img_href).query)
        for key in ("img_url", "imgur", "url"):
            v = qs.get(key)
            if v:
                return v[0]
    except Exception:
        pass
    return img_href


def _to_jpeg(image_data: bytes) -> bytes:
    """统一转成 JPEG，兼容 RGBA/P 等模式。"""
    img = Image.open(BytesIO(image_data))
    if img.mode in ("RGBA", "P", "LA"):
        img = img.convert("RGB")
    out = BytesIO()
    img.save(out, format="JPEG", quality=92, optimize=True)
    return out.getvalue()
