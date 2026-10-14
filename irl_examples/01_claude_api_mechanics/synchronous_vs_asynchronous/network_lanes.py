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
