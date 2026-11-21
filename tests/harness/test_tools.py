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
from neuro_grounded_qa.contracts import InMemoryEvidenceStore, retrieved_ids
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
    _rollout_a, rollout_b = FakeStateChannel(ts), None

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


# -- relation summaries in notes ---------------------------------------------------

GENE_A_SUMMARY = (
    "This entity's relation labels, with edge counts: associated_with (1), "
    "interacts_with (1), member_of (1), targets (1)."
)
DIS_1_SUMMARY = (
    "This entity's relation labels, with edge counts: associated_with (2), "
    "has_phenotype (1), indicated_for (1)."
)


def test_unknown_relation_note_lists_the_entitys_labels_with_counts():
    payload = parse(run(make_toolset().get_neighbors("GENE_A", "has phenotype")))
    assert payload.evidence == [] and payload.truncated is False
    assert payload.note == (
        f"Unknown relation 'has phenotype'. {GENE_A_SUMMARY} "
        "Pass one of these labels exactly as written."
    )
    # Only the entity's own labels, not the whole vocabulary.
    assert "has_phenotype" not in payload.note and "subtype_of" not in payload.note


def test_unknown_relation_on_an_entity_with_no_edges():
    store = InMemoryEvidenceStore.from_records(
        {
            "entities": [
                {"id": "LONE", "name": "Lone", "type": "gene"},
                {"id": "A", "name": "A", "type": "gene"},
                {"id": "B", "name": "B", "type": "gene"},
            ],
            "edges": [{"id": "E:000001", "subject": "A", "relation": "links", "object": "B"}],
        }
    )
    note = parse(run(make_toolset(store).get_neighbors("LONE", "nope"))).note
    assert note == "Unknown relation 'nope'. This entity has no edges."


def test_summary_orders_by_count_then_label():
    """DIS_1 has associated_with twice and two labels once each: the higher count comes
    first, and the tie is broken alphabetically."""
    ts = make_toolset()
    assert ts.relation_counts("DIS_1") == [
        ("associated_with", 2),
        ("has_phenotype", 1),
        ("indicated_for", 1),
    ]
    assert ts.relation_summary("DIS_1") == DIS_1_SUMMARY


def test_summary_counts_edges_in_both_directions():
    """A count is what get_neighbors with that label lists in full: edges where the
    entity is subject and where it is object (DRUG_X targets GENE_A)."""
    store = fixture_store()
    ts = make_toolset(store)
    for label, n in ts.relation_counts("GENE_A"):
        assert len(store.neighbors("GENE_A", label, 100)) == n


def test_truncated_unfiltered_neighbors_note_lists_the_labels():
    payload = parse(run(make_toolset(max_results=2).get_neighbors("DIS_1")))
    assert len(payload.evidence) == 2 and payload.truncated is True
    assert payload.note == (
        f"Showing 2 results; more exist. Pass a relation to narrow the list. {DIS_1_SUMMARY}"
    )


def test_untruncated_neighbors_have_no_label_list():
    payload = parse(run(make_toolset().get_neighbors("DIS_1")))
    assert len(payload.evidence) == 4 and payload.truncated is False
    assert payload.note is None


def test_truncated_filtered_neighbors_have_no_label_list():
    """With a relation given, the model has already chosen; the list would be noise."""
    payload = parse(run(make_toolset(max_results=1).get_neighbors("DIS_1", "associated_with")))
    assert payload.truncated is True
    assert "edge counts" not in payload.note


# -- filtered truncation counts ------------------------------------------------------


def test_truncated_filtered_neighbors_note_gives_shown_of_total():
    """DIS_1 has 2 associated_with edges; with a cap of 1 the note says 1 of 2."""
    payload = parse(run(make_toolset(max_results=1).get_neighbors("DIS_1", "associated_with")))
    assert len(payload.evidence) == 1 and payload.truncated is True
    assert payload.note == "Showing 1 of 2 edges with relation 'associated_with'; more exist."


def test_filtered_total_matches_the_relation_count_and_the_store():
    """The total is the entity's whole count for the relation, as a full filtered
    listing would return it, not the number left after this page."""
    ts = make_toolset(wide_store(25))
    payload = parse(run(ts.get_neighbors("HUB", "links")))
    assert len(payload.evidence) == 20
    assert len(ts.store.neighbors("HUB", "links", 100)) == 25
    assert payload.note == "Showing 20 of 25 edges with relation 'links'; more exist."


def test_untruncated_filtered_neighbors_carry_no_count():
    ts = make_toolset()
    for entity, relation in (("DIS_1", "associated_with"), ("GENE_A", "member_of")):
        payload = parse(run(ts.get_neighbors(entity, relation)))
        assert payload.evidence and payload.truncated is False
        assert payload.note is None
    # Exactly at the cap is not truncated either.
    payload = parse(run(make_toolset(wide_store(20)).get_neighbors("HUB", "links")))
    assert len(payload.evidence) == 20 and payload.note is None


def test_paged_truncated_filtered_note_has_total_and_offset():
    ts = make_toolset(wide_store(25), paginate=True, max_results=10)
    page1 = parse(run(ts.get_neighbors_paged("HUB", "links")))
    page2 = parse(run(ts.get_neighbors_paged("HUB", "links", 10)))
    page3 = parse(run(ts.get_neighbors_paged("HUB", "links", 20)))
    assert page1.note == (
        "Showing 10 of 25 edges with relation 'links'; more exist. "
        "Call again with offset=10 for more."
    )
    assert page2.note == (
        "Showing 10 of 25 edges with relation 'links'; more exist. "
        "Call again with offset=20 for more."
    )
    assert len(page3.evidence) == 5 and page3.truncated is False and page3.note is None


def test_size_truncated_filtered_note_counts_what_fits():
    """Cut by payload size: "shown" is what fit, the total is still the entity's."""
    ts = make_toolset(wide_store(20, name_len=400), max_payload_bytes=4_000)
    out = run(ts.get_neighbors("HUB", "links"))
    assert len(out.encode()) <= 4_000
    payload = parse(out)
    assert 0 < len(payload.evidence) < 20 and payload.truncated is True
    assert payload.note == (
        f"Showing {len(payload.evidence)} of 20 edges with relation 'links'; more exist."
    )


def test_filtered_count_note_stays_under_the_payload_cap():
    """A very long real label, many edges of it and the smallest allowed cap: the
    echoed label is cut and the payload fits."""
    long_label = "long_label_" * 20  # 220 chars: longer than the echo limit
    entities = [{"id": "HUB", "name": "Hub", "type": "gene"}] + [
        {"id": f"L{i}", "name": f"Leaf {i}", "type": "gene"} for i in range(1, 31)
    ]
    edges = [
        {"id": f"E:{i:06d}", "subject": "HUB", "relation": long_label, "object": f"L{i}"}
        for i in range(1, 31)
    ]
    store = InMemoryEvidenceStore.from_records({"entities": entities, "edges": edges})
    ts = make_toolset(store, max_payload_bytes=1_000)
    out = run(ts.get_neighbors("HUB", long_label))
    assert len(out.encode()) <= 1_000
    payload = parse(out)
    assert 0 < len(payload.evidence) < 20 and payload.truncated is True
    assert payload.note.startswith(f"Showing {len(payload.evidence)} of 30 edges with relation '")
    assert "'...; more exist." in payload.note


def test_other_truncation_notes_are_unchanged():
    """Unfiltered pages and search keep their wording: no "of N"."""
    unfiltered = parse(run(make_toolset(max_results=2).get_neighbors("DIS_1"))).note
    assert unfiltered.startswith("Showing 2 results; more exist.")
    search = parse(run(make_toolset(wide_store(30), max_results=5).search_entities("leaf"))).note
    assert search == "Showing 5 results; more exist. Use a more specific name."


def test_size_truncated_unfiltered_neighbors_also_list_the_labels():
    """Cut by payload size rather than by count: still truncated, still listed."""
    ts = make_toolset(wide_store(20, name_len=400), max_payload_bytes=4_000)
    payload = parse(run(ts.get_neighbors("HUB")))
    assert payload.truncated is True
    assert payload.note.endswith("This entity's relation labels, with edge counts: links (20).")


def test_paged_truncated_unfiltered_note_has_offset_and_labels():
    payload = parse(run(make_toolset(max_results=2, paginate=True).get_neighbors_paged("DIS_1")))
    assert payload.note == (
        f"Showing 2 results; more exist. Call again with offset=2 for more. {DIS_1_SUMMARY}"
    )


def many_relations_store(
    n_relations: int, label_len: int = 20, other_label: str | None = None
) -> InMemoryEvidenceStore:
    """A hub ``HUB`` with edges of ``n_relations`` labels: label ``i`` (0-based) has
    ``i + 1`` edges, so counts differ and the order is known. Labels are padded to
    ``label_len`` characters to push note size up. ``other_label``, if given, is added
    on one edge between two leaves: a real label that ``HUB`` has no edges for."""
    entities = [{"id": "HUB", "name": "Hub", "type": "gene"}]
    edges = []
    k = 0
    for i in range(n_relations):
        label = f"rel_{i:02d}_".ljust(label_len, "x")
        for _ in range(i + 1):
            k += 1
            entities.append({"id": f"L{k}", "name": f"Leaf {k}", "type": "gene"})
            edges.append(
                {"id": f"E:{k:06d}", "subject": "HUB", "relation": label, "object": f"L{k}"}
            )
    if other_label is not None:
        edges.append({"id": "E:900000", "subject": "L1", "relation": other_label, "object": "L2"})
    return InMemoryEvidenceStore.from_records({"entities": entities, "edges": edges})


def test_summary_caps_the_list_and_says_how_many_more():
    ts = make_toolset(many_relations_store(6), max_relations_listed=3)
    summary = ts.relation_summary("HUB")
    labels = [label for label, _ in ts.relation_counts("HUB")]
    assert labels[:3] == [f"rel_0{i}_".ljust(20, "x") for i in (5, 4, 3)]
    assert summary == (
        "This entity's relation labels, with edge counts: "
        + ", ".join(f"{label} ({n})" for label, n in ts.relation_counts("HUB")[:3])
        + ", and 3 more."
    )


@pytest.mark.parametrize("relation", [None, "no such relation " * 200])
def test_relation_notes_stay_under_the_payload_cap(relation):
    """Many long labels and the smallest allowed cap: the summary shrinks to fit, and
    a huge model-supplied relation is cut in the echo."""
    ts = make_toolset(
        many_relations_store(30, label_len=60), max_payload_bytes=1_000, max_relations_listed=30
    )
    out = run(ts.get_neighbors("HUB", relation))
    assert len(out.encode()) <= 1_000
    payload = parse(out)
    assert "edge counts" in payload.note and "more" in payload.note
    if relation is None:
        assert payload.truncated is True
    else:
        assert payload.evidence == []


def test_default_cap_lists_every_relation_up_to_fifteen():
    ts = make_toolset(many_relations_store(15))
    assert "more" not in ts.relation_summary("HUB")
    assert ts.relation_summary("HUB").count("(") == 15


# -- a real label the entity has no edges for ----------------------------------------


def test_real_label_absent_for_the_entity_states_absence_then_lists_labels():
    """``has_phenotype`` is in the store's vocabulary but not on GENE_A's edges."""
    ts = make_toolset()
    assert "has_phenotype" in ts.relations
    payload = parse(run(ts.get_neighbors("GENE_A", "has_phenotype")))
    assert payload.evidence == [] and payload.truncated is False
    assert payload.note == (
        f"This entity has no edges with relation 'has_phenotype'. {GENE_A_SUMMARY} "
        "Pass one of these labels exactly as written."
    )
    assert "Unknown relation" not in payload.note


def test_absence_note_in_paged_mode_at_any_offset():
    ts = make_toolset(paginate=True)
    expected = parse(run(make_toolset().get_neighbors("GENE_A", "has_phenotype"))).note
    for offset in (0, 5):
        payload = parse(run(ts.get_neighbors_paged("GENE_A", "has_phenotype", offset)))
        assert payload.evidence == [] and payload.note == expected


def test_real_label_on_an_entity_with_no_edges():
    """GENE_D has no edges at all: the absence sentence, then no list."""
    payload = parse(run(make_toolset().get_neighbors("GENE_D", "targets")))
    assert payload.evidence == [] and payload.truncated is False
    assert payload.note == (
        "This entity has no edges with relation 'targets'. This entity has no edges."
    )


def test_results_with_edges_carry_no_absence_sentence():
    ts = make_toolset()
    small = make_toolset(max_results=1)
    for payload in (
        parse(run(ts.get_neighbors("GENE_A", "member_of"))),  # filtered, untruncated
        parse(run(small.get_neighbors("DIS_1", "associated_with"))),  # filtered, truncated
        parse(run(ts.get_neighbors("DIS_1"))),  # unfiltered
        parse(run(small.get_neighbors("DIS_1"))),  # unfiltered, truncated
    ):
        assert payload.evidence
        assert "no edges" not in (payload.note or "")


def test_other_empty_results_are_unchanged():
    """No relation on an entity with no edges, and a page past the end of a label the
    entity has, still say "No results."; an unknown label still gets the unknown note."""
    assert parse(run(make_toolset().get_neighbors("GENE_D"))).note == "No results."
    paged = make_toolset(paginate=True)
    assert parse(run(paged.get_neighbors_paged("GENE_A", "member_of", 5))).note == "No results."
    note = parse(run(make_toolset().get_neighbors("GENE_A", "has phenotype"))).note
    assert note.startswith("Unknown relation 'has phenotype'.") and "no edges with" not in note


def test_absence_note_stays_under_the_payload_cap():
    """Many long labels on the entity, a very long real label it lacks, and the
    smallest allowed cap: the summary shrinks and the echoed label is cut."""
    far = "elsewhere_" * 500  # a real label, but not on HUB
    store = many_relations_store(30, label_len=60, other_label=far)
    ts = make_toolset(store, max_payload_bytes=1_000, max_relations_listed=30)
    out = run(ts.get_neighbors("HUB", far))
    assert len(out.encode()) <= 1_000
    payload = parse(out)
    assert payload.evidence == []
    assert payload.note.startswith("This entity has no edges with relation 'elsewhere_")
    assert "..." in payload.note and "edge counts" in payload.note and "more" in payload.note


def test_long_ids_are_cut_in_error_notes():
    ts = make_toolset()
    for out in (run(ts.get_edge("E:" + "9" * 50_000)), run(ts.get_neighbors("X" * 50_000))):
        assert len(out.encode()) < 1_000
        assert "..." in parse(out).note


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
