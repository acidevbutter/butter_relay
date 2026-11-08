#!/usr/bin/env python
"""Black-box smoke test for a deployed Butter Relay (staging/prod on Coolify).

Publishes real tasks onto the shared Celery queue (exactly like devbutter_backend's
RelayClient) and polls the relay API until each Message row reaches its expected
terminal status. Needs no DB access: it only uses Redis + the API-key-authenticated
/api/v1/messages endpoint.

Scenarios
  happy      valid project/template/recipient      -> status "sent"
  blocked    recipient domain outside the allow-list -> status "failed"
             (non-production delivery policy; skipped when SMOKE_BLOCKED_TO is unset)
  unknown    template_key that does not exist      -> no Message row is ever created
             (the worker retries NotFound then gives up); asserted by absence
  malformed  project_id that is not a UUID         -> dropped by the worker, no row

Required env
  RELAY_BASE_URL      e.g. https://relay-staging.devbutter.tech
  RELAY_REDIS_URL     the same REDIS_URL the relay worker uses
  RELAY_API_KEY       API key (Bearer) of the project under test
  RELAY_PROJECT_ID    UUID of that project
  SMOKE_TEMPLATE_KEY  an active template key for that project (e.g. devbutter_*)
  SMOKE_TO            recipient on an allowed domain (a Mailpit/own mailbox)
Optional env
  RELAY_QUEUE_NAME    default "relay-shared"
  SMOKE_PAYLOAD       JSON object for the template payload (default "{}")
  SMOKE_BLOCKED_TO    recipient on a NON-allowed domain, enables the "blocked" scenario
  SMOKE_TIMEOUT       seconds to wait per scenario (default 60)

Exit code: 0 all passed, 1 any failure, 2 bad configuration.
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass

import httpx
from celery import Celery

TASK_NAME = "butter_relay.process_message"
TERMINAL = {"sent", "failed"}


def env(name: str, default: str | None = None, *, required: bool = False) -> str:
    value = os.environ.get(name, default)
    if required and not value:
        print(f"config error: {name} is required", file=sys.stderr)
        sys.exit(2)
    return value or ""


@dataclass
class Result:
    name: str
    ok: bool
    detail: str


class Smoke:
    def __init__(self) -> None:
        self.base_url = env("RELAY_BASE_URL", required=True).rstrip("/")
        self.redis_url = env("RELAY_REDIS_URL", required=True)
        self.api_key = env("RELAY_API_KEY", required=True)
        self.project_id = env("RELAY_PROJECT_ID", required=True)
        self.template_key = env("SMOKE_TEMPLATE_KEY", required=True)
        self.to = env("SMOKE_TO", required=True)
        self.blocked_to = env("SMOKE_BLOCKED_TO")
        self.queue = env("RELAY_QUEUE_NAME", "relay-shared")
        self.timeout = int(env("SMOKE_TIMEOUT", "60"))
        try:
            self.payload = json.loads(env("SMOKE_PAYLOAD", "{}"))
        except json.JSONDecodeError as exc:
            print(f"config error: SMOKE_PAYLOAD is not valid JSON: {exc}", file=sys.stderr)
            sys.exit(2)
        self.http = httpx.Client(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=15,
        )
        self.celery = Celery("butter_relay_smoke", broker=self.redis_url)

    # -- helpers ------------------------------------------------------------
    def publish(self, *, project_id: str, template_key: str, to: str) -> str:
        """Publish with an explicit task id: the worker stores it as broker_message_id."""
        task_id = f"smoke-{uuid.uuid4()}"
        self.celery.send_task(
            TASK_NAME,
            task_id=task_id,
            kwargs={
                "project_id": project_id,
                "template_key": template_key,
                "to": to,
                "payload": self.payload,
            },
            queue=self.queue,
        )
        return task_id

    def find_message(self, task_id: str) -> dict | None:
        resp = self.http.get("/api/v1/messages/", params={"limit": 200})
        resp.raise_for_status()
        for message in resp.json():
            if message.get("broker_message_id") == task_id:
                return message
        return None

    def wait_terminal(self, task_id: str) -> dict | None:
        deadline = time.monotonic() + self.timeout
        message = None
        while time.monotonic() < deadline:
            message = self.find_message(task_id)
            if message and message["status"] in TERMINAL:
                return message
            time.sleep(2)
        return message

    def assert_absent(self, task_id: str, wait: int = 15) -> dict | None:
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            message = self.find_message(task_id)
            if message:
                return message
            time.sleep(3)
        return None

    # -- checks -------------------------------------------------------------
    def check_health(self) -> Result:
        try:
            live = self.http.get("/health")
            ready = self.http.get("/health/ready")
        except httpx.HTTPError as exc:
            return Result("health", False, f"API unreachable: {exc}")
        ok = live.status_code == 200 and ready.status_code == 200
        return Result("health", ok, f"/health={live.status_code} /health/ready={ready.status_code}")

    def check_auth(self) -> Result:
        anon = httpx.get(f"{self.base_url}/api/v1/messages/", timeout=15)
        if anon.status_code in (401, 403):
            return Result("auth", True, f"anonymous /api/v1 rejected ({anon.status_code})")
        return Result(
            "auth", False, f"anonymous /api/v1/messages answered {anon.status_code}, expected 401/403"
        )

    def check_happy(self) -> Result:
        task_id = self.publish(
            project_id=self.project_id, template_key=self.template_key, to=self.to
        )
        message = self.wait_terminal(task_id)
        if message is None:
            return Result(
                "happy", False,
                f"no Message row after {self.timeout}s for {task_id}: worker not consuming "
                f"queue '{self.queue}' or Redis/project mismatch",
            )
        ok = message["status"] == "sent"
        return Result(
            "happy", ok,
            f"status={message['status']} attempts={message['attempts']} "
            f"provider={message['provider']} error={message['last_error']}",
        )

    def check_blocked(self) -> Result:
        if not self.blocked_to:
            return Result("blocked", True, "skipped (SMOKE_BLOCKED_TO not set)")
        task_id = self.publish(
            project_id=self.project_id, template_key=self.template_key, to=self.blocked_to
        )
        message = self.wait_terminal(task_id)
        if message is None:
            return Result("blocked", False, f"no Message row after {self.timeout}s")
        ok = message["status"] == "failed" and "blocked" in (message["last_error"] or "")
        return Result("blocked", ok, f"status={message['status']} error={message['last_error']}")

    def check_unknown_template(self) -> Result:
        task_id = self.publish(
            project_id=self.project_id, template_key="smoke_does_not_exist", to=self.to
        )
        message = self.assert_absent(task_id)
        ok = message is None
        return Result(
            "unknown-template", ok,
            "no row created (worker gave up on NotFound)" if ok
            else f"unexpected row status={message['status']}",
        )

    def check_malformed(self) -> Result:
        task_id = self.publish(project_id="not-a-uuid", template_key=self.template_key, to=self.to)
        message = self.assert_absent(task_id)
        ok = message is None
        return Result(
            "malformed", ok,
            "dropped without a row (check worker logs for 'dropping malformed')" if ok
            else f"unexpected row status={message['status']}",
        )

    def run(self) -> int:
        results: list[Result] = []
        checks: list[Callable[[], Result]] = [
            self.check_health,
            self.check_auth,
            self.check_happy,
            self.check_blocked,
            self.check_unknown_template,
            self.check_malformed,
        ]
        for check in checks:
            try:
                results.append(check())
            except Exception as exc:  # a crashed check is a failed check
                results.append(Result(check.__name__.removeprefix("check_"), False, repr(exc)))
            r = results[-1]
            print(f"{'OK  ' if r.ok else 'FAIL'} {r.name}: {r.detail}", flush=True)
            if r.name == "health" and not r.ok:
                break  # nothing else can work
        failed = [r for r in results if not r.ok]
        print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
        return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(Smoke().run())
