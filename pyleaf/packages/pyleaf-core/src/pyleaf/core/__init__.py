"""Core functionality for pyleaf framework."""

from .aop import (
    After,
    AfterReturning,
    AfterThrowing,
    Around,
    Aspect,
    Before,
    Invocation,
    MethodInterceptor,
    Pointcut,
    annotated,
    bean_type,
    mark,
    name_matches,
    within,
)
from .context import ApplicationContext
from .definition import Scope
from .errors import (
    BeanCreationError,
    CircularDependency,
    NoSuchBean,
    NoUniqueBean,
    PyleafError,
)
from .lifecycle import PostConstruct, PreDestroy
from .postprocess import BeanPostProcessor
from .stereotypes import (
    Bean,
    Component,
    Configuration,
    Controller,
    Lazy,
    Order,
    Primary,
    Qualifier,
    Repository,
    Service,
)

__all__ = [
    "After",
    "AfterReturning",
    "AfterThrowing",
    "ApplicationContext",
    "Around",
    "Aspect",
    "Bean",
    "BeanCreationError",
    "BeanPostProcessor",
    "Before",
    "CircularDependency",
    "Component",
    "Configuration",
    "Controller",
    "Invocation",
    "Lazy",
    "MethodInterceptor",
    "NoSuchBean",
    "NoUniqueBean",
    "Order",
    "Pointcut",
    "PostConstruct",
    "PreDestroy",
    "Primary",
    "PyleafError",
    "Qualifier",
    "Repository",
    "Scope",
    "Service",
    "annotated",
    "bean_type",
    "mark",
    "name_matches",
    "within",
]
