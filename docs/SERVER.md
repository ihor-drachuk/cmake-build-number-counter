# Server Guide

The build number server provides synchronized counters across multiple machines. It is **entirely optional** — everything works locally without it.

## Starting the Server

```bash
python src/server.py --accept-unknown         # auto-approve new projects
python src/server.py --port 9000 --host 127.0.0.1
python src/server.py --data-dir /var/lib/build-counter
```

On client machines, set the environment variable:
```bash
export BUILD_SERVER_URL=http://your-server:8080   # Linux/Mac
set BUILD_SERVER_URL=http://your-server:8080      # Windows
```

## CLI Flags

| Flag | Default | Description |
|------|---------|-------------|
| `--port` | `8080` | Server port |
| `--host` | `0.0.0.0` | Bind address |
| `--data-dir` | `server-data/` | Directory for data files |
| `--accept-unknown` | off | Auto-approve new project keys |
| `--max-body-size` | `1024` | Max request body size in bytes |
| `--max-projects` | `100` | Max project count (`0` = unlimited) |
| `--rate-limit` | `10` | Max requests per minute per IP (`0` = off) |
| `--ban-duration` | `600` | Temp ban duration in seconds |
| `--ban-permanent` | off | Use persistent bans instead of temporary |

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Service info |
| `POST` | `/increment` | Increment and return build number |
| `POST` | `/set` | Force-set build number to exact value |

**`POST /increment`** body:
```json
{"project_key": "myproject", "local_version": 5}
```

**`POST /set`** body:
```json
{"project_key": "myproject", "version": 42}
```

## Token Management

See [Security](SECURITY.md) for full authentication documentation.

```bash
python src/server.py --add-token --token-name "ci" --token-projects "my-app,my-lib"
python src/server.py --add-token --token-name "admin" --token-admin
python src/server.py --list-tokens
python src/server.py --remove-token "ci"
```

## Counter Management

Set counters offline (no server process needed):

```bash
python src/server.py --set-counter --project-key myproject --version 42
python src/server.py --set-counter --project-key myproject --version 0 --data-dir /var/lib/build-counter
```

## Data Storage

**Data file:** `server-data/build_numbers.json`

```json
{
  "myproject": 42,
  "another-project": 15
}
```

**Project key format:**
- Must match `[a-zA-Z0-9._-]`, 1-128 characters
- Examples: `my-app`, `com.example.project`, `BuildServer_v2`
- Invalid keys are rejected with 400

**Project approval:**
- A project key present in the file is approved
- Missing key + `--accept-unknown` flag = auto-added starting at 1
- Missing key without the flag = rejected (403)
- With `--accept-unknown`, max project count is enforced (default: 100, `--max-projects 0` for unlimited)

To add/reset a project manually: stop the server, edit the JSON, restart.

## Deployment

### Linux (systemd)

Create `/etc/systemd/system/build-counter.service`:
```ini
[Unit]
Description=Build Number Counter Server
After=network.target

[Service]
Type=simple
User=YOUR_USERNAME
WorkingDirectory=/path/to/build-counter
ExecStart=/usr/bin/python3 /path/to/build-counter/src/server.py --port 8080 --accept-unknown
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable build-counter
sudo systemctl start build-counter
```

### Windows (NSSM)

Using [NSSM](https://nssm.cc/download):

```cmd
nssm install BuildCounterServer "C:\Python39\python.exe" "C:\path\to\src\server.py --port 8080 --accept-unknown"
nssm set BuildCounterServer AppDirectory "C:\path\to\build-counter"
nssm start BuildCounterServer
```
