"""A hosted model often sends one string where a list is asked for; that must not fail the whole reply."""

from __future__ import annotations

import json

import pytest

from app.agent import recommender
from app.agent.client import LLMError


def _candidate(**over):
    c = {"change_id": None, "category": "software", "area": "model", "title": "Resize input frames",
         "problem": "too slow", "rationale": "fewer pixels means less work",
         "affects": ["energy"], "tradeoffs": ["less detail"], "requires": ["a retest"]}  # fmt: skip
    return {**c, **over}


def test_string_where_a_list_is_expected_is_accepted():
    reply = recommender.parse(json.dumps({"summary": "s", "candidates": [
        _candidate(requires="Resize input frames to 512 before inference.", affects="energy", tradeoffs=None),
    ]}))  # fmt: skip
    c = reply.candidates[0]
    assert c.requires == ["Resize input frames to 512 before inference."]
    assert c.affects == ["energy"] and c.tradeoffs == []


def test_over_long_lists_and_texts_are_cut_not_rejected():
    reply = recommender.parse(json.dumps({"summary": "s", "candidates": [
        _candidate(requires=[f"step {i}" for i in range(10)], rationale="x" * 2000),
    ]}))  # fmt: skip
    assert len(reply.candidates[0].requires) == 6
    assert len(reply.candidates[0].rationale) == 900


def test_one_malformed_candidate_is_dropped_and_the_rest_kept():
    reply = recommender.parse(json.dumps({"summary": "s", "candidates": [
        _candidate(), {"category": "nonsense", "title": "x"},
    ]}))  # fmt: skip
    assert len(reply.candidates) == 1


def test_all_candidates_malformed_still_raises():
    with pytest.raises(LLMError):
        recommender.parse(json.dumps({"summary": "s", "candidates": [{"category": "nonsense"}]}))
