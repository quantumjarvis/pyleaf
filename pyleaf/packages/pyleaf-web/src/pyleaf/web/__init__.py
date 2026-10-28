"""Web functionality for pyleaf: REST controllers over FastAPI."""

from .advice import ControllerAdvice, ExceptionHandler, register_advice
from .middleware import RequestScopeMiddleware, WebMiddleware, register_middleware
from .router import build_router, register_controllers
from .stereotypes import (
    DeleteMapping,
    GetMapping,
    PatchMapping,
    PostMapping,
    PutMapping,
    RequestMapping,
    RestController,
)

__all__ = [
    "ControllerAdvice",
    "DeleteMapping",
    "ExceptionHandler",
    "GetMapping",
    "PatchMapping",
    "PostMapping",
    "PutMapping",
    "RequestMapping",
    "RequestScopeMiddleware",
    "RestController",
    "WebMiddleware",
    "build_router",
    "register_advice",
    "register_controllers",
    "register_middleware",
]
