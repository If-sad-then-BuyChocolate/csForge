"""
db_engine.py — Real SQLite engine.
Creates tables from C# entity schemas, seeds 15 mock rows,
handles schema migration when models change.
"""

import sqlite3
import os
import uuid
import random
import string
from datetime import datetime, timedelta
from typing import Optional


# ── TYPE MAPPING ─────────────────────────────────────────────────────────

CSHARP_TO_SQLITE = {
    "Guid": "TEXT",
    "string": "TEXT",
    "String": "TEXT",
    "int": "INTEGER",
    "Int32": "INTEGER",
    "long": "INTEGER",
    "Int64": "INTEGER",
    "short": "INTEGER",
    "Int16": "INTEGER",
    "byte": "INTEGER",
    "bool": "INTEGER",
    "Boolean": "INTEGER",
    "float": "REAL",
    "Single": "REAL",
    "double": "REAL",
    "Double": "REAL",
    "decimal": "REAL",
    "Decimal": "REAL",
    "DateTime": "TEXT",
    "DateTimeOffset": "TEXT",
    "DateOnly": "TEXT",
    "TimeOnly": "TEXT",
    "TimeSpan": "TEXT",
    "byte[]": "BLOB",
    "object": "TEXT",
}

# ── MOCK DATA GENERATORS ──────────────────────────────────────────────────

FIRST_NAMES = ["James","Emma","Oliver","Sophia","Liam","Ava","Noah","Isabella",
               "William","Mia","Ethan","Amelia","Mason","Harper","Lucas"]
LAST_NAMES = ["Smith","Johnson","Williams","Brown","Jones","Garcia","Miller",
              "Davis","Wilson","Moore","Taylor","Anderson","Thomas","Jackson","White"]
COMPANIES = ["Acme Corp","Bright Labs","CoreTech","Dynamo Inc","EdgeSoft",
             "Fusion Ltd","GridWorks","HexaCo","IronPeak","JetStream",
             "KinoPlex","Lumio","MeshNet","NovaBuild","Orbit Systems"]
PRODUCTS = ["Pro Toolkit","Elite Package","Starter Kit","Advanced Suite",
            "Base Module","Core Bundle","Premium Set","Standard Pack",
            "Essential Box","Ultra Edition","Lite Version","Max Plan",
            "Mini Kit","Power Pack","Smart Set"]
ADDRESSES = ["12 Oak St, Austin TX 78701","88 Maple Ave, Denver CO 80201",
             "5 Pine Rd, Seattle WA 98101","201 Elm Blvd, Boston MA 02101",
             "44 Cedar Ln, Portland OR 97201","9 Birch Ct, Chicago IL 60601",
             "67 Walnut Dr, Miami FL 33101","130 Ash Way, Nashville TN 37201",
             "3 Spruce Pl, Phoenix AZ 85001","55 Willow St, Dallas TX 75201",
             "22 Fir Ave, Atlanta GA 30301","99 Poplar Rd, New York NY 10001",
             "7 Cypress Ct, Los Angeles CA 90001","18 Alder Blvd, Las Vegas NV 89101",
             "36 Redwood Dr, San Francisco CA 94101"]
STATUSES = ["Active","Inactive","Pending","Processing","Shipped",
            "Delivered","Cancelled","Suspended","Archived","Draft"]
TITLES = ["Enterprise Deal","Strategic Partnership","Q4 Expansion",
          "Product License","Support Contract","Platform Access",
          "Consulting Agreement","SLA Package","API Integration",
          "Annual Subscription"]


def _mock_value(prop_name: str, prop_type: str, index: int, entity_name: str) -> any:
    """Generate a realistic mock value for a property."""
    n = prop_name.lower()
    t = prop_type.rstrip("?")
    idx = index % 15

    # Guid
    if t == "Guid":
        return str(uuid.uuid4())

    # Boolean
    if t in ("bool", "Boolean"):
        return 1 if idx % 4 != 0 else 0

    # Integer
    if t in ("int", "Int32", "long", "Int64", "short", "Int16", "byte"):
        if any(x in n for x in ("quantity", "stock", "count", "amount")):
            return random.randint(1, 500)
        if "age" in n:
            return random.randint(18, 65)
        if "year" in n:
            return random.randint(2018, 2024)
        if "port" in n:
            return random.randint(3000, 9000)
        return random.randint(1, 200)

    # Float/decimal
    if t in ("decimal", "Decimal", "float", "double", "Double", "Single"):
        if any(x in n for x in ("price", "amount", "cost", "value", "total", "balance")):
            return round(random.uniform(9.99, 999.99), 2)
        if any(x in n for x in ("rate", "percent", "ratio")):
            return round(random.uniform(0.01, 1.0), 4)
        return round(random.uniform(1.0, 500.0), 2)

    # DateTime
    if t in ("DateTime", "DateTimeOffset", "DateOnly"):
        base = datetime.now() - timedelta(days=random.randint(1, 730))
        if t == "DateOnly":
            return base.strftime("%Y-%m-%d")
        return base.strftime("%Y-%m-%dT%H:%M:%S")

    # String — context-aware
    if t in ("string", "String"):
        if any(x in n for x in ("email", "mail")):
            return f"{FIRST_NAMES[idx].lower()}.{LAST_NAMES[idx].lower()}@example.com"
        if "firstname" in n or n == "first":
            return FIRST_NAMES[idx]
        if "lastname" in n or n == "last":
            return LAST_NAMES[idx]
        if "fullname" in n or "displayname" in n:
            return f"{FIRST_NAMES[idx]} {LAST_NAMES[idx]}"
        if "company" in n or "organization" in n or "org" in n:
            return COMPANIES[idx]
        if "phone" in n or "mobile" in n or "tel" in n:
            return f"+1 ({500+idx}) {random.randint(100,999)}-{random.randint(1000,9999)}"
        if "address" in n:
            return ADDRESSES[idx]
        if "title" in n or "subject" in n:
            return TITLES[idx % len(TITLES)]
        if "description" in n or "notes" in n or "comment" in n:
            return f"Auto-generated {entity_name.lower()} record {idx + 1}."
        if "sku" in n or "code" in n:
            return f"SKU-{chr(65 + idx % 26)}{1000 + idx}"
        if "url" in n or "uri" in n or "link" in n:
            return f"https://example.com/{entity_name.lower()}/{idx + 1}"
        if "color" in n or "colour" in n:
            return random.choice(["Red","Blue","Green","Black","White","Grey","Navy"])
        if "status" in n or "state" in n:
            return STATUSES[idx % len(STATUSES)]
        if "name" in n:
            if entity_name == "Product":
                return PRODUCTS[idx]
            return f"{FIRST_NAMES[idx]} {LAST_NAMES[idx]}"
        if "slug" in n:
            return f"{entity_name.lower()}-{idx + 1}"
        if "token" in n or "key" in n or "secret" in n:
            return "".join(random.choices(string.ascii_letters + string.digits, k=32))
        return f"{entity_name}_{str(idx + 1).zfill(3)}"

    return None


# ── DATABASE MANAGEMENT ──────────────────────────────────────────────────

class EntityDatabase:
    def __init__(self, db_dir: str):
        self.db_dir = db_dir
        os.makedirs(db_dir, exist_ok=True)

    def db_path(self, entity_name: str) -> str:
        return os.path.join(self.db_dir, f"{entity_name}.db")

    def get_connection(self, entity_name: str) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path(entity_name))
        conn.row_factory = sqlite3.Row
        return conn

    def _sqlite_type(self, cs_type: str) -> str:
        return CSHARP_TO_SQLITE.get(cs_type.rstrip("?"), "TEXT")

    def create_or_migrate_table(self, entity) -> dict:
        """Create table if not exists, or migrate if schema changed."""
        entity_name = entity["name"]
        props = entity["properties"]

        conn = self.get_connection(entity_name)
        cursor = conn.cursor()

        # Check if table exists
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (entity_name,)
        )
        table_exists = cursor.fetchone() is not None

        if table_exists:
            # Get existing columns
            cursor.execute(f'PRAGMA table_info("{entity_name}")')
            existing_cols = {row["name"]: row["type"] for row in cursor.fetchall()}
            model_cols = {p["name"]: self._sqlite_type(p["type"]) for p in props}

            added = []
            removed = []

            # Detect removed columns
            for col_name in existing_cols:
                if col_name not in model_cols:
                    removed.append(col_name)

            if removed:
                # SQLite requires a full table rebuild to drop columns.
                # Create new table, copy surviving columns, drop old, rename.
                col_defs = []
                for p in props:
                    sqlite_type = self._sqlite_type(p["type"])
                    null_part = "" if p["required"] and not p["nullable"] else " "
                    col_defs.append(f'    "{p["name"]}" {sqlite_type}{null_part}')

                temp_name = f"{entity_name}__new"
                ddl = (
                    f'CREATE TABLE "{temp_name}" (\n'
                    + ",\n".join(col_defs)
                    + "\n)"
                )
                cursor.execute(ddl)

                # Copy only the columns that survive in the new model
                surviving = [c for c in model_cols if c in existing_cols]
                if surviving:
                    cols_str = ", ".join([f'"{c}"' for c in surviving])
                    cursor.execute(
                        f'INSERT INTO "{temp_name}" ({cols_str}) '
                        f'SELECT {cols_str} FROM "{entity_name}"'
                    )

                cursor.execute(f'DROP TABLE "{entity_name}"')
                cursor.execute(f'ALTER TABLE "{temp_name}" RENAME TO "{entity_name}"')
            else:
                # Add new columns
                for col_name, col_type in model_cols.items():
                    if col_name not in existing_cols:
                        cursor.execute(
                            f'ALTER TABLE "{entity_name}" ADD COLUMN "{col_name}" {col_type}'
                        )
                        added.append(col_name)

            conn.commit()
            conn.close()

            return {
                "action": "migrated",
                "added_columns": added,
                "removed_columns": removed,
            }
        else:
            # Create table
            col_defs = []
            for p in props:
                sqlite_type = self._sqlite_type(p["type"])
                null_part = "" if p["required"] and not p["nullable"] else " "
                col_defs.append(f'    "{p["name"]}" {sqlite_type}{null_part}')

            ddl = (
                f'CREATE TABLE IF NOT EXISTS "{entity_name}" (\n'
                + ",\n".join(col_defs)
                + "\n)"
            )
            cursor.execute(ddl)
            conn.commit()
            conn.close()

            # Seed with mock data
            self.seed_table(entity)
            return {"action": "created"}

    def seed_table(self, entity, count: int = 15) -> int:
        """Insert mock rows into the table."""
        entity_name = entity["name"]
        props = entity["properties"]

        if not props:
            return 0

        conn = self.get_connection(entity_name)
        cursor = conn.cursor()

        # Clear existing data
        cursor.execute(f'DELETE FROM "{entity_name}"')

        col_names = [p["name"] for p in props]
        placeholders = ", ".join(["?" for _ in col_names])
        cols_str = ", ".join([f'"{c}"' for c in col_names])
        insert_sql = f'INSERT INTO "{entity_name}" ({cols_str}) VALUES ({placeholders})'

        rows_inserted = 0
        for i in range(count):
            values = [
                _mock_value(p["name"], p["type"], i, entity_name)
                for p in props
            ]
            try:
                cursor.execute(insert_sql, values)
                rows_inserted += 1
            except Exception as e:
                pass

        conn.commit()
        conn.close()
        return rows_inserted

    def get_all_rows(self, entity_name: str) -> list:
        """Return all rows from an entity table."""
        db_path = self.db_path(entity_name)
        if not os.path.exists(db_path):
            return []
        try:
            conn = self.get_connection(entity_name)
            cursor = conn.cursor()
            cursor.execute(f'SELECT * FROM "{entity_name}"')
            rows = [dict(row) for row in cursor.fetchall()]
            conn.close()
            return rows
        except Exception:
            return []

    def get_row_by_id(self, entity_name: str, row_id: str) -> Optional[dict]:
        """Get a single row by ID field."""
        try:
            conn = self.get_connection(entity_name)
            cursor = conn.cursor()
            # Try common ID column names
            for id_col in ("Id", "id", "ID"):
                try:
                    cursor.execute(
                        f'SELECT * FROM "{entity_name}" WHERE "{id_col}" = ?',
                        (row_id,)
                    )
                    row = cursor.fetchone()
                    if row:
                        conn.close()
                        return dict(row)
                except Exception:
                    continue
            conn.close()
            return None
        except Exception:
            return None

    def update_row(self, entity_name: str, row_id: str, data: dict) -> bool:
        """Update a row's data."""
        try:
            conn = self.get_connection(entity_name)
            cursor = conn.cursor()

            set_parts = [f'"{k}" = ?' for k in data.keys()]
            values = list(data.values())

            for id_col in ("Id", "id", "ID"):
                try:
                    cursor.execute(
                        f'UPDATE "{entity_name}" SET {", ".join(set_parts)} WHERE "{id_col}" = ?',
                        values + [row_id]
                    )
                    if cursor.rowcount > 0:
                        conn.commit()
                        conn.close()
                        return True
                except Exception:
                    continue

            conn.close()
            return False
        except Exception:
            return False

    def insert_row(self, entity_name: str, data: dict) -> dict:
        """Insert a new row."""
        try:
            conn = self.get_connection(entity_name)
            cursor = conn.cursor()

            # Auto-set Guid Id if present
            if "Id" in data and not data["Id"]:
                data["Id"] = str(uuid.uuid4())

            col_names = list(data.keys())
            placeholders = ", ".join(["?" for _ in col_names])
            cols_str = ", ".join([f'"{c}"' for c in col_names])
            cursor.execute(
                f'INSERT INTO "{entity_name}" ({cols_str}) VALUES ({placeholders})',
                list(data.values())
            )
            conn.commit()
            conn.close()
            return data
        except Exception as e:
            return {}

    def delete_row(self, entity_name: str, row_id: str) -> bool:
        """Delete a row by ID."""
        try:
            conn = self.get_connection(entity_name)
            cursor = conn.cursor()
            for id_col in ("Id", "id", "ID"):
                try:
                    cursor.execute(
                        f'DELETE FROM "{entity_name}" WHERE "{id_col}" = ?',
                        (row_id,)
                    )
                    if cursor.rowcount > 0:
                        conn.commit()
                        conn.close()
                        return True
                except Exception:
                    continue
            conn.close()
            return False
        except Exception:
            return False

    def reseed(self, entity) -> int:
        """Re-seed an entity table with fresh mock data."""
        entity_name = entity["name"]
        conn = self.get_connection(entity_name)
        cursor = conn.cursor()
        cursor.execute(f'DELETE FROM "{entity_name}"')
        conn.commit()
        conn.close()
        return self.seed_table(entity)

    def get_schema_sql(self, entity_name: str) -> str:
        """Return the CREATE TABLE SQL for an entity."""
        try:
            conn = self.get_connection(entity_name)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                (entity_name,)
            )
            row = cursor.fetchone()
            conn.close()
            return row["sql"] if row else ""
        except Exception:
            return ""
