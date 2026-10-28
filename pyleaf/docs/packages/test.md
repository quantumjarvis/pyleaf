# pyleaf-test

`pyleaf.test` is a pytest plugin (registered via the `pytest11` entry
point, so it activates automatically once installed) for testing
pyleaf-backed applications: a per-test `ApplicationContext` fixture and
Spring-style bean overrides.

## @PyleafTest and the pyleaf_context fixture

```python
from pyleaf.test import PyleafTest


@PyleafTest(scan="myapp", profiles=["test"], properties={"db.url": "mem:"})
class TestService:
    def test_greet(self, pyleaf_context):
        service = pyleaf_context.get(GreetingService)
        assert service.greet() == "Hello!"
```

`pyleaf_context` (function scope) builds an `Environment`, scans the
packages named in `@PyleafTest`, applies bean overrides, refreshes, yields
the context, and closes it on teardown.

`@PyleafTest` parameters:

- `scan` — package(s) to component-scan;
- `profiles` — active profiles;
- `properties` — a flat dict layered **last**, over yaml and env vars
  (it always wins);
- `config_dir` — directory holding `application.yaml` /
  `application-<profile>.yaml`, loaded via `load_environment`.

The decorator may be used bare (`@PyleafTest`), and metadata is read from
the test class first, then the function, then the module — like pytest
marks.

## mock_bean / spy_bean

Override beans before refresh by overriding the plugin's `pyleaf_beans`
fixture, or by declaring a `pyleaf_beans` class attribute:

```python
import pytest
from pyleaf.test import PyleafTest, mock_bean, spy_bean


@PyleafTest(scan="myapp")
class TestWithMocks:
    @pytest.fixture
    def pyleaf_beans(self):
        return [mock_bean(PaymentGateway)]

    def test_charge(self, pyleaf_context):
        gateway = pyleaf_context.get(PaymentGateway)  # a Mock(spec=...)
        ...
```

- `mock_bean(T)` replaces the bean of type `T` with `Mock(spec=T)`;
  `ctx.get(T)` and every dependent's injection see the mock. When nothing
  matches, a new mock bean is registered as `@Primary`.
- `spy_bean(T)` wraps the real instance in `Mock(wraps=...)` — real
  behavior plus call recording; requires an existing bean of that type.

Overrides apply inside `ctx.refresh()` after scanning but before
conditions and instantiation; applying one to an already-refreshed context
raises `PyleafError` naming the bean. Overridden beans become internal:
injectable, but never advised, scanned for listeners, or force-instantiated.

Exported symbols: `PyleafTest`, `BeanOverride`, `mock_bean`, `spy_bean`.
The `pyleaf_context` and `pyleaf_beans` fixtures are requested by name —
they are plugin fixtures, not imports.
