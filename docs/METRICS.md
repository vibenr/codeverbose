# Metric definitions

All metrics are computed per function or method. Constructors are included, and nested
functions are measured as part of their parent. Line numbers come from the tree-sitter
syntax tree, not regexes, so strings that contain `//` or `#` don't confuse the counts.

| Field | Definition |
|---|---|
| `loc` | Rows of the function that contain at least one non-comment token. Docstrings are excluded. |
| `lines_total` | Rows from the signature to the closing brace or last statement. |
| `comment_lines` | Rows inside the body that contain only comments. |
| `doc_lines` | Rows of Javadoc or comments directly above the function, plus the Python docstring. |
| `complexity` | McCabe complexity: 1 + branches (`if`, loops, `case`, `catch`/`except`, ternary, comprehension `if`) + short-circuit `&&`/`\|\|`/`and`/`or`. |
| `loc_per_complexity` | `loc / complexity`. The core verbosity measure: lines spent per unit of decision logic. |
| `tokens` | Non-comment leaf tokens. |
| `avg_identifier_len` | Mean length of distinct identifiers in the function. |
| `compression_ratio` | `len(zlib(source)) / len(source)` at level 9. Repetitive or boilerplate code compresses well, so its ratio is **lower**. Reports only include functions with at least 5 LOC, because tiny inputs are dominated by zlib overhead. |
| `null_checks` | Comparisons against `null`/`None`, plus calls such as `Objects.requireNonNull`, `Optional.ofNullable`, and `Preconditions.checkNotNull`. |
| `log_calls` | Calls on a receiver named `log`, `logger`, `LOG`, `LOGGER`, `logging`, or `System.out`/`System.err`, plus Python `print`. |
| `catch_blocks` / `trivial_catches` | Catch or except clauses, and how many of those only log, rethrow, `pass`, or are empty. |
| `narrating_comments` | Comments with at least 2 content words, where at least half of those words (after camelCase/snake_case splitting and light stemming) appear in the identifiers of the line they describe. That's the same line for trailing comments and the next code line otherwise. Catches `// Initialize the total price` above `double totalPrice = 0`. |
| `style_signals` | Comments or docs matching `Step N:`, "this function/method/class", "here we / let's / now we", emoji, or `// =====` banners. These are heuristic, and humans write them too. |

## Aggregation

- **Medians** are used for per-function ratios, because function-size distributions are heavily skewed.
- **Pooled rates** are used for count metrics: `sum(count) / sum(loc) × 100` across the group.
- **p-value**: a two-sided Mann–Whitney U test, using the normal approximation with tie correction,
  on per-function values. It's reported only when both groups have at least 8 functions. With
  thousands of functions almost everything will be "significant", so look at the effect size
  (the % column) and the distribution charts too.
