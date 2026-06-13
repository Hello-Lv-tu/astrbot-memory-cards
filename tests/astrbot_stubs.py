from __future__ import annotations

import sys
import types
from pathlib import Path


class FakeLogger:
    def exception(self, *args, **kwargs) -> None:
        pass

    def warning(self, *args, **kwargs) -> None:
        pass

    def info(self, *args, **kwargs) -> None:
        pass


class FakeTextPart:
    def __init__(self, text: str) -> None:
        self.text = text
        self._no_save = False

    def mark_as_temp(self):
        self._no_save = True
        return self


class FakeStar:
    def __init__(self, context, config=None) -> None:
        self.context = context
        self.config = config or {}


class FakeStarTools:
    data_dir = Path.cwd()

    @classmethod
    def get_data_dir(cls, plugin_name: str):
        del plugin_name
        cls.data_dir.mkdir(parents=True, exist_ok=True)
        return cls.data_dir


class FakeContext:
    def __init__(self) -> None:
        self.routes: list[tuple[str, object, list[str], str]] = []

    def register_web_api(self, route, handler, methods, desc) -> None:
        self.routes.append((route, handler, methods, desc))


class _Filter:
    class EventMessageType:
        PRIVATE_MESSAGE = "private"

    @staticmethod
    def on_llm_request():
        return lambda function: function

    @staticmethod
    def event_message_type(_message_type):
        return lambda function: function


def install_astrbot_stubs(data_dir: Path) -> None:
    FakeStarTools.data_dir = data_dir

    astrbot = types.ModuleType("astrbot")
    api = types.ModuleType("astrbot.api")
    event = types.ModuleType("astrbot.api.event")
    star = types.ModuleType("astrbot.api.star")
    core = types.ModuleType("astrbot.core")
    agent = types.ModuleType("astrbot.core.agent")
    message = types.ModuleType("astrbot.core.agent.message")

    api.AstrBotConfig = dict
    api.logger = FakeLogger()
    event.AstrMessageEvent = object
    event.filter = _Filter
    star.Context = FakeContext
    star.Star = FakeStar
    star.StarTools = FakeStarTools
    star.register = lambda *args, **kwargs: (lambda cls: cls)
    message.TextPart = FakeTextPart

    sys.modules.update(
        {
            "astrbot": astrbot,
            "astrbot.api": api,
            "astrbot.api.event": event,
            "astrbot.api.star": star,
            "astrbot.core": core,
            "astrbot.core.agent": agent,
            "astrbot.core.agent.message": message,
        }
    )


def unload_plugin_main() -> None:
    sys.modules.pop("astrbot_plugin_memory_cards.main", None)
