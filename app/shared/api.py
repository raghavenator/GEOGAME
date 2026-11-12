"""Consistent JSON errors and request parsing for game APIs."""
from __future__ import annotations

from typing import Any

from flask import Blueprint, jsonify, request
from flask.typing import ResponseReturnValue


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.status, self.code, self.message, self.details = status, code, message, details or {}


def json_body() -> dict[str, Any]:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ApiError(400, "bad_request", "Request body must be a JSON object.")
    return data


def require_str(data: dict[str, Any], key: str, max_len: int = 200) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ApiError(400, "bad_request", f"'{key}' is required and must be a non-empty string.")
    if len(value) > max_len:
        raise ApiError(400, "bad_request", f"'{key}' is too long.")
    return value.strip()


def require_int(data: dict[str, Any], key: str, lo: int, hi: int) -> int:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        raise ApiError(400, "bad_request", f"'{key}' must be an integer between {lo} and {hi}.")
    return value


def register_error_handlers(bp: Blueprint) -> None:
    @bp.errorhandler(ApiError)
    def _handle(err: ApiError) -> ResponseReturnValue:
        body: dict[str, Any] = {"error": {"code": err.code, "message": err.message}}
        if err.details:
            body["error"]["details"] = err.details
        return jsonify(body), err.status
