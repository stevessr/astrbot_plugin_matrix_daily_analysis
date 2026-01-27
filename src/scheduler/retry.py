import asyncio
import random
import time
from collections.abc import Callable
from dataclasses import dataclass

from astrbot.api import logger


@dataclass
class RetryTask:
    """重试任务数据类"""

    html_content: str
    analysis_result: dict  # 保存原始分析结果，用于文本回退
    group_id: str
    platform_id: str  # 需要保存 platform_id 以便找回 Bot
    retry_count: int = 0
    max_retries: int = 3
    created_at: float = 0.0

    def __post_init__(self):
        if self.created_at == 0.0:
            self.created_at = time.time()


class RetryManager:
    """
    重试管理器

    实现了一个简单的延迟队列 + 死信队列机制：
    1. 任务加入队列
    2. Worker 取出任务，尝试执行
    3. 失败则指数退避（延迟）后放回队列
    4. 超过最大重试次数放入死信队列
    """

    def __init__(self, bot_manager, html_render_func: Callable, report_generator=None):
        self.bot_manager = bot_manager
        self.html_render_func = html_render_func
        self.report_generator = report_generator  # 用于生成文本报告
        self.queue = asyncio.Queue()
        self.running = False
        self.worker_task = None
        self._dlq = []  # 死信队列 (Failures)

    async def start(self):
        """启动重试工作进程"""
        if self.running:
            return
        self.running = True
        self.worker_task = asyncio.create_task(self._worker())
        logger.info("[RetryManager] 图片重试管理器已启动")

    async def stop(self):
        """停止重试工作进程"""
        self.running = False
        if self.worker_task:
            self.worker_task.cancel()
            try:
                await self.worker_task
            except asyncio.CancelledError:
                pass

        # 检查剩余任务
        pending_count = self.queue.qsize()
        if pending_count > 0:
            logger.warning(
                f"[RetryManager] 停止时仍有 {pending_count} 个任务在队列中 pending"
            )

        logger.info("[RetryManager] 图片重试管理器已停止")

    async def add_task(
        self, html_content: str, analysis_result: dict, group_id: str, platform_id: str
    ):
        """添加重试任务"""
        if not self.running:
            logger.warning(
                "[RetryManager] 警告：添加任务时管理器未运行，正在尝试启动..."
            )
            await self.start()

        task = RetryTask(
            html_content=html_content,
            analysis_result=analysis_result,
            group_id=group_id,
            platform_id=platform_id,
            created_at=time.time(),
        )
        await self.queue.put(task)
        logger.info(f"[RetryManager] 已添加群 {group_id} 的重试任务")

    async def _worker(self):
        """工作进程循环"""
        while self.running:
            try:
                task: RetryTask = await self.queue.get()

                # 延迟策略：指数回退 (5s, 10s, 20s...) + 随机波动 (1~5s)
                jitter = random.uniform(1, 5)
                delay = 5 * (2**task.retry_count) + jitter

                logger.info(
                    f"[RetryManager] 处理群 {task.group_id} 的重试任务 (第 {task.retry_count + 1} 次尝试)"
                )

                success = await self._process_task(task)

                if success:
                    logger.info(f"[RetryManager] 群 {task.group_id} 重试成功")
                    self.queue.task_done()
                else:
                    task.retry_count += 1
                    if task.retry_count < task.max_retries:
                        logger.warning(
                            f"[RetryManager] 群 {task.group_id} 重试失败，{delay}秒后再次尝试"
                        )
                        asyncio.create_task(self._requeue_after_delay(task, delay))
                        self.queue.task_done()
                    else:
                        logger.error(
                            f"[RetryManager] 群 {task.group_id} 超过最大重试次数，移入死信队列并尝试文本回退"
                        )
                        self._dlq.append(task)
                        self.queue.task_done()
                        # 尝试发送文本回退
                        await self._send_fallback_text(task)
                        await self._notify_failure(task)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[RetryManager] Worker 异常：{e}", exc_info=True)
                await asyncio.sleep(1)

    async def _requeue_after_delay(self, task: RetryTask, delay: float):
        await asyncio.sleep(delay)
        await self.queue.put(task)

    async def _process_task(self, task: RetryTask) -> bool:
        """执行具体的渲染和发送逻辑"""
        try:
            # 1. 尝试渲染
            image_options = {
                "full_page": True,
                "type": "jpeg",
                "quality": 85,
            }
            logger.debug(f"[RetryManager] 正在重新渲染群 {task.group_id} 的图片...")

            # 修改：return_url=False 获取二进制数据而不是 URL
            # 这对于解决 NTmatrix "Timeout" 错误至关重要，因为它避免了 matrix 客户端下载本地/内网 URL 的网络问题
            image_data = await self.html_render_func(
                task.html_content,
                {},
                False,  # return_url=False, 获取 bytes
                image_options,
            )

            if not image_data:
                logger.warning(
                    f"[RetryManager] 重新渲染失败（返回空数据）{task.group_id}"
                )
                return False

            # 2. 获取 Bot 实例
            bot = self.bot_manager.get_bot_instance(task.platform_id)
            if not bot:
                logger.error(
                    f"[RetryManager] 平台 {task.platform_id} 的 Bot 实例未找到，无法重试"
                )
                return False  # 无法重试，因为 Bot 已离线

            if task.platform_id != "matrix":
                logger.warning(
                    f"[RetryManager] 平台 {task.platform_id} 非 Matrix，跳过重试"
                )
                return False

            # 3. 发送图片（Matrix 上传 + 发送）
            logger.info(
                f"[RetryManager] 正在向群 {task.group_id} 发送重试图片 (Matrix 上传模式)..."
            )
            client = bot.api if hasattr(bot, "api") else bot
            if not (hasattr(client, "upload_file") and hasattr(client, "send_message")):
                logger.warning(
                    "[RetryManager] Bot 缺少 Matrix 发送接口，无法发送图片。"
                )
                return False

            try:
                upload_resp = await client.upload_file(
                    image_data, "image/jpeg", "report.jpg"
                )
                content_uri = upload_resp.get("content_uri")
                if not content_uri:
                    logger.warning("[RetryManager] 图片上传失败：未返回 content_uri")
                    return False

                await client.send_message(
                    task.group_id,
                    "m.room.message",
                    {
                        "msgtype": "m.text",
                        "body": "📊 每日群聊分析报告（重试发送）：",
                    },
                )
                await client.send_message(
                    task.group_id,
                    "m.room.message",
                    {
                        "msgtype": "m.image",
                        "body": "Daily Report.jpg",
                        "url": content_uri,
                    },
                )
                return True
            except Exception as e:
                logger.error(f"[RetryManager] Matrix 图片发送失败：{e}")
                return False

        except Exception as e:
            logger.error(f"[RetryManager] 处理任务时发生意外错误：{e}", exc_info=True)
            return False

    async def _send_fallback_text(self, task: RetryTask):
        """发送文本回退报告（使用合并转发）"""
        if not self.report_generator:
            logger.warning("[RetryManager] 未配置 ReportGenerator，无法发送文本回退")
            return

        try:
            logger.info(f"[RetryManager] 正在为群 {task.group_id} 生成文本回退报告...")
            text_report = self.report_generator.generate_text_report(
                task.analysis_result
            )

            bot = self.bot_manager.get_bot_instance(task.platform_id)
            if not bot:
                return

            client = bot.api if hasattr(bot, "api") else bot
            if not hasattr(client, "send_message"):
                logger.warning(
                    "[RetryManager] Bot 缺少 Matrix room_send，无法发送回退文本"
                )
                return

            await client.send_message(
                task.group_id,
                "m.room.message",
                {
                    "msgtype": "m.text",
                    "body": f"⚠️ 图片报告多次生成失败，为您呈现文本版报告：\n{text_report}",
                },
            )

        except Exception as e:
            logger.error(f"[RetryManager] 文本回退发送失败：{e}", exc_info=True)
