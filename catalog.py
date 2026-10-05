"""Сбор данных каталога моделей UnoRouter.

Два источника, как на сайте:
  * ``api.unorouter.com/v1/models`` — полный список id + провайдер (нужен ключ).
  * встроенный JSON страницы unorouter.com — цены, контекст, скидки, успешность,
    задержка, дата выхода, теги. Публичного API с ценами у UnoRouter нет.

Каждая функция возвращает ``(ok, payload_or_error)`` и никогда не бросает исключений
наружу: вызывающий код покажет пользователю текст ошибки, а не traceback.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any, Dict, List, Tuple

API_BASE = "https://api.unorouter.com/v1"
PAGE_URL = "https://unorouter.com/ru/%D0%BC%D0%BE%D0%B4%D0%B5%D0%BB%D0%B8?modality=text"

# Браузерная маскировка: сайт отдаёт полный набор данных обычным GET, но с User-Agent
# браузера — иначе отдаётся урезанная страница без встроенного JSON.
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

_TIMEOUT = 30


def _get(url: str, headers: Dict[str, str] | None = None) -> Tuple[bool, Any]:
    """GET с таймаутом. Возвращает (ok, текст) либо (False, сообщение об ошибке)."""
    req = urllib.request.Request(url, headers=headers or {"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code} от {url}"
    except Exception as exc:  # сеть, таймаут, SSL
        return False, f"Сеть недоступна: {exc}"
    return True, raw.decode("utf-8", errors="replace")


def fetch_models(api_key: str) -> Tuple[bool, Any]:
    """Список id всех моделей из UnoRouter API."""
    if not api_key:
        return False, "Не задан UNOROUTER_API_KEY"
    ok, body = _get(f"{API_BASE}/models", {"Authorization": f"Bearer {api_key}", "User-Agent": _UA})
    if not ok:
        return False, body
    try:
        data = json.loads(body)
    except Exception:
        return False, "Не удалось разобрать ответ /v1/models"
    rows = data.get("data", []) if isinstance(data, dict) else []
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        out.append({
            "id": str(row.get("id", "")),
            "vendor": str(row.get("owned_by", "") or ""),
            "endpoints": row.get("supported_endpoint_types") or [],
        })
    return True, out


def _decode_next_payloads(page_html: str) -> str:
    """Склеить содержимое всех self.__next_f.push([1, "..."]) в один текст."""
    chunks = re.findall(r'self\.__next_f\.push\(\[1,\s*(".*?")\]\)', page_html, re.S)
    parts = []
    for chunk in chunks:
        try:
            parts.append(json.loads(chunk))
        except Exception:
            continue
    return "".join(parts)


def _extract_models_array(text: str) -> List[dict] | None:
    """Достать массив моделей из декодированного RSC-потока страницы."""
    key = '"models":['
    start = text.find(key)
    if start < 0:
        return None
    open_idx = start + len(key) - 1
    depth = 0
    in_str = False
    escaped = False
    for i in range(open_idx, len(text)):
        ch = text[i]
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth == 0:
                    try:
                        parsed = json.loads(text[open_idx:i + 1])
                    except Exception:
                        return None
                    return parsed if isinstance(parsed, list) else None
    return None


def _flatten_model(raw: dict) -> dict:
    """Привести запись сайта к плоскому виду интерфейса."""
    meta = raw.get("metadata") or {}
    if not isinstance(meta, dict):
        meta = {}
    return {
        "id": str(raw.get("model_name", "")),
        "vendor": str(raw.get("vendor", "") or ""),
        "type": str(raw.get("type", "") or "text"),
        "is_free": bool(raw.get("is_free")),
        "online": bool(raw.get("online")),
        "chat": bool(raw.get("chat")),
        "input_price": raw.get("input_price"),
        "output_price": raw.get("output_price"),
        "original_input_price": raw.get("original_input_price"),
        "original_output_price": raw.get("original_output_price"),
        "is_fixed_price": bool(raw.get("is_fixed_price")),
        "context_window": meta.get("contextWindow"),
        "max_input_tokens": meta.get("maxInputTokens"),
        "max_output_tokens": meta.get("maxOutputTokens"),
        "input_modalities": meta.get("inputModalities") or [],
        "output_modalities": meta.get("outputModalities") or [],
        "series": meta.get("series", "") or "",
        "categories": meta.get("categories") or [],
        "supports_tools": bool(meta.get("supportsTools")),
        "supports_vision": bool(meta.get("supportsVision")),
        "supports_cache": bool(meta.get("supportsCache")),
        "is_reasoning": bool(meta.get("isReasoning")),
        "tags": raw.get("tags") or [],
        "supported_parameters": meta.get("supportedParametersAll") or [],
        "release_ts": raw.get("release_ts"),
        "uptime_24h": raw.get("uptime_24h"),
        "success_rate": raw.get("success_rate"),
        "avg_latency_ms": raw.get("avg_latency_ms"),
        "description": raw.get("description", "") or "",
    }


def fetch_page_metadata() -> Tuple[bool, Any]:
    """Цены/контекст/успешность из встроенного JSON страницы UnoRouter."""
    ok, body = _get(PAGE_URL)
    if not ok:
        return False, body
    text = _decode_next_payloads(body)
    if not text:
        # Запасной путь: если разметка поменяется и поток не распарсится,
        # пробуем грубый поиск по сырой странице.
        arr = _extract_models_array(body.replace('\\"', '"'))
        if not arr:
            return False, "Не удалось прочитать данные моделей со страницы UnoRouter"
        raw_models = arr
    else:
        raw_models = _extract_models_array(text)
    if not raw_models:
        return False, "Не удалось прочитать данные моделей со страницы UnoRouter"
    out = []
    for raw in raw_models:
        if isinstance(raw, dict) and raw.get("model_name"):
            out.append(_flatten_model(raw))
    return True, out


def merge_catalog(api_key: str) -> Tuple[bool, Any]:
    """Единый каталог: id из API + метаданные со страницы.

    Возвращает ``{"models": [...], "sources": {...}}``. Метаданные — основной источник,
    список из API дополняет его (модели без страницы остаются с пустыми ценой).
    """
    meta_ok, meta = fetch_page_metadata()
    api_rows: List[dict] = []
    api_ok = False
    if api_key:
        api_ok, api_rows = fetch_models(api_key)  # type: ignore[assignment]
    else:
        api_rows = []

    by_id: Dict[str, dict] = {}
    if meta_ok:
        for row in meta:
            by_id[row["id"]] = row
    if api_ok:
        for row in api_rows:
            existing = by_id.get(row["id"])
            if existing is None:
                by_id[row["id"]] = _flatten_model({
                    "model_name": row["id"],
                    "vendor": row["vendor"],
                    "type": "text",
                    "is_free": row["id"].endswith(":free"),
                })
            elif row["vendor"] and not existing.get("vendor"):
                existing["vendor"] = row["vendor"]

    models = list(by_id.values())
    # Названия сайта — ключ интерфейса; сортируем так же, как сайт: бесплатные первыми нет,
    # порядок — по вендору, затем по имени, чтобы список был стабильным между обновлениями.
    models.sort(key=lambda m: (m["vendor"].lower(), m["id"].lower()))

    errors = []
    if not meta_ok:
        errors.append(f"Метаданные: {meta}")
    if not api_ok:
        errors.append(f"Список API: {api_rows}")

    return True, {
        "models": models,
        "sources": {
            "page_metadata": bool(meta_ok),
            "api_models": bool(api_ok),
            "errors": errors,
        },
    }