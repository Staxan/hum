"""Живая проверка модели одним запросом к UnoRouter.

Проверяются только бесплатные модели — платные не трогаем, чтобы не расходовать баланс.
Текст запроса берётся случайный из пула: одинаковый «привет» у всех моделей выглядит
роботизированно и нагрузочно для шлюза. Ответ урезается по max_tokens.
"""

from __future__ import annotations

import json
import random
import urllib.error
import urllib.request
from typing import Any, Dict, Tuple

API_BASE = "https://api.unorouter.com/v1"

# Вопросы общего характера: не про код, не про конкретный стек, безопасны для любой модели.
PROBE_PROMPTS = [
    "Какая погода сегодня в Москве? Ответь кратко.",
    "Какая погода сегодня в Берлине? Ответь одним-двумя предложениями.",
    "Сколько стоит биткоин и почему он так дорог? Коротко.",
    "Сколько стоит эфириум сегодня? Ответь кратко.",
    "Назови три причины, почему люди изучают языки.",
    "Как приготовить борщ по-быстрому? Три шага.",
    "Что такое эффект Даннинга — Крюгера? Простыми словами.",
    "Почему небо голубое? Объясни коротко.",
]

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


def probe_model(api_key: str, model_id: str, timeout: int = 45) -> Tuple[bool, Dict[str, Any]]:
    """Один проверочный запрос. Возвращает (ok, результат) с текстом ответа или ошибкой."""
    if not api_key:
        return False, {"error": "Не задан UNOROUTER_API_KEY"}
    prompt = random.choice(PROBE_PROMPTS)
    payload = json.dumps({
        "model": model_id,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 120,
        "stream": False,
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{API_BASE}/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": _UA,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = json.loads(exc.read().decode("utf-8", errors="replace")).get("error", {}).get("message", "")
        except Exception:
            pass
        return False, {"error": f"HTTP {exc.code}: {detail or 'запрос отклонён'}", "prompt": prompt}
    except Exception as exc:
        return False, {"error": f"Сеть недоступна: {exc}", "prompt": prompt}

    try:
        data = json.loads(raw)
        text = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
    except Exception:
        return False, {"error": "Не удалось разобрать ответ модели", "prompt": prompt}

    text = str(text).strip()
    if not text:
        return False, {"error": "Модель вернула пустой ответ", "prompt": prompt}
    return True, {
        "ok": True,
        "model": model_id,
        "prompt": prompt,
        "reply": text[:400],
        "reply_chars": len(text),
    }