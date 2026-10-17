"""Build a portable OpenAPI route/request contract from URL modules and decorators."""

import argparse
import ast
import json
import re
from pathlib import Path

BASE = Path(__file__).resolve().parent
CONSTANTS = {
    "TASK_FIELDS": ("title", "description", "assignee_id", "priority", "due_date", "status"),
    "FIELDS": ("display_name", "slug", "timezone"),
}


def literal(node):
    if isinstance(node, ast.Name):
        return CONSTANTS[node.id]
    if isinstance(node, (ast.Tuple, ast.List)):
        result = []
        for item in node.elts:
            result.extend(literal(item.value) if isinstance(item, ast.Starred) else [literal(item)])
        return result
    return ast.literal_eval(node)


def tree(module):
    return ast.parse((BASE / (module.replace(".", "/") + ".py")).read_text(encoding="utf-8"))


def metadata(module, name):
    if name == "attachment_collection":
        return ["GET", "POST"], ["file"], True
    if module == "foundation.views":
        return ["GET"], [], False
    function = next(
        n for n in tree(module).body if isinstance(n, ast.FunctionDef) and n.name == name
    )
    for decorator in function.decorator_list:
        if isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Name):
            if decorator.func.id in {"endpoint", "project_endpoint", "team_endpoint"}:
                methods = literal(decorator.args[0]).split(", ")
                fields = literal(decorator.args[1]) if len(decorator.args) > 1 else []
                return methods, fields, False
    raise ValueError(f"Missing endpoint contract: {module}.{name}")


def routes(module="config.urls", prefix=""):
    nodes = tree(module).body
    imports = {}
    for node in nodes:
        if isinstance(node, ast.ImportFrom):
            parent = node.module or module.rsplit(".", 1)[0]
            for alias in node.names:
                imports[alias.asname or alias.name] = parent + "." + alias.name
    urls = next(
        n.value
        for n in nodes
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "urlpatterns" for t in n.targets)
    )
    for call in urls.elts:
        path = prefix + literal(call.args[0])
        target = call.args[1]
        if isinstance(target, ast.Call):
            yield from routes(literal(target.args[0]), path)
        else:
            if isinstance(target, ast.Attribute):
                view_module, name = imports[target.value.id], target.attr
            else:
                view_module, name = imports.get(target.id, module + "." + target.id).rsplit(".", 1)
            yield "/" + path, view_module, name


def field_schema(name):
    if name == "version":
        return {"type": "integer", "minimum": 1}
    if name in {"read", "assignment_email"}:
        return {"type": "boolean"}
    result = {"type": "string"}
    if name.endswith("_id"):
        result["format"] = "uuid"
    if name == "assignee_id":
        result["nullable"] = True
    if name == "due_date":
        result.update(format="date", nullable=True)
    if name == "password":
        result.update(format="password", writeOnly=True)
    if name == "email":
        result["format"] = "email"
    return result


def document():
    paths = {}
    for route, module, name in routes():
        methods, fields, multipart = metadata(module, name)
        path = re.sub(r"<(?:uuid|str):([^>]+)>", r"{\1}", route)
        parameters = [
            {
                "name": field,
                "in": "path",
                "required": True,
                "schema": {"type": "string", **({"format": "uuid"} if kind == "uuid" else {})},
            }
            for kind, field in re.findall(r"<(uuid|str):([^>]+)>", route)
        ]
        item = paths.setdefault(path, {})
        for method in methods:
            operation = {
                "operationId": re.sub(r"\W+", "_", method.lower() + path).strip("_"),
                "summary": name.replace("_", " ").capitalize(),
                "tags": [module.split(".")[0]],
                "parameters": list(parameters),
                "responses": {
                    "2XX": {
                        "description": (
                            "Successful JSON response, binary download or empty 204 response."
                        )
                    },
                    "default": {
                        "description": (
                            "Validation, access, conflict, throttle or dependency error."
                        ),
                        "content": {
                            "application/json": {"schema": {"$ref": "#/components/schemas/Error"}}
                        },
                    },
                },
            }
            if "/health/" in path or "/auth/" in path and name not in {"me", "sign_out"}:
                operation["security"] = []
            if method in {"POST", "PATCH", "DELETE"}:
                operation["parameters"].append(
                    {
                        "name": "X-CSRFToken",
                        "in": "header",
                        "required": True,
                        "schema": {"type": "string"},
                        "description": "Session CSRF token; send with its cookie.",
                    }
                )
            if method == "POST" and name in {
                "collection",
                "projects",
                "tasks",
                "invitations",
                "exports",
                "attachment_collection",
            }:
                operation["parameters"].append(
                    {
                        "name": "Idempotency-Key",
                        "in": "header",
                        "required": True,
                        "schema": {"type": "string", "minLength": 16, "maxLength": 128},
                    }
                )
            if method in {"POST", "PATCH"} or method == "DELETE" and name == "task_detail":
                body_fields = ["version"] if method == "DELETE" else fields
                schema = {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {field: field_schema(field) for field in body_fields},
                }
                if multipart:
                    schema["properties"] = {"file": {"type": "string", "format": "binary"}}
                    schema["required"] = ["file"]
                operation["requestBody"] = {
                    "required": True,
                    "content": {
                        "multipart/form-data" if multipart else "application/json": {
                            "schema": schema
                        }
                    },
                }
            if method == "GET" and name == "search":
                operation["parameters"] += [
                    {
                        "name": field,
                        "in": "query",
                        "required": field == "q",
                        "schema": {"type": "string"},
                    }
                    for field in ["q", "state", "project", "cursor", "limit"]
                ]
            if name == "download":
                operation["responses"]["2XX"] = {
                    "description": "Authorized private download, no-store.",
                    "content": {
                        "application/zip"
                        if module.startswith("lifecycle")
                        else "application/octet-stream": {
                            "schema": {"type": "string", "format": "binary"}
                        }
                    },
                }
            item[method.lower()] = operation
    return {
        "openapi": "3.0.3",
        "info": {
            "title": "WorkNest API",
            "version": "1.0.0",
            "description": (
                "Same-origin session API. Unknown JSON fields are rejected. "
                "Hidden resources return 404; disallowed visible actions return 403. "
                "Task/comment edits require version. See API.md for response details."
            ),
        },
        "servers": [{"url": "/"}],
        "security": [{"session": []}],
        "paths": paths,
        "components": {
            "securitySchemes": {"session": {"type": "apiKey", "in": "cookie", "name": "sessionid"}},
            "schemas": {
                "Error": {
                    "type": "object",
                    "required": ["error"],
                    "properties": {
                        "error": {
                            "type": "object",
                            "required": ["code", "message", "request_id", "details"],
                            "properties": {
                                "code": {"type": "string"},
                                "message": {"type": "string"},
                                "request_id": {"type": "string"},
                                "details": {"type": "object"},
                            },
                        }
                    },
                }
            },
        },
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    target = BASE.parent / "docs/openapi.json"
    content = json.dumps(document(), indent=2) + "\n"
    if args.check:
        if target.read_text(encoding="utf-8") != content:
            raise SystemExit("OpenAPI contract is stale; run python backend/generate_openapi.py")
    else:
        target.write_text(content, encoding="utf-8")
    print("OpenAPI route/request contract verified.")
