# A.SPEC 0007 — Fijar los 12 casos raros de detección de WordPress

> `risk: low` — Sin migración, sin esquema, sin contrato de API, no hay endpoint. Solo una función
> de clasificación y sus tests. El rollback es borrar dos archivos. Mode: `extreme-poverty`.

## WHY

`scripts/probe_docker.py` ya funciona contra el Docker real de la laptop y hoy clasifica 24
contenedores. Pero su función de detección es de humo: mira dos campos y devuelve una tupla
`(bool, str)`. Se escribió rápido, antes de conocer el formato real de `docker ps`, y quedó
apoyada en una suposición que **ya sabemos falsa**.

La suposición: "el campo `Mounts` de `docker ps` dice qué volúmenes tiene el contenedor, así que
puedo detectar WordPress por `/var/www/html`". Verificado contra Docker 29.1.3:

```
mailadmin-postgres-1   Mounts='mailadmin_pgda…'    <- cortado con puntos suspensivos
mailadmin-app-1        Mounts='/var/run/docke…'    <- idem
spanel-lucas-wp        Mounts='spanel-lucas-wp'     <- ni siquiera es un punto de montaje
```

`Mounts` es texto decorativo para pantalla, no el dato. Filtrar por él produce **falsos negativos
silenciosos**: el script no crashea, simplemente nunca encuentra nada. Ese es el peor modo de
fallar, porque se confunde con "no hay WordPress".

Los casos raros que faltan no son inventados, son los que rompieron el razonamiento:

- `bitnami/wordpress` y `miempresa/wp-custom` no empiezan con `wordpress`, pero son WordPress.
- `nginx:alpine` no dice WordPress en ningún lado, pero sirve `/var/www/html` y es un sitio.
- `mariadb:11` comparte red con el WordPress de al lado y es la mitad de cada sitio, no un sitio.
- `wordpress:cli` matchea el prefijo de imagen y es la herramienta de un solo uso, no un sitio.
- `Labels` es texto `clave=valor,clave=valor`, y buscar `"spanel.site" in labels` también matchea
  `xspanel.sitex`. Un substring no es una clave.

Hoy no hay **una sola línea de test** ate nada de esto.

## WHAT

**Los 12 casos raros quedan escritos como tests que pasan, y la detección se completa hasta
satisfacerlos.**

Es una verdad falsable hoy, y hoy **falla**: de los 12 casos, el código actual no cumple el 3, el
5 ni el 8, y ninguno está protegido por un test.

## Los 12 casos

La detección pasa de devolver una tupla a devolver un `Candidato`, porque "es un sitio" y "lo
puedo arrancar" son dos preguntas distintas: un WordPress con `spanel.managed=false` se sigue
mostrando como sitio, pero queda fuera del alcance de las acciones.

```python
@dataclass(frozen=True, slots=True)
class Candidato:
    es_sitio: bool      # ¿es un WordPress?
    gestionable: bool   # ¿lo podemos arrancar/detener? Casi siempre sí
    senal: str          # la pista que lo identificó
    por_que: str        # frase para mostrar en la interfaz
```

| # | Fixture | Esperado | Por qué está ese caso |
|---|---|---|---|
| 1 | `Image=wordpress:php8.3-apache`, `State=running`, sin etiquetas | sitio, gestionable, señal `imagen` | el caso obvio |
| 2 | `Image=wordpress:cli` | **NO** sitio; `por_que` nombra la herramienta | matchea el prefijo y no es un sitio. Si no, cada provisioning deja una fila fantasma |
| 3 | `Image=miempresa/wp-custom:1.2` + inspect con mount en `/var/www/html` | sitio, señal `montaje` | el nombre de la imagen no dice WordPress |
| 4 | `Image=mariadb:11` | **NO** sitio | es la base de datos del sitio de al lado; sin este caso salen 2 filas por sitio |
| 5 | `Image=nginx:alpine` + inspect con mount en `/var/www/html` | sitio, señal `montaje` | imagen que no dice WordPress pero lo sirve |
| 6 | `Image=bitnami/wordpress:6` | sitio, señal `imagen` | el prefijo no está en la posición 0 |
| 7 | `Labels` con `spanel.site=acme` | sitio, gestionable, señal `etiqueta` | nuestra etiqueta, la que ponemos al provisionar |
| 8 | `Labels` con `spanel.managed=false` | sitio, **`gestionable=False`** | válvula de escape: declarar que un contenedor queda fuera del alcance |
| 9 | `Image=wordpress:...` + `Labels` con `spanel.managed=true` | sitio, gestionable | provisioned |
| 10 | `State=exited` | sitio igual | un WordPress apagado sigue siendo WordPress |
| 11 | `{}`, sin ninguna clave | NO sitio, **sin excepción** | la diferencia entre un `[]` y un 500 |
| 12 | `[]`, lista vacía | `[]`, sin excepción | una máquina sin contenedores es válida |

Los casos 3 y 5 dependen de `docker inspect`, porque no se pueden resolver con la lista. Eso **no
cambia el costo**: la capa 1 sigue dejando pocos candidatos y el inspect se sigue haciendo solo
para esos, nunca para los 24.

## SCOPE

1. `scripts/probe_docker.py`:
   - `Candidato` (dataclass) en lugar de la tupla `(bool, str)`;
   - `parse_labels` con `partition("=")` y clave exacta, sin búsqueda por substring;
   - `detectar_capa1(container)`: solo `Image` y `Labels`, sin red;
   - `detectar_capa2(container, detalle_inspect)`: agrega los `Mounts` reales y `WORDPRESS_DB_HOST`;
   - la salida de consola pasa a usar `Candidato`.
2. `scripts/tests/test_probe_docker.py`: nuevo. Los 12 casos más los tests de `parse_labels`.
3. Este documento.

## OUT OF SCOPE

- **No se crea el plugin `hosting`.** Nada de manifest, permisos ni endpoints. Esto es solo la
  lógica de clasificación, probada sin Docker.
- **No se toca `vendor/systutor-core/**`.** Ni el kernel, ni el plugin mail, ni el shell.
- **No se provisiona, arranca, detiene ni borra ningún contenedor.** El probe es de lectura. Un
  script que empieza a destructive es otro A.SPEC.
- **No se toca el transporte.** SSH, `sshpass -e` y `SSHPASS` quedan como están.
- **No se agregan tests que necesiten un daemon.** Los fixtures son diccionarios literales.

## CONTRACT

**Precondiciones**: ninguna. Los tests corren sin Docker, sin red, sin credenciales y sin base de
datos. El archivo `.env` no se lee desde los tests.

**Postcondiciones**

- `detectar_capa1` es una función pura sobre un `dict` y no hace ninguna llamada de red.
- `detectar_capa2` es una función pura sobre el `dict` de la lista y el `dict` del `docker inspect`.
- Ninguna de las dos lanza excepción ante `{}`, `None` ni tipos inesperados.
- `parse_labels` no matchea por substring: `xspanel.sitex` no es `spanel.site`.
- El probe sigue terminando con código 0 y sigue encontrando los WordPress reales.

## INVARIANTS

```yaml
invariants:
  - id: I1
    statement: >
      La contraseña nunca aparece en el argv. Sigue yendo por `sshpass -e` con `SSHPASS` en el
      entorno del proceso hijo. En el argv se escapa sola a cualquier traceback y a cualquier `ps`.
    proof: test_password_never_in_argv

  - id: I2
    statement: >
      El probe es de solo lectura. No ejecuta ningún comando que cree, arranque, detenga ni borre
      un contenedor o un volumen.
    proof: >
      El test recorrido con el repo entero: los únicos comandos que aparecen en el archivo son
      `docker version`, `docker ps` y `docker inspect`, los tres de lectura. Se agrega a esta A.SPEC
      como `test_probe_only_runs_read_only_commands`.

  - id: I3
    statement: >
      La lista completa se resuelve con una sola llamada de `docker ps`. El `docker inspect` se
      sigue haciendo solo para los candidatos de la capa 1, nunca para los 24.
    proof: >
      `detectar_capa1` no acepta el detalle de inspect, y la capa 2 solo se invoca dentro del
      recorrido de candidatos.

  - id: I4
    statement: >
      Ningún dato vacío o malformado produce excepción. `{}` y `[]` devuelven el resultado vacío.
    proof: casos 11 y 12

  - id: I5
    statement: >
      Las etiquetas se comparan por clave exacta. Un substring no cuenta como clave.
    proof: >
      test_labels_exact_key_not_substring, con `xspanel.sitex=1` y `myspanel.site=1` como
      negativos.

  - id: I6
    statement: >
      Un contenedor WordPress apagado se sigue detectando. El estado no participa de la decisión
      de "es un sitio".
    proof: caso 10

  - id: I7
    statement: >
      `vendor/systutor-core/**` no se toca. Los gates del kernel y el plugin mail quedan como
      estaban.
    proof: change_surface prohíbe el path; `git status` lo confirma.

  - id: I8
    statement: >
      Todo WordPress detectado es gestionable. El sistema administra la maquina, asi que lo que
      hay en ella es suyo: no hace falta un paso extra de "adoptar" para poder operarlo. La unica
      excepcion es la etiqueta `spanel.managed` con un valor negativo explicito
      (`false`, `0`, `no`, `off`), que saca el contenedor del alcance sin sacarlo de la lista.
    proof: >
      casos 1, 7, 8 y 8b, más `test_8c_solo_los_negativos_explicitos_sacan_de_alcance`.
      Verificado contra el Docker real: los tres WordPress que quedaron de agosto no tienen
      ninguna etiqueta y ahora salen como gestionables.

      Decision del usuario, 2026-09-27. La primera version de esta A.SPEC hacia fail-closed y la
      revirtio. Consecuencia asumida: el sistema se atribuye TODOS los WordPress de la maquina,
      incluidos los creados a mano fuera del sistema, y las acciones destructivas los alcanzan
      sin un paso explicito de adopcion. Queda escrito para que sea una decision consciente.
```

  - id: I9
    statement: >
      La deteccion es broad y lo sigue siendo: agregar la regla de gestionabilidad NO puede
      reducir cuantos WordPress se encuentran. `es_sitio` y `gestionable` son banderas
      independientes.
    proof: >
      casos 8 y 8b usan la misma imagen y los dos dan `es_sitio is True`, con `gestionable`
      distinto. Ningun test reduce la cantidad de sitios detectados.
```

## VERIFICATION

Gates, con `ruff` nativo de Termux (`/data/data/com.termux/files/usr/bin/ruff`, 0.15.21):

```bash
ruff check scripts/
python3 -m pyright scripts/
python3 -m pytest scripts/tests -q
```

Nota: `ruff` está instalado como binario, no como módulo de Python, así que el gate es `ruff
check`, no `python3 -m ruff check`. Y `scripts/` está **fuera** del `pyproject.toml` del kernel
(`testpaths = ["tests"]`), por eso el path de pytest es explícito.

**Tests (los 12 casos, uno por test)**

| Test | Caso | Qué falsaría |
|---|---|---|
| `test_1_imagen_wordpress_running` | 1 | el caso obvio deja de funcionar |
| `test_2_wordpress_cli_no_es_sitio` | 2 | la herramienta aparece como sitio |
| `test_3_imagen_custom_con_montaje_es_sitio` | 3 | las imágenes propias no se detectan |
| `test_4_mariadb_no_es_sitio` | 4 | 2 filas por sitio |
| `test_5_nginx_con_montaje_es_sitio` | 5 | el WordPress servido por nginx desaparece |
| `test_6_bitnami_es_sitio` | 6 | el prefijo no literal se pierde |
| `test_7_etiqueta_spanel_site` | 7 | nuestra etiqueta deja de reconocerse |
| `test_8_managed_false_saca_el_contenedor_del_alcance` | 8 | la válvula de escape no se respeta |
| `test_8b_sin_etiqueta_managed_si_es_gestionable` | 8b | aparece un paso extra para operar un sitio encontrado |
| `test_8c_solo_los_negativos_explicitos_sacan_de_alcance` | 8c | un valor raro de la etiqueta cambia el resultado |
| `test_9_provisioned_es_gestionable` | 9 | un sitio provisionado queda fuera de alcance |
| `test_10_apagado_sigue_siendo_sitio` | 10 | un WP parado desaparece de la lista |
| `test_11_diccionario_vacio_no_lanza` | 11 | un dato raro tumba la pantalla con 500 |
| `test_12_lista_vacia_no_lanza` | 12 | una máquina sin contenedores reventa |
| `test_labels_exact_key_not_substring` | I5 | `xspanel.sitex` se toma por etiqueta nuestra |
| `test_password_never_in_argv` | I1 | la contraseña se filtra al log o al `ps` |
| `test_probe_only_runs_read_only_commands` | I2 | el probe empieza a modificar algo |
| `test_transporte_local_no_duplica_el_docker` | I2 | el argv queda con `docker` duplicado y la lista sale vacía |
| `test_inspect_solo_para_los_prefiltrados` | I3 | se paga un inspect por cada contenedor de la máquina |
| `test_parse_labels_separa_por_coma` | I5 | dos etiquetas se pegan en una |
| `test_parse_labels_parte_por_el_primer_igual` | I5 | un valor con `=` se corta mal |
| `test_parse_labels_no_explota_con_basura` | I4 | una etiqueta rarísima tumba el listado |

La spec estimaba 15 tests y son 22. La diferencia no es decorativa: durante la
implementación aparecieron tres bugs que la spec no anticipó, y cada uno lleva su
test. `test_transporte_local_no_duplica_el_docker` y
`test_inspect_solo_para_los_prefiltrados` no estaban previstos, y los tres de
`parse_labels` se agregaron porque el invariante I5 (clave exacta, no substring)
tenía más de un ángulo de ataque. Una spec que no anticipa los casos raros es
una spec que todavia no esta probada.

## ROLLBACK

`git checkout` de `scripts/probe_docker.py` y borrar `scripts/tests/`. Sin migración, sin estado
en el servidor, nada que deshacer del lado de Docker: el probe nunca escribió nada.

## Change Surface

```yaml
change_surface:
  allowed:
    - scripts/probe_docker.py
    - scripts/tests/test_probe_docker.py
    - aspecs/0007-wordpress-detection-rare-cases.md
  prohibited:
    - vendor/systutor-core/**      # el kernel no sabe de Docker
    - vendor/systutor-shell/**     # la UI no se toca
    - docker-compose.yml           # no hay servicios nuevos
    - .env                         # credenciales, solo las decide el deploy
    - .env.docker
```

## Blast Radius

```yaml
blast_radius:
  direct:
    - scripts/probe_docker.py (dos funciones y la salida de consola)
    - scripts/tests/test_probe_docker.py (nuevo, 22 tests)
  indirect:
    - Ninguno. El script corre a mano, no hay proceso que lo llame.
  must_not_affect:
    - surface: credenciales
      surfaces: [build_docker_argv, run]
      invariant: I1
    - surface: solo_lectura
      surfaces: [probe_docker.py completo]
      invariant: I2
    - surface: costo_de_la_listado
      surfaces: [main, capa 1 / capa 2]
      invariant: I3
    - surface: kernel_y_mail
      surfaces: [vendor/systutor-core/**]
      invariant: I7
```

## Composition

```yaml
composition:
  requires_aspecs: []
  must_compose_with: []
  systemic_invariants:
    - La lógica de detección que se fija acá es la que va a consumir el futuro plugin hosting.
      Si se cambia acá, hay que cambiarlo allá también.
  composition_checks:
    - python3 -m pytest scripts/tests -q
```

## Structural Constraints

```yaml
structural_constraints:
  primary_rule: una responsabilidad y una razón para cambiar
  entrypoints_must_stay_thin: true
  review_threshold_lines: 400
  extraction_threshold_lines: 600
  preferred_new_logic_locations:
    - scripts/probe_docker.py
```

`probe_docker.py` queda en ~340 líneas, debajo del umbral de revisión. No aparece ninguna clase
nueva aparte de `Candidato`, que es un dato, no comportamiento.

## Traceability

- **Requirement**: poder ver y filtrar los WordPress que ya están en Docker, empezando por no
  romper con los casos raros
- **A.SPEC**: este documento
- **Code**: `scripts/probe_docker.py` (`Candidato`, `parse_labels`, `detectar_capa1`,
  `detectar_capa2`)
- **Migration**: ninguna
- **Test**: `scripts/tests/test_probe_docker.py` (nuevo, 22 tests)
- **Commit**: `TBD`
- **Deployment**: ninguno. No entra en la imagen de Docker.

- **owner**: `TBD`
- **approver**: `TBD`

## Definition of Done

- [x] Objective satisfied
- [x] Scope respected
- [x] Contract satisfied
- [x] Independent falsable truth exists now
- [x] Invariants preserved
- [x] Verification passed
- [x] Rollback / compensation is honest
- [x] Composition checks passed when applicable
- [x] No unrelated changes
- [x] Structural constraints respected
- [x] Traceability established
