# Docker Test Environment

**⚠️ This docker-compose setup is for TESTING ONLY.**

This directory contains configuration for running a test environment with MCP server and PostgreSQL.
For production Docker usage, see the main `Dockerfile` and `docker-entrypoint.sh` in the project root.

## Structure

- `docker-compose.yml` (in project root) - Main Docker Compose configuration
- `postgres/Dockerfile` - PostgreSQL image with HypoPG extension
- `postgres/init-db.sql` - PostgreSQL initialization script with test databases (4 databases with different access modes)
- `postgres/init-4-databases.sql` - Alternative initialization script (same as init-db.sql)
- `postgres/init-4-test-databases.sql` - Creates 4 test databases (db1, db2, db3, db4) for `test_static_server.py`
- `postgres/init-all-databases.sh` - Bash script to initialize all databases with test data
- `config.json` - Test MCP server configuration with 4 databases

## Test Databases

The initialization scripts create test databases with different access modes:

1. **user_ro_db** - Read-only access, public schema only
   - User: `user_ro` / Password: `password`
   - Role: `user`, Access Mode: `restricted`
   - Table prefix: `app_`

2. **user_rw_db** - Read-write access, public schema only
   - User: `user_rw` / Password: `password`
   - Role: `user`, Access Mode: `unrestricted`

3. **admin_ro_db** - Read-only access, all schemas
   - User: `admin_ro` / Password: `password`
   - Role: `full`, Access Mode: `restricted`

4. **admin_rw_db** - Full access, all schemas
   - User: `postgres` / Password: `postgres`
   - Role: `full`, Access Mode: `unrestricted`

Additionally, `init-4-test-databases.sql` creates 4 databases (db1, db2, db3, db4) with test data for integration testing.

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

The MCP server uses `docker/config.json` which is mounted as `/app/config.json` in the container.
This configuration includes all 4 test databases with their respective access modes.

To modify the configuration:

1. Edit `docker/config.json`
2. Restart the MCP server: `docker-compose restart mcp-server`

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
2. **MCP server**: Uses test configuration with multiple databases
3. **Network**: Isolated test network, not production-ready
4. **Data persistence**: Uses Docker volumes (can be removed with `docker-compose down -v`)

For production deployment, use the main `Dockerfile` and configure it appropriately.
