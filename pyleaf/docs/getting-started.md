# Getting started

## Requirements

- Python >= 3.11
- [uv](https://docs.astral.sh/uv/) for workspace and dependency management

## Run the example

From the workspace root (the `pyleaf/` directory):

```bash
uv sync
cd examples && uv run python -m hello
```

You should see, among the log lines:

```text
pyleaf 0.1.0 — hello
Hello from pyleaf!
ASPECT_INTERNAL_COUNT=2
ENGINE_OK url=sqlite:///%3Amemory%3A
```

What just happened:

1. `PyleafApplication.run("hello")` loaded `examples/hello/application.yaml`
   into a layered `Environment`.
2. It component-scanned the `hello` package and registered
   `GreetingService` (`@Service`) and `InvocationCounterAspect` (`@Aspect`).
3. It discovered `SqlAlchemyAutoConfiguration` through the
   `pyleaf.autoconfigure` entry point installed by
   `pyleaf-starter-sqlalchemy`, and refreshed the context.
4. The starter created an `Engine` bean because `app.datasource.url` was
   configured and no user `Engine` existed.
5. The aspect counted `internal()` invocations — twice: once via
   `public() -> self.internal()` and once via the direct call, proving
   self-invocation interception.

## Write your first app

A pyleaf application is a package of decorated classes plus one
`PyleafApplication.run` call.

```python
# myapp/service.py
from pyleaf.core import Environment, Service


@Service
class GreetingService:
    def __init__(self, environment: Environment) -> None:
        self._environment = environment

    def greet(self) -> str:
        return str(self._environment.get("app.greeting", default="Hello!"))
```

```python
# myapp/__main__.py
from pathlib import Path

from pyleaf.boot import PyleafApplication

from .service import GreetingService

ctx = PyleafApplication.run("myapp", config_dir=Path(__file__).parent)
try:
    print(ctx.get(GreetingService).greet())
finally:
    ctx.close()
```

```yaml
# myapp/application.yaml
app:
  greeting: "Hello from pyleaf!"
```

Constructor parameters are injected by type hint: `Environment` here is a
built-in bean. Add `app.greeting` to `application.yaml`, set
`APP_GREETING=...` as an environment variable, or pass `--app.greeting=...`
— later sources win.

## Where next

- [pyleaf-core](packages/core.md) — stereotypes, injection rules, scopes,
  conditions, AOP, events.
- [pyleaf-boot](packages/boot.md) — auto-configuration ordering, the
  starter contract, configuration properties.
- [pyleaf-test](packages/test.md) — test your beans with the pytest plugin.
