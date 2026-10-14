# tests/test_triager.py
import pytest
from unittest.mock import MagicMock, patch
import anthropic
from triager import CodeReviewTriager, BudgetExceededError
from claude_code.config.model_registry import get_model

sonnet_current = get_model("sonnet_current")

@pytest.fixture
def mock_anthropic(run_online):
    """
    Conditionally intercepts calls to anthropic.Anthropic based on flags.
    If --online is active, this fixture steps aside completely.
    """
    if run_online:
        yield None
    else:
        with patch("triager.anthropic.Anthropic") as mock_class:
            mock_instance = MagicMock()
            mock_instance.messages.create = MagicMock()
            mock_class.return_value = mock_instance
            yield mock_instance

def create_mock_message(text_content="approve", input_tokens=500, output_tokens=10):
    """Helper utility to assemble artificial token usage and text response structures."""
    mock_msg = MagicMock()
    mock_block = MagicMock()
    mock_block.text = text_content
    mock_msg.content = [mock_block]
    mock_msg.usage.input_tokens = input_tokens
    mock_msg.usage.output_tokens = output_tokens
    return mock_msg

# =====================================================================
# STANDARD FUNCTIONALITY TESTS
# =====================================================================

def test_triage_returns_correct_label(mock_anthropic, run_online):
    """1. Checks that the code correctly extracts the model's text answer."""
    if not run_online:
        mock_anthropic.messages.create.return_value = create_mock_message("request_changes")
    
    triager = CodeReviewTriager(model_key=sonnet_current.id, budget_usd=2.0)
    request_payload = {"title": "Fix bug", "description": "Null pointer on line 12"}
    
    result = triager.triage(request_payload)
    
    if not run_online:
        assert result == "request_changes"
        mock_anthropic.messages.create.assert_called_once()
    else:
        assert result in ["approve", "request_changes", "needs_more_info"]

def test_call_count_increments_correctly(mock_anthropic, run_online):
    """2. Verifies internal instance tracking attributes update correctly."""
    if not run_online:
        mock_anthropic.messages.create.return_value = create_mock_message("approve")
    
    triager = CodeReviewTriager(model_key=sonnet_current.id, budget_usd=2.0)
    request_payload = {"title": "Clean PR", "description": "Formatting updates"}
    
    assert triager.call_count == 0
    triager.triage(request_payload)
    assert triager.call_count == 1

def test_budget_exceeded_error_raised_when_budget_is_zero(mock_anthropic, run_online):
    """3. Confirms the guardrails block calls safely when out of funds."""
    triager = CodeReviewTriager(budget_usd=0.0)
    
    with pytest.raises(BudgetExceededError):
        triager.triage({"title": "Test", "description": "Test"})
        
    if not run_online:
        mock_anthropic.messages.create.assert_not_called()

# =====================================================================
# ADVANCED ERROR RESILIENCE MOCKING TESTS (OFFLINE ONLY)
# =====================================================================

def test_triage_retries_on_rate_limit_and_succeeds(mock_anthropic, run_online):
    """4. Simulates a temporary RateLimitError (429) yielding a success on retry."""
    if run_online:
        pytest.skip("Skipping offline error injection tracking during live execution mode.")
        
    rate_limit_error = anthropic.RateLimitError(
        message="Rate limit exceeded", response=MagicMock(status_code=429), body={}
    )
    success_message = create_mock_message("approve")
    mock_anthropic.messages.create.side_effect = [rate_limit_error, success_message]

    triager = CodeReviewTriager(budget_usd=1.0)
    
    with patch("triager.time.sleep") as mock_sleep:
        result = triager.triage({"title": "Fix", "description": "Docs change"})
        assert result == "approve"
        assert mock_anthropic.messages.create.call_count == 2
        mock_sleep.assert_called_once_with(2.0)

def test_triage_fails_fast_on_bad_request(mock_anthropic, run_online):
    """5. Verifies that a BadRequestError (400) bubbles up immediately without looping."""
    if run_online:
        pytest.skip("Skipping offline error injection tracking during live execution mode.")
        
    bad_request_error = anthropic.BadRequestError(
        message="Invalid schema", response=MagicMock(status_code=400), body={}
    )
    mock_anthropic.messages.create.side_effect = bad_request_error

    triager = CodeReviewTriager(budget_usd=1.0)

    with patch("triager.time.sleep") as mock_sleep:
        with pytest.raises(anthropic.BadRequestError):
            triager.triage({"title": "Bad payload", "description": "Crash testing"})
        assert mock_anthropic.messages.create.call_count == 1
        mock_sleep.assert_not_called()

def test_triage_hits_max_retries_and_returns_fallback(mock_anthropic, run_online):
    """6. Simulates persistent 503 outage triggering the default fallback boundary."""
    if run_online:
        pytest.skip("Skipping offline error injection tracking during live execution mode.")
        
    server_error = anthropic.InternalServerError(
        message="Outage", response=MagicMock(status_code=503), body={}
    )
    mock_anthropic.messages.create.side_effect = [server_error, server_error, server_error, server_error]

    triager = CodeReviewTriager(budget_usd=1.0)

    with patch("triager.time.sleep") as mock_sleep:
        result = triager.triage({"title": "PR", "description": "Code details"})
        assert result == "needs_more_info"
        assert mock_anthropic.messages.create.call_count == 4
        assert mock_sleep.call_count == 3
