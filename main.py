"""
matrix 群日常分析插件
基于群聊记录生成精美的日常分析报告，包含话题总结、用户画像、统计数据等

重构版本 - 使用模块化架构
"""

import asyncio
import os

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
from astrbot.core.star.filter.permission import PermissionType

from .src.commands.dialogue_poll import (
    DialoguePollHandler,
    _import_matrix_adapter_module,
)
from .src.commands.group_analysis import GroupAnalysisHandler
from .src.commands.personal_report import PersonalReportHandler
from .src.commands.settings import SettingsHandler
from .src.core.bot_manager import BotManager
from .src.core.config import ConfigManager
from .src.reports.generators import ReportGenerator
from .src.scheduler.auto_scheduler import AutoScheduler
from .src.scheduler.retry import RetryManager
from .src.utils.helpers import MessageAnalyzer


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
        self._plugin_dir = os.path.dirname(__file__)

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
            self.html_render,
        )

        # 初始化命令处理器
        self._init_handlers()

        # 延迟启动自动调度器，给系统时间初始化
        if self.config_manager.get_enable_auto_analysis():
            asyncio.create_task(self._delayed_start_scheduler())

        logger.info("matrix 群日常分析插件已初始化（模块化版本）")

    def _init_handlers(self):
        """初始化命令处理器"""
        self.dialogue_poll_handler = DialoguePollHandler(
            self.config_manager, self.bot_manager
        )
        self.personal_report_handler = PersonalReportHandler(
            self.context, self.config_manager, self.message_analyzer
        )
        self.group_analysis_handler = GroupAnalysisHandler(
            self.config_manager,
            self.message_analyzer,
            self.report_generator,
            self.auto_scheduler,
            self.retry_manager,
            self.bot_manager,
        )
        self.settings_handler = SettingsHandler(self.config_manager, self._plugin_dir)

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
        # 重新初始化命令处理器
        self._init_handlers()

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
            days
            if days and 1 <= days <= 30
            else self.config_manager.get_analysis_days()
        )

        # 发送进度提示
        progress_text = f"🔍 开始分析群聊近{analysis_days}天的活动，请稍候..."
        if self.config_manager.get_use_reaction_for_progress():
            emoji = self.config_manager.get_progress_reaction_emoji() or "🔍"
            try:
                await event.react(emoji)
            except Exception as e:
                logger.debug(f"发送 progress reaction 失败，回退文本提示：{e}")
                yield event.plain_result(progress_text)
        else:
            yield event.plain_result(progress_text)

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

            # 发送分析进度提示
            analyzing_text = f"📊 已获取{len(messages)}条消息，正在进行智能分析..."
            if self.config_manager.get_use_reaction_for_progress():
                # 使用 reaction 时不发送文本，保持安静
                pass
            else:
                yield event.plain_result(analyzing_text)

            # 进行分析 - 传递 unified_msg_origin 以获取正确的 LLM 提供商
            analysis_result = await self.message_analyzer.analyze_messages(
                messages, group_id, event.unified_msg_origin
            )

            # 检查分析结果
            if not analysis_result or not analysis_result.get("statistics"):
                yield event.plain_result("❌ 分析过程中出现错误，请稍后重试")
                return

            # 检查所有分析是否都失败
            topics = analysis_result.get("topics", [])
            user_titles = analysis_result.get("user_titles", [])
            golden_quotes = analysis_result.get("statistics", {}).get(
                "golden_quotes", []
            )

            # 检查各个分析功能是否启用
            topic_enabled = self.config_manager.get_topic_analysis_enabled()
            user_title_enabled = self.config_manager.get_user_title_analysis_enabled()
            golden_quote_enabled = (
                self.config_manager.get_golden_quote_analysis_enabled()
            )

            # 如果启用的分析全部失败（结果为空），则返回错误
            enabled_analyses_failed = []
            if topic_enabled and not topics:
                enabled_analyses_failed.append("话题分析")
            if user_title_enabled and not user_titles:
                enabled_analyses_failed.append("用户称号分析")
            if golden_quote_enabled and not golden_quotes:
                enabled_analyses_failed.append("金句分析")

            # 如果所有启用的分析都失败，不输出报告
            if len(enabled_analyses_failed) == (
                topic_enabled + user_title_enabled + golden_quote_enabled
            ):
                yield event.plain_result(
                    f"❌ 所有分析均失败：{', '.join(enabled_analyses_failed)}。请检查 LLM 配置和网络连接，或稍后重试"
                )
                return

            # 生成报告
            output_format = self.config_manager.get_output_format()
            if output_format == "image":
                (
                    success,
                    message,
                ) = await self.group_analysis_handler.handle_image_report(
                    event, analysis_result, group_id, self.html_render
                )
                if message:
                    yield event.plain_result(message)

            elif output_format == "pdf":
                success, message = await self.group_analysis_handler.handle_pdf_report(
                    event, analysis_result, group_id
                )
                if message:
                    yield event.plain_result(message)
            else:
                text_report = self.group_analysis_handler.handle_text_report(
                    analysis_result
                )
                yield event.plain_result(text_report)

        except Exception as e:
            logger.error(f"群分析失败：{e}", exc_info=True)
            yield event.plain_result(
                f"❌ 分析失败：{str(e)}。请检查网络连接和 LLM 配置，或联系管理员"
            )

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
            days
            if days and 1 <= days <= 365
            else self.config_manager.get_analysis_days()
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

            history_text = (
                self.dialogue_poll_handler.format_messages_for_dialogue_prompt(messages)
            )
            if not history_text:
                yield event.plain_result("❌ 未提取到可用的文本消息")
                return

            max_options = self.config_manager.get_dialogue_poll_max_options()
            option_count = max(2, min(max_options, 10))
            prompt = self.dialogue_poll_handler.build_dialogue_poll_prompt(
                history_text, option_count
            )
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
            parsed = self.dialogue_poll_handler.parse_dialogue_poll_json(result_text)
            if not parsed:
                parsed = self.dialogue_poll_handler.parse_dialogue_poll_json_fallback(
                    result_text
                )
            if not parsed:
                logger.warning("对话投票解析失败，LLM 输出：%s", result_text[:100])
                yield event.plain_result("❌ 解析投票内容失败，请稍后重试")
                return

            question, options = parsed
            options = options[:option_count]
            sent = await self.dialogue_poll_handler.send_dialogue_poll_via_adapter(
                event, platform_id, group_id, question, options
            )
            if sent is True:
                event._has_send_oper = True
                return
            if sent is False:
                fallback_text = self.dialogue_poll_handler.build_poll_fallback_text(
                    question, options
                )
                yield event.plain_result(
                    f"⚠️ Matrix 投票发送失败，已转为文本格式：\n{fallback_text}"
                )
                return
            poll_components = _import_matrix_adapter_module("components")
            Poll = getattr(poll_components, "Poll", None) if poll_components else None
            if Poll is None:
                fallback_text = self.dialogue_poll_handler.build_poll_fallback_text(
                    question, options
                )
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
            yield event.plain_result(self.settings_handler.get_output_format_info())
            return

        success, message = self.settings_handler.set_output_format(format_type)
        yield event.plain_result(message)

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

        available_templates = await self.settings_handler.list_templates()

        if not template_input:
            yield event.plain_result(
                self.settings_handler.get_template_info(available_templates)
            )
            return

        success, message = await self.settings_handler.set_template(
            template_input, available_templates
        )
        yield event.plain_result(message)

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

        available_templates = await self.settings_handler.list_templates()

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
            preview_path = self.settings_handler.get_template_preview_path(
                template_name
            )
            if preview_path:
                yield event.image_result(preview_path)

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

        result = await self.settings_handler.install_pdf_deps()
        yield event.plain_result(result)

    @filter.command("我的群报告")
    async def my_group_report(self, event: AstrMessageEvent, days: int = 7):
        """
        获取自己在群聊中的分析报告
        用法：/我的群报告 [天数=7]
        """
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

        # 获取当前用户的 ID
        current_user_id = event.get_sender_id()
        if not current_user_id:
            yield event.plain_result("❌ 无法获取您的用户 ID")
            return

        # 更新 bot 实例
        self.bot_manager.update_from_event(event)
        if not self.bot_manager.has_bot_instance():
            await self.bot_manager.auto_discover_bot_instances()

        # 检查群组权限
        if not self.config_manager.is_group_allowed(group_id):
            yield event.plain_result("❌ 此群未启用日常分析功能")
            return

        analysis_days = max(1, days)

        # 发送进度提示
        progress_text = f"🔍 开始分析您近{analysis_days}天的群聊活动，请稍候..."
        if self.config_manager.get_use_reaction_for_progress():
            emoji = self.config_manager.get_progress_reaction_emoji() or "🔍"
            try:
                await event.react(emoji)
            except Exception as e:
                logger.debug(f"发送 progress reaction 失败，回退文本提示：{e}")
                yield event.plain_result(progress_text)
        else:
            yield event.plain_result(progress_text)

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
            all_messages = (
                await self.message_analyzer.message_handler.fetch_group_messages(
                    bot_instance, group_id, analysis_days, platform_id
                )
            )
            if not all_messages:
                yield event.plain_result(
                    "❌ 未找到足够的群聊记录，请确保群内有足够的消息历史"
                )
                return

            # 过滤只保留当前用户的消息
            user_messages = [
                msg
                for msg in all_messages
                if msg.get("sender", {}).get("user_id") == current_user_id
            ]

            if not user_messages:
                yield event.plain_result(
                    f"❌ 未找到您在近{analysis_days}天内的消息记录"
                )
                return

            # 检查消息数量是否足够分析
            min_threshold = max(
                5, self.config_manager.get_min_messages_threshold() // 5
            )
            if len(user_messages) < min_threshold:
                yield event.plain_result(
                    f"❌ 您的消息数量不足（{len(user_messages)}条），至少需要{min_threshold}条消息才能进行有效分析"
                )
                return

            # 发送分析进度提示
            analyzing_text = (
                f"📊 已获取您的{len(user_messages)}条消息，正在进行智能分析..."
            )
            if self.config_manager.get_use_reaction_for_progress():
                # 使用 reaction 时不发送文本，保持安静
                pass
            else:
                yield event.plain_result(analyzing_text)

            # 进行个人分析
            personal_report = (
                await self.personal_report_handler.generate_personal_report(
                    user_messages, current_user_id, event.unified_msg_origin
                )
            )

            if not personal_report:
                yield event.plain_result("❌ 分析过程中出现错误，请稍后重试")
                return

            yield event.plain_result(personal_report)

        except Exception as e:
            logger.error(f"个人群报告生成失败：{e}", exc_info=True)
            yield event.plain_result(
                f"❌ 分析失败：{str(e)}。请检查网络连接和 LLM 配置，或联系管理员"
            )

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
            message = self.settings_handler.handle_enable_group(group_id)
            yield event.plain_result(message)
            if "✅" in message:
                await self.auto_scheduler.restart_scheduler()

        elif action == "disable":
            message = self.settings_handler.handle_disable_group(group_id)
            yield event.plain_result(message)
            if "✅" in message:
                await self.auto_scheduler.restart_scheduler()

        elif action == "reload":
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
            yield event.plain_result(
                self.settings_handler.get_analysis_status(group_id)
            )
