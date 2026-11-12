# Contributing to codeverbose

## Dev setup

```bash
git clone https://github.com/your-username/codeverbose.git
cd codeverbose
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
ruff check . && ruff format --check .
```

## Adding a language

1. Add the grammar wheel (for example `tree-sitter-go`) to `dependencies` in `pyproject.toml`.
2. Run `codeverbose debug-ast some_file.go` to see the grammar's node type names.
3. Add a `LanguageSpec` to `src/codeverbose/languages.py` and append it to `_SPECS`. You need:
   - `function_types`: nodes that define a function or method;
   - `comment_types`;
   - `decision_types`: nodes that each add 1 to complexity;
   - `boolean_op_types`: binary nodes whose `operator` field may be `&&`/`||`;
   - `call_types`, `catch_types`, `throw_types`, `null_literal_types`, plus logging and null-check names.
   If the grammar's call node doesn't use `function` or `object`+`name` fields, extend `callee_text()`.
4. Add a fixture under `tests/fixtures/` with one verbose and one terse function, and assert
   the metrics in `tests/test_metrics.py`.

## Improving AI-commit detection

Patterns live in `src/codeverbose/labeling.py` (`_AI_TOOLS`, `AI_MESSAGE_PATTERNS`,
`AI_AUTHOR_PATTERNS`). Add a parametrized case to `tests/test_labeling.py`, and add a
**negative** case whenever the tool's name could also be a person's name.

## Adding a metric

1. Compute it in `measure_function()` (`metrics.py`) and add the field to `FunctionMetrics`.
2. Add the column to `FIELDNAMES` in `scanner.py`.
3. Add a `Metric(...)` entry in `report.py`, and document it in `docs/METRICS.md`.

## Pull requests

Keep PRs focused, include tests, and update the docs when behaviour changes.
