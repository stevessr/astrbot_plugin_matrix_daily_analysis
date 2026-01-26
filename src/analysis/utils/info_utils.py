class InfoUtils:
    @staticmethod
    def get_user_nickname(config_manager, sender) -> str:
        """
        获取用户昵称

        Matrix 平台仅使用昵称/显示名字段
        """
        return (
            sender.get("nickname", "")
            or sender.get("displayname", "")
            or sender.get("user_id", "")
        )
