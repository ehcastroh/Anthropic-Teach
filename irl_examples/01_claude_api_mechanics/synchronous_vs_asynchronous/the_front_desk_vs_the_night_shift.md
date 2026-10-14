# Synchronous vs. Asynchronous Processing

## The Metaphor: The Front Desk vs. The Night Shift

Think of a **blocking (synchronous) call** as walking up to a hotel front desk and asking a question. You stand there. The clerk looks it up. You get a complete answer. You leave. The whole time you were waiting, you could do nothing else - your entire presence was occupied by that one exchange.

**Streaming** is that same clerk, except instead of vanishing into the back office and returning once with the full answer, they narrate as they go: "Checking... room's on the third floor... elevator's to your left... breakfast starts at seven." You start acting on the answer before the clerk finishes the sentence. You were still standing there the whole time - streaming does not free you to leave - but you were not sitting in silence waiting for one giant finished response.

**Batch processing** is dropping a box of 500 claim forms off with the night shift before you go home. You do not wait at the counter. You do not get an immediate answer. You get a claim ticket, you leave, and tomorrow morning you pick up the finished stack. The night shift works through your box at its own pace, cheaper than daytime processing, while you were doing something else entirely.

## The Biggest Takeaway

> The axis that matters is not "fast vs. slow." It is **who is watching, and how much is there.**

- Someone watching one thing right now → **stream it**, so they see progress instead of a frozen screen.
- Nobody watching, but there's a pile of independent, deferrable work → **batch it**, and pay roughly half the realtime price for the privilege of not being in a hurry.
- Everything else - a tool-use loop, a short classification your code needs an answer to before it can take its next step → **block on it**. Blocking is not a mistake; it is the correct default when there is nothing useful to do while waiting and nobody to show partial output to.

> **Note:** The following code is for reference. All executable files are in `irl_examples/01_claude_api_mechanics/synchronous_vs_asynchronous`.
>
> Jump to [Part 5](#part-5-script-execution-routes) for instructions on how to use the test suite.

---

## Four Network Lanes: A Support-Desk Triage Pipeline

This guide builds one small system - a support-ticket triage pipeline - four different ways, each one exercising a different network topology against the same `anthropic` Python SDK. It covers environment setup, all four implementations, failure-handling guidance that is specific to each lane, and an offline/online hybrid test suite.

---

### Part 1: The Three-Step Environment Setup

#### Step 1: Create a Clean Sandbox Directory
```bash
mkdir -p network_lanes_sandbox/tests
cd network_lanes_sandbox
```

#### Step 2: Set Up and Activate a Python Virtual Environment
```bash
python3 -m venv .venv
source .venv/bin/activate
```

#### Step 3: Install Required Dependencies
```bash
pip install anthropic pytest
export ANTHROPIC_API_KEY="your-key-here"
```

---

### Part 2: Core Implementation (Four Classes, One Ticket Payload)

All four classes below triage the same shape of input - `{"title": ..., "description": ...}` - and differ only in how they talk to the network.

```python
# network_lanes.py
import asyncio
import time

import anthropic


class BudgetExceededError(Exception):
    """Custom exception raised when an instance exhausts its allocated budget."""
    pass


class BlockingSupportDesk:
    """
    Synchronous (blocking) lane: one ticket in, one complete classification out.
    The calling thread sits idle for the full round trip of each call.
    """

    def __init__(self, model_key: str = "claude-haiku-4-5-20251001", budget_usd: float = 1.0):
        self.model_key = model_key
        self.budget_usd = budget_usd
        self.spent_usd = 0.0
        self.call_count = 0
        self.client = anthropic.Anthropic()
        self.input_rate = 1.00 / 1_000_000
        self.output_rate = 5.00 / 1_000_000

    def triage(self, ticket: dict) -> str:
        if self.spent_usd >= self.budget_usd or self.budget_usd <= 0:
            raise BudgetExceededError("Operational budget limit hit.")

        prompt = f"Ticket Title: {ticket.get('title', '')}\nDescription: {ticket.get('description', '')}"
        max_retries = 3
        base_delay = 2.0

        for attempt in range(max_retries + 1):
            try:
                message = self.client.messages.create(
                    model=self.model_key,
                    max_tokens=10,
                    system=(
                        "Classify the ticket as EXACTLY one of: bug, feature, question. "
                        "Reply with only the lowercase label."
                    ),
                    messages=[{"role": "user", "content": prompt}],
                )
                in_tokens = message.usage.input_tokens
                out_tokens = message.usage.output_tokens
                self.spent_usd += (in_tokens * self.input_rate) + (out_tokens * self.output_rate)
                self.call_count += 1
                return "".join(b.text for b in message.content if hasattr(b, "text")).strip().lower()

            except (anthropic.BadRequestError, anthropic.AuthenticationError):
                raise  # Fail fast on client bugs

            except (anthropic.RateLimitError, anthropic.InternalServerError, anthropic.APIStatusError) as e:
                status_code = getattr(e, "status_code", None)
                if status_code and status_code != 429 and status_code < 500:
                    raise
                if attempt == max_retries:
                    raise
                time.sleep(base_delay * (2 ** attempt))

        raise RuntimeError("unreachable")


class StreamingSupportDesk:
    """
    Asynchronous streaming lane: one long-lived open socket, incremental text
    chunk fragments arrive as the model generates them. The caller assembles
    the final answer itself; the SDK hands back a complete Message only when
    the socket closes.
    """

    def __init__(self, model_key: str = "claude-haiku-4-5-20251001", budget_usd: float = 1.0):
        self.model_key = model_key
        self.budget_usd = budget_usd
        self.spent_usd = 0.0
        self.call_count = 0
        self.client = anthropic.Anthropic()
        self.input_rate = 1.00 / 1_000_000
        self.output_rate = 5.00 / 1_000_000

    def triage_stream(self, ticket: dict):
        """Yields text chunks as they arrive, then records cost on stream close."""
        if self.spent_usd >= self.budget_usd or self.budget_usd <= 0:
            raise BudgetExceededError("Operational budget limit hit.")

        prompt = f"Ticket Title: {ticket.get('title', '')}\nDescription: {ticket.get('description', '')}"

        with self.client.messages.stream(
            model=self.model_key,
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            yield from stream.text_stream
            final = stream.get_final_message()

        in_tokens = final.usage.input_tokens
        out_tokens = final.usage.output_tokens
        self.spent_usd += (in_tokens * self.input_rate) + (out_tokens * self.output_rate)
        self.call_count += 1


class AsyncConcurrentDesk:
    """
    Asynchronous concurrent lane: AsyncAnthropic plus an asyncio event loop
    fire many realtime requests at once instead of one after another. Each
    call still blocks that one coroutine, but the event loop interleaves
    many coroutines so wall-clock time for the whole batch collapses toward
    the slowest single call instead of the sum of every call.
    """

    def __init__(self, model_key: str = "claude-haiku-4-5-20251001", budget_usd: float = 1.0):
        self.model_key = model_key
        self.budget_usd = budget_usd
        self.spent_usd = 0.0
        self.call_count = 0
        self.client = anthropic.AsyncAnthropic()
        self.input_rate = 1.00 / 1_000_000
        self.output_rate = 5.00 / 1_000_000

    async def aclose(self) -> None:
        await self.client.close()

    async def _classify_one(self, ticket: dict) -> str:
        prompt = f"Ticket Title: {ticket.get('title', '')}\nDescription: {ticket.get('description', '')}"
        message = await self.client.messages.create(
            model=self.model_key,
            max_tokens=10,
            system="Classify the ticket as EXACTLY one of: bug, feature, question.",
            messages=[{"role": "user", "content": prompt}],
        )
        in_tokens = message.usage.input_tokens
        out_tokens = message.usage.output_tokens
        self.spent_usd += (in_tokens * self.input_rate) + (out_tokens * self.output_rate)
        self.call_count += 1
        return "".join(b.text for b in message.content if hasattr(b, "text")).strip().lower()

    async def classify_many(self, tickets: list[dict]) -> list:
        """
        Fire every ticket concurrently; return results in the same order as input.

        Budget is checked once, up front, against the whole batch - not per ticket -
        because every coroutine is already scheduled by the time any one of them
        would discover the budget is gone. `return_exceptions=True` means one
        ticket's failure surfaces as an exception instance at its own position
        instead of canceling every other in-flight coroutine.
        """
        if self.spent_usd >= self.budget_usd or self.budget_usd <= 0:
            raise BudgetExceededError("Operational budget limit hit.")

        return await asyncio.gather(
            *(self._classify_one(t) for t in tickets), return_exceptions=True
        )


class OvernightBatchQueue:
    """
    Asynchronous background-queue lane: submit a whole file of requests and
    get a batch ID back in milliseconds. The work happens off-process, on a
    remote queue, over up to a 24-hour window, at roughly half the per-token
    cost of the realtime lanes above.
    """

    def __init__(self):
        self.client = anthropic.Anthropic()

    def submit(self, tickets: list[dict], model_key: str = "claude-haiku-4-5-20251001") -> str:
        requests = []
        for i, ticket in enumerate(tickets):
            prompt = f"Ticket Title: {ticket.get('title', '')}\nDescription: {ticket.get('description', '')}"
            requests.append({
                "custom_id": f"ticket-{i}",
                "params": {
                    "model": model_key,
                    "max_tokens": 10,
                    "system": "Classify the ticket as EXACTLY one of: bug, feature, question.",
                    "messages": [{"role": "user", "content": prompt}],
                },
            })
        batch = self.client.messages.batches.create(requests=requests)
        return batch.id

    def poll_until_complete(self, batch_id: str, timeout_seconds: int = 3600, poll_interval_seconds: int = 30):
        start = time.monotonic()
        while True:
            batch = self.client.messages.batches.retrieve(batch_id)
            if batch.processing_status == "ended":
                return batch
            if time.monotonic() - start >= timeout_seconds:
                raise TimeoutError(f"Batch {batch_id!r} did not complete within {timeout_seconds}s")
            time.sleep(poll_interval_seconds)

    def results(self, batch_id: str):
        for item in self.client.messages.batches.results(batch_id):
            yield {"custom_id": item.custom_id, "result": item.result}
```

---

### Part 3: Recommended Defense Against Failure Guideline

#### 1. Blocking lane: Transient Failures (Apply Exponential Backoff)
A `RateLimitError` (429) or a 5xx server error is usually temporary. Retry with exponential backoff - `BlockingSupportDesk` doubles its delay on every attempt (2s, 4s, 8s) up to a fixed retry ceiling.

#### 2. Streaming lane: Mid-Stream Failures Need a Different Posture
A stream can fail *after* you've already yielded several chunks to the caller. There is no clean way to "retry" a partially-rendered response in a chat UI - you either discard the partial output and restart the whole call, or you surface a visible error and let the human decide. Do not silently retry a failed stream and re-emit chunks as if nothing happened; the caller may have already rendered the first attempt's partial text.

#### 3. Async-concurrent lane: One Failure Should Not Sink the Whole Batch
`asyncio.gather(*coros)` defaults to canceling every other in-flight coroutine the moment any one of them raises. `classify_many` passes `return_exceptions=True` for exactly this reason: for an unattended fan-out of independent classifications, one bad ticket should not discard 49 good ones. The caller is responsible for walking the returned list and checking each position with `isinstance(result, Exception)` before trusting it as a label.

#### 4. Batch lane: Fatal Failures at Submit Time Must Fail Fast
A `BadRequestError` (400) at submit time means the request shape itself is wrong for every item in the batch - retrying will not help. Let it bubble up immediately. A `TimeoutError` from `poll_until_complete` means the batch is still processing past your patience threshold, not that it failed - the batch may still complete on Anthropic's side even after your poller gives up.

---

### Part 4: Advanced Test Suite (Hybrid Offline/Online Routing)

#### Step 1: Create `tests/conftest.py`
```python
# tests/conftest.py
import pytest

def pytest_addoption(parser):
    parser.addoption(
        "--online",
        action="store_true",
        default=False,
        help="Run tests using live, billable Anthropic API network transactions"
    )

@pytest.fixture
def run_online(request):
    return request.config.getoption("--online")
```

#### Step 2: Mock Each Lane According to Its Own Shape
- **Blocking:** patch `anthropic.Anthropic`, set `messages.create.return_value` to a fabricated `Message`.
- **Streaming:** patch the context manager returned by `messages.stream(...)` so `__enter__().text_stream` is an iterator and `__enter__().get_final_message()` returns a fabricated `Message`.
- **Async-concurrent:** patch `anthropic.AsyncAnthropic`, set `messages.create` to an `AsyncMock` with a `side_effect` list - one fabricated `Message` per concurrent call.
- **Batch:** patch `anthropic.Anthropic`, mock `messages.batches.create`, `.retrieve` (with a `side_effect` sequence simulating `in_progress` → `ended`), and `.results`.

See `test_network_lanes.py` in this directory for the full suite - 11 tests, all four lanes, zero dollars spent when run offline.

---

### Part 5: Script Execution Routes

#### Activate/Deactivate Virtual Environment
```bash
# create sandbox
python3 -m venv .venv && source .venv/bin/activate

# Exit the virtual environment safely
deactivate

# Obliterate the virtual environment directory completely from your disk
rm -rf .venv
```

#### Route A: Standard Fast Verification (Offline & Free)
```bash
# Run all tests with standard progress output
pytest

# Run with the Verbose (-v) flag to see each unique test case
pytest -v

# Run only the streaming-lane tests
pytest -k streaming
```

#### Route B: Live API Verification (Billable Integration Routing)
```bash
# Export your live API credential token to the active shell context
export ANTHROPIC_API_KEY="sk-ant-..."

# Force pytest to skip mocks and hit the live endpoints
pytest --online

# Target one specific test case to run against the live Claude server
pytest --online -k test_blocking_desk_returns_label
```

#### Useful Diagnostic & Debugging Flags
```bash
# Show local variable print() updates directly on the terminal during execution
pytest -s

# Drop into a live interactive PDB session if a test failure occurs
pytest --pdb

# Stop the entire test suite immediately upon hitting the first failure
pytest -x
```

---

### Part 6: Easy-to-Digest API Best Practices

#### Choosing a Lane
- A human is watching one answer appear in real time → **stream**.
- Many independent, deferrable jobs and nobody is waiting → **batch** (roughly half the realtime per-token cost).
- A short call your code needs an answer to before its next step, and no human is watching partial output → **block**.
- Many independent, realtime-urgent calls at once, with no human watching any single one → **async-concurrent** (`AsyncAnthropic` + `asyncio.gather`) - this is the lane that is easiest to confuse with batch, but it still pays realtime pricing and still occupies realtime serving capacity; it only buys you wall-clock concurrency, not the cost discount.

#### Budget and Token Cost Management
The three realtime lanes - blocking, streaming, and async-concurrent - each track `spent_usd` and enforce a pre-call guardrail via `BudgetExceededError`, because each one spends tokens directly against your account the moment it fires a request. The streaming lane is the one place this matters most in practice: once a stream has started, tokens are already being generated and billed even if the caller abandons the connection early - the budget check has to happen *before* `messages.stream(...)` opens the socket, not after. `AsyncConcurrentDesk` checks the budget once, before the whole `asyncio.gather` fan-out, since every coroutine is already scheduled by the time any one of them would discover the budget is gone mid-batch.

`OvernightBatchQueue` has no local `budget_usd` - its cost is settled server-side on the batch itself, not per call from this process, so there is nothing for a client-side pre-call guardrail to check against.

#### Recommended Defense Against Failure Guideline
See Part 3 above - each lane's failure mode is shaped by how and when the caller finds out something went wrong, which is exactly the axis that separates synchronous from asynchronous processing in the first place.
