# pyleaf-actuator

`pyleaf.actuator` mounts Spring-actuator-style operational endpoints on
the auto-configured FastAPI app. It ships no routes of its own into user
space: `ActuatorAutoConfiguration` registers an `actuatorCustomizer` bean
— a `pyleaf.web.FastApiCustomizer` — which the web auto-configuration
applies after all built-in wiring, plus the built-in
`pingHealthIndicator`.

## Endpoints

Mounted under `pyleaf.actuator.base-path` (default `/actuator`):

| Endpoint | Payload |
|---|---|
| `GET /health` | `{"status": "UP"|"DOWN", "components": {<bean>: <report>}}` — aggregates every `HealthIndicator` bean; any `DOWN` report flips the body to `DOWN` and the status to 503 |
| `GET /info` | `info.*` properties (under `app`) plus best-effort git commit/branch |
| `GET /env` | per-`PropertySource` key maps with secret values masked |
| `GET /beans` | every `BeanDefinition`: name, qualified type, scope, `primary`, `lazy`, resolved dependency names — **definitions only**, lazy and request-scoped beans are never instantiated |
| `GET /conditions` | the `ConditionEvaluationReport`: every matched/unmatched definition with its reasons |
| `GET /metrics` | Prometheus exposition — only when the optional `prometheus_client` package is installed (`pip install pyleaf-actuator[prometheus]`) |

`/beans` is the headline difference from generic metrics endpoints: it
answers "why does this bean exist" — definitions, scopes, flags and the
resolved dependency names — read straight from the registry without
touching a single factory. `/conditions` renders the same report the
container produces at startup, including out-of-band entries.

## Flags

| Property | Default | Meaning |
|---|---|---|
| `pyleaf.actuator.enabled` | `true` | `false` drops the whole auto-configuration (custom condition, so a YAML bool works) |
| `pyleaf.actuator.base-path` | `/actuator` | mount prefix |
| `pyleaf.actuator.endpoints.<name>.enabled` | `true` | per-endpoint gate for `health`, `info`, `env`, `beans`, `conditions`, `metrics`; disabled → route absent → 404 |

Example — expose only health and info:

```yaml
pyleaf:
  actuator:
    endpoints:
      env: {enabled: false}
      beans: {enabled: false}
      conditions: {enabled: false}
      metrics: {enabled: false}
```

## Environment masking

`/env` masks values whose key name matches Spring's default secret
pattern — `password|secret|token|key`, case-insensitive — rendering them
as `******`. Additionally, `@ConfigurationProperties` classes may declare
`secret_fields`; fields under their prefix are masked even when the leaf
name does not match the pattern.

```yaml
app:
  api-secret-key: "dev-only-secret-value"   # -> "app.api-secret-key": "******"
```

## Health indicators

```python
from pyleaf.actuator import HealthIndicator
from pyleaf.core import Component


@Component
class DatabaseHealthIndicator(HealthIndicator):
    def health(self) -> dict:
        return {"status": "UP", "details": {"latency_ms": 3}}
```

Every `HealthIndicator` bean contributes `defn.name -> health()` to the
`/health` components map; a raising indicator reports `DOWN` and the
aggregate flips to 503. The auto-configured `pingHealthIndicator` is the
always-UP built-in.

## Trying it

`examples/todo-api/` runs the full stack; `scripts/e2e_m2.sh` asserts
`/actuator/health`, `/actuator/beans` (definitions-only), `/actuator/env`
masking and the rest over real uvicorn HTTP.
