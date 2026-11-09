# Apps plugin

Apps provides a tenant-aware, asynchronous API for inspecting and deploying
GitHub Compose applications. GitHub credentials belong to individual users and
are encrypted at rest. Runtime work is handled only by the serialized `apps`
Dramatiq worker; HTTP requests never invoke GitHub CLI, Docker or SSH.

Configuration uses `SPANEL_APPS_*` variables documented in
`.env.docker.example`. Client secret and AES-256-GCM key values are supplied as
read-only files.
