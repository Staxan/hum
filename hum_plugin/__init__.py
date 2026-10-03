"""Плагин hum — веб-каталог моделей UnoRouter с подключением к профилям Hermes.

При загрузке (старт gateway) поднимает HTTP-сервис на 127.0.0.1:8647 и регистрирует
инструменты, которыми агент может пользоваться: посмотреть каталог, подключить модель,
узнать текущее состояние.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List

_SERVER = None


def _ensure_server():
    """Поднять сервер один раз за процесс; повторные вызовы — no-op."""
    global _SERVER
    if _SERVER is not None:
        return _SERVER
    from .server import start
    try:
        _SERVER = start()
    except OSError as exc:
        # Порт занят — это нормально: сервис уже поднят другим экземпляром.
        print(f"[hum] веб-сервис не поднят: {exc}")
        return None
    from .server import PORT
    print(f"[hum] веб-интерфейс: http://127.0.0.1:{PORT}/")
    return _SERVER


def register(ctx: Any) -> None:
    """Точка входа плагина: сервер + инструменты агента."""
    _ensure_server()
    _register_tools(ctx)


def _register_tools(ctx: Any) -> None:
    register_tool = getattr(ctx, "register_tool", None)
    if not callable(register_tool):
        return

    def hum_models(query: str = "", free_only: bool = False, limit: int = 40) -> str:
        """Каталог моделей UnoRouter: цены, контекст, бесплатные.

        Аргументы: query — поиск по имени/вендору, free_only — только бесплатные,
        limit — сколько вернуть.
        """
        from .server import get_catalog
        models = get_catalog()["models"]
        if free_only:
            models = [m for m in models if m.get("is_free")]
        if query:
            q = query.lower()
            models = [m for m in models if q in m["id"].lower() or q in m.get("vendor", "").lower()]
        rows = [{
            "id": m["id"], "vendor": m.get("vendor"), "free": m.get("is_free"),
            "in": m.get("input_price"), "out": m.get("output_price"),
            "context": m.get("context_window"), "online": m.get("online"),
        } for m in models[:max(1, limit)]]
        return json.dumps({"count": len(models), "models": rows}, ensure_ascii=False)

    def hum_connect(model: str, profile: str = "hefest", restart: bool = True) -> str:
        """Подключить модель к профилю агента: пишет config.yaml и перезапускает gateway."""
        from .connector import apply_model, restart_profile
        ok, result = apply_model(profile, model)
        if not ok:
            return json.dumps({"ok": False, **result}, ensure_ascii=False)
        if restart:
            r_ok, msg = restart_profile(profile)
            result["restarted"] = r_ok
            result["restart_message"] = msg
        result["ok"] = True
        return json.dumps(result, ensure_ascii=False)

    def hum_status() -> str:
        """Текущие агенты, их модели и состояние gateway."""
        from .connector import gateway_running, list_profiles
        agents: List[Dict[str, Any]] = []
        for p in list_profiles():
            agents.append({**p, "running": gateway_running(p["id"])})
        return json.dumps({"agents": agents}, ensure_ascii=False)

    for name, fn, desc, schema in (
        ("hum_models", hum_models,
         "Каталог моделей UnoRouter с ценами и контекстом.",
         {"query": {"type": "string", "description": "Поиск по имени или вендору"},
          "free_only": {"type": "boolean", "description": "Только бесплатные"},
          "limit": {"type": "integer", "description": "Сколько моделей вернуть"}}),
        ("hum_connect", hum_connect,
         "Подключить модель к профилю агента Hermes.",
         {"model": {"type": "string", "description": "id модели, например gpt-4o-mini:free"},
          "profile": {"type": "string", "description": "id профиля: hefest, mira, tim, nika-redaktor, default"},
          "restart": {"type": "boolean", "description": "Перезапустить gateway (по умолчанию да)"}}),
        ("hum_status", hum_status, "Модели и состояние gateway всех профилей агентов.", {}),
    ):
        try:
            register_tool(
                name=name,
                toolset="hum",
                schema={"name": name, "description": desc, "parameters": schema},
                handler=lambda args, _fn=fn: json.dumps(_fn(**(args or {})), ensure_ascii=False),
                check_fn=lambda: True,
            )
        except Exception as exc:
            print(f"[hum] инструмент {name} не зарегистрирован: {exc}")