# Schema Coercion: The Customs Declaration Form

## Opening metaphor

When goods cross an international border, the carrier does not hand the customs officer
a note that says "it's stuff." The carrier fills out a **customs declaration form**: a
structured manifest with specific fields - weight, category, declared value, hazard class,
country of origin. The form enforces a contract. Customs can act on the data because the
shape is guaranteed.

Schema coercion applies the same discipline to data crossing the
**model-to-application boundary**. Claude produces text. Your application needs typed
objects. The "customs declaration form" is your Pydantic model - the contract that every
piece of data must satisfy before it enters your code. Tool-forcing is the mechanism
that makes Claude fill out the form rather than writing free-text commentary about it.

The core takeaway: **define the form in Python (Pydantic), derive the JSON Schema
automatically, force Claude to complete the form via tool-calling, and re-inflate the
result into a typed object with full validation at the boundary.**

## The problem with free-text parsing

```python
# Fragile: Claude could return anything
resp = client.messages.create(...)
text = resp.content[0].text
data = json.loads(text)  # may raise; Claude may wrap in markdown
decision = data["gate_decision"]  # KeyError if field renamed; silent wrong value if shape shifted
```

Every `json.loads` call is a leap of faith. The schema is implicit, lives only in the
system prompt, and drifts from your application types as your code evolves.

## The coercion pipeline

```
1. Define       class DeploymentGateDecision(BaseModel): ...
                       ^-- one source of truth for the contract

2. Derive       schema = DeploymentGateDecision.model_json_schema()
                       ^-- JSON Schema derived from Python types; stays in sync automatically

3. Force        tools=[{"name": TOOL_NAME, "input_schema": schema}]
                tool_choice={"type": "tool", "name": TOOL_NAME}
                       ^-- API validates the response against the schema before returning

4. Inflate      decision = DeploymentGateDecision(**tool_block.input)
                       ^-- Pydantic re-validates with tighter field constraints (Field(ge=0, le=1))
                       ^-- ValidationError is the retry signal, not a crash
```

## Setup

Requires `pydantic >= 2.0` (for `model_json_schema()`):

```bash
pip install "pydantic>=2.0"
```

API key via the shared shell config (see repo README):

```bash
# Already exported from ~/Projects/dotfiles/secrets/secrets.sh via .zshrc
echo $ANTHROPIC_API_KEY
```

## The implementation

See `structured_triage.py` for the complete implementation. Key pieces:

### The Pydantic model

```python
class DeploymentGateDecision(BaseModel):
    gate_decision: Literal["allow", "block", "require_review"]
    risk_score: float = Field(ge=0.0, le=1.0)
    blocking_reasons: list[str]
    summary: str
```

`Field(ge=0.0, le=1.0)` is the tighter constraint that sits ABOVE what JSON Schema
enforces at the API level. The API accepts any float; Pydantic rejects values outside
`[0, 1]` at re-inflation time. This layered validation is intentional.

### The tool definition

```python
_GATE_TOOL = {
    "name": TOOL_NAME,
    "description": "...",
    "input_schema": DeploymentGateDecision.model_json_schema(),  # derived, not hand-written
}
```

The schema is derived once at module load time. When the model class changes, the
schema changes automatically - no manual schema updates required.

### The forced call

```python
resp = client.messages.create(
    model=MODEL_ID,
    tools=[_GATE_TOOL],
    tool_choice={"type": "tool", "name": TOOL_NAME},
    messages=[{"role": "user", "content": change_summary}],
)
```

`tool_choice={"type": "tool", "name": TOOL_NAME}` is the coercion mechanism. The API
will not return until Claude has produced a valid tool_use block matching the schema.
`stop_reason` will always be `"tool_use"`.

### Re-inflation

```python
tool_block = next(b for b in resp.content if b.type == "tool_use")
decision = DeploymentGateDecision(**tool_block.input)
```

This is the customs desk. If `tool_block.input` satisfies every Pydantic field constraint,
you get a typed object. If not, you get a `ValidationError` - the signal to re-prompt.

## Error-handling philosophy

The two failure modes are different in kind:

```mermaid
flowchart TD
    call["client.messages.create()"]
    call -->|"stop_reason=tool_use (expected)"| tb["Extract tool_use block"]
    call -->|"no tool_use block (unexpected)"| retry1["Re-prompt: instruct to use the tool"]
    tb --> inflate["DeploymentGateDecision(**input)"]
    inflate -->|"success"| done["Return typed decision"]
    inflate -->|"ValidationError"| retry2["Re-prompt: name the failing field"]
    retry1 --> attempt["Next attempt"]
    retry2 --> attempt
    attempt -->|"attempt <= MAX_RETRIES"| call
    attempt -->|"attempt > MAX_RETRIES"| error["raise RuntimeError"]
```

**API-level failures** (`anthropic.APIError`, network errors): not retried here - these
are infrastructure problems, not schema problems. Let them propagate and handle at the
call site (circuit breaker, alerting, etc.).

**Missing tool_use block**: rare but possible if the model is confused. Re-prompt with an
explicit instruction to use the tool.

**`pydantic.ValidationError`**: this means the tool_use input satisfied the JSON Schema
but failed Pydantic's tighter `Field(...)` constraints. Re-prompt with the specific field
name and error message. This is targeted enough that one retry usually succeeds.

**Exhausted retries**: raise `RuntimeError` with the last `ValidationError` attached.
This surfaces clearly in logs and is never a silent wrong-value propagation.

## Test suite

See `test_structured_triage.py` for the full pytest suite. Coverage:

- Happy path: allow, block, require_review decisions return typed `DeploymentGateDecision`
- Retry on bad `risk_score` (violates `Field(le=1.0)`) - succeeds on second attempt
- Retry on unsupported `gate_decision` literal - succeeds on second attempt
- Retry exhaustion raises `RuntimeError` after `MAX_RETRIES` attempts
- Corrective suffix included in the re-prompt messages array
- Missing tool_use block triggers retry

## Script execution routes

### Offline: pytest with mocked client (no API key)

```bash
cd irl_examples/03_claude_application_design/schema_coercion && pytest -v
```

### Online: pytest against the live API

The API key is loaded from the environment by `claude_code.security.secrets.load_api_key()`.
It must be exported before running:

```bash
# The key is exported globally from ~/Projects/dotfiles/secrets/secrets.sh via .zshrc
# Verify it is available:
python -c "from claude_code.security.secrets import load_api_key; load_api_key(); print('key OK')"

# Then run the live test variant:
pytest irl_examples/03_claude_application_design/schema_coercion/ -v --online
```

### Quick smoke test (single call)

```bash
PYTHONPATH=irl_examples/03_claude_application_design/schema_coercion python3 -c "
from structured_triage import evaluate_deployment

d = evaluate_deployment('Add rate-limiting middleware to the public API.')
print(d.gate_decision, d.risk_score, d.summary)
"
```
