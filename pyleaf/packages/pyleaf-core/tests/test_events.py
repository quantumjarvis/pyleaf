"""RED acceptance tests for application events (WI 1.6).

``@EventListener`` methods on beans receive events published through the
context: dispatch is by the declared parameter type (a base type catches
subclass events), ``@Order`` sorts listeners, a throwing listener never
blocks the rest, and listener beans are force-instantiated at refresh like
post-processors and aspects.
"""

from pyleaf.core import (
    ApplicationContext,
    Component,
    EventListener,
    EventPublisher,
    Order,
)


class Created:
    pass


class Deleted:
    pass


class Base:
    pass


class Sub(Base):
    pass


class Other:
    pass


def test_publish_delivers_event_to_matching_listener() -> None:
    seen: list[Created] = []

    @Component
    class Recorder:
        @EventListener
        def on_created(self, evt: Created) -> None:
            seen.append(evt)

    ctx = ApplicationContext()
    ctx.register(Recorder)
    ctx.refresh()

    evt = Created()
    ctx.publish(evt)

    assert seen == [evt]


def test_base_class_listener_receives_subclass_events() -> None:
    seen: list[object] = []

    @Component
    class Recorder:
        @EventListener
        def on_base(self, evt: Base) -> None:
            seen.append(evt)

    ctx = ApplicationContext()
    ctx.register(Recorder)
    ctx.refresh()

    sub = Sub()
    ctx.publish(sub)

    assert seen == [sub]


def test_unrelated_listener_does_not_fire() -> None:
    seen: list[object] = []

    @Component
    class Recorder:
        @EventListener
        def on_other(self, evt: Other) -> None:
            seen.append(evt)

        @EventListener
        def on_deleted(self, evt: Deleted) -> None:
            seen.append(evt)

    ctx = ApplicationContext()
    ctx.register(Recorder)
    ctx.refresh()

    ctx.publish(Created())

    assert seen == []


def test_order_decorator_sorts_listener_invocation() -> None:
    calls: list[str] = []

    @Component
    class Recorder:
        @EventListener
        @Order(2)
        def second(self, evt: Created) -> None:
            calls.append("second")

        @EventListener
        @Order(1)
        def first(self, evt: Created) -> None:
            calls.append("first")

    ctx = ApplicationContext()
    ctx.register(Recorder)
    ctx.refresh()

    ctx.publish(Created())

    assert calls == ["first", "second"]


def test_throwing_listener_does_not_block_later_listeners() -> None:
    calls: list[str] = []

    @Component
    class Bad:
        @EventListener
        @Order(1)
        def boom(self, evt: Created) -> None:
            raise RuntimeError("listener exploded")

    @Component
    class Good:
        @EventListener
        @Order(2)
        def ok(self, evt: Created) -> None:
            calls.append("ok")

    ctx = ApplicationContext()
    ctx.register(Bad)
    ctx.register(Good)
    ctx.refresh()

    ctx.publish(Created())  # must not raise

    assert calls == ["ok"]


def test_publish_with_no_listeners_is_a_no_op() -> None:
    ctx = ApplicationContext()
    ctx.refresh()

    ctx.publish(Created())  # nothing registered; nothing happens


def test_lazy_listener_bean_is_force_instantiated_at_refresh() -> None:
    created: list[str] = []
    seen: list[object] = []

    @Component  # lazy singleton by default; nothing ever calls ctx.get()
    class LazyRecorder:
        def __init__(self) -> None:
            created.append("lazyRecorder")
            self.seen = seen

        @EventListener
        def on_created(self, evt: Created) -> None:
            self.seen.append(evt)

    ctx = ApplicationContext()
    ctx.register(LazyRecorder)
    ctx.refresh()

    # Force-instantiated during refresh despite being lazy.
    assert created == ["lazyRecorder"]

    evt = Created()
    ctx.publish(evt)
    assert seen == [evt]


def test_event_publisher_is_injectable() -> None:
    seen: list[object] = []

    @Component
    class Recorder:
        @EventListener
        def on_created(self, evt: Created) -> None:
            seen.append(evt)

    @Component
    class NeedsPublisher:
        def __init__(self, pub: EventPublisher) -> None:
            self.pub = pub

    ctx = ApplicationContext()
    ctx.register(Recorder)
    ctx.register(NeedsPublisher)
    ctx.refresh()

    bean = ctx.get(NeedsPublisher)
    assert hasattr(bean.pub, "publish")
    assert hasattr(bean.pub, "publish_async")

    evt = Created()
    bean.pub.publish(evt)
    assert seen == [evt]
