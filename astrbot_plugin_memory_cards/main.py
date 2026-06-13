from __future__ import annotations

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.core.agent.message import TextPart

from .injection import build_memory_context
from .retrieval import select_relevant_notes
from .store import MemoryStore

PLUGIN_NAME = "astrbot_plugin_memory_cards"
SCOPE_SEPARATOR = "\x1f"


@register(PLUGIN_NAME, "Lv_Tu", "私聊长期记忆卡片", "0.1.0")
class MemoryCardsPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig) -> None:
        super().__init__(context, config)
        self.config = config
        self.data_dir = StarTools.get_data_dir(PLUGIN_NAME)
        self.store = MemoryStore(self.data_dir / "memory.db")
        self._active = False
        self._register_web_apis(context)

    def _register_web_apis(self, context: Context) -> None:
        routes = (
            ("memory/users", self.api_users, ["GET"], "List memory users"),
            ("memory/notes", self.api_notes, ["GET"], "List memory notes"),
            (
                "memory/notes/create",
                self.api_create_note,
                ["POST"],
                "Create memory note",
            ),
            (
                "memory/notes/update",
                self.api_update_note,
                ["POST"],
                "Update memory note",
            ),
            (
                "memory/notes/delete",
                self.api_delete_note,
                ["POST"],
                "Delete memory note",
            ),
        )
        for path, handler, methods, description in routes:
            context.register_web_api(
                f"/{PLUGIN_NAME}/{path}",
                handler,
                methods,
                description,
            )

    async def initialize(self) -> None:
        try:
            await self.store.open()
        except Exception:
            logger.exception("对话便签数据库初始化失败")
            self._active = False
            return
        self._active = True

    async def terminate(self) -> None:
        self._active = False
        close = getattr(self.store, "close", None)
        if close is None:
            return
        try:
            await close()
        except Exception:
            logger.exception("关闭对话便签数据库失败")

    @staticmethod
    def _scope_from_event(
        event: AstrMessageEvent,
    ) -> tuple[str, str, str] | None:
        platform_id = str(event.get_platform_id() or "").strip()
        user_id = str(event.get_sender_id() or "").strip()
        if not platform_id or not user_id:
            return None
        return (
            f"{platform_id}{SCOPE_SEPARATOR}{user_id}",
            platform_id,
            user_id,
        )

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    async def observe_private_user(self, event: AstrMessageEvent) -> None:
        if not self._active or not event.is_private_chat():
            return
        identity = self._scope_from_event(event)
        if identity is None:
            return
        scope_key, platform_id, user_id = identity
        try:
            await self.store.upsert_user(
                scope_key,
                platform_id,
                user_id,
                event.get_sender_name(),
            )
        except Exception:
            logger.exception("登记私聊用户失败")

    @filter.on_llm_request()
    async def inject_memory(self, event: AstrMessageEvent, req) -> None:
        if (
            not self._active
            or not bool(self.config.get("enabled", True))
            or not event.is_private_chat()
        ):
            return
        identity = self._scope_from_event(event)
        if identity is None:
            return
        query = str(
            getattr(req, "prompt", "") or event.get_message_str() or ""
        ).strip()
        if not query:
            return

        try:
            notes, _ = await self.store.list_notes(
                identity[0],
                limit=100,
            )
            selected = select_relevant_notes(
                query,
                notes,
                minimum_score=float(self.config.get("minimum_score", 3.0)),
                max_notes=max(
                    1,
                    min(int(self.config.get("max_injected_notes", 5)), 10),
                ),
                max_chars=max(
                    200,
                    int(self.config.get("max_injected_chars", 1500)),
                ),
                recall_fallback_enabled=bool(
                    self.config.get("recall_fallback_enabled", True)
                ),
            )
            memory_context = build_memory_context(
                selected,
                max_chars=max(
                    200,
                    int(self.config.get("max_injected_chars", 1500)),
                ),
            )
        except Exception:
            logger.exception("检索对话便签失败")
            return

        if memory_context:
            req.extra_user_content_parts.append(
                TextPart(text=memory_context).mark_as_temp()
            )

    async def api_users(self):
        return {"ok": False, "message": "尚未实现"}

    async def api_notes(self):
        return {"ok": False, "message": "尚未实现"}

    async def api_create_note(self):
        return {"ok": False, "message": "尚未实现"}

    async def api_update_note(self):
        return {"ok": False, "message": "尚未实现"}

    async def api_delete_note(self):
        return {"ok": False, "message": "尚未实现"}
