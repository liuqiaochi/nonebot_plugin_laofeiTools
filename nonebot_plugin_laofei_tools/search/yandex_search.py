"""
Yandex Images 反向搜图（以图搜图）

纯 HTTP 爬取，无官方 API：
  1. 把图片转成 JPEG 上传到 Yandex，获得反搜结果页（含 cbir_id）
  2. 解析结果页 div.serp-item 的 data-bem，提取缩略图 / 原图直链 / 来源页
  3. 返回前 N 条（默认 10）

注意：
  - Yandex 对 NSFW 容忍度高于 Google，且反爬强度低于 Google，适合通用以图搜图。
  - 反搜固定使用 yandex.com（国际版）。yandex.com 对多数非俄区/自动化请求会
    返回 "The service is under construction" 限制页，此时需通过环境变量
    YANDEX_PROXY 配置代理（如 http://host:port 或 socks5://...）后方可正常返回。
  - 若仍返回拦截页，多为部署机地域/数据中心 IP 被 Yandex 限制，需走
    代理或真实浏览器引擎（Playwright）才能解决。
  - 代理通过环境变量 YANDEX_PROXY 配置（可放根目录 .env，如
    http://host:port 或 socks5://user:pass@host:port）；为空则直连。
    代理仅作用于本反搜客户端，不影响插件其它网络请求（trust_env 仍保持 False）。
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
from dataclasses import dataclass
from io import BytesIO
from typing import List, Optional
from urllib.parse import parse_qs, urlparse

import logging
from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv())  # 允许在 .env 配置 YANDEX_PROXY
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
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://yandex.com/images/",
    "Origin": "https://yandex.com",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "DNT": "1",
    "Sec-CH-UA": '"Chromium";v="120", "Google Chrome";v="120", "Not?A_Brand";v="24"',
    "Sec-CH-UA-Mobile": "?0",
    "Sec-CH-UA-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

# 反搜主域名：固定使用 yandex.com（国际版）。
# 注意：yandex.com 对非俄区/自动化请求常返回 "The service is under construction"
# 限制页，需配合 YANDEX_PROXY 代理方可正常返回结果。
YANDEX_DOMAINS = ["https://yandex.com"]


@dataclass
class YandexResult:
    thumb: str = ""        # 缩略图：裸 base64 字符串（用于 CQ base64://）
    image_url: str = ""    # 原图直链（尽量）
    source: str = ""       # 来源页 URL
    title: str = ""        # 描述/标题


class YandexReverseSearch:
    """Yandex Images 反向搜图客户端（免费、无需 Key）"""

    def __init__(self, timeout: float = 30.0):
        # 代理：环境变量 YANDEX_PROXY（如 http://host:port 或 socks5://...），
        # 可写在项目根目录 .env；为空则直连。仅作用于本反搜客户端。
        proxy = os.environ.get("YANDEX_PROXY", "").strip()
        self._client = httpx.AsyncClient(
            headers=_HEADERS,
            timeout=httpx.Timeout(timeout, connect=10.0),
            trust_env=False,
            follow_redirects=True,
            proxy=proxy or None,
        )
        logger.info("[Yandex反搜] 代理=%s", proxy or "(无，直连)")

    async def __aenter__(self) -> "YandexReverseSearch":
        return self

    async def __aexit__(self, *exc) -> None:
        await self._client.aclose()

    async def _warmup(self, base: str) -> None:
        """访问根域名与图片首页，建立 yandexuid 等会话 cookie。"""
        for url in (base + "/", base + "/images/"):
            try:
                await self._client.get(url, timeout=10.0)
                logger.info("[Yandex反搜] 预热 GET 完成: %s", url)
            except Exception as e:
                logger.warning("[Yandex反搜] 预热 GET 失败（忽略）: %s %s", url, e)

    async def _upload(self, base: str, jpeg: bytes) -> str:
        """上传图片并取回结果页 HTML（跟随 302）。返回 HTML 文本。"""
        resp = await self._client.post(
            base + "/images/search",
            files={"upfile": ("image.jpg", jpeg, "image/jpeg")},
            data={"rpt": "imageview", "srv": "yandex"},
        )
        logger.info(
            "[Yandex反搜] 上传响应[%s] status=%s, 最终URL=%s, html长度=%d, Location=%r",
            base, resp.status_code, str(resp.url), len(resp.text),
            resp.headers.get("location"),
        )
        resp.raise_for_status()
        return resp.text

    async def search(self, image_data: bytes, limit: int = 10) -> List[YandexResult]:
        jpeg = _to_jpeg(image_data)
        logger.info("[Yandex反搜] 开始：jpeg=%d bytes", len(jpeg))
        try:
            last_html = ""
            results: List[YandexResult] = []

            for base in YANDEX_DOMAINS:
                await self._warmup(base)
                html = await self._upload(base, jpeg)
                results = self._parse(html)
                logger.info("[Yandex反搜][%s] 解析得到 %d 条", base, len(results))

                if results:
                    break
                # 非拦截页的空结果 = 真没搜到，不必换域名
                if not self._is_block_page(html):
                    last_html = html
                    break
                logger.warning("[Yandex反搜] %s 返回拦截页，尝试下一域名", base)
                last_html = html

            # 空结果诊断（区分验证码页 / 结构变化 / 真无结果 / 拦截页）
            if not results:
                self._diagnose(last_html or html)

            # 把缩略图统一解析为内联 base64，便于直接发送
            await asyncio.gather(*[self._resolve_thumb(r) for r in results])

            logger.info("[Yandex反搜] 最终返回 %d 条", len(results))
            return results[:limit]
        except Exception:
            logger.exception("[Yandex反搜] 搜索过程异常")
            return [YandexResult(title="搜索失败，详情见日志")]

    @staticmethod
    def _is_block_page(html: str) -> bool:
        """判断是否为 Yandex 拦截/降级页（非验证码、非结果）。"""
        low = html.lower()
        markers = [
            "we don't recognize", "do not recognize", "something went wrong",
            "access denied", "not available in your region", "запросы",
            "похоже, мы вас не узнали", "captcha", "robot",
            # yandex.com 国际版对多数非俄区请求返回的限制页
            "under construction", "we will be back soon", "service is under",
        ]
        if any(m in low for m in markers):
            return True
        # 结果页必有 serp-item / data-bem；都没有则极可能是拦截页
        if "serp-item" not in html and "data-bem" not in html:
            return True
        return False

    def _diagnose(self, html: str) -> None:
        """结果数为 0 时打印诊断信息，便于定位原因。"""
        soup = BeautifulSoup(html, "html.parser")
        serp = soup.select("div.serp-item")
        bem = soup.select("[data-bem]")
        low = html.lower()
        h1 = soup.select_one("h1")
        h1_text = h1.get_text(strip=True) if h1 else ""
        logger.warning(
            "[Yandex诊断] serp-item 计数=%d, data-bem 元素=%d, 含'img_href'=%d, "
            "含'captcha'=%s, 含'robot'=%s, 含'just moment'=%s, 含'are you a robot'=%s",
            len(serp), len(bem), html.count("img_href"),
            "captcha" in low, "robot" in low, "just moment" in low,
            "are you a robot" in low,
        )
        logger.warning("[Yandex诊断] 页面 h1 文案=%r", h1_text)
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
