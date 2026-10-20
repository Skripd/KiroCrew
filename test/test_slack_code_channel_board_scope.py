"""The workspace publishes only what the channel admits, up to the write itself.

Split from ``test_slack_code_channel_board_live`` (the file-line cap): the bar,
the Code tab and the resource re-check the board's scope and the channel's
activation after every await, right before each write. Shares that module's
fixtures and helpers (imported, so they apply here too).
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path

import pytest
import test_slack_code_channel_board as suite
import test_slack_code_channel_board_live as live

from kiro_crew.slack.code_channels import binding as cc_binding
from kiro_crew.slack.code_channels import board as board_pkg
from kiro_crew.slack.code_channels import surface, surface_store
from kiro_crew.slack.code_channels.board import model, publisher, source
from kiro_crew.slack.code_channels.workspace import service

# The board suite's fixtures (autouse ones included) and helpers, bound here so
# they apply to this module too.
_no_sandbox = suite._no_sandbox
_feature_on = suite._feature_on
_surface_store = suite._surface_store
publisher_clock = suite.publisher_clock
slack = suite.slack
StepClock = suite.StepClock
_Harness = suite._Harness
_Item = suite._Item
_SlackError = suite._SlackError
_NOW = suite._NOW
_binding = suite._binding
_link = suite._link
_publish = suite._publish
_reader = suite._reader
_row = suite._row
_snapshot = suite._snapshot


# The live module's fixtures and helpers, bound here so they apply here too.
_Slots = live._Slots
_bar_keys = live._bar_keys
_bar_labels = live._bar_labels
_live_slot = live._live_slot
_watched = live._watched
lineage = live.lineage
repo = live.repo
_waiting_board = live._waiting_board


# ── GPT round on 1e22f0268e (security class: publish only what the channel admits) ──


@pytest.mark.parametrize("where", ["C1", "C-origin"])
@pytest.mark.parametrize("mode", ["off", "review"])
def test_a_channel_or_origin_set_off_or_review_admits_no_board(monkeypatch, where, mode):
    """The activation gate is part of THE scope check, so every board path obeys it."""
    from kiro_crew.config.loader import ACTIVATION_OFF, ACTIVATION_REVIEW

    source.set_scope_predicate(lambda state, binding: True)  # the exemption admits it
    binding = replace(_binding(), origin_channel_id="C-origin")
    assert source.scope_admits(None, binding)
    activation = ACTIVATION_OFF if mode == "off" else ACTIVATION_REVIEW
    monkeypatch.setattr(
        cc_binding, "live_config", lambda orch=None: suite._LiveConfig({where: activation})
    )
    assert not source.scope_admits(None, binding)


def test_the_board_publish_honours_an_off_channel(monkeypatch):
    from kiro_crew.config.loader import ACTIVATION_OFF
    from kiro_crew.slack.code_channels.board import links

    sent: list = []

    async def fake_publish(binding, snapshot, *, link_for, owner_id="", admits=None):
        sent.append(snapshot)
        return "updated"

    monkeypatch.setattr(publisher, "publish", fake_publish)
    monkeypatch.setattr(links, "link_builder", lambda: _link)
    monkeypatch.setattr(surface, "dashboard_state", lambda: None)
    source.set_scope_predicate(lambda state, binding: True)
    monkeypatch.setattr(
        cc_binding, "live_config", lambda orch=None: suite._LiveConfig({"C1": ACTIVATION_OFF})
    )
    assert asyncio.run(board_pkg.publish(_binding(), _snapshot())) == "out_of_scope"
    assert sent == []


def test_a_scope_refused_during_the_diff_build_sends_no_worker_content(tmp_path, repo, monkeypatch):
    """The diff's git work takes seconds: the scope is judged again before it is sent."""
    tree = tmp_path / "wt-secret"
    suite._git("worktree", "add", "-q", "-b", "feat/secret", str(tree), "main", cwd=repo)
    (tree / "secret_plan.txt").write_text("worker content\n")
    suite._git("add", "secret_plan.txt", cwd=tree)
    suite._git("commit", "-q", "-m", "w", cwd=tree)
    board = model.BoardSnapshot(rows=(_row(title="Secret work", branch="feat/secret"),))
    h = _Harness(tmp_path, snapshot=board)
    binding = suite._FakeBinding(project_dir=str(repo))
    rec = asyncio.run(h.svc.ensure(binding))
    (Path(rec.worktree) / "README.md").write_text("channel change\n")
    real_build = h.svc._build_diff
    admitted = {"now": True}

    def build_then_share(record, board_arg=None):
        out = real_build(record, board_arg)
        if board_arg is not None:
            admitted["now"] = False  # the channel turned shared during the git work
        return out

    monkeypatch.setattr(h.svc, "_build_diff", build_then_share)
    source.set_scope_predicate(lambda state, b: admitted["now"])
    asyncio.run(h.svc.refresh(binding))
    content = h.client.last("set_diff_view")["content"]
    assert "README.md" in content
    assert "secret_plan.txt" not in content and "Secret work" not in content
    assert rec.key not in h.svc._boards


def test_worker_activity_refreshes_nothing_in_an_off_channel(tmp_path, repo, monkeypatch):
    """Off or Review: no bar, no diff tab, no board from a worker-triggered refresh."""
    from kiro_crew.config.loader import ACTIVATION_REVIEW

    h = _Harness(tmp_path, snapshot=model.BoardSnapshot(rows=(_row(title="Fix login"),)))
    binding = suite._FakeBinding(project_dir=str(repo))
    rec = asyncio.run(h.svc.ensure(binding))
    (Path(rec.worktree) / "README.md").write_text("a worker edited this\n")  # a new diff
    before = len(h.client.calls)
    monkeypatch.setattr(
        cc_binding,
        "live_config",
        lambda orch=None: suite._LiveConfig({"C1": ACTIVATION_REVIEW}),
    )
    asyncio.run(h.svc.refresh(binding))
    assert h.client.calls[before:] == [] and h.published == []


def test_the_watch_writes_no_worker_status_in_an_off_channel(tmp_path, monkeypatch, lineage):
    from kiro_crew.config.loader import ACTIVATION_OFF

    lineage.record_worker("chat-w1", "chat-cond")
    h, _rec, _scheduled, statuses = _watched(tmp_path, monkeypatch)
    monkeypatch.setattr(
        cc_binding, "live_config", lambda orch=None: suite._LiveConfig({"C1": ACTIVATION_OFF})
    )
    w1 = _live_slot("chat-w1")
    dash = _Slots(**{"chat-cond": _live_slot("chat-cond"), "chat-w1": w1})
    asyncio.run(h.svc.watch_tick(dash))
    w1.turn_running, w1._turn_generation = True, 1
    asyncio.run(h.svc.watch_tick(dash))
    assert statuses == []


def test_a_board_whose_scope_flips_mid_publish_sends_no_more(slack):
    """Asked again before each write: a refusal during the edit's retry posts nothing."""
    calls = {"n": 0}

    def admits():
        calls["n"] += 1
        return calls["n"] == 1  # admitted for the first write only

    waiting = model.BoardSnapshot(rows=(_row(state=model.WAITING_ON_YOU, report_stamp="r1"),))
    asyncio.run(
        publisher.publish(_binding(), waiting, link_for=_link, owner_id="U0", admits=admits)
    )
    assert len(slack.of("post_blocks")) == 1
    assert slack.of("post_message") == []  # the needs-you mention was re-checked and refused
    slack.fail["update_message"] = "message_not_found"
    calls["n"] = 0
    out = asyncio.run(
        publisher.publish(_binding(), _snapshot(), link_for=_link, owner_id="U0", admits=admits)
    )
    assert out == "out_of_scope" and len(slack.of("post_blocks")) == 1  # no repost


def test_the_publish_wrapper_hands_the_publisher_the_live_scope_check(monkeypatch):
    from kiro_crew.slack.code_channels.board import links

    seen: dict = {}

    async def fake_publish(binding, snapshot, *, link_for, owner_id="", admits=None):
        seen["admits"] = admits
        return "updated"

    monkeypatch.setattr(publisher, "publish", fake_publish)
    monkeypatch.setattr(links, "link_builder", lambda: _link)
    monkeypatch.setattr(surface, "dashboard_state", lambda: None)
    verdict = {"now": True}
    source.set_scope_predicate(lambda state, binding: verdict["now"])
    asyncio.run(board_pkg.publish(_binding(), _snapshot()))
    assert callable(seen["admits"]) and seen["admits"]()
    verdict["now"] = False  # the publisher's later re-checks see the change
    assert not seen["admits"]()


def test_a_bar_whose_scope_flips_during_its_git_reads_is_sent_without_workers(
    tmp_path, repo, monkeypatch
):
    h = _Harness(tmp_path)
    binding = suite._FakeBinding(project_dir=str(repo))
    rec = asyncio.run(h.svc.ensure(binding))
    h.svc._boards[rec.key] = model.BoardSnapshot(rows=(_row(title="Fix login"),))
    stored = h.svc.store.get("T1", "C1")
    stored.bar_hash = ""
    h.svc.store.put(stored)
    real = h.svc._bar_state
    admitted = {"now": True}

    async def read_then_share(record):
        out = await real(record)
        admitted["now"] = False  # shared during the bar's git reads
        return out

    monkeypatch.setattr(h.svc, "_bar_state", read_then_share)
    source.set_scope_predicate(lambda state, b: admitted["now"])
    asyncio.run(h.svc.republish_bar(binding))
    assert not any("Fix login" in label for label in _bar_labels(h))
    assert rec.key not in h.svc._boards


def test_a_diff_refusal_after_the_bar_went_out_resends_the_bar_without_workers(
    tmp_path, repo, monkeypatch
):
    tree = tmp_path / "wt-w"
    suite._git("worktree", "add", "-q", "-b", "feat/w", str(tree), "main", cwd=repo)
    (tree / "w.txt").write_text("w\n")
    suite._git("add", "w.txt", cwd=tree)
    suite._git("commit", "-q", "-m", "w", cwd=tree)
    board = model.BoardSnapshot(rows=(_row(title="Fix login", branch="feat/w"),))
    h = _Harness(tmp_path, snapshot=board)
    binding = suite._FakeBinding(project_dir=str(repo))
    asyncio.run(h.svc.ensure(binding))
    real_build = h.svc._build_diff
    admitted = {"now": True}

    def build_then_share(record, board_arg=None):
        out = real_build(record, board_arg)
        if board_arg is not None:
            admitted["now"] = False
        return out

    monkeypatch.setattr(h.svc, "_build_diff", build_then_share)
    source.set_scope_predicate(lambda state, b: admitted["now"])
    published_admitted: list[bool] = []
    real_publish = h.svc._publish_board

    async def publish_board(b, snap):
        published_admitted.append(admitted["now"])
        return await real_publish(b, snap)

    h.svc._publish_board = publish_board
    asyncio.run(h.svc.refresh(binding))
    assert "workers" not in _bar_keys(h)  # the bar re-sent after the refusal
    # The board is published first (the bar links it), while still admitted;
    # nothing goes out for the board after the refusal.
    assert published_admitted in ([], [True])
    assert h.svc.board("T1", "C1") is None


def test_a_failed_channel_only_rebuild_takes_the_worker_tab_down(tmp_path, repo, monkeypatch):
    h = _Harness(tmp_path, snapshot=model.BoardSnapshot(rows=(_row(title="Fix login"),)))
    binding = suite._FakeBinding(project_dir=str(repo))
    rec = asyncio.run(h.svc.ensure(binding))
    rec = h.svc.store.get("T1", "C1")
    rec.diff_hash, rec.view_id = "old", "V1"  # a combined tab is up
    h.svc._boards[rec.key] = model.BoardSnapshot(rows=(_row(title="Fix login"),))
    calls = {"n": 0}

    def build(record, board_arg=None):
        calls["n"] += 1
        return ("diff --git a/x b/x\n", "main", "w") if board_arg is not None else None

    monkeypatch.setattr(h.svc, "_build_diff", build)
    source.set_scope_predicate(lambda state, b: False)
    asyncio.run(h.svc._publish_diff(rec, binding))
    assert calls["n"] == 2
    assert h.client.last("remove_view") == {"view_id": "V1"}
    assert rec.view_id == "" and rec.diff_hash == ""


def _worker_tree(tmp_path, repo):
    tree = tmp_path / "wt-w"
    suite._git("worktree", "add", "-q", "-b", "feat/w", str(tree), "main", cwd=repo)
    (tree / "w.txt").write_text("worker change\n")
    suite._git("add", "w.txt", cwd=tree)
    suite._git("commit", "-q", "-m", "w", cwd=tree)
    return model.BoardSnapshot(rows=(_row(title="Fix login", branch="feat/w"),))


def _flip_at_the_write(h, admitted):
    """The scope changes the moment a write reaches for the client: after every
    check that ran before it, and before Slack sees anything."""
    client = h.client

    def at_write():
        admitted["now"] = False
        return client

    h.svc._client = at_write


def test_a_bar_whose_scope_flips_at_the_write_goes_out_without_workers(tmp_path, repo):
    board = model.BoardSnapshot(rows=(_row(title="Fix login", session_key="chat-w1"),))
    h = _Harness(tmp_path, snapshot=board)
    binding = suite._FakeBinding(project_dir=str(repo))
    rec = asyncio.run(h.svc.ensure(binding))
    h.svc._boards[rec.key] = board
    admitted = {"now": True}
    source.set_scope_predicate(lambda state, b: admitted["now"])
    _flip_at_the_write(h, admitted)
    stored = h.svc.store.get("T1", "C1")
    stored.bar_hash = ""
    h.svc._sent_bar.pop(rec.key, None)  # nothing sent yet: the resend is visible
    before = len([m for m, _ in h.client.calls if m == "set_context_bar"])
    asyncio.run(h.svc._publish_bar(stored, binding))
    sent = [kw["items"] for m, kw in h.client.calls if m == "set_context_bar"][before:]
    assert sent and all("workers" not in [i["key"] for i in items] for items in sent)
    assert rec.key not in h.svc._boards


def test_a_diff_whose_scope_flips_at_the_write_sends_only_the_channels_own(tmp_path, repo):
    board = _worker_tree(tmp_path, repo)
    h = _Harness(tmp_path, snapshot=board)
    binding = suite._FakeBinding(project_dir=str(repo))
    rec = asyncio.run(h.svc.ensure(binding))
    rec = h.svc.store.get("T1", "C1")
    (Path(rec.worktree) / "README.md").write_text("hello\nchannel edit\n")
    h.svc._boards[rec.key] = board
    admitted = {"now": True}
    source.set_scope_predicate(lambda state, b: admitted["now"])
    _flip_at_the_write(h, admitted)
    asyncio.run(h.svc._publish_diff(rec, binding))
    sent = [kw["content"] for m, kw in h.client.calls if m == "set_diff_view"]
    assert sent and all("worker change" not in c and "Fix login" not in c for c in sent)
    assert "+channel edit" in sent[-1]


def test_a_diff_resent_after_making_room_asks_the_scope_again(tmp_path, repo, monkeypatch):
    from kiro_crew.slack.code_channels import views
    from kiro_crew.slack.code_channels.models import CodeChannelApiError

    board = _worker_tree(tmp_path, repo)
    h = _Harness(tmp_path, snapshot=board)
    binding = suite._FakeBinding(project_dir=str(repo))
    rec = asyncio.run(h.svc.ensure(binding))
    rec = h.svc.store.get("T1", "C1")
    h.svc._boards[rec.key] = board
    admitted = {"now": True}
    source.set_scope_predicate(lambda state, b: admitted["now"])
    real_set = h.client.set_diff_view
    tries = {"n": 0}

    async def capped(channel_id, content, **kw):
        tries["n"] += 1
        if tries["n"] == 1:
            raise CodeChannelApiError("agents.conversations.setView", "too_many_views")
        return await real_set(channel_id, content, **kw)

    removal_admitted: list = []

    async def make_room(client, channel_id, *, for_key, admits=None):
        admitted["now"] = False  # the listViews round trip outlasts a change
        # The removal asks the write's fence too: it refuses now.
        removal_admitted.append((await admits()) if admits is not None else None)
        return "old"

    h.client.set_diff_view = capped
    monkeypatch.setattr(views, "make_room", make_room)
    asyncio.run(h.svc._publish_diff(rec, binding))
    sent = [kw["content"] for m, kw in h.client.calls if m == "set_diff_view"]
    assert tries["n"] == 1 and sent == []  # the resend with workers never went out
    assert removal_admitted == ["out_of_scope"]  # make_room was handed the fence
    assert rec.view_id == "" and rec.diff_hash == ""  # no channel diff of its own


def test_a_turn_starting_during_the_bars_last_await_keeps_its_stop(tmp_path, repo, monkeypatch):
    """GPT on 5c0d70574e: an older refresh's bar must not drop the Stop a turn that
    started during its last await needs (Stop is decided after that await)."""
    from kiro_crew.slack.code_channels import agent_bar

    h = _Harness(tmp_path)
    binding = suite._FakeBinding(project_dir=str(repo))
    rec = asyncio.run(h.svc.ensure(binding))
    real_items = agent_bar.agent_items

    def read_while_a_turn_starts(channel_id):
        h.svc._turning.add(rec.key)  # on_turn_start lands during the read
        return real_items(channel_id)

    monkeypatch.setattr(agent_bar, "agent_items", read_while_a_turn_starts)
    stored = h.svc.store.get("T1", "C1")
    asyncio.run(h.svc._publish_bar(stored, binding))
    assert "stop" in _bar_keys(h)


def test_slacks_stop_during_a_refresh_stops_every_workspace_write(tmp_path, repo, monkeypatch):
    """GPT on 5c0d70574e: Slack's own Stop lands while a refresh awaits its PR and
    git reads. No agents.* call may follow for the channel until its next turn --
    not the bar, the diff, the resource, nor a tab removal."""
    from kiro_crew.slack.code_channels import status

    h = _Harness(tmp_path)
    binding = suite._FakeBinding(project_dir=str(repo))
    rec = asyncio.run(h.svc.ensure(binding))
    (Path(rec.worktree) / "README.md").write_text("hello\nchannel edit\n")
    stored = h.svc.store.get("T1", "C1")
    stored.bar_hash, stored.view_id, stored.diff_hash = "", "V1", "old"
    stored.pr_number, stored.pr_url, stored.pr_state = 7, "https://github.com/a/b/pull/7", "open"
    h.svc.store.put(stored)
    h.svc._sent_bar.pop(rec.key, None)
    before = len(h.client.calls)

    async def pr_read_meanwhile_stopped(record):
        status.quiet_after_stop("C1")  # Slack's Stop arrives during the reads

    monkeypatch.setattr(h.svc, "_refresh_pr", pr_read_meanwhile_stopped)
    try:
        asyncio.run(h.svc.refresh(binding))
        assert h.client.calls[before:] == []
        # A removal is an agents.* call too.
        asyncio.run(h.svc._clear_diff(h.svc.store.get("T1", "C1")))
        assert h.client.calls[before:] == []
    finally:
        status._quiet.discard("C1")


def test_a_worker_tab_comes_down_when_the_channel_is_set_off_at_the_write(
    tmp_path, repo, monkeypatch
):
    """Shared AND Off at the last moment: not even the channel's own diff may go
    out, and the tab already up (it lists workers) is taken down."""
    board = _worker_tree(tmp_path, repo)
    h = _Harness(tmp_path, snapshot=board)
    binding = suite._FakeBinding(project_dir=str(repo))
    rec = asyncio.run(h.svc.ensure(binding))
    rec = h.svc.store.get("T1", "C1")
    (Path(rec.worktree) / "README.md").write_text("hello\nchannel edit\n")
    rec.diff_hash, rec.view_id = "old", "V1"  # a combined tab is up
    h.svc._boards[rec.key] = board
    admitted = {"now": True}
    source.set_scope_predicate(lambda state, b: admitted["now"])
    monkeypatch.setattr(service, "_activation_admits", lambda b: admitted["now"])
    _flip_at_the_write(h, admitted)
    asyncio.run(h.svc._publish_diff(rec, binding))
    assert [m for m, _ in h.client.calls if m == "set_diff_view"] == []
    assert h.client.last("remove_view") == {"view_id": "V1"}
    assert rec.view_id == "" and rec.diff_hash == ""


def test_a_bar_held_back_by_slacks_stop_answers_stopped(tmp_path, repo):
    from kiro_crew.slack.code_channels import status

    h = _Harness(tmp_path)
    binding = suite._FakeBinding(project_dir=str(repo))
    rec = asyncio.run(h.svc.ensure(binding))
    stored = h.svc.store.get("T1", "C1")
    stored.bar_hash = ""
    h.svc.store.put(stored)
    h.svc._sent_bar.pop(rec.key, None)
    client = h.client

    def at_write():
        status.quiet_after_stop("C1")  # Slack's Stop while the bar was built
        return client

    h.svc._client = at_write
    try:
        assert asyncio.run(h.svc.republish_bar(binding)) == "stopped"
    finally:
        status._quiet.discard("C1")


def test_a_bar_held_back_by_the_channel_going_off_answers_channel_off(tmp_path, repo, monkeypatch):
    h = _Harness(tmp_path)
    binding = suite._FakeBinding(project_dir=str(repo))
    rec = asyncio.run(h.svc.ensure(binding))
    stored = h.svc.store.get("T1", "C1")
    stored.bar_hash = ""
    h.svc.store.put(stored)
    h.svc._sent_bar.pop(rec.key, None)
    admitted = {"now": True}
    monkeypatch.setattr(service, "_activation_admits", lambda b: admitted["now"])
    _flip_at_the_write(h, admitted)
    assert asyncio.run(h.svc.republish_bar(binding)) == "channel_off"


def test_a_wrap_up_whose_scope_flips_before_the_post_leaves_the_workers_out(slack, monkeypatch):
    """GPT on 5da454ba22: the lock wait and the archive check can outlast a sharing change."""
    from kiro_crew.slack.code_channels import wrapup

    admitted = {"now": True}

    async def facts(binding):
        return wrapup.Facts(branch="feat/x", workers=("• Fix login: PR #12 merged",))

    async def archived_then_shared(binding):
        admitted["now"] = False  # Slack Connect recorded while the post waited
        return False

    async def no_store(channel, **fields):
        return None

    monkeypatch.setattr(wrapup, "gather_facts", facts)
    monkeypatch.setattr(surface, "is_archived", archived_then_shared)
    monkeypatch.setattr(surface, "channel_admits", lambda cfg, b: True)
    monkeypatch.setattr(surface, "audit", lambda *a, **k: None)
    monkeypatch.setattr(wrapup, "update_async", no_store)
    monkeypatch.setattr(surface, "dashboard_state", lambda: None)
    source.set_scope_predicate(lambda state, b: admitted["now"])
    posted: list = []

    async def post_blocks(channel, blocks, text, thread_ts=None):
        posted.append(json.dumps(blocks))
        return "300.1"

    monkeypatch.setattr(slack, "post_blocks", post_blocks)
    asyncio.run(wrapup.post_wrap_up(_binding(), "All done", caller="U0"))
    assert posted and "Fix login" not in posted[0] and "feat/x" in posted[0]


def test_the_conductors_turn_hooks_forget_the_watchs_status_record(tmp_path, monkeypatch):
    """The base's turn hooks hand the status to the conductor; the watch's record goes."""
    h = _Harness(tmp_path)
    monkeypatch.setattr(h.svc, "ensure", lambda binding: asyncio.sleep(0))
    monkeypatch.setattr(h.svc, "schedule_refresh", lambda binding, *, delay=0.0: None)
    key = "T1:C1"
    for hook in (h.svc.on_turn_start, h.svc.on_turn_end):
        h.svc._working[key] = "suspended"
        h.svc._status_failures[key] = 2
        h.svc._status_hold[key] = h.svc._clock() + 60
        asyncio.run(hook(_binding()))
        assert key not in h.svc._working
        assert key not in h.svc._status_failures and key not in h.svc._status_hold


# ── GPT on a061b43b5d: lineage-driven status obeys the board's publish scope ──


def _lineage_watch(tmp_path, monkeypatch, lineage):
    lineage.record_worker("chat-w1", "chat-cond")
    h, rec, _scheduled, statuses = _watched(tmp_path, monkeypatch)
    monkeypatch.setattr(service, "_sharing_unknown", lambda channel: False)  # confirmed
    w1 = _live_slot("chat-w1")
    cond = _live_slot("chat-cond")
    dash = _Slots(**{"chat-cond": cond, "chat-w1": w1})
    asyncio.run(h.svc.watch_tick(dash))
    return h, rec, statuses, w1, cond, dash


def test_a_refused_channel_gets_no_status_from_a_lineage_worker(tmp_path, monkeypatch, lineage):
    h, _rec, statuses, w1, _cond, dash = _lineage_watch(tmp_path, monkeypatch, lineage)
    source.set_scope_predicate(lambda state, b: False)  # Slack Connect-shared
    w1.turn_running, w1._turn_generation = True, 1
    asyncio.run(h.svc.watch_tick(dash))
    asyncio.run(h.svc.watch_tick(dash))
    assert statuses == []


def test_a_channel_refused_mid_run_hands_the_workers_status_back(tmp_path, monkeypatch, lineage):
    h, rec, statuses, w1, _cond, dash = _lineage_watch(tmp_path, monkeypatch, lineage)
    w1.turn_running, w1._turn_generation = True, 1
    asyncio.run(h.svc.watch_tick(dash))
    assert statuses == ["processing"]
    source.set_scope_predicate(lambda state, b: False)
    asyncio.run(h.svc.watch_tick(dash))
    assert statuses == ["processing", "active"] and rec.key not in h.svc._working
    asyncio.run(h.svc.watch_tick(dash))
    assert statuses == ["processing", "active"]  # once


def test_a_refused_channel_never_overrides_a_running_conductor(tmp_path, monkeypatch, lineage):
    h, _rec, statuses, w1, cond, dash = _lineage_watch(tmp_path, monkeypatch, lineage)
    w1.turn_running, w1._turn_generation = True, 1
    asyncio.run(h.svc.watch_tick(dash))
    assert statuses == ["processing"]
    cond.turn_running = True  # the conductor's own turn owns the status now
    source.set_scope_predicate(lambda state, b: False)
    asyncio.run(h.svc.watch_tick(dash))
    assert statuses == ["processing"]


def test_a_confirmed_refusal_ends_a_status_hold(tmp_path, monkeypatch, lineage):
    h, rec, statuses, w1, _cond, dash = _lineage_watch(tmp_path, monkeypatch, lineage)
    w1.turn_running, w1._turn_generation = True, 1
    asyncio.run(h.svc.watch_tick(dash))
    h.svc._status_hold[rec.key] = h.svc._clock() + 600  # held while sharing was unknown
    source.set_scope_predicate(lambda state, b: False)
    asyncio.run(h.svc.watch_tick(dash))
    assert rec.key not in h.svc._status_hold and statuses[-1] == "active"


def test_a_worker_filed_into_the_channel_folder_still_obeys_the_scope(
    tmp_path, monkeypatch, lineage
):
    """The board files lineage workers into the channel's folder: lineage still decides."""
    from kiro_crew.slack.code_channels.workspace import folders

    lineage.record_worker("chat-w1", "chat-cond")
    h, _rec, _scheduled, statuses = _watched(tmp_path, monkeypatch, folder_id="F-chan")
    monkeypatch.setattr(service, "_sharing_unknown", lambda channel: False)
    w1 = _live_slot("chat-w1")
    dash = _Slots(**{"chat-cond": _live_slot("chat-cond"), "chat-w1": w1})
    monkeypatch.setattr(folders, "subtree_ids", lambda state, fid: {fid})
    monkeypatch.setattr(
        folders,
        "sessions_in",
        lambda state, ids: {"chat-w1": (w1.turn_running, w1._turn_generation)},
    )
    asyncio.run(h.svc.watch_tick(dash))
    source.set_scope_predicate(lambda state, b: False)
    w1.turn_running, w1._turn_generation = True, 1
    asyncio.run(h.svc.watch_tick(dash))
    asyncio.run(h.svc.watch_tick(dash))
    assert statuses == []


def test_a_cached_waiting_row_sets_no_suspended_on_a_refused_channel(
    tmp_path, monkeypatch, lineage
):
    h, rec, _scheduled, statuses = _watched(tmp_path, monkeypatch)
    monkeypatch.setattr(service, "_sharing_unknown", lambda channel: False)
    dash = _waiting_board(h, rec, monkeypatch, lineage)
    # Admitted by the pass's first check, refused by the status check.
    verdicts = iter([True] + [False] * 10)
    source.set_scope_predicate(lambda state, b: next(verdicts))
    asyncio.run(h.svc.watch_tick(dash))
    assert "suspended" not in statuses and rec.key not in h.svc._boards


def test_the_board_is_not_pinned_once_the_scope_refuses(slack):
    _publish(_snapshot())
    pins = len(slack.of("add_pin"))
    surface_store.store().update("C1", board_pinned=False)  # someone unpinned it
    asyncio.run(
        publisher.publish(
            _binding(), _snapshot(), link_for=_link, owner_id="U0", admits=lambda: False
        )
    )
    assert len(slack.of("add_pin")) == pins
