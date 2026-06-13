from astrbot.api import AstrBotConfig
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.core.agent.message import TextPart

PLUGIN_NAME = "astrbot_plugin_memory_cards"


@register(PLUGIN_NAME, "Lv_Tu", "私聊长期记忆卡片", "0.1.0")
class MemoryCardsPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig) -> None:
        super().__init__(context, config)
        self.data_dir = StarTools.get_data_dir(PLUGIN_NAME)
        context.register_web_api(
            f"/{PLUGIN_NAME}/memory/users",
            self.api_users,
            ["GET"],
            "List memory users",
        )

    async def initialize(self) -> None:
        pass

    async def terminate(self) -> None:
        pass

    async def api_users(self):
        return {}

    @filter.on_llm_request()
    async def inject_memory(self, event: AstrMessageEvent, req) -> None:
        if not event.is_private_chat():
            return
        event.get_platform_id()
        event.get_sender_id()
        req.extra_user_content_parts.append(
            TextPart(text="").mark_as_temp()
        )
