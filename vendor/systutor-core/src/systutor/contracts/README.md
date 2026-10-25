# packages/contracts

Espacio para contratos compartidos entre backend, frontend, plugins y tooling interno.

## `domain_ownership`

Namespace único de dominios entre plugins, sin imports cruzados:

- `register_site_provider(app, name, provider)` — un plugin publica los sitios de
  un tenant (`OwnedSite`: `site_id`, `tenant_id`, `container`,
  `primary_domain`). Lo registra Hosting.
- `register_extra_claims(app, name, checker)` — un plugin responde si un dominio
  ya lo publica como ruta extra. Lo registra el plugin `domains`.
- `list_sites` / `resolve_site` — consultas acotadas al tenant.
- `is_primary_domain` — consulta global: un dominio primario no se puede
  reclamar en ningún tenant.
- `is_extra_claimed` — consulta global: Hosting no crea un sitio con un dominio
  que ya publica otro plugin.

Los duplicados con otra implementación fallan en el registro. El registro vive
en `app.state`, así que las pruebas no comparten estado entre procesos.
