"""Web functionality for pyleaf: REST controllers over FastAPI."""

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
    "DeleteMapping",
    "GetMapping",
    "PatchMapping",
    "PostMapping",
    "PutMapping",
    "RequestMapping",
    "RestController",
    "build_router",
    "register_controllers",
]
