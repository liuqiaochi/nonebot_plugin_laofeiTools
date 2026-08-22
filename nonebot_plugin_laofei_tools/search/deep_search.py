"""
深度搜图客户端：IQDB / SauceNAO / ascii2d

三个引擎统一返回 ``SearchResult`` 列表，便于命令层做合并转发。
- IQDB：免费、无需 Key，POST 后解析 HTML（booru 二次元源为主）。
- SauceNAO：免费但需 API Key，返回 JSON（Pixiv / Danbooru / 等）。
- ascii2d：免费、无需 Key，POST 后跟随跳转再解析 HTML（二次元特化）。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from io import BytesIO
from typing import List
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from PIL import Image

# ============ 公共定义 ============

_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

IQDB_BASE = "https://iqdb.org"
ASCII2D_BASE = "https://ascii2d.net"
SAUCENAO_BASE = "https://saucenao.com"


@dataclass
class SearchResult:
    """归一化后的单条搜索结果"""

    source: str  # 服务名，如 "IQDB"
    title: str = ""  # 标题 / 来源名
    url: str = ""  # 原始出处链接（可选）
    thumbnail: str = ""  # 缩略图链接（可选）
    similarity: str = ""  # 相似度，如 "93.5%" 或 ""
    extra: str = ""  # 附加信息（标签 / 描述等）


# ============ 工具函数 ============


def _prepare_upload(image_data: bytes, max_dim: int = 2000, quality: int = 90) -> bytes:
    """
    统一转为 RGB JPEG 并限制最大边长，便于上传到各反搜服务。

    各服务对上传体积 / 尺寸有不同限制，先做一轮轻度压缩可避免 413。
    转换失败则原样返回。
    """
    try:
        img = Image.open(BytesIO(image_data))
        if img.mode in ("RGBA", "P", "LA"):
            img = img.convert("RGB")
        if max(img.width, img.height) > max_dim:
            ratio = max_dim / max(img.width, img.height)
            img = img.resize(
                (int(img.width * ratio), int(img.height * ratio)),
                Image.LANCZOS,
            )
        out = BytesIO()
        img.save(out, format="JPEG", quality=quality, optimize=True)
        return out.getvalue()
    except Exception:
        return image_data


def _abs_url(base: str, url: str) -> str:
    """将相对路径补全为绝对 URL"""
    if not url:
        return ""
    if url.startswith(("http://", "https://")):
        return url
    if url.startswith("//"):
        return "https:" + url
    return base.rstrip("/") + url


def _host_of(url: str) -> str:
    """取链接域名，用于无标题时兜底展示来源"""
    try:
        return urlparse(url).netloc or url
    except Exception:
        return url


# ============ IQDB ============


class IQDBClient:
    """IQDB 反向搜图客户端（免费、无需 Key）"""

    def __init__(self, timeout: float = 30.0):
        self._client = httpx.AsyncClient(
            headers={"User-Agent": _DEFAULT_UA, "Referer": IQDB_BASE + "/"},
            timeout=httpx.Timeout(timeout, connect=10.0),
            trust_env=False,
            follow_redirects=True,
        )

    async def search(self, image_data: bytes) -> List[SearchResult]:
        data = _prepare_upload(image_data)
        try:
            resp = await self._client.post(
                IQDB_BASE + "/",
                files={"file": ("image.jpg", data, "image/jpeg")},
            )
            resp.raise_for_status()
            return self._parse(resp.text)
        except Exception as e:
            return [SearchResult(source="IQDB", title=f"搜索失败：{e}")]

    @staticmethod
    def _parse(html: str) -> List[SearchResult]:
        soup = BeautifulSoup(html, "html.parser")
        results: List[SearchResult] = []
        # 结果分布在多个 <table class="match"> 中，第一个是“Your image”
        for table in soup.find_all("table", class_="match"):
            th = table.find("th")
            label = th.get_text(strip=True) if th else ""
            if label.lower().startswith("your"):
                continue

            img = table.find("img")
            thumb = _abs_url(IQDB_BASE, img.get("src", "")) if img else ""

            text = table.get_text(" ", strip=True)
            sim_match = re.search(r"(\d+(?:\.\d+)?)%", text)
            similarity = (sim_match.group(1) + "%") if sim_match else ""

            links = [a["href"] for a in table.find_all("a", href=True) if a["href"].startswith("http")]
            url = links[0] if links else ""

            title = label or "IQDB 结果"
            if links:
                title = f"{label} · {_host_of(links[0])}" if label else _host_of(links[0])

            results.append(
                SearchResult(
                    source="IQDB",
                    title=title,
                    url=url,
                    thumbnail=thumb,
                    similarity=similarity,
                )
            )
        return results

    async def close(self):
        await self._client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.close()


# ============ SauceNAO ============


class SauceNAOClient:
    """SauceNAO 反向搜图客户端（免费、需 API Key）"""

    def __init__(self, timeout: float = 30.0):
        # 仅从环境变量 SAUCENAO_API_KEY 读取；未配置则该引擎不可用
        self.api_key = os.environ.get("SAUCENAO_API_KEY", "")
        self._client = httpx.AsyncClient(
            headers={"User-Agent": _DEFAULT_UA},
            timeout=httpx.Timeout(timeout, connect=10.0),
            trust_env=False,
        )

    async def search(self, image_data: bytes) -> List[SearchResult]:
        if not self.api_key:
            return [
                SearchResult(
                    source="SauceNAO",
                    title="未配置 API Key",
                    extra="未设置环境变量 SAUCENAO_API_KEY（请写入项目根目录 .env 文件），该引擎不可用（不影响 IQDB / ascii2d）",
                )
            ]
        data = _prepare_upload(image_data)
        try:
            resp = await self._client.post(
                SAUCENAO_BASE + "/search.php",
                data={
                    "api_key": self.api_key,
                    "output_type": "2",
                    "numres": "5",
                },
                files={"file": ("image.jpg", data, "image/jpeg")},
            )
            resp.raise_for_status()
            return self._parse(resp.json())
        except Exception as e:
            return [SearchResult(source="SauceNAO", title=f"搜索失败：{e}")]

    @staticmethod
    def _parse(json_data: dict) -> List[SearchResult]:
        results: List[SearchResult] = []
        for item in json_data.get("results", []):
            header = item.get("header", {})
            data = item.get("data", {})

            similarity = header.get("similarity", "")
            thumb = header.get("thumbnail", "")
            title = (
                data.get("title")
                or data.get("source")
                or header.get("index_name", "SauceNAO")
            )
            ext_urls = data.get("ext_urls") or []
            url = ext_urls[0] if ext_urls else (data.get("source") or "")

            results.append(
                SearchResult(
                    source="SauceNAO",
                    title=str(title),
                    url=url,
                    thumbnail=thumb,
                    similarity=(similarity + "%") if similarity else "",
                )
            )
        return results

    async def close(self):
        await self._client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.close()


# ============ ascii2d ============


class Ascii2dClient:
    """ascii2d 反向搜图客户端（免费、无需 Key，二次元特化）"""

    def __init__(self, timeout: float = 30.0):
        self._client = httpx.AsyncClient(
            headers={
                "User-Agent": _DEFAULT_UA,
                "Accept": (
                    "text/html,application/xhtml+xml,application/xml;q=0.9,"
                    "image/avif,image/webp,*/*;q=0.8"
                ),
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Referer": ASCII2D_BASE + "/",
                "Origin": ASCII2D_BASE,
            },
            timeout=httpx.Timeout(timeout, connect=10.0),
            trust_env=False,
            follow_redirects=True,
        )

    async def search(self, image_data: bytes) -> List[SearchResult]:
        data = _prepare_upload(image_data)
        try:
            # 先访问首页建立会话 cookie，规避 Cloudflare 基础 bot 防护（403）
            try:
                await self._client.get(ASCII2D_BASE + "/")
            except Exception:
                pass
            # 上传后服务端 302 跳转到 /search/<hash>，follow_redirects 已自动跟随
            resp = await self._client.post(
                ASCII2D_BASE + "/search",
                files={"file": ("image.jpg", data, "image/jpeg")},
            )
            resp.raise_for_status()
            return self._parse(resp.text)
        except Exception as e:
            return [SearchResult(source="ascii2d", title=f"搜索失败：{e}")]

    @staticmethod
    def _parse(html: str) -> List[SearchResult]:
        soup = BeautifulSoup(html, "html.parser")
        results: List[SearchResult] = []
        items = soup.select("div.item-box") or soup.select("div.item")
        for item in items:
            img = item.select_one(".item-image img") or item.find("img")
            thumb = _abs_url(ASCII2D_BASE, img.get("src", "")) if img else ""

            detail = item.select_one(".item-detail") or item
            links = [
                a["href"]
                for a in detail.find_all("a", href=True)
                if a["href"].startswith("http")
            ]
            url = links[0] if links else ""
            title = _host_of(links[0]) if links else "ascii2d 结果"
            extra = detail.get_text(" ", strip=True)[:150]

            results.append(
                SearchResult(
                    source="ascii2d",
                    title=title,
                    url=url,
                    thumbnail=thumb,
                    extra=extra,
                )
            )
        return results

    async def close(self):
        await self._client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.close()
