from pyleaf.core import (
    ApplicationContext,
    BeanPostProcessor,
    Component,
    Order,
    PostConstruct,
)


def test_bpp_bean_is_a_bean_post_processor() -> None:
    """Test that a bean implementing both hooks satisfies the Protocol."""

    @Component
    class Hooks:
        def post_process_before_init(self, bean: object, name: str) -> object:
            return bean

        def post_process_after_init(self, bean: object, name: str) -> object:
            return bean

    ctx = ApplicationContext()
    ctx.register(Hooks)
    ctx.refresh()

    assert isinstance(ctx.get(Hooks), BeanPostProcessor)


def test_bpp_sees_bean_before_and_after_post_construct() -> None:
    """Test that before_init runs before @PostConstruct and after_init after."""

    phases: dict[str, bool] = {}

    @Component
    class Flagged:
        def __init__(self) -> None:
            self.initialized = False

        @PostConstruct
        def init(self) -> None:
            self.initialized = True

    @Component
    class PhaseRecorder:
        def post_process_before_init(self, bean: object, name: str) -> object:
            if isinstance(bean, Flagged):
                phases["before"] = bean.initialized
            return bean

        def post_process_after_init(self, bean: object, name: str) -> object:
            if isinstance(bean, Flagged):
                phases["after"] = bean.initialized
            return bean

    ctx = ApplicationContext()
    ctx.register(PhaseRecorder)
    ctx.register(Flagged)
    ctx.refresh()

    ctx.get(Flagged)
    assert phases == {"before": False, "after": True}


def test_bpp_before_init_can_replace_bean() -> None:
    """Test that returning a different object from before_init replaces the bean."""

    class Replacement:
        pass

    replacement = Replacement()

    @Component
    class Target:
        pass

    @Component
    class Replacer:
        def post_process_before_init(self, bean: object, name: str) -> object:
            if isinstance(bean, Target):
                return replacement
            return bean

        def post_process_after_init(self, bean: object, name: str) -> object:
            return bean

    ctx = ApplicationContext()
    ctx.register(Replacer)
    ctx.register(Target)
    ctx.refresh()

    resolved = ctx.get(Target)
    assert resolved is replacement
    # The replacement is what gets cached.
    assert ctx.get_bean("target") is replacement


def test_bpp_order_controls_invocation_order() -> None:
    """Test that @Order sorts BPPs; lower order runs first."""

    calls: list[str] = []

    @Component
    class Subject:
        pass

    @Component
    @Order(5)
    class LateBpp:
        def post_process_before_init(self, bean: object, name: str) -> object:
            if isinstance(bean, Subject):
                calls.append("late")
            return bean

    @Component
    @Order(1)
    class EarlyBpp:
        def post_process_before_init(self, bean: object, name: str) -> object:
            if isinstance(bean, Subject):
                calls.append("early")
            return bean

    ctx = ApplicationContext()
    # Register LateBpp first to prove ordering is by @Order, not registration.
    ctx.register(LateBpp)
    ctx.register(EarlyBpp)
    ctx.register(Subject)
    ctx.refresh()

    ctx.get(Subject)
    assert calls == ["early", "late"]


def test_bpp_is_not_applied_to_itself() -> None:
    """Test that a BPP never post-processes the instance being created when it
    is that instance, while still processing other beans."""

    @Component
    class Recorder:
        def __init__(self) -> None:
            self.seen: list[object] = []

        def post_process_before_init(self, bean: object, name: str) -> object:
            self.seen.append(bean)
            return bean

    @Component
    class Other:
        pass

    ctx = ApplicationContext()
    ctx.register(Recorder)
    ctx.register(Other)
    ctx.refresh()

    other = ctx.get(Other)
    recorder = ctx.get(Recorder)

    assert recorder not in recorder.seen
    assert other in recorder.seen


def test_bpp_with_only_before_init_hook_is_discovered() -> None:
    """Test that a BPP implementing only post_process_before_init (no
    after_init method at all) is still detected and applied."""

    calls: list[str] = []

    @Component
    class Watched:
        pass

    @Component
    class BeforeOnly:
        def post_process_before_init(self, bean: object, name: str) -> object:
            if isinstance(bean, Watched):
                calls.append(name)
            return bean

    ctx = ApplicationContext()
    ctx.register(BeforeOnly)
    ctx.register(Watched)
    ctx.refresh()

    ctx.get(Watched)
    assert calls == ["watched"]
