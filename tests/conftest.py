import pytest

from helpers import make_settings


@pytest.fixture
def settings(tmp_path):
    return make_settings(tmp_path)
