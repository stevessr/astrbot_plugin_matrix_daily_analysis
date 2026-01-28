"""
matrix 群日常分析插件
基于群聊记录生成精美的日常分析报告，包含话题总结、用户画像、统计数据等

重构版本 - 使用模块化架构
"""

import asyncio
import importlib
import json
import re

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
from astrbot.core.star.filter.permission import PermissionType

from .src.core.bot_manager import BotManager

# 导入重构后的模块
from .src.core.config import ConfigManager
from .src.reports.generators import ReportGenerator
from .src.scheduler.auto_scheduler import AutoScheduler
from .src.scheduler.retry import RetryManager
from .src.utils.helpers import MessageAnalyzer
from .src.utils.pdf_utils import PDFInstaller

DEFAULT_DIALOGUE_POLL_PROMPT = (
    "你是群聊文风模仿器。根据下面的聊天记录，生成一个单选投票：给出一个简短的问题 (question)，"
    "以及 {option_count} 条候选发言 (options)。候选发言必须是‘嘎啦给目’风格，语气俏皮、有点碎碎念，但不要冒犯。"
    "不要@具体用户，不要包含隐私或敏感信息。每条候选发言 6-20 字。只输出 JSON 数组，且只包含一个对象，"
    '格式如下：[{"question":"...","options":["...","..."]}]。\\n\\n聊天记录：\\n{history_text}'
)
POLL_EVENT_TYPE_STABLE = "m.poll.start"
POLL_POLL_KEY_STABLE = "m.poll"
POLL_EVENT_TYPE_UNSTABLE = "org.matrix.msc3381.poll.start"
POLL_POLL_KEY_UNSTABLE = "org.matrix.msc3381.poll.start"


def _safe_import(module_path: str):
    try:
        return importlib.import_module(module_path)
    except ModuleNotFoundError as e:
        if module_path == e.name or module_path.startswith(f"{e.name}."):
            return None
        raise


def _import_matrix_adapter_module(module_path: str):
    for base in (
        "astrbot_plugin_matrix_adapter",
        "data.plugins.astrbot_plugin_matrix_adapter",
    ):
        module = _safe_import(f"{base}.{module_path}" if module_path else base)
        if module is not None:
            return module
    return None


@register(
    "astrbot_plugin_matrix_daily_analysis",
    "stevessr",
    "matrix 群日常分析总结插件 - 生成精美的群聊分析报告，支持话题分析、用户形象、群聊圣经等功能",
    "v0.0.1",
    "https://github.com/stevessr/astrbot_plugin_matrix_daily_analysis",
)
class matrixGroupDailyAnalysis(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

        # 初始化模块化组件（使用实例属性而非全局变量）
        self.config_manager = ConfigManager(config)
        self.bot_manager = BotManager(self.config_manager)
        self.bot_manager.set_context(context)
        self.message_analyzer = MessageAnalyzer(
            context, self.config_manager, self.bot_manager
        )
        self.report_generator = ReportGenerator(self.config_manager)
        self.retry_manager = RetryManager(
            self.bot_manager, self.html_render, self.report_generator
        )
        self.auto_scheduler = AutoScheduler(
            self.config_manager,
            self.message_analyzer.message_handler,
            self.message_analyzer,
            self.report_generator,
            self.bot_manager,
            self.retry_manager,
            self.html_render,  # 传入 html_render 函数
        )

        # 延迟启动自动调度器，给系统时间初始化
        if self.config_manager.get_enable_auto_analysis():
            asyncio.create_task(self._delayed_start_scheduler())

        logger.info("matrix 群日常分析插件已初始化（模块化版本）")

    def _ensure_components(self):
        """在热重载或异常后恢复核心组件。"""
        if self.config_manager is None:
            self.config_manager = ConfigManager(self.config)
        if self.bot_manager is None:
            self.bot_manager = BotManager(self.config_manager)
            self.bot_manager.set_context(self.context)
        if self.message_analyzer is None:
            self.message_analyzer = MessageAnalyzer(
                self.context, self.config_manager, self.bot_manager
            )
        if self.report_generator is None:
            self.report_generator = ReportGenerator(self.config_manager)
        if self.retry_manager is None:
            self.retry_manager = RetryManager(
                self.bot_manager, self.html_render, self.report_generator
            )
        if self.auto_scheduler is None:
            self.auto_scheduler = AutoScheduler(
                self.config_manager,
                self.message_analyzer.message_handler,
                self.message_analyzer,
                self.report_generator,
                self.bot_manager,
                self.retry_manager,
                self.html_render,
            )

    async def _delayed_start_scheduler(self):
        """延迟启动调度器，给系统时间初始化"""
        try:
            # 等待 30 秒让系统完全初始化
            await asyncio.sleep(30)

            # 初始化所有 bot 实例
            discovered = await self.bot_manager.initialize_from_config()
            if discovered:
                platform_count = len(discovered)
                logger.info(f"Bot 管理器初始化成功，发现 {platform_count} 个适配器")
                for platform_id, bot_instance in discovered.items():
                    logger.info(
                        f"  - 平台 {platform_id}: {type(bot_instance).__name__}"
                    )

                # 启动调度器
                await self.auto_scheduler.start_scheduler()
            else:
                logger.warning("Bot 管理器初始化失败，未发现任何适配器")
                status = self.bot_manager.get_status_info()
                logger.info(f"Bot 管理器状态：{status}")

            # 始终启动重试管理器，确保手动触发也能使用重试队列
            await self.retry_manager.start()

        except Exception as e:
            logger.debug(f"延迟启动调度器失败，可能由于短时间内多次更新插件配置：{e}")

    async def terminate(self):
        """插件被卸载/停用时调用，清理资源"""
        try:
            logger.info("开始清理 matrix 群日常分析插件资源...")

            # 停止自动调度器
            if self.auto_scheduler:
                logger.info("正在停止自动调度器...")
                await self.auto_scheduler.stop_scheduler()
                logger.info("自动调度器已停止")

            if self.retry_manager:
                await self.retry_manager.stop()

            # 重置实例属性
            self.auto_scheduler = None
            self.bot_manager = None
            self.message_analyzer = None
            self.report_generator = None
            self.config_manager = None

            logger.info("matrix 群日常分析插件资源清理完成")

        except Exception as e:
            logger.error(f"插件资源清理失败：{e}")

    @filter.command("群分析")
    @filter.permission_type(PermissionType.ADMIN)
    async def analyze_group_daily(
        self, event: AstrMessageEvent, days: int | None = None
    ):
        """
        分析群聊日常活动
        用法：/群分析 [天数]
        """
        self._ensure_components()
        if self.config_manager is None:
            self._ensure_components()
        if self.config_manager is None:
            yield event.plain_result("❌ 配置初始化失败，请重启插件后重试")
            return
        platform_name = event.get_platform_name()
        if platform_name != "matrix":
            yield event.plain_result("❌ 此功能仅支持 Matrix 群聊/房间")
            return

        group_id = event.session.session_id
        if not group_id:
            yield event.plain_result("❌ 请在群聊中使用此命令")
            return

        # 更新 bot 实例（用于手动命令）
        self.bot_manager.update_from_event(event)
        if not self.bot_manager.has_bot_instance():
            await self.bot_manager.auto_discover_bot_instances()

        # 检查群组权限
        if not self.config_manager.is_group_allowed(group_id):
            yield event.plain_result("❌ 此群未启用日常分析功能")
            return

        # 设置分析天数
        analysis_days = (
            days if days and 1 <= days <= 7 else self.config_manager.get_analysis_days()
        )

        yield event.plain_result(f"🔍 开始分析群聊近{analysis_days}天的活动，请稍候...")

        # 调试：输出当前配置
        logger.info(f"当前输出格式配置：{self.config_manager.get_output_format()}")

        try:
            # 获取该群对应的平台 ID 和 bot 实例
            platform_id = await self.auto_scheduler.get_platform_id_for_group(group_id)
            if not platform_id and hasattr(event, "get_platform_id"):
                platform_id = event.get_platform_id()
            bot_instance = self.bot_manager.get_bot_instance(platform_id)

            if not bot_instance:
                yield event.plain_result(
                    f"❌ 未找到群 {group_id} 对应的 bot 实例（平台：{platform_id}）"
                )
                return

            # 获取群聊消息
            messages = await self.message_analyzer.message_handler.fetch_group_messages(
                bot_instance, group_id, analysis_days, platform_id
            )
            if not messages:
                yield event.plain_result(
                    "❌ 未找到足够的群聊记录，请确保群内有足够的消息历史"
                )
                return

            # 检查消息数量是否足够分析
            min_threshold = self.config_manager.get_min_messages_threshold()
            if len(messages) < min_threshold:
                yield event.plain_result(
                    f"❌ 消息数量不足（{len(messages)}条），至少需要{min_threshold}条消息才能进行有效分析"
                )
                return

            yield event.plain_result(
                f"📊 已获取{len(messages)}条消息，正在进行智能分析..."
            )

            # 进行分析 - 传递 unified_msg_origin 以获取正确的 LLM 提供商
            analysis_result = await self.message_analyzer.analyze_messages(
                messages, group_id, event.unified_msg_origin
            )

            # 检查分析结果
            if not analysis_result or not analysis_result.get("statistics"):
                yield event.plain_result("❌ 分析过程中出现错误，请稍后重试")
                return

            # 生成报告
            output_format = self.config_manager.get_output_format()
            if output_format == "image":
                (
                    image_url,
                    html_content,
                ) = await self.report_generator.generate_image_report(
                    analysis_result, group_id, self.html_render
                )
                if image_url:
                    # Matrix 平台发送图片（上传后发送）
                    try:
                        logger.info(f"正在尝试发送图片报告：{image_url}")
                        sent = await self.auto_scheduler._send_image_message(
                            group_id, image_url
                        )
                        if sent:
                            logger.info(f"图片报告发送成功：{group_id}")
                        elif html_content:
                            yield event.plain_result(
                                "[AstrBot matrix 群日常分析总结插件] ⚠️ 图片报告发送失败，已加入重试队列。"
                            )
                            platform_id = (
                                await self.auto_scheduler.get_platform_id_for_group(
                                    group_id
                                )
                            )
                            await self.retry_manager.add_task(
                                html_content, analysis_result, group_id, platform_id
                            )
                        else:
                            yield event.plain_result(
                                "❌ 图片发送失败，且无法进行重试（无 HTML 内容）。"
                            )
                    except Exception as send_err:
                        logger.error(f"图片报告发送失败：{send_err}")
                        if html_content:
                            yield event.plain_result(
                                "[AstrBot matrix 群日常分析总结插件] ⚠️ 图片报告发送异常，已加入重试队列。"
                            )
                            platform_id = (
                                await self.auto_scheduler.get_platform_id_for_group(
                                    group_id
                                )
                            )
                            await self.retry_manager.add_task(
                                html_content, analysis_result, group_id, platform_id
                            )
                        else:
                            yield event.plain_result(
                                f"❌ 图片发送失败：{send_err}，且无法进行重试（无 HTML 内容）。"
                            )

                elif html_content:
                    # 生成失败但有 HTML，加入重试队列
                    logger.warning("图片报告生成失败，加入重试队列")
                    yield event.plain_result(
                        "[AstrBot matrix 群日常分析总结插件] ⚠️ 图片报告暂无法生成，已加入重试队列，稍后将自动重试发送。"
                    )
                    # 获取 platform_id
                    platform_id = await self.auto_scheduler.get_platform_id_for_group(
                        group_id
                    )
                    await self.retry_manager.add_task(
                        html_content, analysis_result, group_id, platform_id
                    )
                else:
                    # 如果图片生成失败且无 HTML，回退到文本报告
                    logger.warning("图片报告生成失败（无 HTML），回退到文本报告")
                    text_report = self.report_generator.generate_text_report(
                        analysis_result
                    )
                    yield event.plain_result(
                        f"[AstrBot matrix 群日常分析总结插件] ⚠️ 图片报告生成失败，以下是文本版本：\n\n{text_report}"
                    )
            elif output_format == "pdf":
                if not self.config_manager.playwright_available:
                    yield event.plain_result(
                        "❌ PDF 功能不可用，请使用 /安装 PDF 命令安装依赖"
                    )
                    return

                pdf_path = await self.report_generator.generate_pdf_report(
                    analysis_result, group_id
                )
                if pdf_path:
                    sent = await self.auto_scheduler._send_pdf_file(group_id, pdf_path)
                    if not sent:
                        logger.warning("PDF 发送失败，回退到文本报告")
                        text_report = self.report_generator.generate_text_report(
                            analysis_result
                        )
                        yield event.plain_result(
                            f"\n📝 以下是文本版本的分析报告：\n\n{text_report}"
                        )
                else:
                    # 如果 PDF 生成失败，提供详细的错误信息和解决方案
                    # yield event.plain_result("❌ PDF 报告生成失败")
                    # yield event.plain_result("🔧 可能的解决方案：")
                    # yield event.plain_result("1. 使用 /安装 PDF 命令重新安装依赖")
                    # yield event.plain_result("2. 检查网络连接是否正常")
                    # yield event.plain_result("3. 暂时使用图片格式：/设置格式 image")

                    # 回退到文本报告
                    logger.warning("PDF 报告生成失败，回退到文本报告")
                    text_report = self.report_generator.generate_text_report(
                        analysis_result
                    )
                    yield event.plain_result(
                        f"\n📝 以下是文本版本的分析报告：\n\n{text_report}"
                    )
            else:
                text_report = self.report_generator.generate_text_report(
                    analysis_result
                )
                yield event.plain_result(text_report)

        except Exception as e:
            logger.error(f"群分析失败：{e}", exc_info=True)
            yield event.plain_result(
                f"❌ 分析失败：{str(e)}。请检查网络连接和 LLM 配置，或联系管理员"
            )

    def _format_messages_for_dialogue_prompt(
        self, messages: list[dict], max_messages: int = 120
    ) -> str:
        """将消息整理为对话提示词文本。"""
        prefixes = [
            prefix.lower().strip()
            for prefix in self.config_manager.get_history_filter_prefixes()
            if isinstance(prefix, str) and prefix.strip()
        ]
        user_filters = {
            user.lower().strip()
            for user in self.config_manager.get_history_filter_users()
            if isinstance(user, str) and user.strip()
        }
        skip_bot = self.config_manager.should_skip_history_bots()
        entries: list[tuple[float, str, str]] = []
        for msg in messages:
            sender = (
                msg.get("sender", {}).get("nickname")
                or msg.get("sender", {}).get("user_id")
                or "匿名"
            )
            msg_time = msg.get("time", 0) or 0
            sender_id = str(msg.get("sender", {}).get("user_id") or "").strip()
            for content in msg.get("message", []):
                if content.get("type") != "text":
                    continue
                text = content.get("data", {}).get("text", "").strip()
                if not text:
                    continue
                if self._should_skip_history_message(
                    sender_id, text, prefixes, user_filters, skip_bot
                ):
                    continue
                if len(text) > 80:
                    text = text[:77] + "..."
                entries.append((msg_time, sender, text))

        if not entries:
            return ""

        entries.sort(key=lambda x: x[0])
        recent = entries[-max_messages:]
        lines = [f"{sender}: {text}" for _, sender, text in recent]
        return "\n".join(lines)

    def _should_skip_history_message(
        self,
        sender_id: str,
        text: str,
        prefixes: list[str],
        user_filters: set[str],
        skip_bot: bool,
    ) -> bool:
        """基于配置决定是否跳过该条历史消息。"""
        if skip_bot and sender_id and self.bot_manager:
            if self.bot_manager.should_filter_bot_message(sender_id):
                return True
        if sender_id and sender_id.lower() in user_filters:
            return True
        lower_text = text.lower().lstrip()
        for prefix in prefixes:
            if prefix and lower_text.startswith(prefix):
                return True
        return False

    def _build_dialogue_poll_prompt(self, history_text: str, option_count: int) -> str:
        """构造对话投票的 LLM 提示词。"""
        template = (
            self.config_manager.get_dialogue_poll_prompt()
            or DEFAULT_DIALOGUE_POLL_PROMPT
        )
        try:
            return template.replace("{option_count}", str(option_count)).replace(
                "{history_text}", history_text
            )
        except Exception as e:
            logger.warning(f"对话投票提示词格式化失败，回退默认提示词：{e}")
            return DEFAULT_DIALOGUE_POLL_PROMPT.replace(
                "{option_count}", str(option_count)
            ).replace("{history_text}", history_text)

    def _parse_dialogue_poll_json(self, text: str) -> tuple[str, list[str]] | None:
        """解析 LLM 输出的投票 JSON。"""
        from .src.analysis.utils.json_utils import fix_json

        if not text:
            return None
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if not match:
            logger.warning("对话投票 JSON 匹配失败，未找到数组结构")
            return None
        json_text = fix_json(match.group())
        logger.debug(f"对话投票 JSON 修复后：{json_text}")
        try:
            data = json.loads(json_text)
        except Exception as e:
            try:
                json_text_alt = json_text.replace('\\"', '"')
                data = json.loads(json_text_alt)
            except Exception:
                logger.warning(
                    f"对话投票 JSON 解析失败：{e} | raw={text} | cleaned={json_text}"
                )
                data = None
        if data is None:
            return None
        if not isinstance(data, list) or not data:
            logger.warning("对话投票 JSON 内容异常（非列表或空）")
            return None
        first = data[0] if isinstance(data[0], dict) else None
        if not first:
            logger.warning("对话投票 JSON 第一个元素非对象或为空")
            return None
        question = str(first.get("question", "")).strip()
        options_raw = first.get("options", [])
        if not isinstance(options_raw, list):
            return None
        options: list[str] = []
        for item in options_raw:
            if not item:
                continue
            text_item = str(item).strip()
            if not text_item:
                continue
            if len(text_item) > 32:
                text_item = text_item[:29] + "..."
            if text_item not in options:
                options.append(text_item)
        if not question:
            question = "请选择下一句"
        if len(options) < 2:
            logger.warning("对话投票选项数量不足，LLM 输出：%s", options_raw)
            return None
        return question, options

    def _parse_dialogue_poll_json_fallback(
        self, text: str
    ) -> tuple[str, list[str]] | None:
        """在 JSON 解析失败时尝试关键词提取 question/options。"""
        question_match = re.search(r'"question"\s*:\s*"([^"]+)"', text)
        options_match = re.search(r'"options"\s*:\s*\[([^\]]+)\]', text)
        if not question_match or not options_match:
            return None
        question = question_match.group(1).strip()
        candidate_block = options_match.group(1)
        options = []
        seen = set()
        for item in re.findall(r'"([^"]+)"', candidate_block):
            clean = item.strip()
            if not clean or clean in seen:
                continue
            seen.add(clean)
            if len(clean) > 32:
                clean = clean[:29] + "..."
            options.append(clean)
        if not question:
            question = "请选择下一句"
        if len(options) < 2:
            return None
        return question, options

    def _build_poll_fallback_text(self, question: str, options: list[str]) -> str:
        """构建投票失败时的文本回退内容。"""
        safe_question = (question or "").strip() or "请选择"
        lines = [safe_question]
        lines.extend(
            [f"{idx + 1}. {opt}" for idx, opt in enumerate(options or []) if opt]
        )
        return "\n".join(lines).strip()

    async def _send_dialogue_poll_via_adapter(
        self,
        event: AstrMessageEvent,
        platform_id: str | None,
        room_id: str,
        question: str,
        options: list[str],
    ) -> bool | None:
        """优先通过 Matrix 适配器直接发送投票。"""
        if hasattr(event, "client") and getattr(event, "client"):
            try:
                poll_module = _import_matrix_adapter_module(
                    "sender.handlers.poll",
                )
                if not poll_module or not hasattr(poll_module, "send_poll"):
                    raise RuntimeError("Matrix adapter poll handler not available")
                _send_poll = poll_module.send_poll

                is_encrypted_room = False
                if hasattr(event, "e2ee_manager") and event.e2ee_manager:
                    try:
                        is_encrypted_room = await event.client.is_room_encrypted(
                            room_id
                        )
                    except Exception as e:
                        logger.debug(f"检查房间加密状态失败：{e}")

                try:
                    await _send_poll(
                        event.client,
                        room_id,
                        question,
                        options,
                        reply_to=None,
                        thread_root=None,
                        use_thread=False,
                        is_encrypted_room=is_encrypted_room,
                        e2ee_manager=getattr(event, "e2ee_manager", None),
                        max_selections=1,
                        kind="m.disclosed",
                        event_type=POLL_EVENT_TYPE_UNSTABLE,
                        poll_key=POLL_POLL_KEY_UNSTABLE,
                    )
                    logger.info("对话投票已通过 Matrix 客户端发送（MSC3381）")
                    return True
                except Exception as e:
                    logger.warning(f"发送投票失败，尝试回退到稳定事件类型：{e}")

                try:
                    await _send_poll(
                        event.client,
                        room_id,
                        question,
                        options,
                        reply_to=None,
                        thread_root=None,
                        use_thread=False,
                        is_encrypted_room=is_encrypted_room,
                        e2ee_manager=getattr(event, "e2ee_manager", None),
                        max_selections=1,
                        kind="m.disclosed",
                        event_type=POLL_EVENT_TYPE_STABLE,
                        poll_key=POLL_POLL_KEY_STABLE,
                    )
                    logger.info("对话投票已通过 Matrix 客户端发送（稳定事件类型）")
                    return True
                except Exception as e:
                    logger.error(f"发送投票失败（稳定事件类型仍失败）：{e}")
                    return False
            except Exception as e:
                logger.debug(f"Matrix 客户端投票发送路径不可用：{e}")

        platform = None
        if self.bot_manager:
            platform = self.bot_manager.get_platform(
                platform_id=platform_id, platform_name="matrix"
            )
        if not platform:
            return None

        sender = getattr(platform, "sender", None)
        if not sender or not hasattr(sender, "send_poll"):
            return None

        try:
            await sender.send_poll(
                room_id,
                question=question,
                answers=options,
                max_selections=1,
                event_type=POLL_EVENT_TYPE_UNSTABLE,
                poll_key=POLL_POLL_KEY_UNSTABLE,
            )
            logger.info("对话投票已通过 Matrix 适配器发送（MSC3381）")
            return True
        except Exception as e:
            logger.warning(f"发送投票失败，尝试回退到稳定事件类型：{e}")

        try:
            await sender.send_poll(
                room_id,
                question=question,
                answers=options,
                max_selections=1,
                event_type=POLL_EVENT_TYPE_STABLE,
                poll_key=POLL_POLL_KEY_STABLE,
            )
            logger.info("对话投票已通过 Matrix 适配器发送（稳定事件类型）")
            return True
        except Exception as e:
            logger.error(f"发送投票失败（回退事件类型仍失败）：{e}")
            return False

    @filter.command("对话投票")
    @filter.permission_type(PermissionType.ADMIN)
    async def generate_dialogue_poll(
        self,
        event: AstrMessageEvent,
        days: int | None = None,
        guidance: str | None = None,
    ):
        """
        根据历史消息生成对话选项并以单选投票发送
        用法：/对话投票 [天数] [诱导]
        说明：诱导为可选补充指令，将被追加到提示词中
        """
        # Block default chat replies once this command is handled.
        event.should_call_llm(True)
        event.stop_event()
        event._has_send_oper = True
        from .src.analysis.utils.llm_utils import (
            call_provider_with_retry,
            extract_response_text,
        )

        self._ensure_components()
        platform_name = event.get_platform_name()
        if platform_name != "matrix":
            yield event.plain_result("❌ 此功能仅支持 Matrix 群聊/房间")
            return

        group_id = event.session.session_id
        if not group_id:
            yield event.plain_result("❌ 请在群聊中使用此命令")
            return

        # 更新 bot 实例（用于手动命令）
        self.bot_manager.update_from_event(event)
        if not self.bot_manager.has_bot_instance():
            await self.bot_manager.auto_discover_bot_instances()

        # 检查群组权限
        if not self.config_manager.is_group_allowed(group_id):
            yield event.plain_result("❌ 此群未启用日常分析功能")
            return

        analysis_days = (
            days if days and 1 <= days <= 7 else self.config_manager.get_analysis_days()
        )
        progress_text = f"🫪 正在根据近{analysis_days}天聊天生成对话选项，请稍候..."
        if self.config_manager.get_use_reaction_for_progress():
            emoji = self.config_manager.get_progress_reaction_emoji() or "🫪"
            try:
                await event.react(emoji)
            except Exception as e:
                logger.debug(f"发送 progress reaction 失败，回退文本提示：{e}")
                yield event.plain_result(progress_text)
        else:
            yield event.plain_result(progress_text)

        try:
            if self.config_manager is None:
                self._ensure_components()
            if self.config_manager is None:
                yield event.plain_result("❌ 配置初始化失败，请重启插件后重试")
                return
            platform_id = await self.auto_scheduler.get_platform_id_for_group(group_id)
            if not platform_id and hasattr(event, "get_platform_id"):
                platform_id = event.get_platform_id()
            bot_instance = self.bot_manager.get_bot_instance(platform_id)
            if not bot_instance:
                yield event.plain_result(
                    f"❌ 未找到群 {group_id} 对应的 bot 实例（平台：{platform_id}）"
                )
                return

            messages = await self.message_analyzer.message_handler.fetch_group_messages(
                bot_instance, group_id, analysis_days, platform_id
            )
            if not messages:
                yield event.plain_result("❌ 未找到足够的群聊记录")
                return

            min_threshold = self.config_manager.get_min_messages_threshold()
            if len(messages) < min_threshold:
                yield event.plain_result(
                    f"❌ 消息数量不足（{len(messages)}条），至少需要{min_threshold}条消息"
                )
                return

            history_text = self._format_messages_for_dialogue_prompt(messages)
            if not history_text:
                yield event.plain_result("❌ 未提取到可用的文本消息")
                return

            max_options = self.config_manager.get_dialogue_poll_max_options()
            option_count = max(2, min(max_options, 10))
            prompt = self._build_dialogue_poll_prompt(history_text, option_count)
            guidance_text = (guidance or "").strip()
            if guidance_text:
                prompt = (
                    f"{prompt}\n\n补充要求：\n{guidance_text}\n"
                    "注意：仍需只输出 JSON 数组。"
                )
            max_tokens = self.config_manager.get_dialogue_poll_max_tokens()
            llm_resp = await call_provider_with_retry(
                self.context,
                self.config_manager,
                prompt,
                max_tokens=max_tokens,
                temperature=0.9,
                umo=event.unified_msg_origin,
                provider_id_key="dialogue_poll_provider_id",
            )
            if not llm_resp:
                yield event.plain_result("❌ LLM 生成失败，请稍后重试")
                return

            result_text = extract_response_text(llm_resp)
            parsed = self._parse_dialogue_poll_json(result_text)
            if not parsed:
                parsed = self._parse_dialogue_poll_json_fallback(result_text)
            if not parsed:
                logger.warning("对话投票解析失败，LLM 输出：%s", result_text[:100])
                yield event.plain_result("❌ 解析投票内容失败，请稍后重试")
                return

            question, options = parsed
            options = options[:option_count]
            sent = await self._send_dialogue_poll_via_adapter(
                event, platform_id, group_id, question, options
            )
            if sent is True:
                event._has_send_oper = True
                return
            if sent is False:
                fallback_text = self._build_poll_fallback_text(question, options)
                yield event.plain_result(
                    f"⚠️ Matrix 投票发送失败，已转为文本格式：\n{fallback_text}"
                )
                return
            poll_components = _import_matrix_adapter_module("components")
            Poll = getattr(poll_components, "Poll", None) if poll_components else None
            if Poll is None:
                fallback_text = self._build_poll_fallback_text(question, options)
                yield event.plain_result(
                    f"⚠️ 未检测到 Matrix 适配器投票组件，已转为文本格式：\n{fallback_text}"
                )
                return

            poll = Poll(question=question, answers=options, max_selections=1)
            yield event.chain_result([poll])
            return

        except Exception as e:
            logger.error(f"对话投票生成失败：{e}", exc_info=True)
            yield event.plain_result(
                f"❌ 对话投票生成失败：{str(e)}。请检查网络连接和 LLM 配置"
            )

    @filter.command("设置格式")
    @filter.permission_type(PermissionType.ADMIN)
    async def set_output_format(self, event: AstrMessageEvent, format_type: str = ""):
        """
        设置分析报告输出格式
        用法：/设置格式 [image|text|pdf]
        """
        self._ensure_components()
        platform_name = event.get_platform_name()
        if platform_name != "matrix":
            yield event.plain_result("❌ 此功能仅支持 Matrix 群聊/房间")
            return

        group_id = event.session.session_id
        if not group_id:
            yield event.plain_result("❌ 请在群聊中使用此命令")
            return

        if not format_type:
            current_format = self.config_manager.get_output_format()
            pdf_status = (
                "✅"
                if self.config_manager.playwright_available
                else "❌ (需安装 Playwright)"
            )
            yield event.plain_result(f"""📊 当前输出格式：{current_format}

可用格式：
• image - 图片格式 (默认)
• text - 文本格式
• pdf - PDF 格式 {pdf_status}

用法：/设置格式 [格式名称]""")
            return

        format_type = format_type.lower()
        if format_type not in ["image", "text", "pdf"]:
            yield event.plain_result("❌ 无效的格式类型，支持：image, text, pdf")
            return

        if format_type == "pdf" and not self.config_manager.playwright_available:
            yield event.plain_result("❌ PDF 格式不可用，请使用 /安装 PDF 命令安装依赖")
            return

        self.config_manager.set_output_format(format_type)
        yield event.plain_result(f"✅ 输出格式已设置为：{format_type}")

    @filter.command("设置模板")
    @filter.permission_type(PermissionType.ADMIN)
    async def set_report_template(
        self, event: AstrMessageEvent, template_input: str = ""
    ):
        """
        设置分析报告模板
        用法：/设置模板 [模板名称或序号]
        """
        self._ensure_components()
        platform_name = event.get_platform_name()
        if platform_name != "matrix":
            yield event.plain_result("❌ 此功能仅支持 Matrix 群聊/房间")
            return

        import os

        # 获取模板目录和可用模板列表（使用 asyncio.to_thread 避免阻塞）
        template_base_dir = os.path.join(
            os.path.dirname(__file__), "src", "reports", "templates"
        )

        def _list_templates_sync():
            if os.path.exists(template_base_dir):
                return sorted(
                    [
                        d
                        for d in os.listdir(template_base_dir)
                        if os.path.isdir(os.path.join(template_base_dir, d))
                        and not d.startswith("__")
                    ]
                )
            return []

        available_templates = await asyncio.to_thread(_list_templates_sync)

        if not template_input:
            current_template = self.config_manager.get_report_template()
            # 列出可用的模板（带序号）
            template_list_str = "\n".join(
                [f"【{i}】{t}" for i, t in enumerate(available_templates, start=1)]
            )
            yield event.plain_result(f"""🎨 当前报告模板：{current_template}

可用模板：
{template_list_str}

用法：/设置模板 [模板名称或序号]
💡 使用 /查看模板 查看预览图""")
            return

        # 判断输入是序号还是模板名称
        template_name = template_input
        if template_input.isdigit():
            index = int(template_input)
            if 1 <= index <= len(available_templates):
                template_name = available_templates[index - 1]
            else:
                yield event.plain_result(
                    f"❌ 无效的序号 '{template_input}'，有效范围：1-{len(available_templates)}"
                )
                return

        # 检查模板是否存在（使用 asyncio.to_thread 避免阻塞）
        template_dir = os.path.join(template_base_dir, template_name)
        template_exists = await asyncio.to_thread(os.path.exists, template_dir)
        if not template_exists:
            yield event.plain_result(f"❌ 模板 '{template_name}' 不存在")
            return

        self.config_manager.set_report_template(template_name)
        yield event.plain_result(f"✅ 报告模板已设置为：{template_name}")

    @filter.command("查看模板")
    @filter.permission_type(PermissionType.ADMIN)
    async def view_templates(self, event: AstrMessageEvent):
        """
        查看所有可用的报告模板及预览图
        用法：/查看模板
        """
        self._ensure_components()
        platform_name = event.get_platform_name()
        if platform_name != "matrix":
            yield event.plain_result("❌ 此功能仅支持 Matrix 群聊/房间")
            return

        import os

        # 获取模板目录
        template_dir = os.path.join(
            os.path.dirname(__file__), "src", "reports", "templates"
        )
        assets_dir = os.path.join(os.path.dirname(__file__), "assets")

        # 获取可用模板列表（使用 asyncio.to_thread 避免阻塞）
        def _list_templates_sync():
            if os.path.exists(template_dir):
                return sorted(
                    [
                        d
                        for d in os.listdir(template_dir)
                        if os.path.isdir(os.path.join(template_dir, d))
                        and not d.startswith("__")
                    ]
                )
            return []

        available_templates = await asyncio.to_thread(_list_templates_sync)

        if not available_templates:
            yield event.plain_result("❌ 未找到任何可用的报告模板")
            return

        # 获取当前使用的模板
        current_template = self.config_manager.get_report_template()

        # 圆圈数字序号
        circle_numbers = ["①", "②", "③", "④", "⑤", "⑥", "⑦", "⑧", "⑨", "⑩"]

        yield event.plain_result(
            f"🎨 可用报告模板列表\n📌 当前使用：{current_template}\n💡 使用 /设置模板 [序号] 切换"
        )

        # 为每个模板创建一个节点
        for index, template_name in enumerate(available_templates):
            # 标记当前正在使用的模板
            current_mark = " ✅" if template_name == current_template else ""

            # 获取序号
            num_label = (
                circle_numbers[index]
                if index < len(circle_numbers)
                else f"({index + 1})"
            )

            # 发送模板名称
            yield event.plain_result(f"{num_label} {template_name}{current_mark}")

            # 添加预览图
            preview_image_path = os.path.join(assets_dir, f"{template_name}-demo.jpg")
            if os.path.exists(preview_image_path):
                yield event.image_result(preview_image_path)

    @filter.command("安装 PDF")
    @filter.permission_type(PermissionType.ADMIN)
    async def install_pdf_deps(self, event: AstrMessageEvent):
        """
        安装 PDF 功能依赖
        用法：/安装 PDF
        """
        self._ensure_components()
        platform_name = event.get_platform_name()
        if platform_name != "matrix":
            yield event.plain_result("❌ 此功能仅支持 Matrix 群聊/房间")
            return

        yield event.plain_result("🔄 开始安装 PDF 功能依赖，请稍候...")

        try:
            # 安装 playwright (内部已包含浏览器内核安装逻辑)
            result = await PDFInstaller.install_playwright(self.config_manager)
            yield event.plain_result(result)

        except Exception as e:
            logger.error(f"安装 PDF 依赖失败：{e}", exc_info=True)
            yield event.plain_result(f"❌ 安装过程中出现错误：{str(e)}")

    @filter.command("分析设置")
    @filter.permission_type(PermissionType.ADMIN)
    async def analysis_settings(self, event: AstrMessageEvent, action: str = "status"):
        """
        管理分析设置
        用法：/分析设置 [enable|disable|status|reload|test]
        - enable: 启用当前群的分析功能
        - disable: 禁用当前群的分析功能
        - status: 查看当前状态
        - reload: 重新加载配置并重启定时任务
        - test: 测试自动分析功能
        """
        self._ensure_components()
        platform_name = event.get_platform_name()
        if platform_name != "matrix":
            yield event.plain_result("❌ 此功能仅支持 Matrix 群聊/房间")
            return

        group_id = event.session.session_id
        if not group_id:
            yield event.plain_result("❌ 请在群聊中使用此命令")
            return

        if action == "enable":
            mode = self.config_manager.get_group_list_mode()
            if mode == "whitelist":
                glist = self.config_manager.get_group_list()
                if group_id not in glist:
                    glist.append(group_id)
                    self.config_manager.set_group_list(glist)
                    yield event.plain_result("✅ 已将当前群加入白名单")
                    # 重新启动定时任务
                    await self.auto_scheduler.restart_scheduler()
                else:
                    yield event.plain_result("ℹ️ 当前群已在白名单中")
            elif mode == "blacklist":
                glist = self.config_manager.get_group_list()
                if group_id in glist:
                    glist.remove(group_id)
                    self.config_manager.set_group_list(glist)
                    yield event.plain_result("✅ 已将当前群从黑名单移除")
                    # 重新启动定时任务
                    await self.auto_scheduler.restart_scheduler()
                else:
                    yield event.plain_result("ℹ️ 当前群不在黑名单中")
            else:
                yield event.plain_result("ℹ️ 当前为无限制模式，所有群聊默认启用")

        elif action == "disable":
            mode = self.config_manager.get_group_list_mode()
            if mode == "whitelist":
                glist = self.config_manager.get_group_list()
                if group_id in glist:
                    glist.remove(group_id)
                    self.config_manager.set_group_list(glist)
                    yield event.plain_result("✅ 已将当前群从白名单移除")
                    # 重新启动定时任务
                    await self.auto_scheduler.restart_scheduler()
                else:
                    yield event.plain_result("ℹ️ 当前群不在白名单中")
            elif mode == "blacklist":
                glist = self.config_manager.get_group_list()
                if group_id not in glist:
                    glist.append(group_id)
                    self.config_manager.set_group_list(glist)
                    yield event.plain_result("✅ 已将当前群加入黑名单")
                    # 重新启动定时任务
                    await self.auto_scheduler.restart_scheduler()
                else:
                    yield event.plain_result("ℹ️ 当前群已在黑名单中")
            else:
                yield event.plain_result(
                    "ℹ️ 当前为无限制模式，如需禁用请切换到黑名单模式"
                )

        elif action == "reload":
            # 重新启动定时任务
            await self.auto_scheduler.restart_scheduler()
            yield event.plain_result("✅ 已重新加载配置并重启定时任务")

        elif action == "test":
            # 测试自动分析功能
            if not self.config_manager.is_group_allowed(group_id):
                yield event.plain_result("❌ 请先启用当前群的分析功能")
                return

            yield event.plain_result("🧪 开始测试自动分析功能...")

            # 更新 bot 实例（用于测试）
            self.bot_manager.update_from_event(event)

            # 执行自动分析
            try:
                await self.auto_scheduler._perform_auto_analysis_for_group(group_id)
                yield event.plain_result("✅ 自动分析测试完成，请查看群消息")
            except Exception as e:
                yield event.plain_result(f"❌ 自动分析测试失败：{str(e)}")

        else:  # status
            is_allowed = self.config_manager.is_group_allowed(group_id)
            status = "已启用" if is_allowed else "未启用"
            mode = self.config_manager.get_group_list_mode()

            auto_status = (
                "已启用" if self.config_manager.get_enable_auto_analysis() else "未启用"
            )
            auto_time = self.config_manager.get_auto_analysis_time()

            pdf_status = PDFInstaller.get_pdf_status(self.config_manager)
            output_format = self.config_manager.get_output_format()
            min_threshold = self.config_manager.get_min_messages_threshold()

            yield event.plain_result(f"""📊 当前群分析功能状态：
• 群分析功能：{status} (模式：{mode})
• 自动分析：{auto_status} ({auto_time})
• 输出格式：{output_format}
• PDF 功能：{pdf_status}
• 最小消息数：{min_threshold}

💡 可用命令：enable, disable, status, reload, test
💡 支持的输出格式：image, text, pdf (图片和 PDF 包含活跃度可视化)
💡 其他命令：/设置格式，/安装 PDF""")
