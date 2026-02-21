# CSForge

**C# Entity Explorer — Two-Way Sync · Live Mock APIs · Infrastructure Generator**

Point CSForge at any C# project folder. It parses your entity classes using a real
syntax tree, seeds SQLite databases with mock data, spins up live REST servers per
entity, and lets you edit your model directly in the UI — every change writes back
to the `.cs` file on disk instantly.

<img width="1910" height="943" alt="image" src="https://github.com/user-attachments/assets/25dc14df-204b-49d1-a39e-ec0b9ce18a89" />

---

## Demo

> Point CSForge at `sample_project/` right after cloning to see it in action immediately.

---

## Quick Start

**Requirements:** Python 3.10+

```bash
# 1. Install dependencies
pip install flask watchdog tree-sitter tree-sitter-languages

# 2. Run
python start.py
```

Opens automatically at `http://localhost:7848`.

---

## Features

### 🔍 Models Tab
<img width="1654" height="489" alt="image" src="https://github.com/user-attachments/assets/7aef583d-4ca5-4d5c-b71d-28b4d690095e" />

- Scans any C# project folder recursively for entity classes
- Displays all properties with type, nullability, and required status
- **Two-way sync** — rename a property, change its type, or toggle nullable in the UI and the `.cs` file updates on disk immediately
- Add new properties via the inline form — appended directly to the source file
- Delete properties — removed from the source file
- Collapsible SQLite data table with 15 auto-seeded rows per entity

### ⚡ Live Servers Tab
<img width="1653" height="869" alt="image" src="https://github.com/user-attachments/assets/7fdf4455-bf0f-4235-8ae0-4f5eddbf64c0" />

- Spin up a real HTTP REST server per entity with one click
- Each server gets its own port (starting at 5100)
- Full CRUD: `GET`, `POST`, `PUT`, `DELETE`
- CORS enabled — works with Postman, curl, or any frontend
- OpenAPI / Swagger spec at `/swagger`
- Health check at `/health`
- Live response preview built into the UI

### 🏗️ Infrastructure Generator Tab
<img width="1652" height="589" alt="image" src="https://github.com/user-attachments/assets/2bf5ab9a-a067-4ee7-a889-42eaa6f471c7" />

| Pattern | Generated files |
|---|---|
| **Repository** | Interface + Repository + Service + Controller + DbContext + DI wiring |
| **CQRS / MediatR** | Query/Command records, typed Handlers, MediatR dispatch |
| **Minimal API** | Endpoint groups, no controllers, lean `Program.cs` |
| **Clean Architecture** | Domain · Application · Infrastructure · Presentation layers |

Supports SQLite, SQL Server, PostgreSQL, and MongoDB targets.
Download the entire generated structure as a `.zip`.
<img width="1641" height="580" alt="image" src="https://github.com/user-attachments/assets/042da6ef-6d22-45ed-9f0c-2d964e22decb" />

### 👁️ File Watcher
- Watches the project directory for any `.cs` file saves
- Re-parses the file and migrates the SQLite schema automatically
- New columns added; removed columns flagged
- Real-time SSE pushes changes to the UI without a page reload

---

## How Two-Way Sync Works

The parser is backed by **tree-sitter** (a real C# concrete syntax tree), so edits
target exact byte ranges in the file rather than pattern-matching text.

| UI Action | `.cs` file change |
|---|---|
| Rename property | `public string Name` → `public string NewName` |
| Change type | `public string X` → `public int X` |
| Toggle nullable | `public string X` → `public string? X` |
| Add property | New `public T Name { get; set; }` line appended |
| Delete property | Property line (+ XML doc + attributes) removed |

---

## Project Structure

```
csforge/
├── start.py                  # Launcher — the only file you need to run
├── backend/
│   ├── app.py                # Flask API server
│   ├── cs_parser.py          # tree-sitter C# parser + two-way file sync
│   ├── db_engine.py          # SQLite table management + mock data seeding
│   ├── live_server.py        # Per-entity HTTP REST server engine
│   ├── file_watcher.py       # watchdog .cs file watcher + SSE broadcast
│   └── infra_gen.py          # Infrastructure code generator (4 patterns)
├── frontend/
│   └── index.html            # Full React UI — single file, no build step
├── sample_project/
│   └── Models/               # Three ready-to-use example entities
└── databases/                # Auto-created at runtime — one .db per entity
```

---

## Scanning Your Own Project

Enter the path to your C# project folder in the sidebar and click **Scan Project**:

```
C:\Projects\MyApi\src\MyApi.Domain\Entities
/home/user/projects/MyApi/Models
```

The scanner walks the directory recursively, skipping `bin/`, `obj/`, `Migrations/`,
and other non-entity folders. Any `.cs` file containing a `public class` with
`{ get; set; }` properties is treated as an entity.

---

## Live Server curl Examples

Once a server is running (e.g. `Product` on port 5100):

```bash
# List all
curl http://localhost:5100/api/products

# Get one
curl http://localhost:5100/api/products/<guid>

# Create
curl -X POST http://localhost:5100/api/products \
  -H "Content-Type: application/json" \
  -d '{"Name":"Widget","Price":9.99}'

# Update
curl -X PUT http://localhost:5100/api/products/<guid> \
  -H "Content-Type: application/json" \
  -d '{"Name":"Widget Pro","Price":19.99}'

# Delete
curl -X DELETE http://localhost:5100/api/products/<guid>

# OpenAPI spec
curl http://localhost:5100/swagger
```

---

## Backend API Reference

The backend runs on `localhost:7847`:

```
POST   /api/project/scan                        Scan a directory
GET    /api/entities                            List loaded entities
GET    /api/entities/{name}/rows                SQLite rows for an entity

POST   /api/entities/{name}/properties/rename   Rename a property
POST   /api/entities/{name}/properties/type     Change property type
POST   /api/entities/{name}/properties/nullable Toggle nullable
POST   /api/entities/{name}/properties/add      Add a property
DELETE /api/entities/{name}/properties/{prop}   Remove a property

POST   /api/entities/{name}/reseed              Re-seed with fresh mock data
POST   /api/servers/{name}/start                Start a live REST server
POST   /api/servers/{name}/stop                 Stop a live REST server
POST   /api/infra/generate                      Generate infrastructure code
GET    /api/infra/download                      Download generated code as .zip
GET    /api/events                              SSE stream for real-time updates
```

---

## License

MIT
