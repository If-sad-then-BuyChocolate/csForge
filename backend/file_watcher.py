"""
file_watcher.py — Real file system watcher using watchdog.
Detects .cs file changes and triggers rescan + DB migration.
"""

import os
import time
import threading
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler


class CSharpFileWatcher:
    def __init__(self, on_change_callback):
        self.observer = None
        self.on_change = on_change_callback
        self.watched_path = None
        self._debounce_timers = {}
        self._lock = threading.Lock()

    def start(self, path: str):
        self.stop()
        if not os.path.isdir(path):
            return False

        self.watched_path = path
        handler = _CSHandler(self._debounced_change)
        self.observer = Observer()
        self.observer.schedule(handler, path, recursive=True)
        self.observer.start()
        return True

    def stop(self):
        if self.observer:
            try:
                self.observer.stop()
                self.observer.join(timeout=2)
            except Exception:
                pass
            self.observer = None

    def _debounced_change(self, file_path: str, event_type: str):
        """Debounce rapid file changes (e.g. editor saves multiple events)."""
        with self._lock:
            if file_path in self._debounce_timers:
                self._debounce_timers[file_path].cancel()

            t = threading.Timer(
                0.5,
                self._fire_change,
                args=(file_path, event_type)
            )
            self._debounce_timers[file_path] = t
            t.start()

    def _fire_change(self, file_path: str, event_type: str):
        with self._lock:
            self._debounce_timers.pop(file_path, None)
        try:
            self.on_change(file_path, event_type)
        except Exception:
            pass

    @property
    def is_running(self):
        return self.observer is not None and self.observer.is_alive()


class _CSHandler(FileSystemEventHandler):
    def __init__(self, callback):
        self.callback = callback

    def _handle(self, event, event_type):
        if event.is_directory:
            return
        if not event.src_path.endswith(".cs"):
            return
        # Skip generated/migration files
        path = event.src_path
        skip_patterns = ["\\obj\\", "/obj/", "\\bin\\", "/bin/",
                         "Designer.cs", ".g.cs", "Migration"]
        if any(p in path for p in skip_patterns):
            return
        self.callback(path, event_type)

    def on_modified(self, event):
        self._handle(event, "modified")

    def on_created(self, event):
        self._handle(event, "created")

    def on_deleted(self, event):
        self._handle(event, "deleted")

    def on_moved(self, event):
        self._handle(event, "renamed")
