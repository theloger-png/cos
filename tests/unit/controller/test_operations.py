"""Unit tests for the operation log: classification, middleware, API and progress."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from common.utils import hash_api_key
from controller import operations
from controller.api.auth_users import create_access_token
from controller.api.deps import db_session
from controller.api.operations_middleware import OperationLogMiddleware
from controller.api.routers import operations as operations_router
from controller.db import session as db_session_module
from controller.db.base import Base
from controller.db.models import APIKey, Operation, Tenant, User, VM


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

_ID = "11111111-2222-3333-4444-555555555555"


class TestClassify:
    @pytest.mark.parametrize(
        "method,path,action,has_id",
        [
            ("POST", "/api/v1/vms", "vm.create", False),
            ("DELETE", f"/api/v1/vms/{_ID}", "vm.delete", True),
            ("POST", f"/api/v1/vms/{_ID}/start", "vm.start", True),
            ("POST", f"/api/v1/vms/{_ID}/stop", "vm.stop", True),
            ("POST", f"/api/v1/vms/{_ID}/reboot", "vm.reboot", True),
            ("POST", f"/api/v1/vms/{_ID}/migrate", "vm.migrate", True),
            ("POST", f"/api/v1/vms/{_ID}/reset-password", "vm.reset_password", True),
            ("PUT", f"/api/v1/vms/{_ID}/hardware", "vm.hardware", True),
            ("POST", "/api/v1/nodes", "node.register", False),
            ("DELETE", f"/api/v1/nodes/{_ID}", "node.delete", True),
            ("POST", "/api/v1/networks", "network.create", False),
            ("POST", "/api/v1/tenants", "tenant.create", False),
            ("POST", f"/api/v1/tenants/{_ID}/apikeys", "tenant.apikey", True),
            ("POST", f"/api/v1/templates/{_ID}/fetch-image", "template.fetch_image", True),
            ("DELETE", f"/api/v1/templates/{_ID}", "template.delete", True),
        ],
    )
    def test_known_routes(self, method, path, action, has_id):
        info, target_id = operations.classify(method, path)
        assert info.action == action
        assert (target_id == _ID) is has_id

    def test_trailing_slash_is_tolerated(self):
        assert operations.classify("POST", "/api/v1/vms/")[0].action == "vm.create"

    def test_unknown_mutating_route_falls_back_to_generic_entry(self):
        info, target_id = operations.classify("PATCH", "/api/v1/future/thing")
        assert info.action == "http.patch"
        assert info.label == "PATCH /api/v1/future/thing"
        assert target_id is None


class TestIsIgnored:
    @pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS", "get"])
    def test_reads_are_ignored(self, method):
        assert operations.is_ignored(method, "/api/v1/vms")

    def test_heartbeat_and_console_ticket_are_ignored(self):
        assert operations.is_ignored("POST", f"/api/v1/nodes/{_ID}/heartbeat")
        assert operations.is_ignored("POST", f"/api/v1/vms/{_ID}/console-ticket")

    def test_mutations_are_not_ignored(self):
        assert not operations.is_ignored("POST", "/api/v1/vms")
        assert not operations.is_ignored("DELETE", f"/api/v1/vms/{_ID}")


class TestBuildDescription:
    def test_force_stop_route(self):
        info, tid = operations.classify("POST", f"/api/v1/vms/{_ID}/force-stop")
        assert info.action == "vm.force_stop"
        assert operations.build_description(info, "web-01", tid) == "Hard stop VM web-01"

    def test_uses_target_name(self):
        info, tid = operations.classify("POST", f"/api/v1/vms/{_ID}/stop")
        assert operations.build_description(info, "web-01", tid) == "Soft stop VM web-01"

    def test_falls_back_to_short_id(self):
        info, tid = operations.classify("DELETE", f"/api/v1/vms/{_ID}")
        assert operations.build_description(info, None, tid) == "Delete VM 11111111"

    def test_generic_route_has_no_suffix(self):
        info, _ = operations.classify("PATCH", "/x")
        assert operations.build_description(info, None, None) == "PATCH /x"


class TestOutcomeAndProgressOutsideRequest:
    @pytest.mark.asyncio
    async def test_noops_when_no_operation_is_bound(self):
        operations.set_outcome(failed=True, error="x")
        await operations.report_progress(50, "half")  # must not raise or touch the DB


# ---------------------------------------------------------------------------
# Integration against in-memory SQLite
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def sessionmaker(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(db_session_module, "AsyncSessionLocal", maker, raising=False)
    yield maker
    await engine.dispose()


async def _add(maker, *objs):
    async with maker() as s:
        s.add_all(objs)
        await s.commit()


def _user(name="alice", tenant_id=None) -> User:
    return User(id=uuid.uuid4(), username=name, hashed_password="x", role="admin", tenant_id=tenant_id, active=True)


def _bearer(user: User) -> dict:
    token = create_access_token({"sub": str(user.id), "username": user.username, "role": user.role})
    return {"Authorization": f"Bearer {token}"}


async def _all_ops(maker) -> list[Operation]:
    async with maker() as s:
        return list((await s.execute(select(Operation).order_by(Operation.started_at))).scalars().all())


def _make_app(maker) -> FastAPI:
    app = FastAPI()
    app.add_middleware(OperationLogMiddleware)

    @app.post("/api/v1/vms")
    async def create_vm(body: dict):
        await operations.report_progress(40, "halfway")
        return {"ok": True}

    @app.post("/api/v1/vms/{vm_id}/stop")
    async def stop_vm(vm_id: str):
        raise HTTPException(status_code=409, detail="VM is not running")

    @app.post("/api/v1/templates/{tpl_id}/fetch-image")
    async def fetch(tpl_id: str):
        operations.set_outcome(failed=True, message="0/2 nodes", error="node1: boom")
        return {"ok": True}

    @app.post("/api/v1/nodes/{node_id}/heartbeat")
    async def heartbeat(node_id: str):
        return {}

    @app.post("/api/v1/auth/login")
    async def login(body: dict):
        if body.get("password") != "good":
            raise HTTPException(status_code=401, detail="Invalid username or password")
        return {"token": "t"}

    @app.get("/api/v1/vms")
    async def list_vms():
        return []

    @app.post("/api/v1/boom")
    async def boom():
        raise RuntimeError("kaput")

    async def _db():
        async with maker() as session:
            yield session

    app.dependency_overrides[db_session] = _db
    app.include_router(operations_router.router)
    return app


class TestMiddleware:
    @pytest.mark.asyncio
    async def test_successful_create_is_recorded_with_name_user_and_progress(self, sessionmaker):
        user = _user("alice")
        await _add(sessionmaker, user)
        client = TestClient(_make_app(sessionmaker))
        r = client.post("/api/v1/vms", json={"name": "web-01", "password": "secret"}, headers=_bearer(user))
        assert r.status_code == 200
        (op,) = await _all_ops(sessionmaker)
        assert (op.user_label, op.action, op.description) == ("alice", "vm.create", "Create VM web-01")
        assert op.status == "success" and op.progress == 100
        assert op.message == "halfway"
        assert op.finished_at is not None
        assert "secret" not in repr(op.__dict__)

    @pytest.mark.asyncio
    async def test_http_error_is_recorded_as_failed_with_detail(self, sessionmaker):
        user = _user()
        vm_id = uuid.uuid4()
        await _add(sessionmaker, user)
        client = TestClient(_make_app(sessionmaker))
        r = client.post(f"/api/v1/vms/{vm_id}/stop", headers=_bearer(user))
        assert r.status_code == 409
        (op,) = await _all_ops(sessionmaker)
        assert op.status == "failed" and op.error == "VM is not running"
        assert op.target_id == str(vm_id)

    @pytest.mark.asyncio
    async def test_target_name_is_looked_up_for_id_routes(self, sessionmaker):
        user = _user()
        tenant = Tenant(id=uuid.uuid4(), name="t", email="t@x")
        vm = VM(id=uuid.uuid4(), name="db-02", tenant_id=tenant.id, node_id=uuid.uuid4(), cpu_cores=1, ram_mb=1, disk_gb=1)
        await _add(sessionmaker, user, tenant)
        async with sessionmaker() as s:  # VM has FKs; SQLite does not enforce them
            s.add(vm)
            await s.commit()
        client = TestClient(_make_app(sessionmaker))
        client.post(f"/api/v1/vms/{vm.id}/stop", headers=_bearer(user))
        (op,) = await _all_ops(sessionmaker)
        assert op.description == "Soft stop VM db-02"

    @pytest.mark.asyncio
    async def test_handler_can_override_outcome_of_http_200(self, sessionmaker):
        user = _user()
        await _add(sessionmaker, user)
        client = TestClient(_make_app(sessionmaker))
        assert client.post(f"/api/v1/templates/{uuid.uuid4()}/fetch-image", headers=_bearer(user)).status_code == 200
        (op,) = await _all_ops(sessionmaker)
        assert op.status == "failed" and op.error == "node1: boom" and op.message == "0/2 nodes"

    @pytest.mark.asyncio
    async def test_unhandled_exception_is_recorded_and_still_raised(self, sessionmaker):
        user = _user()
        await _add(sessionmaker, user)
        client = TestClient(_make_app(sessionmaker), raise_server_exceptions=False)
        assert client.post("/api/v1/boom", headers=_bearer(user)).status_code == 500
        (op,) = await _all_ops(sessionmaker)
        assert op.status == "failed" and "kaput" in op.error

    @pytest.mark.asyncio
    async def test_reads_heartbeats_and_unauthenticated_requests_are_not_recorded(self, sessionmaker):
        user = _user()
        await _add(sessionmaker, user)
        client = TestClient(_make_app(sessionmaker))
        client.get("/api/v1/vms", headers=_bearer(user))
        client.post(f"/api/v1/nodes/{uuid.uuid4()}/heartbeat", headers=_bearer(user))
        client.post("/api/v1/vms", json={"name": "x"})  # no credentials
        client.post("/api/v1/vms", json={"name": "x"}, headers={"Authorization": "Bearer garbage"})
        assert await _all_ops(sessionmaker) == []

    @pytest.mark.asyncio
    async def test_api_key_caller_is_labelled_and_tenant_scoped(self, sessionmaker):
        tenant_id = uuid.uuid4()
        key = APIKey(id=uuid.uuid4(), tenant_id=tenant_id, key_hash=hash_api_key("k-123"), description="ci")
        await _add(sessionmaker, key)
        client = TestClient(_make_app(sessionmaker))
        client.post("/api/v1/vms", json={"name": "v"}, headers={"X-API-Key": "k-123"})
        (op,) = await _all_ops(sessionmaker)
        assert op.user_label == "api-key:ci" and op.tenant_id == tenant_id

    @pytest.mark.asyncio
    async def test_login_success_and_failure_are_recorded_without_password(self, sessionmaker):
        client = TestClient(_make_app(sessionmaker))
        client.post("/api/v1/auth/login", json={"username": "bob", "password": "good"})
        client.post("/api/v1/auth/login", json={"username": "bob", "password": "wrong-pw"})
        ok, bad = await _all_ops(sessionmaker)
        assert (ok.status, ok.description) == ("success", "Login")
        assert (bad.status, bad.description, bad.error) == ("failed", "Failed login attempt", "Invalid username or password")
        assert "wrong-pw" not in repr(bad.__dict__) and "good" not in repr(ok.__dict__)

    @pytest.mark.asyncio
    async def test_unavailable_log_database_never_breaks_the_request(self, sessionmaker, monkeypatch):
        user = _user()
        await _add(sessionmaker, user)
        client = TestClient(_make_app(sessionmaker))
        headers = _bearer(user)

        # Auth lookup already happened via the working maker above; now break
        # only the log writes by failing every later session open.
        calls = {"n": 0}
        real = db_session_module.AsyncSessionLocal

        def flaky():
            calls["n"] += 1
            if calls["n"] > 1:  # 1st call = actor lookup, then start_operation
                raise RuntimeError("db down")
            return real()

        monkeypatch.setattr(db_session_module, "AsyncSessionLocal", flaky)
        assert client.post("/api/v1/vms", json={"name": "v"}, headers=headers).status_code == 200


class TestListEndpoint:
    async def _seed(self, maker):
        t1, t2 = uuid.uuid4(), uuid.uuid4()
        now = datetime.now(timezone.utc)

        def op(i, **kw):
            base = dict(
                id=uuid.uuid4(), user_label="alice", action="vm.start", description=f"op{i}",
                status="success", started_at=now - timedelta(minutes=10 - i),
            )
            base.update(kw)
            return Operation(**base)

        await _add(
            maker,
            op(1, tenant_id=t1), op(2, tenant_id=t2, user_label="bob", action="node.delete"),
            op(3, tenant_id=t1, status="failed", action="vm.stop"), op(4, tenant_id=None, status="running"),
        )
        return t1, t2

    @pytest.mark.asyncio
    async def test_admin_sees_everything_newest_first(self, sessionmaker):
        await self._seed(sessionmaker)
        admin = _user("root")
        await _add(sessionmaker, admin)
        data = TestClient(_make_app(sessionmaker)).get("/api/v1/operations", headers=_bearer(admin)).json()
        assert [o["description"] for o in data] == ["op4", "op3", "op2", "op1"]

    @pytest.mark.asyncio
    async def test_tenant_user_sees_only_own_tenant(self, sessionmaker):
        t1, _ = await self._seed(sessionmaker)
        u = _user("tenantuser", tenant_id=t1)
        await _add(sessionmaker, u)
        data = TestClient(_make_app(sessionmaker)).get("/api/v1/operations", headers=_bearer(u)).json()
        assert sorted(o["description"] for o in data) == ["op1", "op3"]

    @pytest.mark.asyncio
    async def test_filters_and_limit(self, sessionmaker):
        await self._seed(sessionmaker)
        admin = _user("root")
        await _add(sessionmaker, admin)
        c = TestClient(_make_app(sessionmaker))
        h = _bearer(admin)
        assert [o["description"] for o in c.get("/api/v1/operations?status=failed", headers=h).json()] == ["op3"]
        assert [o["description"] for o in c.get("/api/v1/operations?user=bob", headers=h).json()] == ["op2"]
        assert [o["description"] for o in c.get("/api/v1/operations?action=vm", headers=h).json()] == ["op4", "op3", "op1"]
        assert len(c.get("/api/v1/operations?limit=2", headers=h).json()) == 2
        assert c.get("/api/v1/operations?status=bogus", headers=h).status_code == 422

    @pytest.mark.asyncio
    async def test_requires_authentication(self, sessionmaker):
        assert TestClient(_make_app(sessionmaker)).get("/api/v1/operations").status_code == 401

    @pytest.mark.asyncio
    async def test_before_cursor_pages_back_in_time(self, sessionmaker):
        await self._seed(sessionmaker)
        admin = _user("root")
        await _add(sessionmaker, admin)
        c = TestClient(_make_app(sessionmaker))
        h = _bearer(admin)
        first = c.get("/api/v1/operations?limit=2", headers=h).json()
        rest = c.get("/api/v1/operations", params={"before": first[-1]["started_at"]}, headers=h).json()
        assert [o["description"] for o in rest] == ["op2", "op1"]


class TestMaintenance:
    @pytest.mark.asyncio
    async def test_purge_removes_only_old_finished_operations(self, sessionmaker):
        now = datetime.now(timezone.utc)
        old = now - timedelta(days=91)

        def op(desc, started, status):
            return Operation(id=uuid.uuid4(), user_label="u", action="a", description=desc, status=status, started_at=started)

        await _add(sessionmaker, op("old-done", old, "success"), op("old-running", old, "running"), op("fresh", now, "success"))
        assert await operations.purge_old_operations() == 1
        assert sorted(o.description for o in await _all_ops(sessionmaker)) == ["fresh", "old-running"]

    @pytest.mark.asyncio
    async def test_stale_running_operations_are_failed_at_startup(self, sessionmaker):
        await _add(sessionmaker, Operation(id=uuid.uuid4(), user_label="u", action="a", description="d", status="running", started_at=datetime.now(timezone.utc)))
        assert await operations.fail_stale_running_operations() == 1
        (op,) = await _all_ops(sessionmaker)
        assert op.status == "failed" and "restarted" in op.error and op.finished_at is not None

    @pytest.mark.asyncio
    async def test_system_event_is_recorded_under_system_user(self, sessionmaker):
        await operations.record_system_event("node.offline", "Node n1 marked offline", success=False)
        (op,) = await _all_ops(sessionmaker)
        assert op.user_label == "system" and op.status == "failed" and op.progress is None
