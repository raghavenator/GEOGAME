"""Declarative per-game settings schemas.

A game declares a tuple of settings fields. The same schema is used to
(1) render the settings modal generically, (2) validate what the client
sends when a round starts, and (3) build a stable key for high scores.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Union


@dataclass(frozen=True)
class IntSetting:
    key: str
    label: str
    default: int
    min: int
    max: int
    step: int = 1
    help: str = ""
    unit: str = ""
    # Optional cap from a ChoiceSetting's ``counts``: the value can't exceed the count of the
    # choice picked in setting ``cap_by`` (e.g. questions <= countries in the region).
    # ``cap_note`` is shown and used as the error: "{choice}" = its label, "{cap}" = the count.
    cap_by: str = ""
    cap_note: str = ""

    def validate(self, value: Any) -> int:
        if isinstance(value, bool):
            raise ValueError(f"{self.label} must be a whole number.")
        if isinstance(value, str) and value.strip().lstrip("-").isdigit():
            value = int(value.strip())
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        if not isinstance(value, int):
            raise ValueError(f"{self.label} must be a whole number.")
        if not self.min <= value <= self.max:
            raise ValueError(f"{self.label} must be between {self.min} and {self.max}.")
        return value

    def to_json(self) -> dict[str, Any]:
        out = {"type": "int", "key": self.key, "label": self.label, "default": self.default,
               "min": self.min, "max": self.max, "step": self.step, "help": self.help, "unit": self.unit}
        if self.cap_by:
            out.update(cap_by=self.cap_by, cap_note=self.cap_note)
        return out


@dataclass(frozen=True)
class ChoiceSetting:
    key: str
    label: str
    default: str
    choices: tuple[tuple[str, str], ...]  # (value, label)
    help: str = ""
    # Optional: show the choices under headings, ((heading, (value, ...)), ...), and a count
    # next to each label ((value, n), ...), which an IntSetting's ``cap_by`` can also use.
    groups: tuple[tuple[str, tuple[str, ...]], ...] = ()
    counts: tuple[tuple[str, int], ...] = ()

    def label_for(self, value: str) -> str:
        return dict(self.choices).get(value, value)

    def validate(self, value: Any) -> str:
        allowed = {v for v, _ in self.choices}
        if not isinstance(value, str) or value not in allowed:
            raise ValueError(f"{self.label} must be one of: {', '.join(sorted(allowed))}.")
        return value

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"type": "choice", "key": self.key, "label": self.label, "default": self.default,
                               "choices": [{"value": v, "label": lbl} for v, lbl in self.choices], "help": self.help}
        if self.counts:
            counts = dict(self.counts)
            for c in out["choices"]:
                if c["value"] in counts:
                    c["count"] = counts[c["value"]]
        if self.groups:
            out["groups"] = [{"label": heading, "values": list(values)} for heading, values in self.groups]
        return out


Setting = Union[IntSetting, ChoiceSetting]
SettingsSchema = tuple[Setting, ...]


class SettingsError(ValueError):
    def __init__(self, errors: dict[str, str]) -> None:
        super().__init__("; ".join(errors.values()))
        self.errors = errors


def defaults(schema: SettingsSchema) -> dict[str, Any]:
    return {s.key: s.default for s in schema}


def validate_settings(schema: SettingsSchema, raw: Any) -> dict[str, Any]:
    """Return clean settings (defaults filled in) or raise SettingsError.

    Unknown keys are rejected so typos surface instead of being ignored.
    """
    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping):
        raise SettingsError({"_": "Settings must be an object."})
    known = {s.key for s in schema}
    errors: dict[str, str] = {k: f"Unknown setting '{k}'." for k in raw if k not in known}
    clean: dict[str, Any] = {}
    for field in schema:
        value = raw.get(field.key, field.default)
        try:
            clean[field.key] = field.validate(value)
        except ValueError as exc:
            errors[field.key] = str(exc)
    for field in schema:
        if isinstance(field, IntSetting) and field.cap_by and field.key in clean and field.cap_by in clean:
            message = cap_error(schema, field, clean)
            if message:
                errors[field.key] = message
    if errors:
        raise SettingsError(errors)
    return clean


def cap_error(schema: SettingsSchema, field: IntSetting, clean: Mapping[str, Any]) -> str:
    """The cap message if ``field``'s value exceeds the count of the choice it's capped by, else ""."""
    source = next((s for s in schema if s.key == field.cap_by), None)
    if not isinstance(source, ChoiceSetting):
        return ""
    choice = clean[field.cap_by]
    cap = dict(source.counts).get(choice)
    if cap is None or cap >= field.max or clean[field.key] <= cap:
        return ""
    return (field.cap_note or "{choice} allows at most {cap}.").format(choice=source.label_for(choice), cap=cap)


def settings_key(settings: Mapping[str, Any]) -> str:
    """Stable string identifying a settings configuration (for high scores)."""
    return "|".join(f"{k}={settings[k]}" for k in sorted(settings))


def schema_to_json(schema: SettingsSchema) -> list[dict[str, Any]]:
    return [s.to_json() for s in schema]
