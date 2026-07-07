# Work Board Centralization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert the single-project work board into the centralized cross-project board by adding a `project` dimension, a repeatable Atlas-ledger sync, and fork-hygiene guardrails.

**Architecture:** The board (ORM `db_work_board.py`, service `work_board_service.py`, router `work_board_router.py`) is already built and tested. We add one column (`project`), rescope the single-NOW invariant and NEXT cap to be per-project, thread an optional `project` filter through service and router, and add a one-way idempotent sync from Atlas-Copilot's `docs/pilot/ledger.json` into the board under namespaced ids (`atlas-copilot:w-004`). Board = source of truth for the cross-project view; the Atlas ledger remains Atlas's local write surface.

**Tech Stack:** Python 3.11+, FastAPI, synchronous SQLAlchemy (intentional — do not "fix"), Alembic, pytest with in-memory SQLite.

## Global Constraints

- Repo: `/Users/chadkuisel/Workspace/mcp-context-forge` (Chad's fork). All paths below are relative to it unless absolute.
- Sign every commit: `git commit -s`. Conventional Commits. **Never mention AI assistants in commits/diffs.** Do not push.
- Sync SQLAlchemy in async handlers is intentional — never flag or change it.
- Alembic: run `alembic heads` before authoring any migration; point `down_revision` at the real head. Idempotent upgrades (inspector-guarded).
- Ruff line length 200; run `make pre-commit` after writing code in each task.
- Never edit these upstream-owned files: `mcpgateway/db.py`, `mcpgateway/main.py`, `mcpgateway/config.py`, `mcpgateway/auth.py`, `mcpgateway/api/v1/__init__.py` (already registers the board), `mcpgateway/templates/admin.html`.

---

## ANTI-DRIFT CONTRACT (binding on the implementation agent)

1. **Touch only the files listed in the current task.** If correctness seems to require touching anything else, STOP and report — do not improvise.
2. **No new features.** No new lanes, no new endpoints beyond those listed, no UI work, no renames, no refactors of adjacent code. If you get an idea, record it by creating a `tangents`-lane item on the board (or note it in your final report) — never by writing code.
3. **Never modify the attention state machine** (`_AGENT_RESOLUTION_TRANSITIONS`, `add_note` semantics, EARS R1–R10 behavior) or weaken existing test assertions to make new code pass. If an existing test fails, your change is wrong — not the test.
4. **Migration discipline:** if `alembic heads` shows a head other than `e3d48e0f3f0f`, use that head as `down_revision`. Never create a second head. Never edit an existing migration file.
5. **Two-strike rule:** if a test fails twice after one fix attempt, STOP, leave the working tree intact, and report the failure verbatim.
6. **Sync is one-way** (ledger → board). Never write to `ledger.json` or anything in the Atlas-Copilot repo.
7. **Verify persistence before every commit:** `git diff --stat` must list exactly the files the task names.
8. **No upstream pulls/merges** during this plan.
9. Each task is complete only when its test step passes and its commit exists. Do not batch commits across tasks.

---

### Task 1: Fork hygiene — upstream remote + merge policy

**Files:**
- Modify: `CLAUDE.md` (append one section at end)

**Interfaces:**
- Consumes: nothing
- Produces: nothing code-visible; policy text later tasks assume (no upstream pulls)

- [ ] **Step 1: Add the upstream remote**

```bash
cd /Users/chadkuisel/Workspace/mcp-context-forge
git remote add upstream https://github.com/IBM/mcp-context-forge.git
git remote -v
```

Expected: four lines; `origin` (Helix9393 fork) fetch/push and `upstream` (IBM) fetch/push. If `upstream` already exists, skip the add.

- [ ] **Step 2: Fetch upstream refs (read-only; no merge)**

```bash
git fetch upstream --tags 2>&1 | tail -3
```

Expected: ref/tag lines or silence. Do NOT merge, rebase, or pull.

- [ ] **Step 3: Append the merge policy to CLAUDE.md**

Append verbatim at the end of `CLAUDE.md`:

```markdown
## Fork merge policy

This is a personal fork. Upstream (IBM/mcp-context-forge) is tracked via the `upstream`
remote for visibility and CVE monitoring only. Policy: **pinned; CVE-only cherry-picks.**
No routine upstream merges. Divergence must stay additive — work-board files
(`db_work_board.py`, `services/work_board_service.py`, `routers/work_board_router.py`,
its migrations, templates, and tests) plus the minimal registration touch-points already
made. Any change that widens the upstream-modified file surface requires an explicit
operator decision first.
```

- [ ] **Step 4: Verify and commit**

```bash
git diff --stat
git add CLAUDE.md
git commit -s -m "docs: add fork merge policy (pinned, CVE-only cherry-picks)"
```

Expected `git diff --stat` before add: only `CLAUDE.md`.

---

### Task 2: `project` column + per-project single-NOW index

**Files:**
- Modify: `mcpgateway/db_work_board.py` (WorkBoardItem: one column + one index)
- Create: `mcpgateway/alembic/versions/<generated>_add_work_board_project_column.py`
- Test: `tests/unit/mcpgateway/services/test_work_board_service.py` (one new test)

**Interfaces:**
- Consumes: existing `WorkBoardItem` (24 columns, `__table_args__` with `ck_work_board_lane`, `ck_work_board_attention`, partial unique index `uq_work_board_single_now` on `lane` where `lane='now'`)
- Produces: `WorkBoardItem.project: Mapped[str]` (String(64), NOT NULL, server_default `'gateway'`); `uq_work_board_single_now` now spans (`project`, `lane`)

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/mcpgateway/services/test_work_board_service.py` (match the file's existing imports; the `db_session` fixture already exists at line ~41):

```python
class TestProjectColumn:
    def test_project_defaults_to_gateway(self, db_session):
        item = WorkBoardItem(id="x-1", lane="tangents", title="t", status="parked")
        db_session.add(item)
        db_session.commit()
        db_session.refresh(item)
        assert item.project == "gateway"
```

If `WorkBoardItem` is not already imported in the test file, add it to the existing `from mcpgateway.db_work_board import ...` line.

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/unit/mcpgateway/services/test_work_board_service.py::TestProjectColumn -v 2>&1 | tail -5
```

Expected: FAIL — `AttributeError: ... object has no attribute 'project'` (or TypeError on construction).

- [ ] **Step 3: Add the column to the model**

In `mcpgateway/db_work_board.py`, inside `WorkBoardItem`, directly after the `lane` column (line ~49):

```python
    # Cross-project scope: which repo/project owns this item ("gateway" = this repo,
    # "atlas-copilot" = synced from the Atlas pilot ledger). Drives per-project
    # single-NOW and NEXT-cap invariants; filterable on every read path.
    project: Mapped[str] = mapped_column(String(64), nullable=False, server_default=text("'gateway'"), default="gateway")
```

- [ ] **Step 4: Rescope the single-NOW index**

In the same file's `__table_args__` (line ~114), the `Index("uq_work_board_single_now", "lane", unique=True, ...)` entry: add `"project"` as the first column argument, keeping the name, `unique=True`, and the existing partial-index kwargs (`sqlite_where`/`postgresql_where`) byte-identical:

```python
        Index(
            "uq_work_board_single_now",
            "project",
            "lane",
            unique=True,
            # keep the existing sqlite_where / postgresql_where kwargs exactly as they are
        ),
```

- [ ] **Step 5: Run test to verify it passes**

```bash
python -m pytest tests/unit/mcpgateway/services/test_work_board_service.py::TestProjectColumn -v 2>&1 | tail -5
```

Expected: PASS (the fixture uses `Base.metadata.create_all`, so the model change is live immediately).

- [ ] **Step 6: Confirm the real migration head**

```bash
cd /Users/chadkuisel/Workspace/mcp-context-forge && alembic heads 2>&1 | tail -3
```

Expected: a single head, `e3d48e0f3f0f`. If the head differs, use that value as `down_revision` in Step 7. If `alembic heads` errors on env config, verify instead: `grep -rl "down_revision.*e3d48e0f3f0f" mcpgateway/alembic/versions/` — empty output confirms `e3d48e0f3f0f` is the chain tip.

- [ ] **Step 7: Create the migration**

```bash
alembic revision -m "add work_board project column" 2>&1 | tail -2
```

Then replace the generated file's body (keep the generated `revision` id; set `down_revision` per Step 6), following the house idempotency style of `e3d48e0f3f0f_add_work_board_meta_table.py`:

```python
def upgrade() -> None:
    """Add work_board_items.project and rescope the single-NOW index per project (idempotent)."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [c["name"] for c in inspector.get_columns("work_board_items")]
    if "project" not in columns:
        op.add_column(
            "work_board_items",
            sa.Column("project", sa.String(64), nullable=False, server_default="gateway"),
        )
    indexes = {i["name"]: i for i in sa.inspect(op.get_bind()).get_indexes("work_board_items")}
    idx = indexes.get("uq_work_board_single_now")
    if idx is not None and "project" not in idx["column_names"]:
        op.drop_index("uq_work_board_single_now", table_name="work_board_items")
        op.create_index(
            "uq_work_board_single_now",
            "work_board_items",
            ["project", "lane"],
            unique=True,
            sqlite_where=sa.text("lane = 'now'"),
            postgresql_where=sa.text("lane = 'now'"),
        )


def downgrade() -> None:
    """Drop the project column and restore the single-column NOW index (non-fatal on failure, house style)."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [c["name"] for c in inspector.get_columns("work_board_items")]
    if "project" in columns:
        try:
            op.drop_index("uq_work_board_single_now", table_name="work_board_items")
            op.create_index("uq_work_board_single_now", "work_board_items", ["lane"], unique=True, sqlite_where=sa.text("lane = 'now'"), postgresql_where=sa.text("lane = 'now'"))
            op.drop_column("work_board_items", "project")
        except Exception as e:  # pylint: disable=broad-except
            print(f"Warning: Could not downgrade work_board project column: {e}")
```

Note: before finalizing, open the current model's `uq_work_board_single_now` definition and copy its exact `sqlite_where`/`postgresql_where` expressions into `create_index` — they must match the model, not this plan.

- [ ] **Step 8: Lint, run the board test files, commit**

```bash
make pre-commit 2>&1 | tail -10
python -m pytest tests/unit/mcpgateway/services/test_work_board_service.py -q 2>&1 | tail -3
git add mcpgateway/db_work_board.py mcpgateway/alembic/versions/ tests/unit/mcpgateway/services/test_work_board_service.py
git commit -s -m "feat: add project scope column to work board items"
```

Expected: all existing tests still pass (existing rows/tests get `project="gateway"` by default).

---

### Task 3: Per-project service semantics + read filters

**Files:**
- Modify: `mcpgateway/services/work_board_service.py`
- Test: `tests/unit/mcpgateway/services/test_work_board_service.py`

**Interfaces:**
- Consumes: `WorkBoardItem.project` from Task 2
- Produces (exact signatures Tasks 4–5 rely on):
  - `create_item(db: Session, lane: str, payload: Dict[str, Any], project: str = "gateway") -> WorkBoardItem`
  - `get_board(db: Session, project: Optional[str] = None) -> Dict[str, Any]`
  - `get_backlog(db: Session, project: Optional[str] = None) -> List[Dict[str, Any]]`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/mcpgateway/services/test_work_board_service.py` (using the module's existing service import name — match how other tests in the file call `create_item`/`get_board`):

```python
class TestPerProjectScoping:
    def test_two_projects_can_each_hold_a_now(self, db_session):
        create_item(db_session, "now", {"title": "Gateway NOW"}, project="gateway")
        item = create_item(db_session, "now", {"title": "Atlas NOW"}, project="atlas-copilot")
        assert item.project == "atlas-copilot"

    def test_same_project_second_now_conflicts(self, db_session):
        create_item(db_session, "now", {"title": "First"}, project="gateway")
        with pytest.raises(WorkBoardConflictError):
            create_item(db_session, "now", {"title": "Second"}, project="gateway")

    def test_next_cap_is_per_project(self, db_session):
        for i in range(1, 6):
            create_item(db_session, "next", {"title": f"g{i}", "priority": i}, project="gateway")
        item = create_item(db_session, "next", {"title": "a1", "priority": 1}, project="atlas-copilot")
        assert item.project == "atlas-copilot"

    def test_get_board_filters_by_project(self, db_session):
        create_item(db_session, "tangents", {"title": "g", "status": "parked"}, project="gateway")
        create_item(db_session, "tangents", {"title": "a", "status": "parked"}, project="atlas-copilot")
        board = get_board(db_session, project="atlas-copilot")
        assert [t["title"] for t in board["tangents"]] == ["a"]
        assert len(get_board(db_session)["tangents"]) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/unit/mcpgateway/services/test_work_board_service.py::TestPerProjectScoping -v 2>&1 | tail -8
```

Expected: FAIL — `TypeError: create_item() got an unexpected keyword argument 'project'`.

- [ ] **Step 3: Implement — create_item (line ~297)**

Change the signature and the two conflict checks; set `project` on the constructed item:

```python
def create_item(db: Session, lane: str, payload: Dict[str, Any], project: str = "gateway") -> WorkBoardItem:
```

```python
    if lane == "now":
        existing_now = db.query(WorkBoardItem).filter(WorkBoardItem.lane == "now", WorkBoardItem.project == project).first()
        if existing_now is not None:
            raise WorkBoardConflictError(f"NOW is already occupied by '{existing_now.id}'.")

    if lane == "next":
        count = db.query(func.count(WorkBoardItem.id)).filter(WorkBoardItem.lane == "next", WorkBoardItem.project == project).scalar()  # pylint: disable=not-callable
        if count >= _NEXT_LANE_CAP:
            raise WorkBoardConflictError(f"NEXT lane is at its {_NEXT_LANE_CAP}-item cap; drop or promote an item first.")
```

In the `WorkBoardItem(...)` constructor call at the end of `create_item`, add `project=project,`. Where `create_item` renumbers `next`-lane priorities, scope that query with `WorkBoardItem.project == project` as well.

- [ ] **Step 4: Implement — read paths**

`get_board` (line ~1342):

```python
def get_board(db: Session, project: Optional[str] = None) -> Dict[str, Any]:
    query = db.query(WorkBoardItem)
    if project is not None:
        query = query.filter(WorkBoardItem.project == project)
    all_items = query.all()
```

(The rest of the function's in-Python lane grouping is untouched — note `get_board` without a filter now returns multiple NOW candidates across projects; keep `now_item` as-is, first match, and do not redesign the aggregate shape in this task.)

`get_backlog` (line ~832): same pattern — add `project: Optional[str] = None` and apply `.filter(WorkBoardItem.project == project)` when not None to its base query.

Internal lane movers (`promote_next` ~line 576, `demote_now` ~line 690, `_get_now` ~line 573): everywhere they query `lane == "now"` or count `lane == "next"`, additionally filter by the fetched item's own project (`WorkBoardItem.project == item.project` — use each function's local variable for the item being moved; in `demote_now` it is `now_item`). Change nothing else in these functions.

- [ ] **Step 5: Run the full service test file**

```bash
python -m pytest tests/unit/mcpgateway/services/test_work_board_service.py -q 2>&1 | tail -3
```

Expected: PASS, zero failures — existing single-project tests must pass unchanged (defaults preserve old behavior). Two-strike rule applies.

- [ ] **Step 6: Lint and commit**

```bash
make pre-commit 2>&1 | tail -10
git add mcpgateway/services/work_board_service.py tests/unit/mcpgateway/services/test_work_board_service.py
git commit -s -m "feat: per-project NOW/NEXT invariants and project read filters"
```

---

### Task 4: Router + schema — expose `project`

**Files:**
- Modify: `mcpgateway/routers/work_board_router.py`
- Test: `tests/unit/mcpgateway/routers/test_work_board_router.py`

**Interfaces:**
- Consumes: Task 3 signatures (`get_board(db, project=None)`, `get_backlog(db, project=None)`, `create_item(db, lane, payload, project)`)
- Produces: `GET /board?project=`, `GET /board/backlog?project=`, and `WorkBoardItemCreateIn.project: str = "gateway"`

- [ ] **Step 1: Write the failing test**

Open `tests/unit/mcpgateway/routers/test_work_board_router.py`, find its existing test for `GET ""` (the board read), and add a sibling test using the same client/fixture/auth pattern the file already uses (do not invent a new fixture):

```python
def test_get_board_passes_project_filter(<same fixtures as the existing get-board test>):
    # seed one item per project via the file's existing seeding helper/pattern
    resp = <existing client>.get("<existing board base path>?project=atlas-copilot", <existing auth kwargs>)
    assert resp.status_code == 200
    body = resp.json()
    assert all(t["project"] == "atlas-copilot" for lane in ("next", "tangents", "findings") for t in body[lane])
```

Anti-drift note: the angle-bracketed parts mean "reuse the identical mechanism already present in this file's board-read test" — copy that test, change only the query string and assertions. Do not restructure the test module.

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/unit/mcpgateway/routers/test_work_board_router.py -k project -v 2>&1 | tail -5
```

Expected: FAIL (project param ignored → both projects' items returned, or `project` key absent).

- [ ] **Step 3: Implement the router changes**

`GET ""` handler (line ~181):

```python
@work_board_router.get("")
@require_permission("tools.read")
async def get_board(project: Optional[str] = None, db=Depends(get_db), _user=Depends(get_current_user_with_permissions)) -> Dict[str, Any]:
    """Return the full board (all lanes) plus the next-move projection.

    Args:
        project: Optional project scope filter (e.g. ``atlas-copilot``).
        db: Database session.
        _user: Authenticated user context (RBAC only; unused directly).

    Returns:
        Dict[str, Any]: The full board, grouped by lane.
    """
    return service.get_board(db, project=project)
```

`GET "/backlog"` handler (line ~211): same change — add `project: Optional[str] = None` first parameter, pass `project=project` to `service.get_backlog`.

`WorkBoardItemCreateIn` (line ~80): add after `title`:

```python
    project: str = Field(default="gateway", max_length=64, description="Project scope, e.g. 'atlas-copilot'")
```

In the item-create handler (the `@work_board_router.post` that consumes `WorkBoardItemCreateIn`), pass `project=body.project` through to `service.create_item`, and exclude `project` from the payload dict if the handler builds it via `.model_dump()` (`body.model_dump(exclude={"lane", "project"}, exclude_none=True)` — match the existing exclude set, adding `"project"`).

Also confirm item serialization includes `project`: grep for where item dicts are built (service `_item_to_dict` or similar). If `project` is not included, add `"project": item.project,` to that one serializer. Touch nothing else in it.

- [ ] **Step 4: Run the router test file**

```bash
python -m pytest tests/unit/mcpgateway/routers/test_work_board_router.py -q 2>&1 | tail -3
```

Expected: PASS, zero failures.

- [ ] **Step 5: Lint and commit**

```bash
make pre-commit 2>&1 | tail -10
git add mcpgateway/routers/work_board_router.py tests/unit/mcpgateway/routers/test_work_board_router.py
git commit -s -m "feat: expose project scope on work board API"
```

---

### Task 5: Repeatable Atlas-ledger sync

**Files:**
- Modify: `mcpgateway/services/work_board_service.py` (one new function: `upsert_synced_item`)
- Create: `tools/ledger_sync/sync_atlas_ledger.py`
- Test: `tests/unit/mcpgateway/services/test_work_board_service.py`

**Interfaces:**
- Consumes: `WorkBoardItem.project` (Task 2); `add_note(db, item_id, text_value, author="operator", resolution=None)` (exists, line ~479); `LANES` tuple (line ~42)
- Produces: `upsert_synced_item(db: Session, project: str, external_id: str, lane: str, payload: Dict[str, Any]) -> WorkBoardItem`; CLI `python tools/ledger_sync/sync_atlas_ledger.py [--ledger PATH]`

- [ ] **Step 1: Write the failing tests**

```python
class TestUpsertSyncedItem:
    def test_creates_namespaced_item(self, db_session):
        item = upsert_synced_item(db_session, "atlas-copilot", "w-004", "now", {"title": "Gateway pivot", "started": "2026-07-05"})
        assert item.id == "atlas-copilot:w-004"
        assert item.project == "atlas-copilot"
        assert item.lane == "now"

    def test_is_idempotent_and_updates_fields(self, db_session):
        upsert_synced_item(db_session, "atlas-copilot", "w-001", "next", {"title": "old", "priority": 2})
        upsert_synced_item(db_session, "atlas-copilot", "w-001", "next", {"title": "new title", "priority": 1})
        rows = db_session.query(WorkBoardItem).filter(WorkBoardItem.id == "atlas-copilot:w-001").all()
        assert len(rows) == 1
        assert rows[0].title == "new title"
        assert rows[0].priority == 1

    def test_never_touches_attention(self, db_session):
        item = upsert_synced_item(db_session, "atlas-copilot", "w-002", "next", {"title": "t", "priority": 1})
        add_note(db_session, item.id, "operator asks a thing", author="operator")
        upsert_synced_item(db_session, "atlas-copilot", "w-002", "next", {"title": "t2", "priority": 1})
        db_session.refresh(item)
        assert item.attention == "needs_attention"

    def test_rejects_invalid_lane(self, db_session):
        with pytest.raises(WorkBoardValidationError):
            upsert_synced_item(db_session, "atlas-copilot", "w-003", "bogus", {"title": "t"})
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/unit/mcpgateway/services/test_work_board_service.py::TestUpsertSyncedItem -v 2>&1 | tail -6
```

Expected: FAIL — `ImportError`/`NameError: upsert_synced_item`.

- [ ] **Step 3: Implement the service function**

Add to `mcpgateway/services/work_board_service.py` (after `create_item` is fine):

```python
def upsert_synced_item(db: Session, project: str, external_id: str, lane: str, payload: Dict[str, Any]) -> WorkBoardItem:
    """Idempotently mirror an item from an external tracker (e.g. the Atlas pilot ledger).

    The row id is namespaced ``{project}:{external_id}`` so external ids can never collide
    across projects. Mirror semantics: the external tracker is authoritative for its own
    project's title/lane/ordering, so this bypasses the per-project NOW/NEXT-cap 409s that
    guard interactive writes. It NEVER touches ``attention``, ``change_kind``, or
    ``run_state`` — those belong to this board's state machines.

    Args:
        db: SQLAlchemy session.
        project: Project scope, e.g. ``"atlas-copilot"``.
        external_id: The id in the source tracker, e.g. ``"w-004"``.
        lane: One of :data:`LANES`.
        payload: Mirrored fields (``title`` plus optional ``priority``, ``branch``,
            ``started``, ``captured``, ``status``, ``severity``, ``source``).

    Returns:
        WorkBoardItem: The created or updated, committed item.

    Raises:
        WorkBoardValidationError: If ``lane`` is invalid or ``title`` resolves empty.
    """
    if lane not in LANES:
        raise WorkBoardValidationError(f"Invalid lane '{lane}'.")
    title = (payload.get("title") or "").strip()
    if not title:
        raise WorkBoardValidationError("title is required and must be non-empty.")

    item_id = f"{project}:{external_id}"
    if lane == "now":
        # The mirrored project moved its NOW: demote any stale mirrored NOW rows first so the
        # (project, lane='now') unique index cannot trip mid-upsert.
        db.query(WorkBoardItem).filter(
            WorkBoardItem.project == project,
            WorkBoardItem.lane == "now",
            WorkBoardItem.id != item_id,
        ).update({"lane": "next", "priority": None, "started": None}, synchronize_session="fetch")

    item = db.query(WorkBoardItem).filter(WorkBoardItem.id == item_id).first()
    if item is None:
        item = WorkBoardItem(id=item_id, project=project, lane=lane, title=title)
        db.add(item)
    item.lane = lane
    item.title = title
    for field in ("priority", "branch", "started", "captured", "status", "severity", "source"):
        if payload.get(field) is not None:
            setattr(item, field, payload[field])
    db.commit()
    db.refresh(item)
    return item
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/unit/mcpgateway/services/test_work_board_service.py -q 2>&1 | tail -3
```

Expected: PASS, zero failures across the whole file.

- [ ] **Step 5: Verify the session factory import path for the CLI**

```bash
grep -n "SessionLocal" /Users/chadkuisel/Workspace/mcp-context-forge/mcpgateway/db.py | head -3
```

Expected: a `SessionLocal = sessionmaker(...)` (or equivalent) definition. If the factory has a different name, use that name in Step 6 — change nothing in `db.py`.

- [ ] **Step 6: Create the sync CLI**

Create `tools/ledger_sync/sync_atlas_ledger.py`:

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""One-way, idempotent sync: Atlas-Copilot docs/pilot/ledger.json -> work board.

Safe to run repeatedly (e.g. after every Atlas `pilot:refresh`). Items land under
project="atlas-copilot" with ids namespaced "atlas-copilot:<ledger-id>". Ledger notes
are appended as author="agent" commentary (resolution=None), which by contract leaves
the attention state machine untouched. Never writes back to the ledger.
"""

# Standard
import argparse
import json
from pathlib import Path

# First-Party
from mcpgateway.db import SessionLocal
from mcpgateway.services import work_board_service as svc

PROJECT = "atlas-copilot"
DEFAULT_LEDGER = Path.home() / "Workspace/Atlas-Copilot/docs/pilot/ledger.json"


def _note_key(at: str) -> str:
    return f"[ledger {at}]"


def _sync_notes(db, item, notes) -> int:
    """Append only ledger notes not already mirrored (dedupe on the [ledger <at>] prefix)."""
    existing = {n.text.split("]", 1)[0] + "]" for n in item.notes if n.text.startswith("[ledger ")}
    added = 0
    for n in notes or []:
        key = _note_key(n["at"])
        if key not in existing:
            svc.add_note(db, item.id, f"{key} {n['text']}", author="agent")
            added += 1
    return added


def sync(ledger_path: Path) -> dict:
    data = json.loads(ledger_path.read_text())
    db = SessionLocal()
    stats = {"items": 0, "notes": 0}
    try:
        now = data.get("now")
        if now:
            item = svc.upsert_synced_item(db, PROJECT, now["id"], "now", {"title": now["title"], "branch": now.get("branch"), "started": now.get("started")})
            stats["items"] += 1
            stats["notes"] += _sync_notes(db, item, now.get("notes"))
        for i, nx in enumerate(data.get("next", []), start=1):
            item = svc.upsert_synced_item(db, PROJECT, nx["id"], "next", {"title": nx["title"], "priority": nx.get("priority", i)})
            stats["items"] += 1
            stats["notes"] += _sync_notes(db, item, nx.get("notes"))
        for t in data.get("tangents", []):
            item = svc.upsert_synced_item(db, PROJECT, t["id"], "tangents", {"title": t["title"], "status": t.get("status", "parked"), "captured": t.get("captured")})
            stats["items"] += 1
            stats["notes"] += _sync_notes(db, item, t.get("notes"))
        for f in data.get("findings", []):
            item = svc.upsert_synced_item(db, PROJECT, f["id"], "findings", {"title": f["title"], "status": f.get("status", "open"), "severity": f.get("severity", "advisory"), "source": f.get("source")})
            stats["items"] += 1
            stats["notes"] += _sync_notes(db, item, f.get("notes"))
    finally:
        db.close()
    return stats


def main() -> None:
    ap = argparse.ArgumentParser(description="Sync the Atlas pilot ledger into the work board (one-way, idempotent).")
    ap.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER, help="Path to ledger.json")
    args = ap.parse_args()
    stats = sync(args.ledger)
    print(f"Synced {stats['items']} items, appended {stats['notes']} new notes from {args.ledger}")


if __name__ == "__main__":
    main()
```

Before finalizing: open `docs/pilot/ledger.json`'s `tangents` and `findings` arrays in the Atlas repo (read-only) and confirm the field names used above (`status`, `captured`, `severity`, `source`) match; adjust the two loops if the ledger uses different keys. The `now`/`next` shapes are confirmed (`id`, `title`, `branch`, `started`, `priority`, `notes[].at/.text`).

- [ ] **Step 7: Dry-run the CLI against the real ledger (real DB, additive-only)**

```bash
cd /Users/chadkuisel/Workspace/mcp-context-forge && python tools/ledger_sync/sync_atlas_ledger.py 2>&1 | tail -3
python tools/ledger_sync/sync_atlas_ledger.py 2>&1 | tail -3
```

Expected: first run prints `Synced N items, appended M new notes ...`; the second run prints the same N with `appended 0 new notes` (idempotency proof). If the DB isn't migrated yet, run `alembic upgrade head` first.

- [ ] **Step 8: Lint and commit**

```bash
make pre-commit 2>&1 | tail -10
git add mcpgateway/services/work_board_service.py tools/ledger_sync/sync_atlas_ledger.py tests/unit/mcpgateway/services/test_work_board_service.py
git commit -s -m "feat: idempotent atlas ledger sync into work board"
```

---

### Task 6: Sync contract doc + verification gate

**Files:**
- Create: `docs/work-board-sync-contract.md`

**Interfaces:**
- Consumes: everything above
- Produces: the written contract agents on every project follow

- [ ] **Step 1: Write the contract**

Create `docs/work-board-sync-contract.md`:

```markdown
# Work Board Sync Contract

**Board = source of truth for the cross-project view.** Questions like "what is my NOW
across projects?" and "what needs attention anywhere?" are answered ONLY by the board
(`GET /board`, `GET /board/backlog`, or their MCP tools) — never by stitching files.

**Per-project write surfaces:**
- `gateway` project: write directly to the board (API/MCP/admin UI).
- `atlas-copilot` project: the Atlas pilot ledger (`docs/pilot/ledger.json`) remains the
  local write surface. It is mirrored one-way into the board by
  `tools/ledger_sync/sync_atlas_ledger.py`. Run the sync after every `pilot:refresh`.
  Never write atlas items directly on the board; they will be overwritten by the mirror.
- Any new project: write directly to the board with its own `project` value, or add a
  sync following the same namespaced-id (`<project>:<external-id>`) pattern.

**Invariants:** single NOW per project; NEXT cap per project (interactive writes only —
mirrored projects inherit their tracker's discipline); mirrored notes arrive as
author="agent" commentary and never move the attention state machine; sync never
writes back to the source tracker.

**Tangent rule (all projects):** mid-session ideas go to the board's `tangents` lane
(or the Atlas tangent lane, which mirrors in) — not into code.
```

- [ ] **Step 2: Verification gate (manual, report results — do not fix findings in this plan)**

Run and record pass/fail for each:

```bash
python -m pytest tests/unit/mcpgateway/services/test_work_board_service.py tests/unit/mcpgateway/routers/test_work_board_router.py -q 2>&1 | tail -3
alembic upgrade head 2>&1 | tail -2
python tools/ledger_sync/sync_atlas_ledger.py 2>&1 | tail -2
```

Then `make dev`, open the admin Work Board tab, and confirm: atlas items visible, per-item notes render, a manual note on a gateway item still flips it to `needs_attention`. Any defect found: file it as a board `findings` item — do not fix inline.

- [ ] **Step 3: Commit**

```bash
git add docs/work-board-sync-contract.md
git commit -s -m "docs: work board sync contract and verification gate"
```

---

### Task 7: Evict scope-creep directories from the fork

**Files:**
- Delete (from this repo): `tools/desktop-mcp-sync/`, `ai_config_files_reference/`

**Interfaces:**
- Consumes: nothing
- Produces: nothing — pure divergence reduction per the Task 1 merge policy

- [ ] **Step 1: Confirm nothing in the gateway imports them**

```bash
cd /Users/chadkuisel/Workspace/mcp-context-forge
grep -rl "desktop-mcp-sync\|desktop_mcp_sync\|ai_config_files_reference" mcpgateway/ tests/ Makefile docker-compose.yml 2>/dev/null | head -5
```

Expected: no output. If ANY file matches, STOP and report (two-strike rule) — do not evict.

- [ ] **Step 2: Copy out, then remove from the fork**

```bash
mkdir -p ~/Workspace/desktop-mcp-sync ~/Workspace/ai-config-reference
cp -R tools/desktop-mcp-sync/. ~/Workspace/desktop-mcp-sync/
cp -R ai_config_files_reference/. ~/Workspace/ai-config-reference/
git rm -r -q tools/desktop-mcp-sync ai_config_files_reference
git status --short | head -10
```

Expected: only deletions listed. The copies are plain folders; initializing them as repos is the operator's call, not this plan's.

- [ ] **Step 3: Commit**

```bash
git commit -s -m "chore: move desktop-mcp-sync and ai config reference out of the fork"
```

---

## Self-review record

- Spec coverage: architect actions 1 (Task 5 + Tasks 2–4 prerequisites), 2 (Task 1), 3 (Task 6 Step 2), 5 (Task 7), 6 (Task 6 Step 1) — action 4 (SQLite/launchd ops collapse) intentionally out of scope: machine-level configuration, no repo deliverable.
- Types: `project: str = "gateway"` consistent across model/service/router/sync; `Optional[str] = None` on all read filters; `upsert_synced_item` signature identical in Task 5 interface, implementation, and tests.
- Known unknowns are bounded with in-task verify steps (index kwargs in Task 2 Step 7, router test fixtures in Task 4 Step 1, SessionLocal name in Task 5 Step 5, ledger tangent/finding keys in Task 5 Step 6) — each names the exact command and the fallback.
