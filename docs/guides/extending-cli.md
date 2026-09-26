# Extending CLI

This guide shows how to add your own command-line options and an event handler that reacts to test progress, for example to write custom reports or send results to another system.

## Prerequisites

- Schemathesis installed in a Python environment you can import your own module from (`uvx` works if the module is in the current directory)
- A module name for your extension; this guide uses `cli_extensions.py`

## Setting up CLI extensions

### Step 1: Create an option group

Group related options together for better CLI organization:

```python
# cli_extensions.py
import click
from schemathesis import cli
from schemathesis.engine import Status, events

# Create a group for your options
group = cli.add_group("Counter options")
```

### Step 2: Add options to the group

```python
group.add_option("--counter-initial", type=int, default=0, help="Initial counter value", envvar="COUNTER_INITIAL")

group.add_option(
    "--counter-output",
    type=click.Path(file_okay=True, dir_okay=False, writable=True),
    help="File to write counter results",
    envvar="COUNTER_OUTPUT",
)

group.add_option(
    "--counter-verbose", is_flag=True, default=False, help="Show detailed counter information", envvar="COUNTER_VERBOSE"
)
```

### Step 3: Create the event handler

The handler receives every engine event. A *scenario* is one API operation in one test phase, so an operation tested in the coverage and fuzzing phases produces two scenarios:

```python
@cli.handler()
class CounterHandler(cli.EventHandler):
    def __init__(self, *args, **params):
        self.initial_value = params["counter_initial"]
        self.output_file = params["counter_output"]
        self.verbose = params["counter_verbose"]

        self.total_events = self.initial_value
        self.scenarios = 0
        self.failures = 0
        self.errors = []

    def handle_event(self, ctx, event) -> None:
        self.total_events += 1

        if isinstance(event, events.ScenarioStarted):
            self.scenarios += 1

        elif isinstance(event, events.ScenarioFinished):
            if event.status == Status.FAILURE:
                self.failures += 1
            if self.verbose:
                ctx.add_summary_line(f"{event.phase.value} {event.label}: {event.status.name}")

        elif isinstance(event, events.NonFatalError):
            self.errors.append(event.info.message)

        elif isinstance(event, events.EngineFinished):
            self._generate_summary(ctx)

    def _generate_summary(self, ctx):
        ctx.add_summary_line("")
        ctx.add_summary_line("Counter Summary:")
        ctx.add_summary_line(f"  Total events: {self.total_events}")
        ctx.add_summary_line(f"  Scenarios: {self.scenarios}")
        ctx.add_summary_line(f"  Failed scenarios: {self.failures}")
        ctx.add_summary_line(f"  Errors: {len(self.errors)}")

        if self.output_file:
            self._write_output_file()
            ctx.add_summary_line(f"  Results written to: {self.output_file}")

    def _write_output_file(self):
        with open(self.output_file, "w") as f:
            f.write("Counter Results\n")
            f.write(f"Total events: {self.total_events}\n")
            f.write(f"Scenarios: {self.scenarios}\n")
            f.write(f"Failed scenarios: {self.failures}\n")
            f.write(f"Errors: {len(self.errors)}\n")

            if self.errors:
                f.write("\nErrors:\n")
                for error in self.errors:
                    f.write(f"- {error}\n")
```

### Step 4: Load the extension and run

!!! note "Works with `st fuzz` too"
    Handlers registered with `@cli.handler()` run for both `st run` and `st fuzz`. Events like `EngineFinished` fire in both commands.

```bash
export SCHEMATHESIS_HOOKS=cli_extensions

uvx schemathesis run \
  --counter-initial 100 \
  --counter-output results.txt \
  --counter-verbose \
  http://localhost:8000/openapi.json
```

Lines added with `ctx.add_summary_line` appear in the summary section. For an API with one operation:

```
examples POST /performance: SKIP
coverage POST /performance: SUCCESS
fuzzing POST /performance: SUCCESS

Counter Summary:
  Total events: 128
  Scenarios: 3
  Failed scenarios: 0
  Errors: 0
  Results written to: results.txt
```

## Troubleshooting

**`No such option '--counter-initial'`.** The extension was not loaded. Set `SCHEMATHESIS_HOOKS` before the command and check that `cli_extensions.py` is importable from the current directory.

**`KeyError` in `__init__`.** `params` keys are the option names without leading dashes, with `-` replaced by `_`: `--counter-output` becomes `counter_output`.
