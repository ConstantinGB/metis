from app.main import get_customer
from app.services import Service


def test_get_customer():
    assert get_customer


class TestService:
    def test_run(self):
        assert Service().run() == 1
