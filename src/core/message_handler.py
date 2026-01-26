"""
消息处理模块
负责群聊消息的获取、过滤和预处理
"""

from collections import defaultdict
from datetime import datetime, timedelta

from astrbot.api import logger

from ...src.models.data_models import EmojiStatistics, GroupStatistics, TokenUsage
from ...src.visualization.activity_charts import ActivityVisualizer


class MessageHandler:
    """消息处理器"""

    def __init__(self, config_manager, bot_manager=None):
        self.config_manager = config_manager
        self.activity_visualizer = ActivityVisualizer()
        self.bot_manager = bot_manager

    async def set_bot_matrix_ids(self, bot_matrix_ids):
        """设置机器人matrix号（支持单个matrix号或matrix号列表）"""
        try:
            if self.bot_manager:
                # 确保传入的是列表，保持统一处理
                if isinstance(bot_matrix_ids, list):
                    self.bot_manager.set_bot_matrix_ids(bot_matrix_ids)
                elif bot_matrix_ids:
                    self.bot_manager.set_bot_matrix_ids([bot_matrix_ids])
            logger.info(f"设置机器人matrix号: {bot_matrix_ids}")
        except Exception as e:
            logger.error(f"设置机器人matrix号失败: {e}")

    def set_bot_manager(self, bot_manager):
        """设置bot管理器"""
        self.bot_manager = bot_manager

    def _extract_bot_matrix_id_from_instance(self, bot_instance):
        """从bot实例中提取matrix号（单个）"""
        if hasattr(bot_instance, "self_id") and bot_instance.self_id:
            return str(bot_instance.self_id)
        elif hasattr(bot_instance, "matrix") and bot_instance.matrix:
            return str(bot_instance.matrix)
        elif hasattr(bot_instance, "user_id") and bot_instance.user_id:
            return str(bot_instance.user_id)
        return None

    async def fetch_group_messages(
        self, bot_instance, group_id: str, days: int, platform_id: str | None = None
    ) -> list[dict]:
        """获取群聊消息记录"""
        try:
            # 验证参数
            if not group_id or not bot_instance:
                logger.error(f"群 {group_id} 参数无效")
                return []

            # 确保bot_manager有matrix号列表用于过滤
            if self.bot_manager and not self.bot_manager.has_bot_matrix_id():
                # 尝试从bot_instance提取matrix号并设置为列表
                bot_matrix_id = self._extract_bot_matrix_id_from_instance(bot_instance)
                if bot_matrix_id:
                    # 将单个matrix号转换为列表，保持统一处理
                    self.bot_manager.set_bot_matrix_ids([bot_matrix_id])

            # 计算时间范围
            end_time = datetime.now()
            start_time = end_time - timedelta(days=days)

            logger.info(f"开始获取群 {group_id} 近 {days} 天的消息记录")
            logger.info(
                f"时间范围: {start_time.strftime('%Y-%m-%d %H:%M:%S')} 到 {end_time.strftime('%Y-%m-%d %H:%M:%S')}"
            )

            # 仅支持 Matrix 平台
            return await self._fetch_matrix_messages(bot_instance, group_id, days, start_time, end_time)

        except Exception as e:
            logger.error(f"群 {group_id} 获取群聊消息记录失败: {e}", exc_info=True)
            return []

    async def _fetch_matrix_messages(
        self, bot_instance, group_id: str, days: int, start_time: datetime, end_time: datetime
    ) -> list[dict]:
        """从 Matrix 平台获取群聊消息"""
        messages = []
        # Matrix use millisecond timestamp
        start_ts = int(start_time.timestamp() * 1000)
        end_ts = int(end_time.timestamp() * 1000)

        try:
            limit = self.config_manager.get_max_messages()
            logger.info(f"正在从 Matrix 获取消息，limit={limit}")

            # 获取群成员列表以填充昵称
            display_names = {}
            try:
                if hasattr(bot_instance, "api") and hasattr(bot_instance.api, "get_room_members"):
                    members_resp = await bot_instance.api.get_room_members(group_id)
                    member_events = members_resp.get("chunk", [])
                    for event in member_events:
                        if event.get("type") == "m.room.member":
                            user_id = event.get("state_key")
                            content = event.get("content", {})
                            displayname = content.get("displayname")
                            if user_id and displayname:
                                display_names[user_id] = displayname
            except Exception as e:
                logger.warning(f"获取群成员列表失败，将无法显示昵称: {e}")

            # 使用 room_messages 获取历史消息
            # direction='b' (backwards), limit=config.max_messages
            if hasattr(bot_instance, "api") and hasattr(bot_instance.api, "room_messages"):
                response = await bot_instance.api.room_messages(
                    room_id=group_id,
                    limit=limit,
                    direction="b"
                )
                chunk = response.get("chunk", [])

                # Matrix 返回的是 chunk，包含 events
                for event in chunk:
                    # 过滤非消息事件
                    if event.get("type") != "m.room.message":
                        continue

                    # 检查时间
                    ts = event.get("origin_server_ts", 0)
                    if ts < start_ts:
                        continue # 太旧的消息
                    if ts > end_ts:
                        continue

                    content = event.get("content", {})
                    sender = event.get("sender")

                    # 过滤机器人自己的消息
                    if self.bot_manager and self.bot_manager.should_filter_bot_message(sender):
                        continue

                    # 获取昵称
                    nickname = display_names.get(sender, sender)

                    # 转换消息格式
                    msg_dict = {
                        "time": ts / 1000,
                        "sender": {
                            "user_id": sender,
                            "nickname": nickname,
                            "card": nickname
                        },
                        "message": []
                    }

                    msg_type = content.get("msgtype")
                    if msg_type == "m.text":
                        msg_dict["message"].append({
                            "type": "text",
                            "data": {"text": content.get("body", "")}
                        })
                    elif msg_type == "m.image":
                        msg_dict["message"].append({
                            "type": "image",
                            "data": {"file": content.get("url", "")} # mxc:// url
                        })
                    else:
                        # 其他类型作为文本处理
                         msg_dict["message"].append({
                            "type": "text",
                            "data": {"text": f"[{msg_type}] {content.get('body', '')}"}
                        })

                    messages.append(msg_dict)

                logger.info(f"Matrix 获取到 {len(messages)} 条有效消息")
                return messages
            else:
                logger.error("Bot 实例缺少 Matrix API 支持")
                return []

        except Exception as e:
            logger.error(f"Matrix 获取消息失败: {e}", exc_info=True)
            return []

    def calculate_statistics(self, messages: list[dict]) -> GroupStatistics:
        """计算基础统计数据"""
        total_chars = 0
        participants = set()
        hour_counts = defaultdict(int)
        emoji_statistics = EmojiStatistics()

        for msg in messages:
            sender_id = str(msg.get("sender", {}).get("user_id", ""))
            participants.add(sender_id)

            # 统计时间分布
            msg_time = datetime.fromtimestamp(msg.get("time", 0))
            hour_counts[msg_time.hour] += 1

            # 处理消息内容
            for content in msg.get("message", []):
                if content.get("type") == "text":
                    text = content.get("data", {}).get("text", "")
                    total_chars += len(text)
                elif content.get("type") == "face":
                    # matrix基础表情
                    emoji_statistics.face_count += 1
                    face_id = content.get("data", {}).get("id", "unknown")
                    emoji_statistics.face_details[f"face_{face_id}"] = (
                        emoji_statistics.face_details.get(f"face_{face_id}", 0) + 1
                    )
                elif content.get("type") == "mface":
                    # 动画表情/魔法表情
                    emoji_statistics.mface_count += 1
                    emoji_id = content.get("data", {}).get("emoji_id", "unknown")
                    emoji_statistics.face_details[f"mface_{emoji_id}"] = (
                        emoji_statistics.face_details.get(f"mface_{emoji_id}", 0) + 1
                    )
                elif content.get("type") == "bface":
                    # 超级表情
                    emoji_statistics.bface_count += 1
                    emoji_id = content.get("data", {}).get("p", "unknown")
                    emoji_statistics.face_details[f"bface_{emoji_id}"] = (
                        emoji_statistics.face_details.get(f"bface_{emoji_id}", 0) + 1
                    )
                elif content.get("type") == "sface":
                    # 小表情
                    emoji_statistics.sface_count += 1
                    emoji_id = content.get("data", {}).get("id", "unknown")
                    emoji_statistics.face_details[f"sface_{emoji_id}"] = (
                        emoji_statistics.face_details.get(f"sface_{emoji_id}", 0) + 1
                    )
                elif content.get("type") == "image":
                    # 检查是否是动画表情（通过summary字段判断）
                    data = content.get("data", {})
                    summary = data.get("summary", "")
                    if "动画表情" in summary or "表情" in summary:
                        # 动画表情（以image形式发送）
                        emoji_statistics.mface_count += 1
                        file_name = data.get("file", "unknown")
                        emoji_statistics.face_details[f"animated_{file_name}"] = (
                            emoji_statistics.face_details.get(
                                f"animated_{file_name}", 0
                            )
                            + 1
                        )
                    else:
                        # 普通图片，不计入表情统计
                        pass
                elif (
                    content.get("type") in ["record", "video"]
                    and "emoji" in str(content.get("data", {})).lower()
                ):
                    # 其他可能的表情类型
                    emoji_statistics.other_emoji_count += 1

        # 找出最活跃时段
        most_active_hour = (
            max(hour_counts.items(), key=lambda x: x[1])[0] if hour_counts else 0
        )
        most_active_period = (
            f"{most_active_hour:02d}:00-{(most_active_hour + 1) % 24:02d}:00"
        )

        # 生成活跃度可视化数据
        activity_visualization = (
            self.activity_visualizer.generate_activity_visualization(messages)
        )

        return GroupStatistics(
            message_count=len(messages),
            total_characters=total_chars,
            participant_count=len(participants),
            most_active_period=most_active_period,
            golden_quotes=[],
            emoji_count=emoji_statistics.total_emoji_count,  # 保持向后兼容
            emoji_statistics=emoji_statistics,
            activity_visualization=activity_visualization,
            token_usage=TokenUsage(),
        )
