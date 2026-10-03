"""HTTP-сервер плагина hum: JSON API + статика веб-интерфейса.

Только стандартная библиотека — чтобы установка была одной командой без pip-зависимостей.
Слушает 127.0.0.1:8780.
"""

from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse

from . import catalog, connector, favorites
from .model_check import probe_model

HOST = "127.0.0.1"
# Порт по умолчанию — 8780. НЕ используем диапазон 8644-8648: его целиком занимают
# служебные порты Hermes (webhook, line, gateway-профили), и плагин получил бы чужой сокет.
PORT = int(os.environ.get("HUM_PORT", "8780"))

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

# Кэш каталога: цены у UnoRouter обновляются редко, а страница тяжёлая.
_CATALOG_CACHE: Dict[str, Any] = {"at": 0.0, "data": None}
_CATALOG_TTL = 300.0
_CATALOG_LOCK = threading.Lock()


def api_key() -> str:
    """Ключ UnoRouter: сперва переменная профиля, затем его config.yaml."""
    key = os.environ.get("UNOROUTER_API_KEY", "").strip()
    if key:
        return key
    for profile in connector.list_profiles():
        cfg = Path(profile["config_path"])
        try:
            import yaml
            data = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
            model_cfg = data.get("model") or {}
            if isinstance(model_cfg, dict):
                value = str(model_cfg.get("api_key") or "").strip()
                if value and not value.startswith("«"):
                    return value
        except Exception:
            continue
    return ""


def get_catalog(force: bool = False) -> Dict[str, Any]:
    """Каталог из кэша либо свежая загрузка."""
    with _CATALOG_LOCK:
        now = time.time()
        if not force and _CATALOG_CACHE["data"] and now - _CATALOG_CACHE["at"] < _CATALOG_TTL:
            return _CATALOG_CACHE["data"]
        ok, data = catalog.merge_catalog(api_key())
        if not ok:
            # Отдаём прошлый каталог, если он есть: интерфейс не должен пустеть.
            if _CATALOG_CACHE["data"]:
                stale = dict(_CATALOG_CACHE["data"])
                stale["stale"] = True
                return stale
            return {"models": [], "sources": {"page_metadata": False, "api_models": False,
                                              "errors": [str(data)]}}
        _CATALOG_CACHE["at"] = now
        _CATALOG_CACHE["data"] = data
        return data


class Handler(BaseHTTPRequestHandler):
    server_version = "hum/0.1"

    def log_message(self, format: str, *args: Any) -> None:  # тише в логах gateway
        pass

    # ── ответы ────────────────────────────────────────────────────────────
    def _send_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, name: str) -> None:
        path = (WEB_DIR / name).resolve()
        if not str(path).startswith(str(WEB_DIR.resolve())) or not path.is_file():
            self.send_error(404)
            return
        ctype = "text/html; charset=utf-8" if path.suffix == ".html" else "application/octet-stream"
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ── маршруты ──────────────────────────────────────────────────────────
    def do_GET(self) -> None:  # noqa: N802 - имя задано базовым классом
        parsed = urlparse(self.path)
        route = parsed.path
        query = parse_qs(parsed.query)

        if route in ("/", "/index.html"):
            return self._send_file("index.html")
        if route in ("/style.css", "/app.js"):
            return self._send_file(route.lstrip("/"))
        if route == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return
        if route == "/api/catalog":
            data = get_catalog(force=query.get("refresh", ["0"])[0] == "1")
            favs = set(favorites.list_favorites())
            models = data.get("models", [])
            # Избранное помечаем на сервере — фронт просто сортирует строки.
            for m in models:
                m["favorite"] = m["id"] in favs
            return self._send_json({
                "models": models,
                "sources": data.get("sources", {}),
                "stale": data.get("stale", False),
                "favorites": sorted(favs),
            })
        if route == "/api/agents":
            agents = connector.list_profiles()
            for a in agents:
                a["running"] = connector.gateway_running(a["id"])
            return self._send_json({"agents": agents})
        if route == "/api/health":
            return self._send_json({"ok": True, "port": PORT, "has_key": bool(api_key())})
        self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        route = parsed.path
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}") if length else {}
        except Exception:
            return self._send_json({"ok": False, "error": "Некорректный JSON"}, 400)

        if route == "/api/connect":
            model_id = str(body.get("model") or "").strip()
            profile_id = str(body.get("profile") or "").strip()
            restart = bool(body.get("restart", True))
            ok, result = connector.apply_model(profile_id, model_id)
            if not ok:
                return self._send_json({"ok": False, **result}, 400)
            restart_msg = ""
            if restart:
                r_ok, restart_msg = connector.restart_profile(profile_id)
                result["restarted"] = r_ok
                result["restart_message"] = restart_msg
            return self._send_json({"ok": True, **result})

        if route == "/api/check":
            model_id = str(body.get("model") or "").strip()
            ok, result = probe_model(api_key(), model_id)
            return self._send_json(result, 200 if ok else 502)

        if route == "/api/favorite":
            model_id = str(body.get("model") or "").strip()
            state = favorites.toggle(model_id)
            return self._send_json({"ok": True, "model": model_id, "favorite": state})

        self.send_error(404)


def start(port: Optional[int] = None) -> ThreadingHTTPServer:
    """Поднять сервер в фоне. Возвращает объект — для остановки и проверки порта."""
    global PORT
    if port:
        PORT = port
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd