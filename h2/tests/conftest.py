import pytest
from h2.scenarios import make_bundle


@pytest.fixture
def bundle():
    return make_bundle()
