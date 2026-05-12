import unittest
import threading
from unittest.mock import patch
import random

from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.tasks as tasks_module
from api.tasks import router as tasks_router
from api.tasks import RegisterTaskRequest, _create_task_record, _log, _run_register, _task_store, _tasks, _tasks_lock
from core.base_mailbox import BaseMailbox, MailboxAccount
from core.base_platform import Account, BasePlatform


class _FakeMailbox(BaseMailbox):
    def get_email(self) -> MailboxAccount:
        return MailboxAccount(email="demo@example.com")

    def get_current_ids(self, account: MailboxAccount) -> set:
        return set()

    def wait_for_code(
        self,
        account: MailboxAccount,
        keyword: str = "",
        timeout: int = 120,
        before_ids: set = None,
        code_pattern: str = None,
        **kwargs,
    ) -> str:
        return "123456"


class _FakePlatform(BasePlatform):
    name = "fake"
    display_name = "Fake"

    def __init__(self, config=None, mailbox=None):
        super().__init__(config)
        self.mailbox = mailbox

    def register(self, email: str, password: str = None) -> Account:
        account = self.mailbox.get_email()
        self.mailbox.wait_for_code(account, timeout=1)
        return Account(
            platform="fake",
            email=account.email,
            password=password or "pw",
        )

    def check_valid(self, account: Account) -> bool:
        return True


class RegisterTaskControlFlowTests(unittest.TestCase):
    def _build_request(self):
        return RegisterTaskRequest(
            platform="fake",
            count=1,
            concurrency=1,
            proxy="http://proxy.local:8080",
            extra={"mail_provider": "fake"},
        )

    def _run_with_control(self, task_id: str, *, stop: bool = False, skip: bool = False):
        req = self._build_request()
        _create_task_record(task_id, req, "manual", None)
        if stop:
            _task_store.request_stop(task_id)
        if skip:
            _task_store.request_skip_current(task_id)

        with (
            patch("core.registry.get", return_value=_FakePlatform),
            patch("core.base_mailbox.create_mailbox", return_value=_FakeMailbox()),
            patch("core.db.save_account", side_effect=lambda account: account),
            patch("api.tasks._save_task_log"),
        ):
            _run_register(task_id, req)

        return _task_store.snapshot(task_id)

    def test_skip_current_marks_attempt_as_skipped(self):
        snapshot = self._run_with_control("task-control-skip", skip=True)

        self.assertEqual(snapshot["status"], "done")
        self.assertEqual(snapshot["success"], 0)
        self.assertEqual(snapshot["skipped"], 1)
        self.assertEqual(snapshot["errors"], [])

    def test_successful_run_records_worker_stage_state(self):
        snapshot = self._run_with_control("task-worker-state-success")

        self.assertEqual(snapshot["status"], "done")
        self.assertEqual(snapshot["success"], 1)
        self.assertIn("worker_states", snapshot)
        self.assertEqual(len(snapshot["worker_states"]), 1)
        worker = snapshot["worker_states"][0]
        self.assertEqual(worker["index"], 1)
        self.assertEqual(worker["state"], "success")
        self.assertEqual(worker["stage"], "done")
        self.assertEqual(worker["email"], "demo@example.com")
        self.assertTrue(worker["message"])

    def test_stop_marks_task_as_stopped(self):
        snapshot = self._run_with_control("task-control-stop", stop=True)

        self.assertEqual(snapshot["status"], "stopped")
        self.assertEqual(snapshot["success"], 0)
        self.assertEqual(snapshot["skipped"], 0)
        self.assertEqual(snapshot["errors"], [])

    def test_log_falls_back_when_stdout_rejects_unicode(self):
        calls = []

        def fake_print(value):
            calls.append(value)
            if len(calls) == 1:
                raise UnicodeEncodeError("gbk", value, 0, 1, "illegal multibyte sequence")

        with patch("api.tasks.print", side_effect=fake_print):
            _log("missing-task", "✅ 注册流程完成")

        self.assertEqual(len(calls), 2)
        self.assertIn("?", calls[1])

    def test_task_logs_are_capped_with_absolute_offset(self):
        task_id = "task-capped-logs"
        req = self._build_request()
        _create_task_record(task_id, req, "manual", None)

        original_limit = tasks_module.TASK_LOG_LIMIT
        try:
            tasks_module.TASK_LOG_LIMIT = 3
            for i in range(5):
                _log(task_id, f"line-{i}")
            with _tasks_lock:
                _tasks[task_id]["status"] = "done"
        finally:
            tasks_module.TASK_LOG_LIMIT = original_limit

        snapshot = _task_store.snapshot(task_id)
        self.assertEqual(snapshot["log_offset"], 2)
        self.assertEqual(snapshot["log_total"], 5)
        self.assertEqual(len(snapshot["logs"]), 3)
        self.assertTrue(snapshot["logs"][0].endswith("line-2"))
        self.assertTrue(snapshot["logs"][-1].endswith("line-4"))

    def test_task_detail_can_omit_logs_for_lightweight_polling(self):
        task_id = "task-detail-without-logs"
        req = self._build_request()
        _create_task_record(task_id, req, "manual", None)
        _log(task_id, "line-0")

        app = FastAPI()
        app.include_router(tasks_router)
        client = TestClient(app)

        response = client.get(f"/tasks/{task_id}?include_logs=0")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertNotIn("logs", body)
        self.assertEqual(body["log_total"], 1)
        self.assertIn("worker_states", body)
        self.assertIn("control", body)

    def test_log_stream_uses_absolute_since_after_log_trimming(self):
        task_id = "task-stream-capped-logs"
        req = self._build_request()
        _create_task_record(task_id, req, "manual", None)

        original_limit = tasks_module.TASK_LOG_LIMIT
        try:
            tasks_module.TASK_LOG_LIMIT = 3
            for i in range(5):
                _log(task_id, f"line-{i}")
            with _tasks_lock:
                _tasks[task_id]["status"] = "done"
        finally:
            tasks_module.TASK_LOG_LIMIT = original_limit

        app = FastAPI()
        app.include_router(tasks_router)
        client = TestClient(app)
        with client.stream("GET", f"/tasks/{task_id}/logs/stream?since=2") as response:
            self.assertEqual(response.status_code, 200)
            body = "".join(response.iter_text())
        self.assertIn('"index": 2', body)
        self.assertIn("line-2", body)
        self.assertIn('"index": 4', body)
        self.assertIn("line-4", body)
        self.assertIn('"done": true', body)

    def test_register_task_buys_next_mailbox_only_after_worker_finishes(self):
        task_id = "task-bounded-mailbox-pipeline"
        req = RegisterTaskRequest(
            platform="fake",
            count=3,
            concurrency=1,
            stagger_seconds=0,
            proxy="http://proxy.local:8080",
            extra={"mail_provider": "fake"},
        )
        _create_task_record(task_id, req, "manual", None)

        events = []
        events_lock = threading.Lock()
        first_register_started = threading.Event()
        allow_first_register_to_finish = threading.Event()
        test_case = self

        class _BlockingMailbox(BaseMailbox):
            def __init__(self, email: str):
                self.email = email

            def get_email(self) -> MailboxAccount:
                return MailboxAccount(email=self.email)

            def get_current_ids(self, account: MailboxAccount) -> set:
                return set()

            def wait_for_code(self, *args, **kwargs) -> str:
                return "123456"

        class _BlockingPlatform(BasePlatform):
            name = "fake"
            display_name = "Fake"

            def __init__(self, config=None, mailbox=None):
                super().__init__(config)
                self.mailbox = mailbox

            def register(self, email: str, password: str = None) -> Account:
                mailbox_account = self.mailbox.get_email()
                with events_lock:
                    events.append(f"register-start:{mailbox_account.email}")
                if mailbox_account.email == "user1@example.com":
                    first_register_started.set()
                    test_case.assertTrue(allow_first_register_to_finish.wait(timeout=2))
                return Account(
                    platform="fake",
                    email=mailbox_account.email,
                    password=password or "pw",
                )

            def check_valid(self, account: Account) -> bool:
                return True

        def create_mailbox(provider, extra, proxy):
            with events_lock:
                email = f"user{len([e for e in events if e.startswith('create:')]) + 1}@example.com"
                events.append(f"create:{email}")
            return _BlockingMailbox(email)

        with (
            patch("core.registry.get", return_value=_BlockingPlatform),
            patch("core.base_mailbox.create_mailbox", side_effect=create_mailbox),
            patch("core.db.save_account", side_effect=lambda account: account),
            patch("api.tasks._save_task_log"),
            patch("api.tasks.time.sleep", return_value=None),
        ):
            runner = threading.Thread(target=_run_register, args=(task_id, req))
            runner.start()

            self.assertTrue(first_register_started.wait(timeout=2))
            # Give the producer loop a chance to over-buy if it is not bounded.
            self.assertFalse(allow_first_register_to_finish.wait(timeout=0.1))
            with events_lock:
                created_before_first_finished = [
                    event for event in events if event.startswith("create:")
                ]

            allow_first_register_to_finish.set()
            runner.join(timeout=5)

        self.assertFalse(runner.is_alive())
        self.assertEqual(created_before_first_finished, ["create:user1@example.com"])
        snapshot = _task_store.snapshot(task_id)
        self.assertEqual(snapshot["status"], "done")
        self.assertEqual(snapshot["success"], 3)

    def test_stop_interrupts_active_register_attempt_at_checkpoint(self):
        task_id = "task-stop-active-attempt"
        req = RegisterTaskRequest(
            platform="fake",
            count=1,
            concurrency=1,
            stagger_seconds=0,
            proxy="http://proxy.local:8080",
            extra={"mail_provider": "fake"},
        )
        _create_task_record(task_id, req, "manual", None)
        register_started = threading.Event()
        stop_requested = threading.Event()

        class _InterruptiblePlatform(BasePlatform):
            name = "fake"
            display_name = "Fake"

            def __init__(self, config=None, mailbox=None):
                super().__init__(config)
                self.mailbox = mailbox

            def register(self, email: str, password: str = None) -> Account:
                register_started.set()
                assert getattr(self, "_task_control", None) is not None
                stop_requested.wait(timeout=2)
                self._task_control.checkpoint()
                raise AssertionError("checkpoint should have raised StopTaskRequested")

            def check_valid(self, account: Account) -> bool:
                return True

        with (
            patch("core.registry.get", return_value=_InterruptiblePlatform),
            patch("core.base_mailbox.create_mailbox", return_value=_FakeMailbox()),
            patch("core.db.save_account", side_effect=lambda account: account),
            patch("api.tasks._save_task_log"),
            patch("api.tasks.time.sleep", return_value=None),
        ):
            runner = threading.Thread(target=_run_register, args=(task_id, req))
            runner.start()
            self.assertTrue(register_started.wait(timeout=2))
            _task_store.request_stop(task_id)
            stop_requested.set()
            runner.join(timeout=5)

        self.assertFalse(runner.is_alive())
        snapshot = _task_store.snapshot(task_id)
        self.assertEqual(snapshot["status"], "stopped")
        self.assertEqual(snapshot["success"], 0)
        self.assertEqual(snapshot["errors"], [])

    def test_pause_blocks_new_work_until_resume(self):
        task_id = "task-pause-before-mailbox"
        req = RegisterTaskRequest(
            platform="fake",
            count=2,
            concurrency=1,
            stagger_seconds=0,
            proxy="http://proxy.local:8080",
            extra={"mail_provider": "fake"},
        )
        _create_task_record(task_id, req, "manual", None)
        _task_store.request_pause(task_id)

        created_mailboxes = []

        def create_mailbox(provider, extra, proxy):
            created_mailboxes.append(len(created_mailboxes) + 1)
            return _FakeMailbox()

        class _QuickPlatform(BasePlatform):
            name = "fake"
            display_name = "Fake"

            def __init__(self, config=None, mailbox=None):
                super().__init__(config)
                self.mailbox = mailbox

            def register(self, email: str, password: str = None) -> Account:
                account = self.mailbox.get_email()
                return Account(
                    platform="fake",
                    email=account.email,
                    password=password or "pw",
                )

            def check_valid(self, account: Account) -> bool:
                return True

        with (
            patch("core.registry.get", return_value=_QuickPlatform),
            patch("core.base_mailbox.create_mailbox", side_effect=create_mailbox),
            patch("core.db.save_account", side_effect=lambda account: account),
            patch("api.tasks._save_task_log"),
        ):
            runner = threading.Thread(target=_run_register, args=(task_id, req))
            runner.start()
            self.assertFalse(runner.join(timeout=0.2))
            self.assertEqual(created_mailboxes, [])
            self.assertTrue(_task_store.snapshot(task_id)["control"]["paused"])

            _task_store.request_resume(task_id)
            runner.join(timeout=5)

        self.assertFalse(runner.is_alive())
        self.assertEqual(created_mailboxes, [1, 2])
        snapshot = _task_store.snapshot(task_id)
        self.assertEqual(snapshot["status"], "done")
        self.assertFalse(snapshot["control"]["paused"])
        self.assertEqual(snapshot["success"], 2)

    def test_register_task_does_not_cap_worker_count_below_requested_concurrency(self):
        task_id = "task-worker-count-follows-request"
        req = RegisterTaskRequest(
            platform="fake",
            count=6,
            concurrency=6,
            stagger_seconds=0,
            proxy="http://proxy.local:8080",
            extra={"mail_provider": "fake"},
        )
        _create_task_record(task_id, req, "manual", None)
        captured_worker_counts = []

        class _CapturingExecutor:
            def __init__(self, max_workers):
                captured_worker_counts.append(max_workers)

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def submit(self, *args, **kwargs):
                raise AssertionError("mailbox creation is forced to fail before submit")

        with (
            patch("core.registry.get", return_value=_FakePlatform),
            patch("core.base_mailbox.create_mailbox", side_effect=RuntimeError("mailbox down")),
            patch("concurrent.futures.ThreadPoolExecutor", _CapturingExecutor),
            patch("api.tasks._save_task_log"),
            patch("api.tasks.time.sleep", return_value=None),
        ):
            _run_register(task_id, req)

        self.assertEqual(captured_worker_counts, [6])

    def test_parallel_mail_mix_uses_shuffled_round_robin_distribution(self):
        task_id = "task-parallel-mail-mix"
        req = RegisterTaskRequest(
            platform="fake",
            count=5,
            concurrency=1,
            stagger_seconds=0,
            proxy="http://proxy.local:8080",
            extra={
                "mail_provider": "luckmail",
                "mail_provider_mix": ["luckmail", "cfworker", "mail2925"],
            },
        )
        _create_task_record(task_id, req, "manual", None)

        created_providers = []

        class _ProviderMailbox(_FakeMailbox):
            def __init__(self, provider: str):
                self.provider = provider

            def get_email(self) -> MailboxAccount:
                return MailboxAccount(email=f"{self.provider}@example.com")

        class _QuickPlatform(BasePlatform):
            name = "fake"
            display_name = "Fake"

            def __init__(self, config=None, mailbox=None):
                super().__init__(config)
                self.mailbox = mailbox

            def register(self, email: str, password: str = None) -> Account:
                account = self.mailbox.get_email()
                return Account(
                    platform="fake",
                    email=account.email,
                    password=password or "pw",
                )

            def check_valid(self, account: Account) -> bool:
                return True

        def create_mailbox(provider, extra, proxy):
            created_providers.append(provider)
            return _ProviderMailbox(provider)

        with (
            patch("core.registry.get", return_value=_QuickPlatform),
            patch("core.base_mailbox.create_mailbox", side_effect=create_mailbox),
            patch("core.db.save_account", side_effect=lambda account: account),
            patch("api.tasks._save_task_log"),
            patch("api.tasks.random.shuffle", side_effect=lambda items: items.__setitem__(slice(None), ["cfworker", "mail2925", "luckmail"])),
        ):
            _run_register(task_id, req)

        self.assertEqual(
            created_providers,
            ["cfworker", "mail2925", "luckmail", "cfworker", "mail2925"],
        )
        snapshot = _task_store.snapshot(task_id)
        self.assertEqual(snapshot["status"], "done")
        self.assertEqual(snapshot["success"], 5)


class RegisterTaskControlApiTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(tasks_router)
        self.client = TestClient(app)

    def _create_pending_task(self, task_id: str):
        req = RegisterTaskRequest(
            platform="fake",
            count=1,
            concurrency=1,
            extra={"mail_provider": "fake"},
        )
        _create_task_record(task_id, req, "manual", None)

    def test_stop_endpoint_requests_stop_and_returns_control_snapshot(self):
        task_id = "task-api-stop"
        self._create_pending_task(task_id)

        response = self.client.post(f"/tasks/{task_id}/stop")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertTrue(data["control"]["stop_requested"])

    def test_skip_current_endpoint_requests_skip_and_returns_control_snapshot(self):
        task_id = "task-api-skip"
        self._create_pending_task(task_id)

        response = self.client.post(f"/tasks/{task_id}/skip-current")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["control"]["pending_skip_requests"], 1)

    def test_pause_and_resume_endpoints_update_control_snapshot(self):
        task_id = "task-api-pause"
        self._create_pending_task(task_id)

        pause_response = self.client.post(f"/tasks/{task_id}/pause")
        resume_response = self.client.post(f"/tasks/{task_id}/resume")

        self.assertEqual(pause_response.status_code, 200)
        self.assertEqual(resume_response.status_code, 200)
        self.assertTrue(pause_response.json()["control"]["paused"])
        self.assertFalse(resume_response.json()["control"]["paused"])

    def test_task_snapshots_include_platform_for_frontend_recovery(self):
        task_id = "task-api-platform"
        self._create_pending_task(task_id)

        detail_response = self.client.get(f"/tasks/{task_id}")
        list_response = self.client.get("/tasks")

        self.assertEqual(detail_response.status_code, 200)
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(detail_response.json()["platform"], "fake")
        self.assertIn("worker_states", detail_response.json())
        self.assertEqual(detail_response.json()["worker_states"], [])
        self.assertTrue(any(item["id"] == task_id and item["platform"] == "fake" for item in list_response.json()))


if __name__ == "__main__":
    unittest.main()
