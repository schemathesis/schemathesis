# Extending CLI

Add custom command-line options and event handlers to integrate Schemathesis with your testing workflow.

## When to extend the CLI

Extend the CLI when you need to:

- **Add reporting options** - Custom output formats or tracking metrics
- **Integrate with external tools** - Send results to monitoring systems or databases  

## Setting up CLI extensions

### Step 1: Create an option group

Group related options together for better CLI organization:

```python
# cli_extensions.py
import click
from schemathesis import cli

# Create a group for your options
group = cli.add_group("Counter options")
```

### Step 2: Add options to the group

```python
group.add_option(
    "--counter-initial", 
    type=int, 
    default=0,
    help="Initial counter value",
    envvar="COUNTER_INITIAL"
)

group.add_option(
    "--counter-output",
    type=click.Path(file_okay=True, dir_okay=False, writable=True),
    help="File to write counter results",
    envvar="COUNTER_OUTPUT"
)

group.add_option(
    "--counter-verbose",
    is_flag=True,
    default=False,
    help="Show detailed counter information",
    envvar="COUNTER_VERBOSE"
)
```

### Step 3: Create the event handler

```python
@cli.handler()
class CounterHandler(cli.EventHandler):
    def __init__(self, *args, **params):
        self.initial_value = params["counter_initial"]
        self.output_file = params["counter_output"]
        self.verbose = params["counter_verbose"]

        self.total_events = self.initial_value
        self.test_cases = 0
        self.failures = 0
        self.errors = []

    def handle_event(self, ctx, event) -> None:
        self.total_events += 1

        if isinstance(event, events.ScenarioStarted):
            self.test_cases += 1
            if self.verbose:
                ctx.add_summary_line(f"Starting test #{self.test_cases}")

        elif isinstance(event, events.ScenarioFinished):
            if event.status == Status.SUCCESS:
                if self.verbose:
                    ctx.add_summary_line(f"✓ Test #{self.test_cases} passed")
            elif event.status == Status.FAILURE:
                self.failures += 1
                if self.verbose:
                    ctx.add_summary_line(f"✗ Test #{self.test_cases} failed")

        elif isinstance(event, events.NonFatalError):
            self.errors.append(event.info.message)

        elif isinstance(event, events.EngineFinished):
            self._generate_summary(ctx)

    def _generate_summary(self, context):
        context.add_summary_line("")
        context.add_summary_line("Counter Summary:")
        context.add_summary_line(f"  Total events: {self.total_events}")
        context.add_summary_line(f"  Test cases: {self.test_cases}")
        context.add_summary_line(f"  Failures: {self.failures}")
        context.add_summary_line(f"  Errors: {len(self.errors)}")

        if self.output_file:
            self._write_output_file()
            context.add_summary_line(
                f"  Results written to: {self.output_file}"
            )

    def _write_output_file(self):
        with open(self.output_file, "w") as f:
            f.write("Counter Results\n")
            f.write(f"Total events: {self.total_events}\n")
            f.write(f"Test cases: {self.test_cases}\n")
            f.write(f"Failures: {self.failures}\n")
            f.write(f"Errors: {len(self.errors)}\n")

            if self.errors:
                f.write("\nErrors:\n")
                for error in self.errors:
                    f.write(f"- {error}\n")
```

## Using the extension

```bash
export SCHEMATHESIS_HOOKS=cli_extensions

# Use the custom options
schemathesis run \
  --counter-initial 100 \
  --counter-output results.txt \
  --counter-verbose \
  http://localhost:8000/openapi.json
```

**Output:**
```
Starting test case #1
✓ Test case #1 passed
Starting test case #2
✗ Test case #2 failed

Counter Summary:
  Total events: 125
  Test cases: 2
  Failures: 1
  Errors: 0
  Results written to: results.txt
```
