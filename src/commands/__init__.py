"""
命令处理模块
将各个命令的具体实现拆分到独立文件中
"""

from .dialogue_poll import DialoguePollHandler
from .group_analysis import GroupAnalysisHandler
from .personal_report import PersonalReportHandler
from .settings import SettingsHandler

__all__ = [
    "DialoguePollHandler",
    "GroupAnalysisHandler",
    "PersonalReportHandler",
    "SettingsHandler",
]
