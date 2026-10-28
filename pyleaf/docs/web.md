# pyleaf-web

`pyleaf.web` layers Spring-style class controllers on top of FastAPI:
`@RestController` beans with `@GetMapping`/`@PostMapping`/… methods,
`@ControllerAdvice` exception mapping, `@WebMiddleware` bean middleware,
the REQUEST bean scope, and `WebAutoConfiguration` which builds the
`FastAPI` app that `PyleafApplication.run()` serves through uvicorn.

Everything is a bean: controllers, advice and middleware classes register
through normal component dispatch, so constructor injection, `@Order`,
conditions and AOP apply to them exactly as to any `@Service`.

## Controllers

```python
from pyleaf.web import GetMapping, PostMapping, RestController


@RestController("/todos")                # path prefix; bare @RestController works too
class TodoController:
    def __init__(self, service: TodoService) -> None:
        self._service = service          # constructor injection as usual

    @GetMapping("")
    def list_todos(self) -> list[TodoOut]: ...

    @PostMapping("", status_code=201)
    def create_todo(self, payload: TodoCreate) -> TodoOut: ...
```

- Method mappings: `GetMapping`, `PostMapping`, `PutMapping`,
  `PatchMapping`, `DeleteMapping`, `RequestMapping(path, methods=...)`.
  Signature: `(path="", *, status_code=None, response_model=None,
  **kwargs)`; extra kwargs pass to `APIRouter.add_api_route`. Mappings
  stack on one method.
- Handler semantics are FastAPI's: path/query parameters bind by name,
  pydantic models become request bodies, return annotations become
  response models. `Depends` still works.
- `build_router(ctx)` collects every controller's routes into an
  `APIRouter`; `register_controllers(app_or_router, ctx)` mounts them.
  Route order is deterministic: controllers by `(@Order`, registration),
  methods in declaration order.

## REQUEST scope

```python
from pyleaf.core import Component, RequestScope, Scope


@Component(scope=Scope.REQUEST)
class RequestAudit:
    def __init__(self) -> None:
        RequestScope.current()           # fail loudly if built scope-less
        self.request_id = uuid4().hex
```

- A `Scope.REQUEST` dependency is always injected as a
  `RequestScopedProxy` — one shared proxy per definition whose every
  attribute access resolves the instance cached in the *current* scope.
  `ctx.get`/`ctx.get_bean` return the proxy too, so nothing outside a
  request ever materializes the bean.
- `RequestScope` is a `contextvars`-bound per-request cache: instances
  are created through the normal container pipeline (their own
  dependencies resolve inside the scope) and `@PreDestroy` runs when the
  scope closes.
- `RequestScopeMiddleware` (mounted automatically by
  `WebAutoConfiguration`, outermost layer) activates a fresh scope around
  every HTTP request. Accessing a request bean outside a scope raises
  `ScopeNotActive` naming the bean.
- Deliberate difference from Spring: `proxy.__class__` stays
  `RequestScopedProxy` — `isinstance(proxy, T)` is **not** supported.

## @ControllerAdvice

```python
from pyleaf.web import ControllerAdvice, ExceptionHandler


@ControllerAdvice
class TodoAdvice:
    @ExceptionHandler(TodoNotFoundError, status_code=404)
    def not_found(self, exc: TodoNotFoundError) -> dict:
        return {"detail": str(exc)}
```

- `register_advice(app, ctx)` installs one ASGI dispatcher middleware
  that resolves, per raised exception, the handler whose declared
  exception type is *closest in the MRO* (most specific wins across all
  advice beans; ties break on the bean's `@Order`, then declaration
  order).
- Handler parameters inject from `(request: Request, exc: <exception>)`,
  resolved by annotation then conventional names; handlers may be sync or
  async. A `Response` return is sent verbatim; anything else becomes a
  `JSONResponse` with the decorator's `status_code` (default 500).
- The dispatcher sits *outside* FastAPI's `ExceptionMiddleware`, so
  `HTTPException` and `RequestValidationError` keep their built-in 404 /
  422 handling — validation passes through advice untouched. Unhandled
  exceptions still produce the default 500.

## @WebMiddleware

```python
@WebMiddleware
class RequestTimingMiddleware:
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["x-request-id"] = self._audit.request_id
        return response
```

- A `@WebMiddleware` bean defines `async dispatch(request, call_next)` —
  the Starlette `BaseHTTPMiddleware` contract — and is mounted by
  `register_middleware(app, ctx)`.
- Ordering mirrors Spring's ordered filters: the **lowest** `@Order` is
  the **outermost** middleware (sees the request first, the response
  last). Unordered beans sit innermost.
- Bean middleware runs inside the request scope, so `dispatch` may freely
  touch `Scope.REQUEST` dependencies.

## WebAutoConfiguration and server mode

The `pyleaf.autoconfigure` entry point registers `WebAutoConfiguration`
(`@ConditionalOnClass("fastapi")`, gated by `pyleaf.web.enabled`), which
provides two beans that back off to user definitions:

- `fastapiApp` — the assembled `FastAPI` app: controllers, advice
  dispatcher, bean middleware, `RequestScopeMiddleware`, CORS from
  `pyleaf.web.cors.*` (only when `allowed-origins` is set), a plain
  `GET /health` readiness route, then every `FastApiCustomizer` bean in
  `@Order` (this is the extension point `pyleaf-actuator` uses). The app
  lifespan owns `ctx.close()` when served.
- `httpClient` — a shared `httpx.AsyncClient` closed cleanly on shutdown
  even though `aclose()` is async (the managed subclass schedules the
  close on the running loop or drives it with `asyncio.run`).

`PyleafApplication.run()` detects a `FastAPI` bean and serves it with
uvicorn, blocking until shutdown; uvicorn owns the signals and the
lifespan closes the context. Properties (all CLI-overridable):

| Property | Default | Meaning |
|---|---|---|
| `pyleaf.web.enabled` | `true` | `false` disables the web beans and serving |
| `pyleaf.web.host` | `127.0.0.1` | bind host — deliberately not `0.0.0.0` |
| `pyleaf.web.port` | `8080` | bind port |
| `pyleaf.web.workers` | `1` | uvicorn workers |
| `pyleaf.web.access-log` | `true` | uvicorn access log |
| `pyleaf.web.cors.allowed-origins` | — | CSV; mounting CORS requires it |
| `pyleaf.web.cors.allowed-methods` | `*` | CSV |
| `pyleaf.web.cors.allowed-headers` | `*` | CSV |
| `pyleaf.web.cors.allow-credentials` | `false` | bool |
| `pyleaf.web.cors.max-age` | `600` | seconds |

`--no-server` (CLI) or `no_server=True` (API) boots without serving — the
CLI path installs the signal-handler shutdown instead.

## Testing: the `pyleaf_client` fixture

`pyleaf-test` provides `pyleaf_client` (function scope): it builds the
same app wiring as `fastapiApp` — controllers, advice, bean middleware,
`RequestScopeMiddleware` — over the current `pyleaf_context` and wraps it
in a `TestClient`. Because the app is built per test, `mock_bean` /
`override_bean` applied to a controller's dependencies is reflected in
the served responses, and request-scoped beans get a real scope per
request. Requires `pyleaf-web` installed; the fixture skips with a clear
message otherwise.

## Example

`examples/todo-api/` exercises the whole stack end to end —
`@RestController` CRUD over the SQLAlchemy starter, a `@Service` /
`@Repository` layer, `@ControllerAdvice` 404 mapping, a `@WebMiddleware`
request-id header and the `Scope.REQUEST` audit bean. `scripts/e2e_m2.sh`
boots it under real uvicorn and asserts the contract over HTTP.
