"""
统计分析模块
负责用户活跃度分析和其他统计功能
"""

from collections import defaultdict

from ..utils.time_utils import get_hour_from_timestamp
from .utils import InfoUtils


class UserAnalyzer:
    """用户分析器"""

    def __init__(self, config_manager):
        self.config_manager = config_manager

    def analyze_users(self, messages: list[dict]) -> dict[str, dict]:
        """分析用户活跃度"""
        # 获取机器人 matrix 号列表用于过滤
        bot_matrix_ids = self.config_manager.get_bot_matrix_ids()

        user_stats = defaultdict(
            lambda: {
                "message_count": 0,
                "char_count": 0,
                "emoji_count": 0,
                "nickname": "",
                "hours": defaultdict(int),
                "reply_count": 0,
            }
        )

        for msg in messages:
            if not isinstance(msg, dict):
                continue
            sender = msg.get("sender", {})
            if not isinstance(sender, dict):
                continue
            user_id = str(sender.get("user_id", ""))

            # 跳过机器人自己的消息，避免进入统计
            if bot_matrix_ids and user_id in [str(matrix) for matrix in bot_matrix_ids]:
                continue

            nickname = InfoUtils.get_user_nickname(self.config_manager, sender)

            user_stats[user_id]["message_count"] += 1
            user_stats[user_id]["nickname"] = nickname

            # 统计时间分布
            hour = get_hour_from_timestamp(msg.get("time", 0))
            user_stats[user_id]["hours"][hour] += 1

            # 处理消息内容
            message_items = msg.get("message", [])
            if not isinstance(message_items, list):
                continue
            for content in message_items:
                if not isinstance(content, dict):
                    continue
                data = content.get("data", {})
                if not isinstance(data, dict):
                    data = {}
                if content.get("type") == "text":
                    text = data.get("text", "")
                    user_stats[user_id]["char_count"] += len(text)
                elif content.get("type") == "face":
                    # matrix 基础表情
                    user_stats[user_id]["emoji_count"] += 1
                elif content.get("type") == "mface":
                    # 动画表情/魔法表情
                    user_stats[user_id]["emoji_count"] += 1
                elif content.get("type") == "bface":
                    # 超级表情
                    user_stats[user_id]["emoji_count"] += 1
                elif content.get("type") == "sface":
                    # 小表情
                    user_stats[user_id]["emoji_count"] += 1
                elif content.get("type") == "image":
                    # 检查是否是动画表情（通过 summary 字段判断）
                    summary = data.get("summary", "")
                    if "动画表情" in summary or "表情" in summary:
                        # 动画表情（以 image 形式发送）
                        user_stats[user_id]["emoji_count"] += 1
                elif content.get("type") == "reply":
                    user_stats[user_id]["reply_count"] += 1

        return dict(user_stats)

    def get_top_users(
        self, user_analysis: dict[str, dict], limit: int = 10
    ) -> list[dict]:
        """获取最活跃的用户"""
        # 获取机器人 matrix 号列表用于过滤
        bot_matrix_ids = self.config_manager.get_bot_matrix_ids()

        users = []
        for user_id, stats in user_analysis.items():
            # 过滤机器人自己
            if bot_matrix_ids and str(user_id) in [
                str(matrix) for matrix in bot_matrix_ids
            ]:
                continue

            users.append(
                {
                    "user_id": user_id,
                    "nickname": stats["nickname"],
                    "message_count": stats["message_count"],
                    "char_count": stats["char_count"],
                    "emoji_count": stats["emoji_count"],
                    "reply_count": stats["reply_count"],
                }
            )

        # 按消息数量排序
        users.sort(key=lambda x: x["message_count"], reverse=True)
        return users[:limit]

    def get_user_activity_pattern(
        self, user_analysis: dict[str, dict], user_id: str
    ) -> dict:
        """获取用户活动模式"""
        if user_id not in user_analysis:
            return {}

        stats = user_analysis[user_id]
        hours = stats["hours"]

        # 找出最活跃的时间段
        most_active_hour = max(hours.items(), key=lambda x: x[1])[0] if hours else 0

        # 计算夜间活跃度
        night_messages = sum(hours[h] for h in range(0, 6))
        night_ratio = (
            night_messages / stats["message_count"] if stats["message_count"] > 0 else 0
        )

        return {
            "most_active_hour": most_active_hour,
            "night_ratio": night_ratio,
            "hourly_distribution": dict(hours),
        }
