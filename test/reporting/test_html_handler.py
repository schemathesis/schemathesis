from schemathesis.cli.commands.fuzz.context import FuzzExecutionContext
from schemathesis.cli.commands.run.handlers.html import HtmlReportHandler
from schemathesis.config import SchemathesisConfig
from schemathesis.engine import events
from schemathesis.engine.run import PhaseName


def test_handler_keeps_first_seen_error_detail_per_title(tmp_path):
    execution_context = FuzzExecutionContext(config=SchemathesisConfig().projects.get_default())
    handler = HtmlReportHandler(output_dir=tmp_path / "report")
    for label, message in (("GET /a", "first"), ("GET /b", "second")):
        event = events.NonFatalError(
            error=ConnectionError(message), phase=PhaseName.FUZZING, label=label, related_to_operation=True
        )
        execution_context.on_event(event)
        handler.handle_event(execution_context, event)
    handler.shutdown(execution_context)
    index = (tmp_path / "report" / "index.html").read_text(encoding="utf-8")
    assert "first" in index
    assert "second" not in index
    assert "2 occurrences" in index
    assert '<span class="path">/a</span>' in index
    assert '<span class="path">/b</span>' in index
