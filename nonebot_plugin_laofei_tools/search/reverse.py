"""
反向图片搜索：多引擎查找图片出处与作者（含 X 原帖）。

移植自 astrbot_plugin_bitchfinder，按本插件 NoneBot2 环境改造：
- 原实现拆成 engines.py / fuse.py / sauce.py 三个文件，此处合并为单模块
  （sauce.py 的 Yandex 客户端已被 engines.py 取代，仅哈希函数仍有价值）
- 网络层由 aiohttp 改为 httpx（与插件其余模块 soutubot.py 一致，不新增依赖）
- 置信度模型、正则提取、融合排序全部保持原实现，一处未改

为什么不用 soutubot：soutubot.moe 是动漫/本子垂直库，
搜不到 X.com / 微博 / 小红书 等站点；此处走通用搜索引擎。

引擎状态（2026-10 实测，沿用原作者结论）：
- Yandex (.com/.ru)：可用，无 key，同一 CBIR 后端，双域名互为备份
- SauceNAO：官方 API，需免费 api_key（100 次/天）；匿名请求当前被 CF 拦
- ascii2d：全站 Cloudflare 挑战，需 cf_clearance cookie
- Google Cloud Vision（WEB_DETECTION）：付费，需 GCP key
缺 key / 被风控的引擎自动跳过，不影响其他引擎。
"""

import asyncio
import base64
import io
import json
import re
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import quote, unquote

import httpx
from PIL import Image

from nonebot import get_driver, logger


# ========== 配置读取（全部可选） ==========

# 逻辑名 -> (NoneBot 配置项, 默认值)
_CFG_ATTRS = {
    "timeout": ("longge_reverse_timeout", 20),
    "proxy": ("longge_reverse_proxy", ""),
    "max_results": ("longge_reverse_max_results", 5),
    "saucenao_api_key": ("saucenao_api_key", ""),
    "gcv_api_key": ("gcv_api_key", ""),
    "ascii2d_cf_clearance": ("ascii2d_cf_clearance", ""),
}


def load_config() -> dict:
    """从 NoneBot 配置读取反搜参数，缺失回退默认值（每次调用实时读取）。"""
    cfg = {k: default for k, (_, default) in _CFG_ATTRS.items()}
    try:
        driver_config = get_driver().config
    except Exception:
        driver_config = None
    if driver_config is None:
        return cfg
    for key, (attr, _) in _CFG_ATTRS.items():
        try:
            val = getattr(driver_config, attr, None)
            if val is not None:
                cfg[key] = val
        except Exception:
            pass
    return cfg


def engine_status(cfg: dict) -> list:
    """返回各引擎可用性清单（启动时打日志用）。"""
    return [
        "yandex",
        "saucenao" if cfg.get("saucenao_api_key") else "-saucenao(无key)",
        "ascii2d" if cfg.get("ascii2d_cf_clearance") else "-ascii2d(无cookie)",
        "gcv" if cfg.get("gcv_api_key") else "-gcv(无key)",
    ]


# ========== 统一数据结构 ==========

@dataclass
class PostCandidate:
    """一个候选出处：可能带作者、可能带具体帖子 URL。"""

    engine: str            # yandex / saucenao / ascii2d / gcv
    page_url: str          # 出处页面（帖子或镜像页）
    title: str = ""
    author_handle: str = ""      # 不含 @
    author_platform: str = ""    # X/Twitter / Pixiv / ...
    post_url: str = ""           # 明确的原帖 URL（区别于镜像页）
    description: str = ""        # 正文摘要（供二次署名提取）
    image_url: str = ""          # 候选原图
    weight: float = 0.0          # 引擎内部证据权重（融合前）


class EngineError(Exception):
    """引擎级失败（被调用方捕获后降级）。"""


_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def _make_client(timeout: int, proxy: str = "", cookies: Optional[dict] = None) -> httpx.AsyncClient:
    """构造 httpx 客户端。

    verify=False：Yandex 证书链在国内环境偶有不完整（对应原实现 ssl=False）
    trust_env=False：不读系统代理环境变量，统一走显式 proxy 配置
    proxy 参数名在 httpx>=0.28 为 proxy / 旧版为 proxies，两者兼容。
    """
    kwargs = dict(
        timeout=httpx.Timeout(float(timeout), connect=10.0),
        headers={"User-Agent": _UA},
        verify=False,
        trust_env=False,
        follow_redirects=True,  # Yandex / ascii2d 结果页可能 302
    )
    if cookies:
        kwargs["cookies"] = cookies
    if proxy:
        try:
            return httpx.AsyncClient(proxy=proxy, **kwargs)      # httpx >= 0.28
        except TypeError:
            return httpx.AsyncClient(proxies=proxy, **kwargs)    # httpx < 0.28
    return httpx.AsyncClient(**kwargs)


# ---------------------------------------------------------------------------
# Yandex（.com 与 .ru 同一 CBIR 后端，双域名互备）
# ---------------------------------------------------------------------------

_UPLOAD_REQUEST = json.dumps({"blocks": [{"block": "b-page_type_search-by-image__link"}]})
_YANDEX_HOSTS = ("yandex.com", "yandex.ru")


async def search_yandex(
    image_bytes: bytes,
    *,
    timeout: int = 20,
    proxy: str = "",
    host: str = "yandex.com",
) -> tuple:
    """上传图片到 Yandex 并解析相似图结果页。

    返回 (result_page_url, candidates)。两域名同后端，一个失败自动换另一个。
    """
    last_err: Optional[Exception] = None
    hosts = (host,) + tuple(h for h in _YANDEX_HOSTS if h != host)
    for h in hosts:
        try:
            return await _search_yandex_on(image_bytes, h, timeout=timeout, proxy=proxy)
        except Exception as e:  # noqa: BLE001 - 引擎级降级
            last_err = e
    raise EngineError(f"Yandex 双域名均失败: {last_err}")


async def _search_yandex_on(image_bytes: bytes, host: str, *, timeout: int, proxy: str) -> tuple:
    async with _make_client(timeout, proxy) as client:
        resp = await client.post(
            f"https://{host}/images/search",
            params={"rpt": "imageview", "format": "json", "request": _UPLOAD_REQUEST},
            files={"upfile": ("blob", image_bytes, "image/jpeg")},
        )
        resp.raise_for_status()
        blocks = resp.json().get("blocks") or []

        params = (blocks[0] or {}).get("params", {}) if blocks else {}
        cbir_id = params.get("cbirId")
        original_url = params.get("originalImageUrl", "")
        if not cbir_id:
            raise EngineError("上传响应中未找到 cbirId")

        result_url = (
            f"https://{host}/images/search?rpt=imageview&cbir_page=similar&cbir_id="
            + quote(f"url={original_url}&cbir_id={cbir_id}", safe="=&")
        )
        resp2 = await client.get(result_url)
        resp2.raise_for_status()
        html = resp2.text

    return result_url, _parse_yandex_html(html)


def _parse_yandex_html(html: str) -> list:
    """从结果页 HTML 中提取 cbirSites JSON 数组（结构变更时返回空列表而非抛异常）。"""
    txt = html.replace("&quot;", '"').replace("&#39;", "'").replace("&amp;", "&")
    i = txt.find('"cbirSites":{"sites"')
    if i == -1:
        return []
    j = txt.find("[", i)
    if j == -1:
        return []

    depth = 0
    end = -1
    for k in range(j, len(txt)):
        c = txt[k]
        if c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                end = k
                break
    if end == -1:
        return []
    try:
        raw_list = json.loads(txt[j : end + 1])
    except json.JSONDecodeError:
        return []

    out: list = []
    seen: set = set()
    for item in raw_list:
        page_url = re.sub(
            r"[?&]utm_medium=organic(&|%26|&amp;)?utm_source=yandexsmartcamera.*$",
            "",
            item.get("url", ""),
        ).rstrip("&?")
        if not page_url or page_url in seen:
            continue
        seen.add(page_url)
        orig = item.get("originalImage", {}) or {}
        thumb = item.get("thumb", {}) or {}
        out.append(
            PostCandidate(
                engine="yandex",
                page_url=page_url,
                title=item.get("title", ""),
                description=item.get("description", ""),
                image_url=orig.get("url", "") or thumb.get("url", ""),
            )
        )
    return out


# ---------------------------------------------------------------------------
# SauceNAO：官方 API，直接返回作者与原帖 URL（X status / Pixiv artwork）
# ---------------------------------------------------------------------------


async def search_saucenao(
    image_url: str,
    *,
    api_key: str,
    timeout: int = 25,
    proxy: str = "",
) -> list:
    """调用 SauceNAO API（db=999 全库）。image_url 需为公网可访问 URL。"""
    if not api_key:
        raise EngineError("SauceNAO 需要 api_key（saucenao.com 免费注册，100 次/天）")
    params = {
        "db": 999,
        "output_type": 2,
        "numres": 16,
        "api_key": api_key,
        "url": image_url,
    }
    async with _make_client(timeout, proxy) as client:
        resp = await client.get("https://saucenao.com/search.php", params=params)
        if resp.status_code != 200:
            raise EngineError(f"SauceNAO HTTP {resp.status_code}（可能被 CF 拦或 key 无效）")
        data = resp.json()

    if data.get("header", {}).get("status") not in (0, None):
        raise EngineError(f"SauceNAO status={data['header'].get('status')}")

    out: list = []
    for result in data.get("results", []):
        d = result.get("data", {})
        ext_urls = d.get("ext_urls", []) or []
        handle = (
            d.get("twitter_user_handle")
            or d.get("author_name")
            or d.get("member_name")
            or ""
        )
        platform = ""
        post_url = ""
        for u in ext_urls:
            if "twitter.com" in u or "x.com" in u:
                platform = "X/Twitter"
                post_url = u
                break
            if "pixiv.net" in u:
                platform = "Pixiv"
                post_url = u
        if not platform and ext_urls:
            post_url = ext_urls[0]
        out.append(
            PostCandidate(
                engine="saucenao",
                page_url=post_url or (ext_urls[0] if ext_urls else ""),
                post_url=post_url,
                title=d.get("title", ""),
                author_handle=handle.lstrip("@"),
                author_platform=platform or ("Pixiv" if d.get("pixiv_id") else ""),
                weight=0.5,
            )
        )
    return out


# ---------------------------------------------------------------------------
# ascii2d：无官方 API，解析彩色搜索结果页；当前被 CF 拦，支持 cf_clearance
# ---------------------------------------------------------------------------


async def search_ascii2d(
    image_bytes: bytes,
    *,
    timeout: int = 30,
    proxy: str = "",
    cf_clearance: str = "",
) -> list:
    """上传到 ascii2d 并解析彩色结果页中的原帖链接。"""
    cookies = {"cf_clearance": cf_clearance} if cf_clearance else {}
    async with _make_client(timeout, proxy, cookies=cookies) as client:
        resp = await client.post(
            "https://ascii2d.net/search/file",
            files={"file": ("blob", image_bytes, "image/jpeg")},
        )
        html = resp.text
        if resp.status_code == 403 or "Just a moment" in html[:2000]:
            raise EngineError(
                "ascii2d 被 Cloudflare 拦截：可配置有效的 cf_clearance cookie"
            )
        resp.raise_for_status()

    out: list = []
    seen: set = set()
    for m in re.finditer(r'<div class="detail-box[^"]*"[^>]*>(.*?)</div>', html, re.S):
        block = m.group(1)
        link_m = re.search(r'href="(https?://[^"]+)"[^>]*>', block)
        if not link_m:
            continue
        url = link_m.group(1)
        if url in seen or "ascii2d" in url:
            continue
        seen.add(url)
        handle = ""
        platform = ""
        um = re.search(r"(?:twitter|x)\.com/@?(\w{2,20})(?:/status/(\d+))?", url)
        if um:
            handle = um.group(1)
            platform = "X/Twitter"
        else:
            pm = re.search(r"pixiv\.net/(?:artworks/\d+|users/(\d+))", url)
            if pm:
                handle = pm.group(1) or ""
                platform = "Pixiv"
        title_m = re.search(r"<h4[^>]*>(.*?)</h4>", block, re.S)
        out.append(
            PostCandidate(
                engine="ascii2d",
                page_url=url,
                post_url=url if "/status/" in url or "artworks/" in url else "",
                title=re.sub(r"<[^>]+>", "", title_m.group(1)).strip() if title_m else "",
                author_handle=handle,
                author_platform=platform,
                weight=0.4,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Google Cloud Vision WEB_DETECTION（官方付费）
# ---------------------------------------------------------------------------


async def search_gcv(
    image_bytes: bytes,
    *,
    api_key: str,
    timeout: int = 30,
    proxy: str = "",
) -> list:
    """Google Cloud Vision API webDetection：返回完全/部分匹配页面。"""
    if not api_key:
        raise EngineError("GCV 需要 GCP API key（Vision API，webDetection）")
    body = {
        "requests": [
            {
                "image": {"content": base64.b64encode(image_bytes).decode()},
                "features": [{"type": "WEB_DETECTION", "maxResults": 20}],
            }
        ]
    }
    async with _make_client(timeout, proxy) as client:
        resp = await client.post(
            f"https://vision.googleapis.com/v1/images:annotate?key={quote(api_key)}",
            json=body,
        )
        if resp.status_code != 200:
            raise EngineError(f"GCV HTTP {resp.status_code}: {resp.text[:150]}")
        data = resp.json()

    wd = data.get("responses", [{}])[0].get("webDetection", {})
    out: list = []
    seen: set = set()
    for section, w in (
        ("pagesWithMatchingImages", 0.3),
        ("fullMatchingImages", 0.2),
        ("partialMatchingImages", 0.15),
    ):
        for item in wd.get(section, []) or []:
            url = item.get("url", "")
            if not url or url in seen:
                continue
            seen.add(url)
            out.append(PostCandidate(engine="gcv", page_url=url, weight=w))
    return out


# ---------------------------------------------------------------------------
# 感知哈希：用于「候选原图 == 查询图」的硬确认
# ---------------------------------------------------------------------------

try:
    _LANCZOS = Image.Resampling.LANCZOS  # Pillow >= 9.1
except AttributeError:  # Pillow < 9.1
    _LANCZOS = Image.LANCZOS


def _perceptual_hashes(data: bytes) -> Optional[tuple]:
    """返回 (aHash64, dHash64)，失败返回 None。"""
    try:
        img = Image.open(io.BytesIO(data)).convert("L")
    except Exception:
        return None

    # aHash: 8x8 均值哈希
    small = img.resize((8, 8), _LANCZOS)
    px: list = list(small.tobytes())
    avg = sum(px) / 64
    ahash = 0
    for bit, v in enumerate(px):
        if v > avg:
            ahash |= 1 << bit

    # dHash: 9x8 相邻差分哈希
    small2 = img.resize((9, 8), _LANCZOS)
    px2: list = list(small2.tobytes())
    dhash = 0
    bit = 0
    for row in range(8):
        for col in range(8):
            if px2[row * 9 + col] > px2[row * 9 + col + 1]:
                dhash |= 1 << bit
            bit += 1
    return ahash, dhash


def _hash_similarity(h1: tuple, h2: tuple) -> float:
    """两组哈希的归一化汉明相似度（0-1）。"""

    def _sim(a: int, b: int) -> float:
        return 1.0 - bin(a ^ b).count("1") / 64.0

    return (_sim(h1[0], h2[0]) + _sim(h1[1], h2[1])) / 2


# 感知哈希确认的资源闸门（原实现无上限，在本插件场景下必须收敛）：
# 未配 SauceNAO key 时 Yandex 常返回 50+ 个噪声 handle（无关站点标题词），
# 若无限制会对每个 handle 串行下载至多 3 张候选图，最坏上百次请求，
# 在 QQ 里直接表现为机器人"卡死"。故设三重闸门。
_MAX_VERIFY_HANDLES = 12   # 参与确认的 handle 上限（取关联条目最多的前 N 个）
_VERIFY_IMAGE_TIMEOUT = 10  # 单张候选图下载超时（秒）
_MAX_VERIFY_BUDGET = 30.0   # 整个确认阶段的总时间预算（秒），超时即用已有结果


async def phash_verify_all(
    candidates: list,
    query: bytes,
    proxy: str = "",
    timeout: int = 20,
) -> dict:
    """对所有带原图 URL 的候选按署名分组下载，计算与查询图的相似度。

    覆盖全部引擎（不只 Yandex），同图硬确认的 +20% 置信度加成
    不会漏掉非 Yandex 引擎命中的作者。

    注意 handle 数量按 _MAX_VERIFY_HANDLES 截断、整体受 _MAX_VERIFY_BUDGET
    时间约束：宁可少确认几个弱 handle，也不能让机器人无响应。
    """
    scores: dict = {}
    qh = _perceptual_hashes(query)
    if qh is None:
        return scores

    per_handle: dict = {}
    for c in candidates:
        if not c.image_url:
            continue
        handles = set()
        if c.author_handle:
            handles.add(c.author_handle.lower())
        for m in _DESC_CREDIT_RE.finditer(c.description or ""):
            h = _clean(m.group(1) or m.group(2))
            if h:
                handles.add(h.lower())
        m = _TITLE_HANDLE_RE.search(c.title) or _X_URL_RE.search(c.page_url)
        if m:
            h = _clean(m.group(1))
            if h:
                handles.add(h.lower())
        for h in handles:
            per_handle.setdefault(h, []).append(c.image_url)

    if not per_handle:
        return scores

    # 按关联条目数降序：同一 handle 被引擎命中次数越多越可能是真作者
    ordered = sorted(per_handle.items(), key=lambda kv: -len(kv[1]))[:_MAX_VERIFY_HANDLES]
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _MAX_VERIFY_BUDGET

    # 候选图下载用独立的较短超时（timeout 参数是引擎请求超时，此处不宜共用）
    async with _make_client(_VERIFY_IMAGE_TIMEOUT, proxy) as client:
        for h, urls in ordered:
            if loop.time() > deadline:
                logger.warning(
                    f"[找鸡] 感知哈希确认超预算({_MAX_VERIFY_BUDGET}s)，"
                    f"提前结束（剩余 {len(ordered)} 个 handle 未确认）"
                )
                break
            for u in urls[:3]:
                if loop.time() > deadline:
                    break
                try:
                    resp = await client.get(u)
                    if resp.status_code != 200:
                        continue
                    ch = _perceptual_hashes(resp.content)
                except Exception:
                    continue
                if ch is None:
                    continue
                sim = _hash_similarity(qh, ch)
                if sim > scores.get(h, 0.0):
                    scores[h] = sim
    return scores


# ---------------------------------------------------------------------------
# 跨引擎证据融合与置信度评分
#
# 置信度模型（0-1，封顶）：
# - description/正文署名（转发者正文注明原作者）：+0.35/次
# - SauceNAO 返回的作者（官方索引，直接给 status URL）：+0.45
# - ascii2d 结果作者（链接直达 status/artwork）：+0.30
# - 标题/URL handle（可能是转发者）：+0.10~0.15/次
# - 感知哈希同图硬确认（>=0.90）：+0.20（跨>=2 域）或 +0.10（单域）
# ---------------------------------------------------------------------------

_TITLE_HANDLE_RE = re.compile(r"@([A-Za-z0-9_]{1,20})")
_X_URL_RE = re.compile(
    r"(?:x\.com|twitter\.com|sotwe\.com|urlebird\.com)/(?:user/)?@?([A-Za-z0-9_]{2,20})(?:[/?]|$)"
)
# 强署名：明确 credit 模式（"sc: handle" / "handle's tweet photo/video"）
_DESC_CREDIT_RE = re.compile(
    r"(?:sc|credit|cr|source|via|cn|photo|drawn\s+by|tw)\s*[:：]\s*@?([A-Za-z0-9_]{2,20})"
    r"|([A-Za-z0-9_]{2,20})'(?:s|’s)\s+(?:tweet\s+)?(?:photo|video|status|post)"
)
# 弱证据：裸 @mention（可能是转发者自引用）
_DESC_MENTION_RE = re.compile(r"@([A-Za-z0-9_]{2,20})")
_STATUS_RE = re.compile(r"(?:x\.com|twitter\.com)/(\w{2,20})/status/(\d+)")

_STOPWORDS = {"status", "search", "user", "home", "intent", "sunna", "sc", "i", "web"}

# handle 形态：4-20 位字母数字下划线，至少含一个字母（排除纯数字 id/日期）
_HANDLE_LIKE_RE = re.compile(r"[A-Za-z0-9_]{4,20}")
_LETTER_RE = re.compile(r"[A-Za-z]")

# 聚合站标题常见通用词，不是人名
_TITLE_NOISE = {
    "nude", "leaks", "leaked", "onlyfans", "patreon", "photo", "photos",
    "video", "videos", "naked", "sexy", "cosplay", "forum", "wall",
    "free", "top", "best", "girl", "girls", "asian", "japanese",
    "leakedmodels", "fappening", "fapeza", "fapello", "nudostar",
    "nudogram", "fapodrop", "tokyomotion", "sotwe", "urlebird",
    "twitter", "profile", "голая", "фото", "сливы",
    "page", "pages", "index", "home", "main", "list", "category", "tag",
    "search", "archive", "post", "posts", "comments", "login", "update",
    "aliexpress", "imall", "telegra", "telegram", "telegraph", "vikacg",
    "alibaba", "amazon", "ebay", "reddit", "google", "yandex", "bing",
    "kitty", "welfare", "silver", "misty", "models", "model",
}


def _is_handle_like(tok: str) -> bool:
    """判断标题 token 是否像账号名：形态匹配、含字母、非通用词。"""
    if not _HANDLE_LIKE_RE.fullmatch(tok):
        return False
    if not _LETTER_RE.search(tok):
        return False
    return tok.lower() not in _TITLE_NOISE


def _domain_root(url: str) -> str:
    """域名根（剥离语言前缀 ru./de. 等），用于跨站一致计分。"""
    host = url.split("/")[2] if "://" in url else ""
    parts = host.split(".")
    if len(parts) > 2 and len(parts[0]) <= 3:
        parts = parts[1:]
    return ".".join(parts)


def _clean(h) -> str:
    if not h or h.lower() in _STOPWORDS:
        return ""
    return h.lstrip("@").strip()


@dataclass
class AuthorVerdict:
    """一个作者的融合判定。"""

    handle: str
    platform: str
    confidence: float  # 0-1
    post_url: str = ""  # 最佳已知原帖（具体 status URL 优先）
    page_url: str = ""  # 证据页面
    evidence: list = field(default_factory=list)  # 人类可读证据链
    domains: set = field(default_factory=set)  # 引用该 handle 的独立域名


def fuse(candidates: list, phash_scores: Optional[dict] = None) -> list:
    """融合所有引擎候选。phash_scores: handle.lower() -> 相似度。"""
    phash_scores = phash_scores or {}
    authors: dict = {}

    def _author(handle: str, platform: str) -> AuthorVerdict:
        key = (handle.lower(), platform)
        if key not in authors:
            authors[key] = AuthorVerdict(handle=handle, platform=platform, confidence=0.0)
        return authors[key]

    for c in candidates:
        status_m = _STATUS_RE.search(c.post_url or "") or _STATUS_RE.search(c.page_url or "")
        pixiv_m = re.search(r"pixiv\.net/artworks/(\d+)", c.post_url or c.page_url or "")

        if c.engine == "saucenao" and c.author_handle:
            a = _author(c.author_handle, c.author_platform or "X/Twitter")
            a.confidence = min(1.0, a.confidence + 0.45)
            a.evidence.append(f"SauceNAO 索引命中: {c.page_url[:70]}")
            if status_m:
                a.post_url = status_m.group(0)
            elif pixiv_m:
                a.post_url = f"https://www.pixiv.net/artworks/{pixiv_m.group(1)}"

        elif c.engine == "ascii2d" and c.author_handle:
            a = _author(c.author_handle, c.author_platform or "X/Twitter")
            a.confidence = min(1.0, a.confidence + 0.30)
            a.evidence.append(f"ascii2d 结果: {c.page_url[:70]}")
            if status_m and not a.post_url:
                a.post_url = status_m.group(0)

        elif c.engine == "yandex":
            platform = _platform_of(c)
            domain_root = _domain_root(c.page_url)
            title_m = _TITLE_HANDLE_RE.search(c.title) or _X_URL_RE.search(c.page_url)
            title_h = _clean(title_m.group(1)) if title_m else ""

            # 1) description 强署名（credit 模式，原作者）
            for m in _DESC_CREDIT_RE.finditer(c.description or ""):
                h = _clean(m.group(1) or m.group(2))
                if h:
                    a = _author(h, platform)
                    a.confidence = min(1.0, a.confidence + 0.35)
                    a.domains.add(domain_root)
                    snippet = c.description[max(0, m.start() - 15) : m.end() + 10]
                    a.evidence.append(f"转发正文署名: 「…{snippet}…」")
                    a.page_url = a.page_url or c.page_url
                    if status_m and not a.post_url:
                        a.post_url = status_m.group(0)

            # 2) description 裸 @mention：只有与页面标题 handle 不同才算独立弱证据
            for m in _DESC_MENTION_RE.finditer(c.description or ""):
                h = _clean(m.group(1))
                if h and h.lower() != title_h.lower():
                    a = _author(h, platform)
                    a.confidence = min(1.0, a.confidence + 0.10)
                    a.domains.add(domain_root)
                    a.evidence.append(f"正文提及: @ {h}")
                    a.page_url = a.page_url or c.page_url

            # 3) 标题/URL handle（转发者）—— 跨域名一致性提权
            if title_h:
                a = _author(title_h, platform)
                if domain_root not in a.domains:
                    a.confidence = min(1.0, a.confidence + (0.15 if a.domains else 0.10))
                    a.domains.add(domain_root)
                else:
                    a.confidence = min(1.0, a.confidence + 0.05)
                a.evidence.append(f"站点署名({domain_root}): {c.page_url[:55]}")
                a.page_url = a.page_url or c.page_url

            # 4) 聚合站标题开头的 handle 形态词（"pon9019 Nude Leaks..."）
            if not platform:
                for raw_tok in (c.title or "").split():
                    tok = raw_tok.strip(",.;:!?()[]").split("-")[0]
                    if not _is_handle_like(tok):
                        continue
                    a = _author(tok, "未知")
                    if domain_root not in a.domains:
                        a.domains.add(domain_root)
                        if len(a.domains) >= 2:
                            # 第二个独立域名确认后才计分（单域噪声不注册）；
                            # 封顶 0.30：跨站人名只说明"此人的图在流通"，
                            # 不能压过正文强署名（0.35，直接指明作者）
                            a.confidence = min(0.30, a.confidence + 0.10)
                            a.evidence.append(
                                f"聚合站人名(跨站一致 x{len(a.domains)}): {c.title[:50]}"
                            )
                            a.page_url = a.page_url or c.page_url
                    else:
                        a.confidence = min(0.30, a.confidence + 0.05)

                # URL 路径中的 handle 形态段（fappeningbook.com/pon9019-nude/）
                for seg in c.page_url.split("/")[3:]:
                    seg = seg.split("?")[0]
                    m2 = _HANDLE_LIKE_RE.match(seg)
                    if (
                        m2
                        and _is_handle_like(m2.group(0))
                        and (m2.group(0).lower(), "未知") in authors
                    ):
                        a = authors[(m2.group(0).lower(), "未知")]
                        if domain_root not in a.domains:
                            a.domains.add(domain_root)
                            if len(a.domains) >= 2:
                                a.confidence = min(0.30, a.confidence + 0.10)
                                a.evidence.append(f"URL 路径含该名({domain_root})")

        elif c.engine == "gcv":
            m = re.search(r"(?:x\.com|twitter\.com)/(\w{2,20})/?$", c.page_url)
            h = _clean(m.group(1)) if m else ""
            if h:
                a = _author(h, "X/Twitter")
                a.confidence = min(1.0, a.confidence + 0.15)
                a.evidence.append(f"Google Vision 匹配页: {c.page_url[:70]}")

    # --- 感知哈希加成 ---
    # 教训：转发页同样含同图，"页面含同图"≠"该页账号是作者"。
    # 跨域名（>=2 站点引用）的 handle 才给完整作者确认加成；
    # 单域名命中只给图源页加成（信息量："同图在该账号页面出现过"）。
    for (h_lower, _), a in authors.items():
        sim = phash_scores.get(h_lower, 0.0)
        if sim >= 0.90:
            if len(a.domains) >= 2:
                a.confidence = min(1.0, a.confidence + 0.20)
                a.evidence.append(f"感知哈希同图确认(跨{len(a.domains)}域): {sim:.0%}")
            else:
                a.confidence = min(1.0, a.confidence + 0.10)
                a.evidence.append(f"同图出现在其页面(单域,或为转发): {sim:.0%}")
        elif sim >= 0.75:
            a.confidence = min(1.0, a.confidence + 0.05)
            a.evidence.append(f"感知哈希可疑相似: {sim:.0%}")

    ranked = sorted(authors.values(), key=lambda a: -a.confidence)
    return [a for a in ranked if a.confidence >= 0.10]


# 优先平台：找「鸡」要的是能去关注的社交账号（twitter / ins / 微博 / 小红书）。
# Pixiv、Danbooru 等图库站不认账（用户明确要求 pixiv 不考虑），
# 「未知/身份(站内人名)」连平台归属都没有，同样排除。
_PREFERRED_PLATFORMS = {"X/Twitter", "Instagram", "微博", "小红书"}


def _no_result(verdicts: list, yandex_url: str) -> str:
    """未命中时的文案：区分「压根没候选」与「候选都不是社交账号」。"""
    if verdicts:
        detail = "命中了图库/画站或身份不明来源，已忽略（只认社交账号）"
    else:
        detail = "换张更清晰的图试试"
    msg = f"没找到鸡 😢 {detail}"
    if yandex_url:
        msg += f"\n人工排查：{yandex_url}"
    return msg


def format_top1(verdicts: list, yandex_url: str = "") -> str:
    """只返回置信度最高的「社交平台」作者，单条短文本。

    样式（QQ 文本消息，emoji 走客户端渲染，无 PIL 字体问题）：
        🎯 **lg找鸡结果**

        🌐 平台：Twitter
        👤 名字：aqua_cosplay
        🆔 账号：@aqua_cosplay
        https://x.com/aqua_cosplay

    两条筛选原则：
    1) 平台优先 —— 不再是 verdicts[0]，而是在社交平台候选中取置信度最高者；
       这样 Pixiv 即使分数最高也不会被报出来。
    2) 置信度低于 _STRONG_CONFIDENCE 视为没找到 ——
       弱证据多为转发者，直接报账号会误导（与 format_verdicts 同一教训）。
    """
    # verdicts 已按 confidence 降序，过滤保序 => social[0] 即社交平台里最可信的
    social = [v for v in verdicts if v.platform in _PREFERRED_PLATFORMS]
    if not social:
        return _no_result(verdicts, yandex_url)

    v = social[0]
    if v.confidence < _STRONG_CONFIDENCE:
        msg = "没找到鸡 😢 最像的账号可信度不足，不敢乱报"
        if yandex_url:
            msg += f"\n人工排查：{yandex_url}"
        return msg

    platform = "Twitter" if v.platform == "X/Twitter" else v.platform

    lines = [
        "🎯 **lg找鸡结果**",
        "",
        f"🌐 平台：{platform}",
        f"👤 名字：{v.handle}",
        f"🆔 账号：@{v.handle}",
    ]
    url = v.post_url or v.page_url
    if url:
        lines.append(url)
    return "\n".join(lines)


def _platform_of(c: PostCandidate) -> str:
    d = c.page_url.split("/")[2] if "://" in c.page_url else ""
    table = [
        ("x.com", "X/Twitter"),
        ("twitter.com", "X/Twitter"),
        ("sotwe.com", "X/Twitter"),
        ("urlebird.com", "X/Twitter"),
        ("weibo.com", "微博"),
        ("weibo.cn", "微博"),
        ("xiaohongshu.com", "小红书"),
        ("instagram.com", "Instagram"),
        ("pixiv.net", "Pixiv"),
        ("danbooru.donmai.us", "Danbooru"),
        ("coser.one", "Cosplay站"),
        ("cosppi.net", "Cosplay站"),
    ]
    for prefix, name in table:
        if d == prefix or d.endswith("." + prefix):
            return name
    return ""


# 强证据阈值：有正文署名/官方引擎命中/同图确认之一才作为"作者候选"展示
_STRONG_CONFIDENCE = 0.30


def format_verdicts(verdicts: list, limit: int = 5) -> str:
    """格式化融合结果。

    教训：镜像页标题 handle 大多是转发者。仅靠弱证据（+0.10 累加）的作者
    不允许以"作者候选"名义出现，折叠进明确标注低可信的列表，避免误导。
    """
    if not verdicts:
        return (
            "未能确定作者候选（各引擎均无署名证据）。\n"
            "提示：配置 saucenao_api_key 可显著提升原帖命中率。"
        )

    for v in verdicts:
        if v.platform == "未知":
            v.platform = "身份(站内人名)"

    strong = [v for v in verdicts if v.confidence >= _STRONG_CONFIDENCE]
    weak = [v for v in verdicts if v.confidence < _STRONG_CONFIDENCE]

    lines = []
    if strong:
        lines.append(f"作者候选（{len(strong)} 位，按置信度排序）：")
        for v in strong[:limit]:
            lines.append(f"  @{v.handle}  [{v.platform}]  置信度 {v.confidence:.2f}")
            if v.post_url:
                lines.append(f"      原帖: {v.post_url}")
            elif v.page_url:
                lines.append(f"      证据页: {v.page_url}")
            for ev in v.evidence[:3]:
                lines.append(f"      · {ev}")
    else:
        lines.append("未找到可靠署名证据（无正文 credit、无官方引擎命中、无同图确认）。")
        lines.append("以下仅为结果页中出现过的账号，多为转发者，低可信：")
        for v in weak[:limit]:
            lines.append(f"  ? @{v.handle}  [{v.platform}]  置信度 {v.confidence:.2f}")
    if strong and weak:
        lines.append(f"（另有 {len(weak)} 个仅弱证据账号，多为转发者，已折叠）")
    if not strong:
        lines.append("提示：配置 saucenao_api_key 可显著提升原帖命中率。")

    # 身份类候选（站内人名，无平台归属）附 X 搜索验证链接
    for i, line in enumerate(lines):
        if "[身份(站内人名)]" in line:
            try:
                handle = line.split("@")[1].split(" ")[0]
            except IndexError:
                continue
            lines.insert(i + 1, f"      X 搜索验证: https://x.com/search?q={handle}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 编排：多引擎并行 -> 融合 -> 文本结果
# ---------------------------------------------------------------------------


def extract_public_url(yandex_result_url: str) -> str:
    """从 Yandex 结果页 URL 中解出 Yandex 托管的公网原图 URL。

    SauceNAO 需要一个它能抓到的图片 URL；Yandex 的
    avatars.mds.yandex.net 托管 URL 可公开访问，符合要求。
    """
    m = re.search(r"url=(https[^&]+)", yandex_result_url)
    if m:
        return unquote(m.group(1))
    return ""


async def reverse_search_all(image_bytes: bytes) -> str:
    """一次完整的反向图片搜索，返回可直接发送的结果文本。

    流程：Yandex 上传（顺带拿到公网 URL）-> 其余引擎并发 -> 感知哈希确认
    -> 跨源融合 -> 只输出置信度第一名的精简文本（format_top1）。
    任何引擎失败只记日志，不影响其他引擎。
    """
    cfg = load_config()
    timeout = int(cfg.get("timeout", 20))
    proxy = cfg.get("proxy", "") or ""

    candidates: list = []
    yandex_url = ""
    yandex_error = ""

    # 1) 先做 Yandex 上传，拿到公网可访问的原图 URL（供 SauceNAO 用）
    try:
        yandex_url, yx = await search_yandex(image_bytes, timeout=timeout, proxy=proxy)
        candidates.extend(yx)
    except Exception as e:
        yandex_error = str(e)
        logger.warning(f"[找鸡] Yandex 失败: {e}")

    # 2) 其余引擎并发（SauceNAO 需要公网图片 URL）
    tasks: dict = {}
    if cfg.get("saucenao_api_key") and yandex_url:
        img_public_url = extract_public_url(yandex_url)
        if img_public_url:
            tasks["saucenao"] = asyncio.create_task(
                search_saucenao(
                    img_public_url,
                    api_key=cfg["saucenao_api_key"],
                    timeout=timeout,
                    proxy=proxy,
                )
            )
    if cfg.get("ascii2d_cf_clearance"):
        tasks["ascii2d"] = asyncio.create_task(
            search_ascii2d(
                image_bytes,
                timeout=timeout,
                proxy=proxy,
                cf_clearance=cfg["ascii2d_cf_clearance"],
            )
        )
    if cfg.get("gcv_api_key"):
        tasks["gcv"] = asyncio.create_task(
            search_gcv(image_bytes, api_key=cfg["gcv_api_key"], timeout=timeout, proxy=proxy)
        )

    if tasks:
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        for name, res in zip(tasks.keys(), results):
            if isinstance(res, BaseException):
                logger.warning(f"[找鸡] {name} 失败: {res}")
            else:
                logger.info(f"[找鸡] {name}: {len(res)} 条")
                candidates.extend(res)

    if not candidates:
        msg = "所有引擎均未返回结果。"
        if yandex_error:
            msg += f" Yandex: {yandex_error}"
        return msg

    # 3) 候选原图做感知哈希硬确认
    phash_scores = await phash_verify_all(candidates, image_bytes, proxy, timeout)

    # 4) 融合评分 -> 只输出第一名（用户要求的精简样式，单条文本）
    verdicts = fuse(candidates, phash_scores)
    return format_top1(verdicts, yandex_url=yandex_url)
