"""
infra_gen.py — Generates real C# infrastructure code from entity schemas.
Supports: Repository, CQRS/MediatR, Minimal API, Clean Architecture.
"""


def _props_to_cs_class(entity: dict) -> str:
    """Regenerate C# class properties string for reference."""
    lines = []
    for p in entity["properties"]:
        null = "?" if p["nullable"] else ""
        req = "required " if p["required"] and not p["nullable"] else ""
        lines.append(f"        public {req}{p['type']}{null} {p['name']} {{ get; set; }}")
    return "\n".join(lines)


# ── DB PROVIDER HELPERS ───────────────────────────────────────────────────

def _ef_db_line(db: str) -> str:
    """Return the EF Core provider opt.Use*() call for the given db."""
    if db == "sqlserver":
        return ('opt.UseSqlServer(builder.Configuration.GetConnectionString("DefaultConnection")\n'
                '        ?? "Server=(localdb)\\\\mssqllocaldb;Database=AppDb;Trusted_Connection=True")')
    if db == "postgres":
        return ('opt.UseNpgsql(builder.Configuration.GetConnectionString("DefaultConnection")\n'
                '        ?? "Host=localhost;Database=AppDb;Username=postgres;Password=postgres")')
    return 'opt.UseSqlite("Data Source=app.db")'


def _ef_pkg(db: str) -> str:
    if db == "sqlserver":
        return "// dotnet add package Microsoft.EntityFrameworkCore.SqlServer"
    if db == "postgres":
        return "// dotnet add package Npgsql.EntityFrameworkCore.PostgreSQL"
    return "// dotnet add package Microsoft.EntityFrameworkCore.Sqlite"


def generate(entities: list, pattern: str, db: str = "sqlite") -> list:
    """
    Generate infrastructure files for the given entities and pattern.
    Returns list of {label, path, code} dicts.
    db: "sqlite" | "sqlserver" | "postgres" | "mongo"
    """
    if pattern == "repository":
        return _repository(entities, db)
    elif pattern == "cqrs":
        return _cqrs(entities, db)
    elif pattern == "minimal":
        return _minimal_api(entities, db)
    elif pattern == "clean":
        return _clean_architecture(entities, db)
    return []


# ── REPOSITORY PATTERN ────────────────────────────────────────────────────

def _repository(entities: list, db: str = "sqlite") -> list:
    tabs = []
    for e in entities:
        ns = e["namespace"] or "Application"
        name = e["name"]
        tabs.append({
            "label": f"I{name}Repository.cs",
            "path": f"Infrastructure/Repositories/Interfaces/I{name}Repository.cs",
            "code": _repo_interface(e, ns),
        })
        tabs.append({
            "label": f"{name}Repository.cs",
            "path": f"Infrastructure/Repositories/{name}Repository.cs",
            "code": _mongo_repo_impl(e, ns) if db == "mongo" else _repo_impl(e, ns),
        })
        tabs.append({
            "label": f"{name}Service.cs",
            "path": f"Application/Services/{name}Service.cs",
            "code": _service(e, ns),
        })
        tabs.append({
            "label": f"{name}sController.cs",
            "path": f"API/Controllers/{name}sController.cs",
            "code": _controller(e, ns),
        })
    _ns = entities[0]["namespace"] if entities else "Application"
    if db == "mongo":
        tabs.append({
            "label": "MongoDbContext.cs",
            "path": "Infrastructure/Persistence/MongoDbContext.cs",
            "code": _mongo_dbcontext(entities, _ns),
        })
        tabs.append({
            "label": "Program.cs",
            "path": "Program.cs",
            "code": _mongo_repo_program(entities),
        })
    else:
        tabs.append({
            "label": "AppDbContext.cs",
            "path": "Infrastructure/Persistence/AppDbContext.cs",
            "code": _dbcontext(entities, _ns),
        })
        tabs.append({
            "label": "Program.cs",
            "path": "Program.cs",
            "code": _repo_program(entities, db),
        })
    return tabs


def _repo_interface(e: dict, ns: str) -> str:
    name = e["name"]
    return f"""using System;
using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;

namespace {ns}.Infrastructure.Repositories.Interfaces;

public interface I{name}Repository
{{
    Task<IReadOnlyList<{name}>> GetAllAsync(CancellationToken ct = default);
    Task<{name}?> GetByIdAsync(Guid id, CancellationToken ct = default);
    Task<{name}> CreateAsync({name} entity, CancellationToken ct = default);
    Task<{name}> UpdateAsync({name} entity, CancellationToken ct = default);
    Task DeleteAsync(Guid id, CancellationToken ct = default);
    Task<bool> ExistsAsync(Guid id, CancellationToken ct = default);
}}
"""


def _repo_impl(e: dict, ns: str) -> str:
    name = e["name"]
    return f"""using Microsoft.EntityFrameworkCore;
using {ns}.Infrastructure.Repositories.Interfaces;

namespace {ns}.Infrastructure.Repositories;

public sealed class {name}Repository(AppDbContext ctx) : I{name}Repository
{{
    public async Task<IReadOnlyList<{name}>> GetAllAsync(CancellationToken ct = default)
        => await ctx.{name}s.AsNoTracking().ToListAsync(ct);

    public async Task<{name}?> GetByIdAsync(Guid id, CancellationToken ct = default)
        => await ctx.{name}s.AsNoTracking().FirstOrDefaultAsync(x => x.Id == id, ct);

    public async Task<{name}> CreateAsync({name} entity, CancellationToken ct = default)
    {{
        entity.Id = Guid.NewGuid();
        ctx.{name}s.Add(entity);
        await ctx.SaveChangesAsync(ct);
        return entity;
    }}

    public async Task<{name}> UpdateAsync({name} entity, CancellationToken ct = default)
    {{
        ctx.{name}s.Update(entity);
        await ctx.SaveChangesAsync(ct);
        return entity;
    }}

    public async Task DeleteAsync(Guid id, CancellationToken ct = default)
        => await ctx.{name}s.Where(x => x.Id == id).ExecuteDeleteAsync(ct);

    public async Task<bool> ExistsAsync(Guid id, CancellationToken ct = default)
        => await ctx.{name}s.AnyAsync(x => x.Id == id, ct);
}}
"""


def _service(e: dict, ns: str) -> str:
    name = e["name"]
    return f"""using {ns}.Infrastructure.Repositories.Interfaces;

namespace {ns}.Application.Services;

public sealed class {name}Service(
    I{name}Repository repo,
    ILogger<{name}Service> logger)
{{
    public async Task<IReadOnlyList<{name}>> GetAllAsync(CancellationToken ct = default)
    {{
        logger.LogInformation("[{{Service}}] GetAll", nameof({name}Service));
        return await repo.GetAllAsync(ct);
    }}

    public async Task<{name}> GetByIdOrThrowAsync(Guid id, CancellationToken ct = default)
    {{
        var entity = await repo.GetByIdAsync(id, ct);
        return entity ?? throw new KeyNotFoundException($"{name} {{id}} not found");
    }}

    public async Task<{name}> CreateAsync({name} entity, CancellationToken ct = default)
    {{
        logger.LogInformation("[{{Service}}] Create", nameof({name}Service));
        return await repo.CreateAsync(entity, ct);
    }}

    public async Task<{name}> UpdateAsync({name} entity, CancellationToken ct = default)
    {{
        if (!await repo.ExistsAsync(entity.Id, ct))
            throw new KeyNotFoundException($"{name} {{entity.Id}} not found");
        return await repo.UpdateAsync(entity, ct);
    }}

    public async Task DeleteAsync(Guid id, CancellationToken ct = default)
        => await repo.DeleteAsync(id, ct);
}}
"""


def _controller(e: dict, ns: str) -> str:
    name = e["name"]
    plural = name + "s"
    return f"""using Microsoft.AspNetCore.Mvc;
using {ns}.Application.Services;

namespace {ns}.API.Controllers;

[ApiController]
[Route("api/[controller]")]
public sealed class {plural}Controller({name}Service svc) : ControllerBase
{{
    [HttpGet]
    public async Task<IActionResult> GetAll(CancellationToken ct)
        => Ok(await svc.GetAllAsync(ct));

    [HttpGet("{{id:guid}}")]
    public async Task<IActionResult> GetById(Guid id, CancellationToken ct)
    {{
        try {{ return Ok(await svc.GetByIdOrThrowAsync(id, ct)); }}
        catch (KeyNotFoundException) {{ return NotFound(); }}
    }}

    [HttpPost]
    public async Task<IActionResult> Create({name} body, CancellationToken ct)
    {{
        var created = await svc.CreateAsync(body, ct);
        return CreatedAtAction(nameof(GetById), new {{ id = created.Id }}, created);
    }}

    [HttpPut("{{id:guid}}")]
    public async Task<IActionResult> Update(Guid id, {name} body, CancellationToken ct)
    {{
        body.Id = id;
        try {{ return Ok(await svc.UpdateAsync(body, ct)); }}
        catch (KeyNotFoundException) {{ return NotFound(); }}
    }}

    [HttpDelete("{{id:guid}}")]
    public async Task<IActionResult> Delete(Guid id, CancellationToken ct)
    {{
        await svc.DeleteAsync(id, ct);
        return NoContent();
    }}
}}
"""


def _dbcontext(entities: list, ns: str) -> str:
    sets = "\n".join(
        f"    public DbSet<{e['name']}> {e['name']}s {{ get; init; }} = null!;"
        for e in entities
    )
    configs = "\n".join(
        f"        builder.Entity<{e['name']}>().HasKey(x => x.Id);"
        for e in entities
    )
    return f"""using Microsoft.EntityFrameworkCore;

namespace {ns}.Infrastructure.Persistence;

public sealed class AppDbContext(DbContextOptions<AppDbContext> options) : DbContext(options)
{{
{sets}

    protected override void OnModelCreating(ModelBuilder builder)
    {{
        base.OnModelCreating(builder);
{configs}
    }}
}}
"""


def _repo_program(entities: list, db: str = "sqlite") -> str:
    ns = entities[0]["namespace"] if entities else "Application"
    repos = "\n".join(
        f"builder.Services.AddScoped<I{e['name']}Repository, {e['name']}Repository>();\n"
        f"builder.Services.AddScoped<{e['name']}Service>();"
        for e in entities
    )
    return f"""{_ef_pkg(db)}
using Microsoft.EntityFrameworkCore;
using {ns}.Infrastructure.Persistence;
using {ns}.Infrastructure.Repositories;
using {ns}.Infrastructure.Repositories.Interfaces;
using {ns}.Application.Services;

var builder = WebApplication.CreateBuilder(args);

builder.Services.AddDbContext<AppDbContext>(opt =>
    {_ef_db_line(db)});

{repos}

builder.Services.AddControllers();
builder.Services.AddEndpointsApiExplorer();
builder.Services.AddSwaggerGen();

var app = builder.Build();

using (var scope = app.Services.CreateScope())
    await scope.ServiceProvider.GetRequiredService<AppDbContext>().Database.MigrateAsync();

app.UseSwagger();
app.UseSwaggerUI();
app.MapControllers();
app.Run();
"""


# ── CQRS / MEDIATR ────────────────────────────────────────────────────────

def _cqrs(entities: list, db: str = "sqlite") -> list:
    tabs = []
    for e in entities:
        ns = e["namespace"] or "Application"
        name = e["name"]
        tabs.append({
            "label": f"{name}Queries.cs",
            "path": f"Application/{name}/Queries/{name}Queries.cs",
            "code": _mongo_cqrs_queries(e, ns) if db == "mongo" else _cqrs_queries(e, ns),
        })
        tabs.append({
            "label": f"{name}Commands.cs",
            "path": f"Application/{name}/Commands/{name}Commands.cs",
            "code": _mongo_cqrs_commands(e, ns) if db == "mongo" else _cqrs_commands(e, ns),
        })
        tabs.append({
            "label": f"{name}sController.cs",
            "path": f"API/Controllers/{name}sController.cs",
            "code": _cqrs_controller(e, ns),
        })
    _ns = entities[0]["namespace"] if entities else "Application"
    if db == "mongo":
        tabs.append({
            "label": "MongoDbContext.cs",
            "path": "Infrastructure/Persistence/MongoDbContext.cs",
            "code": _mongo_dbcontext(entities, _ns),
        })
        tabs.append({
            "label": "Program.cs",
            "path": "Program.cs",
            "code": _mongo_cqrs_program(entities),
        })
    else:
        tabs.append({
            "label": "AppDbContext.cs",
            "path": "Infrastructure/Persistence/AppDbContext.cs",
            "code": _dbcontext(entities, _ns),
        })
        tabs.append({
            "label": "Program.cs",
            "path": "Program.cs",
            "code": _cqrs_program(entities, db),
        })
    return tabs


def _cqrs_queries(e: dict, ns: str) -> str:
    name = e["name"]
    return f"""using MediatR;
using Microsoft.EntityFrameworkCore;
using {ns}.Infrastructure.Persistence;

namespace {ns}.Application.{name}s.Queries;

// ── Get All ──────────────────────────────────────────────────────────────
public sealed record GetAll{name}sQuery : IRequest<IReadOnlyList<{name}>>;

public sealed class GetAll{name}sHandler(AppDbContext ctx)
    : IRequestHandler<GetAll{name}sQuery, IReadOnlyList<{name}>>
{{
    public async Task<IReadOnlyList<{name}>> Handle(
        GetAll{name}sQuery request, CancellationToken ct)
        => await ctx.{name}s.AsNoTracking().ToListAsync(ct);
}}

// ── Get By Id ─────────────────────────────────────────────────────────────
public sealed record Get{name}ByIdQuery(Guid Id) : IRequest<{name}?>;

public sealed class Get{name}ByIdHandler(AppDbContext ctx)
    : IRequestHandler<Get{name}ByIdQuery, {name}?>
{{
    public async Task<{name}?> Handle(
        Get{name}ByIdQuery request, CancellationToken ct)
        => await ctx.{name}s.FindAsync([request.Id], ct);
}}
"""


def _cqrs_commands(e: dict, ns: str) -> str:
    name = e["name"]
    return f"""using MediatR;
using Microsoft.EntityFrameworkCore;
using {ns}.Infrastructure.Persistence;

namespace {ns}.Application.{name}s.Commands;

// ── Create ────────────────────────────────────────────────────────────────
public sealed record Create{name}Command({name} Payload) : IRequest<{name}>;

public sealed class Create{name}Handler(AppDbContext ctx)
    : IRequestHandler<Create{name}Command, {name}>
{{
    public async Task<{name}> Handle(Create{name}Command request, CancellationToken ct)
    {{
        var entity = request.Payload with {{ Id = Guid.NewGuid() }};
        ctx.{name}s.Add(entity);
        await ctx.SaveChangesAsync(ct);
        return entity;
    }}
}}

// ── Update ────────────────────────────────────────────────────────────────
public sealed record Update{name}Command(Guid Id, {name} Payload) : IRequest<{name}>;

public sealed class Update{name}Handler(AppDbContext ctx)
    : IRequestHandler<Update{name}Command, {name}>
{{
    public async Task<{name}> Handle(Update{name}Command request, CancellationToken ct)
    {{
        var entity = request.Payload with {{ Id = request.Id }};
        ctx.{name}s.Update(entity);
        await ctx.SaveChangesAsync(ct);
        return entity;
    }}
}}

// ── Delete ────────────────────────────────────────────────────────────────
public sealed record Delete{name}Command(Guid Id) : IRequest;

public sealed class Delete{name}Handler(AppDbContext ctx)
    : IRequestHandler<Delete{name}Command>
{{
    public async Task Handle(Delete{name}Command request, CancellationToken ct)
        => await ctx.{name}s.Where(x => x.Id == request.Id).ExecuteDeleteAsync(ct);
}}
"""


def _cqrs_controller(e: dict, ns: str) -> str:
    name = e["name"]
    return f"""using MediatR;
using Microsoft.AspNetCore.Mvc;
using {ns}.Application.{name}s.Queries;
using {ns}.Application.{name}s.Commands;

namespace {ns}.API.Controllers;

[ApiController]
[Route("api/[controller]")]
public sealed class {name}sController(IMediator mediator) : ControllerBase
{{
    [HttpGet]
    public async Task<IActionResult> GetAll(CancellationToken ct)
        => Ok(await mediator.Send(new GetAll{name}sQuery(), ct));

    [HttpGet("{{id:guid}}")]
    public async Task<IActionResult> GetById(Guid id, CancellationToken ct)
    {{
        var result = await mediator.Send(new Get{name}ByIdQuery(id), ct);
        return result is null ? NotFound() : Ok(result);
    }}

    [HttpPost]
    public async Task<IActionResult> Create({name} body, CancellationToken ct)
    {{
        var created = await mediator.Send(new Create{name}Command(body), ct);
        return CreatedAtAction(nameof(GetById), new {{ id = created.Id }}, created);
    }}

    [HttpPut("{{id:guid}}")]
    public async Task<IActionResult> Update(Guid id, {name} body, CancellationToken ct)
        => Ok(await mediator.Send(new Update{name}Command(id, body), ct));

    [HttpDelete("{{id:guid}}")]
    public async Task<IActionResult> Delete(Guid id, CancellationToken ct)
    {{
        await mediator.Send(new Delete{name}Command(id), ct);
        return NoContent();
    }}
}}
"""


def _cqrs_program(entities: list, db: str = "sqlite") -> str:
    ns = entities[0]["namespace"] if entities else "Application"
    return f"""{_ef_pkg(db)}
using MediatR;
using Microsoft.EntityFrameworkCore;
using {ns}.Infrastructure.Persistence;

var builder = WebApplication.CreateBuilder(args);

builder.Services.AddDbContext<AppDbContext>(opt =>
    {_ef_db_line(db)});

builder.Services.AddMediatR(cfg =>
    cfg.RegisterServicesFromAssembly(typeof(Program).Assembly));

builder.Services.AddControllers();
builder.Services.AddEndpointsApiExplorer();
builder.Services.AddSwaggerGen();

var app = builder.Build();
app.UseSwagger();
app.UseSwaggerUI();
app.MapControllers();
app.Run();
"""


# ── MINIMAL API ──────────────────────────────────────────────────────────

def _minimal_api(entities: list, db: str = "sqlite") -> list:
    tabs = []
    ns = entities[0]["namespace"] if entities else "Application"
    for e in entities:
        tabs.append({
            "label": f"{e['name']}Endpoints.cs",
            "path": f"API/Endpoints/{e['name']}Endpoints.cs",
            "code": _minimal_endpoints(e, ns),
        })
        tabs.append({
            "label": f"{e['name']}Repository.cs",
            "path": f"Infrastructure/{e['name']}Repository.cs",
            "code": _mongo_minimal_repo(e, ns) if db == "mongo" else _minimal_repo(e, ns),
        })
    if db == "mongo":
        tabs.append({
            "label": "Program.cs",
            "path": "Program.cs",
            "code": _mongo_minimal_program(entities, ns),
        })
    else:
        tabs.append({
            "label": "AppDbContext.cs",
            "path": "Infrastructure/AppDbContext.cs",
            "code": _dbcontext(entities, ns),
        })
        tabs.append({
            "label": "Program.cs",
            "path": "Program.cs",
            "code": _minimal_program(entities, ns, db),
        })
    return tabs


def _minimal_endpoints(e: dict, ns: str) -> str:
    name = e["name"]
    plural = name.lower() + "s"
    return f"""using Microsoft.AspNetCore.Http.HttpResults;
using {ns}.Infrastructure;

namespace {ns}.API.Endpoints;

public static class {name}Endpoints
{{
    public static IEndpointRouteBuilder Map{name}s(this IEndpointRouteBuilder app)
    {{
        var group = app.MapGroup("api/{plural}")
            .WithTags("{name}s")
            .WithOpenApi();

        group.MapGet("", async (I{name}Repository repo, CancellationToken ct)
            => Results.Ok(await repo.GetAllAsync(ct)));

        group.MapGet("{{id:guid}}", async (Guid id, I{name}Repository repo, CancellationToken ct)
            => await repo.GetByIdAsync(id, ct) is {{ }} item
                ? Results.Ok(item)
                : Results.NotFound());

        group.MapPost("", async ({name} body, I{name}Repository repo, CancellationToken ct) =>
        {{
            var created = await repo.CreateAsync(body, ct);
            return Results.Created($"/api/{plural}/{{created.Id}}", created);
        }});

        group.MapPut("{{id:guid}}", async (Guid id, {name} body, I{name}Repository repo, CancellationToken ct) =>
        {{
            body.Id = id;
            return Results.Ok(await repo.UpdateAsync(body, ct));
        }});

        group.MapDelete("{{id:guid}}", async (Guid id, I{name}Repository repo, CancellationToken ct) =>
        {{
            await repo.DeleteAsync(id, ct);
            return Results.NoContent();
        }});

        return app;
    }}
}}
"""


def _minimal_repo(e: dict, ns: str) -> str:
    name = e["name"]
    return f"""using Microsoft.EntityFrameworkCore;

namespace {ns}.Infrastructure;

public interface I{name}Repository
{{
    Task<IReadOnlyList<{name}>> GetAllAsync(CancellationToken ct = default);
    Task<{name}?> GetByIdAsync(Guid id, CancellationToken ct = default);
    Task<{name}> CreateAsync({name} entity, CancellationToken ct = default);
    Task<{name}> UpdateAsync({name} entity, CancellationToken ct = default);
    Task DeleteAsync(Guid id, CancellationToken ct = default);
}}

public sealed class {name}Repository(AppDbContext ctx) : I{name}Repository
{{
    public async Task<IReadOnlyList<{name}>> GetAllAsync(CancellationToken ct = default)
        => await ctx.{name}s.AsNoTracking().ToListAsync(ct);

    public async Task<{name}?> GetByIdAsync(Guid id, CancellationToken ct = default)
        => await ctx.{name}s.FindAsync([id], ct);

    public async Task<{name}> CreateAsync({name} e, CancellationToken ct = default)
    {{
        e.Id = Guid.NewGuid();
        ctx.{name}s.Add(e);
        await ctx.SaveChangesAsync(ct);
        return e;
    }}

    public async Task<{name}> UpdateAsync({name} e, CancellationToken ct = default)
    {{
        ctx.{name}s.Update(e);
        await ctx.SaveChangesAsync(ct);
        return e;
    }}

    public async Task DeleteAsync(Guid id, CancellationToken ct = default)
        => await ctx.{name}s.Where(x => x.Id == id).ExecuteDeleteAsync(ct);
}}
"""


def _minimal_program(entities: list, ns: str, db: str = "sqlite") -> str:
    repos = "\n".join(
        f"builder.Services.AddScoped<I{e['name']}Repository, {e['name']}Repository>();"
        for e in entities
    )
    maps = "\n".join(f"app.Map{e['name']}s();" for e in entities)
    return f"""{_ef_pkg(db)}
using Microsoft.EntityFrameworkCore;
using {ns}.Infrastructure;

var builder = WebApplication.CreateBuilder(args);

builder.Services.AddDbContext<AppDbContext>(opt =>
    {_ef_db_line(db)});

{repos}

builder.Services.AddEndpointsApiExplorer();
builder.Services.AddSwaggerGen();

var app = builder.Build();
app.UseSwagger();
app.UseSwaggerUI();

{maps}

app.Run();
"""


# ── CLEAN ARCHITECTURE ────────────────────────────────────────────────────

def _clean_architecture(entities: list, db: str = "sqlite") -> list:
    tabs = []
    ns = entities[0]["namespace"] if entities else "Application"

    for e in entities:
        tabs.append({
            "label": f"{e['name']}.Domain.cs",
            "path": f"Domain/Entities/{e['name']}.cs",
            "code": _clean_domain(e, ns),
        })
        tabs.append({
            "label": f"{e['name']}.UseCases.cs",
            "path": f"Application/UseCases/{e['name']}s/{e['name']}UseCases.cs",
            "code": _clean_usecases(e, ns),
        })
        tabs.append({
            "label": f"{e['name']}.Infra.cs",
            "path": f"Infrastructure/Repositories/{e['name']}Repository.cs",
            "code": _mongo_clean_infra(e, ns) if db == "mongo" else _clean_infra(e, ns),
        })
        tabs.append({
            "label": f"{e['name']}sController.cs",
            "path": f"Presentation/Controllers/{e['name']}sController.cs",
            "code": _clean_controller(e, ns),
        })

    if db == "mongo":
        tabs.append({
            "label": "DependencyInjection.cs",
            "path": "Infrastructure/DependencyInjection.cs",
            "code": _mongo_clean_di(entities, ns),
        })
    else:
        tabs.append({
            "label": "AppDbContext.cs",
            "path": "Infrastructure/Persistence/AppDbContext.cs",
            "code": _dbcontext(entities, ns),
        })
        tabs.append({
            "label": "DependencyInjection.cs",
            "path": "Infrastructure/DependencyInjection.cs",
            "code": _clean_di(entities, ns, db),
        })
    return tabs


def _clean_domain(e: dict, ns: str) -> str:
    name = e["name"]
    props = _props_to_cs_class(e)
    required_props = [p for p in e["properties"] if p["required"] and p["name"] != "Id"]
    factory_params = ", ".join(
        f"{p['type']} {p['name'][0].lower() + p['name'][1:]}"
        for p in required_props[:4]
    )
    factory_assigns = ", ".join(
        f"{p['name']} = {p['name'][0].lower() + p['name'][1:]}"
        for p in required_props[:4]
    )

    return f"""namespace Domain.Entities;

/// <summary>Domain entity for {name}.</summary>
public sealed class {name} : BaseEntity
{{
{props}

    public static {name} Create({factory_params})
        => new() {{ Id = Guid.NewGuid(){', ' + factory_assigns if factory_assigns else ''} }};
}}

// Domain/Interfaces/I{name}Repository.cs
namespace Domain.Interfaces;

public interface I{name}Repository
{{
    Task<IReadOnlyList<{name}>> GetAllAsync(CancellationToken ct = default);
    Task<{name}?> GetByIdAsync(Guid id, CancellationToken ct = default);
    Task AddAsync({name} entity, CancellationToken ct = default);
    Task UpdateAsync({name} entity, CancellationToken ct = default);
    Task RemoveAsync({name} entity, CancellationToken ct = default);
}}

// Domain/BaseEntity.cs
namespace Domain;

public abstract class BaseEntity
{{
    public Guid Id {{ get; init; }}
}}
"""


def _clean_usecases(e: dict, ns: str) -> str:
    name = e["name"]
    return f"""namespace Application.UseCases.{name}s;

// ── Get All ───────────────────────────────────────────────────────────────
public sealed record GetAll{name}sRequest;
public sealed record GetAll{name}sResponse(IReadOnlyList<{name}> Items);

public sealed class GetAll{name}sUseCase(
    I{name}Repository repo,
    ILogger<GetAll{name}sUseCase> logger)
{{
    public async Task<GetAll{name}sResponse> ExecuteAsync(
        GetAll{name}sRequest request, CancellationToken ct = default)
    {{
        logger.LogInformation("Executing GetAll{name}s");
        var items = await repo.GetAllAsync(ct);
        return new GetAll{name}sResponse(items);
    }}
}}

// ── Create ────────────────────────────────────────────────────────────────
public sealed record Create{name}Request({name} Payload);
public sealed record Create{name}Response({name} Created);

public sealed class Create{name}UseCase(I{name}Repository repo)
{{
    public async Task<Create{name}Response> ExecuteAsync(
        Create{name}Request request, CancellationToken ct = default)
    {{
        var entity = request.Payload;
        await repo.AddAsync(entity, ct);
        return new Create{name}Response(entity);
    }}
}}

// ── Delete ────────────────────────────────────────────────────────────────
public sealed record Delete{name}Request(Guid Id);

public sealed class Delete{name}UseCase(I{name}Repository repo)
{{
    public async Task ExecuteAsync(Delete{name}Request request, CancellationToken ct = default)
    {{
        var entity = await repo.GetByIdAsync(request.Id, ct)
            ?? throw new KeyNotFoundException($"{name} {{request.Id}} not found");
        await repo.RemoveAsync(entity, ct);
    }}
}}
"""


def _clean_infra(e: dict, ns: str) -> str:
    name = e["name"]
    return f"""using Domain.Interfaces;
using Microsoft.EntityFrameworkCore;

namespace Infrastructure.Repositories;

internal sealed class {name}Repository(AppDbContext ctx) : I{name}Repository
{{
    public async Task<IReadOnlyList<{name}>> GetAllAsync(CancellationToken ct = default)
        => await ctx.{name}s.AsNoTracking().ToListAsync(ct);

    public async Task<{name}?> GetByIdAsync(Guid id, CancellationToken ct = default)
        => await ctx.{name}s.FindAsync([id], ct);

    public async Task AddAsync({name} entity, CancellationToken ct = default)
    {{
        ctx.{name}s.Add(entity);
        await ctx.SaveChangesAsync(ct);
    }}

    public async Task UpdateAsync({name} entity, CancellationToken ct = default)
    {{
        ctx.{name}s.Update(entity);
        await ctx.SaveChangesAsync(ct);
    }}

    public async Task RemoveAsync({name} entity, CancellationToken ct = default)
    {{
        ctx.{name}s.Remove(entity);
        await ctx.SaveChangesAsync(ct);
    }}
}}
"""


def _clean_controller(e: dict, ns: str) -> str:
    name = e["name"]
    return f"""using Application.UseCases.{name}s;
using Microsoft.AspNetCore.Mvc;

namespace Presentation.Controllers;

[ApiController]
[Route("api/[controller]")]
public sealed class {name}sController(
    GetAll{name}sUseCase getAll,
    Create{name}UseCase create,
    Delete{name}UseCase delete) : ControllerBase
{{
    [HttpGet]
    public async Task<IActionResult> GetAll(CancellationToken ct)
    {{
        var response = await getAll.ExecuteAsync(new GetAll{name}sRequest(), ct);
        return Ok(response.Items);
    }}

    [HttpPost]
    public async Task<IActionResult> Create({name} body, CancellationToken ct)
    {{
        var response = await create.ExecuteAsync(new Create{name}Request(body), ct);
        return CreatedAtAction(nameof(GetAll), new {{}}, response.Created);
    }}

    [HttpDelete("{{id:guid}}")]
    public async Task<IActionResult> Delete(Guid id, CancellationToken ct)
    {{
        try
        {{
            await delete.ExecuteAsync(new Delete{name}Request(id), ct);
            return NoContent();
        }}
        catch (KeyNotFoundException) {{ return NotFound(); }}
    }}
}}
"""


def _clean_di(entities: list, ns: str, db: str = "sqlite") -> str:
    repos = "\n".join(
        f"        services.AddScoped<I{e['name']}Repository, {e['name']}Repository>();"
        for e in entities
    )
    usecases = "\n".join(
        f"        services.AddScoped<GetAll{e['name']}sUseCase>();\n"
        f"        services.AddScoped<Create{e['name']}UseCase>();\n"
        f"        services.AddScoped<Delete{e['name']}UseCase>();"
        for e in entities
    )
    return f"""{_ef_pkg(db)}
using Domain.Interfaces;
using Infrastructure.Repositories;
using Microsoft.EntityFrameworkCore;

namespace Infrastructure;

public static class DependencyInjection
{{
    public static IServiceCollection AddInfrastructure(
        this IServiceCollection services,
        IConfiguration config)
    {{
        services.AddDbContext<AppDbContext>(opt =>
            {_ef_db_line(db)});

{repos}
        return services;
    }}

    public static IServiceCollection AddApplication(this IServiceCollection services)
    {{
{usecases}
        return services;
    }}
}}

// Program.cs
// var builder = WebApplication.CreateBuilder(args);
// builder.Services.AddInfrastructure(builder.Configuration);
// builder.Services.AddApplication();
// builder.Services.AddControllers();
// var app = builder.Build();
// app.UseSwagger(); app.UseSwaggerUI(); app.MapControllers(); app.Run();
"""


# ── MONGODB IMPLEMENTATIONS ───────────────────────────────────────────────

def _mongo_dbcontext(entities: list, ns: str) -> str:
    colls = "\n".join(
        f"    public IMongoCollection<{e['name']}> {e['name']}s"
        f" => _db.GetCollection<{e['name']}>(\"{e['name'].lower()}s\");"
        for e in entities
    )
    return f"""// dotnet add package MongoDB.Driver
using MongoDB.Driver;

namespace {ns}.Infrastructure.Persistence;

public sealed class MongoDbContext
{{
    private readonly IMongoDatabase _db;
    public MongoDbContext(IMongoDatabase db) => _db = db;

{colls}
}}
"""


def _mongo_repo_impl(e: dict, ns: str) -> str:
    name = e["name"]
    plural = name.lower() + "s"
    return f"""// dotnet add package MongoDB.Driver
using MongoDB.Driver;
using {ns}.Infrastructure.Repositories.Interfaces;

namespace {ns}.Infrastructure.Repositories;

public sealed class {name}Repository : I{name}Repository
{{
    private readonly IMongoCollection<{name}> _col;

    public {name}Repository(IMongoDatabase db)
        => _col = db.GetCollection<{name}>("{plural}");

    public async Task<IReadOnlyList<{name}>> GetAllAsync(CancellationToken ct = default)
        => (await _col.FindAsync(Builders<{name}>.Filter.Empty, cancellationToken: ct)).ToList();

    public async Task<{name}?> GetByIdAsync(Guid id, CancellationToken ct = default)
        => (await _col.FindAsync(x => x.Id == id, cancellationToken: ct)).FirstOrDefault();

    public async Task<{name}> CreateAsync({name} entity, CancellationToken ct = default)
    {{
        entity.Id = Guid.NewGuid();
        await _col.InsertOneAsync(entity, cancellationToken: ct);
        return entity;
    }}

    public async Task<{name}> UpdateAsync({name} entity, CancellationToken ct = default)
    {{
        await _col.ReplaceOneAsync(x => x.Id == entity.Id, entity, cancellationToken: ct);
        return entity;
    }}

    public async Task DeleteAsync(Guid id, CancellationToken ct = default)
        => await _col.DeleteOneAsync(x => x.Id == id, ct);

    public async Task<bool> ExistsAsync(Guid id, CancellationToken ct = default)
        => await _col.CountDocumentsAsync(x => x.Id == id, cancellationToken: ct) > 0;
}}
"""


def _mongo_repo_program(entities: list) -> str:
    ns = entities[0]["namespace"] if entities else "Application"
    repos = "\n".join(
        f"builder.Services.AddScoped<I{e['name']}Repository, {e['name']}Repository>();\n"
        f"builder.Services.AddScoped<{e['name']}Service>();"
        for e in entities
    )
    return f"""// dotnet add package MongoDB.Driver
using MongoDB.Driver;
using {ns}.Infrastructure.Persistence;
using {ns}.Infrastructure.Repositories;
using {ns}.Infrastructure.Repositories.Interfaces;
using {ns}.Application.Services;

var builder = WebApplication.CreateBuilder(args);

builder.Services.AddSingleton<IMongoClient>(
    new MongoClient(builder.Configuration.GetConnectionString("Mongo") ?? "mongodb://localhost:27017"));
builder.Services.AddScoped<IMongoDatabase>(sp =>
    sp.GetRequiredService<IMongoClient>().GetDatabase("AppDb"));
builder.Services.AddScoped<MongoDbContext>();

{repos}

builder.Services.AddControllers();
builder.Services.AddEndpointsApiExplorer();
builder.Services.AddSwaggerGen();

var app = builder.Build();
app.UseSwagger();
app.UseSwaggerUI();
app.MapControllers();
app.Run();
"""


def _mongo_cqrs_queries(e: dict, ns: str) -> str:
    name = e["name"]
    return f"""// dotnet add package MongoDB.Driver
using MediatR;
using MongoDB.Driver;
using {ns}.Infrastructure.Persistence;

namespace {ns}.Application.{name}s.Queries;

// ── Get All ─────────────────────────────────────────────
public sealed record GetAll{name}sQuery : IRequest<IReadOnlyList<{name}>>;

public sealed class GetAll{name}sHandler(MongoDbContext ctx)
    : IRequestHandler<GetAll{name}sQuery, IReadOnlyList<{name}>>
{{
    public async Task<IReadOnlyList<{name}>> Handle(
        GetAll{name}sQuery request, CancellationToken ct)
        => (await ctx.{name}s.FindAsync(Builders<{name}>.Filter.Empty, cancellationToken: ct)).ToList();
}}

// ── Get By Id ─────────────────────────────────────────────
public sealed record Get{name}ByIdQuery(Guid Id) : IRequest<{name}?>;

public sealed class Get{name}ByIdHandler(MongoDbContext ctx)
    : IRequestHandler<Get{name}ByIdQuery, {name}?>
{{
    public async Task<{name}?> Handle(
        Get{name}ByIdQuery request, CancellationToken ct)
        => (await ctx.{name}s.FindAsync(x => x.Id == request.Id, cancellationToken: ct)).FirstOrDefault();
}}
"""


def _mongo_cqrs_commands(e: dict, ns: str) -> str:
    name = e["name"]
    return f"""// dotnet add package MongoDB.Driver
using MediatR;
using MongoDB.Driver;
using {ns}.Infrastructure.Persistence;

namespace {ns}.Application.{name}s.Commands;

// ── Create ──────────────────────────────────────────────
public sealed record Create{name}Command({name} Payload) : IRequest<{name}>;

public sealed class Create{name}Handler(MongoDbContext ctx)
    : IRequestHandler<Create{name}Command, {name}>
{{
    public async Task<{name}> Handle(Create{name}Command request, CancellationToken ct)
    {{
        var entity = request.Payload;
        entity.Id = Guid.NewGuid();
        await ctx.{name}s.InsertOneAsync(entity, cancellationToken: ct);
        return entity;
    }}
}}

// ── Update ──────────────────────────────────────────────
public sealed record Update{name}Command(Guid Id, {name} Payload) : IRequest<{name}>;

public sealed class Update{name}Handler(MongoDbContext ctx)
    : IRequestHandler<Update{name}Command, {name}>
{{
    public async Task<{name}> Handle(Update{name}Command request, CancellationToken ct)
    {{
        var entity = request.Payload;
        entity.Id = request.Id;
        await ctx.{name}s.ReplaceOneAsync(x => x.Id == request.Id, entity, cancellationToken: ct);
        return entity;
    }}
}}

// ── Delete ──────────────────────────────────────────────
public sealed record Delete{name}Command(Guid Id) : IRequest;

public sealed class Delete{name}Handler(MongoDbContext ctx)
    : IRequestHandler<Delete{name}Command>
{{
    public async Task Handle(Delete{name}Command request, CancellationToken ct)
        => await ctx.{name}s.DeleteOneAsync(x => x.Id == request.Id, ct);
}}
"""


def _mongo_cqrs_program(entities: list) -> str:
    ns = entities[0]["namespace"] if entities else "Application"
    return f"""// dotnet add package MongoDB.Driver
using MediatR;
using MongoDB.Driver;
using {ns}.Infrastructure.Persistence;

var builder = WebApplication.CreateBuilder(args);

builder.Services.AddSingleton<IMongoClient>(
    new MongoClient(builder.Configuration.GetConnectionString("Mongo") ?? "mongodb://localhost:27017"));
builder.Services.AddScoped<IMongoDatabase>(sp =>
    sp.GetRequiredService<IMongoClient>().GetDatabase("AppDb"));
builder.Services.AddScoped<MongoDbContext>();

builder.Services.AddMediatR(cfg =>
    cfg.RegisterServicesFromAssembly(typeof(Program).Assembly));

builder.Services.AddControllers();
builder.Services.AddEndpointsApiExplorer();
builder.Services.AddSwaggerGen();

var app = builder.Build();
app.UseSwagger();
app.UseSwaggerUI();
app.MapControllers();
app.Run();
"""


def _mongo_minimal_repo(e: dict, ns: str) -> str:
    name = e["name"]
    plural = name.lower() + "s"
    return f"""// dotnet add package MongoDB.Driver
using MongoDB.Driver;

namespace {ns}.Infrastructure;

public interface I{name}Repository
{{
    Task<IReadOnlyList<{name}>> GetAllAsync(CancellationToken ct = default);
    Task<{name}?> GetByIdAsync(Guid id, CancellationToken ct = default);
    Task<{name}> CreateAsync({name} entity, CancellationToken ct = default);
    Task<{name}> UpdateAsync({name} entity, CancellationToken ct = default);
    Task DeleteAsync(Guid id, CancellationToken ct = default);
}}

public sealed class {name}Repository : I{name}Repository
{{
    private readonly IMongoCollection<{name}> _col;

    public {name}Repository(IMongoDatabase db)
        => _col = db.GetCollection<{name}>("{plural}");

    public async Task<IReadOnlyList<{name}>> GetAllAsync(CancellationToken ct = default)
        => (await _col.FindAsync(Builders<{name}>.Filter.Empty, cancellationToken: ct)).ToList();

    public async Task<{name}?> GetByIdAsync(Guid id, CancellationToken ct = default)
        => (await _col.FindAsync(x => x.Id == id, cancellationToken: ct)).FirstOrDefault();

    public async Task<{name}> CreateAsync({name} e, CancellationToken ct = default)
    {{
        e.Id = Guid.NewGuid();
        await _col.InsertOneAsync(e, cancellationToken: ct);
        return e;
    }}

    public async Task<{name}> UpdateAsync({name} e, CancellationToken ct = default)
    {{
        await _col.ReplaceOneAsync(x => x.Id == e.Id, e, cancellationToken: ct);
        return e;
    }}

    public async Task DeleteAsync(Guid id, CancellationToken ct = default)
        => await _col.DeleteOneAsync(x => x.Id == id, ct);
}}
"""


def _mongo_minimal_program(entities: list, ns: str) -> str:
    repos = "\n".join(
        f"builder.Services.AddScoped<I{e['name']}Repository, {e['name']}Repository>();"
        for e in entities
    )
    maps = "\n".join(f"app.Map{e['name']}s();" for e in entities)
    return f"""// dotnet add package MongoDB.Driver
using MongoDB.Driver;
using {ns}.Infrastructure;

var builder = WebApplication.CreateBuilder(args);

builder.Services.AddSingleton<IMongoClient>(
    new MongoClient(builder.Configuration.GetConnectionString("Mongo") ?? "mongodb://localhost:27017"));
builder.Services.AddScoped<IMongoDatabase>(sp =>
    sp.GetRequiredService<IMongoClient>().GetDatabase("AppDb"));

{repos}

builder.Services.AddEndpointsApiExplorer();
builder.Services.AddSwaggerGen();

var app = builder.Build();
app.UseSwagger();
app.UseSwaggerUI();

{maps}

app.Run();
"""


def _mongo_clean_infra(e: dict, ns: str) -> str:
    name = e["name"]
    plural = name.lower() + "s"
    return f"""// dotnet add package MongoDB.Driver
using Domain.Interfaces;
using MongoDB.Driver;

namespace Infrastructure.Repositories;

internal sealed class {name}Repository : I{name}Repository
{{
    private readonly IMongoCollection<{name}> _col;

    public {name}Repository(IMongoDatabase db)
        => _col = db.GetCollection<{name}>("{plural}");

    public async Task<IReadOnlyList<{name}>> GetAllAsync(CancellationToken ct = default)
        => (await _col.FindAsync(Builders<{name}>.Filter.Empty, cancellationToken: ct)).ToList();

    public async Task<{name}?> GetByIdAsync(Guid id, CancellationToken ct = default)
        => (await _col.FindAsync(x => x.Id == id, cancellationToken: ct)).FirstOrDefault();

    public async Task AddAsync({name} entity, CancellationToken ct = default)
    {{
        entity.Id = Guid.NewGuid();
        await _col.InsertOneAsync(entity, cancellationToken: ct);
    }}

    public async Task UpdateAsync({name} entity, CancellationToken ct = default)
        => await _col.ReplaceOneAsync(x => x.Id == entity.Id, entity, cancellationToken: ct);

    public async Task RemoveAsync({name} entity, CancellationToken ct = default)
        => await _col.DeleteOneAsync(x => x.Id == entity.Id, ct);
}}
"""


def _mongo_clean_di(entities: list, ns: str) -> str:
    repos = "\n".join(
        f"        services.AddScoped<I{e['name']}Repository, {e['name']}Repository>();"
        for e in entities
    )
    usecases = "\n".join(
        f"        services.AddScoped<GetAll{e['name']}sUseCase>();\n"
        f"        services.AddScoped<Create{e['name']}UseCase>();\n"
        f"        services.AddScoped<Delete{e['name']}UseCase>();"
        for e in entities
    )
    return f"""// dotnet add package MongoDB.Driver
using Domain.Interfaces;
using Infrastructure.Repositories;
using MongoDB.Driver;

namespace Infrastructure;

public static class DependencyInjection
{{
    public static IServiceCollection AddInfrastructure(
        this IServiceCollection services,
        IConfiguration config)
    {{
        services.AddSingleton<IMongoClient>(
            new MongoClient(config.GetConnectionString("Mongo") ?? "mongodb://localhost:27017"));
        services.AddScoped<IMongoDatabase>(sp =>
            sp.GetRequiredService<IMongoClient>().GetDatabase("AppDb"));

{repos}
        return services;
    }}

    public static IServiceCollection AddApplication(this IServiceCollection services)
    {{
{usecases}
        return services;
    }}
}}

// Program.cs
// var builder = WebApplication.CreateBuilder(args);
// builder.Services.AddInfrastructure(builder.Configuration);
// builder.Services.AddApplication();
// builder.Services.AddControllers();
// var app = builder.Build();
// app.UseSwagger(); app.UseSwaggerUI(); app.MapControllers(); app.Run();
"""
