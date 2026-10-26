# Filemanager

Standalone file explorer for registered, tenant-owned targets. Hosting supplies
WordPress targets with an internal configurable root (`SPANEL_HOSTING_FILES_ROOT`,
default `/var/www/html`) and static targets rooted at `/usr/share/nginx/html`.
WordPress requires PHP with ZipArchive in the target container. Static sites
have no PHP, so the same fixed PHP script runs in a disposable helper container
(`SPANEL_FILEMANAGER_HELPER_IMAGE`, default `wordpress:cli`) that mounts the
site's volumes with `--volumes-from`. Users need `filemanager.files.read` to
browse/download and `filemanager.files.manage` to modify, in addition to the
provider's permissions. The transport works over local Docker or SSH; no host
path is exposed to the browser. See A.SPEC 0023 for the API and invariants.
