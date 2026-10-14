# tests/test_network_lanes.py
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import anthropic
import pytest
from network_lanes import (
    AsyncConcurrentDesk,
    BlockingSupportDesk,
    BudgetExceededError,
    OvernightBatchQueue,
    StreamingSupportDesk,
)


def create_mock_message(text_content="bug", input_tokens=500, output_tokens=10):
    """Helper utility to assemble artificial token usage and text response structures."""
    mock_msg = MagicMock()
    mock_block = MagicMock()
    mock_block.text = text_content
    mock_msg.content = [mock_block]
    mock_msg.usage.input_tokens = input_tokens
    mock_msg.usage.output_tokens = output_tokens
    return mock_msg


# =====================================================================
# LANE 1: SYNCHRONOUS (BLOCKING) - mirrors test_triager.py's pattern
# =====================================================================

@pytest.fixture
def mock_anthropic(run_online):
    if run_online:
        yield None
    else:
        with patch("network_lanes.anthropic.Anthropic") as mock_class:
            mock_instance = MagicMock()
            mock_instance.messages.create = MagicMock()
            mock_class.return_value = mock_instance
            yield mock_instance


def test_blocking_desk_returns_label(mock_anthropic, run_online):
    """A blocking call returns one complete classification label."""
    if not run_online:
        mock_anthropic.messages.create.return_value = create_mock_message("bug")

    desk = BlockingSupportDesk(budget_usd=2.0)
    result = desk.triage({"title": "Crash on launch", "description": "NPE on line 12"})

    if not run_online:
        assert result == "bug"
        mock_anthropic.messages.create.assert_called_once()
    else:
        assert result in ["bug", "feature", "question"]


def test_blocking_desk_budget_exceeded_blocks_call(mock_anthropic, run_online):
    """Pre-execution guardrail fires before any network call is made."""
    desk = BlockingSupportDesk(budget_usd=0.0)

    with pytest.raises(BudgetExceededError):
        desk.triage({"title": "Test", "description": "Test"})

    if not run_online:
        mock_anthropic.messages.create.assert_not_called()


def test_blocking_desk_retries_on_rate_limit(mock_anthropic, run_online):
    """A transient 429 is retried with exponential backoff and then succeeds."""
    if run_online:
        pytest.skip("Skipping offline error injection during live execution mode.")

    rate_limit_error = anthropic.RateLimitError(
        message="Rate limit exceeded", response=MagicMock(status_code=429), body={}
    )
    success = create_mock_message("feature")
    mock_anthropic.messages.create.side_effect = [rate_limit_error, success]

    desk = BlockingSupportDesk(budget_usd=1.0)
    with patch("network_lanes.time.sleep") as mock_sleep:
        result = desk.triage({"title": "Add dark mode", "description": "Please"})
        assert result == "feature"
        assert mock_anthropic.messages.create.call_count == 2
        mock_sleep.assert_called_once_with(2.0)


# =====================================================================
# LANE 2: ASYNCHRONOUS STREAMING - the socket stays open, chunks arrive incrementally
# =====================================================================

def test_streaming_desk_yields_chunks_then_records_cost(mock_anthropic, run_online):
    """Chunks are yielded before the final Message is available; cost is recorded after."""
    if run_online:
        pytest.skip("Skipping offline streaming mock during live execution mode.")

    mock_stream_cm = MagicMock()
    mock_stream_cm.__enter__.return_value.text_stream = iter(["bu", "g"])
    final_message = create_mock_message("bug", input_tokens=300, output_tokens=5)
    mock_stream_cm.__enter__.return_value.get_final_message.return_value = final_message
    mock_anthropic.messages.stream.return_value = mock_stream_cm

    desk = StreamingSupportDesk(budget_usd=1.0)
    chunks = list(desk.triage_stream({"title": "Crash", "description": "NPE"}))

    assert chunks == ["bu", "g"]
    assert desk.call_count == 1
    assert desk.spent_usd > 0.0


def test_streaming_desk_budget_exceeded_blocks_call(mock_anthropic, run_online):
    """The streaming lane enforces the same pre-execution budget guardrail as the blocking lane."""
    desk = StreamingSupportDesk(budget_usd=0.0)

    with pytest.raises(BudgetExceededError):
        next(desk.triage_stream({"title": "Test", "description": "Test"}))


# =====================================================================
# LANE 3: ASYNCHRONOUS CONCURRENT (AsyncAnthropic + asyncio.gather)
# =====================================================================

@pytest.fixture
def mock_async_anthropic(run_online):
    if run_online:
        yield None
    else:
        with patch("network_lanes.anthropic.AsyncAnthropic") as mock_class:
            mock_instance = MagicMock()
            mock_instance.messages.create = AsyncMock()
            mock_class.return_value = mock_instance
            yield mock_instance


def test_async_desk_classifies_many_concurrently_in_input_order(mock_async_anthropic, run_online):
    """asyncio.gather preserves input order even though calls complete out of order."""
    if run_online:
        pytest.skip("Skipping offline async mock during live execution mode.")

    mock_async_anthropic.messages.create.side_effect = [
        create_mock_message("bug"),
        create_mock_message("feature"),
        create_mock_message("question"),
    ]

    desk = AsyncConcurrentDesk()
    tickets = [
        {"title": "Crash", "description": "NPE"},
        {"title": "Dark mode", "description": "Please"},
        {"title": "How do I...", "description": "Docs unclear"},
    ]
    results = asyncio.run(desk.classify_many(tickets))

    assert results == ["bug", "feature", "question"]
    assert mock_async_anthropic.messages.create.call_count == 3


def test_async_desk_budget_exceeded_blocks_call(mock_async_anthropic, run_online):
    """The async-concurrent lane enforces the same pre-execution budget guardrail as the other realtime lanes."""
    if run_online:
        pytest.skip("Skipping offline async mock during live execution mode.")

    desk = AsyncConcurrentDesk(budget_usd=0.0)
    tickets = [{"title": "Crash", "description": "NPE"}]

    with pytest.raises(BudgetExceededError):
        asyncio.run(desk.classify_many(tickets))

    mock_async_anthropic.messages.create.assert_not_called()


def test_async_desk_one_failure_does_not_discard_the_rest(mock_async_anthropic, run_online):
    """return_exceptions=True means one bad ticket surfaces as an exception at its own position."""
    if run_online:
        pytest.skip("Skipping offline async mock during live execution mode.")

    boom = anthropic.APIStatusError(
        message="server error", response=MagicMock(status_code=500), body={}
    )
    mock_async_anthropic.messages.create.side_effect = [
        create_mock_message("bug"),
        boom,
        create_mock_message("question"),
    ]

    desk = AsyncConcurrentDesk()
    tickets = [
        {"title": "Crash", "description": "NPE"},
        {"title": "Dark mode", "description": "Please"},
        {"title": "How do I...", "description": "Docs unclear"},
    ]
    results = asyncio.run(desk.classify_many(tickets))

    assert results[0] == "bug"
    assert results[1] is boom
    assert results[2] == "question"


# =====================================================================
# LANE 4: ASYNCHRONOUS BATCH (background queue)
# =====================================================================

@pytest.fixture
def mock_batch_anthropic(run_online):
    if run_online:
        yield None
    else:
        with patch("network_lanes.anthropic.Anthropic") as mock_class:
            mock_instance = MagicMock()
            mock_class.return_value = mock_instance
            yield mock_instance


def test_batch_queue_submit_returns_id_immediately(mock_batch_anthropic, run_online):
    """submit() returns a batch ID without waiting for any generation to finish."""
    if run_online:
        pytest.skip("Skipping offline batch mock during live execution mode.")

    mock_batch_anthropic.messages.batches.create.return_value = MagicMock(id="batch_abc123")

    queue = OvernightBatchQueue()
    batch_id = queue.submit([{"title": "Crash", "description": "NPE"}])

    assert batch_id == "batch_abc123"
    mock_batch_anthropic.messages.batches.create.assert_called_once()


def test_batch_queue_poll_until_complete_returns_on_ended_status(mock_batch_anthropic, run_online):
    """poll_until_complete() loops while in_progress and returns once status is 'ended'."""
    if run_online:
        pytest.skip("Skipping offline batch mock during live execution mode.")

    in_progress = MagicMock(processing_status="in_progress")
    ended = MagicMock(processing_status="ended")
    mock_batch_anthropic.messages.batches.retrieve.side_effect = [in_progress, ended]

    queue = OvernightBatchQueue()
    with patch("network_lanes.time.sleep") as mock_sleep:
        result = queue.poll_until_complete("batch_abc123", poll_interval_seconds=5)
        assert result.processing_status == "ended"
        assert mock_batch_anthropic.messages.batches.retrieve.call_count == 2
        mock_sleep.assert_called_once_with(5)


def test_batch_queue_poll_until_complete_times_out(mock_batch_anthropic, run_online):
    """poll_until_complete() raises TimeoutError if the batch never reaches 'ended'."""
    if run_online:
        pytest.skip("Skipping offline batch mock during live execution mode.")

    mock_batch_anthropic.messages.batches.retrieve.return_value = MagicMock(processing_status="in_progress")

    queue = OvernightBatchQueue()
    with patch("network_lanes.time.sleep"), patch("network_lanes.time.monotonic", side_effect=[0, 0, 100]):
        with pytest.raises(TimeoutError):
            queue.poll_until_complete("batch_abc123", timeout_seconds=60, poll_interval_seconds=5)
