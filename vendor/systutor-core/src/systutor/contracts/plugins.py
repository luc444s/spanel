from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator, model_validator

SEMVER_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")
PLUGIN_ID_PATTERN = re.compile(r"^[a-z0-9_-]+$")
ENTRYPOINT_PATTERN = re.compile(r"^[a-zA-Z_][\w\.]*:[a-zA-Z_][\w]*$")
PERMISSION_PATTERN = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+\.[a-z0-9_]+$")
EVENT_PATTERN = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+\.[a-z0-9_]+$")


class PluginManifestContract(BaseModel):
    id: str
    name: str
    version: str
    api_version: str
    requires: list[str] = Field(default_factory=list)
    backend_entrypoint: str
    frontend_entrypoint: str
    permissions: list[str] = Field(default_factory=list)
    events: list[str] = Field(default_factory=list)
    description: str

    @field_validator("id")
    @classmethod
    def validate_plugin_id(cls, value: str) -> str:
        if not PLUGIN_ID_PATTERN.match(value):
            raise ValueError("plugin id must use lowercase letters, numbers or underscores")
        return value

    @field_validator("version")
    @classmethod
    def validate_version(cls, value: str) -> str:
        if not SEMVER_PATTERN.match(value):
            raise ValueError("plugin version must follow semver")
        return value

    @field_validator("api_version")
    @classmethod
    def validate_api_version(cls, value: str) -> str:
        if not value.isdigit():
            raise ValueError("api_version must be numeric")
        return value

    @field_validator("backend_entrypoint")
    @classmethod
    def validate_backend_entrypoint(cls, value: str) -> str:
        if not ENTRYPOINT_PATTERN.match(value):
            raise ValueError("backend_entrypoint must use module.path:function format")
        return value

    @field_validator("frontend_entrypoint")
    @classmethod
    def validate_frontend_entrypoint(cls, value: str) -> str:
        if not value.strip() or "/" not in value:
            raise ValueError("frontend_entrypoint must be a relative frontend path")
        return value

    @field_validator("requires")
    @classmethod
    def validate_requires(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("requires must not contain duplicates")
        for item in value:
            if not PLUGIN_ID_PATTERN.match(item):
                raise ValueError("requires must contain valid plugin ids")
        return value

    @field_validator("permissions")
    @classmethod
    def validate_permissions(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("plugin must declare at least one permission")
        if len(set(value)) != len(value):
            raise ValueError("permissions must not contain duplicates")
        for item in value:
            if not PERMISSION_PATTERN.match(item):
                raise ValueError("permissions must use <module>.<resource>.<action>")
        return value

    @field_validator("events")
    @classmethod
    def validate_events(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("events must not contain duplicates")
        for item in value:
            if not EVENT_PATTERN.match(item):
                raise ValueError("events must use <module>.<resource>.<action>")
        return value

    @model_validator(mode="after")
    def validate_cross_fields(self) -> PluginManifestContract:
        if self.id in self.requires:
            raise ValueError("plugin cannot require itself")
        expected_namespace = f"{self.id}."
        for permission in self.permissions:
            if not permission.startswith(expected_namespace):
                raise ValueError("permissions must use the plugin id namespace")
        for event_name in self.events:
            if not event_name.startswith(expected_namespace):
                raise ValueError("events must use the plugin id namespace")
        return self
