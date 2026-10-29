# A.SPEC 0041 — Solicitar el certificado TLS de un dominio nuevo desde el panel

> `risk: high` — relaja una compuerta de visibilidad del gestor de certificados y
> puede encolar emisiones de Certbot para hostnames que todavía no existen en el
> inventario. Un error de autorización permite que un tenant solicite un
> certificado de otro dominio. Exige `approver:` humano.

## WHY

El modal **Certificados TLS** solo lista lo que el agente ya publicó en
`state.json`. Para un dominio nuevo no hay fila y no hay forma de pedir el
certificado desde el panel:

- `CertificatesService.request_certificate` exige que el hostname exista en el
  inventario (`_visible_certificate`). Un `mail.<dominio-nuevo>` ausente
  responde `404 Certificado desconocido`.
- `GET /{hostname}/jobs/{job_id}` usa la misma comprobación, así que aunque se
  encolara una solicitud no se podría seguir su estado antes de que el agente
  la publicara.
- El frontend solo ofrece acciones por fila (`Regenerar`, `Forzar`, etc.). No
  existe un alta de hostname.

La operación actual obliga a editar la configuración del host, refrescar el
estado y recién entonces usar “Regenerar”. El objetivo es que un administrador
pueda pedir la emisión de `mail.<dominio>` para un dominio que ya pertenece al
tenant, con una solicitud validada, sin abrir una vía para hostnames ajenos.

## WHAT

En el modal **Certificados TLS** aparece la acción **Agregar certificado**
(solo con `mail.certificates.manage`). Abre un formulario para un hostname
`mail.<dominio>` y encola una solicitud `ensure` aunque ese hostname todavía no
esté en el inventario.

Reglas de la nueva vía:

- Un tenant solo puede pedir `mail.<d>` cuando `<d>` es uno de sus dominios
  primarios (`list_sites`). Cualquier otro hostname responde `404`.
- El administrador de plataforma (`core.platform.admin`) puede pedir cualquier
  hostname válido con prefijo `mail.`.
- `GET /{hostname}/jobs/{job_id}` aplica la misma regla, para poder seguir el
  job antes del primer `state.json` que incluya el certificado.
- El hostname se normaliza (minúsculas, sin punto final) antes de escribir la
  solicitud.
- La allowlist del agente sigue siendo la autoridad final: si el hostname no
  está en `/etc/spanel/cert-agent.toml`, el agente falla el job y la UI muestra
  el error. Esta A.SPEC **no** modifica el agente en tiempo de ejecución ni
  permite que la web edite su configuración.
- El instalador gana `--add-allowlist <hostname>` (repetible) para agregar
  hostnames a la configuración existente **sin reescribir el resto del TOML**.
  Así el alta de un dominio nuevo no exige recordar y repetir toda la lista de
  hostnames actuales ni perder rutas personalizadas, `[postfix]` o `[dovecot]`.

Verdad falsable: **un tenant puede encolar y consultar un `ensure` para
`mail.<su-dominio>` ausente del inventario, y no puede hacerlo para
`mail.<dominio-de-otro-tenant>`, para `smtp.<su-dominio>` ni para
`mail.sub.<su-dominio>`.**

## SCOPE

Entra:

- `backend/certificates/service.py`:
  - resolución de un hostname ausente del inventario con `_is_new_hostname_allowed`;
  - `request_certificate` y `get_job` usan esa resolución;
  - el comportamiento para certificados existentes y `cf-mail` no cambia.
- Frontend del plugin `mail`:
  - botón **Agregar certificado** en `CertificatesDialog`;
  - `NewCertificateDialog` con validación de forma y advertencia de allowlist;
  - helper puro de validación de hostname;
  - acción `requestNew` en `useCertificateManager`, reutilizando el polling actual.
- Tests del router de certificados y del instalador (ya escritos como contrato
  en esta A.SPEC).
- `scripts/install_cert_agent.py`: flag `--add-allowlist`, helpers
  `_read_allowlist` y `_write_allowlist`, sin tocar el resto de la configuración.
- `docs/certificate-manager.md`: alta de dominio nuevo y prerrequisito de
  allowlist del host.

No entra:

- Cambiar el agente en tiempo de ejecución, su lógica de emisión o permitir que
  la web edite la allowlist.
- Cambiar Certbot, la instalación o el mapa SNI.
- Crear sitios o dominios en `hosting`/`domains`.
- Emitir para wildcards, `cf-mail`, hostnames sin prefijo `mail.` o dominios que
  no sean primarios del tenant.
- Cambiar el esquema de `state.json`, la base de datos o los permisos.
- Reimplementar ACME o el challenge de Certbot.

## CONTRACT

**Precondiciones**

- Existe un `TenantContext` autenticado con `mail.certificates.manage`.
- `list_sites` devuelve los dominios primarios del tenant.
- El hostname solicitado es válido según `normalize_hostname`.

**Postcondiciones**

1. `POST /api/v1/plugins/mail/certificates/{hostname}/request` con
   `{"action":"ensure"}` y `hostname = mail.<dominio-primario-del-tenant>`
   ausente del inventario responde `202` con `hostname` normalizado y
   `job_id`, y escribe `requests/<job_id>.json` con ese hostname y acción.
2. `GET /api/v1/plugins/mail/certificates/{hostname}/jobs/{job_id}` para ese
   mismo hostname responde `200` con `state="queued"` antes de que el hostname
   aparezca en el inventario.
3. El mismo `POST`/`GET` con un hostname de otro tenant, con `smtp.<dominio>`,
   con `<dominio>` o con `mail.sub.<dominio>` responde `404` y no escribe
   ninguna solicitud.
4. El administrador de plataforma puede pedir un `mail.*` válido aunque no sea
   dominio de su tenant.
5. Un hostname que ya existe en el inventario conserva exactamente el
   comportamiento anterior, incluido `409` para `cf-mail`.
6. La UI muestra **Agregar certificado** solo con permiso de gestión; el
   formulario no envía nada si el hostname no tiene la forma `mail.<dominio>`.
7. `python3 scripts/install_cert_agent.py --add-allowlist mail.<nuevo>`
   agrega el hostname normalizado a la allowlist existente y conserva el resto
   del TOML (comentarios, rutas, `[postfix]`, `[dovecot]`) byte a byte.
8. `--add-allowlist` es idempotente: si el hostname ya está, informa
   `sin cambios` y no reescribe el archivo.
9. `--add-allowlist` falla con `InstallError` si el archivo de configuración no
   existe; no inventa una configuración parcial.

**Acción válida:** `ensure` desde el formulario. `force` sigue disponible en
las filas existentes; no se ofrece para altas.

## INVARIANTS

```yaml
invariants:
  - id: I1
    statement: Un certificado existente conserva visibilidad, acciones y el 409 de cf-mail.
    proof: test_request_requires_manage_and_returns_202, test_cf_mail_request_is_conflict, test_detail_hides_other_tenant_and_global, test_second_request_returns_409_with_existing_job.
  - id: I2
    statement: Un tenant no puede pedir ni consultar jobs de un hostname ausente que no sea mail.<su-dominio-primario>.
    proof: test_new_hostname_of_other_tenant_is_not_found, test_new_hostname_job_is_hidden_from_other_tenant.
  - id: I3
    statement: Un hostname nuevo que no sea exactamente mail.<dominio-primario> se rechaza sin escribir solicitud.
    proof: test_new_hostname_must_be_the_mail_host_of_a_domain.
  - id: I4
    statement: El payload sigue rechazando campos extra y acciones inválidas antes de escribir.
    proof: test_new_hostname_rejects_extra_payload_without_writing, test_invalid_hostname_and_extra_payload_are_rejected.
  - id: I5
    statement: El agente en ejecución no se modifica; la allowlist solo cambia por el instalador y nunca se exponen claves privadas ni contenido PEM.
    proof: git diff --stat sin scripts/spanel_cert_agent/**; test_state_with_private_key_is_not_served y los tests de allowlist del instalador.
  - id: I6
    statement: El listado, la salud, las alertas y el inventario no cambian de forma.
    proof: test_list_filters_tenant_and_requires_read, test_platform_admin_sees_global, test_state_with_private_key_is_not_served.
  - id: I7
    statement: Agregar un hostname a la allowlist no pierde ningún otro valor de la configuración del agente.
    proof: test_add_allowlist_mergea_sin_perder_el_resto_del_archivo, test_add_allowlist_agrega_la_clave_si_falta.
  - id: I8
    statement: La edición de allowlist es idempotente y no escribe en dry-run ni cuando no hay cambios.
    proof: test_add_allowlist_dry_run_no_escribe, test_add_allowlist_sin_cambios_no_reescribe, test_add_allowlist_exige_config_existente.
```

## VERIFICATION

Tests del contrato (ya escritos en `test_certificates_router.py`):

| Test | Verifica |
|---|---|
| `test_request_accepts_new_mail_hostname_of_tenant_domain` | postcondiciones 1 y 2 |
| `test_new_hostname_of_other_tenant_is_not_found` | I2 |
| `test_new_hostname_must_be_the_mail_host_of_a_domain` | I3 |
| `test_platform_admin_can_request_unlisted_mail_hostname` | postcondición 4 |
| `test_new_hostname_job_is_hidden_from_other_tenant` | I2 |
| `test_new_hostname_rejects_extra_payload_without_writing` | I4 |
| `test_add_allowlist_mergea_sin_perder_el_resto_del_archivo` | I7 |
| `test_add_allowlist_agrega_la_clave_si_falta` | I7 |
| `test_add_allowlist_dry_run_no_escribe` | I8 |
| `test_add_allowlist_sin_cambios_no_reescribe` | I8 |
| `test_add_allowlist_exige_config_existente` | I8 |
| `test_parser_acepta_add_allowlist_repetible` | postcondición 7 |
| `test_parser_no_mezcla_allowlist_con_add_allowlist` | postcondición 7 |

Comandos:

```bash
cd vendor/systutor-core
../../.venv/bin/python -m pytest plugins/mail/backend/tests/test_certificates_router.py -q
../../.venv/bin/python -m pytest plugins/mail/backend/tests -q
cd /data/data/com.termux/files/home/Proyectos/mailadmin
../../.venv/bin/python -m pytest scripts/tests/test_install_cert_agent.py -q
ruff check vendor/systutor-core/plugins/mail/backend/certificates \
  vendor/systutor-core/plugins/mail/backend/tests/test_certificates_router.py \
  scripts/install_cert_agent.py scripts/tests/test_install_cert_agent.py
cd apps/web && npm run build
```

Esperado: los 16 tests del router y los 16 del instalador pasan, la suite
completa del plugin pasa, `ruff` no reporta errores y `tsc --noEmit` + build de
Vite terminan sin errores (el formulario nuevo compila y no rompe el bundle).

Prueba manual mínima (host con agente instalado):

1. `python3 scripts/install_cert_agent.py --add-allowlist mail.nuevo.com` y
   `spanel-cert-agent refresh`.
2. Abrir **Correos → Certificados TLS → Agregar certificado**, ingresar
   `mail.nuevo.com` y solicitar.
3. Verificar que el job pasa a `succeeded` y que la fila aparece como SNI.
4. Confirmar que `grep allowlist /etc/spanel/cert-agent.toml` conserva los
   hostnames anteriores y las secciones `[postfix]`/`[dovecot]`.

**No verificado acá**: la emisión real con Let’s Encrypt y la recarga de
Postfix/Dovecot dependen del host y de la allowlist; las pruebas automatizadas
solo cubren la API, la validación y la UI.

## ROLLBACK

Sin migración ni estado persistente nuevo.

- `git revert` del commit elimina la acción de UI y restaura la comprobación
  estricta de inventario.
- Las solicitudes ya escritas en `/var/lib/spanel/cert-manager/requests/` las
  consume el agente aunque se revierta la web. Para detenerlas, borrar los
  archivos `requests/<job_id>.json` pendientes antes de que el agente los lea.
- Los certificados ya emitidos no se revierten desde el panel; se administran
  con el rollback existente de la A.SPEC 0036.
- Para deshacer un alta de allowlist, quitar el hostname del array
  `allowlist` en `/etc/spanel/cert-agent.toml` y ejecutar
  `spanel-cert-agent refresh`. No se borran certificados ni linajes.

## Change Surface

```yaml
change_surface:
  allowed:
    - aspecs/0041-mail-new-domain-certificate-request.md
    - vendor/systutor-core/plugins/mail/backend/certificates/service.py
    - vendor/systutor-core/plugins/mail/backend/tests/test_certificates_router.py
    - vendor/systutor-core/plugins/mail/frontend/api.ts
    - vendor/systutor-core/plugins/mail/frontend/components/CertificatesDialog.tsx
    - vendor/systutor-core/plugins/mail/frontend/components/certificates/NewCertificateDialog.tsx
    - vendor/systutor-core/plugins/mail/frontend/components/certificates/certificateHostname.ts
    - vendor/systutor-core/plugins/mail/frontend/components/certificates/useCertificateManager.ts
    - scripts/install_cert_agent.py
    - scripts/tests/test_install_cert_agent.py
    - docs/certificate-manager.md
  prohibited:
    - scripts/spanel_cert_agent/**
    - vendor/systutor-core/plugins/mail/backend/certificates/inventory.py
    - vendor/systutor-core/plugins/mail/backend/certificates/jobs.py
    - vendor/systutor-core/plugins/mail/backend/certificates/router.py
    - vendor/systutor-core/plugins/mail/backend/certificates/schemas.py
    - vendor/systutor-core/plugins/mail/backend/certificates/validation.py
    - vendor/systutor-core/src/**
    - docker-compose.yml
```

## Blast Radius

```yaml
blast_radius:
  direct:
    - POST /api/v1/plugins/mail/certificates/{hostname}/request para hostnames ausentes
    - GET /api/v1/plugins/mail/certificates/{hostname}/jobs/{job_id}
    - modal Certificados TLS
  indirect:
    - jobs de emisión escritos en /var/lib/spanel/cert-manager/requests/
    - emisión de Certbot y mapa SNI cuando el agente procesa el job
    - /etc/spanel/cert-agent.toml vía --add-allowlist
  must_not_affect:
    - inventario, salud y alertas existentes
    - certificado global cf-mail
    - visibilidad por tenant de certificados existentes
    - lógica de emisión del agente y su validación de allowlist
    - resto de la configuración del agente (rutas, postfix, dovecot)
    - claves privadas o contenido PEM
```

## Composition

```yaml
composition:
  requires_aspecs: ["0035", "0036"]
  must_compose_with: []
  systemic_invariants:
    - El agente sigue siendo la única autoridad de emisión e instalación.
    - Un tenant nunca ve ni opera certificados de otro tenant.
  composition_checks:
    - pytest plugins/mail/backend/tests -q
    - npm run build (apps/web)
```

## Structural Constraints

```yaml
structural_constraints:
  primary_rule: one coherent responsibility and one main reason to change
  entrypoints_must_stay_thin: true
  review_threshold_lines: 400
  extraction_threshold_lines: 600
  preferred_new_logic_locations:
    - vendor/systutor-core/plugins/mail/frontend/components/certificates/
```

## Traceability

- Requirement: pedir el certificado TLS de `mail.<dominio-nuevo>` desde el panel sin abrir una vía cross-tenant.
- owner: luc444s
- approver: luc444s
- Commit:
- Deployment: rebuild de la imagen `app`; no requiere variables nuevas.

## Definition of Done

- [ ] Objective satisfied
- [ ] Scope respected
- [ ] Contract satisfied
- [ ] Independent falsable truth exists now
- [ ] Invariants preserved
- [ ] Verification passed
- [ ] Rollback / compensation is honest
- [ ] Composition checks passed when applicable
- [ ] No unrelated changes
- [ ] Structural constraints respected
- [ ] Traceability established
