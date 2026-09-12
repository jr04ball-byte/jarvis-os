"""Recursive validation for the JSON Schema subset used by Jarvis tools."""
import math


def validate(value, schema, path="arguments"):
    kind = schema.get("type")
    types = {"object": dict, "array": list, "string": str, "boolean": bool,
             "integer": int, "number": (int, float), "null": type(None)}
    if kind in types and (not isinstance(value, types[kind]) or
                         (kind in {"integer", "number"} and isinstance(value, bool))):
        raise ValueError(f"{path} must be {kind}")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path} must be one of {schema['enum']}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not math.isfinite(value):
            raise ValueError(f"{path} must be finite")
        for limit, compare in (("minimum", lambda a,b: a < b), ("maximum", lambda a,b: a > b)):
            if limit in schema and compare(value, schema[limit]):
                raise ValueError(f"{path} violates {limit} {schema[limit]}")
    if isinstance(value, dict):
        props = schema.get("properties", {})
        for name in schema.get("required", []):
            if name not in value:
                raise ValueError(f"{path}: missing required argument '{name}'")
        for name, item in value.items():
            child = props.get(name, schema.get("additionalProperties", True))
            if child is False:
                raise ValueError(f"{path}: unknown field '{name}'")
            if isinstance(child, dict):
                validate(item, child, f"{path}.{name}")
    if isinstance(value, list):
        for index, item in enumerate(value):
            validate(item, schema.get("items", {}), f"{path}[{index}]")
