# Filemanager

Standalone file explorer for registered, tenant-owned targets. Hosting supplies
WordPress targets with an internal configurable root (`SPANEL_HOSTING_FILES_ROOT`,
default `/var/www/html`). Requires PHP with ZipArchive in the target container.
Users need `filemanager.files.read` to browse/download and
`filemanager.files.manage` to modify, in addition to the provider's permissions.
The transport works over local Docker or SSH; no host path is exposed to the
browser. See A.SPEC 0023 for the API and invariants.
