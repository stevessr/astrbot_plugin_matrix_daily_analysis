<div align="center">

# Matrix 群聊日常分析插件

[![AstrBot](https://img.shields.io/badge/AstrBot-Plugin-ff69b4?style=for-the-badge)](https://github.com/AstrBotDevs/AstrBot)
[![License](https://img.shields.io/badge/License-MIT-green.svg?style=for-the-badge)](LICENSE)

_✨ 基于 AstrBot 的 Matrix 群聊日常分析插件，生成结构化统计与精美报告。 ✨_

</div>

## 功能概览

### 🎯 智能分析
- **统计数据**：消息数、活跃人数、表情与时间分布等
- **话题分析**：基于 LLM 提取核心话题与总结
- **用户称号**：按聊天行为生成称号与画像
- **金句提取**：精选代表性发言与理由

### 📊 报告输出
- **image**：渲染图片报告（推荐）
- **text**：文本报告
- **pdf**：PDF 报告（需安装 Playwright）

### ⚙️ 自动化与模板
- **定时自动分析**：按日计划执行
- **并发控制**：避免 LLM 请求过载
- **模板切换**：多套模板可选，支持预览

> Matrix-only：本插件仅支持 Matrix 平台。

## 配置结构（按用途分组）

配置已按用途分组（仅列关键项）：

- `group_access`：群聊权限
  - `mode`（whitelist/blacklist/none）
  - `list`
- `auto_analysis`：自动分析
  - `enabled` / `time` / `bot_matrix_ids`
- `analysis`：分析参数
  - `days` / `max_messages` / `min_messages_threshold` / `max_concurrent_tasks`
  - `history_filters`（含 prefixes/users/skip_bots，控制所有分析功能的历史消息过滤）
  - `topic` / `user_title` / `golden_quote`（含 enabled / max_* / max_tokens / provider_id / prompts）
  - `dialogue_poll`（含 provider_id、max_tokens、max_options、prompt，控制 `/对话投票`）
  - `dialogue_poll`（含 provider_id，用于 `/对话投票` 命令）
- `llm`：通用 LLM 设置
  - `provider_id` / `timeout` / `retries` / `backoff`
- `output`：输出设置
  - `format`（image/text/pdf）
  - `template`
  - `pdf.filename_format` / `pdf.browser_path`

> PDF 报告固定保存在插件数据目录的 `reports` 下，不支持自定义输出目录。

## 使用方法

### 群分析
```
/群分析 [天数]
```
- 默认 1 天，可指定 1-7 天

### 对话投票
```
/对话投票 [天数]
```
- 根据历史消息生成“嘎啦给目”风格单选投票
- 默认 1 天，可指定 1-7 天

### 分析设置
```
/分析设置 [enable|disable|status|reload|test]
```
- `enable` / `disable`：启用或禁用当前房间
- `status`：查看状态
- `reload`：重载配置并重启定时任务
- `test`：测试自动分析

### 输出格式
```
/设置格式 [image|text|pdf]
```

### 模板
```
/查看模板
/设置模板 [模板名称或序号]
```

### PDF 依赖安装
```
/安装 PDF
```
> 安装完成后需完全重启 AstrBot。

## 依赖要求

- 已配置可用的 LLM Provider
- 已安装 Matrix 适配器：`astrbot_plugin_matrix_adapter`

## 注意事项

- 大量消息会增加 LLM Token 消耗
- Matrix 发送图片/文件需要先上传，网络不畅会影响发送
- 图片发送失败会进入重试队列，失败后回退文本


### P.S.
本插件基于

https://github.com/SXP-Simon/astrbot_plugin_qq_group_daily_analysis

修改
