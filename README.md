
# Postgres MCP Pro (FastMCP Fork)

## Overview

**Postgres MCP Pro** is an open-source MCP (Model Context Protocol) server built on top of [FastMCP](https://gofastmcp.com/) that supports you and your AI agents throughout the entire development process—from initial coding, through testing and deployment, to production tuning and maintenance.

This fork of the original [postgres-fastmcp](https://github.com/crystaldba/postgres-fastmcp) project has been rewritten to use FastMCP, providing:

- **🚀 Enhanced Performance** — FastMCP is optimized for high performance
- **🔧 Flexible Configuration** — support for multiple databases simultaneously via `config.json`
- **🌐 Multiple Transports** — support for HTTP, stdio, and streamable-http
- **🔐 Granular Access Control** — four access modes for different use cases
- **📦 Server Composition** — ability to mount multiple servers with prefixes

### Key Features

- **🔍 Database Health** — analyze index health, connection utilization, buffer cache, vacuum health, sequence limits, replication lag, and more
- **⚡ Index Tuning** — explore thousands of possible indexes to find the best solution for your workload, using industrial-strength algorithms
- **📈 Query Plans** — validate and optimize performance by reviewing EXPLAIN plans and simulating the impact of hypothetical indexes
- **🧠 Schema Intelligence** — context-aware SQL generation based on detailed understanding of the database schema
- **🛡️ Safe SQL Execution** — configurable access control, including support for read-only mode and safe SQL parsing, making it usable for both development and production

## Quick Start

### Prerequisites

Before getting started, ensure you have:

1. Access credentials for your database
2. Python 3.12 or higher
3. `uv` for dependency management (recommended)

### Running the Server

The server can be run in several modes depending on your needs:

#### 1. Single Database Mode (CLI)

For quick start with a single database, use CLI parameters:

**HTTP mode:**

```bash
uv run postgres-fastmcp \
  --database-uri "postgresql://user:password@localhost:5432/dbname" \
  --transport http \
  --port 8000 \
  --role full \
  --access-mode restricted
```

**STDIO mode (for MCP clients like Claude Desktop):**

```bash
uv run postgres-fastmcp \
  --database-uri "postgresql://user:password@localhost:5432/dbname" \
  --transport stdio \
  --role user \
  --access-mode restricted
```

**With custom tool prefix:**

```bash
uv run postgres-fastmcp \
  --database-uri "postgresql://user:password@localhost:5432/dbname" \
  --transport http \
  --name "mydb" \
  --role full \
  --access-mode unrestricted
```

#### 2. Configuration File Mode

Create a `config.json` file in the current directory:

**Basic multi-database configuration:**

```json
{
    "name": "postgres-fastmcp",
    "transport": "http",
    "host": "0.0.0.0",
    "port": 8000,
    "endpoint": "mcp",
    "databases": {
        "production": {
            "database_uri": "postgresql://user:password@localhost:5432/production",
            "role": "full",
            "access_mode": "restricted",
            "transport": "http"
        },
        "development": {
            "database_uri": "postgresql://user:password@localhost:5432/development",
            "role": "full",
            "access_mode": "unrestricted",
            "transport": "http"
        }
    }
}
```

**With separate endpoints:**

```json
{
    "name": "postgres-fastmcp",
    "transport": "http",
    "host": "0.0.0.0",
    "port": 8000,
    "endpoint": "mcp",
    "databases": {
        "app1": {
            "database_uri": "postgresql://user:password@localhost:5432/app1",
            "role": "user",
            "access_mode": "restricted",
            "endpoint": true,
            "transport": "http"
        },
        "app2": {
            "database_uri": "postgresql://user:password@localhost:5432/app2",
            "role": "full",
            "access_mode": "unrestricted",
            "endpoint": true,
            "transport": "streamable-http"
        }
    }
}
```

Then run:

```bash
uv run postgres-fastmcp
```

#### 3. Environment Variables Mode

You can also configure the server using environment variables:

```bash
export TRANSPORT=http
export HOST=0.0.0.0
export PORT=8000
export DATABASES__PRODUCTION__DATABASE_URI=postgresql://user:pass@localhost:5432/prod
export DATABASES__PRODUCTION__ROLE=full
export DATABASES__PRODUCTION__ACCESS_MODE=restricted
export DATABASES__DEVELOPMENT__DATABASE_URI=postgresql://user:pass@localhost:5432/dev
export DATABASES__DEVELOPMENT__ROLE=full
export DATABASES__DEVELOPMENT__ACCESS_MODE=unrestricted

uv run postgres-fastmcp
```

#### 4. Mixed Configuration

You can combine configuration sources. Priority order (highest to lowest):

1. CLI parameters
2. `config.json` file
3. Environment variables
4. Default values

## Configuration

### Access Control

The project uses two independent parameters for flexible security control:

#### Role (`role`)

Determines schema access and available tools:

| Role | Schemas | Tools | Description |
|------|---------|-------|-------------|
| `user` | Only `public` | Basic (4) | Basic role with access limited to public schema |
| `full` | All schemas | All (9) | Full role with access to all schemas and extended privileges |

#### Access Mode (`access_mode`)

Determines SQL access level:

| Access Mode | SQL Access | Description |
|-------------|------------|-------------|
| `restricted` | Read-only (SELECT only) | Restricted access mode |
| `unrestricted` | Read-write (DML: INSERT/UPDATE/DELETE) or full access (DDL) | Unrestricted access mode |

#### Combination Matrix

| Role | Access Mode | Tools | SQL Access | Schemas |
|------|-------------|-------|------------|---------|
| `user` | `restricted` | Basic (4) | Read-only | `public` |
| `user` | `unrestricted` | Basic (4) | Read-write | `public` |
| `full` | `restricted` | All (9) | Read-only | All |
| `full` | `unrestricted` | All (9) | Full access (DDL) | All |

**Default values:**

- `role`: `"user"` (default)
- `access_mode`: `"restricted"` (default)
- Default combination: `role="user"` + `access_mode="restricted"` (maximum security)

### Transports

The server supports three transport types, each suitable for different use cases:

#### HTTP Transport

HTTP transport allows running the server as a web application. This is ideal for:

- Integration with web-based MCP clients (like Cursor)
- Multiple clients connecting to the same server
- Production deployments

**Single database:**

```bash
uv run postgres-fastmcp \
  --database-uri "postgresql://user:password@localhost:5432/dbname" \
  --transport http \
  --port 8000
```

The server will be available at `http://localhost:8000/mcp` (or at the specified endpoint).

**Multiple databases (Server Composition):**
When multiple databases are configured with `endpoint=false` (default), all tools are available at the main endpoint with prefixes:

```json
{
    "transport": "http",
    "databases": {
        "db1": {
            "database_uri": "postgresql://...",
            "endpoint": false
        },
        "db2": {
            "database_uri": "postgresql://...",
            "endpoint": false
        }
    }
}
```

Tools will be available as: `db1_list_objects`, `db2_list_objects`, etc.

#### Streamable-HTTP Transport

Streamable-HTTP provides streaming data transfer for large responses. This is useful for:

- Large query results
- Long-running operations
- Real-time data streaming

**Note:** Currently, MCP tools do not use streaming. Streamable-HTTP transport is available for future use and protocol-level streaming support.

**Global streamable-http transport:**

```json
{
    "transport": "streamable-http",
    "databases": {
        "db1": {
            "database_uri": "postgresql://...",
            "endpoint": false
        },
        "db2": {
            "database_uri": "postgresql://...",
            "endpoint": false
        }
    }
}
```

**Per-server streamable-http transport (for servers with endpoint=true):**

```json
{
    "transport": "http",
    "databases": {
        "analytics": {
            "database_uri": "postgresql://...",
            "endpoint": true,
            "transport": "streamable-http"
        },
        "main": {
            "database_uri": "postgresql://...",
            "endpoint": true,
            "transport": "http"
        }
    }
}
```

Each server can have its own transport type (`"http"` or `"streamable-http"`). When using streamable-http globally, all servers in the main endpoint will use streaming transport. For separate endpoints, each server can specify its own transport type.

#### STDIO Transport

STDIO transport is used for integration with MCP clients via standard input/output. This is ideal for:

- Desktop MCP clients (like Claude Desktop)
- Direct process communication
- Development and testing

**Single database:**

```bash
uv run postgres-fastmcp \
  --database-uri "postgresql://user:password@localhost:5432/dbname" \
  --transport stdio
```

**Multiple databases:**
In stdio mode, all databases are automatically registered with prefixes, regardless of the `endpoint` setting:

```json
{
    "transport": "stdio",
    "databases": {
        "db1": {
            "database_uri": "postgresql://...",
            "endpoint": true  # Ignored in stdio mode
        },
        "db2": {
            "database_uri": "postgresql://...",
            "endpoint": false  # Ignored in stdio mode
        }
    }
}
```

All tools will be available with prefixes: `db1_list_objects`, `db2_list_objects`, etc.

### Configuration Methods

The server supports multiple configuration methods with the following priority (highest to lowest):

1. **CLI parameters** - Command-line arguments (highest priority)
2. **config.json file** - JSON configuration file in the current directory
3. **Environment variables** - System environment variables
4. **Default values** - Built-in defaults (lowest priority)

#### CLI Parameters

All configuration can be provided via command-line arguments:

```bash
uv run postgres-fastmcp \
  --database-uri "postgresql://user:password@localhost:5432/dbname" \
  --transport http \
  --host 0.0.0.0 \
  --port 8000 \
  --role full \
  --access-mode restricted \
  --name "mydb" \
  --endpoint
```

#### config.json File

Create a `config.json` file in the current directory:

```json
{
    "name": "postgres-fastmcp",
    "transport": "http",
    "host": "0.0.0.0",
    "port": 8000,
    "endpoint": "mcp",
    "databases": {
        "production": {
            "database_uri": "postgresql://user:password@localhost:5432/production",
            "role": "full",
            "access_mode": "restricted",
            "endpoint": false,
            "transport": "http"
        }
    }
}
```

#### Environment Variables

Use nested delimiter `__` (double underscore) for nested configuration:

```bash
export TRANSPORT=http
export HOST=0.0.0.0
export PORT=8000
export DATABASES__PRODUCTION__DATABASE_URI=postgresql://user:pass@localhost:5432/prod
export DATABASES__PRODUCTION__ROLE=full
export DATABASES__PRODUCTION__ACCESS_MODE=restricted
export DATABASES__DEVELOPMENT__DATABASE_URI=postgresql://user:pass@localhost:5432/dev
export DATABASES__DEVELOPMENT__ROLE=user
export DATABASES__DEVELOPMENT__ACCESS_MODE=unrestricted
```

#### .env File

You can also use a `.env` file in the current directory with the same format as environment variables:

```env
TRANSPORT=http
HOST=0.0.0.0
PORT=8000
DATABASES__PRODUCTION__DATABASE_URI=postgresql://user:pass@localhost:5432/prod
DATABASES__PRODUCTION__ROLE=full
DATABASES__PRODUCTION__ACCESS_MODE=restricted
```

### MCP Client Integration

#### Cursor (HTTP Transport)

For HTTP transport, configure Cursor in `~/.cursor/mcp.json`:

**Single database:**

```json
{
    "mcpServers": {
        "postgres": {
            "type": "sse",
            "url": "http://localhost:8000/mcp"
        }
    }
}
```

**Multiple databases with separate endpoints:**

```json
{
    "mcpServers": {
        "postgres-prod": {
            "type": "sse",
            "url": "http://localhost:8000/production/mcp"
        },
        "postgres-dev": {
            "type": "sse",
            "url": "http://localhost:8000/development/mcp"
        }
    }
}
```

**Multiple databases with Server Composition (single endpoint with prefixes):**

```json
{
    "mcpServers": {
        "postgres": {
            "type": "sse",
            "url": "http://localhost:8000/mcp"
        }
    }
}
```

Tools will be available as: `production_list_objects`, `development_list_objects`, etc.

#### Claude Desktop (STDIO Transport)

For stdio transport, configure Claude Desktop in `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

**Single database:**

```json
{
    "mcpServers": {
        "postgres": {
            "command": "uv",
            "args": ["run", "postgres-fastmcp", "--transport", "stdio"],
            "env": {
                "DATABASES__DEFAULT__DATABASE_URI": "postgresql://user:pass@localhost:5432/dbname"
            }
        }
    }
}
```

**Multiple databases:**

```json
{
    "mcpServers": {
        "postgres": {
            "command": "uv",
            "args": ["run", "postgres-fastmcp", "--transport", "stdio"],
            "env": {
                "DATABASES__PRODUCTION__DATABASE_URI": "postgresql://user:pass@localhost:5432/prod",
                "DATABASES__PRODUCTION__ROLE": "full",
                "DATABASES__PRODUCTION__ACCESS_MODE": "restricted",
                "DATABASES__DEVELOPMENT__DATABASE_URI": "postgresql://user:pass@localhost:5432/dev",
                "DATABASES__DEVELOPMENT__ROLE": "full",
                "DATABASES__DEVELOPMENT__ACCESS_MODE": "unrestricted"
            }
        }
    }
}
```

All tools will be available with prefixes: `production_list_objects`, `development_list_objects`, etc.

## Multi-Server Architecture

The server supports two mounting modes for multiple databases:

### Server Composition (endpoint=false)

When `endpoint=false` (default), all databases are mounted in the main endpoint using FastMCP's Server Composition feature. Tools are automatically prefixed with the server name to prevent conflicts.

**Configuration:**

```json
{
    "transport": "http",
    "databases": {
        "production": {
            "database_uri": "postgresql://...",
            "endpoint": false
        },
        "development": {
            "database_uri": "postgresql://...",
            "endpoint": false
        }
    }
}
```

**Result:**

- All tools available at: `http://localhost:8000/mcp`
- Tools prefixed: `production_list_objects`, `development_list_objects`, etc.
- Single endpoint for all databases

### Separate Endpoints (endpoint=true)

When `endpoint=true`, each database gets its own HTTP endpoint. This allows different transport types per server and better isolation.

**Configuration:**

```json
{
    "transport": "http",
    "databases": {
        "app1": {
            "database_uri": "postgresql://...",
            "endpoint": true,
            "transport": "http"
        },
        "app2": {
            "database_uri": "postgresql://...",
            "endpoint": true,
            "transport": "streamable-http"
        }
    }
}
```

**Result:**

- App1 tools at: `http://localhost:8000/app1/mcp`
- App2 tools at: `http://localhost:8000/app2/mcp`
- Each endpoint can have different transport types
- Tools always prefixed with server name

### Tool Prefixes

Tool prefixes are automatically added based on the server name to prevent conflicts when multiple MCP servers are connected to a single agent.

**Rules:**

- Single server with no explicit prefix: no prefix (tools: `list_objects`, `execute_sql`)
- Single server with explicit prefix: uses prefix (tools: `mydb_list_objects`, `mydb_execute_sql`)
- Multiple servers: always prefixed with server name (tools: `db1_list_objects`, `db2_list_objects`)

For detailed architecture documentation, see [Multi-Endpoint Server Architecture](./docs/architecture/multi-endpoint-server.md).

## Technical Details

### FastMCP

This fork has been completely rewritten on top of [FastMCP](https://gofastmcp.com/), a modern framework for building MCP servers. FastMCP provides:

- High performance thanks to asynchronous architecture
- Built-in support for HTTP and stdio transports
- Server Composition for mounting multiple servers
- Simplified API for tool registration

### Multiple Databases

The project supports working with multiple databases simultaneously. Each database is configured separately with its own access mode and connection parameters.

When using HTTP transport with multiple databases configured:

- With `endpoint=false`: tools available at main endpoint with prefixes (Server Composition)
- With `endpoint=true`: each database gets its own endpoint at `/{server_name}/mcp`

### Lifecycle Management

The server uses FastMCP lifespan for managing database connection lifecycles:

- Automatic connection pool creation on startup
- Proper connection closure on shutdown
- Signal handling (SIGINT, SIGTERM)

### Safe SQL Execution

The project uses multi-layered protection for safe SQL execution:

1. **SQL Parsing** — uses `pglast` library to analyze SQL before execution
2. **Read-only Transactions** — for read-only modes, PostgreSQL read-only transactions are used
3. **COMMIT/ROLLBACK Checks** — blocks attempts to bypass read-only mode
4. **Timeouts** — limits query execution time in restricted modes

## MCP API

The server provides functionality via [MCP tools](https://modelcontextprotocol.io/docs/concepts/tools).

### Available Tools

| Tool Name | Description |
|-----------|-------------|
| `list_schemas` | Lists all database schemas available in the PostgreSQL instance |
| `list_objects` | Lists database objects (tables, views, sequences, extensions) within a specified schema |
| `get_object_details` | Provides information about a specific database object, for example, a table's columns, constraints, and indexes |
| `execute_sql` | Executes SQL statements on the database, with read-only limitations when connected in restricted modes |
| `explain_query` | Gets the execution plan for a SQL query describing how PostgreSQL will process it. Can be invoked with hypothetical indexes to simulate the behavior after adding indexes |
| `get_top_queries` | Reports the slowest SQL queries based on total execution time using `pg_stat_statements` data |
| `analyze_workload_indexes` | Analyzes the database workload to identify resource-intensive queries, then recommends optimal indexes for them |
| `analyze_query_indexes` | Analyzes a list of specific SQL queries (up to 10) and recommends optimal indexes for them |
| `analyze_db_health` | Performs comprehensive health checks including: buffer cache hit rates, connection health, constraint validation, index health (duplicate/unused/invalid), sequence limits, and vacuum health |

### Access Control Limitations

- **`user` role**: Only basic tools available (`list_objects`, `get_object_details`, `explain_query`, `execute_sql`)
- **`full` role**: All tools available (basic tools + `list_schemas`, `analyze_workload_indexes`, `analyze_query_indexes`, `analyze_db_health`, `get_top_queries`)
- **`restricted` access_mode**: Only SELECT queries allowed
- **`unrestricted` access_mode**: DML (INSERT/UPDATE/DELETE) allowed; DDL allowed only for `full` role

## PostgreSQL Extension Installation (Optional)

To enable index tuning and comprehensive performance analysis, you need to install the `pg_stat_statements` and `hypopg` extensions in your database.

### Installing Extensions

```sql
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
CREATE EXTENSION IF NOT EXISTS hypopg;
```

**Important**: For `pg_stat_statements`, you must add it to `shared_preload_libraries` in PostgreSQL configuration and restart the server.

### Installing Extensions on Cloud Providers

If your Postgres database is running on a cloud provider managed service (AWS RDS, Azure SQL, Google Cloud SQL), the `pg_stat_statements` and `hypopg` extensions should already be available. You can just run the `CREATE EXTENSION` commands using a role with sufficient privileges.

### Installing Extensions on Self-Managed Postgres

If you are managing your own Postgres installation, you may need to do additional work:

- Before loading the `pg_stat_statements` extension, ensure it is listed in `shared_preload_libraries` in the Postgres configuration file
- The `hypopg` extension may require additional system-level installation (e.g., via your package manager) because it does not always ship with Postgres

## Configuration Examples

### Example 1: Production and Development Databases

Separate production (read-only) and development (read-write) databases:

```json
{
    "name": "postgres-fastmcp",
    "transport": "http",
    "host": "0.0.0.0",
    "port": 8000,
    "endpoint": "mcp",
    "databases": {
        "production": {
            "database_uri": "postgresql://user:password@prod-server:5432/production",
            "role": "full",
            "access_mode": "restricted",
            "endpoint": false
        },
        "development": {
            "database_uri": "postgresql://user:password@localhost:5432/development",
            "role": "full",
            "access_mode": "unrestricted",
            "endpoint": false
        }
    }
}
```

Tools available at `http://localhost:8000/mcp`:

- `production_list_objects`, `production_execute_sql` (read-only)
- `development_list_objects`, `development_execute_sql` (read-write)

### Example 2: Separate Endpoints for Different Apps

Each application gets its own endpoint:

```json
{
    "name": "postgres-fastmcp",
    "transport": "http",
    "host": "0.0.0.0",
    "port": 8000,
    "endpoint": "mcp",
    "databases": {
        "analytics": {
            "database_uri": "postgresql://user:password@localhost:5432/analytics",
            "role": "full",
            "access_mode": "restricted",
            "endpoint": true,
            "transport": "streamable-http"
        },
        "main": {
            "database_uri": "postgresql://user:password@localhost:5432/main",
            "role": "user",
            "access_mode": "unrestricted",
            "endpoint": true,
            "transport": "http"
        }
    }
}
```

Endpoints:

- Analytics: `http://localhost:8000/analytics/mcp` (streamable-http transport)
- Main: `http://localhost:8000/main/mcp` (standard HTTP)

### Example 3: STDIO Mode with Multiple Databases

For Claude Desktop or other stdio-based clients:

```json
{
    "transport": "stdio",
    "databases": {
        "db1": {
            "database_uri": "postgresql://user:password@localhost:5432/db1",
            "role": "full",
            "access_mode": "restricted"
        },
        "db2": {
            "database_uri": "postgresql://user:password@localhost:5432/db2",
            "role": "user",
            "access_mode": "unrestricted"
        }
    }
}
```

All tools available with prefixes: `db1_list_objects`, `db2_list_objects`, etc.

### Example 4: User Role with Restricted Access

Basic user access with read-only SQL:

```json
{
    "transport": "http",
    "databases": {
        "readonly": {
            "database_uri": "postgresql://readonly_user:password@localhost:5432/mydb",
            "role": "user",
            "access_mode": "restricted",
            "endpoint": false
        }
    }
}
```

Available tools (4): `list_objects`, `get_object_details`, `explain_query`, `execute_sql` (SELECT only)

## Usage Examples

### Get Database Health Overview

Ask your AI agent:
> Check the health of my database and identify any issues.

### Analyze Slow Queries

> What are the slowest queries in my database? And how can I speed them up?

### Get Performance Recommendations

> My app is slow. How can I make it faster?

### Generate Index Recommendations

> Analyze my database workload and suggest indexes to improve performance.

### Optimize a Specific Query

> Help me optimize this query: SELECT * FROM orders JOIN customers ON orders.customer_id = customers.id WHERE orders.created_at > '2023-01-01';

## Development

### Local Development Setup

1. **Install uv**:

   ```bash
   curl -sSL https://astral.sh/uv/install.sh | sh
   ```

2. **Clone the repository**:

   ```bash
   git clone https://github.com/your-username/postgres-fastmcp.git
   cd postgres-fastmcp
   ```

3. **Install dependencies**:

   ```bash
   uv sync
   ```

4. **Install package in development mode**:

   ```bash
   uv pip install -e .
   ```

### Running Tests

**Prerequisites:**

- Docker must be installed and running
- Docker images will be built automatically on first test run, or you can prepare them manually:

```bash
# Prepare Docker images for testing (optional, but recommended for faster test runs)
uv run python tests/prepare_docker_images.py
```

**Run all tests:**

```bash
uv run python -m pytest
```

**Run specific test file:**

```bash
uv run python -m pytest tests/unit/index/test_dta_calc.py -v
```

**Run tests with real database (integration tests):**

```bash
uv run python -m pytest tests/integration/ -v
```

### Code Formatting

The project uses `ruff` for formatting and linting:

```bash
uv run ruff format .
uv run ruff check .
```

### Type Checking

The project uses `mypy` for type checking:

```bash
uv run mypy src/
```

## Differences from Original Project

This fork differs from the original [postgres-fastmcp](https://github.com/crystaldba/postgres-fastmcp) with the following key changes:

|Original Project|This Fork|
|------------------|-----------|
|Standard MCP implementation|FastMCP framework|
|Single database per server|Multiple databases|
|Modes: restricted/unrestricted|Modes: role (user/full) + access_mode (restricted/unrestricted)|
|SSE transport only|HTTP, stdio, streamable-http|
|Configuration via CLI/env|Configuration via config.json + env|

## Technical Notes

### Index Tuning

The index tuning implementation follows the same approach as the original project, using the [Anytime Algorithm of Database Tuning Advisor for Microsoft SQL Server](https://www.microsoft.com/en-us/research/wp-content/uploads/2020/06/Anytime-Algorithm-of-Database-Tuning-Advisor-for-Microsoft-SQL-Server.pdf).

### Database Health

Database health checks are adapted from [PgHero](https://github.com/ankane/pghero) and include:

- Index Health (unused, duplicate, bloated indexes)
- Buffer Cache Hit Rate
- Connection Health
- Vacuum Health (transaction ID wraparound prevention)
- Replication Health
- Constraint Health
- Sequence Health

### Postgres Client Library

The project uses [psycopg3](https://www.psycopg.org/) for asynchronous I/O connections to Postgres, providing access to the full Postgres feature set.

### Protected SQL Execution

The project implements multi-layered SQL protection:

- SQL parsing using `pglast` to detect and reject unsafe statements
- Read-only transactions for restricted modes
- Timeout limits for query execution
- Schema restrictions for user modes

## License

MIT License

## Acknowledgments

This project is a fork of [postgres-fastmcp](https://github.com/crystaldba/postgres-fastmcp) by [Crystal DBA](https://www.crystaldba.ai), rewritten to use [FastMCP](https://gofastmcp.com/).
