# Docker Test Environment

**⚠️ This docker-compose setup is for TESTING ONLY.**

This directory contains configuration for running a test environment with MCP server and PostgreSQL.
For production Docker usage, see the main `Dockerfile` and entrypoint in the project root.

**One MCP server = one database.** The server always runs in single-DB mode.

There is no `docker-compose.yml` in this repository (it was removed): the pieces below are run by
hand with plain `docker` commands.

## Structure

- `postgres/Dockerfile` - PostgreSQL image with HypoPG extension
- `postgres/init-db.sql` - PostgreSQL initialization script with test databases (4 databases with different access modes)
- `postgres/init-4-databases.sql` - Alternative initialization script (same as init-db.sql)
- `postgres/init-4-test-databases.sql` - Creates 4 test databases (db1, db2, db3, db4) for integration tests
- `postgres/init-all-databases.sh` - Bash script to initialize all databases with test data
- `config.json` - Test MCP server configuration for **one** database (see format in `src/postgres_fastmcp/app/config/__init__.py`)

## Test Databases (choose one per server)

The initialization scripts create test databases. **Each MCP server instance connects to a single database.** Default `config.json` uses `user_ro_db`.

| Database      | User      | Password | access_mode | write_mode | Notes           |
|--------------|-----------|----------|-------------|------------|-----------------|
| user_ro_db   | user_ro   | password | basic       | false      | table_prefix: app_ |
| user_rw_db   | user_rw   | password | basic       | true       |                 |
| admin_ro_db  | admin_ro  | password | full        | false      |                 |
| admin_rw_db  | postgres  | postgres | full        | true       |                 |

Additionally, `init-4-test-databases.sql` creates db1, db2, db3, db4 for integration tests.

To use another database, edit `docker/config.json` and set `database` to the desired host, port, user, password, name, access_mode, write_mode (and table_prefix for basic). Or run the server with `--database-uri postgresql://user:pass@postgres:5432/dbname` and no config file.

## Prerequisites

- Docker installed
- Ports 8000 and 5432 available (or map different host ports below)

## Running

### Start the test environment

```bash
# Isolated network so the two containers can reach each other by name
docker network create mcp-network

# PostgreSQL with HypoPG, initialized with the test databases above
docker build -t postgres-hypopg docker/postgres
docker run -d --name postgres --network mcp-network -p 5432:5432 \
  -e POSTGRES_PASSWORD=postgres \
  -v "$(pwd)/docker/postgres/init-db.sql:/docker-entrypoint-initdb.d/init-db.sql:ro" \
  postgres-hypopg

# MCP server, built from the project root Dockerfile, with docker/config.json mounted
docker build -t postgres-fastmcp .
docker run -d --name mcp-server --network mcp-network -p 8000:8000 \
  -v "$(pwd)/docker/config.json:/app/config.json:ro" \
  postgres-fastmcp
```

### View logs

```bash
docker logs -f mcp-server
docker logs -f postgres
```

### Stop the environment

```bash
docker rm -f mcp-server postgres
docker network rm mcp-network   # add -v to a volume mount below to drop data too
```

### Rebuild after changes

```bash
docker rm -f mcp-server
docker build -t postgres-fastmcp .
docker run -d --name mcp-server --network mcp-network -p 8000:8000 \
  -v "$(pwd)/docker/config.json:/app/config.json:ro" \
  postgres-fastmcp
```

## Access

- **MCP server**: <http://localhost:8000/mcp>
- **Health check**: <http://localhost:8000/health> (`200` when the database answers `SELECT 1`, `503` otherwise; no token required)
- **PostgreSQL**:
  - From host: `localhost:5432`
  - From Docker network: `postgres:5432`
  - Default user: `postgres` / Password: `postgres`

## Network

Both containers run in an isolated Docker network `mcp-network` (created above).

- Port 8000 for the MCP server is published externally
- Port 5432 for PostgreSQL is published externally (for testing)
- Containers communicate via hostname `postgres` inside the network

## Configuration

The MCP server uses `docker/config.json` mounted as `/app/config.json` in the container. The file may have `server`, `fastmcp`, `database` and `auth` sections (one database per server); every field is optional and falls back to `MCP_<SECTION>_<FIELD>` environment variables and defaults.

To use a different test database:

1. Edit `docker/config.json` and change the `database` section (host, port, user, password, name, access_mode, write_mode, table_prefix).
2. Restart: `docker restart mcp-server`

## Troubleshooting

### Port already in use

If ports 8000 or 5432 are already in use, map different host ports when starting the containers, e.g. `-p 8001:8000` and `-p 5433:5432`.

### Database connection issues

Check that PostgreSQL is up:

```bash
docker ps
docker logs postgres
```

### MCP server not starting

Check MCP server logs:

```bash
docker logs mcp-server
```

Verify the config.json is valid:

```bash
docker exec mcp-server cat /app/config.json | python3 -m json.tool
```

## Differences from Production

This test environment differs from production in several ways:

1. **PostgreSQL configuration**: Uses test credentials and exposes port 5432
2. **MCP server**: Uses test config with one database (single-DB mode only)
3. **Network**: Isolated test network, not production-ready
4. **Data persistence**: No named volume by default, so `docker rm` also drops the database data

For production deployment, use the main `Dockerfile` and configure it appropriately.
