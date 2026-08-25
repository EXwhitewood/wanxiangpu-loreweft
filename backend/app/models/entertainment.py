"""
娱乐设置模型 (Entertainment Settings)
=====================================

定义"等待娱乐"功能的配置数据结构。该功能用于在长时间等待章节生成/审查
工作流完成时，向用户提供跳转外部平台（抖音/B站/网易云等）和经典小游戏
（贪吃蛇/2048）以解闷。

设计原则
--------
- 与 AppSettings（API 密钥配置）解耦：娱乐设置与 API 配置无关，独立存储
  在 `data/entertainment_settings.json`，避免污染 agent_settings.json
- 默认链接预置：首次加载若无存储文件，返回内置默认链接列表
- 链接可自定义：用户可在设置页面新增/删除/编辑自定义链接
"""

from typing import Literal

from pydantic import BaseModel, Field


# 链接分类（视频/音乐/小说/自定义）
LinkCategory = Literal["video", "music", "novel", "custom"]


class EntertainmentLink(BaseModel):
    """单条娱乐跳转链接"""
    name: str                                    # 显示名称，如"B站"
    url: str                                     # 完整 URL，如"https://www.bilibili.com"
    category: LinkCategory = "custom"            # 分类，用于分组显示
    icon: str | None = None                      # 可选 emoji 图标，如"📺"
    enabled: bool = True                         # 是否启用，可在设置中临时关闭


class EntertainmentSettings(BaseModel):
    """娱乐功能完整配置"""
    enabled: bool = True                                            # 总开关：是否启用娱乐功能
    show_trigger_button: bool = True                                # 是否显示悬浮触发按钮
    auto_open_on_workflow_wait: bool = False                        # 工作流进入等待态时自动弹出（默认关闭，避免打扰）
    notify_on_workflow_complete: bool = True                        # 工作流完成时弱提醒（顶部 toast）
    links: list[EntertainmentLink] = Field(default_factory=list)    # 跳转链接列表
    enabled_games: list[str] = Field(default_factory=lambda: ["snake", "2048"])  # 启用的小游戏 ID 列表


def get_default_links() -> list[EntertainmentLink]:
    """获取内置默认链接列表（首次加载或重置时使用）"""
    return [
        # 视频平台
        EntertainmentLink(name="抖音", url="https://www.douyin.com", category="video", icon="🎵"),
        EntertainmentLink(name="B站", url="https://www.bilibili.com", category="video", icon="📺"),
        EntertainmentLink(name="YouTube", url="https://www.youtube.com", category="video", icon="▶️"),
        EntertainmentLink(name="快手", url="https://www.kuaishou.com", category="video", icon="🎬"),
        # 音乐平台
        EntertainmentLink(name="网易云音乐", url="https://music.163.com", category="music", icon="🎵"),
        EntertainmentLink(name="QQ音乐", url="https://y.qq.com", category="music", icon="🎶"),
        EntertainmentLink(name="Spotify", url="https://open.spotify.com", category="music", icon="🎧"),
        # 小说平台
        EntertainmentLink(name="番茄小说", url="https://fanqienovel.com", category="novel", icon="🍅"),
        EntertainmentLink(name="起点中文网", url="https://www.qidian.com", category="novel", icon="📚"),
        EntertainmentLink(name="晋江文学城", url="https://www.jjwxc.net", category="novel", icon="📖"),
    ]


def get_default_settings() -> EntertainmentSettings:
    """获取带默认链接的娱乐设置实例"""
    return EntertainmentSettings(
        enabled=True,
        show_trigger_button=True,
        auto_open_on_workflow_wait=False,
        notify_on_workflow_complete=True,
        links=get_default_links(),
        enabled_games=["snake", "2048"],
    )
