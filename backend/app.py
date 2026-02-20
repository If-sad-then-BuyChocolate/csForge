"""
app.py — CSForge main API server.
Flask backend that exposes all features to the frontend:
  - Project scanning / watching
  - Entity CRUD (parsed from real .cs files)
  - SQLite database management
  - Live HTTP server management
  - Two-way sync (model edits write back to .cs files)
  - Infrastructure code generation
  - SSE for real-time updates
"""

import io
import json
import os
import queue
import threading
import time
import zipfile
from flask import Flask, request, jsonify, Response, stream_with_context, send_file
from cs_parser import scan_directory, parse_cs_file, rename_property, \
    change_property_type, toggle_nullable, add_property, remove_property
from db_engine import EntityDatabase
from live_server import start_server, stop_server, get_running_servers, is_running
from file_watcher import CSharpFileWatcher
from infra_gen import generate as generate_infra

app = Flask(__name__)

# ── State ──────────────────────────────────────────────────────────────────
_state = {
    "project_path": None,
    "entities": {},      # name -> entity dict
    "watch_log": [],     # list of change events
}
_db = EntityDatabase(os.path.join(os.path.dirname(__file__), "..", "databases"))
_sse_queues: list = []  # SSE subscriber queues
_watcher = None
_last_infra: list = []  # last generated infra files [{label, path, code}]

# ── SSE Broadcasting ───────────────────────────────────────────────────────

def _broadcast(event_type: str, data: dict):
    msg = {"type": event_type, "data": data, "ts": time.time()}
    dead = []
    for q in _sse_queues:
        try:
            q.put_nowait(msg)
        except Exception:
            dead.append(q)
    for q in dead:
        try:
            _sse_queues.remove(q)
        except ValueError:
            pass


# ── CORS Middleware ────────────────────────────────────────────────────────

@app.after_request
def add_cors(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response


@app.route("/", defaults={"path": ""}, methods=["OPTIONS"])
@app.route("/<path:path>", methods=["OPTIONS"])
def options_handler(path):
    return Response(status=204)


# ── SSE Endpoint ────────────────────────────────────────────────────────────

@app.route("/api/events")
def sse_events():
    q = queue.Queue(maxsize=100)
    _sse_queues.append(q)

    def generate():
        # Send initial ping
        yield "data: {\"type\":\"connected\"}\n\n"
        while True:
            try:
                msg = q.get(timeout=15)
                yield f"data: {json.dumps(msg)}\n\n"
            except Exception:
                # Heartbeat
                yield "data: {\"type\":\"ping\"}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        }
    )


# ── Project Scanning ────────────────────────────────────────────────────────

@app.route("/api/project/scan", methods=["POST"])
def scan_project():
    data = request.get_json() or {}
    path = data.get("path", "").strip()

    if not path:
        path = os.path.join(os.path.dirname(__file__), "..", "sample_project")

    path = os.path.abspath(path)
    if not os.path.isdir(path):
        return jsonify({"error": f"Directory not found: {path}"}), 400

    _state["project_path"] = path
    entities = scan_directory(path)

    if not entities:
        return jsonify({"error": "No C# entity classes found in directory"}), 404

    # Process each entity: create/migrate DB, store in state
    results = []
    for entity in entities:
        ed = entity.to_dict()
        name = ed["name"]
        _state["entities"][name] = ed

        migration = _db.create_or_migrate_table(ed)
        row_count = len(_db.get_all_rows(name))

        results.append({
            "name": name,
            "namespace": ed["namespace"],
            "file": ed["file_path"],
            "properties": len(ed["properties"]),
            "db_action": migration["action"],
            "rows": row_count,
        })

    # Start file watcher
    _start_watcher(path)

    _broadcast("scan_complete", {
        "path": path,
        "entities": results,
    })

    return jsonify({
        "path": path,
        "entities": results,
        "total": len(results),
    })


@app.route("/api/project/path", methods=["GET"])
def get_project_path():
    return jsonify({"path": _state["project_path"]})


@app.route("/api/watch/status", methods=["GET"])
def watch_status():
    """Return what path is being watched and which .cs files are included."""
    path = _state["project_path"]
    running = _watcher is not None and _watcher.is_running
    watched_files = []
    if path and os.path.isdir(path):
        for dirpath, _, filenames in os.walk(path):
            for fname in filenames:
                if fname.endswith(".cs"):
                    watched_files.append(
                        os.path.relpath(os.path.join(dirpath, fname), path)
                        .replace("\\", "/")
                    )
    return jsonify({
        "watching": running,
        "path": path,
        "file_count": len(watched_files),
        "files": sorted(watched_files),
    })


# ── Entity Management ────────────────────────────────────────────────────────

@app.route("/api/entities", methods=["GET"])
def get_entities():
    entities = []
    for name, ed in _state["entities"].items():
        rows = _db.get_all_rows(name)
        server_info = get_running_servers().get(name)
        entities.append({
            **ed,
            "row_count": len(rows),
            "is_live": server_info is not None,
            "server": server_info,
        })
    return jsonify(entities)


@app.route("/api/entities/<name>", methods=["GET"])
def get_entity(name):
    ed = _state["entities"].get(name)
    if not ed:
        return jsonify({"error": "Entity not found"}), 404
    rows = _db.get_all_rows(name)
    server_info = get_running_servers().get(name)
    return jsonify({
        **ed,
        "row_count": len(rows),
        "is_live": server_info is not None,
        "server": server_info,
        "schema_sql": _db.get_schema_sql(name),
    })


# ── Two-Way Sync: Property Operations ─────────────────────────────────────

@app.route("/api/entities/<name>/properties/rename", methods=["POST"])
def rename_prop(name):
    ed = _state["entities"].get(name)
    if not ed:
        return jsonify({"error": "Entity not found"}), 404

    data = request.get_json() or {}
    old_name = data.get("old_name", "").strip()
    new_name = data.get("new_name", "").strip()

    if not old_name or not new_name:
        return jsonify({"error": "old_name and new_name required"}), 400

    if not new_name[0].isupper():
        new_name = new_name[0].upper() + new_name[1:]

    success = rename_property(ed["file_path"], old_name, new_name)
    if not success:
        return jsonify({"error": f"Could not rename {old_name} in {ed['file_path']}"}), 500

    # Re-parse and update state
    _rescan_file(ed["file_path"])

    _broadcast("property_renamed", {
        "entity": name,
        "old_name": old_name,
        "new_name": new_name,
    })

    return jsonify({"success": True, "old_name": old_name, "new_name": new_name})


@app.route("/api/entities/<name>/properties/type", methods=["POST"])
def change_prop_type(name):
    ed = _state["entities"].get(name)
    if not ed:
        return jsonify({"error": "Entity not found"}), 404

    data = request.get_json() or {}
    prop_name = data.get("prop_name", "").strip()
    new_type = data.get("new_type", "").strip()
    nullable = data.get("nullable", False)

    if not prop_name or not new_type:
        return jsonify({"error": "prop_name and new_type required"}), 400

    success = change_property_type(ed["file_path"], prop_name, new_type, nullable)
    if not success:
        return jsonify({"error": f"Could not change type of {prop_name}"}), 500

    _rescan_file(ed["file_path"])

    _broadcast("property_type_changed", {
        "entity": name,
        "prop": prop_name,
        "new_type": new_type,
        "nullable": nullable,
    })

    return jsonify({"success": True})


@app.route("/api/entities/<name>/properties/nullable", methods=["POST"])
def toggle_prop_nullable(name):
    ed = _state["entities"].get(name)
    if not ed:
        return jsonify({"error": "Entity not found"}), 404

    data = request.get_json() or {}
    prop_name = data.get("prop_name", "").strip()
    nullable = data.get("nullable", False)

    if not prop_name:
        return jsonify({"error": "prop_name required"}), 400

    success = toggle_nullable(ed["file_path"], prop_name, nullable)
    if not success:
        return jsonify({"error": f"Could not toggle nullable on {prop_name}"}), 500

    _rescan_file(ed["file_path"])

    _broadcast("property_nullable_changed", {
        "entity": name,
        "prop": prop_name,
        "nullable": nullable,
    })

    return jsonify({"success": True})


@app.route("/api/entities/<name>/properties/add", methods=["POST"])
def add_prop(name):
    ed = _state["entities"].get(name)
    if not ed:
        return jsonify({"error": "Entity not found"}), 404

    data = request.get_json() or {}
    prop_name = data.get("name", "").strip()
    prop_type = data.get("type", "string").strip()
    nullable = data.get("nullable", False)

    if not prop_name:
        return jsonify({"error": "Property name required"}), 400

    # Pascal case
    if prop_name and not prop_name[0].isupper():
        prop_name = prop_name[0].upper() + prop_name[1:]

    success = add_property(ed["file_path"], prop_name, prop_type, nullable)
    if not success:
        return jsonify({"error": f"Could not add property {prop_name}"}), 500

    # Re-parse, migrate DB
    updated = _rescan_file(ed["file_path"])
    if updated:
        _db.create_or_migrate_table(updated)

    _broadcast("property_added", {
        "entity": name,
        "prop": prop_name,
        "type": prop_type,
        "nullable": nullable,
    })

    return jsonify({"success": True, "prop": prop_name})


@app.route("/api/entities/<name>/properties/<prop_name>", methods=["DELETE"])
def delete_prop(name, prop_name):
    ed = _state["entities"].get(name)
    if not ed:
        return jsonify({"error": "Entity not found"}), 404

    success = remove_property(ed["file_path"], prop_name)
    if not success:
        return jsonify({"error": f"Could not remove {prop_name}"}), 500

    _rescan_file(ed["file_path"])

    _broadcast("property_removed", {
        "entity": name,
        "prop": prop_name,
    })

    return jsonify({"success": True})


# ── SQLite Data ───────────────────────────────────────────────────────────

@app.route("/api/entities/<name>/rows", methods=["GET"])
def get_rows(name):
    if name not in _state["entities"]:
        return jsonify({"error": "Entity not found"}), 404
    rows = _db.get_all_rows(name)
    return jsonify(rows)


@app.route("/api/entities/<name>/rows/<row_id>", methods=["GET"])
def get_row(name, row_id):
    row = _db.get_row_by_id(name, row_id)
    if not row:
        return jsonify({"error": "Row not found"}), 404
    return jsonify(row)


@app.route("/api/entities/<name>/rows/<row_id>", methods=["PUT", "PATCH"])
def update_row(name, row_id):
    data = request.get_json() or {}
    success = _db.update_row(name, row_id, data)
    if success:
        _broadcast("row_updated", {"entity": name, "id": row_id})
        return jsonify({"success": True})
    return jsonify({"error": "Update failed or row not found"}), 404


@app.route("/api/entities/<name>/rows", methods=["POST"])
def insert_row(name):
    data = request.get_json() or {}
    result = _db.insert_row(name, data)
    if result:
        _broadcast("row_inserted", {"entity": name})
        return jsonify(result), 201
    return jsonify({"error": "Insert failed"}), 500


@app.route("/api/entities/<name>/rows/<row_id>", methods=["DELETE"])
def delete_row(name, row_id):
    success = _db.delete_row(name, row_id)
    if success:
        _broadcast("row_deleted", {"entity": name, "id": row_id})
        return jsonify({"success": True})
    return jsonify({"error": "Row not found"}), 404


@app.route("/api/entities/<name>/reseed", methods=["POST"])
def reseed(name):
    ed = _state["entities"].get(name)
    if not ed:
        return jsonify({"error": "Entity not found"}), 404
    count = _db.reseed(ed)
    _broadcast("reseeded", {"entity": name, "rows": count})
    return jsonify({"success": True, "rows": count})


@app.route("/api/entities/<name>/schema", methods=["GET"])
def get_schema(name):
    sql = _db.get_schema_sql(name)
    return jsonify({"sql": sql, "entity": name})


@app.route("/api/entities/<name>", methods=["DELETE"])
def delete_entity(name):
    if name not in _state["entities"]:
        return jsonify({"error": "Entity not found"}), 404
    del _state["entities"][name]
    # Drop the SQLite db file for this entity
    db_path = _db.db_path(name)
    if os.path.exists(db_path):
        try:
            os.remove(db_path)
        except Exception:
            pass
    _broadcast("entity_removed", {"name": name})
    return jsonify({"success": True, "name": name})


@app.route("/api/browse-folder", methods=["GET"])
def browse_folder():
    """Open a native OS folder-picker dialog and return the chosen path."""
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.wm_attributes("-topmost", True)
        path = filedialog.askdirectory(title="Select C# project folder")
        root.destroy()
        if path:
            return jsonify({"path": os.path.normpath(path)})
        return jsonify({"path": None})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Live Servers ───────────────────────────────────────────────────────────

@app.route("/api/servers", methods=["GET"])
def list_servers():
    return jsonify(get_running_servers())


@app.route("/api/servers/<name>/start", methods=["POST"])
def server_start(name):
    ed = _state["entities"].get(name)
    if not ed:
        return jsonify({"error": "Entity not found"}), 404

    if is_running(name):
        info = get_running_servers()[name]
        return jsonify({"already_running": True, **info})

    info = start_server(name, _db, ed["properties"])
    _broadcast("server_started", {"entity": name, **info})
    return jsonify(info)


@app.route("/api/servers/<name>/stop", methods=["POST"])
def server_stop(name):
    success = stop_server(name)
    if success:
        _broadcast("server_stopped", {"entity": name})
        return jsonify({"success": True})
    return jsonify({"error": "Server not running"}), 400


# ── Infrastructure Generation ───────────────────────────────────────────────

@app.route("/api/infra/generate", methods=["POST"])
def gen_infra():
    data = request.get_json() or {}
    entity_names = data.get("entities", [])
    pattern = data.get("pattern", "repository")
    db = data.get("db_provider", "sqlite")

    if not entity_names:
        return jsonify({"error": "No entities specified"}), 400

    selected = [_state["entities"][n] for n in entity_names if n in _state["entities"]]
    if not selected:
        return jsonify({"error": "None of the specified entities found"}), 404

    global _last_infra
    files = generate_infra(selected, pattern, db)
    _last_infra = files
    return jsonify({"files": files, "pattern": pattern, "db": db, "count": len(files)})


@app.route("/api/infra/download-zip", methods=["GET"])
def download_infra_zip():
    if not _last_infra:
        return jsonify({"error": "No generated files available. Generate first."}), 404

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in _last_infra:
            zf.writestr(f["path"], f["code"])
    buf.seek(0)
    return send_file(
        buf,
        mimetype="application/zip",
        as_attachment=True,
        download_name="csforge_infra.zip",
    )


# ── Watch Log ─────────────────────────────────────────────────────────────

@app.route("/api/watch-log", methods=["GET"])
def watch_log():
    return jsonify(_state["watch_log"][-50:])


# ── Internals ─────────────────────────────────────────────────────────────

def _rescan_file(file_path: str) -> dict:
    """Re-parse a single file and update state."""
    try:
        entity = parse_cs_file(file_path)
        if entity and entity.properties:
            ed = entity.to_dict()
            _state["entities"][entity.name] = ed
            return ed
    except Exception:
        pass
    return None


def _on_file_change(file_path: str, event_type: str):
    """Called by file watcher when a .cs file changes."""
    log_entry = {
        "file": os.path.basename(file_path),
        "path": file_path,
        "event": event_type,
        "ts": time.strftime("%H:%M:%S"),
    }
    _state["watch_log"].insert(0, log_entry)
    _state["watch_log"] = _state["watch_log"][:100]

    if event_type == "deleted":
        # Remove entity if file deleted
        to_remove = [n for n, e in _state["entities"].items()
                     if e["file_path"] == file_path]
        for n in to_remove:
            del _state["entities"][n]
            _broadcast("entity_removed", {"name": n})
        return

    # Re-parse the file
    updated = _rescan_file(file_path)
    if updated:
        name = updated["name"]
        # Migrate DB if schema changed
        migration = _db.create_or_migrate_table(updated)

        _broadcast("entity_updated", {
            "name": name,
            "file": os.path.basename(file_path),
            "event": event_type,
            "migration": migration,
            "properties": len(updated["properties"]),
        })
    else:
        # File changed but couldn't parse — might be mid-edit
        _broadcast("file_changed", {
            "file": os.path.basename(file_path),
            "event": event_type,
        })


def _start_watcher(path: str):
    global _watcher
    if _watcher:
        _watcher.stop()
    _watcher = CSharpFileWatcher(_on_file_change)
    _watcher.start(path)


if __name__ == "__main__":
    # Auto-scan sample project on startup
    sample = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "sample_project")
    )
    if os.path.isdir(sample):
        entities = scan_directory(sample)
        if entities:
            _state["project_path"] = sample
            for entity in entities:
                ed = entity.to_dict()
                _state["entities"][ed["name"]] = ed
                _db.create_or_migrate_table(ed)
            _start_watcher(sample)
            print(f"[OK] Auto-loaded {len(entities)} entities from {sample}")
            print(f"[OK] Watching directory: {sample}")
            cs_files = [
                os.path.relpath(os.path.join(dp, f), sample)
                for dp, _, fnames in os.walk(sample)
                for f in fnames if f.endswith(".cs")
            ]
            for f in sorted(cs_files):
                print(f"[watch]   {f}")

    print("[OK] CSForge backend starting on http://localhost:7847")
    app.run(host="0.0.0.0", port=7847, debug=False, threaded=True)
