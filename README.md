# codeverbose

**Measure how verbose AI-assisted code is compared to human-written code, in your own repository.**

`codeverbose` parses every function in a codebase, uses `git` history to work out whether
an AI tool helped write it, and compares the two groups on metrics that are normalised by
how much logic the code actually contains. "More lines" on its own only tells you which code
did more work; _lines per decision point_ tells you which code spent more words saying it.

```text
$ codeverbose analyze .
metric                           ai  human  unlabeled  AI vs human       p
----------------------------  -----  -----  ---------  -----------  ------
Function length (LOC)          14.0    6.0        6.0      +133.3%  <0.001
Lines per decision point       2.80   1.50       1.50       +86.7%  <0.001
Comment lines / code line     0.357  0.000      0.000      AI only  <0.001
Avg identifier length           9.7    2.6        2.7      +273.7%  <0.001
Compression ratio             0.403  0.797      0.798       -49.4%  <0.001
Narrating comments / 100 LOC  28.57   0.00       0.00      AI only  <0.001
...
```

<sub>(Output from the synthetic demo repo in the tests, not a real-world finding.)</sub>

Supported languages: **Java**, **Python**. Adding another is about 40 lines; see
[CONTRIBUTING.md](CONTRIBUTING.md).

## Quick start

Requires Python 3.10+ and `git`.

```bash
pip install git+https://github.com/vibenr/codeverbose.git
# or, from a clone:  pip install -e .

cd path/to/your/repo
codeverbose commits             # check how commits were labeled first
codeverbose analyze . --html report.html
```

Open `report.html` for the full table plus distribution charts.

## How code gets labeled

Every function gets one of four labels, decided by `git blame` over its lines:

| Label       | Meaning                                                                        |
| ----------- | ------------------------------------------------------------------------------ |
| `ai`        | Most lines come from commits with an AI marker                                 |
| `human`     | Most lines come from commits before the cutoff date, or matched a `human` rule |
| `unlabeled` | Authorship couldn't be determined                                              |
| `mixed`     | No single label covers at least 60% of the lines (`--min-share`)               |

A commit counts as **AI-assisted** if it has any of these markers:

- a `Co-Authored-By:` trailer naming Claude, Copilot, Cursor, Codex, Gemini, Devin, Aider, Windsurf, and others;
- a "Generated with …" footer such as `🤖 Generated with Claude Code`, or `aider:` commit prefixes;
- a known AI agent author email (for example `noreply@anthropic.com`, Copilot or Cursor agent accounts).

A commit counts as **human** if it predates `--human-before` (default `2022-06-21`, when GitHub
Copilot became generally available). Pass `--human-before none` to disable this.

All remaining commits are `unlabeled`. Add `--assume-human` if your team discloses AI use
reliably and you want to treat unmarked commits as human.

### Custom rules

When your team marks AI work differently (a bot account, a branch convention, a folder of
generated code), write a rules file:

```csv
kind,pattern,label
author,ci-agent@yourcorp\.com,ai
commit,4f9a2c1,ai
path,legacy/**,human
```

```bash
codeverbose analyze . --labels labels.csv
```

Rules take priority over the built-in detection. `author` patterns are regexes, `commit`
patterns are SHA prefixes, and `path` patterns are globs.

### Without git: benchmark mode

You can compare two folders directly, for example LLM solutions against reference solutions:

```bash
codeverbose scan llm_outputs/ --label ai    -o ai.csv
codeverbose scan reference/   --label human -o human.csv
codeverbose report ai.csv human.csv --html report.html
```

## Metrics

Each metric is computed per function. Short functions (under 3 LOC by default) are ignored
so getters don't drown out everything else. Full definitions are in
[docs/METRICS.md](docs/METRICS.md).

| Metric                                  | What it captures                                                    |
| --------------------------------------- | ------------------------------------------------------------------- |
| Lines per decision point                | LOC ÷ cyclomatic complexity. The headline verbosity number.         |
| Comment / doc lines per code line       | How much prose surrounds the code                                   |
| Narrating comments                      | Comments that restate the next line (`// increment counter`)        |
| Compression ratio                       | zlib-compressed size ÷ raw size; lower means more repetitive code   |
| Avg identifier length                   | `userAccountValidationResult` versus `result`                       |
| Null checks, log calls, trivial catches | Defensive and ceremonial code per 100 LOC                           |
| AI style signals                        | `Step 1:`, "This function…", emoji, and banner comments (heuristic) |

The AI-vs-human column shows the relative difference. The `p` column is a two-sided
Mann–Whitney U test on the per-function values.

## Commands

| Command                                        | Purpose                                                                     |
| ---------------------------------------------- | --------------------------------------------------------------------------- |
| `codeverbose analyze [PATH]`                   | Scan and report in one step. Add `--html FILE`, `--json`, or `-o rows.csv`  |
| `codeverbose scan [PATH] -o rows.csv`          | Write per-function metrics to CSV for your own analysis                     |
| `codeverbose report a.csv [b.csv ...]`         | Build a report from one or more scan CSVs                                   |
| `codeverbose commits [PATH] --list ai`         | Show how each commit was labeled, to audit before trusting results          |
| `codeverbose compare <commit> [PATH]`          | Compare verbosity before and after a commit (see below)                     |
| `codeverbose compare <ref_a>..<ref_b> [PATH]`  | Compare verbosity between two arbitrary refs                                |
| `codeverbose debug-ast FILE`                   | Print the syntax tree, which helps when adding a language                   |

### Commit comparison (`compare`)

Compare how verbose code got before and after a specific commit:

```bash
codeverbose compare a3f9c12 /path/to/your/repo
codeverbose compare main..HEAD /path/to/your/repo
codeverbose compare a3f9c12 /path/to/your/repo --json
```

With an Anthropic API key, add `--ai` to get a Claude qualitative summary:

```bash
ANTHROPIC_API_KEY=sk-... codeverbose compare a3f9c12 /path/to/your/repo --ai
```

The output has three sections:

1. **Aggregate metric deltas** — mean change across all modified functions
2. **Top most-changed functions** — ranked by |ΔLOC|
3. **New code verbosity** — full metric table for every added function, plus aggregate averages

> **Note: `compare` requires a local git clone.**
> The commit hash must exist in the repository on your machine. If a hash is missing,
> run `git fetch --all` inside the repo first. Remote-only commits (not yet fetched)
> and commits from other repositories will not be found.
> GitHub API support (so you can pass a hash without cloning) is not yet implemented.

Useful flags include `--include 'src/main/**'`, `--exclude '**/test/**'`, `--lang java`,
`--max-files 500` for a quick look, and `--jobs N` for parallel `git blame`.

## GitHub Action

```yaml
# .github/workflows/codeverbose.yml
name: codeverbose
on: [workflow_dispatch, pull_request]
jobs:
  verbosity:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 0 } # full history is required for labeling
      - uses: your-username/codeverbose@v0.1.0
        with:
          args: --include 'src/**'
```

The action writes the table to the job summary and uploads `codeverbose-report.html` as an artifact.

## Read the results honestly

- **Only disclosed AI use is detectable.** After 2022, the human bucket contains some
  undisclosed AI code, which shrinks any real difference. That's why `unlabeled` is always
  shown separately.
- **Verbosity isn't the same as bad code.** Explicit error handling in a payment path may be
  exactly right. Treat the numbers as a description of style, not as a quality score.
- **Confounders are real.** AI-assisted code is newer and may sit in different parts of the
  codebase. Use `--include` to compare like with like, such as one module or one service.
- **Squash merges hide authorship.** If the merge commit lacks the trailer, the code is
  labeled by the merge commit.

## Contributing

Contributions are welcome, especially new languages, better AI-commit detection, and new
metrics. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT
