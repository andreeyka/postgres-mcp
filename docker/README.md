# Docker Test Environment

**⚠️ This docker-compose setup is for TESTING ONLY.**

This directory contains configuration for running a test environment with MCP server and PostgreSQL.
For production Docker usage, see the main `Dockerfile` and entrypoint in the project root.

**One MCP server = one database.** The server always runs in single-DB mode.

## Structure

- `docker-compose.yml` (in project root) - Main Docker Compose configuration
- `postgres/Dockerfile` - PostgreSQL image with HypoPG extension
- `postgres/init-db.sql` - PostgreSQL initialization script with test databases (4 databases with different access modes)
- `postgres/init-4-databases.sql` - Alternative initialization script (same as init-db.sql)
- `postgres/init-4-test-databases.sql` - Creates 4 test databases (db1, db2, db3, db4) for `test_static_server.py`
- `postgres/init-all-databases.sh` - Bash script to initialize all databases with test data
- `config.json` - Test MCP server configuration for **one** database (see format in `src/postgres_fastmcp/config/__init__.py`)

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

- Docker and Docker Compose installed
- Ports 8000 and 5432 available (or modify in docker-compose.yml)

## Running

### Start the test environment

```bash
# Build and start all services
docker-compose up --build

# Run in background
docker-compose up -d --build
```

### View logs

```bash
# View all logs
docker-compose logs -f

# View MCP server logs only
docker-compose logs -f mcp-server

# View PostgreSQL logs only
docker-compose logs -f postgres
```

### Stop the environment

```bash
# Stop services (keeps data)
docker-compose down

# Stop and remove volumes (will delete all database data)
docker-compose down -v
```

### Rebuild after changes

```bash
# Rebuild and restart
docker-compose up --build --force-recreate
```

## Access

- **MCP server**: <http://localhost:8000/mcp>
- **PostgreSQL**:
  - From host: `localhost:5432`
  - From Docker network: `postgres:5432`
  - Default user: `postgres` / Password: `postgres`

## Network

All services run in an isolated Docker network `mcp-network`.

- Port 8000 for the MCP server is published externally
- Port 5432 for PostgreSQL is published externally (for testing)
- Services communicate via hostname `postgres` inside the network

## Configuration

The MCP server uses `docker/config.json` mounted as `/app/config.json` in the container. The file must have `server`, `fastmcp`, and `database` sections (one database per server).

To use a different test database:

1. Edit `docker/config.json` and change the `database` section (host, port, user, password, name, access_mode, write_mode, table_prefix).
2. Restart: `docker-compose restart mcp-server`

## Troubleshooting

### Port already in use

If ports 8000 or 5432 are already in use, modify them in `docker-compose.yml`:

```yaml
ports:
  - "8001:8000"  # Change host port
  - "5433:5432"  # Change host port
```

### Database connection issues

Check that PostgreSQL is healthy:

```bash
docker-compose ps
docker-compose logs postgres
```

### MCP server not starting

Check MCP server logs:

```bash
docker-compose logs mcp-server
```

Verify the config.json is valid:

```bash
docker-compose exec mcp-server cat /app/config.json | python -m json.tool
```

## Differences from Production

This test environment differs from production in several ways:

1. **PostgreSQL configuration**: Uses test credentials and exposes port 5432
2. **MCP server**: Uses test config with one database (single-DB mode only)
3. **Network**: Isolated test network, not production-ready
4. **Data persistence**: Uses Docker volumes (can be removed with `docker-compose down -v`)

For production deployment, use the main `Dockerfile` and configure it appropriately.
