"""Routing tests: ``@RestController`` + ``@*Mapping`` over FastAPI (WI 2.1)."""

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from pyleaf.core import ApplicationContext, Order, Service
from pyleaf.web import (
    DeleteMapping,
    GetMapping,
    PatchMapping,
    PostMapping,
    PutMapping,
    RequestMapping,
    RestController,
    build_router,
    register_controllers,
)


def _client(ctx: ApplicationContext) -> TestClient:
    app = FastAPI()
    app.include_router(build_router(ctx))
    return TestClient(app)


class TodoIn(BaseModel):
    title: str


class Todo(BaseModel):
    id: int
    title: str


def test_get_with_prefix_and_path_param() -> None:
    @RestController("/todos")
    class TodoController:
        @GetMapping("/{todo_id}")
        def get_todo(self, todo_id: int) -> dict[str, object]:
            return {"id": todo_id, "title": f"todo-{todo_id}"}

    ctx = ApplicationContext()
    ctx.register(TodoController)
    ctx.refresh()

    resp = _client(ctx).get("/todos/42")
    assert resp.status_code == 200
    assert resp.json() == {"id": 42, "title": "todo-42"}


def test_decorator_variants() -> None:
    """Bare ``@RestController``, keyword prefix and bare ``@GetMapping``."""

    @RestController
    class BareController:
        @GetMapping("/x")
        def x(self) -> str:
            return "x"

    @RestController(prefix="/api")
    class KwController:
        @GetMapping("/y")
        def y(self) -> str:
            return "y"

    @RestController("/bare-map")
    class BareMappingController:
        @GetMapping
        def root(self) -> str:
            return "root"

    ctx = ApplicationContext()
    ctx.register(BareController)
    ctx.register(KwController)
    ctx.register(BareMappingController)
    ctx.refresh()

    client = _client(ctx)
    assert client.get("/x").json() == "x"
    assert client.get("/api/y").json() == "y"
    assert client.get("/bare-map").json() == "root"


def test_all_http_methods() -> None:
    @RestController("/items")
    class ItemController:
        @GetMapping("/{item_id}")
        def read(self, item_id: int) -> dict[str, object]:
            return {"verb": "GET", "id": item_id}

        @PostMapping("")
        def create(self) -> dict[str, str]:
            return {"verb": "POST"}

        @PutMapping("/{item_id}")
        def replace(self, item_id: int) -> dict[str, object]:
            return {"verb": "PUT", "id": item_id}

        @PatchMapping("/{item_id}")
        def patch(self, item_id: int) -> dict[str, object]:
            return {"verb": "PATCH", "id": item_id}

        @DeleteMapping("/{item_id}")
        def delete(self, item_id: int) -> dict[str, object]:
            return {"verb": "DELETE", "id": item_id}

    ctx = ApplicationContext()
    ctx.register(ItemController)
    ctx.refresh()

    client = _client(ctx)
    assert client.get("/items/1").json() == {"verb": "GET", "id": 1}
    assert client.post("/items").json() == {"verb": "POST"}
    assert client.put("/items/2").json() == {"verb": "PUT", "id": 2}
    assert client.patch("/items/3").json() == {"verb": "PATCH", "id": 3}
    assert client.delete("/items/4").json() == {"verb": "DELETE", "id": 4}


def test_request_mapping_multi_method() -> None:
    @RestController
    class MultiController:
        @RequestMapping("/multi", methods=("GET", "POST"))
        def multi(self) -> dict[str, bool]:
            return {"ok": True}

    ctx = ApplicationContext()
    ctx.register(MultiController)
    ctx.refresh()

    client = _client(ctx)
    assert client.get("/multi").status_code == 200
    assert client.post("/multi").status_code == 200
    assert client.put("/multi").status_code == 405


def test_stacked_mappings() -> None:
    """Two mapping decorators on one method register both routes."""

    @RestController("/stack")
    class StackController:
        @GetMapping("/a")
        @PostMapping("/b")
        def both(self) -> str:
            return "both"

    ctx = ApplicationContext()
    ctx.register(StackController)
    ctx.refresh()

    client = _client(ctx)
    assert client.get("/stack/a").json() == "both"
    assert client.post("/stack/b").json() == "both"


def test_post_body_pydantic_and_status_code() -> None:
    @RestController("/todos")
    class TodoController:
        @PostMapping("", status_code=201)
        def create(self, body: TodoIn) -> Todo:
            return Todo(id=1, title=body.title)

    ctx = ApplicationContext()
    ctx.register(TodoController)
    ctx.refresh()

    client = _client(ctx)
    resp = client.post("/todos", json={"title": "write tests"})
    assert resp.status_code == 201
    assert resp.json() == {"id": 1, "title": "write tests"}
    assert client.post("/todos", json={"bad": True}).status_code == 422


def test_query_params() -> None:
    @RestController("/search")
    class SearchController:
        @GetMapping("")
        def search(self, q: str, limit: int = 10) -> dict[str, object]:
            return {"q": q, "limit": limit}

    ctx = ApplicationContext()
    ctx.register(SearchController)
    ctx.refresh()

    client = _client(ctx)
    assert client.get("/search", params={"q": "abc"}).json() == {
        "q": "abc",
        "limit": 10,
    }
    assert client.get("/search", params={"q": "abc", "limit": 3}).json() == {
        "q": "abc",
        "limit": 3,
    }
    assert client.get("/search").status_code == 422


def test_route_order_is_deterministic() -> None:
    """Defs sort by (@Order, registration); methods keep declaration order."""

    @RestController("/b")
    class BController:
        @GetMapping("/second")
        def second(self) -> str:
            return "b2"

        @GetMapping("/first")
        def first(self) -> str:
            return "b1"

    @Order(1)
    @RestController("/a")
    class AController:
        @GetMapping("/x")
        def x(self) -> str:
            return "x"

    ctx = ApplicationContext()
    ctx.register(BController)  # registered first, but ordered second
    ctx.register(AController)
    ctx.refresh()

    router = build_router(ctx)
    paths = [route.path for route in router.routes]
    assert paths == ["/a/x", "/b/second", "/b/first"]


def test_declaration_order_controls_matching() -> None:
    """``/special`` declared before ``/{thing_id}`` wins path matching."""

    @RestController("/things")
    class ThingController:
        @GetMapping("/special")
        def special(self) -> str:
            return "special"

        @GetMapping("/{thing_id}")
        def by_id(self, thing_id: str) -> str:
            return f"id:{thing_id}"

    ctx = ApplicationContext()
    ctx.register(ThingController)
    ctx.refresh()

    client = _client(ctx)
    assert client.get("/things/special").json() == "special"
    assert client.get("/things/abc").json() == "id:abc"


def test_404_and_405() -> None:
    @RestController("/only-get")
    class OnlyGetController:
        @GetMapping("")
        def read(self) -> str:
            return "ok"

    ctx = ApplicationContext()
    ctx.register(OnlyGetController)
    ctx.refresh()

    client = _client(ctx)
    assert client.get("/nowhere").status_code == 404
    assert client.post("/only-get").status_code == 405


def test_register_controllers_convenience() -> None:
    """``register_controllers`` mounts on FastAPI apps and APIRouters."""

    @RestController("/c")
    class CController:
        @GetMapping("/ping")
        def ping(self) -> str:
            return "pong"

    ctx = ApplicationContext()
    ctx.register(CController)
    ctx.refresh()

    app = FastAPI()
    returned = register_controllers(app, ctx)
    assert returned is app
    assert TestClient(app).get("/c/ping").json() == "pong"

    router = APIRouter()
    assert register_controllers(router, ctx) is router
    nested = FastAPI()
    nested.include_router(router)
    assert TestClient(nested).get("/c/ping").json() == "pong"


def test_non_controller_beans_are_ignored() -> None:
    @Service
    class PlainService:
        pass

    @RestController("/solo")
    class SoloController:
        @GetMapping("")
        def read(self) -> str:
            return "ok"

    ctx = ApplicationContext()
    ctx.register(PlainService)
    ctx.register(SoloController)
    ctx.refresh()

    router = build_router(ctx)
    assert [route.path for route in router.routes] == ["/solo"]


def test_async_handler() -> None:
    @RestController("/async")
    class AsyncController:
        @GetMapping("/{n}")
        async def get_n(self, n: int) -> dict[str, int]:
            return {"n": n}

    ctx = ApplicationContext()
    ctx.register(AsyncController)
    ctx.refresh()

    resp = _client(ctx).get("/async/7")
    assert resp.status_code == 200
    assert resp.json() == {"n": 7}
