# Matcharr

**Swipe together. Match a movie. Start watching.**

Matcharr connects one household or self-hosted instance to Plex or Jellyfin,
turns its movie library into swipeable cards, and announces a match only when every eligible
person in a room likes the same movie. It is a Flask application with Jinja
templates and vanilla JavaScript; it does not use React or user accounts.

## How it works

1. Enter a display name. Matcharr creates a random participant UUID and keeps it
   in the signed Flask session cookie.
2. Create a room or join with its short invite code.
   Room members can also share a quick-join link or QR code; new visitors enter
   only a display name before joining.
3. The owner creates a lobby. Members can suggest bonus titles from the synced
   media library and mark themselves ready before the owner starts.
4. Matcharr freezes both the eligible member list and the candidate movie pool,
   then appends the lobby's bonus picks.
5. Members swipe independently. Touch, mouse, stylus, keyboard arrows, and
   visible buttons are supported.
6. A RustyDB transaction records each decision and creates one match when every
   frozen eligible member has liked a movie and nobody has skipped it.
7. Current cards are saved across refreshes; members can see group progress and
   undo their latest non-matching swipe.
8. At completion, matches are ranked and everyone casts a final vote for
   tonight's winner. Sessions can be deleted by the owner and expire after 24 hours.

There are no usernames, emails, or passwords. A display name is presentation,
not proof of identity. Clearing cookies can permanently disconnect a browser
from its old anonymous participant identity. A room owner can remove abandoned
memberships so a name can be used again.

## Architecture

```text
Browser (Jinja + CSS + Pointer Events)
          |
          v
Flask routes / JSON API / CSRF + room authorization
          |
          +--> room, session, swipe, match services
          |             |
          |             v
          |      RustyDB abstraction
          |      mysql-connector-python wire client
          |             |
          |             v
          |      RustyDB MySQL wire protocol :3307
          |
          +--> Plex/Jellyfin adapter --> configured media server
                  |
                  +--> metadata cached in RustyDB
                  +--> authenticated artwork proxied by Flask
```

Open room, swipe, and final-vote pages check for updates every 750 ms by default.
Set `MATCHARR_POLL_INTERVAL_MS` to change this interval. Pages check again as
soon as they return to the foreground or the browser reconnects.

## RustyDB compatibility

RustyDB is required; Matcharr never falls back to SQLite, PostgreSQL, MySQL, or
another database. The schema and driver layer are tested against the current
[`ghcr.io/ipandral/rustydb`](https://github.com/IPandral/RustyDB/pkgs/container/rustydb)
image pinned by digest in Compose. That RustyDB image is built for Linux
AMD64, so the bundled stack requires an AMD64 host or emulation.

Relevant implemented behavior:

- supported values are `INTEGER`, `FLOAT`, `TEXT`, `BOOLEAN`, and `NULL`;
- UUIDs are generated in Python and stored as `TEXT`;
- dates are Unix timestamps in `INTEGER`; JSON is serialized into `TEXT`;
- writes use binary-protocol parameters; parameterized reads use the text-result
  protocol for reliable numeric decoding; transactions use connection-local
  `BEGIN`/`COMMIT`/`ROLLBACK`;
- Matcharr uses pure-Python Connector/Python with certificate and hostname
  verification, `caching_sha2_password` authentication, and autocommit enabled
  outside explicit transactions;
- trusted application booleans use fixed `TRUE`/`FALSE` SQL variants because
  Connector/Python encodes Python `bool` as an integer parameter while RustyDB
  strictly distinguishes `BOOLEAN` and `INTEGER`; all user text and numeric
  values remain parameter-bound;
- typed base-table reads are used after joined candidate selection because
  RustyDB v0.3.0-beta reports joined wildcard prepared-result metadata as text;
- `PRIMARY KEY`, composite `UNIQUE`, `CHECK`, indexes, and `RESTRICT` foreign
  keys are implemented;
- transaction conflicts include `Serialization conflict`; Matcharr retries the
  complete transaction with bounded randomized backoff;
- cascades, recursive CTEs, window functions, lateral/full joins, and native
  JSON/date/UUID types are not used.

Migrations use one connection and transaction per file. Run them with:

```bash
flask --app run.py db-migrate
```

## Media server setup

Copy [`.env.example`](.env.example) to `.env` next to `docker-compose.yml`.
Generate two independent random values by running the command below twice.
Use the first value for `SECRET_KEY` and the second for `RUSTYDB_PASSWORD`.
Compose passes the database password to both containers at startup. Git and
Docker builds exclude your local `.env`.

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

Choose one provider using `MEDIA_SERVER_TYPE`. Use a server address reachable
from the Matcharr container. For a media server running on the Docker Desktop
host, use `host.docker.internal` as the hostname. For a separate LAN server, use
its LAN IP or hostname. Include any configured base path, such as `/jellyfin`.

### Plex

1. Set `PLEX_URL` to your Plex Media Server address, usually on port 32400.
2. In Plex Web, open a movie's **Get Info** menu and select **View XML**.
   Copy the `X-Plex-Token` value from the XML page URL into `PLEX_TOKEN`.
   This produces a temporary token. For persistent authentication, follow the
   third-party developer guidance linked from Plex's instructions.
   [Plex token instructions](https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/)
3. Request `/library/sections` on your Plex server with the token in the
   `X-Plex-Token` request header. Find the movie library's `Directory` element
   and copy its numeric `key` into `PLEX_LIBRARY_ID`. Use its title for
   `PLEX_LIBRARY_NAME`.
   [Plex library listing](https://support.plex.tv/articles/201638786-plex-media-server-url-commands/)

```env
MEDIA_SERVER_TYPE=plex
PLEX_URL=http://192.168.1.100:32400
PLEX_TOKEN=replace-this
PLEX_LIBRARY_ID=1
PLEX_LIBRARY_NAME=Movies
```

### Jellyfin

1. Set `JELLYFIN_URL` to your Jellyfin server address, usually on port 8096.
2. Sign in as a Jellyfin administrator. Open **Dashboard → API Keys**, create
   a key named **Matcharr**, and copy the key into `JELLYFIN_API_KEY`.
3. Open **Dashboard → Users** and select the account whose library access and
   watched status Matcharr should use. Copy the `userId` value from the
   user editor's address bar into `JELLYFIN_USER_ID`. You can also request
   `GET /Users` with the API key and copy the selected account's `Id`.
   [Jellyfin user management](https://jellyfin.org/docs/general/server/users/adding-managing-users/)
   [Jellyfin authorization header](https://gist.github.com/nielsvanvelzen/ea047d9028f676185832e51ffaf12a6f)
4. Set `JELLYFIN_LIBRARY_NAME` to the name shown in Jellyfin, such as
   `Movies`. Leave `JELLYFIN_LIBRARY_ID` blank to look up the library by name
   among the selected user's views. To set the ID directly, request
   `GET /Users/<user-id>/Views` and copy the movie library's `Id`.

```env
MEDIA_SERVER_TYPE=jellyfin
JELLYFIN_URL=http://192.168.1.100:8096
JELLYFIN_API_KEY=replace-this
JELLYFIN_USER_ID=replace-this
JELLYFIN_LIBRARY_ID=
JELLYFIN_LIBRARY_NAME=Movies
```

Tokens stay server-side: they are not emitted in HTML, JavaScript, API
responses, image URLs, health output, or user-facing errors. Poster and
background routes proxy authenticated images from cached server-side paths.

Run a sync from a room page or:

```bash
flask --app run.py media-sync
```

Matcharr also starts one background sync automatically after the running app
receives its first request. This does not delay app readiness. Manual sync
remains available as a refresh-now action; overlapping automatic, web, or CLI
sync attempts are skipped. Set `MATCHARR_AUTO_SYNC=false` to disable only the
automatic startup refresh.

The synchronizer reads the configured movie library, normalizes Plex or
Jellyfin metadata, and updates the same cache. The default weighted pool is 25%
recently added, 25% recently released, and 50% random. Room owners can move all
three sliders from 0–100; Matcharr treats them as relative weights and
normalizes them automatically. Existing combined, recently added, popular,
highly rated, and unwatched feeds remain available.

## Local development

1. Create a Python 3.15 virtual environment.
2. Install `requirements.txt`.
3. Start RustyDB with its `server` feature and TLS enabled. Configure the
   values from `.env.example` and set `RUSTYDB_HOST` to a hostname or IP
   covered by the server certificate.
4. Apply migrations and sync the selected media server.
5. Run Flask:

```bash
pip install -r requirements.txt
flask --app run.py db-migrate
flask --app run.py media-sync
flask --app run.py run --debug
```

Set `SECRET_KEY`, `RUSTYDB_PASSWORD`, and `RUSTYDB_TLS_CA` (the path to the
PEM CA bundle that signed the RustyDB server certificate) in your shell
before running these commands. Generate a signing key with
`python -c "import secrets; print(secrets.token_hex(32))"`.
For plain HTTP local development, set `MATCHARR_SECURE_COOKIES=false`. Keep it
`true` behind HTTPS in production.

## Docker deployment

Matcharr images are hosted on [GitHub Container Registry](https://github.com/users/IPandral/packages/container/package/matcharr).
Pull the latest image directly with:

```bash
docker pull ghcr.io/ipandral/matcharr:latest
```

To run Matcharr with RustyDB, place [`docker-compose.yml`](docker-compose.yml)
and [`.env.example`](.env.example) in the same directory. Copy the template
to `.env` with `cp .env.example .env` on macOS or Linux, or
`Copy-Item .env.example .env` in PowerShell. Complete the
[media server setup](#media-server-setup). For local testing over plain HTTP,
set `MATCHARR_SECURE_COOKIES=false`.

RustyDB requires a certificate for the Compose hostname `rustydb`. Place
`ca.crt`, `server.crt` (with any intermediates), and `server.key` in the
directory set by `RUSTYDB_TLS_DIR`, which defaults to `./rustydb-tls`.
To generate a private CA and a one-year server certificate, download
[`scripts/generate-rustydb-tls.sh`](scripts/generate-rustydb-tls.sh) and run
`bash generate-rustydb-tls.sh` from the Compose directory with Bash and
OpenSSL installed. On Linux, grant the RustyDB container user read access to the
private key with `sudo chown 1000 ./rustydb-tls/server.key` and keep its mode
at `0600`. Matcharr mounts only the CA certificate.

For upgrades, [back up the existing database](#backup-and-recovery) first and
replace the Compose file and Matcharr image together. Keep the existing Compose
project name and `rustydb_data` volume. Once the certificates and credentials are
in place, start the stack:

```bash
docker compose pull
docker compose up -d
```

Compose pulls `ghcr.io/ipandral/matcharr:latest` and the pinned
`ghcr.io/ipandral/rustydb` image. Set `MATCHARR_IMAGE` in `.env` to use a
specific Matcharr version, such as `ghcr.io/ipandral/matcharr:1.0.0`.
If the Matcharr package is private, sign in to `ghcr.io` before pulling.
RustyDB exposes its TLS-enabled MySQL wire port (3307) and HTTPS port (8080)
only on the internal Compose network.
Matcharr waits for its health check, applies migrations, and starts Gunicorn.
Only the Matcharr HTTP port is published, on the host loopback interface by
default. Set `MATCHARR_BIND_ADDRESS` to the host's LAN address if trusted
devices should connect directly.

You can also pull a specific application release directly:

```bash
docker pull ghcr.io/ipandral/matcharr:1.0.0
```

## Container releases

GitHub Actions builds Linux `amd64` and `arm64` images. Each new commit that
passes tests and publishes its image from `main` gets the next minor GitHub
Release, starting with `v1.0.0`, then `v1.1.0`, `v1.2.0`, and so on. The
workflow publishes the full version, minor, major, `latest`, and commit image
tags. A rerun of an already released commit reuses its version.

Push a semantic version tag such as `v2.0.0` to publish that Docker version
manually. Pull requests build and test without publishing. CI runs the Python
integration suite against the same RustyDB image pinned in Compose.

After the first workflow run, the repository owner must open the Matcharr
package settings on GitHub and change its visibility to **Public** so
unauthenticated servers can pull it. The image includes OCI source metadata and
the workflow publishes a GitHub artifact attestation whenever the repository
is public. Private user-owned repositories skip attestation because GitHub does
not provide that feature for them.

Terminate TLS at a trusted reverse proxy and retain
`MATCHARR_SECURE_COOKIES=true`. `/health` reports Matcharr/RustyDB availability
and the last media sync status without exposing credentials or private
connection details.

## Configuration

See `.env.example` for all supported settings. Important operational controls
include invite length and throttling, swipe distance/velocity, transaction
retry count, poll interval, request-size limit, RustyDB wire credentials, and
media-server connection values. `SECRET_KEY`, `RUSTYDB_PASSWORD`,
`PLEX_TOKEN`, and `JELLYFIN_API_KEY` must be injected through the environment
and must never be committed.

Set `MATCHARR_PUBLIC_URL` (for example, `https://matcharr.example.com`) when
running behind a reverse proxy whose public host differs from the incoming
Docker request. Matcharr uses it for share links and QR codes. Leave it empty
to use the request host automatically.

## Testing

```bash
pytest
npm test
```

Backend unit tests use in-memory fakes or scripted database connections, not
SQLite. Real integration tests require a live RustyDB wire server with TLS.
Set `RUSTYDB_HOST`, `RUSTYDB_WIRE_PORT`, `RUSTYDB_USERNAME`,
`RUSTYDB_PASSWORD`, and `RUSTYDB_TLS_CA` for that server:

```bash
MATCHARR_RUSTYDB_INTEGRATION=1 pytest -m integration
```

The integration suite validates parameterized queries, migration syntax,
transactions, rollback, and conflict behavior over the actual MySQL wire
protocol.

## Feedback and security

Bug reports and feature requests are welcome through the repository issue
forms. This project does not accept code contributions or pull requests. See
[SECURITY.md](SECURITY.md) for private vulnerability reporting.

## License

Copyright © 2026 IPandral. Matcharr is available under the [MIT License](LICENSE).

## Security

- signed, `HttpOnly`, `SameSite=Lax` sessions and production `Secure` cookies;
- CSRF tokens on HTML forms and JSON mutations;
- server-side membership, frozen-session eligibility, candidate-pool, and
  owner checks;
- prepared driver parameters for all user-controlled data;
- cryptographically random invite codes and participant/request UUIDs;
- session-window invalid-invite throttling;
- idempotent swipe requests and database uniqueness constraints;
- one-transaction swipe/match creation with serializable conflict retries;
- bounded request bodies, escaped Jinja output, safe errors, and restrictive
  browser security headers.

Invite codes are bearer-like shared secrets. Share them privately and archive
rooms that are no longer in use. Matcharr is intended for a trusted
self-hosted group, not as an internet-scale public identity service.

## Backup and recovery

Back up RustyDB's `/data` volume with RustyDB's documented offline
`backup`/`restore` commands. Keep the base backup and required WAL archives
off-host, test restoration periodically, and do not prune archives until the
corresponding recovery point is safely copied. Back up the application `.env`
through a separate secrets manager; never put it in the database archive.

## Known limitations and future work

- Anonymous identities cannot be recovered after cookie loss.
- Invalid-invite throttling is browser-session scoped; a reverse-proxy rate
  limit should supplement it for a public deployment.
- Discovery uses library metadata available from the configured server;
  popularity is local view count rather than a global trend feed.
- Future improvements include optional SSE/WebSockets, administrator controls,
  richer media-server hubs, browser identity transfer, scheduled sync, and
  end-to-end tests against configured Plex and Jellyfin sandboxes.
