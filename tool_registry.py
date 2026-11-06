# =============================================================================
# tool_registry.py — Registry dinâmico de tools
# =============================================================================

from __future__ import annotations

import inspect
import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


ToolHandler = Callable[[Dict[str, Any]], Awaitable[Any] | Any]

_handlers: Dict[str, ToolHandler] = {}
_definitions: Dict[str, dict] = {}
_runtime_profiles: Dict[str, dict] = {}


def register_tool(name: str, handler: ToolHandler, definition: Optional[dict] = None) -> None:
    """Regista/atualiza uma tool no registry global."""
    tool_name = str(name or "").strip()
    if not tool_name:
        raise ValueError("Tool name vazio")
    _handlers[tool_name] = handler
    if definition is not None:
        _definitions[tool_name] = definition


def has_tool(name: str) -> bool:
    return str(name or "").strip() in _handlers


def get_all_tool_definitions() -> List[dict]:
    return list(_definitions.values())


def get_registered_tool_names() -> List[str]:
    return list(_handlers.keys())


def get_tool_definition(name: str) -> Optional[dict]:
    return _definitions.get(str(name or "").strip())


def register_tool_runtime_profile(
    name: str,
    *,
    inject_conv_id: bool = False,
    inject_user_sub: bool = False,
) -> None:
    tool_name = str(name or "").strip()
    if not tool_name:
        raise ValueError("Tool name vazio")
    _runtime_profiles[tool_name] = {
        "inject_conv_id": bool(inject_conv_id),
        "inject_user_sub": bool(inject_user_sub),
    }


def get_tool_runtime_profile(name: str) -> dict:
    return dict(_runtime_profiles.get(str(name or "").strip(), {}))


def _validate_schema_value(value: Any, schema: dict, path: str, errors: List[str]) -> None:
    if not isinstance(schema, dict):
        return

    if "anyOf" in schema and isinstance(schema["anyOf"], list):
        branch_errors: List[List[str]] = []
        for branch in schema["anyOf"]:
            local_errors: List[str] = []
            _validate_schema_value(value, branch, path, local_errors)
            if not local_errors:
                return
            branch_errors.append(local_errors)
        errors.append(f"{path}: valor não corresponde a nenhuma variante permitida")
        return

    expected_type = schema.get("type")
    if expected_type is None and "properties" in schema:
        expected_type = "object"

    if expected_type == "object":
        if not isinstance(value, dict):
            errors.append(f"{path}: esperado object")
            return
        properties = schema.get("properties") or {}
        required = schema.get("required") or []
        for key in required:
            if key not in value:
                errors.append(f"{path}.{key}: campo obrigatório em falta")
        if schema.get("additionalProperties") is False:
            for key in value.keys():
                if key not in properties:
                    errors.append(f"{path}.{key}: campo não permitido")
        for key, prop_schema in properties.items():
            if key in value:
                _validate_schema_value(value[key], prop_schema, f"{path}.{key}", errors)
        if "enum" in schema and value not in schema["enum"]:
            errors.append(f"{path}: valor fora do enum permitido")
        return

    if expected_type == "array":
        if not isinstance(value, list):
            errors.append(f"{path}: esperado array")
            return
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for idx, item in enumerate(value):
                _validate_schema_value(item, item_schema, f"{path}[{idx}]", errors)
        return

    if expected_type == "string":
        if not isinstance(value, str):
            errors.append(f"{path}: esperado string")
            return
    elif expected_type == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            errors.append(f"{path}: esperado integer")
            return
    elif expected_type == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            errors.append(f"{path}: esperado number")
            return
    elif expected_type == "boolean":
        if not isinstance(value, bool):
            errors.append(f"{path}: esperado boolean")
            return

    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: valor fora do enum permitido")


def validate_tool_arguments(tool_name: str, arguments: Optional[Dict[str, Any]] = None) -> List[str]:
    definition = get_tool_definition(tool_name)
    if not definition:
        return []
    schema = (
        (definition.get("function") or {}).get("parameters")
        if isinstance(definition, dict)
        else None
    )
    if not isinstance(schema, dict):
        return []
    args = arguments or {}
    errors: List[str] = []
    _validate_schema_value(args, schema, "$", errors)
    return errors


async def execute_tool(tool_name: str, arguments: Optional[Dict[str, Any]] = None) -> Any:
    fn = _handlers.get(str(tool_name or "").strip())
    if not fn:
        return {"error": f"Tool desconhecida: {tool_name}"}
    args = arguments or {}
    try:
        validation_errors = validate_tool_arguments(tool_name, args)
        if validation_errors:
            return {
                "error": "Argumentos inválidos para a tool.",
                "validation_errors": validation_errors[:20],
            }
        result = fn(args)
        if inspect.isawaitable(result):
            return await result
        return result
    except Exception as e:
        logging.error("[ToolRegistry] execute_tool failed (%s): %s", tool_name, e, exc_info=True)
        return {"error": f"Erro ao executar tool {tool_name}: {str(e)}"}
