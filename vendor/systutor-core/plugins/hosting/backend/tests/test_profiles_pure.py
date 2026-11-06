"""Pure profile logic: validation, run argv with any profile, and I1."""

from __future__ import annotations

import pytest

from plugins.hosting.backend.profiles import (
    ProfileSpec,
    ProfileValidationError,
    marker_for,
    validate_profile,
)
from plugins.hosting.backend.settings import HostingSettings
from plugins.hosting.backend.static import (
    LABEL_KIND,
    LABEL_PROFILE,
    MARKER,
    WEB_ROOT,
    StaticNames,
    build_static_run_args,
    index_html_for,
    profiled_content,
)


def _king_spec(command: str | None = None) -> ProfileSpec:
    return ProfileSpec(
        id="king",
        name="King",
        image="httpd:alpine",
        web_root="/usr/local/apache2/htdocs",
        container_port=8080,
        health_mode="http_200",
        env={"CACHE": "/tmp"},
        command=command,
    )


# --- V2: validate_profile --------------------------------------------------

def test_validate_profile_rejects_privileged_or_empty_image() -> None:
    with pytest.raises(ProfileValidationError):
        validate_profile(
            image="--privileged",
            web_root="/srv", container_port=80, health_mode="marker",
        )
    with pytest.raises(ProfileValidationError):
        validate_profile(
            image="", web_root="/srv", container_port=80, health_mode="marker",
        )
    with pytest.raises(ProfileValidationError):
        validate_profile(
            image="registry.local/app --privileged",
            web_root="/srv", container_port=80, health_mode="marker",
        )


def test_validate_profile_rejects_port_outside_range() -> None:
    with pytest.raises(ProfileValidationError):
        validate_profile(
            image="nginx:alpine", web_root="/srv", container_port=0, health_mode="marker",
        )
    with pytest.raises(ProfileValidationError):
        validate_profile(
            image="nginx:alpine", web_root="/srv",
            container_port=65536, health_mode="marker",
        )


def test_validate_profile_rejects_relative_web_root() -> None:
    with pytest.raises(ProfileValidationError):
        validate_profile(
            image="nginx:alpine", web_root="usr/share",
            container_port=80, health_mode="marker",
        )


def test_validate_profile_rejects_non_object_or_non_string_env() -> None:
    bad_envs = ({"A": 1}, ["A=1"])  # type: ignore[var-annotated]
    for bad_env in bad_envs:
        with pytest.raises(ProfileValidationError):
            validate_profile(
                image="nginx:alpine", web_root="/srv", container_port=80,
                health_mode="marker",
                env_json=bad_env,  # type: ignore[arg-type]
            )


def test_validate_profile_accepts_a_good_shape() -> None:
    data = validate_profile(
        image="hugomr/hugo-server:slim",
        web_root="/srv/hugo",
        container_port=1313,
        health_mode="http_200",
        env_json={"FOO": "bar"},
        command="test -f /srv/hugo/index.html && npm run preview",
        profile_id="hugo",
    )
    assert data["id"] == "hugo"
    assert data["container_port"] == 1313


# --- V2: run argv for a custom profile --------------------------------------

def test_custom_profile_run_args_carry_env_mount_and_port() -> None:
    settings = HostingSettings(network="proxy-lab", static_image="nginx:alpine")
    names = StaticNames.for_profile("pagina", _king_spec())
    args = build_static_run_args(names, settings, "t1", "pagina.spanel", traefik=True)
    assert names.image == "httpd:alpine"
    assert args[-1] == "httpd:alpine"
    assert "spanel-pagina-web:/usr/local/apache2/htdocs" in args
    joined = [item for item in args if "loadbalancer.server.port" in item]
    assert joined == ["traefik.http.services.pagina.loadbalancer.server.port=8080"]
    assert args[args.index("--env") + 1] == "CACHE=/tmp"
    assert f"{LABEL_PROFILE}=king" in args
    # No command: the image's CMD stays.
    assert "sh" not in args


def test_custom_profile_command_appends_sh_c() -> None:
    settings = HostingSettings(network="proxy-lab")
    names = StaticNames.for_profile(
        "pagina", _king_spec(command="npm install && npm run build")
    )
    args = build_static_run_args(names, settings, "t1", "pagina.spanel")
    assert args[-3:] == ["sh", "-c", "npm install && npm run build"]


def test_env_only_enters_when_defined() -> None:
    settings = HostingSettings(network="proxy-lab")
    plain = build_static_run_args(
        StaticNames("pagina"), settings, "t1", "pagina.spanel"
    )
    assert "--env" not in plain


# --- I1: the builtin nginx reproduces the A.SPEC 0031 argv -------------------

def test_builtin_nginx_argv_matches_the_0031_shape() -> None:
    settings = HostingSettings(network="proxy-lab", static_image="nginx:alpine")
    legacy = StaticNames("pagina", image=settings.static_image)
    builtin_names = StaticNames.for_profile(
        "pagina",
        ProfileSpec(id="nginx", name="Nginx (estático)", image=settings.static_image),
    )
    args_legacy = build_static_run_args(legacy, settings, "t1", "pagina.spanel")
    args_builtin = build_static_run_args(builtin_names, settings, "t1", "pagina.spanel")
    # Same parts, same order: only the profile id differs (nginx == default).
    assert args_legacy == args_builtin
    assert args_builtin[args_builtin.index("--network") + 1] == "proxy-lab"
    assert f"spanel-pagina-web:{WEB_ROOT}" in args_builtin
    assert args_builtin[-1] == settings.static_image
    assert any("loadbalancer.server.port=80" in item for item in args_builtin)
    assert f"{LABEL_KIND}=static" in args_builtin


# --- the seed and the health marker -----------------------------------------

def test_seed_carries_the_profile_marker() -> None:
    html = profiled_content("king")["index.html"]
    assert MARKER in html
    assert 'data-spanel-profile="king"' in html
    assert profiled_content("nginx")["index.html"] == index_html_for("nginx")
    assert marker_for("king") == 'data-spanel-profile="king"'
