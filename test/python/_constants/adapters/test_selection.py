from schemathesis.python._constants.adapters import select_adapter


class _DummyAdapter:
    name = "dummy"

    def matches(self, app):
        return getattr(app, "_dummy", False)

    def handlers(self, app):
        return []


def test_select_returns_first_match():
    a = _DummyAdapter()

    class App:
        _dummy = True

    assert select_adapter(App(), adapters=[a]) is a


def test_select_returns_none_when_no_match():
    assert select_adapter(object(), adapters=[_DummyAdapter()]) is None


def test_matches_raising_skips_adapter():
    class Bad:
        name = "bad"

        def matches(self, app):
            raise RuntimeError("boom")

        def handlers(self, app):
            return []

    class App:
        _dummy = True

    matched = select_adapter(App(), adapters=[Bad(), _DummyAdapter()])
    assert matched is not None and matched.name == "dummy"
