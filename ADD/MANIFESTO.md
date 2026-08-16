# ADD — Atomic Development Discipline

## Manifiesto

ADD es una disciplina de desarrollo de software en la que cada cambio se
diseña, implementa y valida como una unidad mínima, independiente, trazable
y reversible, sin limitar la ambición o escala del sistema.

```
Sistema enorme
      │
      ▼
┌──────────────────────────────────────┐
│             AMBICIÓN                 │
│                                      │
│ ERP + Hosting + Logistics + AI ...  │
└──────────────────────────────────────┘
                 │
                 ▼
        cambios pequeños
                 │
       ┌─────────┼─────────┐
       ▼         ▼         ▼
    A.SPEC     A.SPEC     A.SPEC
    0001       0002       0003
```

No hacemos pequeño el producto. Hacemos pequeño el cambio.

## Principio central: AAA

> **AAA — Atomicity Applies to Change, not Ambition.**

ADD does not constrain the size of the system.
ADD constrains the size of each change made to the system.

Un ERP gigantesco puede construirse perfectamente:

```
                     ERP
                      │
     ┌────────────────┼────────────────┐
     │                │                │
 Logistics        Commerce         Hosting
     │                │                │
  80 A.SPEC        120 A.SPEC        40 A.SPEC
```

La arquitectura puede ser enorme. Los cambios siguen siendo pequeños.

## ADD no impone arquitectura

ADD no prescribe DDD, microservicios, REST, PostgreSQL ni Clean Architecture.
Convive con cualquiera:

```
             ADD
              │
 ┌────────────┼────────────┐
 ▼            ▼            ▼
DDD       Modular       Microservices
          Monolith
```

- DDD organiza el dominio.
- TDD organiza la validación mediante tests.
- SDD organiza el desarrollo mediante especificaciones.
- ADD organiza el tamaño y aislamiento del cambio.

ADD complementa a las demás.

## Contexto mínimo suficiente

Cada token que entra a la cache es una probabilidad de alucinación.

ADD **no** prohíbe guardar contexto: prohíbe guardar contexto innecesario.

Regla: el contexto que entra debe ser el mínimo suficiente para cumplir el
contrato — ni menos (aumenta errores por ignorancia) ni más (aumenta errores
por ruido). La A.SPEC, con su SCOPE, OUT OF SCOPE, Change Surface e
Invariantes, es precisamente la herramienta para delimitar ese contexto.

## ADD y agentes

Un humano puede entregar una A.SPEC (ej. `HOST-0042`) y un agente recibe
únicamente:

1. SPEC
2. Contexto relevante del repositorio
3. Change surface permitida
4. Invariantes
5. Comandos de verificación

El agente ejecuta:

```
inspect → implement → test → verify diff → report
```

El agente no necesita entender todo el sistema para modificarlo con
seguridad. Este es uno de los argumentos más fuertes de ADD en desarrollo
agéntico.
