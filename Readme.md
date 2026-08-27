# MQTT Embedded Project

A Docker Compose project that demonstrates a secure MQTT data pipeline:

```text
publisher -> Mosquitto MQTT broker -> subscriber -> PostgreSQL
                                      |
                                      +-> Flask API
```

MQTT connections use TLS and username/password authentication. The subscriber-to-PostgreSQL connection also uses TLS with certificate verification. The publisher generates temperature and humidity measurements every 30 seconds. The subscriber stores them in PostgreSQL and exposes them through an authenticated API.

## Prerequisites

- Docker Engine
- Docker Compose v2 (`docker compose`)
- OpenSSL
- `mosquitto_passwd` (usually provided by the Mosquitto client package)

## Clone the project

```bash
git clone <repository-url> mqtt_embeded_project
cd mqtt_embeded_project
```

The repository intentionally does not contain `.env` files, passwords, private keys, certificates, or generated Mosquitto data. Create the following files before starting the stack.

## Create environment files

Create `.env_publisher`:

```bash
cat > .env_publisher <<'EOF'
MQTT_HOST=mosquitto
MQTT_PORT=8883
MQTT_USERNAME=mosquitto
MQTT_PASSWORD=change-this-mqtt-password
MQTT_TOPIC=meter/data
MQTT_CA_CERT=/app/ca.crt
EOF
```

Create `.env_postgres`:

```bash
cat > .env_postgres <<'EOF'
POSTGRES_USER=postgres
POSTGRES_PASSWORD=change-this-postgres-password
POSTGRES_DB=measurements
EOF
```

Create `.env_subscriber`. Use the same MQTT credentials as `.env_publisher` and the same PostgreSQL password as `.env_postgres`:

```bash
cat > .env_subscriber <<'EOF'
MQTT_HOST=mosquitto
MQTT_PORT=8883
MQTT_USERNAME=mosquitto
MQTT_PASSWORD=change-this-mqtt-password
MQTT_TOPIC=meter/data
MQTT_CA_CERT=/app/ca.crt
DATABASE_URL=postgresql://postgres:change-this-postgres-password@postgres:5432/postgres?sslmode=verify-full&sslrootcert=/app/postgres-ca.crt
WEB_PORT=5000
WEB_HOST=0.0.0.0
AUTH_SEED=replace-with-a-random-seed
AUTH_PASSWORD_HASH=replace-with-a-sha256-hash
EOF
```

Generate a random API seed and replace `AUTH_SEED`:

```bash
openssl rand -hex 32
```

The API password hash is SHA-256 of `api-password + AUTH_SEED`. Generate it after choosing the API password and seed:

```bash
python3 - <<'PY'
import hashlib

password = "replace-with-api-password"
seed = "replace-with-a-random-seed"
print(hashlib.sha256((password + seed).encode("utf-8")).hexdigest())
PY
```

Put the resulting value in `AUTH_PASSWORD_HASH`. Do not use the MQTT or PostgreSQL password as the API password.

## Create Mosquitto credentials and certificates

Create the broker password file. The username and password must match the values in both MQTT environment files:

```bash
mkdir -p mosquitto/config mosquitto/data mosquitto/log
mosquitto_passwd -c mosquitto/config/passwd mosquitto
```

Generate a private CA, then a Mosquitto server certificate valid for the Docker hostname `mosquitto`:

```bash
cd mosquitto/config

openssl genrsa -out ca.key 4096
openssl req -x509 -new -nodes -key ca.key -sha256 -days 3650 \
  -out ca.crt \
  -subj "/C=US/O=MQTT Embedded Project/CN=MQTT Local CA"

openssl genrsa -out server.key 2048
openssl req -new -key server.key -out server.csr \
  -subj "/C=US/O=MQTT Embedded Project/CN=mosquitto"

cat > server.ext <<'EOF'
authorityKeyIdentifier=keyid,issuer
basicConstraints=CA:FALSE
keyUsage=digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=DNS:mosquitto
EOF

openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key \
  -CAcreateserial -out server.crt -days 825 -sha256 -extfile server.ext

chmod 600 ca.key server.key
chmod 644 ca.crt server.crt
cd ../..
```

The broker uses `ca.crt`, `server.crt`, and `server.key`. Clients trust `ca.crt`; never distribute the CA private key or server private key.

## Create PostgreSQL certificates

The PostgreSQL server certificate must contain `DNS:postgres`, because `postgres` is the hostname in `DATABASE_URL`:

```bash
mkdir -p postgres/certs
cd postgres/certs

openssl genrsa -out ca.key 4096
openssl req -x509 -new -nodes -key ca.key -sha256 -days 3650 \
  -out ca.crt \
  -subj "/C=US/O=MQTT Embedded Project/CN=PostgreSQL Local CA"

openssl genrsa -out server.key 2048
openssl req -new -key server.key -out server.csr \
  -subj "/C=US/O=MQTT Embedded Project/CN=postgres"

cat > server.ext <<'EOF'
authorityKeyIdentifier=keyid,issuer
basicConstraints=CA:FALSE
keyUsage=digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=DNS:postgres
EOF

openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key \
  -CAcreateserial -out server.crt -days 825 -sha256 -extfile server.ext

chmod 600 ca.key server.key
chmod 644 ca.crt server.crt
openssl verify -CAfile ca.crt server.crt
cd ../..
```

The PostgreSQL image copies the server certificate and private key during its build. The subscriber receives only `postgres/certs/ca.crt` and verifies the server with `sslmode=verify-full`.

## Start the stack

Build the publisher, subscriber, and PostgreSQL images, then start all services:

```bash
docker compose build
docker compose up -d
```

Check service health and logs:

```bash
docker compose ps
docker compose logs -f mqtt_meter_subscriber
```

PostgreSQL has a health check, so the subscriber waits until the database accepts connections. The publisher continuously publishes a measurement every 30 seconds.

## API

The API is available at `http://localhost:5000`. Requests require a bearer token whose value is the plaintext API password used to create `AUTH_PASSWORD_HASH`:

```bash
curl -H "Authorization: Bearer replace-with-api-password" \
  "http://localhost:5000/measurements"
```

Filter measurements by an optional inclusive ISO-8601 time range:

```bash
curl -H "Authorization: Bearer replace-with-api-password" \
  "http://localhost:5000/measurements?start=2026-08-27T00:00:00Z&end=2026-08-27T23:59:59Z"
```

Invalid dates, or a start date later than the end date, return HTTP `400`. Missing or invalid authentication returns HTTP `401`.

## Useful checks

Verify the subscriber's PostgreSQL connection is encrypted:

```bash
docker compose exec mqtt_meter_subscriber python -c \
  'import os, psycopg2; c=psycopg2.connect(os.environ["DATABASE_URL"]); cur=c.cursor(); cur.execute("SELECT ssl, version, cipher FROM pg_stat_ssl WHERE pid=pg_backend_pid()"); print(cur.fetchone()); c.close()'
```

The expected result has `True` for SSL and a TLS version/cipher, for example `TLSv1.3`.

## Stop and reset

Stop the services:

```bash
docker compose down
```

To remove local PostgreSQL data as well, remove the generated data directory used by the current Docker setup only after confirming that its contents are disposable. You will need to recreate any database initialization state afterward.

## Security notes

- Do not commit `.env*`, passwords, private keys, certificate requests, serial files, generated certificates, database data, or logs.
- The example passwords are placeholders and must be replaced.
- For production, use a managed secret store and a CA managed for the deployment environment.
- The Flask development server is suitable for local testing only; use a production WSGI server for deployment.

## Automatic subscriber updates

The subscriber includes an opt-in updater. It is disabled by default because the repository currently has no GitHub Releases. Enable it only after publishing signed release assets.

Each release must contain these assets:

- `subscriber.tar.gz`
- `subscriber.tar.gz.sha256`
- `subscriber.tar.gz.sig`

The archive must contain `subscriber/subscriber.py`, `subscriber/updater.py`, and `subscriber/version.txt`. The release tag must match the version in `version.txt` (for example, tag `v0.2.0` and file content `0.2.0`). The checksum is SHA-256 of the archive. The signature is an Ed25519 signature of the archive, encoded as base64. The public key is stored in `UPDATE_PUBLIC_KEY`, also base64 encoded.

Generate an Ed25519 signing key pair with a trusted key-management process. Run this once from a private directory. Never commit or upload the private key:

```bash
mkdir -p update-keys
openssl genpkey -algorithm ED25519 -out update-keys/update-signing.key
openssl pkey -in update-keys/update-signing.key -pubout -out update-keys/update-signing.pub
chmod 600 update-keys/update-signing.key
```

The updater expects the raw 32-byte Ed25519 public key, base64 encoded. Put this value in `UPDATE_PUBLIC_KEY`:

```bash
openssl pkey -in update-keys/update-signing.key -pubout -outform DER \
  | tail -c 32 | base64 -w 0
```

Create and sign a release artifact. The `-rawin` option is required for Ed25519 signatures with OpenSSL 3:

```bash
mkdir -p release/subscriber
cp subscriber/subscriber.py subscriber/updater.py subscriber/version.txt release/subscriber/
tar -czf subscriber.tar.gz -C release subscriber
sha256sum subscriber.tar.gz > subscriber.tar.gz.sha256
openssl pkeyutl -sign -rawin -inkey update-keys/update-signing.key \
  -in subscriber.tar.gz -out subscriber.tar.gz.sig.bin
base64 -w 0 subscriber.tar.gz.sig.bin > subscriber.tar.gz.sig
rm subscriber.tar.gz.sig.bin
```

Upload `subscriber.tar.gz`, `subscriber.tar.gz.sha256`, and the base64 text file `subscriber.tar.gz.sig` to a GitHub Release. The release tag must match the version in `subscriber/version.txt`.

Configure the subscriber in `.env_subscriber`:

```text
UPDATE_ENABLED=true
UPDATE_GITHUB_REPOSITORY=mdimec4/mqtt_embeded_projec
UPDATE_INTERVAL_SECONDS=3600
UPDATE_STARTUP_TIMEOUT_SECONDS=20
UPDATE_PUBLIC_KEY=base64-encoded-ed25519-public-key
UPDATE_SMTP_HOST=smtp.example.com
UPDATE_SMTP_PORT=587
UPDATE_SMTP_USERNAME=smtp-user
UPDATE_SMTP_PASSWORD=smtp-password
UPDATE_EMAIL_FROM=from@example.com
UPDATE_EMAIL_TO=to@example.com
```

The subscriber checks the latest release at the configured interval. It downloads the archive, checksum, and signature over HTTPS, verifies both checksum and Ed25519 signature, compiles the candidate code, backs up the running version, and restarts. The launcher confirms startup before deleting the backup. If startup fails, it restores the previous version and sends a rollback email. Email notifications are skipped when `UPDATE_SMTP_HOST` or `UPDATE_EMAIL_TO` is not configured.

The updater changes subscriber application files only. It does not replace `.env` files, certificates, PostgreSQL data, Mosquitto configuration, or Docker images.
