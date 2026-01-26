"""
自动调度器模块
负责定时任务和自动分析功能
"""

import asyncio
import base64
import weakref
from datetime import datetime, timedelta

import aiohttp

from astrbot.api import logger


class AutoScheduler:
    """自动调度器"""

    def __init__(
        self,
        config_manager,
        message_handler,
        analyzer,
        report_generator,
        bot_manager,
        retry_manager,  # 新增
        html_render_func=None,
    ):
        self.config_manager = config_manager
        self.message_handler = message_handler
        self.analyzer = analyzer
        self.report_generator = report_generator
        self.bot_manager = bot_manager
        self.retry_manager = retry_manager  # 保存引用
        self.html_render_func = html_render_func
        self.scheduler_task = None
        self.last_execution_date = None  # 记录上次执行日期，防止重复执行

    def set_bot_instance(self, bot_instance):
        """设置 bot 实例（保持向后兼容）"""
        self.bot_manager.set_bot_instance(bot_instance)

    def set_bot_matrix_ids(self, bot_matrix_ids):
        """设置 bot matrix 号（支持单个 matrix 号或 matrix 号列表）"""
        # 确保传入的是列表，保持统一处理
        if isinstance(bot_matrix_ids, list):
            self.bot_manager.set_bot_matrix_ids(bot_matrix_ids)
        elif bot_matrix_ids:
            self.bot_manager.set_bot_matrix_ids([bot_matrix_ids])

    async def get_platform_id_for_group(self, group_id):
        """根据群 ID 获取对应的平台 ID"""
        try:
            # 首先检查已注册的 bot 实例
            if (
                hasattr(self.bot_manager, "_bot_instances")
                and self.bot_manager._bot_instances
            ):
                # 如果只有一个实例，直接返回
                if len(self.bot_manager._bot_instances) == 1:
                    platform_id = list(self.bot_manager._bot_instances.keys())[0]
                    logger.debug(f"只有一个适配器，使用平台：{platform_id}")
                    return platform_id

                # 如果有多个实例，尝试通过 API 检查群属于哪个适配器
                logger.info(f"检测到多个适配器，正在验证群 {group_id} 属于哪个平台...")
                for (
                    platform_id,
                    bot_instance,
                ) in self.bot_manager._bot_instances.items():
                    try:
                        # 尝试调用 get_group_info 来验证群是否存在
                        if hasattr(bot_instance, "call_action"):
                            result = await bot_instance.call_action(
                                "get_group_info", group_id=int(group_id)
                            )
                            if result and result.get("group_id"):
                                logger.info(f"✅ 群 {group_id} 属于平台 {platform_id}")
                                return platform_id
                            else:
                                logger.debug(
                                    f"平台 {platform_id} 返回了无效结果：{result}"
                                )
                        else:
                            logger.debug(
                                f"平台 {platform_id} 的 bot 实例没有 call_action 方法"
                            )
                    except Exception as e:
                        # 检查是否是特定的错误码（1200 表示不在该群）
                        error_msg = str(e)
                        if (
                            "retcode=1200" in error_msg
                            or "消息 undefined 不存在" in error_msg
                        ):
                            logger.debug(
                                f"平台 {platform_id} 确认群 {group_id} 不存在：{e}"
                            )
                        else:
                            logger.debug(
                                f"平台 {platform_id} 无法获取群 {group_id} 信息：{e}"
                            )
                        continue

                # 如果所有适配器都尝试失败，记录错误并返回 None
                logger.error(
                    f"❌ 无法确定群 {group_id} 属于哪个平台 (已尝试：{list(self.bot_manager._bot_instances.keys())})"
                )
                return None

            # 没有任何 bot 实例，返回 None
            logger.error("❌ 没有注册的 bot 实例")
            return None
        except Exception as e:
            logger.error(f"❌ 获取平台 ID 失败：{e}")
            return None

    async def start_scheduler(self):
        """启动定时任务调度器"""
        if not self.config_manager.get_enable_auto_analysis():
            logger.info("自动分析功能未启用")
            return

        # 延迟启动，给系统时间初始化
        await asyncio.sleep(10)

        logger.info(
            f"启动定时任务调度器，自动分析时间：{self.config_manager.get_auto_analysis_time()}"
        )

        self.scheduler_task = asyncio.create_task(self._scheduler_loop())

    async def stop_scheduler(self):
        """停止定时任务调度器"""
        if self.scheduler_task and not self.scheduler_task.done():
            self.scheduler_task.cancel()
            logger.info("已停止定时任务调度器")

    async def restart_scheduler(self):
        """重启定时任务调度器"""
        await self.stop_scheduler()
        if self.config_manager.get_enable_auto_analysis():
            await self.start_scheduler()

    async def _scheduler_loop(self):
        """调度器主循环"""
        while True:
            try:
                now = datetime.now()
                target_time = datetime.strptime(
                    self.config_manager.get_auto_analysis_time(), "%H:%M"
                ).replace(year=now.year, month=now.month, day=now.day)

                # 如果今天的目标时间已过，设置为明天
                if now >= target_time:
                    target_time += timedelta(days=1)

                # 计算等待时间
                wait_seconds = (target_time - now).total_seconds()
                logger.info(
                    f"定时分析将在 {target_time.strftime('%Y-%m-%d %H:%M:%S')} 执行，等待 {wait_seconds:.0f} 秒"
                )

                # 等待到目标时间
                await asyncio.sleep(wait_seconds)

                # 执行自动分析
                if self.config_manager.get_enable_auto_analysis():
                    # 检查今天是否已经执行过，防止重复执行
                    if self.last_execution_date == target_time.date():
                        logger.info(
                            f"今天 {target_time.date()} 已经执行过自动分析，跳过执行"
                        )
                        # 等待到明天再检查
                        await asyncio.sleep(3600)  # 等待 1 小时后再检查
                        continue

                    logger.info("开始执行定时分析")
                    await self._run_auto_analysis()
                    self.last_execution_date = target_time.date()  # 记录执行日期
                    logger.info(
                        f"定时分析执行完成，记录执行日期：{self.last_execution_date}"
                    )
                else:
                    logger.info("自动分析已禁用，跳过执行")
                    break

            except Exception as e:
                logger.error(f"定时任务调度器错误：{e}")
                # 等待 5 分钟后重试
                await asyncio.sleep(300)

    async def _run_auto_analysis(self):
        """执行自动分析 - 并发处理所有群聊"""
        try:
            logger.info("开始执行自动群聊分析（并发模式）")

            # 根据配置确定需要分析的群组
            group_list_mode = self.config_manager.get_group_list_mode()

            # 始终获取所有群组并进行过滤
            logger.info(f"自动分析使用 {group_list_mode} 模式，正在获取群列表...")
            all_groups = await self._get_all_groups()
            logger.info(f"共获取到 {len(all_groups)} 个群组：{all_groups}")
            enabled_groups = []
            for group_id in all_groups:
                if self.config_manager.is_group_allowed(group_id):
                    enabled_groups.append(group_id)

            logger.info(
                f"根据 {group_list_mode} 过滤后，共有 {len(enabled_groups)} 个群聊需要分析"
            )

            if not enabled_groups:
                logger.info("没有启用的群聊需要分析")
                return

            logger.info(
                f"将为 {len(enabled_groups)} 个群聊并发执行分析：{enabled_groups}"
            )

            # 创建并发任务 - 为每个群聊创建独立的分析任务
            # 限制最大并发数
            max_concurrent = self.config_manager.get_max_concurrent_tasks()
            logger.info(f"自动分析并发数限制：{max_concurrent}")
            sem = asyncio.Semaphore(max_concurrent)

            async def safe_perform_analysis(group_id):
                async with sem:
                    return await self._perform_auto_analysis_for_group_with_timeout(
                        group_id
                    )

            analysis_tasks = []
            for group_id in enabled_groups:
                task = asyncio.create_task(
                    safe_perform_analysis(group_id),
                    name=f"analysis_group_{group_id}",
                )
                analysis_tasks.append(task)

            # 并发执行所有分析任务，使用 return_exceptions=True 确保单个任务失败不影响其他任务
            results = await asyncio.gather(*analysis_tasks, return_exceptions=True)

            # 统计执行结果
            success_count = 0
            error_count = 0

            for i, result in enumerate(results):
                group_id = enabled_groups[i]
                if isinstance(result, Exception):
                    logger.error(f"群 {group_id} 分析任务异常：{result}")
                    error_count += 1
                else:
                    success_count += 1

            logger.info(
                f"并发分析完成 - 成功：{success_count}, 失败：{error_count}, 总计：{len(enabled_groups)}"
            )

        except Exception as e:
            logger.error(f"自动分析执行失败：{e}", exc_info=True)

    async def _perform_auto_analysis_for_group_with_timeout(self, group_id: str):
        """为指定群执行自动分析（带超时控制）"""
        try:
            # 为每个群聊设置独立的超时时间（20 分钟）- 使用 asyncio.wait_for 兼容所有 Python 版本
            await asyncio.wait_for(
                self._perform_auto_analysis_for_group(group_id), timeout=1200
            )
        except asyncio.TimeoutError:
            logger.error(f"群 {group_id} 分析超时（20 分钟），跳过该群分析")
        except Exception as e:
            logger.error(f"群 {group_id} 分析任务执行失败：{e}")

    async def _perform_auto_analysis_for_group(self, group_id: str):
        """为指定群执行自动分析（核心逻辑）"""
        # 为每个群聊使用独立的锁，避免全局锁导致串行化
        group_lock_key = f"analysis_{group_id}"
        if not hasattr(self, "_group_locks"):
            self._group_locks = weakref.WeakValueDictionary()

        # 从 WeakValueDictionary 获取锁，如果不存在则创建
        # 注意：必须将锁赋值给局部变量以保持引用，否则可能会被回收
        lock = self._group_locks.get(group_lock_key)
        if lock is None:
            lock = asyncio.Lock()
            self._group_locks[group_lock_key] = lock

        async with lock:
            try:
                start_time = asyncio.get_event_loop().time()

                # 检查 bot 管理器状态
                if not self.bot_manager.is_ready_for_auto_analysis():
                    status = self.bot_manager.get_status_info()
                    logger.warning(
                        f"群 {group_id} 自动分析跳过：bot 管理器未就绪 - {status}"
                    )
                    return

                logger.info(f"开始为群 {group_id} 执行自动分析（并发任务）")

                # 获取所有可用的平台，依次尝试获取消息
                messages = None
                platform_id = None
                bot_instance = None

                # 获取所有可用的平台 ID 和 bot 实例
                if (
                    hasattr(self.bot_manager, "_bot_instances")
                    and self.bot_manager._bot_instances
                ):
                    available_platforms = list(self.bot_manager._bot_instances.items())
                    logger.info(
                        f"群 {group_id} 检测到 {len(available_platforms)} 个可用平台，开始依次尝试..."
                    )

                    for test_platform_id, test_bot_instance in available_platforms:
                        # 检查该平台是否启用了此插件
                        if not self.bot_manager.is_plugin_enabled(
                            test_platform_id, "astrbot_plugin_matrix_daily_analysis"
                        ):
                            logger.debug(f"平台 {test_platform_id} 未启用此插件，跳过")
                            continue

                        try:
                            logger.info(
                                f"尝试使用平台 {test_platform_id} 获取群 {group_id} 的消息..."
                            )
                            analysis_days = self.config_manager.get_analysis_days()
                            test_messages = (
                                await self.message_handler.fetch_group_messages(
                                    test_bot_instance,
                                    group_id,
                                    analysis_days,
                                    test_platform_id,
                                )
                            )

                            if test_messages and len(test_messages) > 0:
                                # 成功获取到消息，使用这个平台
                                messages = test_messages
                                platform_id = test_platform_id
                                bot_instance = test_bot_instance
                                logger.info(
                                    f"✅ 群 {group_id} 成功通过平台 {platform_id} 获取到 {len(messages)} 条消息"
                                )
                                break
                            else:
                                logger.debug(
                                    f"平台 {test_platform_id} 未获取到消息，继续尝试下一个平台"
                                )
                        except Exception as e:
                            logger.debug(
                                f"平台 {test_platform_id} 获取消息失败：{e}，继续尝试下一个平台"
                            )
                            continue

                    if not messages:
                        logger.warning(
                            f"群 {group_id} 所有平台都尝试失败，未获取到足够的消息记录"
                        )
                        return
                else:
                    # 回退到原来的逻辑（单个平台）
                    logger.warning(f"群 {group_id} 没有多个平台可用，使用回退逻辑")
                    platform_id = await self.get_platform_id_for_group(group_id)

                    if not platform_id:
                        logger.error(f"❌ 群 {group_id} 无法获取平台 ID，跳过分析")
                        return

                    bot_instance = self.bot_manager.get_bot_instance(platform_id)

                    if not bot_instance:
                        logger.error(
                            f"❌ 群 {group_id} 未找到对应的 bot 实例（平台：{platform_id}）"
                        )
                        return

                    # 获取群聊消息
                    analysis_days = self.config_manager.get_analysis_days()
                    messages = await self.message_handler.fetch_group_messages(
                        bot_instance, group_id, analysis_days, platform_id
                    )

                    if messages is None:
                        logger.warning(f"群 {group_id} 获取消息失败，跳过分析")
                        return
                    elif not messages:
                        logger.warning(f"群 {group_id} 未获取到足够的消息记录")
                        return

                # 检查消息数量
                min_threshold = self.config_manager.get_min_messages_threshold()
                if len(messages) < min_threshold:
                    logger.warning(
                        f"群 {group_id} 消息数量不足（{len(messages)}条），跳过分析"
                    )
                    return

                logger.info(f"群 {group_id} 获取到 {len(messages)} 条消息，开始分析")

                # 进行分析 - 构造正确的 unified_msg_origin
                # platform_id 已经在前面获取，直接使用
                umo = f"{platform_id}:GroupMessage:{group_id}" if platform_id else None
                analysis_result = await self.analyzer.analyze_messages(
                    messages, group_id, umo
                )
                if not analysis_result:
                    logger.error(f"群 {group_id} 分析失败")
                    return

                # 生成并发送报告
                await self._send_analysis_report(group_id, analysis_result, platform_id)

                # 记录执行时间
                end_time = asyncio.get_event_loop().time()
                execution_time = end_time - start_time
                logger.info(f"群 {group_id} 分析完成，耗时：{execution_time:.2f}秒")

            except Exception as e:
                logger.error(f"群 {group_id} 自动分析执行失败：{e}", exc_info=True)

            finally:
                # 锁资源由 WeakValueDictionary 自动管理，无需手动清理
                logger.info(f"群 {group_id} 自动分析完成")

    async def _get_all_groups(self) -> list[str]:
        """获取所有 bot 实例所在的群列表"""
        all_groups = set()

        if (
            not hasattr(self.bot_manager, "_bot_instances")
            or not self.bot_manager._bot_instances
        ):
            return []

        for platform_id, bot_instance in self.bot_manager._bot_instances.items():
            # 检查该平台是否启用了此插件
            if not self.bot_manager.is_plugin_enabled(
                platform_id, "astrbot_plugin_matrix_daily_analysis"
            ):
                logger.debug(f"平台 {platform_id} 未启用此插件，跳过获取群列表")
                continue

            # Only support Matrix
            if platform_id != "matrix":
                continue

            try:
                if hasattr(bot_instance, "api") and hasattr(bot_instance.api, "get_joined_rooms"):
                    rooms = await bot_instance.api.get_joined_rooms()
                    all_groups.update(rooms)
                    logger.info(f"Matrix 平台获取到 {len(rooms)} 个房间")
            except Exception as e:
                logger.error(f"Matrix 获取房间列表失败：{e}")

        return list(all_groups)

    async def _send_analysis_report(
        self, group_id: str, analysis_result: dict, platform_id: str | None = None
    ):
        logger.info(
            f"[DEBUG][SEND_REPORT] enter "
            f"group_id={group_id}, "
            f"platform_id={platform_id}, "
            f"analysis_result_keys={list(analysis_result.keys()) if isinstance(analysis_result, dict) else type(analysis_result)}"
        )

        """发送分析报告到群"""
        try:
            # Define avatar getter function
            async def avatar_getter(user_id):
                if not platform_id:
                    return None

                # Check if it's Matrix
                if platform_id == "matrix":
                    try:
                        # Assuming user_id is MXID
                        bot_instance = self.bot_manager.get_bot_instance(platform_id)
                        if bot_instance and hasattr(bot_instance, "api") and hasattr(bot_instance.api, "get_avatar_url"):
                            # Get profile to find avatar_url (mxc URI)
                            avatar_mxc = await bot_instance.api.get_avatar_url(user_id)

                            if avatar_mxc and hasattr(bot_instance.api, "get_thumbnail"):
                                # Convert mxc to bytes (thumbnail) and then to base64 data URI
                                avatar_bytes = await bot_instance.api.get_thumbnail(
                                    avatar_mxc, width=100, height=100, method="crop"
                                )
                                b64 = base64.b64encode(avatar_bytes).decode()
                                return f"data:image/jpeg;base64,{b64}"
                    except Exception as e:
                        logger.debug(f"Matrix avatar fetch failed for {user_id}: {e}")
                        return None
                return None

            output_format = self.config_manager.get_output_format()

            if output_format == "image":
                if self.html_render_func:
                    # 使用图片格式
                    logger.info(f"群 {group_id} 自动分析使用图片报告格式")
                    try:
                        (
                            image_url,
                            html_content,
                        ) = await self.report_generator.generate_image_report(
                            analysis_result, group_id, self.html_render_func, avatar_getter
                        )
                        logger.debug(
                            f"[DEBUG][SEND_REPORT] 图片生成结果 "
                            f"group_id={group_id}, "
                            f"image_url={'Success' if image_url else 'Fail'}, "
                            f"html_content={'Available' if html_content else 'None'}"
                        )

                        if image_url:
                            success = await self._send_image_message(
                                group_id, image_url
                            )
                            if success:
                                logger.info(f"群 {group_id} 图片报告发送成功")
                            else:
                                # 图片发送失败，回退到文本
                                logger.warning(
                                    f"群 {group_id} 发送图片报告失败，回退到文本报告"
                                )
                                text_report = (
                                    self.report_generator.generate_text_report(
                                        analysis_result
                                    )
                                )
                                await self._send_text_message(
                                    group_id, f"📊 每日群聊分析报告：\n\n{text_report}"
                                )
                        elif html_content:
                            # 生成失败但有 HTML，加入重试队列
                            logger.warning(
                                f"群 {group_id} 图片报告生成失败，加入重试队列"
                            )

                            # 尝试获取 platform_id (如果参数为 None)
                            if not platform_id:
                                platform_id = await self.get_platform_id_for_group(
                                    group_id
                                )

                            if platform_id:
                                # 定时任务静默重试，不发送提示消息，只记录日志
                                logger.info(
                                    f"群 {group_id} 图片生成失败，已静默加入重试队列"
                                )
                                await self.retry_manager.add_task(
                                    html_content, analysis_result, group_id, platform_id
                                )
                            else:
                                logger.error(
                                    f"群 {group_id} 无法获取平台 ID，无法加入重试队列"
                                )
                                # Fallback to text
                                text_report = (
                                    self.report_generator.generate_text_report(
                                        analysis_result
                                    )
                                )
                                await self._send_text_message(
                                    group_id, f"📊 每日群聊分析报告：\n\n{text_report}"
                                )

                        else:
                            # 图片生成失败（返回 None），回退到文本
                            logger.warning(
                                f"群 {group_id} 图片报告生成失败（返回 None），回退到文本报告"
                            )
                            text_report = self.report_generator.generate_text_report(
                                analysis_result
                            )
                            await self._send_text_message(
                                group_id, f"📊 每日群聊分析报告：\n\n{text_report}"
                            )
                    except Exception as img_e:
                        logger.error(
                            f"群 {group_id} 图片报告生成异常：{img_e}，回退到文本报告"
                        )
                        text_report = self.report_generator.generate_text_report(
                            analysis_result
                        )
                        await self._send_text_message(
                            group_id, f"📊 每日群聊分析报告：\n\n{text_report}"
                        )
                else:
                    # 没有 html_render 函数，回退到文本报告
                    logger.warning(f"群 {group_id} 缺少 html_render 函数，回退到文本报告")
                    text_report = self.report_generator.generate_text_report(
                        analysis_result
                    )
                    await self._send_text_message(
                        group_id, f"📊 每日群聊分析报告：\n\n{text_report}"
                    )

            elif output_format == "pdf":
                if not self.config_manager.playwright_available:
                    logger.warning(f"群 {group_id} PDF 功能不可用，回退到文本报告")
                    text_report = self.report_generator.generate_text_report(
                        analysis_result
                    )
                    await self._send_text_message(
                        group_id, f"📊 每日群聊分析报告：\n\n{text_report}"
                    )
                else:
                    try:
                        pdf_path = await self.report_generator.generate_pdf_report(
                            analysis_result, group_id, avatar_getter
                        )
                        if pdf_path:
                            await self._send_pdf_file(group_id, pdf_path)
                            logger.info(f"群 {group_id} 自动分析完成，已发送 PDF 报告")
                        else:
                            logger.error(
                                f"群 {group_id} PDF 报告生成失败（返回 None），回退到文本报告"
                            )
                            text_report = self.report_generator.generate_text_report(
                                analysis_result
                            )
                            await self._send_text_message(
                                group_id, f"📊 每日群聊分析报告：\n\n{text_report}"
                            )
                    except Exception as pdf_e:
                        logger.error(
                            f"群 {group_id} PDF 报告生成异常：{pdf_e}，回退到文本报告"
                        )
                        text_report = self.report_generator.generate_text_report(
                            analysis_result
                        )
                        await self._send_text_message(
                            group_id, f"📊 每日群聊分析报告：\n\n{text_report}"
                        )
            else:
                text_report = self.report_generator.generate_text_report(
                    analysis_result
                )
                await self._send_text_message(
                    group_id, f"📊 每日群聊分析报告：\n\n{text_report}"
                )

            logger.info(f"群 {group_id} 自动分析完成，已发送报告")

        except Exception as e:
            logger.error(f"发送分析报告到群 {group_id} 失败：{e}")

    async def _send_image_message(self, group_id: str, image_url: str):
        """发送图片消息到群（仅支持 Matrix，通过 upload 方式）"""
        try:
            prefix_text = "📊 每日群聊分析报告已生成："

            # ===== 获取平台 =====
            if (
                hasattr(self.bot_manager, "_bot_instances")
                and self.bot_manager._bot_instances
            ):
                available_platforms = list(self.bot_manager._bot_instances.items())
                logger.info(
                    f"群 {group_id} 检测到 {len(available_platforms)} 个可用平台，开始依次尝试发送图片..."
                )
            else:
                logger.warning(f"群 {group_id} 没有多个平台可用，使用回退逻辑")
                platform_id = await self.get_platform_id_for_group(group_id)
                if not platform_id:
                    logger.error(f"❌ 群 {group_id} 无法获取平台 ID，无法发送图片")
                    return False
                bot_instance = self.bot_manager.get_bot_instance(platform_id)
                if not bot_instance:
                    logger.error(
                        f"❌ 群 {group_id} 发送图片失败：缺少 bot 实例（平台：{platform_id}）"
                    )
                    return False
                available_platforms = [(platform_id, bot_instance)]

            # 仅支持 Matrix，必须下载后上传
            try:
                # 设置请求超时和响应大小限制
                timeout = aiohttp.ClientTimeout(total=30)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(image_url) as resp:
                        if resp.status != 200:
                            logger.error(
                                f"群 {group_id} 下载图片失败：status={resp.status}"
                            )
                            image_bytes = None
                        else:
                            max_bytes = 10 * 1024 * 1024 # 10MB
                            image_bytes = await resp.read()
                            if len(image_bytes) > max_bytes:
                                logger.error(f"图片太大：{len(image_bytes)}")
                                image_bytes = None
            except Exception as e:
                logger.error(f"群 {group_id} 下载图片失败：{e}")
                image_bytes = None

            if image_bytes:
                for test_platform_id, test_bot_instance in available_platforms:
                    if test_platform_id != "matrix":
                        continue

                    try:
                        logger.info("尝试使用 Matrix 平台发送图片...")
                        if hasattr(test_bot_instance, "api") and hasattr(test_bot_instance.api, "upload_file"):
                            upload_resp = await test_bot_instance.api.upload_file(image_bytes, "image/png", "report.png")
                            content_uri = upload_resp.get("content_uri")
                            if content_uri:
                                    # Send Text First
                                    await test_bot_instance.api.room_send(
                                    room_id=group_id,
                                    message_type="m.room.message",
                                    content={"msgtype": "m.text", "body": prefix_text}
                                    )
                                    # Send Image
                                    await test_bot_instance.api.room_send(
                                    room_id=group_id,
                                    message_type="m.room.message",
                                    content={
                                        "msgtype": "m.image",
                                        "body": "Daily Report.png",
                                        "url": content_uri
                                    }
                                    )
                                    logger.info("✅ Matrix 图片发送成功")
                                    return True
                    except Exception as e:
                            logger.error(f"Matrix 图片发送失败：{e}")
                    continue

            logger.error(f"❌ 群 {group_id} 图片发送失败，回退到文本")
            await self._send_text_message(
                group_id,
                f"{prefix_text}\n图片发送失败，请查看链接：\n{image_url}",
            )
            return False

        except Exception as e:
            logger.error(f"发送图片消息到群 {group_id} 失败：{e}")
            return False

    async def _send_text_message(self, group_id: str, text_content: str):
        """发送文本消息到群 - 仅支持 Matrix"""
        try:
            # 获取所有可用的平台，依次尝试发送
            if (
                hasattr(self.bot_manager, "_bot_instances")
                and self.bot_manager._bot_instances
            ):
                available_platforms = list(self.bot_manager._bot_instances.items())
                logger.info(
                    f"群 {group_id} 检测到 {len(available_platforms)} 个可用平台，开始依次尝试发送文本..."
                )
            else:
                platform_id = await self.get_platform_id_for_group(group_id)
                if not platform_id:
                    logger.error(f"❌ 群 {group_id} 无法获取平台 ID，无法发送文本")
                    return False
                bot_instance = self.bot_manager.get_bot_instance(platform_id)
                available_platforms = [(platform_id, bot_instance)]

            for test_platform_id, test_bot_instance in available_platforms:
                if test_platform_id != "matrix":
                    continue
                try:
                    await test_bot_instance.api.room_send(
                        room_id=group_id,
                        message_type="m.room.message",
                        content={"msgtype": "m.text", "body": text_content}
                    )
                    logger.info("✅ Matrix 文本发送成功")
                    return True
                except Exception as e:
                    logger.error(f"Matrix 文本发送失败：{e}")
                    continue

            logger.error(f"❌ 群 {group_id} 文本发送失败")
            return False

        except Exception as e:
            logger.error(f"发送文本消息到群 {group_id} 失败：{e}")
            return False

    async def _send_pdf_file(self, group_id: str, pdf_path: str):
        """发送 PDF 文件到群 - 仅支持 Matrix"""
        try:
            # 获取所有可用的平台，依次尝试发送
            if (
                hasattr(self.bot_manager, "_bot_instances")
                and self.bot_manager._bot_instances
            ):
                available_platforms = list(self.bot_manager._bot_instances.items())
                logger.info(
                    f"群 {group_id} 检测到 {len(available_platforms)} 个可用平台，开始依次尝试发送 PDF..."
                )
            else:
                platform_id = await self.get_platform_id_for_group(group_id)
                if not platform_id:
                    logger.error(f"❌ 群 {group_id} 无法获取平台 ID，无法发送 PDF")
                    return False
                bot_instance = self.bot_manager.get_bot_instance(platform_id)
                available_platforms = [(platform_id, bot_instance)]

            for test_platform_id, test_bot_instance in available_platforms:
                if test_platform_id != "matrix":
                    continue
                try:
                    # Read file content
                    with open(pdf_path, "rb") as f:
                        pdf_data = f.read()

                    if hasattr(test_bot_instance, "api") and hasattr(test_bot_instance.api, "upload_file"):
                        # Upload
                        upload_resp = await test_bot_instance.api.upload_file(pdf_data, "application/pdf", "report.pdf")
                        content_uri = upload_resp.get("content_uri")
                        if content_uri:
                                # Send Text First
                                await test_bot_instance.api.room_send(
                                room_id=group_id,
                                message_type="m.room.message",
                                content={"msgtype": "m.text", "body": "📊 每日群聊分析报告已生成："}
                                )
                                # Send File
                                await test_bot_instance.api.room_send(
                                room_id=group_id,
                                message_type="m.room.message",
                                content={
                                    "msgtype": "m.file",
                                    "body": "Daily Report.pdf",
                                    "url": content_uri,
                                    "info": {"mimetype": "application/pdf"}
                                }
                                )
                                logger.info("✅ Matrix PDF 发送成功")
                                return True
                except Exception as e:
                        logger.error(f"Matrix PDF 发送失败：{e}")
                continue

            logger.error(f"❌ 群 {group_id} PDF 发送失败")
            return False

        except Exception as e:
            logger.error(f"发送 PDF 文件到群 {group_id} 失败：{e}")
            return False
