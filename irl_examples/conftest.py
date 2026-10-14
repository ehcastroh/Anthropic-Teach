"""Shared pytest configuration for every irl_examples/ project.

Registers the --online flag once for the whole tree. Without it, tests run
offline against mocks and need no API key. With it, tests call the live,
billable Anthropic API.
"""

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--online",
        action="store_true",
        default=False,
        help="Run tests that call the live Anthropic API (billable; needs ANTHROPIC_API_KEY).",
    )


@pytest.fixture
def run_online(request: pytest.FixtureRequest) -> bool:
    """True when --online is passed at the command line; False otherwise."""
    return request.config.getoption("--online")


@pytest.fixture
def online(run_online: bool) -> bool:
    """Alias of run_online, kept for the schema_coercion tests."""
    return run_online
