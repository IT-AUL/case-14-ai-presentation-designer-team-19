from deckdna.research.web import WebResearch


def test_no_new_facts_has_explicit_status():
    result = WebResearch(queries=["query"], pages_fetched=2).to_dict()
    assert result["added_facts"] == 0
    assert result["status"] == "no_new_facts"
    assert result["reason"] == "no_new_facts"


def test_disabled_and_added_statuses():
    assert WebResearch(degraded="web_disabled").to_dict()["status"] == "skipped"
    added = WebResearch(facts_kept=3).to_dict()
    assert added["status"] == "added" and added["added_facts"] == 3
    assert added["reason"] is None
