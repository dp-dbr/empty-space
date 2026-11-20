import pytest

from dp_agentic_collector.adapters.schema_validation import validate_schema
from dp_agentic_collector.extensions.interfaces import ValidationContext


@pytest.fixture
def st(app):
    return app.registries.subject_types.get("application")


def ctx(peers=None):
    return ValidationContext("t", "c", "s", peer_data=peers or {})


def run_rules(st, data, peers=None):
    return [v for r in st.rules() for v in r.check(data, ctx(peers))]


def test_registered_from_folder(app, st):
    assert st.schema_version == "1"
    assert set(st.supported_locales()) == {"en", "de", "fr", "it"}
    assert any(p.startswith("dir:") for p in app.registries.loaded_plugins)


def test_schema(st):
    ok = {"name": "CRM", "business_owner": "Sales", "criticality": "low", "number_of_users": 3}
    assert validate_schema(st.json_schema(), ok) == []
    bad = validate_schema(st.json_schema(), {"name": "CRM", "criticality": "extreme", "number_of_users": -1})
    assert {v.path for v in bad} >= {"criticality", "number_of_users", "$"}


def test_high_criticality_requires_technical_owner(st):
    data = {"name": "CRM", "business_owner": "Sales", "criticality": "high"}
    assert [v.rule for v in run_rules(st, data)] == ["high_criticality_requires_technical_owner"]
    assert run_rules(st, {**data, "technical_owner": "Team A"}) == []


def test_interfaces_must_reference_existing_applications(st):
    data = {"name": "CRM", "business_owner": "Sales", "criticality": "low", "interfaces": ["ERP", "Ghost"]}
    v = run_rules(st, data, peers={"erp": {"name": "ERP"}})
    assert [(x.rule, x.path) for x in v] == [("interfaces_reference_existing_applications", "interfaces[1]")]


@pytest.mark.parametrize("locale,word", [("en", "Criticality"), ("de", "Kritikalität"), ("fr", "Criticité"),
                                         ("it", "Criticità")])
def test_factsheet_localized(st, locale, word):
    body = st.render_factsheet({"name": "CRM", "criticality": "low"},
                               {"subject": {"name": "CRM"}, "changes": [], "previous_version": None,
                                "sources": [], "open_points": [], "rationale": []}, locale)
    assert word in body and "CRM" in body
