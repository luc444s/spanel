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

## 10. Ley estructural

Además de atomicidad observable, ADD exige coherencia estructural.

### 10.1 Regla primaria

Un archivo MUST preservar:

- una superficie de responsabilidad coherente
- una razón principal de cambio

La pregunta correcta no es "¿cuántas líneas tiene?" sino:

> "¿Este archivo sigue haciendo una sola cosa coherente?"

### 10.2 Tamaño como heurística

El tamaño del archivo NO es la regla primaria. Es una señal de alerta.

- `0-200` líneas: cómodo
- `200-400` líneas: tolerable
- `>400` líneas: revisar cohesión
- `>600` líneas: extracción fuertemente recomendada

Estas cifras no fallan una A.SPEC por sí mismas. Solo elevan exigencia de
justificación estructural.

### 10.3 Archivos de entrypoint

Archivos como `plugin.py`, `register.py`, `main.py`, `router.py` o equivalentes
MUST actuar principalmente como entrypoint o composition root.

Pueden:

- registrar routers
- cablear dependencias
- exponer entrypoints públicos

No deben convertirse en contenedores de toda la lógica del feature si esa
lógica puede vivir en módulos vecinos más cohesivos.

### 10.4 Trigger de extracción

Si una A.SPEC agrega una nueva responsabilidad observable a un archivo ya bajo
presión estructural (`>400` líneas o múltiples motivos de cambio), la
implementación MUST hacer una de estas dos cosas:

1. extraer la nueva responsabilidad a un módulo nuevo
2. abrir una A.SPEC estructural previa o pareada para separar el archivo

### 10.5 Falla estructural

Una A.SPEC falla aunque el comportamiento nuevo funcione si:

- mezcla varias responsabilidades no relacionadas en un mismo archivo
- convierte un entrypoint en un god-file
- aumenta acoplamiento evitable entre rutas, servicios y acceso a datos
- deja el archivo con múltiples razones principales de cambio

## 11. Commit y changelog

ADD exige trazabilidad, no burocracia innecesaria.

### 11.1 Commit obligatorio

Cada A.SPEC integrada MUST quedar trazable a un commit identificable.

Idealmente:

- un commit por A.SPEC
- o una secuencia corta de commits claramente atribuibles a esa A.SPEC

El mensaje de commit SHOULD referenciar el identificador de la A.SPEC cuando
sea posible.

### 11.2 Changelog no obligatorio por defecto

ADD NO exige changelog por cada commit.

Un changelog es opcional salvo que el proceso del proyecto o la release lo
requiera explícitamente.

### 11.3 Cuándo sí exigir changelog

Changelog SHOULD existir cuando:

- hay release pública
- hay cambios operativos o de despliegue relevantes
- múltiples equipos o agentes necesitan historial resumido
- el cambio afecta usuarios o integradores externos

### 11.4 Regla mínima

La regla mínima de ADD es:

- commit trazable: obligatorio
- changelog por commit: opcional
- changelog por release o hito: recomendado

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
