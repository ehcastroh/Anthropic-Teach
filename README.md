# Anthropic-Teach

Three hands-on lessons on building with the Claude API in Python. Each lesson is a
notebook with a paired homework and a real-world example project:

| # | Topic | Lesson | Homework | IRL example |
|---|---|---|---|---|
| 01 | Claude API mechanics | `lessons/01_claude_api_mechanics.ipynb` | `homework/01_claude_api_mechanics_homework.ipynb` | `irl_examples/01_claude_api_mechanics/` |
| 02 | Software engineering foundations | `lessons/02_software_engineering_foundations.ipynb` | `homework/02_software_engineering_foundations_homework.ipynb` | `irl_examples/02_software_engineering_foundations/` |
| 03 | Claude application design | `lessons/03_claude_application_design.ipynb` | `homework/03_claude_application_design_homework.ipynb` | `irl_examples/03_claude_application_design/` |

Every code example moves through three tiers: an inline introduction, a factored version
using the small `claude_code/` package, and a production-shaped version that uses a script or a
full module. Worked homework answers are in `homework/solutions/`.

---

## Lessons

**01 - Claude API mechanics.** Sync and streaming Messages API calls, content blocks and
`stop_reason`, tool use, vision, extended thinking, `count_tokens`, and the Message Batches
API with the realtime-vs-batch decision. Companion modules: `claude_code.api`, `claude_code.tools`,
`claude_code.config.model_registry`. Companion scripts: `scripts/streaming_client_smoke_test.py`,
`scripts/batches_submit_and_poll.py`.

**02 - Software engineering foundations.** The REST/JSON contract under the SDK, sync vs.
async, transient vs. terminal error classes and retry with backoff, refactoring a prototype
into a testable module, mocking the client with pytest, version control, and code review of
common first-week Claude integration mistakes. Companion modules: `claude_code.api.retry`,
`claude_code.api.errors`, `claude_code.config`.

**03 - Claude application design.** Instruction interpretation across surfaces, content
boundaries for untrusted input, JSON Schema for tools and structured output, deriving a tool
`input_schema` from a pydantic model, session hygiene, and plugin management. Companion
modules: `claude_code.prompts`, `claude_code.security.injection`, `claude_code.tools`, `claude_code.agents.loop`.

## Homework

Each homework notebook has `### YOUR CODE HERE ###` stubs and smoke tests. Compare with the
matching notebook in `homework/solutions/` once you have tried it.

- **01** - budget-capped multi-prompt pipeline, streaming with token counting, realtime vs.
  batch decisions, and a Batches API round-trip over `assets/homework_wikipedia_snippets.json`.
- **02** - retry-safe ticket triage, a `TicketTriager` class with a budget cap, pytest tests
  with a mocked client, and code review request classification.
- **03** - calendar event schema, sentiment validator, a document summarizer trust boundary,
  a pydantic-derived code review schema, and validation error handling at the trust boundary.

An unattempted homework notebook stops at its first unfilled stub. That is the signal that
the question is still open, not a bug.

## IRL examples

Self-contained projects, each with a plain-language walkthrough (`*.md`), source, and tests.
Tests run offline against mocks by default and cost nothing.

| Folder | What it shows |
|---|---|
| `irl_examples/01_claude_api_mechanics/synchronous_vs_asynchronous/` | Four network lanes (blocking, streaming, concurrent, batch) for a support-ticket triage pipeline |
| `irl_examples/02_software_engineering_foundations/integration_mocking/` | Unit testing a `CodeReviewTriager` with a mocked client, including retry and fallback paths |
| `irl_examples/03_claude_application_design/injection_detection/` | Prompt injection primer, boundary-wrapper tests, and pydantic structured outputs |
| `irl_examples/03_claude_application_design/schema_coercion/` | Forcing Claude to fill a pydantic-derived tool schema and re-inflating a typed result |

---

## Layout

```
lessons/        3 lesson notebooks
homework/       3 homework notebooks; solutions/ holds the worked answers
irl_examples/   one folder per lesson, each with runnable example projects and tests
claude_code/    importable package the notebooks use (Claude-Code as a valid import name)
scripts/        2 CLI scripts that lesson 01 invokes
assets/         input data for homework 01
flake.nix       optional Nix dev shell (Python 3.12, uv, ruff)
pyproject.toml  dependencies, pinned by uv.lock
```


## Setup

Requirements: [uv](https://docs.astral.sh/uv/) and git. uv downloads Python 3.12 itself, so
you do not need to install Python first. Works on macOS, Linux and Windows 11; CI runs the
checks below on all three on every push.

**1. Install uv**

| macOS / Linux | Windows (PowerShell) |
|---|---|
| `curl -LsSf https://astral.sh/uv/install.sh \| sh` | `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 \| iex"` |

Homebrew (`brew install uv`) and WinGet (`winget install astral-sh.uv`) also work. Open a new
terminal afterwards.

**2. Clone and install**

```bash
git clone https://github.com/ehcastroh/Anthropic-Teach.git
cd Anthropic-Teach
uv sync --frozen
```

On NixOS, or anywhere Nix is installed, `nix develop` does the same.

**3. Set your API key** (console.anthropic.com). Skip this if you only use dry-run mode (below).

| macOS / Linux (bash, zsh) | Windows (PowerShell) |
|---|---|
| `export ANTHROPIC_API_KEY="sk-ant-..."` | `$env:ANTHROPIC_API_KEY = "sk-ant-..."` |

That sets it for the current terminal only. To keep it, add the line to your shell profile
(macOS/Linux) or run `setx ANTHROPIC_API_KEY "sk-ant-..."` and reopen the terminal (Windows).
The key always comes from your environment, never from a project file, so there is no `.env`
to create. Do not paste it into a notebook cell.

**4. Start Jupyter**

```bash
uv run jupyter lab
```

### Dry-run mode

Set `CLAUDE_CODE_DRY_RUN=1` to skip every live API call. No key and no spend are needed, and
every notebook still runs top to bottom.

| macOS / Linux | Windows (PowerShell) |
|---|---|
| `export CLAUDE_CODE_DRY_RUN=1` | `$env:CLAUDE_CODE_DRY_RUN = "1"` |

A placeholder key is filled in automatically if none is set. Set the flag before `uv run jupyter lab`. Without it, each notebook makes real calls. The cover cell
of each lesson lists its cost: a few cents per lesson on Haiku, plus about $0.12 if you also
run the live batch.

## Verify everything runs

IRL example tests, offline and free (33 pass, 1 live-API test skipped). Same on every OS:

```bash
uv run pytest
```

Lessons and worked solutions, in dry-run mode, no API key needed:

```bash
# macOS / Linux
export CLAUDE_CODE_DRY_RUN=1
for nb in lessons/*.ipynb homework/solutions/*.ipynb; do
  uv run jupyter nbconvert --to notebook --execute --output-dir /tmp/nbcheck "$nb"
done
```

```powershell
# Windows (PowerShell)
$env:CLAUDE_CODE_DRY_RUN = "1"
foreach ($nb in (Get-ChildItem lessons\*.ipynb, homework\solutions\*.ipynb)) {
  uv run jupyter nbconvert --to notebook --execute --output-dir $env:TEMP\nbcheck $nb.FullName
}
```

The example tests can hit the live API on demand with `uv run pytest --online`, which is
billable.

The `.md` walkthroughs inside `irl_examples/` show bash commands (`export`, `python3`,
`PYTHONPATH=...`). On Windows, use the PowerShell forms above, run Python with `uv run python`,
and set `PYTHONPATH` with `$env:PYTHONPATH = "..."` first.
