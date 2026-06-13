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

第一版没有聊天指令，也不支持群聊记忆或自动提取便签。

## 配置

- `enabled`：启用自动注入
- `max_injected_notes`：单次最多便签数，默认 5
- `max_injected_chars`：单次上下文字符数，默认 1500
- `minimum_score`：最低相关性分数，默认 3.0
- `recall_fallback_enabled`：明确回忆意图时返回最近便签

## 隐私与安全

- 只保存管理员明确创建的便签，不保存聊天全文。
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
