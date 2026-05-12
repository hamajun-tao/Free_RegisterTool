from unittest import mock

import requests

from core.smsbower import (
    SmsBowerClient,
    SmsBowerError,
    SmsBowerInvalidPhoneExceptionError,
    SmsBowerWaitRetryError,
)


class _Response:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        return None


@mock.patch("core.smsbower.time.sleep", return_value=None)
def test_wait_for_code_keeps_polling_after_transient_request_error(_sleep):
    client = SmsBowerClient("demo-key")

    with mock.patch(
        "core.smsbower.requests.get",
        side_effect=[
            requests.exceptions.SSLError("EOF occurred in violation of protocol"),
            _Response("STATUS_WAIT_CODE"),
            _Response("STATUS_OK:726375"),
            _Response("ACCESS_READY"),
        ],
    ) as get:
        code = client.wait_for_code("296870886", timeout=30, interval=1)

    assert code == "726375"
    assert [call.kwargs["params"]["action"] for call in get.call_args_list] == [
        "getStatus",
        "getStatus",
        "getStatus",
        "setStatus",
    ]


@mock.patch("core.smsbower.time.sleep", return_value=None)
def test_wait_for_code_still_fails_on_cancel_status(_sleep):
    client = SmsBowerClient("demo-key")

    with mock.patch(
        "core.smsbower.requests.get",
        return_value=_Response("STATUS_CANCEL"),
    ):
        try:
            client.wait_for_code("296870886", timeout=30, interval=1)
        except SmsBowerError as exc:
            assert "取消" in str(exc) or "cancel" in str(exc).lower()
        else:
            raise AssertionError("cancelled activation should fail")


def test_get_number_classifies_wrong_exception_phone_response():
    client = SmsBowerClient("demo-key")

    with mock.patch(
        "core.smsbower.requests.get",
        return_value=_Response("WRONG_EXCEPTION_PHONE"),
    ):
        try:
            client.get_number(
                service="dr",
                country="10",
                phone_exception="84365237020",
            )
        except SmsBowerInvalidPhoneExceptionError as exc:
            assert "WRONG_EXCEPTION_PHONE" in str(exc)
        else:
            raise AssertionError("WRONG_EXCEPTION_PHONE should be classified")


@mock.patch("core.smsbower.time.sleep", return_value=None)
def test_wait_for_code_raises_retry_immediately_on_wait_retry_status(_sleep):
    client = SmsBowerClient("demo-key")

    with mock.patch(
        "core.smsbower.requests.get",
        return_value=_Response("STATUS_WAIT_RETRY"),
    ):
        try:
            client.wait_for_code("296870886", timeout=30, interval=1)
        except SmsBowerWaitRetryError as exc:
            assert "retry" in str(exc).lower()
        else:
            raise AssertionError("STATUS_WAIT_RETRY should trigger immediate retry handling")
