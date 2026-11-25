import pytest


@pytest.fixture(autouse=True)
def reset_litellm_cache():
    """Ensure LiteLLM global cache is clean between tests."""
    import litellm

    litellm.cache = None
    yield
    litellm.cache = None
