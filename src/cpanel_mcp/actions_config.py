"""Carga y validación de allowed_actions.json."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import ConfigError

VALID_LEVELS = {"lectura", "escritura_reversible", "alto_impacto"}


@dataclass(frozen=True)
class ActionSpec:
    name: str
    level: str
    enabled: bool
    description: str = ""


def _require_str(item: dict[str, Any], key: str, index: int) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(
            f"La acción #{index + 1} de allowed_actions.json necesita "
            f"un '{key}' de texto no vacío."
        )
    return value.strip()


def load_actions(path: Path) -> list[ActionSpec]:
    """Lee el archivo de acciones y falla cerrado ante cualquier anomalía."""

    path = Path(path)
    if not path.exists():
        raise ConfigError(f"No existe el archivo de acciones permitidas: {path}")

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(
            f"No se pudo leer allowed_actions.json ({path}): {exc}"
        ) from exc

    if not isinstance(payload, dict):
        raise ConfigError("allowed_actions.json debe contener un objeto JSON.")
    raw_actions = payload.get("actions")
    if not isinstance(raw_actions, list) or not raw_actions:
        raise ConfigError(
            "allowed_actions.json debe contener una lista 'actions' no vacía."
        )

    seen: set[str] = set()
    actions: list[ActionSpec] = []
    for index, item in enumerate(raw_actions):
        if not isinstance(item, dict):
            raise ConfigError(
                f"La acción #{index + 1} de allowed_actions.json no es un objeto."
            )

        name = _require_str(item, "name", index)
        level = _require_str(item, "level", index)
        if level not in VALID_LEVELS:
            raise ConfigError(
                f"La acción '{name}' tiene un nivel inválido: {level!r}. "
                f"Usa uno de: {', '.join(sorted(VALID_LEVELS))}."
            )
        if "enabled" not in item or type(item["enabled"]) is not bool:
            raise ConfigError(
                f"La acción '{name}' necesita 'enabled' booleano (true o false)."
            )
        if name in seen:
            raise ConfigError(f"La acción '{name}' está duplicada.")
        seen.add(name)

        description = item.get("description", "")
        if description is not None and not isinstance(description, str):
            raise ConfigError(
                f"La descripción de la acción '{name}' debe ser texto."
            )

        actions.append(
            ActionSpec(
                name=name,
                level=level,
                enabled=item["enabled"],
                description=(description or "").strip(),
            )
        )

    return actions
