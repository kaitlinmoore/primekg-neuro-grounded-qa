"""The KG toolset: payload format, pass-through to the store, caps, budget, paging and
error payloads. Synthetic stores only."""

from __future__ import annotations

import asyncio
import json

import pytest
from mcp.server.mcpserver import MCPServer
from verifiers.v1.harnesses.utils.mcp import mcp_content_to_chat_content

from contracts.builders import TraceBuilder, call
from harness.helpers import (
    STORE_PATH,
    FakeStateChannel,
    fixture_store,
    make_toolset,
    parse,
    run,
    wide_store,
)
from neuro_grounded_qa.contracts import retrieved_ids
from neuro_grounded_qa.tools import KGToolsetConfig, load_store
from neuro_grounded_qa.tools.kg import DEFAULT_MAX_PAYLOAD_BYTES

# -- every tool returns a parseable payload ------------------------------------------


@pytest.mark.parametrize(
    "tool, kwargs",
    [
        ("search_entities", {"text": "gene"}),
        ("search_entities", {"text": "no such thing"}),
        ("get_neighbors", {"entity_id": "GENE_A"}),
        ("get_neighbors", {"entity_id": "GENE_A", "relation": "member_of"}),
        ("get_edge", {"edge_id": "E:000001"}),
        ("get_edge", {"edge_id": "E:999999"}),
    ],
)
def test_every_tool_output_parses_as_payload(tool, kwargs):
    ts = make_toolset()
    payload = parse(run(getattr(ts, tool)(**kwargs)))
    assert len(payload.evidence) <= ts.config.max_results
    assert len(payload.entities) <= ts.config.max_results


def test_search_passes_store_ranking_through():
    store = fixture_store()
    ts = make_toolset(store)
    payload = parse(run(ts.search_entities("gene")))
    assert payload.entities == store.search_entities("gene", 20)
    assert payload.evidence == []  # entities are never evidence
    assert payload.truncated is False


def test_search_exact_match_ranks_first():
    payload = parse(run(make_toolset().search_entities("drug x")))
    assert payload.entities[0].id == "DRUG_X"


def test_neighbors_pass_store_results_through():
    store = fixture_store()
    ts = make_toolset(store)
    assert parse(run(ts.get_neighbors("GENE_A"))).evidence == store.neighbors("GENE_A", None, 20)
    filtered = parse(run(ts.get_neighbors("GENE_A", "member_of"))).evidence
    assert [e.id for e in filtered] == ["E:000007"]


def test_get_edge_returns_the_edge_as_evidence():
    store = fixture_store()
    payload = parse(run(make_toolset(store).get_edge("E:000004")))
    assert payload.evidence == [store.get("E:000004")]


# -- caps ------------------------------------------------------------------------


def test_neighbors_capped_at_max_results_with_truncated_flag():
    ts = make_toolset(wide_store(25))
    payload = parse(run(ts.get_neighbors("HUB")))
    assert [e.id for e in payload.evidence] == [f"E:{i:06d}" for i in range(1, 21)]
    assert payload.truncated is True
    assert "more exist" in payload.note
    assert "relation" in payload.note  # tells the model how to narrow


def test_exactly_max_results_is_not_truncated():
    payload = parse(run(make_toolset(wide_store(20)).get_neighbors("HUB")))
    assert len(payload.evidence) == 20
    assert payload.truncated is False
    assert payload.note is None


def test_search_capped_with_truncated_flag():
    ts = make_toolset(wide_store(30), max_results=5)
    payload = parse(run(ts.search_entities("leaf")))
    assert len(payload.entities) == 5
    assert payload.truncated is True


def test_payload_size_cap_drops_results_until_it_fits():
    ts = make_toolset(wide_store(20, name_len=400), max_payload_bytes=4_000)
    out = run(ts.get_neighbors("HUB"))
    assert len(out.encode()) <= 4_000
    payload = parse(out)
    assert 0 < len(payload.evidence) < 20
    assert payload.truncated is True
    # The shown edges are still the first ones, in store order.
    assert [e.id for e in payload.evidence] == [
        f"E:{i:06d}" for i in range(1, len(payload.evidence) + 1)
    ]


def test_default_payload_cap_is_under_the_chat_harness_truncation_limit():
    from verifiers.v1.harnesses.utils.compaction import TOOL_OUTPUT_MAX_BYTES

    assert DEFAULT_MAX_PAYLOAD_BYTES < TOOL_OUTPUT_MAX_BYTES
    # A full page of long-named edges still comes back whole and parseable.
    out = run(make_toolset(wide_store(25, name_len=300)).get_neighbors("HUB"))
    assert len(out.encode()) <= DEFAULT_MAX_PAYLOAD_BYTES
    assert parse(out).truncated is True


# -- call budget -----------------------------------------------------------------


def test_budget_refuses_calls_past_max_calls():
    ts = make_toolset(max_calls=2)
    first = parse(run(ts.get_edge("E:000001")))
    second = parse(run(ts.search_entities("gene")))
    third = parse(run(ts.get_edge("E:000002")))
    assert first.evidence and second.entities
    assert third.evidence == [] and third.entities == []
    assert "budget" in third.note
    assert ts.state.kg_calls == 3  # refused calls are counted too


def test_failed_calls_count_against_the_budget():
    ts = make_toolset(max_calls=1)
    run(ts.get_edge("E:999999"))  # unknown ID, still a call
    assert "budget" in parse(run(ts.get_edge("E:000001"))).note


def test_zero_budget_refuses_everything():
    ts = make_toolset(max_calls=0)
    payload = parse(run(ts.get_edge("E:000001")))
    assert payload.evidence == [] and "budget" in payload.note


def test_budget_counts_per_rollout_through_the_state_channel():
    """Through the registered tools, the counter lives in the rollout's state (pulled
    and pushed around each call), not on the shared server object."""
    ts = make_toolset(max_calls=2)
    mcp = MCPServer("kg")
    ts.register(mcp)
    rollout_a, rollout_b = FakeStateChannel(ts), None

    async def call_edge():
        result = await mcp.call_tool("get_edge", {"edge_id": "E:000001"})
        return parse(mcp_content_to_chat_content(result.content))

    async def scenario():
        nonlocal rollout_b
        out_a = [await call_edge() for _ in range(3)]
        rollout_b = FakeStateChannel(ts)  # a second rollout starts from fresh state
        out_b = await call_edge()
        return out_a, out_b

    out_a, out_b = run(scenario())
    assert [bool(p.evidence) for p in out_a] == [True, True, False]
    assert out_b.evidence  # rollout B has its own budget
    assert rollout_b.state.kg_calls == 1


def test_parallel_calls_cannot_overrun_the_budget():
    """Concurrent calls of one rollout are serialized, so last-write-wins state sync
    can't let them all pass the check."""
    ts = make_toolset(max_calls=2)
    mcp = MCPServer("kg")
    ts.register(mcp)
    channel = FakeStateChannel(ts)

    async def burst():
        results = await asyncio.gather(
            *(mcp.call_tool("get_edge", {"edge_id": "E:000001"}) for _ in range(6))
        )
        return [parse(mcp_content_to_chat_content(r.content)) for r in results]

    payloads = run(burst())
    assert sum(bool(p.evidence) for p in payloads) == 2
    assert channel.state.kg_calls == 6


def test_without_the_lock_parallel_calls_would_overrun():
    """Documents why the lock exists: the library's state wrapper alone loses
    increments under concurrency."""
    ts = make_toolset(max_calls=2)
    channel = FakeStateChannel(ts)
    unlocked = ts._with_state(ts.get_edge)

    async def burst():
        return await asyncio.gather(*(unlocked(edge_id="E:000001") for _ in range(6)))

    payloads = [parse(s) for s in run(burst())]
    assert sum(bool(p.evidence) for p in payloads) > 2
    assert channel.state.kg_calls < 6


# -- pagination ------------------------------------------------------------------


def _schemas(ts) -> dict[str, dict]:
    mcp = MCPServer("kg")
    ts.register(mcp)
    return {t.name: t.input_schema for t in run(mcp.list_tools())}


def test_pagination_off_by_default():
    assert KGToolsetConfig().paginate is False
    schemas = _schemas(make_toolset())
    assert set(schemas) == {"search_entities", "get_neighbors", "get_edge"}
    assert "offset" not in schemas["get_neighbors"]["properties"]


def test_pagination_on_adds_offset_and_pages():
    ts = make_toolset(wide_store(25), paginate=True, max_results=10)
    schemas = _schemas(ts)
    assert set(schemas) == {"search_entities", "get_neighbors", "get_edge"}
    assert "offset" in schemas["get_neighbors"]["properties"]

    page1 = parse(run(ts.get_neighbors_paged("HUB")))
    page3 = parse(run(ts.get_neighbors_paged("HUB", offset=20)))
    assert [e.id for e in page1.evidence][0] == "E:000001"
    assert page1.truncated and "offset=10" in page1.note
    assert [e.id for e in page3.evidence] == [f"E:{i:06d}" for i in range(21, 26)]
    assert page3.truncated is False


def test_paged_offset_hint_follows_size_truncation():
    ts = make_toolset(wide_store(20, name_len=400), paginate=True, max_payload_bytes=4_000)
    payload = parse(run(ts.get_neighbors_paged("HUB")))
    assert f"offset={len(payload.evidence)}" in payload.note


# -- errors come back as payloads ------------------------------------------------


@pytest.mark.parametrize(
    "tool, kwargs, fragment",
    [
        ("get_neighbors", {"entity_id": "NOPE"}, "Unknown entity ID"),
        ("get_neighbors", {"entity_id": "GENE_A", "relation": "causes"}, "Unknown relation"),
        ("get_edge", {"edge_id": "E:999999"}, "No edge"),
        ("get_edge", {"edge_id": "not an id"}, "No edge"),
        ("search_entities", {"text": "   "}, "Empty search"),
        ("get_neighbors_paged", {"entity_id": "GENE_A", "offset": -1}, "offset"),
    ],
)
def test_errors_are_payloads_with_a_note(tool, kwargs, fragment):
    payload = parse(run(getattr(make_toolset(), tool)(**kwargs)))
    assert payload.evidence == [] and payload.entities == []
    assert fragment in payload.note


def test_unknown_relation_note_lists_the_vocabulary():
    note = parse(run(make_toolset().get_neighbors("GENE_A", "causes"))).note
    assert "member_of" in note and "targets" in note


def test_unexpected_exception_becomes_a_note():
    class BrokenStore:
        build_id = "broken"

        def search_entities(self, text, limit):
            raise RuntimeError("boom")

    payload = parse(run(make_toolset(BrokenStore()).search_entities("x")))
    assert payload.evidence == [] and "Internal tool error" in payload.note


def test_error_note_ids_never_count_as_retrieved():
    """An ID echoed in an error note isn't evidence: only the payload's evidence list
    is read."""
    out = run(make_toolset().get_edge("E:000001x"))
    b = TraceBuilder()
    b.user("q")
    b.assistant(calls=[call("c1", "kg_get_edge", edge_id="E:000001x")])
    b.tool("c1", out, name="kg_get_edge")
    assert retrieved_ids(b.build()) == set()


# -- through MCP into a trace ----------------------------------------------------


def test_mcp_result_lands_in_a_trace_as_retrieved_evidence():
    """The text the chat harness puts in the tool message is exactly the payload, and
    the strict retrieval rule reads its evidence IDs."""
    ts = make_toolset()
    mcp = MCPServer("kg")
    ts.register(mcp)
    result = run(mcp.call_tool("get_neighbors", {"entity_id": "DIS_1", "relation": "associated_with"}))
    content = mcp_content_to_chat_content(result.content)
    assert isinstance(content, str) and not result.is_error

    b = TraceBuilder()
    b.user("q")
    b.assistant(calls=[call("c1", "kg_get_neighbors", entity_id="DIS_1")])
    b.tool("c1", content, name="kg_get_neighbors")
    assert retrieved_ids(b.build()) == {"E:000001", "E:000002"}


# -- store loading ---------------------------------------------------------------


def test_load_store_reads_the_fixture_records_file():
    store = load_store(STORE_PATH)
    assert store.build_id == "fixture-mini-store-v1"
    assert store.get("E:000001") == fixture_store().get("E:000001")


def test_toolset_loads_its_store_from_config_in_setup():
    from neuro_grounded_qa.tools import KGToolset

    ts = KGToolset(KGToolsetConfig(store_path=STORE_PATH))
    run(ts.setup())
    assert ts.store.build_id == "fixture-mini-store-v1"


def test_load_store_errors(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_store(tmp_path / "missing.json")
    bad_format = tmp_path / "store.parquet"
    bad_format.write_bytes(b"")
    with pytest.raises(ValueError, match="unknown evidence store format"):
        load_store(bad_format)
    not_object = tmp_path / "store.json"
    not_object.write_text(json.dumps([1, 2]))
    with pytest.raises(ValueError, match="JSON object"):
        load_store(not_object)
