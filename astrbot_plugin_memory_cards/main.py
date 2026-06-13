from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime, timedelta

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.core.agent.message import TextPart
from quart import jsonify, request

from .extraction import (
    EXTRACTION_SYSTEM_PROMPT,
    build_extraction_prompt,
    normalize_for_duplicate_check,
    parse_candidates,
)
from .injection import build_memory_context
from .retrieval import select_relevant_notes
from .scheduler import ExtractionScheduler
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
        self.scheduler = ExtractionScheduler(
            self.store,
            self.process_extraction_scope,
            message_threshold=lambda: self._config_int(
                "auto_extract_message_threshold", 20, 1, 500
            ),
            idle_minutes=lambda: self._config_int(
                "auto_extract_idle_minutes", 30, 1, 1440
            ),
        )
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
        if bool(self.config.get("auto_extract_enabled", True)):
            await self.scheduler.start()

    async def terminate(self) -> None:
        self._active = False
        await self.scheduler.stop()
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
            if bool(self.config.get("auto_extract_enabled", True)):
                message = str(event.get_message_str() or "").strip()
                if message:
                    await self.store.append_buffer_message(
                        scope_key,
                        "user",
                        message,
                    )
        except Exception:
            logger.exception("登记私聊用户失败")

    @filter.on_agent_done()
    async def buffer_final_reply(self, event, run_context, resp) -> None:
        del run_context
        if (
            not self._active
            or not bool(self.config.get("auto_extract_enabled", True))
            or not event.is_private_chat()
            or getattr(resp, "role", "") == "err"
        ):
            return
        identity = self._scope_from_event(event)
        content = str(getattr(resp, "completion_text", "") or "").strip()
        if identity is None or not content:
            return
        provider_id = ""
        try:
            provider_id = await self.context.get_current_chat_provider_id(
                event.unified_msg_origin
            )
        except Exception:
            logger.warning("无法确定当前会话模型，便签整理时将重试")
        try:
            await self.store.append_buffer_message(
                identity[0],
                "assistant",
                content,
                provider_id,
            )
        except Exception:
            logger.exception("缓冲机器人回复失败")

    async def process_extraction_scope(self, scope_key: str) -> None:
        now = datetime.now(UTC)
        batch = await self.store.claim_extraction_batch(
            scope_key,
            message_threshold=self._config_int(
                "auto_extract_message_threshold", 20, 1, 500
            ),
            idle_before=now
            - timedelta(
                minutes=self._config_int(
                    "auto_extract_idle_minutes", 30, 1, 1440
                )
            ),
            now=now,
        )
        if batch is None:
            return
        try:
            provider_id = str(
                self.config.get("auto_extract_provider_id", "") or ""
            ).strip()
            if not provider_id:
                provider_id = next(
                    (
                        message.provider_id
                        for message in reversed(batch.messages)
                        if message.provider_id
                    ),
                    "",
                )
            if not provider_id:
                raise RuntimeError("没有可用于整理便签的模型")

            existing = await self.store.list_notes_for_retrieval(scope_key)
            response = await self.context.llm_generate(
                chat_provider_id=provider_id,
                prompt=build_extraction_prompt(batch.messages, existing),
                system_prompt=EXTRACTION_SYSTEM_PROMPT,
            )
            candidates = parse_candidates(
                getattr(response, "completion_text", ""),
                max_notes=self._config_int(
                    "auto_extract_max_notes", 5, 1, 20
                ),
            )
            known = {
                normalize_for_duplicate_check(note.content) for note in existing
            }
            for candidate in candidates:
                normalized = normalize_for_duplicate_check(candidate.content)
                if candidate.action == "create":
                    if normalized in known:
                        continue
                    await self.store.create_note(
                        scope_key,
                        candidate.category,
                        candidate.content,
                        source="auto",
                        source_batch_id=batch.batch_id,
                    )
                    known.add(normalized)
                elif candidate.note_id is not None:
                    await self.store.update_note(
                        scope_key,
                        candidate.note_id,
                        candidate.category,
                        candidate.content,
                    )
            await self.store.complete_extraction_batch(scope_key, batch.batch_id)
        except Exception as exc:
            logger.exception("自动整理对话便签失败")
            await self.store.fail_extraction_batch(
                scope_key,
                batch.batch_id,
                str(exc),
                now
                + timedelta(
                    minutes=self._config_int(
                        "auto_extract_retry_minutes", 10, 1, 1440
                    )
                ),
            )

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
            notes = await self.store.list_notes_for_retrieval(identity[0])
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
        if not self._active:
            return self._error("便签存储未就绪", 503)
        try:
            users = await self.store.list_users()
        except Exception:
            logger.exception("读取便签用户失败")
            return self._error("读取用户列表失败", 500)
        return jsonify({"ok": True, "items": [asdict(user) for user in users]})

    async def api_notes(self):
        if not self._active:
            return self._error("便签存储未就绪", 503)
        scope_key = str(request.args.get("scope_key", "")).strip()
        if not scope_key:
            return self._error("缺少 scope_key", 400)
        try:
            limit = int(request.args.get("limit", 50))
            offset = int(request.args.get("offset", 0))
        except (TypeError, ValueError):
            return self._error("分页参数无效", 400)
        try:
            notes, total = await self.store.list_notes(
                scope_key,
                keyword=str(request.args.get("keyword", "")),
                category=request.args.get("category"),
                limit=limit,
                offset=offset,
            )
        except Exception:
            logger.exception("读取便签失败")
            return self._error("读取便签失败", 500)
        return jsonify(
            {
                "ok": True,
                "items": [asdict(note) for note in notes],
                "total": total,
            }
        )

    async def api_create_note(self):
        if not self._active:
            return self._error("便签存储未就绪", 503)
        payload = await self._json_payload()
        if payload is None:
            return self._error("请求内容必须是 JSON 对象", 400)
        scope_key = str(payload.get("scope_key", "")).strip()
        if not scope_key:
            return self._error("缺少 scope_key", 400)
        try:
            if not await self.store.user_exists(scope_key):
                return self._error("用户不存在", 404)
            note = await self.store.create_note(
                scope_key,
                str(payload.get("category", "其他")),
                str(payload.get("content", "")),
            )
        except ValueError as exc:
            return self._error(str(exc), 400)
        except Exception:
            logger.exception("新增便签失败")
            return self._error("新增便签失败", 500)
        return jsonify({"ok": True, "note": asdict(note)})

    async def api_update_note(self):
        if not self._active:
            return self._error("便签存储未就绪", 503)
        payload = await self._json_payload()
        if payload is None:
            return self._error("请求内容必须是 JSON 对象", 400)
        scope_key = str(payload.get("scope_key", "")).strip()
        note_id = self._parse_note_id(payload.get("id"))
        if not scope_key or note_id is None:
            return self._error("scope_key 或便签 ID 无效", 400)
        try:
            note = await self.store.update_note(
                scope_key,
                note_id,
                str(payload.get("category", "其他")),
                str(payload.get("content", "")),
            )
        except ValueError as exc:
            return self._error(str(exc), 400)
        except Exception:
            logger.exception("更新便签失败")
            return self._error("更新便签失败", 500)
        if note is None:
            return self._error("便签不存在或不属于该用户", 404)
        return jsonify({"ok": True, "note": asdict(note)})

    async def api_delete_note(self):
        if not self._active:
            return self._error("便签存储未就绪", 503)
        payload = await self._json_payload()
        if payload is None:
            return self._error("请求内容必须是 JSON 对象", 400)
        scope_key = str(payload.get("scope_key", "")).strip()
        note_id = self._parse_note_id(payload.get("id"))
        if not scope_key or note_id is None:
            return self._error("scope_key 或便签 ID 无效", 400)
        try:
            deleted = await self.store.delete_note(scope_key, note_id)
        except Exception:
            logger.exception("删除便签失败")
            return self._error("删除便签失败", 500)
        if not deleted:
            return self._error("便签不存在或不属于该用户", 404)
        return jsonify({"ok": True})

    @staticmethod
    async def _json_payload() -> dict | None:
        payload = await request.get_json(silent=True)
        return payload if isinstance(payload, dict) else None

    @staticmethod
    def _parse_note_id(value) -> int | None:
        try:
            note_id = int(value)
        except (TypeError, ValueError):
            return None
        return note_id if note_id > 0 else None

    @staticmethod
    def _error(message: str, status: int):
        return jsonify(
            {
                "ok": False,
                "status": "error",
                "code": status,
                "message": message,
            }
        )

    def _config_int(
        self,
        key: str,
        default: int,
        minimum: int,
        maximum: int,
    ) -> int:
        try:
            value = int(self.config.get(key, default))
        except (TypeError, ValueError):
            value = default
        return max(minimum, min(value, maximum))
