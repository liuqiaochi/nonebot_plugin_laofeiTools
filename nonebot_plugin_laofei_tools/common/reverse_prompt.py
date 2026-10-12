"""
图反推提示词

调用 DeepSeek 视觉模型，从用户发送/引用的图片反推可用于 NovelAI 的 AI 绘画提示词。
权限：普通用户可用，但仅「已开启 AI 功能（ai_chat 模块）」的群生效；私聊不受群开关限制。
依赖：deepseek_api_key 已配置。
注意：视觉模型与对话模型（config.deepseek_model）解耦，视觉模型固定使用 VISION_MODEL 常量（DeepSeek-V4.1-Flash，当前官方唯一视觉 ID）。
"""

import asyncio
import base64
from io import BytesIO

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, MessageEvent, Message, MessageSegment
from nonebot.log import logger
from nonebot.matcher import Matcher
from PIL import Image

from ..config import is_ai_group_enabled
from .utils import download_image
from .ai_chat import _get_client

# 视觉模型固定 slug，与对话模型（config.deepseek_model）分离。
# DeepSeek-V4.1 起视觉能力已原生并入 deepseek-flash（即 V4.1-Flash），不再有独立 vision slug；
# 旧的 deepseek-v4-flash-vision-exp 已于 2026-09-10 退役，仅作兼容别名路由到 4.1-Flash。
# 故视觉模型固定为 deepseek-flash（当前官方视觉 ID），对话模型即使配 deepseek-chat 纯文本也不影响图反推。
VISION_MODEL = "deepseek-flash"

# 图反推全局串行队列：同一时刻仅一个任务访问视觉模型，其余并发请求排队（避免打爆 DeepSeek API）
_reverse_lock = asyncio.Lock()

# 反推提示词的指令：要求输出 Danbooru 风格、逗号分隔的英文标签，覆盖关键维度
REVERSE_PROMPT_INSTRUCTION = (
    "请用逗号分隔的英文标签，模仿 Danbooru 风格，输出用于 AI 绘画（NovelAI）的提示词。"
    "必须涵盖以下维度：主体/角色、发色与发型、服装与配饰、表情、动作与姿势、"
    "背景与场景、构图与镜头、画质增强 tag（如 masterpiece, best quality）。"
    "只输出提示词本身，不要任何解释、前缀或引号。"
)


reverse_prompt_cmd = on_command(
    "图反推",
    aliases={"反推提示词", "图片反推", "提示词反推"},
    priority=5,
    block=True,
    force_whitespace=True,
)


@reverse_prompt_cmd.handle()
async def handle_reverse_prompt(
    matcher: Matcher,
    bot: Bot,
    event: MessageEvent,
):
    """从消息内或引用消息中的图片反推 AI 绘画提示词（仅已开启 AI 功能的群可用）"""
    # 0. 群聊开关守卫：仅「已开启 AI 功能（ai_chat 模块）」的群可用；私聊放行
    gid = getattr(event, "group_id", None)
    if gid is not None and not is_ai_group_enabled(str(gid)):
        # 统一开关层约定：模块关闭时静默拦截，不回复
        await matcher.finish()
        return

    # 1. 提取图片（优先消息内图片，其次引用图片）
    image_url = _extract_image_url(event)
    if not image_url:
        await matcher.finish("请发送一张图片（直接附带或引用图片）后发送「图反推」")
        return

    await matcher.send("正在反推提示词，请稍候...")

    # 进入队列：同一时刻仅一个图反推任务访问视觉模型，其余并发请求排队串行
    async with _reverse_lock:
        # 2. 下载图片
        try:
            image_data = await download_image(bot, image_url)
        except Exception as e:
            logger.error(f"图反推图片下载失败: {e}")
            await matcher.finish("图片下载失败，请重试")
            return

        if not image_data:
            await matcher.finish("图片下载失败，请重试")
            return

        # 统一转码为 PNG（DeepSeek 明确支持的格式），并校验字节确为可解码图片
        try:
            image_data = _normalize_image(image_data)
        except Exception as e:
            logger.error(f"图反推图片解码失败: {e}")
            await matcher.finish("图片无法解码，请换一张图重试")
            return

        # 3. 调用 DeepSeek 视觉模型反推提示词
        try:
            client = _get_client()
            data_url = f"data:image/png;base64,{base64.b64encode(image_data).decode()}"
            # 注意：OpenAI SDK 的 chat.completions.create 是**同步阻塞**调用，
            # 必须放进线程池（asyncio.to_thread）以免冻结整个事件循环；
            # 视觉请求比纯文本慢，且不设 timeout 会永久挂起，故显式 timeout=60。
            # 关键：DeepSeek 视觉模型（deepseek-flash = V4.1-Flash）默认开启“思考模式”，
            # 会把整段 max_tokens 预算花在 reasoning_content 上，
            # 导致最终 content 为空（finish_reason=length）。显式关闭思考模式，
            # 确保直接输出提示词（已确认是官方视觉模型的已知行为，非本插件问题）。
            response = await asyncio.to_thread(
                client.chat.completions.create,
                model=VISION_MODEL,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": REVERSE_PROMPT_INSTRUCTION},
                            {"type": "image_url", "image_url": {"url": data_url}},
                        ],
                    }
                ],
                temperature=0.5,
                max_tokens=800,
                timeout=60,
                extra_body={"thinking": {"type": "disabled"}},
            )
            prompt = (response.choices[0].message.content or "").strip()
        except ValueError as e:
            # DeepSeek API Key 未配置
            await matcher.finish(f"图反推配置错误：{e}")
            return
        except Exception as e:
            logger.error(f"图反推调用 DeepSeek 失败: {e}")
            # 直接回显异常类型与信息便于排查，而非一律“请稍后再试”
            await matcher.finish(f"反推失败：{type(e).__name__} - {e}")
            return

        if not prompt:
            # 诊断：记录并向用户回显 finish_reason / 思考模式状态，便于排查
            try:
                choice = response.choices[0]
                fr = getattr(choice, "finish_reason", None)
                has_reason = bool(getattr(choice.message, "reasoning_content", None))
                logger.warning(
                    f"图反推返回空内容: model={VISION_MODEL}, finish_reason={fr}, "
                    f"有reasoning_content={has_reason}（多为视觉模型思考模式耗尽预算）"
                )
                detail = f"（finish_reason={fr}，思考模式={has_reason}）"
            except Exception:
                detail = ""
            await matcher.finish(f"未能反推出提示词，请换一张图重试{detail}")
            return

        # 4. 返回结果（直接返回文本便于复制）
        msg = Message()
        if getattr(event, "message_id", None):
            msg.append(MessageSegment.reply(event.message_id))
        msg.append(
            MessageSegment.text(f"以下为反推提示词（可直接用于 NovelAI）：\n\n{prompt}")
        )
        await matcher.finish(msg)


def _extract_image_url(event: MessageEvent) -> str:
    """从消息内或引用消息中提取第一张图片的 url/file 字段"""
    # 优先消息内图片
    for seg in event.message:
        if seg.type == "image":
            url = seg.data.get("url") or seg.data.get("file")
            if url:
                return url
    # 其次引用消息中的图片
    reply = getattr(event, "reply", None)
    if reply and getattr(reply, "message", None):
        for seg in reply.message:
            if seg.type == "image":
                url = seg.data.get("url") or seg.data.get("file")
                if url:
                    return url
    return ""


def _normalize_image(data: bytes, max_side: int = 1280) -> bytes:
    """将任意 PIL 可解码的图片解码并统一重编码为 PNG（DeepSeek 明确支持的格式）。

    作用：
    - 不再依赖扩展名/文件头“猜测” MIME，从根本上避免“实际字节非 webp/png/jpeg/gif
      却标成 image/jpeg 被 DeepSeek 拒图”的问题；
    - 同时校验字节确为可解码图片（若下载到的是错误 HTML 页等，会在这一步抛异常）；
    - 超长边超过 max_side 时等比缩图，降低 base64 体积与视觉 token 消耗。
    """
    img = Image.open(BytesIO(data))
    img = img.convert("RGB")  # 丢弃 alpha / 动画帧，统一为静态 RGB
    w, h = img.size
    if max(w, h) > max_side:
        scale = max_side / float(max(w, h))
        img = img.resize(
            (max(1, int(w * scale)), max(1, int(h * scale))),
            Image.LANCZOS,
        )
    out = BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()
