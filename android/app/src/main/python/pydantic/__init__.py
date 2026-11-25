"""Sustituto mínimo de Pydantic para Android.

Chaquopy no ofrece pydantic-core (está escrito en Rust) para Android, así que aquí se implementa solo lo que usa
`app/main.py`: `BaseModel`, `Field` y la validación «lax» de tipos simples, `Literal`, `X | None`, `list[...]`,
`dict[...]` y modelos anidados. En el contenedor se sigue usando Pydantic de verdad; este paquete solo se empaqueta en
la app Android. Si `main.py` empieza a usar algo que no esté aquí, falla al importar o al validar, nunca en silencio.
"""
from __future__ import annotations

import re
import types
import typing
from typing import Any, Literal, Union, get_args, get_origin, get_type_hints

__all__ = ["BaseModel", "Field", "FieldInfo", "ValidationError", "validate_value"]

_MISSING = object()
_CONSTRAINTS = ("gt", "ge", "lt", "le", "min_length", "max_length", "pattern")


class FieldInfo:
    def __init__(self, default: Any = _MISSING, *, alias: str | None = None, **constraints: Any):
        if default is Ellipsis:
            default = _MISSING
        unknown = set(constraints) - set(_CONSTRAINTS) - {"description", "title"}
        if unknown:
            raise TypeError(f"Restricción no soportada en el sustituto de Pydantic: {sorted(unknown)}")
        self.default = default
        self.alias = alias
        self.constraints = {k: v for k, v in constraints.items() if k in _CONSTRAINTS and v is not None}

    @property
    def required(self) -> bool:
        return self.default is _MISSING


def Field(default: Any = _MISSING, **kwargs: Any) -> Any:  # noqa: N802 - mismo nombre que en Pydantic
    return FieldInfo(default, **kwargs)


class ValidationError(ValueError):
    def __init__(self, errors: list[dict]):
        super().__init__(f"{len(errors)} errores de validación")
        self._errors = errors

    def errors(self) -> list[dict]:
        return self._errors


class _Invalid(Exception):
    def __init__(self, kind: str, msg: str):
        super().__init__(msg)
        self.kind = kind
        self.msg = msg


def _error(loc: tuple, kind: str, msg: str, value: Any) -> dict:
    return {"type": kind, "loc": list(loc), "msg": msg, "input": value if _json_safe(value) else repr(value)}


def _json_safe(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool, list, dict))


_TRUE = {"1", "true", "on", "yes", "y", "t"}
_FALSE = {"0", "false", "off", "no", "n", "f"}
_INT_TEXT = re.compile(r"^\s*[+-]?\d+\s*$")


def _scalar(value: Any, kind: type) -> Any:
    if kind is bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, int) and value in (0, 1):
            return bool(value)
        if isinstance(value, str) and value.strip().lower() in _TRUE | _FALSE:
            return value.strip().lower() in _TRUE
        raise _Invalid("bool_parsing", "Input should be a valid boolean")
    if kind is int:
        if isinstance(value, bool):
            raise _Invalid("int_type", "Input should be a valid integer")
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        if isinstance(value, str) and _INT_TEXT.match(value):
            return int(value)
        raise _Invalid("int_parsing", "Input should be a valid integer, unable to parse string as an integer")
    if kind is float:
        if isinstance(value, bool):
            raise _Invalid("float_type", "Input should be a valid number")
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                pass
        raise _Invalid("float_parsing", "Input should be a valid number, unable to parse string as a number")
    if kind is str:
        if isinstance(value, str):
            return value
        raise _Invalid("string_type", "Input should be a valid string")
    if isinstance(value, kind):
        return value
    raise _Invalid("is_instance_of", f"Input should be an instance of {kind.__name__}")


def _check_constraints(value: Any, constraints: dict) -> None:
    if not constraints or value is None:
        return
    if "gt" in constraints and not value > constraints["gt"]:
        raise _Invalid("greater_than", f"Input should be greater than {constraints['gt']}")
    if "ge" in constraints and not value >= constraints["ge"]:
        raise _Invalid("greater_than_equal", f"Input should be greater than or equal to {constraints['ge']}")
    if "lt" in constraints and not value < constraints["lt"]:
        raise _Invalid("less_than", f"Input should be less than {constraints['lt']}")
    if "le" in constraints and not value <= constraints["le"]:
        raise _Invalid("less_than_equal", f"Input should be less than or equal to {constraints['le']}")
    if "min_length" in constraints and len(value) < constraints["min_length"]:
        raise _Invalid("too_short", f"Value should have at least {constraints['min_length']} items")
    if "max_length" in constraints and len(value) > constraints["max_length"]:
        raise _Invalid("too_long", f"Value should have at most {constraints['max_length']} items")
    if "pattern" in constraints and not re.search(constraints["pattern"], value):
        raise _Invalid("string_pattern_mismatch", f"String should match pattern '{constraints['pattern']}'")


def validate_value(value: Any, annotation: Any, constraints: dict | None = None, loc: tuple = ()) -> Any:
    """Valida y convierte `value` al tipo `annotation`. Lanza ValidationError con la ubicación del fallo."""
    try:
        result = _convert(value, annotation, loc)
        _check_constraints(result, constraints or {})
        return result
    except _Invalid as exc:
        raise ValidationError([_error(loc, exc.kind, exc.msg, value)]) from None


def _convert(value: Any, annotation: Any, loc: tuple) -> Any:
    if annotation is Any or annotation is None or annotation is typing.Any:
        return value
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin in (Union, types.UnionType):
        if value is None and type(None) in args:
            return None
        errors = []
        for option in args:
            if option is type(None):
                continue
            try:
                return _convert(value, option, loc)
            except _Invalid as exc:
                errors.append(exc)
            except ValidationError as exc:
                errors.append(_Invalid(exc.errors()[0]["type"], exc.errors()[0]["msg"]))
        raise errors[0] if errors else _Invalid("none_required", "Input should be None")
    if origin is Literal:
        for option in args:
            if value == option and type(value) is type(option):
                return option
        raise _Invalid("literal_error", "Input should be " + " or ".join(repr(a) for a in args))
    if origin in (list, typing.List):
        if not isinstance(value, (list, tuple)):
            raise _Invalid("list_type", "Input should be a valid list")
        item_type = args[0] if args else Any
        out, errors = [], []
        for index, item in enumerate(value):
            try:
                out.append(validate_value(item, item_type, None, (*loc, index)))
            except ValidationError as exc:
                errors.extend(exc.errors())
        if errors:
            raise ValidationError(errors)
        return out
    if origin in (dict, typing.Dict):
        if not isinstance(value, dict):
            raise _Invalid("dict_type", "Input should be a valid dictionary")
        key_type, value_type = args if args else (Any, Any)
        out, errors = {}, []
        for key, item in value.items():
            try:
                out[validate_value(key, key_type, None, (*loc, key))] = validate_value(item, value_type, None, (*loc, key))
            except ValidationError as exc:
                errors.extend(exc.errors())
        if errors:
            raise ValidationError(errors)
        return out
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        if isinstance(value, annotation):
            return value
        if not isinstance(value, dict):
            raise _Invalid("model_type", f"Input should be a valid dictionary or instance of {annotation.__name__}")
        return annotation._validate(value, loc)
    if isinstance(annotation, type):
        return _scalar(value, annotation)
    raise TypeError(f"Tipo no soportado en el sustituto de Pydantic: {annotation!r}")


class BaseModel:
    __fields__: dict[str, tuple[Any, FieldInfo]] = {}

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        cls._resolved = False

    @classmethod
    def _model_fields(cls) -> dict[str, tuple[Any, FieldInfo]]:
        # Se resuelve tarde: con `from __future__ import annotations` las anotaciones son texto que puede citar
        # clases definidas después (un modelo anidado).
        if not cls.__dict__.get("_resolved"):
            hints = get_type_hints(cls)
            fields = {}
            for name, annotation in hints.items():
                if name.startswith("_") or get_origin(annotation) is typing.ClassVar:
                    continue
                default = getattr(cls, name, _MISSING)
                info = default if isinstance(default, FieldInfo) else FieldInfo(default)
                fields[name] = (annotation, info)
            cls.__fields__ = fields
            cls._resolved = True
        return cls.__fields__

    @classmethod
    def _validate(cls, data: dict, loc: tuple = ()) -> "BaseModel":
        values, errors = {}, []
        for name, (annotation, info) in cls._model_fields().items():
            key = info.alias or name
            if key not in data:
                if info.required:
                    errors.append(_error((*loc, key), "missing", "Field required", None))
                else:
                    values[name] = info.default
                continue
            try:
                values[name] = validate_value(data[key], annotation, info.constraints, (*loc, key))
            except ValidationError as exc:
                errors.extend(exc.errors())
        if errors:
            raise ValidationError(errors)
        instance = object.__new__(cls)
        instance.__dict__.update(values)
        return instance

    def __init__(self, **data: Any) -> None:
        self.__dict__.update(type(self)._validate(data).__dict__)

    @classmethod
    def model_validate(cls, data: dict) -> "BaseModel":
        return cls._validate(data)

    def model_dump(self) -> dict:
        def dump(value: Any) -> Any:
            if isinstance(value, BaseModel):
                return value.model_dump()
            if isinstance(value, list):
                return [dump(v) for v in value]
            if isinstance(value, dict):
                return {k: dump(v) for k, v in value.items()}
            return value

        return {name: dump(getattr(self, name)) for name in type(self)._model_fields()}

    def __repr__(self) -> str:
        fields = ", ".join(f"{k}={getattr(self, k)!r}" for k in type(self)._model_fields())
        return f"{type(self).__name__}({fields})"

    def __eq__(self, other: object) -> bool:
        return type(self) is type(other) and self.model_dump() == other.model_dump()  # type: ignore[union-attr]
