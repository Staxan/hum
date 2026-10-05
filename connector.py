"""Подключение выбранной модели к профилю Hermes.

Что делает «Подключить»:
  1. Находит профиль агента (каталог ``~/.hermes/profiles/<name>`` + дефолтный ``~/.hermes``).
  2. Пишет ``model.default`` в его ``config.yaml`` через ``atomic_yaml_write`` — с бэкапом
     файла и без риска оставить обрезанный YAML.
  3. Сбрасывает ``model_override`` в ``sessions.json``: сессионный override имеет приоритет
     над конфигом, поэтому старый ручной ``/model`` иначе победил бы свежее значение.
  4. Перезапускает gateway этого профиля.

Модель пишется прямо в ``model.default``, а не через ``${VAR}``: gateway читает конфиг
через ``read_raw_config()``, который НЕ раскрывает переменные окружения — плейсхолдер
остался бы строкой. Это проверено живьём, см. README.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Профили, которые всегда доступны как агенты, плюс всё, что реально лежит на диске.
DEFAULT_AGENT_LABELS = {
    "default": "Гера",
    "hefest": "Гефест",
    "mira": "Мира",
    "nika-redaktor": "Ника",
    "tim": "Тим",
}


def hermes_home() -> Path:
    """Корень Hermes текущего процесса."""
    return Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))


def profiles_root() -> Path:
    """Каталог профилей (внутри дефолтного Hermes — profiles/)."""
    home = hermes_home()
    # В мультиплекс-режиме HERMES_HOME уже указывает на профиль; ищем общий корень.
    if home.parent.name == "profiles":
        return home.parent
    return home / "profiles"


def list_profiles() -> List[dict]:
    """Все профили: имя, текущая модель, запущен ли gateway."""
    root = profiles_root()
    found: List[dict] = []

    # Дефолтный профиль — каталог Hermes. Если HERMES_HOME уже указывает на профиль,
    # корень профилей — его родитель, а дефолтный профиль лежит уровнем выше.
    default_home = root.parent if root.name == "profiles" else root
    candidates = [("", default_home)]
    if root.is_dir():
        candidates += [(p.name, p) for p in sorted(root.iterdir()) if p.is_dir()]

    seen = set()
    for name, home in candidates:
        cfg = home / "config.yaml"
        if not cfg.exists() or home in seen:
            continue
        seen.add(home)
        model, provider = read_current_model(cfg)
        found.append({
            "id": name or "default",
            "label": DEFAULT_AGENT_LABELS.get(name or "default", name or "Гера"),
            "home": str(home),
            "model": model,
            "provider": provider,
            "config_path": str(cfg),
        })
    return found


def read_current_model(config_path: Path) -> Tuple[str, str]:
    """Текущие (model.default, provider) из config.yaml. Безопасный разбор: при битом YAML — пусто."""
    try:
        import yaml
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return "", ""
    model_cfg = data.get("model")
    if isinstance(model_cfg, dict):
        return str(model_cfg.get("default") or ""), str(model_cfg.get("provider") or "")
    if isinstance(model_cfg, str):
        return model_cfg, ""
    return "", ""


def _backup_config(config_path: Path) -> Optional[Path]:
    """Скопировать config.yaml в .backup — за один шаг от отката."""
    try:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup = config_path.with_suffix(f".yaml.hum-backup-{stamp}")
        shutil.copy2(config_path, backup)
        return backup
    except Exception:
        return None


def apply_model(profile_id: str, model_id: str) -> Tuple[bool, Dict[str, Any]]:
    """Записать модель в config.yaml профиля. (ok, детали/ошибка)."""
    profiles = {p["id"]: p for p in list_profiles()}
    profile = profiles.get(profile_id)
    if not profile:
        return False, {"error": f"Профиль «{profile_id}» не найден"}
    if not model_id or ":" not in model_id and "/" not in model_id:
        return False, {"error": "Некорректное имя модели"}

    config_path = Path(profile["config_path"])

    # Родной писатель Hermes: атомарная запись + бэкап, не ломает YAML при обрыве.
    # Импорт внутри функции: вне окружения Hermes этих модулей просто нет.
    atomic_yaml_write = None
    cfg = None
    try:
        from hermes_cli.config import atomic_yaml_write as _write, load_config as _load
        atomic_yaml_write = _write
        cfg = _load()
    except Exception:
        cfg = None

    backup = _backup_config(config_path)

    try:
        if cfg is not None and atomic_yaml_write is not None:
            model_cfg = cfg.get("model")
            if not isinstance(model_cfg, dict):
                model_cfg = {}
                cfg["model"] = model_cfg
            model_cfg["default"] = model_id
            atomic_yaml_write(config_path, cfg)
        else:
            # Запасной путь без внутренних API (если плагин запущен вне Hermes).
            import yaml
            try:
                data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            except Exception:
                data = {}
            if not isinstance(data.get("model"), dict):
                data["model"] = {}
            data["model"]["default"] = model_id
            config_path.write_text(
                yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    except Exception as exc:
        # Откат: вернуть исходный файл, если запись не удалась.
        if backup and backup.exists():
            try:
                shutil.copy2(backup, config_path)
            except Exception:
                pass
        return False, {"error": f"Не удалось записать config.yaml: {exc}"}

    written, _ = read_current_model(config_path)
    if written != model_id:
        return False, {"error": f"Запись не подтверждена: в файле «{written}»"}

    cleared = clear_session_override(profile["home"])

    return True, {
        "profile": profile["id"],
        "label": profile["label"],
        "model": model_id,
        "config_path": str(config_path),
        "backup": str(backup) if backup else None,
        "session_override_cleared": cleared,
    }


def clear_session_override(profile_home: str) -> bool:
    """Сбросить model_override в sessions.json — иначе старый /model перебьёт конфиг."""
    sessions_file = Path(profile_home) / "sessions" / "sessions.json"
    if not sessions_file.exists():
        return False
    try:
        raw = json.loads(sessions_file.read_text(encoding="utf-8"))
    except Exception:
        return False
    if isinstance(raw, dict) and "sessions" in raw:
        raw = raw["sessions"]
    if not isinstance(raw, dict):
        return False
    changed = False
    for entry in raw.values():
        if isinstance(entry, dict) and entry.get("model_override"):
            entry["model_override"] = None
            changed = True
    if changed:
        try:
            sessions_file.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            return False
    return changed


def restart_profile(profile_id: str, wait_s: int = 90) -> Tuple[bool, str]:
    """Перезапустить gateway профиля. Профиль без запущенного gateway — сообщение, не ошибка.

    Важно: ``hermes gateway restart`` — процесс, который после рестарта ОСТАЁТСЯ ЖИТЬ
    сам и становится gateway. Ждать его завершения нельзя: он не завершится, пока
    работает. Поэтому запускаем его отдельной сессией и ждём готовности по процессу.
    """
    cmd = ["hermes", "gateway", "restart"]
    if profile_id != "default":
        cmd += ["--profile", profile_id]

    logs_dir = Path(hermes_home()) / "logs"
    try:
        logs_dir.mkdir(parents=True, exist_ok=True)
        log = open(logs_dir / "hum-restart.log", "a", encoding="utf-8")
    except Exception:
        log = subprocess.DEVNULL  # type: ignore[assignment]

    try:
        proc = subprocess.Popen(
            cmd, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            start_new_session=True,  # отделяемся от процесса-группы веб-сервиса
        )
    except FileNotFoundError:
        return False, "Команда hermes не найдена в PATH"
    except Exception as exc:
        return False, f"Не удалось запустить рестарт: {exc}"

    # Ждём не завершения процесса (он не завершится), а появления gateway.
    deadline = time.time() + wait_s
    while time.time() < deadline:
        time.sleep(2)
        if proc.poll() is not None:
            return False, f"Рестарт завершился с кодом {proc.returncode}, см. logs/hum-restart.log"
        if gateway_running(profile_id):
            return True, "gateway запущен"
    return False, f"Gateway не поднялся за {wait_s} с, см. logs/hum-restart.log"


def gateway_running(profile_id: str) -> Optional[bool]:
    """Запущен ли gateway профиля. None — определить не удалось.

    Важно: pgrep по строке ``--profile <id>`` находит и нашу собственную команду,
    поэтому процессы с текущим pid и его родителем отбрасываются — иначе профиль
    всегда считался бы запущенным.
    """
    try:
        out = subprocess.run(["pgrep", "-af", "hermes"], capture_output=True,
                             text=True, timeout=10).stdout
    except Exception:
        return None
    me = {os.getpid(), os.getppid()}
    needle = f"--profile {profile_id}" if profile_id != "default" else "hermes gateway"
    for line in out.splitlines():
        pid_str, _, cmd = line.partition(" ")
        if not cmd.startswith("hermes") and "hermes" not in cmd.split(" ")[0]:
            continue
        if "gateway" not in cmd:
            continue
        if not pid_str.isdigit() or int(pid_str) in me:
            continue
        if needle in cmd:
            return True
    return False