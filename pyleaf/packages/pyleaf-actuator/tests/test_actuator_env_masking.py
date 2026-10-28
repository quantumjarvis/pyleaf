"""Masking tests for the actuator ``/env`` endpoint (plus a ``/info`` check)."""

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from pyleaf.actuator import ActuatorAutoConfiguration
from pyleaf.boot import ConfigurationProperties
from pyleaf.core import (
    ApplicationContext,
    Environment,
    PropertySource,
)
from pyleaf.web.autoconfigure import WebAutoConfiguration


def _ctx(
    *classes: type, values: dict[str, object] | None = None
) -> ApplicationContext:
    ctx = ApplicationContext(
        environment=Environment(
            sources=[PropertySource("test", values or {})]
        )
    )
    for cls in classes:
        ctx.register(cls)
    ctx.refresh()
    return ctx


def _test_source(body: list[dict[str, Any]]) -> dict[str, Any]:
    return next(s for s in body if s["name"] == "test")["properties"]


def test_env_masks_password_secret_token_and_key_case_insensitive() -> None:
    values = {
        "db.password": "s3cret",
        "cache.Secret": "shh",
        "auth.ToKen": "tok",
        "service.api-key": "k",
        "app.name": "demo",
        "app.region": "eu-1",
    }
    ctx = _ctx(WebAutoConfiguration, ActuatorAutoConfiguration, values=values)
    body = TestClient(ctx.get(FastAPI)).get("/actuator/env").json()
    props = _test_source(body)

    for key in ("db.password", "cache.Secret", "auth.ToKen", "service.api-key"):
        assert props[key] == "******", key
    assert props["app.name"] == "demo"
    assert props["app.region"] == "eu-1"


def test_env_masks_configuration_properties_secret_fields() -> None:
    """Fields flagged secret on a @ConfigurationProperties model are masked."""

    @ConfigurationProperties(prefix="db")
    class DbProps(BaseModel):
        host: str = "localhost"
        credentials_file: str = Field(
            default="", json_schema_extra={"secret": True}
        )

    values = {
        "db.host": "db.internal",
        "db.credentials_file": "/run/secrets/db",
    }
    ctx = _ctx(
        DbProps,
        WebAutoConfiguration,
        ActuatorAutoConfiguration,
        values=values,
    )
    body = TestClient(ctx.get(FastAPI)).get("/actuator/env").json()
    props = _test_source(body)

    # 'credentials_file' does not match the name regex; the Field(secret=True)
    # flag recorded on the bound model is what masks it.
    assert props["db.credentials_file"] == "******"
    assert props["db.host"] == "db.internal"


def test_info_merges_info_properties_under_app() -> None:
    values = {
        "info.app.name": "demo",
        "info.app.version": "1.2.3",
    }
    ctx = _ctx(WebAutoConfiguration, ActuatorAutoConfiguration, values=values)
    resp = TestClient(ctx.get(FastAPI)).get("/actuator/info")

    assert resp.status_code == 200
    assert resp.json()["app"] == {"name": "demo", "version": "1.2.3"}
