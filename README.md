# AstrBot 对话便签

一个面向 AstrBot 4.25.5+ 的私聊长期记忆插件。管理员可以在 AstrBot 后台维护用户
便签；每次回复前，插件只选择与当前消息相关的少量便签，以临时内容块提供给模型。

## 功能

- 私聊用户隔离，群聊不登记、不读取、不注入
- 后台查看、搜索、筛选、新增、编辑和删除便签
- 分类：偏好、习惯、人物、事件、雷区、目标、待办、其他
- SQLite 持久化
- 中文片段、英文单词和分类名称的本地相关性检索
- 明确询问“你还记得我吗”时的最近便签回退
- `TextPart(...).mark_as_temp()` 本轮临时注入，不写入聊天历史
- 用户与机器人消息按批次缓冲，默认累计 20 条或空闲 30 分钟后自动整理
- 自动整理静默运行，支持使用当前会话模型或指定专用模型
- 自动便签可在后台查看、修改和删除，并带有“自动生成”标识

## 安装

将 `astrbot_plugin_memory_cards` 整个目录放到 AstrBot 的 `data/plugins/` 中，然后在
AstrBot 后台重载插件或重启 AstrBot。

容器部署时需要持久化：

```text
data/plugin_data/astrbot_plugin_memory_cards/
```

数据库文件为 `memory.db`。插件卸载不会主动删除它。

## 使用

1. 用户先与机器人私聊一次，插件会登记平台实例、用户 ID 和当前昵称。
2. 进入 AstrBot 后台的插件详情页，打开“对话便签”页面。
3. 选择用户，新增或维护便签。
4. 用户继续私聊时，相关便签会自动作为当次请求的临时参考信息。

插件没有聊天指令，也不支持群聊记忆。自动提取只处理私聊。

## 配置

- `enabled`：启用自动注入
- `max_injected_notes`：单次最多便签数，默认 5
- `max_injected_chars`：单次上下文字符数，默认 1500
- `minimum_score`：最低相关性分数，默认 3.0
- `recall_fallback_enabled`：明确回忆意图时返回最近便签
- `auto_extract_enabled`：启用自动提取，默认开启
- `auto_extract_idle_minutes`：空闲触发时间，默认 30 分钟
- `auto_extract_message_threshold`：消息数触发阈值，默认 20 条
- `auto_extract_provider_id`：可选专用整理模型 ID，留空使用当前会话模型
- `auto_extract_max_notes`：单次最多新增或更新便签数，默认 5
- `auto_extract_retry_minutes`：失败重试间隔，默认 10 分钟

消息数和空闲时间是“或”关系，任一达到即整理。没有未处理的新消息时不会调用
模型。

## 隐私与安全

- 为自动整理暂存尚未处理的私聊文本；整理成功后删除对应缓冲批次。
- 自动提取禁止保存密码、验证码、Cookie、令牌和 API 密钥等凭据。
- 隔离键由 AstrBot 平台实例 ID 与发送者 ID 组成。
- 便签被明确标记为不可信参考，不能覆盖当前用户消息。
- WebUI 和接口复用 AstrBot Dashboard 登录鉴权，不新开端口。
- 正文通过文本节点渲染，API 使用参数化 SQL。

## 开发验证

```powershell
python -m pytest -v
python -m ruff check .
python -m compileall astrbot_plugin_memory_cards
```
