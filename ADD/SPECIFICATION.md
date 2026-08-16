# ADD — Specification

Definición normativa de qué significa cumplir ADD.

## 1. Unidad fundamental: A.SPEC

Una Atomic Specification (A.SPEC) es la unidad canónica de ADD.

Ejemplo:

```
A.SPEC HOST-0001
Docker WordPress Discovery
```

Una A.SPEC responde obligatoriamente:

| Sección      | Pregunta que responde                                    |
|--------------|----------------------------------------------------------|
| WHY          | ¿Qué problema concreto existe?                           |
| WHAT         | ¿Qué comportamiento observable cambia?                   |
| SCOPE        | ¿Qué entra?                                              |
| OUT OF SCOPE | ¿Qué explícitamente NO entra?                            |
| CONTRACT     | ¿Qué debe cumplirse?                                     |
| INVARIANTS   | ¿Qué comportamiento existente no puede romperse?         |
| VERIFICATION | ¿Cómo demostramos que funciona?                          |
| ROLLBACK     | ¿Cómo deshacemos el cambio?                              |

Una SPEC no es documentación: es un contrato de cambio.

## 2. Definición de atomicidad

"Atómico" **no** significa "pocas líneas de código".

Una modificación de 300 líneas puede representar un solo cambio conceptual,
mientras que una de 15 líneas puede mezclar tres comportamientos.

**Una A.SPEC es atómica cuando representa una sola transición observable
del sistema.**

Contraejemplo — NO atómica:

```
HOST-0001 "Implementar administración de WordPress"
  incluye: discovery, restart, logs, backup, creación, SSL
```

Correcto:

```
HOST-0001 → Discover existing WordPress
HOST-0002 → Assign discovered site to tenant
HOST-0003 → Restart site
HOST-0004 → Read container logs
HOST-0005 → Create database backup
HOST-0006 → Restore database backup
HOST-0007 → Provision WordPress
HOST-0008 → Attach domain
HOST-0009 → Provision SSL
```

Cada una produce un cambio observable.

## 3. Las 5 propiedades de un Atomic Change

- **A — Atomic**: una responsabilidad observable.
- **B — Bounded**: scope y non-scope explícitos.
- **C — Contractual**: precondiciones, postcondiciones e invariantes.
- **D — Verifiable**: existe una forma objetiva de demostrar que funciona.
- **E — Traceable**: debe poder seguirse la cadena

```
Requirement → A.SPEC → Code → Migration → Test → Commit → Deployment
```

La trazabilidad es esencial para que agentes programen usando ADD.

## 4. El ciclo ADD

```
DEFINE → BOUND → CONTRACT → IMPLEMENT → VERIFY → INTEGRATE
```

- **DEFINE**: describe una sola modificación observable.
- **BOUND**: establece qué puede y qué no puede tocar.
- **CONTRACT**: define comportamiento esperado e invariantes.
- **IMPLEMENT**: realiza únicamente lo necesario.
- **VERIFY**: comprueba contrato + invariantes.
- **INTEGRATE**: commit/deployment asociado a la A.SPEC.

**Regla fuerte en IMPLEMENT: no opportunistic refactoring.**

Si mientras implementas encuentras otra mejora ("ya que estoy aquí podría
refactorizar..."), eso es una **nueva A.SPEC**.

## 5. Change Surface

Cada A.SPEC declara su Change Surface:

```yaml
change_surface:
  allowed:
    - plugins/hosting/backend/discovery.py
    - plugins/hosting/backend/models.py
    - tests/hosting/test_discovery.py
  prohibited:
    - kernel/auth/**
    - kernel/tenancy/**
    - plugins/logistics/**
```

La implementación declara de antemano qué superficie del sistema está
autorizada a modificar. Potentísimo para agentes de IA.

## 6. Blast Radius

Change Surface ≠ Blast Radius.

- **Change Surface**: qué código modificamos.
- **Blast Radius**: qué comportamiento podría verse afectado.

```yaml
blast_radius:
  direct:
    - hosting.docker.discovery
  indirect:
    - hosting.site.list
  must_not_affect:
    - auth
    - tenants
    - logistics
    - existing_containers
```

ADD obliga a pensar no solo "¿qué archivo cambio?" sino "¿qué podría
romper?".

## 7. Invariantes

Uno de los pilares más fuertes.

```yaml
invariants:
  - Existing Docker containers MUST NOT be modified.
  - Discovery MUST be read-only.
  - Containers MUST continue running if Systutor is unavailable.
  - Tenant isolation MUST remain enforced.
```

Si cualquier invariante deja de cumplirse:

> **A.SPEC = FAILED**

aunque la funcionalidad nueva aparentemente funcione.

## 8. Definition of Done

Una A.SPEC solo puede cerrarse cuando:

- [x] Objective satisfied
- [x] Scope respected
- [x] Contract satisfied
- [x] Invariants preserved
- [x] Verification passed
- [x] No unrelated changes
- [x] Traceability established

Esto elimina el ambiguo "parece que ya funciona".

## 9. Estructura de documentos

```
ADD/
├── MANIFESTO.md
├── SPECIFICATION.md
└── ASPEC-TEMPLATE.md
```

Opcional:

```
ADD/
├── examples/
│   ├── bugfix.aspec.md
│   ├── feature.aspec.md
│   ├── migration.aspec.md
│   └── agent-task.aspec.md
└── schemas/
    └── aspec.schema.json
```
