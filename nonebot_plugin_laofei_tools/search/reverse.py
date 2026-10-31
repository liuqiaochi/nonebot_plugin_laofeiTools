"""
反向图片搜索：Google Lens 反查图片出处与作者（含 X 原帖）。

移植自 astrbot_plugin_bitchfinder，按本插件 NoneBot2 环境改造：
- 原实现拆成 engines.py / fuse.py / sauce.py 三个文件，此处合并为单模块
- 网络层由 aiohttp 改为 httpx（与插件其余模块 soutubot.py 一致，不新增依赖）
- 置信度模型、正则提取、融合排序全部保持原实现，一处未改

2026-10-30 改造：删除 Yandex / SauceNAO / ascii2d / Google Cloud Vision
等其余引擎，只保留 Google Lens（经 SerpApi 代理层）。原因：实测 Yandex
返回的几乎都是盗图聚合站，而 Lens 才真正给出作者原帖；且 Lens 只需一个
serpapi_key 即可，免去维护多套 key / cookie。

为什么不用 soutubot：soutubot.moe 是动漫/本子垂直库，
搜不到 X.com / 微博 / 小红书 等站点；此处走通用搜索引擎。

Google Lens 链路（经 SerpApi）：
1. 没有现成公网 URL 时，先把图压到长边 1600 / JPEG，再丢到临时图床
   （litterbox → uguu → qu.ax 兜底）换一个公网直链（Lens 只认 URL）。
2. 调 SerpApi engine=google_lens，直连优先（实测 1.5s 可达），直连超时/
   失败才走 lens_proxy。
3. 解析 exact_matches / visual_matches，按链接形态与排序位置给证据权重。
4. 融合评分，只输出社交平台里置信度最高的那位作者。

配置（全部在 nonebot 配置 / .env）：
- serpapi_keys：必需（serpapi.com 免费 100 次/月/key）。支持填多个 key 组成数组，
  每月每 key 自动计数，满 100 自动切下一个；当月全部用完则暂停本功能，次月 1 号重置。
  .env 写法：SERPAPI_KEYS=["k1","k2"] 或 逗号分隔 SERPAPI_KEYS=k1,k2
- lens_proxy：大陆机器访问 Google 多半不通，填如 http://127.0.0.1:7890
- longge_reverse_timeout / longge_reverse_max_results：可选
"""

import asyncio
import datetime
import hashlib
import io
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import quote

import httpx
from PIL import Image

from nonebot import get_driver, logger

from ..config import DATA_DIR
from ..common.data_utils import safe_json_save


try:
    _LANCZOS = Image.Resampling.LANCZOS  # Pillow >= 9.1
except AttributeError:  # Pillow < 9.1
    _LANCZOS = Image.LANCZOS


# ========== 结果标题与配图 ==========

_IMAGE_DIR = Path(__file__).resolve().parent.parent / "image"
FINDED_IMAGE = _IMAGE_DIR / "finded.png"

RESULT_TITLE = "🎯 **lg找姬结果**"


def finded_cq() -> str:
    """结果标题下方固定带的配图 CQ 码（image/finded.png）。

    用 file:// 本地路径而不是 base64：base64 大图会把整条消息撑爆（AI 配图
    1.6MB 那次就是这么失败的）。文件缺失时返回空串，不影响结果本身。
    """
    if not FINDED_IMAGE.exists():
        return ""
    return f"[CQ:image,file=file://{FINDED_IMAGE.as_posix()}]"


# ========== 配置读取 ==========

# 逻辑名 -> (NoneBot 配置项, 默认值)
_CFG_ATTRS = {
    "timeout": ("longge_reverse_timeout", 25),
    "max_results": ("longge_reverse_max_results", 5),
    "serpapi_keys": ("serpapi_keys", []),
    "serpapi_key_single": ("serpapi_key", ""),  # 旧单 key 兼容
    # lens_proxy 同时用于 SerpApi 调用与图床上传（都是境外服务，大陆需代理）
    "proxy": ("lens_proxy", ""),
    # 主路：mac 侧浏览器 sidecar（tools/fish_sidecar/server.mjs）
    "sidecar_url": ("lens_sidecar_url", ""),
    "sidecar_token": ("lens_sidecar_token", ""),
    "sidecar_timeout": ("lens_sidecar_timeout", 90),
    # 调试：结果走合并转发展示多项
    "debug_forward": ("longge_reverse_debug_forward", True),
}


def load_config() -> dict:
    """从 NoneBot 配置读取反搜参数，缺失回退默认值（每次调用实时读取）。

    把 serpapi_keys（数组）与旧的 serpapi_key（单值）合并、去空、去重，
    统一成 cfg["serpapi_keys"] 列表。
    """
    cfg = {k: default for k, (_, default) in _CFG_ATTRS.items()}
    try:
        driver_config = get_driver().config
    except Exception:
        driver_config = None
    if driver_config is None:
        driver_config = _Empty()
    for key, (attr, _) in _CFG_ATTRS.items():
        try:
            val = getattr(driver_config, attr, None)
            if val is not None:
                cfg[key] = val
        except Exception:
            pass

    # 规整 serpapi key 数组：优先 serpapi_keys，空则回退旧的 serpapi_key 单值
    keys = cfg.pop("serpapi_keys") or []
    single = (cfg.pop("serpapi_key_single") or "").strip()
    if not keys and single:
        keys = [single]
    seen = set()
    uniq = []
    for k in keys:
        k = (k or "").strip()
        if k and k not in seen:
            seen.add(k)
            uniq.append(k)
    cfg["serpapi_keys"] = uniq
    return cfg


class _Empty:
    """driver 不可用时充当占位，getattr 一律返回 None。"""

    def __getattr__(self, _name):
        return None


def engine_status(cfg: dict) -> list:
    """返回引擎可用性清单（启动时打日志用）：主路 sidecar + 兜底 SerpApi。"""
    status = []
    if (cfg.get("sidecar_url") or "").strip():
        status.append("sidecar(主路,本机浏览器)")
    else:
        status.append("-sidecar(未配置lens_sidecar_url)")
    keys = cfg.get("serpapi_keys") or []
    if keys:
        status.append(f"lens(兜底,{len(keys)} key,每 key 100 次/月)")
    else:
        status.append("-lens(未配置serpapi_keys)")
    return status


# ---------------------------------------------------------------------------
# SerpApi 免费额度计数：每 key 每月 100 次，满则切下一个；当月全用完则暂停；
# 月份变化（次月 1 号后首次使用）自动重置。
#
# 计数文件存于 DATA_DIR/serpapi_usage.json，按 key 的 sha256 前 16 位做指纹，
# 不落明文 key。仅在 SerpApi 真正请求成功（HTTP 200）后才保留计数；上传图床
# 失败等前置异常会退回本次占用，不白白消耗免费次数。
# ---------------------------------------------------------------------------

_SERPAPI_USAGE_FILE = DATA_DIR / "serpapi_usage.json"
_SERPAPI_FREE_LIMIT = 100
_MONTH_FMT = "%Y-%m"
_usage_lock = asyncio.Lock()


def _serpapi_fp(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _cur_month() -> str:
    return datetime.datetime.now().strftime(_MONTH_FMT)


def _load_serpapi_usage() -> dict:
    try:
        if _SERPAPI_USAGE_FILE.exists():
            with open(_SERPAPI_USAGE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and "month" in data and isinstance(data.get("counts"), dict):
                return data
    except Exception:
        pass
    return {"month": _cur_month(), "counts": {}}


def _reset_serpapi_if_new_month(usage: dict) -> None:
    m = _cur_month()
    if usage.get("month") != m:
        usage["month"] = m
        usage["counts"] = {}


def _save_serpapi_usage(usage: dict) -> None:
    safe_json_save(_SERPAPI_USAGE_FILE, usage)


async def _acquire_serpapi_key(cfg: dict):
    """原子地挑一个当月未满 100 次的 key，并把计数 +1。

    返回 (key, fingerprint, remaining)；若当月所有 key 都用完则返回
    (None, None, 0)。调用方在 SerpApi 真正成功后才保留计数，失败需调
    _refund_serpapi_key 退回，避免上传图床失败等也白白消耗额度。
    """
    keys = cfg.get("serpapi_keys") or []
    async with _usage_lock:
        usage = _load_serpapi_usage()
        _reset_serpapi_if_new_month(usage)
        for k in keys:
            fp = _serpapi_fp(k)
            c = int(usage["counts"].get(fp, 0))
            if c < _SERPAPI_FREE_LIMIT:
                usage["counts"][fp] = c + 1
                _save_serpapi_usage(usage)
                return k, fp, _SERPAPI_FREE_LIMIT - (c + 1)
        return None, None, 0


async def _refund_serpapi_key(fp: str) -> None:
    """退回一次额度占用（用于 SerpApi 实际未成功的情况）。"""
    if not fp:
        return
    async with _usage_lock:
        usage = _load_serpapi_usage()
        c = int(usage["counts"].get(fp, 0))
        if c > 0:
            usage["counts"][fp] = c - 1
            _save_serpapi_usage(usage)


# ========== 统一数据结构 ==========

@dataclass
class PostCandidate:
    """一个候选出处：可能带作者、可能带具体帖子 URL。"""

    engine: str            # 固定 "lens"
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

    verify=False：境外站点证书链在国内环境偶有不完整（对应原实现 ssl=False）
    trust_env=False：不读系统代理环境变量，统一走显式 proxy 配置
    proxy 参数名在 httpx>=0.28 为 proxy / 旧版为 proxies，两者兼容。
    """
    kwargs = dict(
        timeout=httpx.Timeout(float(timeout), connect=10.0),
        headers={"User-Agent": _UA},
        verify=False,
        trust_env=False,
        follow_redirects=True,
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
# 上传前压缩：把图压到长边 1600 / JPEG q88
# ---------------------------------------------------------------------------

def shrink_for_upload(
    image_bytes: bytes,
    *,
    max_side: int = 1600,
    quality: int = 88,
) -> bytes:
    """上传前把图压到长边 1600 / JPEG q88。

    QQ 转来的原图常有 8.8MB，直接传图床/给 Lens 既慢又易超时；缩到 ~400KB
    后稳定几秒。视觉检索不需要原分辨率，Lens 吃得住。
    """
    if not image_bytes:
        return image_bytes
    try:
        im = Image.open(io.BytesIO(image_bytes))
        im.load()
        w, h = im.size
        scale = min(1.0, float(max_side) / max(w, h))
        if scale < 1.0:
            im = im.resize(
                (max(1, int(w * scale)), max(1, int(h * scale))),
                _LANCZOS,
            )
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=quality, optimize=True)
        out = buf.getvalue()
        return out if out else image_bytes
    except Exception:  # noqa: BLE001 - 缩图失败就用原图，别因此不搜
        return image_bytes


# ---------------------------------------------------------------------------
# 临时图床：Lens 只认公网图片 URL，所以先把字节传到图床换直链
# ---------------------------------------------------------------------------

_UPLOAD_TARGETS = (
    ("litterbox", "https://litterbox.catbox.moe/resources/internals/api.php"),
    ("uguu", "https://uguu.se/upload.php"),
    ("quax", "https://qu.ax/upload.php"),
)


async def upload_image(image_bytes: bytes, *, timeout: int = 20, proxy: str = "") -> str:
    """把图丢到临时图床，换一个公网直链（Lens 只认 URL，不认字节）。

    图床不能只挂一家——litterbox 偶尔回 500，Lens 就整条哑掉，结论变成
    "所有引擎均未返回结果"。所以按 litterbox → uguu → qu.ax 依次兜底。
    """
    if not image_bytes:
        raise EngineError("upload: 没有图片数据")
    errs: list = []
    for name, api in _UPLOAD_TARGETS:
        for _ in range(2):
            try:
                return await _upload_one(name, api, image_bytes, timeout=timeout, proxy=proxy)
            except Exception as e:  # noqa: BLE001 - 换下一家
                errs.append(f"{name}: {type(e).__name__}: {str(e)[:60]}")
                await asyncio.sleep(0.4)
    raise EngineError("upload: 全部图床失败 " + " | ".join(errs[-3:]))


async def _upload_one(name: str, api: str, image_bytes: bytes, *, timeout: int, proxy: str) -> str:
    if name == "litterbox":
        data = {"reqtype": "fileupload", "time": "1h"}
        files = {"fileToUpload": ("blob.jpg", image_bytes, "image/jpeg")}
    elif name == "uguu":
        data = None
        files = {"files[]": ("blob.jpg", image_bytes, "image/jpeg")}
    else:
        data = None
        files = {"file": ("blob.jpg", image_bytes, "image/jpeg")}
    async with _make_client(timeout, proxy) as sess:
        if name == "litterbox":
            resp = await sess.post(api, data=data, files=files)
        else:
            resp = await sess.post(api, files=files)
        body = (resp.text or "").strip()
        if resp.status_code != 200:
            raise EngineError(f"HTTP {resp.status_code} {body[:80]}")
    if name == "litterbox":
        if body.startswith("http"):
            return body
        raise EngineError(f"无直链 {body[:80]}")
    try:
        data = json.loads(body)
    except Exception as e:  # noqa: BLE001
        raise EngineError(f"非 JSON {body[:80]}") from e
    files_list = (data or {}).get("files") or []
    url = (files_list[0] or {}).get("url") if files_list else ""
    if data.get("success") and url:
        return str(url)
    raise EngineError(f"无直链 {body[:80]}")


_FXTWITTER_API = "https://api.fxtwitter.com/"


async def fetch_user_name(handle: str, *, timeout: int = 6, proxy: str = "") -> str:
    """取 X 账号的显示名（fxtwitter，免登录）。

    只在"Lens 只给出转图帖正文 @ 到的账号"时需要：那种情况下标题里的名字是
    转发者的，真名拿不到。失败一律返回空串，不影响结论。
    """
    h = (handle or "").lstrip("@").strip()
    if not h:
        return ""
    for attempt in range(3):
        try:
            async with _make_client(timeout, proxy) as sess:
                resp = await sess.get(f"{_FXTWITTER_API}{h}")
                if resp.status_code == 200:
                    data = resp.json()
                    name = str(((data or {}).get("user") or {}).get("name") or "").strip()
                    if name:
                        return name
        except Exception:  # noqa: BLE001 - 补名失败不算错误
            pass
        if attempt < 2:
            await asyncio.sleep(0.4)
    return ""


# ---------------------------------------------------------------------------
# Google Lens（经 SerpApi，等价于 Lens 的"包含此图的页面"）
# ---------------------------------------------------------------------------

_SERPAPI_ENDPOINT = "https://serpapi.com/search.json"
_LENS_DIRECT_TIMEOUT = 8  # 直连只给 8s，超时立刻改走代理
_X_STATUS_RE = re.compile(
    r"(?:x\.com|twitter\.com|sotwe\.com|urlebird\.com)/[^/]+/status/", re.I
)


async def search_lens(
    image_url: str = "",
    *,
    api_key: str,
    image_bytes: Optional[bytes] = None,
    timeout: int = 25,
    proxy: str = "",
    limit: int = 40,
) -> list:
    """Google Lens 视觉匹配（SerpApi 代理层）。

    直连优先（实测 1.5s 可达）；直连超时/失败才走 proxy —— 代理只在这一步
    用，其他时间一律直连。
    没有现成公网 URL 时自己传图床，不依赖其他引擎。
    返回的每条是"包含该图的页面"，作者提取交给 fuse。
    """
    if not api_key:
        raise EngineError("lens: 未配置 serpapi_key")
    if not image_url:
        if not image_bytes:
            raise EngineError("lens: 缺少公网图片 URL")
        image_url = await upload_image(image_bytes, timeout=min(timeout, 20), proxy=proxy)

    params = {
        "engine": "google_lens",
        "url": image_url,
        "api_key": api_key,
        "hl": "zh-CN",
    }

    attempts = [None]
    if proxy:
        attempts.append(proxy)

    last_err: Optional[Exception] = None
    for px in attempts:
        budget = timeout if px else min(timeout, _LENS_DIRECT_TIMEOUT)
        try:
            async with _make_client(budget, px or "") as sess:
                resp = await sess.get(_SERPAPI_ENDPOINT, params=params)
                body = resp.text
                if resp.status_code != 200:
                    raise EngineError(f"lens: HTTP {resp.status_code} {body[:160]}")
                data = json.loads(body)
        except EngineError as e:
            last_err = e
            continue
        except Exception as e:
            last_err = EngineError(f"lens: {type(e).__name__}: {e}")
            continue

        out: list = []
        # exact_matches = 完全相同的图；visual_matches = 视觉相似页。
        # 权重看链接形态：指向具体 X 帖子的基本就是原帖本身（实测老图第一条
        # 就是 x.com/<作者>/status/<id>）；只给用户页或第三方站点的弱得多。
        for key in ("exact_matches", "visual_matches"):
            for item in (data.get(key) or [])[:limit]:
                if not isinstance(item, dict):
                    continue
                link = (item.get("link") or "").strip()
                if not link.startswith("http"):
                    continue
                # position 是 Lens 的相关度排序：前 3 位基本是同一张图的原帖，
                # 之后的"视觉相似"往往是别人拍的同类图。靠后的只当弱证据，不进结论。
                try:
                    pos = int(item.get("position") or 99)
                except Exception:
                    pos = 99
                title = (item.get("title") or "").strip()
                # 正文一开口就 @ 人 → 这条不是原帖，是转图/祝福/回复帖。发帖人
                # 不是作者，但正文里 @ 到的那个账号常常正是作者本人。
                reply_m = re.search(r"[:：]\s*[\"'“”]?\s*@([A-Za-z0-9_]{3,20})", title)
                author_hint = reply_m.group(1) if reply_m else ""
                is_x_post = bool(_X_STATUS_RE.search(link))
                if is_x_post and pos <= 3 and not author_hint:
                    weight = 0.88
                elif is_x_post and pos <= 3 and author_hint:
                    weight = 0.78
                elif key == "exact_matches" and not author_hint:
                    weight = 0.85
                else:
                    weight = 0.55
                out.append(
                    PostCandidate(
                        engine="lens",
                        page_url=link,
                        title=title,
                        description=(item.get("source") or "").strip(),
                        weight=weight,
                        author_handle=author_hint,
                    )
                )
        return out

    raise last_err or EngineError("lens: 未知失败")


# ---------------------------------------------------------------------------
# 跨引擎证据融合与置信度评分（此处只有 lens 一种来源）
#
# 置信度模型（0-1，封顶）：
# - Lens 完全匹配且指向具体 X 原帖：+0.35
# - Lens 视觉匹配 / 弱证据页面：+0.20
# 任何单条都不足以瞎报，故强证据阈值设为 0.30（见 _STRONG_CONFIDENCE）。
# ---------------------------------------------------------------------------

_X_URL_RE = re.compile(
    r"(?:x\.com|twitter\.com|sotwe\.com|urlebird\.com)/(?:user/)?@?([A-Za-z0-9_]{2,20})(?:[/?]|$)"
)
_STATUS_RE = re.compile(r"(?:x\.com|twitter\.com)/(\w{2,20})/status/(\d+)")

_STOPWORDS = {"status", "search", "user", "home", "intent", "sunna", "sc", "i", "web"}


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


# Lens 标题格式：「<昵称> <平台介词> <平台>: <正文>」，介词随语言变
# （on / sur / auf / على …），所以只把介词当一个 token 跳过。
_LENS_PREP = r"(?:on|sur|auf|no|en|на|على|علی|in|de)"
_LENS_PLAT = (
    r"(?:X|Twitter|Instagram|Facebook|Threads|YouTube|TikTok|Pinterest|"
    r"Reddit|Tumblr|Weibo|Bilibili|微博|小红书)"
)
_LENS_NAME_RE = re.compile(
    rf"^(.{{1,40}}?)\s*{_LENS_PREP}\s+{_LENS_PLAT}\s*[:：]"
)


def _lens_name(title: str) -> str:
    """从 Lens 标题抠显示名：'魔法猫猫王迟迟 على X: "正文"' → '魔法猫猫王迟迟'。"""
    m = _LENS_NAME_RE.match((title or "").strip())
    if not m:
        return ""
    name = m.group(1).strip(" \"'“”‘’-—·")
    return name if 1 < len(name) <= 40 else ""


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
    display_name: str = ""  # Lens 标题里的中文名，优先于 handle 展示


def fuse(candidates: list, phash_scores: Optional[dict] = None) -> list:
    """融合所有候选。此处只处理 lens 来源。phash_scores 保留接口，固定为空。"""
    authors: dict = {}

    def _author(handle: str, platform: str) -> AuthorVerdict:
        key = (handle.lower(), platform)
        if key not in authors:
            authors[key] = AuthorVerdict(handle=handle, platform=platform, confidence=0.0)
        return authors[key]

    for c in candidates:
        status_m = _STATUS_RE.search(c.post_url or "") or _STATUS_RE.search(c.page_url or "")

        if c.engine == "lens":
            # Google Lens 给的是"包含这张图的页面"。纪律和 Yandex 一样：
            # 只认 URL 结构（平台账号页/帖子）和帖子正文里 @ 到的账号，
            # 绝不拿标题猜人名。
            platform = _platform_of(c)
            domain_root = _domain_root(c.page_url)
            url_m = _X_URL_RE.search(c.page_url or "")
            h = ""
            via_post = False
            if c.author_handle:
                # 转图/祝福帖：作者是正文 @ 的那个人，帖子本身属于转发者，
                # 所以不能把这条转发帖的链接当作者的原帖。
                h = _clean(c.author_handle)
                via_post = True
            elif url_m:
                h = _clean(url_m.group(1))
            if h:
                exact = c.weight >= 0.75
                # 平台识别不出时保留「未知」，不再硬算成 X/Twitter（避免误标推特）
                a = _author(h, platform or "未知")
                a.confidence = min(1.0, a.confidence + (0.35 if exact else 0.20))
                a.domains.add(domain_root)
                if not via_post:
                    status_m2 = _STATUS_RE.search(c.page_url or "")
                    if status_m2 and not a.post_url:
                        a.post_url = status_m2.group(0)
                if c.title and not a.display_name and not via_post:
                    nm = _lens_name(c.title)
                    if nm:
                        a.display_name = nm
                a.page_url = a.page_url or c.page_url
                a.evidence.append(
                    f"Lens {'完全匹配' if exact else '视觉匹配'}({domain_root}): "
                    f"{c.page_url[:55]}"
                )

    ranked = sorted(
        authors.values(),
        key=lambda a: (
            -a.confidence,
            -len(a.domains),
            0 if a.post_url else 1,
            a.handle.lower(),
        ),
    )
    return [a for a in ranked if a.confidence >= 0.10]


# 优先平台：找「姬」要的是能去关注的社交账号（twitter / ins / 微博 / 小红书）。
# Pixiv、Danbooru 等图库站不认账（用户明确要求 pixiv 不考虑），
# 「未知/身份(站内人名)」连平台归属都没有，同样排除。
_PREFERRED_PLATFORMS = {"X/Twitter", "Instagram", "微博", "小红书"}

# 强证据阈值：有 Lens 完全匹配 / 具体原帖之一才作为"作者候选"展示
_STRONG_CONFIDENCE = 0.30


def _no_result(verdicts: list) -> str:
    """未命中时的文案：区分「压根没候选」与「候选都不是社交账号」。"""
    if verdicts:
        detail = "命中了图库/画站或身份不明来源，已忽略（只认社交账号）"
    else:
        detail = "换张更清晰的图，或该图全网暂无转载"
    return f"没找到姬 😢 {detail}"


def format_top1(verdicts: list) -> str:
    """只返回置信度最高的「社交平台」作者，单条短文本。

    样式（QQ 文本消息，emoji 走客户端渲染，无 PIL 字体问题）：
        🎯 **lg找姬结果**

        🌐 平台：Twitter
        👤 名字：aqua_cosplay
        🆔 账号：@aqua_cosplay
        https://x.com/aqua_cosplay

    两条筛选原则：
    1) 平台优先 —— 在社交平台候选中取置信度最高者；Pixiv 即使分数最高也不报。
    2) 置信度低于 _STRONG_CONFIDENCE 视为没找到 —— 弱证据多为转发者，不乱报。
    """
    items = _verdict_items(verdicts)
    if not items:
        return _no_result(verdicts)
    if items[0]["weak"]:
        return "没找到姬 😢 最像的账号可信度不足，不敢乱报"
    return _render_one(items[0])


def _render_one(item: dict) -> str:
    """单个结果渲染成短文本：标题下紧跟 finded.png，再列平台/名字/账号。"""
    lines = [RESULT_TITLE]
    img = finded_cq()
    if img:
        lines.append(img)
    lines += [
        "",
        f"🌐 平台：{item['platform']}",
        f"👤 名字：{item['name']}",
        f"🆔 账号：@{item['handle']}",
    ]
    if item.get("url"):
        lines.append(item["url"])
    return "\n".join(lines)


def _render_all(items: list) -> str:
    """多个候选渲染成单条文本：标题+配图只一次，随后逐段列出（弱证据跳过）。"""
    show = [it for it in items if not it.get("weak")]
    if not show:
        show = list(items)  # 全弱证据时也照列，交由调用方决定是否判没找到
    lines = [RESULT_TITLE]
    img = finded_cq()
    if img:
        lines.append(img)
    lines.append("")
    for i, it in enumerate(show, 1):
        if len(show) > 1:
            lines.append(f"——— #{i} {it['platform']} ———")
        lines += [
            f"🌐 平台：{it['platform']}",
            f"👤 名字：{it['name']}",
            f"🆔 账号：@{it['handle']}",
        ]
        if it.get("url"):
            lines.append(it["url"])
    return "\n".join(lines)


def _verdict_items(verdicts: list) -> list:
    """融合判定 -> 可展示项列表（verdicts 已按置信度降序，此处保持顺序）。

    只保留社交平台；低于强证据阈值的标 weak=True —— 单条文本会直接判没找到，
    但调试用的合并转发会照样列出来，方便看清楚到底命中了什么。
    """
    items = []
    for v in verdicts or []:
        # 只认社交平台；识别不出的标「未知平台」也保留（不再冒充推特），弱证据阈值再筛
        if v.platform not in _PREFERRED_PLATFORMS and v.platform != "未知":
            continue
        plat = "未知平台" if v.platform == "未知" else v.platform
        items.append(
            {
                "platform": "Twitter" if v.platform == "X/Twitter" else plat,
                "name": v.display_name or v.handle,
                "handle": v.handle,
                "url": v.post_url or v.page_url,
                "score": round(float(v.confidence), 3),
                "weak": v.confidence < _STRONG_CONFIDENCE,
                "evidence": list(v.evidence)[:3],
            }
        )
    return items


def _platform_of(c: PostCandidate) -> str:
    d = c.page_url.split("/")[2] if "://" in c.page_url else ""
    table = [
        ("x.com", "X/Twitter"),
        ("twitter.com", "X/Twitter"),
        ("sotwe.com", "X/Twitter"),
        ("urlebird.com", "X/Twitter"),
        ("weibo.com", "微博"),
        ("weibo.cn", "微博"),
        ("sinaimg.cn", "微博"),
        ("xiaohongshu.com", "小红书"),
        ("xhscdn.com", "小红书"),
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


# ---------------------------------------------------------------------------
# 主路：mac 侧浏览器 sidecar（tools/fish_sidecar/server.mjs）
#
# bot 跑在无 GUI 的 Ubuntu 虚拟机里，起不了浏览器，也就没法直连 Google Lens；
# 而 mac 上有真实浏览器、链路已跑通。所以把「鱼鱼找鸡」当独立服务常驻 mac，
# VM 把图片字节 POST 过去取 JSON 结果。
#
# 这么做而不是把 2324 行 JS 移植成 Python：零移植工作量、不污染插件依赖
# （不引 playwright / 不装 chromium），进程隔离（sidecar 崩了不影响 bot）。
# 它也不吃 SerpApi 的 100 次/月额度；mac 关机或超时则自动降级到 SerpApi。
# ---------------------------------------------------------------------------

# Node 侧 matchPlatform 给出的 platform 取值 -> (展示名, 权重)
# 权重沿用参考插件的 PLATFORM_WEIGHT：twitter 最优先，pixiv 最末。
_SIDECAR_PLATFORM = {
    "twitter": ("X/Twitter", 1),
    "weibo": ("微博", 2),
    "bilibili": ("B站", 3),
    "instagram": ("Instagram", 4),
    "pixiv": ("Pixiv", 9),
}
# pixiv 是画师作品号，不是要找的人的社交主页 —— 用户明确要求不考虑。
_SIDECAR_SKIP = {"pixiv"}


async def search_via_sidecar(image_bytes: bytes, cfg: dict) -> dict:
    """调 mac 侧 sidecar 反查，返回 {"ok","accounts","error","lens_failed"}。

    ok=True 表示链路通了（账号可能为空）；ok=False 表示主路不可用，调用方应
    降级到 SerpApi。任何异常都在此吞掉并转成 ok=False，不向上抛。
    """
    url = (cfg.get("sidecar_url") or "").strip().rstrip("/")
    token = (cfg.get("sidecar_token") or "").strip()
    timeout = int(cfg.get("sidecar_timeout") or 90)
    out = {"ok": False, "accounts": [], "error": "", "lens_failed": False}
    if not url:
        out["error"] = "未配置 lens_sidecar_url"
        return out

    headers = {"Content-Type": "image/jpeg"}
    if token:
        headers["x-fish-token"] = token
    # 过局域网前先压图：QQ 原图常 8MB+，传过去再给浏览器上传会很慢
    payload = shrink_for_upload(image_bytes)

    try:
        async with _make_client(timeout, "") as sess:
            resp = await sess.post(f"{url}/find", content=payload, headers=headers)
    except Exception as e:  # noqa: BLE001 - 主路不可用时降级，不算致命
        out["error"] = f"{type(e).__name__}: {str(e)[:80]}"
        return out

    if resp.status_code != 200:
        out["error"] = f"HTTP {resp.status_code} {(resp.text or '')[:80]}"
        return out
    try:
        data = resp.json()
    except Exception as e:  # noqa: BLE001
        out["error"] = f"非 JSON 响应: {str(e)[:60]}"
        return out
    if not data.get("ok"):
        out["error"] = str(data.get("error") or "sidecar 返回 ok=false")[:120]
        return out

    out["ok"] = True
    out["accounts"] = data.get("accounts") or []
    out["lens_failed"] = any(
        str((f or {}).get("engine", "")).lower() == "lens"
        for f in (data.get("failures") or [])
    )
    return out


def format_sidecar(accounts: list) -> str:
    """把 sidecar 返回的账号列表格式化成与 SerpApi 路一致的短文本。

    取平台权重最优（twitter > 微博 > B站 > Instagram）的那个账号，pixiv 忽略。
    无可用账号时返回空串，交由调用方决定是"没找到"还是降级重试。
    """
    items = _sidecar_items(accounts)
    if not items:
        return ""
    return _render_one(items[0])


def _sidecar_items(accounts: list) -> list:
    """sidecar 账号 -> 可展示项，按平台权重升序（twitter 最优先），pixiv 忽略。"""
    packed = []
    for a in accounts or []:
        if not isinstance(a, dict):
            continue
        pid = str(a.get("platform") or "").strip().lower()
        name_weight = _SIDECAR_PLATFORM.get(pid)
        if not name_weight or pid in _SIDECAR_SKIP:
            continue
        platform, weight = name_weight
        handle = str(a.get("handle") or "").lstrip("@").strip()
        if not handle:
            continue
        author = str(a.get("authorName") or "").strip()
        packed.append(
            (
                weight,
                {
                    "platform": "Twitter" if platform == "X/Twitter" else platform,
                    "name": author or handle,
                    "handle": handle,
                    "url": str(a.get("url") or ""),
                    "score": None,  # sidecar 不给置信度，只给平台优先级
                    "weak": False,
                    "evidence": [],
                },
            )
        )
    packed.sort(key=lambda x: x[0])
    return [item for _, item in packed]


# ---------------------------------------------------------------------------
# 编排：压缩 -> Google Lens -> 融合 -> 文本结果
# ---------------------------------------------------------------------------


def render_text(detail: dict) -> str:
    """结构化结果 -> 单条文本：取置信度最高的候选（top1），否则用 message。"""
    if detail.get("ok") and detail.get("items"):
        for it in detail["items"]:
            if not it.get("weak"):
                return _render_one(it)
        return _render_one(detail["items"][0])  # 全弱也保底展示第一条
    return detail.get("message") or "没找到姬 😢"


async def reverse_search_detail(image_bytes: bytes) -> dict:
    """一次完整的反向图片搜索，返回结构化结果（不拼文本）。

    返回：
        {
          "ok": bool,          # 是否够格判为「找到了」（弱证据时为 False）
          "source": str,       # "sidecar" / "serpapi" / ""
          "items": [...],      # 已按优先级排序；每项 platform/name/handle/url/
                               # score/weak/evidence。弱证据也会列出来供调试
          "message": str,      # ok=False 时的文案
          "debug": {...},      # 引擎、耗时、候选数、sidecar 报错等
        }

    双路：
    1) 主路 —— mac 侧浏览器 sidecar（配了 lens_sidecar_url 才启用）。把压过的
       图 POST 给常驻 mac 的 Node 服务，拿它解出的账号直接出结论。不吃 SerpApi
       额度；mac 关机 / 超时 / 报错一律记日志后落到第 2 路。
    2) 兜底 —— SerpApi（Google Lens）。挑选当月未满额的 key（计数 +1 已原子
       完成）-> 压缩原图 -> 必要时上传图床 -> 融合评分。

    额度纪律：仅在 SerpApi 真正请求成功后才保留计数；上传图床等前置失败会
    自动退回，不白白消耗免费次数。主路「通了但没找到账号」时也不再走兜底，
    除非是 lens 引擎本身失败（captcha 之类）才值得重试。
    任何环节失败只记日志，不抛出。
    """
    started = time.time()
    cfg = load_config()
    timeout = int(cfg.get("timeout", 25))
    proxy = cfg.get("proxy", "") or ""
    keys = cfg.get("serpapi_keys") or []
    out = {"ok": False, "source": "", "items": [], "message": "", "debug": {}}

    def done(message: str = "") -> dict:
        out["debug"]["elapsed"] = round(time.time() - started, 2)
        if message:
            out["message"] = message
        return out

    # ── 主路：mac 侧浏览器 sidecar（不消耗 SerpApi 免费额度）──
    if (cfg.get("sidecar_url") or "").strip():
        res = await search_via_sidecar(image_bytes, cfg)
        out["debug"]["sidecar"] = {
            "ok": res["ok"],
            "error": res["error"],
            "lens_failed": res["lens_failed"],
            "raw_accounts": len(res["accounts"] or []),
            "raw": res["accounts"],
        }
        if res["ok"]:
            items = _sidecar_items(res["accounts"])
            if items:
                out.update(ok=True, source="sidecar", items=items)
                return done()
            # 链路通了但没账号：若 lens 引擎本身失败（captcha / 网络），
            # 值得再让 SerpApi 试一次；否则认定"确实没找到"，省下免费额度。
            if not res["lens_failed"]:
                return done("没找到姬 😢 换张更清晰的图，或该图全网暂无转载")
            logger.warning("[找姬] sidecar 的 lens 引擎失败，降级 SerpApi 重试")
        else:
            logger.warning(f"[找姬] sidecar 不可用，降级 SerpApi：{res['error']}")

    # ── 兜底：SerpApi（受每 key 100 次/月额度保护）──
    if not keys:
        return done(
            "未配置 serpapi_keys，Google Lens 无法使用。\n"
            "在 .env 填入 SERPAPI_KEYS=[\"k1\",\"k2\"]（https://serpapi.com，免费 100 次/月/key）。"
        )

    # 挑一个当月未满 100 次的 key（计数 +1 已原子完成）；全用完则暂停本功能
    key, fp, remain = await _acquire_serpapi_key(cfg)
    if not key:
        total = len(keys) * _SERPAPI_FREE_LIMIT
        return done(
            f"本月 SerpApi 免费额度已用完 😢（共 {len(keys)} 个 key，每个 100 次/月，"
            f"合计 {total} 次）。次月 1 号自动重置，请稍后再试。"
        )

    up_bytes = shrink_for_upload(image_bytes)

    try:
        candidates = await search_lens(
            image_bytes=up_bytes,
            api_key=key,
            timeout=timeout,
            proxy=proxy,
        )
    except Exception as e:
        # 上传图床 / SerpApi 调用失败：退回刚才占用的额度，本次不计数
        await _refund_serpapi_key(fp)
        logger.warning(f"[找姬] Lens 失败: {e}")
        return done(f"搜索失败：{str(e)}")

    if not candidates:
        # SerpApi 已成功返回（只是没匹配到），额度照常消耗
        return done("没找到可靠的出处 😢 换张更清晰的图，或该图全网暂无转载")

    verdicts = fuse(candidates, None)
    items = _verdict_items(verdicts)
    out["debug"]["serpapi"] = {
        "candidates": len(candidates),
        "verdicts": len(verdicts),
        "remaining": remain,
    }
    if not items:
        return done(_no_result(verdicts))
    # top1 弱证据即判没找到（只报最高置信度那条）；items 仍带回供调试查看
    if items[0]["weak"]:
        out.update(source="serpapi", items=items)
        return done("没找到姬 😢 最像的账号可信度不足，不敢乱报")

    out.update(ok=True, source="serpapi", items=items)
    return done()


async def reverse_search_all(image_bytes: bytes) -> str:
    """一次完整的反向图片搜索，返回可直接发送的单条文本。

    与 reverse_search_detail 的区别：那个给结构化数据（合并转发展示多项用），
    这个只给拼好的单条短文本。两者共用同一套编排与额度逻辑。
    """
    return render_text(await reverse_search_detail(image_bytes))
