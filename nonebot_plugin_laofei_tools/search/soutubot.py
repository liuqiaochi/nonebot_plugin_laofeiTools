"""
soutubot.moe API 封装（适配 2026-09 站点改版）

改版后站点后端为 Rust 实现（imsearch），
接口契约见 https://soutubot.moe/openapi.json

实测结论（2026-09-09）：
- **无需任何鉴权**：旧版基于 window.GLOBAL.m 动态生成 X-API-KEY 的机制已废弃，
  新版 OpenAPI 中无任何 securitySchemes，实测不带 key 直接 200。
- POST /api/search **同步**返回完整结果，无需再用 result_id 二次查询。
- GET  /api/results/{id} 可按 result_id 回查，返回结构与 /api/search 完全一致。
- multipart 字段：file(必填) / factor(默认1.2) / metadata_mode(默认display) / top_k(可选)。

新版响应 -> 统一结构的字段映射（见 _normalize_result）：
    results[]                              -> data[]
    score（内部评分）                       -> similarity（百分比，见下方说明）
    path_segments[0].source_key            -> source
    path_segments[0].thumbnail_url         -> previewImageUrl（已是绝对 URL）
    path_segments[0].source_url/page_url   -> url（已是绝对 URL，无需再拼域名）
    timing.total_ms                        -> executionTime

关于「相似度百分比」的说明（2026-09-09 二次修正，以此为准）：
- **score 本身就是百分比相似度**，直接原值展示即可，不要做任何换算。
  证据：线上 bot 实际输出「相似度: 95.8%」，彼时代码即 `f"{score:.1f}%"`。
- 【曾犯的错误，勿重蹈】一度用四叶草图标自测，得到 score 仅 15~45，便误判
  score 非百分比、还加了一层线性映射。实为该测试图与库内匹配度本就低；
  换成真实图片后 score 可达 95+。**score 区间随查询图而异，低分是真实低匹配度。**
- segment.similarity_groups[].best_similarity 是另一回事：仅在命中已知
  「持久相似组」时存在（实测 50 条仅 6 条有，恒 98%+），语义是「组内最佳」，
  **不可当逐条相似度用**，否则排序错乱。

metadata 结构（display_kind 决定可用字段）：
- "doujinshi"（nhentai / ehentai / jmcomic / panda）：
    metadata.source.id/url、metadata.title.primary/japanese_or_alias、
    metadata.facts.page_count/language/category/chapter
- "booru"（gelbooru / danbooru / zerochan …）：
    仅 metadata.post.post_id/post_url 与 metadata.asset/tags，无标题·页数·语言
- pixiv：metadata 整体为 null，仅有 external_id
"""

from io import BytesIO
from typing import Optional

import httpx
from PIL import Image

BASE_URL = "https://soutubot.moe"
SEARCH_API = f"{BASE_URL}/api/search"
RESULT_API = f"{BASE_URL}/api/results"

# 默认 User-Agent
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

# 仅本子/漫画类具备完整元数据（标题 / 页数 / 语言），图库类可据此过滤
KIND_DOUJINSHI = "doujinshi"

# source_key -> 展示名
SOURCE_DISPLAY = {
    "nhentai": "NHentai",
    "ehentai": "E-Hentai",
    "jmcomic": "JMComic",
    "panda": "Panda Chaika",
    "gelbooru": "Gelbooru",
    "danbooru": "Danbooru",
    "zerochan": "Zerochan",
    "konachan": "Konachan",
    "yande": "Yande.re",
    "rule34": "Rule34",
    "sankaku": "Sankaku",
    "pixiv": "Pixiv",
    "twitter": "Twitter/X",
    "kemono": "Kemono",
    "nijie": "Nijie",
}

# 语言码 -> 中文名（nhentai / ehentai 的 facts.language 为英文小写）
LANGUAGE_CN = {
    "chinese": "中文",
    "japanese": "日文",
    "english": "英文",
    "korean": "韩文",
    "french": "法文",
    "german": "德文",
    "spanish": "西班牙文",
    "italian": "意大利文",
    "russian": "俄文",
    "portuguese": "葡萄牙文",
    "thai": "泰文",
    "vietnamese": "越南文",
    "indonesian": "印尼文",
    "translated": "翻译",
    "rewrite": "改写",
    "original": "原作",
    "unknown": "未知",
}


def _display_source(source_key: str) -> str:
    """来源标识 -> 展示名，未知来源则首字母大写兜底"""
    if not source_key:
        return "未知来源"
    if source_key in SOURCE_DISPLAY:
        return SOURCE_DISPLAY[source_key]
    return source_key.capitalize()


def _language_cn(language: str) -> str:
    """语言码 -> 中文名，未收录则返回原值"""
    if not language:
        return ""
    return LANGUAGE_CN.get(language.strip().lower(), language)


def _first_segment(result_item: dict) -> dict:
    """取结果的首个 path_segments，容错缺字段的情况"""
    segments = result_item.get("path_segments") or []
    return segments[0] if segments else {}


def _build_title(result_item: dict, segment: dict) -> str:
    """
    构造可读标题。

    新版响应不再提供 title 字段，改用资源原始路径的最后两级
    （对 nhentai 类目而言即「本子目录名/文件名」，信息量足够）。
    """
    raw_path = result_item.get("raw_path") or segment.get("normalized_path") or ""
    parts = [p for p in raw_path.replace("\\", "/").split("/") if p]
    if not parts:
        return segment.get("source_key") or "未知标题"
    return "/".join(parts[-2:])


def _pick_url(segment: dict) -> str:
    """
    挑选最合适的跳转链接（新版返回的均为绝对 URL）。

    优先级：页级链接（可直达匹配页，如 nhentai.net/g/366466/18/）
            > 本子作品主页（metadata.source.url）
            > 图库帖子页（metadata.post.post_url）
            > 来源页（source_url）
    """
    metadata = segment.get("metadata") or {}
    src = metadata.get("source") or {}
    post = metadata.get("post") or {}
    return (
        segment.get("page_url")
        or src.get("url")
        or post.get("post_url")
        or segment.get("source_url")
        or ""
    )


def _extract_metadata(segment: dict, item: dict) -> dict:
    """
    从 segment 中提取展示所需的完整元数据。

    按 display_kind 分别处理：
    - doujinshi：可拿到作品 ID、完整标题（含日文/别名）、页数/章节、语言
    - booru 等：只有帖子 ID 与链接，无标题/页数/语言
    """
    metadata = segment.get("metadata") or {}
    kind = metadata.get("display_kind") or ""
    src = metadata.get("source") or {}
    post = metadata.get("post") or {}
    title_obj = metadata.get("title") or {}
    facts = metadata.get("facts") or {}

    primary = title_obj.get("primary") or ""
    alias = title_obj.get("japanese_or_alias") or ""

    # 作品 ID：本子类用 source.id，图库类用 post.post_id，最后兜底 external_id
    work_id = str(src.get("id") or post.get("post_id") or segment.get("external_id") or "")

    # 标题优先展示「原名」（日文/原始语言标题），更符合阅读习惯；
    # 原名缺失时退回译名(primary)，再无则退回路径兜底。
    title = alias or primary or _build_title(item, segment)

    # primary 与标题不同时保留为「译名」备用（当前不展示，保留字段便于日后启用）
    alt_title = primary if (primary and primary != title) else ""

    return {
        "kind": kind,
        "workId": work_id,
        "title": title,
        "altTitle": alt_title,
        "pageCount": facts.get("page_count"),
        "chapter": facts.get("chapter"),
        "language": facts.get("language") or "",
        "languageCn": _language_cn(facts.get("language") or ""),
        "category": facts.get("category") or "",
    }


def _normalize_result(raw: dict) -> dict:
    """
    将新版响应规整为上层使用的统一结构，隔离站点字段变化的影响。

    统一结构：
    {
        "data": [{
            "score": float,             # 相似度百分比（API 原值，排序依据）
            "similarity": float,        # 同 score，直接原值展示，不做换算
            "source": str,              # 来源标识，如 nhentai / gelbooru
            "sourceName": str,          # 来源展示名，如 NHentai
            "kind": str,                # doujinshi / booru / ""
            "workId": str,              # 作品 ID
            "title": str,               # 完整标题（原名优先，不截断）
            "altTitle": str,            # 译名 primary（与原名不同时才有，当前不展示）
            "pageCount": int | None,    # 页数
            "chapter": str | None,      # 章节（部分来源用章节而非页数）
            "language": str,            # 语言码原文，如 chinese
            "languageCn": str,          # 语言中文名，如 中文
            "category": str,            # 分类，如 Doujinshi / Non-H
            "previewImageUrl": str,     # 绝对 URL
            "url": str,                 # 详情地址（绝对 URL，优先页级）
        }],
        "executionTime": int,
        "resultId": str,
    }
    """
    results = raw.get("results") or []

    data = []
    for item in results:
        segment = _first_segment(item)
        source_key = segment.get("source_key") or "unknown"
        score = item.get("score", 0)
        # score 本身即百分比相似度，原值展示（不做任何换算）
        data.append({
            "score": score,
            "similarity": score,
            "source": source_key,
            "sourceName": _display_source(source_key),
            "title": "",
            "altTitle": "",
            "pageCount": None,
            "chapter": None,
            "language": "",
            "languageCn": "",
            "category": "",
            "workId": "",
            "kind": "",
            **_extract_metadata(segment, item),
            "previewImageUrl": segment.get("thumbnail_url") or "",
            "url": _pick_url(segment),
        })

    # 按原始 score 降序（实测已有序，此处兜底；与 similarity 单调等价）
    data.sort(key=lambda x: x.get("score", 0), reverse=True)

    return {
        "data": data,
        "executionTime": (raw.get("timing") or {}).get("total_ms", 0),
        "resultId": raw.get("result_id") or "",
    }


class SoutubotClient:
    """soutubot.moe 客户端（新版 API，无需鉴权）"""

    def __init__(self, user_agent: str = DEFAULT_UA):
        self.user_agent = user_agent
        self._client = httpx.AsyncClient(
            headers={
                "User-Agent": user_agent,
                "Referer": BASE_URL,
            },
            timeout=httpx.Timeout(60.0, connect=15.0),
            trust_env=False,
        )

    def _compress_image(self, image_data: bytes, target_size: int = 1024 * 1024) -> bytes:
        """
        上传前将图片压缩到 target_size（默认 1MB）以内，避免服务器返回 413。

        策略：
        1. 已为支持格式且体积达标 -> 直接返回；
        2. 限制最大边长，避免超大分辨率拖垮压缩；
        3. 迭代降低 JPEG 质量（90 -> 20）直到达标；
        4. 仅靠质量仍不达标则逐步缩小尺寸后重试；
        5. 极端情况仍无法达标，返回当前能生成的最小体积结果。
        """
        # 已达标且为可直接上传的格式，原样返回
        if len(image_data) <= target_size:
            try:
                img = Image.open(BytesIO(image_data))
                if img.format in ("JPEG", "PNG", "WEBP"):
                    return image_data
            except Exception:
                pass

        try:
            img = Image.open(BytesIO(image_data))
        except Exception:
            return image_data

        # 统一转 RGB（处理 RGBA / P 等带透明通道或调色板的模式）
        if img.mode in ("RGBA", "P", "LA"):
            img = img.convert("RGB")

        # 限制最大边长，防止超大分辨率导致单张难以压到目标
        max_dim = 2000
        if max(img.width, img.height) > max_dim:
            ratio = max_dim / max(img.width, img.height)
            img = img.resize(
                (int(img.width * ratio), int(img.height * ratio)),
                Image.LANCZOS,
            )

        def _save(im, quality: int) -> bytes:
            buf = BytesIO()
            im.save(buf, format="JPEG", quality=quality, optimize=True, progressive=True)
            return buf.getvalue()

        # 第一遍：仅降质量
        result = None
        quality = 90
        while quality >= 20:
            data = _save(img, quality)
            if len(data) <= target_size:
                result = data
                break
            quality -= 10

        # 第二遍：缩小尺寸 + 降质量（应对高分辨率大图）
        if result is None:
            scale = 0.9
            while scale > 0.3:
                w, h = int(img.width * scale), int(img.height * scale)
                small = img.resize((w, h), Image.LANCZOS)
                for q in range(80, 10, -10):
                    data = _save(small, q)
                    if len(data) <= target_size:
                        result = data
                        break
                if result is not None:
                    break
                scale -= 0.1

        # 兜底：返回能生成的最小体积结果（交由上传阶段处理）
        if result is None:
            result = _save(img, 20)

        return result

    async def search(
        self,
        image_data: bytes,
        strict_mode: bool = False,
    ) -> dict:
        """
        上传图片搜索（同步返回完整结果）

        Args:
            image_data: 图片二进制数据
            strict_mode: 是否使用严格模式（factor=1.4，否则1.2）

        Returns:
            统一结构的结果，见 _normalize_result
        """
        compressed = self._compress_image(image_data)

        factor = 1.4 if strict_mode else 1.2
        files = {
            "file": ("image.jpg", BytesIO(compressed), "image/jpeg"),
        }
        data = {
            "factor": str(factor),
            "metadata_mode": "display",
        }

        resp = await self._client.post(
            SEARCH_API,
            data=data,
            files=files,
        )
        resp.raise_for_status()

        raw = resp.json()
        if not isinstance(raw, dict) or raw.get("status") != "ok":
            raise RuntimeError(f"搜图服务返回异常状态：{str(raw)[:200]}")

        return _normalize_result(raw)

    async def get_result(self, result_id: str) -> dict:
        """
        按 result_id 回查历史搜索结果

        Args:
            result_id: 搜索结果 ID

        Returns:
            统一结构的结果，见 _normalize_result
        """
        resp = await self._client.get(f"{RESULT_API}/{result_id}")
        resp.raise_for_status()
        return _normalize_result(resp.json())

    async def close(self):
        """关闭 HTTP 客户端"""
        await self._client.aclose()


# 单例模式，方便复用
_client_instance: Optional[SoutubotClient] = None


async def get_client() -> SoutubotClient:
    """获取 SoutubotClient 实例"""
    global _client_instance
    if _client_instance is None:
        _client_instance = SoutubotClient()
    return _client_instance
