"""
live_server.py — Spins up real HTTP REST servers per entity.
Each entity gets its own port. Serves from SQLite. Full CRUD.
Includes CORS headers for browser/Postman use.
"""

import json
import threading
import re
import uuid
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from typing import Optional
import time


# Registry of running servers
_servers: dict = {}  # entity_name -> {"server": HTTPServer, "thread": Thread, "port": int}
_port_counter = 5100


def _next_port() -> int:
    global _port_counter
    while _is_port_in_use(_port_counter):
        _port_counter += 1
    p = _port_counter
    _port_counter += 1
    return p


def _is_port_in_use(port: int) -> bool:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.2)
        return s.connect_ex(("localhost", port)) == 0


def make_handler(entity_name: str, db_engine, entity_props: list):
    """Create a request handler class bound to an entity's database."""

    plural = entity_name.lower() + "s"
    base_path = f"/api/{plural}"

    class EntityHandler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass  # Suppress default logging, we handle it ourselves

        def _cors_headers(self):
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, DELETE, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

        def _json_response(self, status: int, data):
            body = json.dumps(data, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self._cors_headers()
            self.end_headers()
            self.wfile.write(body)

        def _read_body(self) -> Optional[dict]:
            length = int(self.headers.get("Content-Length", 0))
            if length == 0:
                return {}
            raw = self.rfile.read(length)
            try:
                return json.loads(raw.decode("utf-8"))
            except Exception:
                return {}

        def _parse_id(self, path: str) -> Optional[str]:
            """Extract ID from path like /api/products/some-guid"""
            parts = path.rstrip("/").split("/")
            if len(parts) >= 4:
                return parts[3]
            return None

        def do_OPTIONS(self):
            self.send_response(204)
            self._cors_headers()
            self.end_headers()

        def do_GET(self):
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/")

            # GET /api/entities
            if path == base_path:
                rows = db_engine.get_all_rows(entity_name)
                self._json_response(200, rows)
                return

            # GET /api/entities/{id}
            row_id = self._parse_id(path)
            if row_id and path.startswith(base_path + "/"):
                row = db_engine.get_row_by_id(entity_name, row_id)
                if row:
                    self._json_response(200, row)
                else:
                    self._json_response(404, {"error": f"{entity_name} not found", "id": row_id})
                return

            # GET /swagger (basic swagger-like JSON)
            if path in ("/swagger", "/swagger/v1/swagger.json", "/openapi.json"):
                self._json_response(200, _build_openapi(entity_name, plural, entity_props))
                return

            # Health check
            if path in ("/health", "/"):
                self._json_response(200, {
                    "status": "healthy",
                    "entity": entity_name,
                    "endpoint": f"http://localhost:{_servers.get(entity_name, {}).get('port', '?')}{base_path}",
                    "record_count": len(db_engine.get_all_rows(entity_name)),
                })
                return

            self._json_response(404, {"error": "Not found"})

        def do_POST(self):
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/")

            if path != base_path:
                self._json_response(404, {"error": "Not found"})
                return

            body = self._read_body()
            if not body:
                self._json_response(400, {"error": "Request body required"})
                return

            # Auto-assign Id if not provided
            if "Id" in [p["name"] for p in entity_props] and "Id" not in body:
                body["Id"] = str(uuid.uuid4())

            result = db_engine.insert_row(entity_name, body)
            if result:
                self._json_response(201, result)
            else:
                self._json_response(500, {"error": "Insert failed"})

        def do_PUT(self):
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/")
            row_id = self._parse_id(path)

            if not row_id:
                self._json_response(400, {"error": "ID required in path"})
                return

            body = self._read_body()
            success = db_engine.update_row(entity_name, row_id, body)
            if success:
                self._json_response(200, body)
            else:
                self._json_response(404, {"error": f"{entity_name} {row_id} not found"})

        def do_PATCH(self):
            self.do_PUT()

        def do_DELETE(self):
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/")
            row_id = self._parse_id(path)

            if not row_id:
                self._json_response(400, {"error": "ID required in path"})
                return

            success = db_engine.delete_row(entity_name, row_id)
            if success:
                self.send_response(204)
                self._cors_headers()
                self.end_headers()
            else:
                self._json_response(404, {"error": f"{entity_name} {row_id} not found"})

    return EntityHandler


def _build_openapi(entity_name: str, plural: str, props: list) -> dict:
    """Generate a basic OpenAPI 3.0 spec for an entity."""
    base = f"/api/{plural}"

    schema_props = {}
    for p in props:
        t = p["type"].rstrip("?")
        if t in ("int", "long", "short", "byte"):
            oas_type = {"type": "integer"}
        elif t in ("float", "double", "decimal"):
            oas_type = {"type": "number"}
        elif t in ("bool", "Boolean"):
            oas_type = {"type": "boolean"}
        elif t == "Guid":
            oas_type = {"type": "string", "format": "uuid"}
        elif t in ("DateTime", "DateTimeOffset"):
            oas_type = {"type": "string", "format": "date-time"}
        else:
            oas_type = {"type": "string"}

        if p.get("nullable"):
            oas_type["nullable"] = True
        schema_props[p["name"]] = oas_type

    return {
        "openapi": "3.0.0",
        "info": {
            "title": f"{entity_name} API",
            "version": "1.0.0",
            "description": f"Live mock API for {entity_name} — powered by CSForge"
        },
        "paths": {
            base: {
                "get": {
                    "summary": f"Get all {plural}",
                    "operationId": f"getAll{entity_name}s",
                    "responses": {"200": {"description": "Success"}}
                },
                "post": {
                    "summary": f"Create {entity_name}",
                    "operationId": f"create{entity_name}",
                    "responses": {"201": {"description": "Created"}}
                }
            },
            f"{base}/{{id}}": {
                "get": {
                    "summary": f"Get {entity_name} by ID",
                    "operationId": f"get{entity_name}ById",
                    "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "Success"}, "404": {"description": "Not Found"}}
                },
                "put": {
                    "summary": f"Update {entity_name}",
                    "operationId": f"update{entity_name}",
                    "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "Success"}}
                },
                "delete": {
                    "summary": f"Delete {entity_name}",
                    "operationId": f"delete{entity_name}",
                    "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"204": {"description": "No Content"}}
                }
            }
        },
        "components": {
            "schemas": {
                entity_name: {
                    "type": "object",
                    "properties": schema_props
                }
            }
        }
    }


def start_server(entity_name: str, db_engine, entity_props: list) -> dict:
    """Start a live HTTP server for an entity. Returns server info."""
    if entity_name in _servers:
        return _servers[entity_name]

    port = _next_port()
    handler = make_handler(entity_name, db_engine, entity_props)

    server = HTTPServer(("0.0.0.0", port), handler)

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    plural = entity_name.lower() + "s"
    info = {
        "port": port,
        "entity": entity_name,
        "base_url": f"http://localhost:{port}",
        "endpoint": f"http://localhost:{port}/api/{plural}",
        "swagger": f"http://localhost:{port}/swagger",
        "health": f"http://localhost:{port}/health",
        "started_at": time.strftime("%H:%M:%S"),
        "thread": thread,
        "server": server,
    }
    _servers[entity_name] = info

    # Small pause to confirm server starts
    time.sleep(0.1)
    return {k: v for k, v in info.items() if k not in ("thread", "server")}


def stop_server(entity_name: str) -> bool:
    """Stop a running server for an entity."""
    if entity_name not in _servers:
        return False
    info = _servers.pop(entity_name)
    try:
        info["server"].shutdown()
    except Exception:
        pass
    return True


def get_running_servers() -> dict:
    """Return info about all running servers."""
    return {
        name: {k: v for k, v in info.items() if k not in ("thread", "server")}
        for name, info in _servers.items()
    }


def is_running(entity_name: str) -> bool:
    return entity_name in _servers
