"""Decide whether a container is a WordPress site.

Everything in this module is PURE: it receives what `docker ps` or `docker
inspect` returned and answers. No network, no Docker and no credentials, so
the 12 rare cases from A.SPEC 0007 are tested offline.

It returns a `Candidato`, not a `(bool, str)`, because "is a site" and "can
we operate it" are two different questions: a WordPress with
`spanel.managed=false` is still shown as a site, but is outside the scope of
the actions.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Candidato:
    """The verdict on a container. It is data, not behaviour."""

    es_sitio: bool  # ¿es un WordPress?
    gestionable: bool  # ¿lo podemos arrancar/detener? Casi siempre sí
    senal: str  # la pista que lo identifico
    por_que: str  # frase para mostrar en la interfaz


SENAL_NINGUNA = ""
SENAL_IMAGEN = "imagen"
SENAL_ETIQUETA = "etiqueta"
SENAL_MONTAJE = "montaje"
SENAL_ENTORNO = "entorno"
SENAL_HERRAMIENTA = "herramienta"
SENAL_BASE_DATOS = "base_datos"
ETIQUETA_SITIO = "spanel.site"
ETIQUETA_GESTION = "spanel.managed"
RUTA_WEB = "/var/www/html"
# Imagenes que son la mitad de un sitio, no el sitio. Sin esta lista cada sitio
# sale dos veces en la pantalla (caso raro 4).
PREFIXOS_DE_BASE_DATOS = (
    "mariadb:",
    "mysql:",
    "percona:",
    "postgres:",
    "postgresql:",
    "redis:",
)
NADA = Candidato(False, False, SENAL_NINGUNA, "ninguna pista coincide")


def _texto(valor: object) -> str:
    """Like str() but safe: None and odd types become a string."""
    if isinstance(valor, str):
        return valor
    return "" if valor is None else str(valor)


def _mapa(valor: object) -> dict:
    """Empty dict for anything that is not a dict (rare case 11)."""
    return valor if isinstance(valor, dict) else {}


def _lista(valor: object) -> list:
    """Empty list for anything that is not a list (rare case 12)."""
    return list(valor) if isinstance(valor, (list, tuple)) else []


def parse_labels(raw: object) -> dict[str, str]:
    """Turn Docker's "a=1,b=2" text into a dictionary.

    The key is split with `partition("=")` and matched EXACTLY: that is why
    `xspanel.sitex` does not count as `spanel.site`. A substring is not a key.
    """
    if not isinstance(raw, str) or not raw:
        return {}
    labels: dict[str, str] = {}
    for pair in raw.split(","):
        clave, _, valor = pair.partition("=")
        if clave.strip():
            labels[clave.strip()] = valor.strip()
    return labels


def es_operable(container: object) -> bool:
    """Can we start, stop or delete this container?

    **USER DECISION, 2026-09-27**: every WordPress we find is operable. There
    is no intermediate state of "this one is yours but be careful".

    The reasoning: if the system administers the server, it owns what is on
    it. The only thing that can withdraw a container from this rule is the
    `spanel.managed` label with an explicit negative value. That stays as an
    escape valve.

    OJO: this rule means the system claims ownership of ALL WordPress on the
    machine, including the ones the user created by hand. Verified against
    the real Docker: the three WordPress sites from August have no label, and
    with this rule all three are operable. It is what was asked for, and it
    is coherent with the system being the administrator of the machine.
    """
    labels = parse_labels(_mapa(container).get("Labels"))

    gestion = labels.get(ETIQUETA_GESTION, "").strip().lower()
    return gestion not in {"false", "0", "no", "off"}


def detectar_capa1(container: object) -> Candidato:
    """Layer 1: ONLY `Image` and `Labels`. Pure: no network call.

    It ALWAYS returns a Candidato, so the caller does not have to tell "no
    data" apart from "not a site". State is not looked at: a stopped
    WordPress is still WordPress (rare case 10).

    VERIFIED against Docker 29.1.3 on 2026-09-27: `Image` and `Labels` come
    complete, but `Mounts` does NOT; Docker truncates it with dots
    ("/var/run/docke..."), that is, screen text, not the data. The real
    mounts have to be requested with `docker inspect`.
    """
    datos = _mapa(container)
    image = _texto(datos.get("Image")).strip()
    labels = parse_labels(datos.get("Labels"))
    operable = es_operable(datos)

    # wordpress:cli matches the image prefix and is the one-shot tool, not a
    # site: without this every provisioning leaves a ghost row.
    if image.startswith("wordpress:cli") or image.endswith("/wp-cli"):
        return Candidato(False, False, SENAL_HERRAMIENTA, f"{image} es la herramienta wp-cli")
    if image.startswith(PREFIXOS_DE_BASE_DATOS):
        return Candidato(
            False,
            False,
            SENAL_BASE_DATOS,
            f"{image} es la base de datos del sitio de al lado",
        )

    # The label goes before the image: it is the strongest hint, because we
    # put it there ourselves when provisioning.
    if ETIQUETA_SITIO in labels:
        return Candidato(
            True,
            operable,
            SENAL_ETIQUETA,
            f"tiene la etiqueta {ETIQUETA_SITIO} nuestra",
        )

    # "wordpress" in any position: this is how bitnami/wordpress enters, which
    # does not have it at position 0 (rare case 6).
    if "wordpress" in image.lower():
        return Candidato(True, operable, SENAL_IMAGEN, f"la imagen dice wordpress: {image}")

    return NADA


def _info_de_inspect(detalle: object) -> dict:
    """The `docker inspect` dict. Tolerates being wrapped in a list."""
    if isinstance(detalle, list) and detalle:
        detalle = detalle[0]
    return _mapa(detalle)


def destinos_de_montaje(detalle_inspect: object) -> list[str]:
    """The REAL `Mounts`, which came truncated with dots in the list."""
    return [
        _texto(_mapa(m).get("Destination")).strip()
        for m in _lista(_info_de_inspect(detalle_inspect).get("Mounts"))
    ]


def entorno_de_inspect(detalle_inspect: object) -> dict[str, str]:
    """The real container environment, as a dictionary."""
    entorno: dict[str, str] = {}
    for par in _lista(_mapa(_info_de_inspect(detalle_inspect).get("Config")).get("Env")):
        clave, _, valor = _texto(par).partition("=")
        if clave.strip():
            entorno[clave.strip()] = valor.strip()
    return entorno


def detectar_capa2(container: object, detalle_inspect: object) -> Candidato:
    """Layer 2: the `docker inspect` one. Adds the real mounts and environment.

    Rare cases 3 and 5 are resolved ONLY here, because the image does not say
    WordPress: `miempresa/wp-custom` and `nginx:alpine` serve `/var/www/html`.
    That is why the inspect is reserved for the ones that already passed the
    prefilter.
    """
    base = detectar_capa1(container)
    if base.es_sitio or base.senal in {SENAL_HERRAMIENTA, SENAL_BASE_DATOS}:
        return base  # layer 1 already resolved it, or discarded it by name

    operable = es_operable(container)
    for destino in destinos_de_montaje(detalle_inspect):
        if destino == RUTA_WEB or destino.startswith(f"{RUTA_WEB}/"):
            return Candidato(True, operable, SENAL_MONTAJE, f"monta {RUTA_WEB}, el sitio vive ahi")
    if "WORDPRESS_DB_HOST" in entorno_de_inspect(detalle_inspect):
        return Candidato(True, operable, SENAL_ENTORNO, "define WORDPRESS_DB_HOST en su entorno")
    return base


def es_prefiltrado(container: object) -> bool:
    """Is a `docker inspect` worth it for this container?

    If layer 1 discards it by NAME (the wp-cli tool, the neighbour database),
    no. Everything else passes, because an image that does not say WordPress
    can still be serving a site.
    """
    return detectar_capa1(container).senal in {SENAL_NINGUNA, SENAL_IMAGEN, SENAL_ETIQUETA}


def detectar_todos(
    contenedores: object,
    inspeccionar: Callable[[str], object] | None = None,
) -> list[tuple[str, Candidato]]:
    """Walk the `docker ps` list and return (name, verdict) for candidates.

    `inspeccionar` is optional: without it layer 2 does not run and the
    decision is made with layer 1 alone. With it, it is called ONLY for the
    ones that passed the prefilter, never for all 24: the whole list comes
    from a single `docker ps` call. An empty list returns an empty list,
    without an exception (rare case 12).
    """
    candidatos: list[tuple[str, Candidato]] = []
    for container in _lista(contenedores):
        if not es_prefiltrado(container):
            continue
        nombre = _texto(_mapa(container).get("Names")).split(",")[0].strip()
        detalle = inspeccionar(nombre) if inspeccionar is not None and nombre else {}
        candidatos.append((nombre, detectar_capa2(container, detalle)))
    return candidatos
