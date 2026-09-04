"""The AI Cloud Architect.

Three groups of test, in order of what they protect:

1. **Invariants.** The assistant is read-only, never invents a resource, and
   works with no language model configured. These are the properties that make
   it safe to put in front of a user at all.
2. **Routing.** Intent is decided by pattern, so the same question must route
   the same way every time. A router that drifted would make every downstream
   guarantee conditional.
3. **Answers.** Each supported question actually gets answered from the right
   engine, and says which one.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.engine.architect import (
    COMPARISONS,
    Architect,
    Intent,
    Source,
    classify,
)
from app.engine.pipeline import Pipeline
from app.main import app
from app.models.ir import Kind

PIPELINE = Pipeline()
ARCHITECT = Architect()

THREE_TIER = (
    "a production three-tier web app in eu-west-1 with an auto scaling group "
    "behind a load balancer, a Multi-AZ PostgreSQL database in private "
    "subnets, a Redis cache, an S3 bucket and a NAT gateway"
)


@pytest.fixture(scope="module")
def design():
    return PIPELINE.run(THREE_TIER)


def ask(question: str, result):
    return ARCHITECT.ask(question, result)


# ==========================================================================
# 1. invariants
# ==========================================================================

def test_asking_never_mutates_the_spec(design):
    """The assistant explains; it does not design."""
    before = design.spec.model_dump(mode="json")
    for question in [
        "why was the NAT gateway added?",
        "reduce cost",
        "improve security",
        "explain the architecture",
        "compare EC2 vs ECS",
        "show me the terraform",
        "how do I deploy this?",
    ]:
        ask(question, design)
    assert design.spec.model_dump(mode="json") == before


def test_asking_never_changes_the_terraform(design):
    before = dict(design.terraform)
    ask("improve security and reduce cost", design)
    assert design.terraform == before


def test_the_module_imports_nothing_that_could_change_a_design():
    """Read-only by construction, not by discipline."""
    import app.engine.architect as module

    source = module.__file__
    with open(source, encoding="utf-8") as handle:
        text = handle.read()
    for forbidden in ("ResourceMapper", "TerraformGenerator", "RuleExtractor",
                      "LLMExtractor", "Optimizer("):
        assert forbidden not in text, f"architect imports {forbidden}"


@pytest.mark.parametrize("question", [
    "why was the NAT gateway added?",
    "why is there no WAF?",
    "reduce cost",
    "improve security",
    "explain the networking",
    "explain the security groups",
    "explain the architecture",
    "what does validation say?",
    "how much does this cost?",
    "compare EC2 vs ECS",
    "how do I deploy this?",
])
def test_answers_only_reference_resources_that_exist(question, design):
    """A hallucinated resource id would be the worst failure this can have."""
    answer = ask(question, design)
    known = {r.id for r in design.spec.resources}
    assert set(answer.resources) <= known, answer.resources


def test_the_assistant_works_with_no_language_model(design):
    """llm_available is false in the default deployment, so this is the norm."""
    assert ARCHITECT.llm is None
    for question in [
        "why was the load balancer added?",
        "reduce cost", "improve security", "explain the architecture",
        "compare RDS vs Aurora", "explain validation", "what does it cost?",
    ]:
        answer = ask(question, design)
        assert answer.text.strip(), question
        assert answer.deterministic, question
        assert Source.LLM.value not in answer.sources, question


def test_a_general_question_declines_rather_than_guessing(design):
    """Without a model, saying nothing is better than inventing something."""
    answer = ask("what is AWS in general?", design)
    assert answer.deterministic
    assert "not configured" in answer.text.lower() or "guess" in answer.text.lower()


def test_a_failing_model_does_not_break_the_assistant(design):
    class Broken:
        available = True

        def answer(self, question, spec):
            raise RuntimeError("model unavailable")

    architect = Architect(llm=Broken())
    answer = architect.ask("explain cloud computing in general", design)
    assert answer.text.strip(), "a model failure must not produce an empty answer"


def test_a_model_answer_is_labelled_as_one(design):
    class Stub:
        available = True

        def answer(self, question, spec):
            return "A general explanation."

    architect = Architect(llm=Stub())
    answer = architect.ask("teach me about cloud computing in general", design)
    assert not answer.deterministic
    assert Source.LLM.value in answer.sources


# ==========================================================================
# 2. routing
# ==========================================================================

@pytest.mark.parametrize("question,expected", [
    ("why was the NAT gateway added?", Intent.EXPLANATION),
    ("why is the database private?", Intent.NETWORKING),
    ("what policy added this?", Intent.EXPLANATION),
    ("what triggered this dependency?", Intent.EXPLANATION),
    ("reduce cost", Intent.OPTIMIZATION),
    ("how can I lower the monthly cost?", Intent.OPTIMIZATION),
    ("improve security", Intent.OPTIMIZATION),
    ("suggest improvements", Intent.OPTIMIZATION),
    ("make this production-ready", Intent.OPTIMIZATION),
    ("compare EC2 vs ECS", Intent.COMPARISON),
    ("ECS versus EKS", Intent.COMPARISON),
    ("what are the alternatives?", Intent.COMPARISON),
    ("explain the validation findings", Intent.VALIDATION),
    ("what errors are there?", Intent.VALIDATION),
    ("how much does this cost?", Intent.COST),
    ("show me the terraform", Intent.TERRAFORM),
    ("explain the networking", Intent.NETWORKING),
    ("explain routing", Intent.NETWORKING),
    ("explain the security groups", Intent.SECURITY),
    ("explain IAM roles", Intent.SECURITY),
    ("explain the architecture", Intent.ARCHITECTURE),
    ("give me an overview", Intent.ARCHITECTURE),
    ("how do I deploy this?", Intent.DEPLOYMENT),
])
def test_questions_route_to_the_right_module(question, expected):
    assert classify(question) is expected, question


def test_routing_is_deterministic():
    """Same question, same route — every time, with no model involved."""
    question = "how can I reduce the cost of this architecture?"
    assert len({classify(question) for _ in range(50)}) == 1


def test_an_empty_question_is_handled(design):
    answer = ask("", design)
    assert answer.text.strip()
    assert answer.follow_ups, "an empty question should offer somewhere to start"


# ==========================================================================
# 3. answers come from the right engine
# ==========================================================================

def test_why_a_dependency_exists_cites_the_policy_rule(design):
    answer = ask("why was the internet gateway added?", design)
    assert "policy." in answer.text, "the rule that added it must be named"
    assert Source.TRACE.value in answer.sources
    assert answer.deterministic


def test_why_a_requested_resource_exists_quotes_the_prompt(design):
    answer = ask("why is there a redis cache?", design)
    assert "asked for" in answer.text.lower()
    assert Source.SPEC.value in answer.sources


def test_why_something_is_absent_explains_the_generation_rule(design):
    answer = ask("why is there no WAF?", design)
    assert "did not ask" in answer.text.lower() or "excluded" in answer.text.lower()
    assert answer.deterministic


def test_an_explicit_refusal_is_reported_as_one():
    result = PIPELINE.run("two web servers without a load balancer")
    answer = ask("why is there no load balancer?", result)
    assert "excluded" in answer.text.lower()
    assert "without" in answer.text


def test_reduce_cost_returns_costed_recommendations(design):
    answer = ask("how do I reduce cost?", design)
    assert Source.OPTIMIZER.value in answer.sources
    assert answer.recommendations, "a cost question must return the actual suggestions"
    assert any(r["monthly_delta_usd"] < 0 for r in answer.recommendations)


def test_recommendations_carry_priority_saving_and_difficulty(design):
    answer = ask("suggest improvements", design)
    for item in answer.recommendations:
        for key in ("priority", "monthly_delta_usd", "difficulty", "reason", "resources"):
            assert key in item


def test_the_assistant_states_that_nothing_was_applied(design):
    answer = ask("improve security", design)
    assert "advisory" in answer.text.lower() or "not been applied" in answer.text.lower()


def test_cost_answer_gives_daily_monthly_and_annual(design):
    answer = ask("what does this cost?", design)
    assert Source.COST.value in answer.sources
    assert "a month" in answer.text and "a day" in answer.text and "a year" in answer.text


def test_cost_answer_admits_the_figures_are_approximate(design):
    answer = ask("how much will this cost?", design)
    assert "not live" in answer.text.lower() or "static" in answer.text.lower()


def test_terraform_answer_returns_the_real_block(design):
    answer = ask("show me the terraform for the load balancer", design)
    assert answer.code, "a terraform question should return code"
    assert answer.code in "\n".join(design.terraform.values())


def test_networking_answer_describes_the_actual_layout(design):
    answer = ask("explain the networking", design)
    assert str(design.spec.availability_zones) in answer.text
    assert design.spec.region in answer.text
    assert Source.SPEC.value in answer.sources


def test_security_answer_lists_the_real_groups(design):
    answer = ask("explain the security groups", design)
    assert "security group" in answer.text.lower()
    for group in design.spec.of_kind(Kind.SECURITY_GROUP):
        assert group.id in answer.text, f"{group.id} is in the design but not the answer"
    assert Source.SPEC.value in answer.sources


def test_architecture_answer_separates_requested_from_required(design):
    answer = ask("explain this architecture", design)
    assert "asked for" in answer.text
    assert "mandatory dependencies" in answer.text
    assert design.spec.name in answer.text


def test_validation_answer_matches_the_findings(design):
    answer = ask("explain the validation findings", design)
    assert Source.VALIDATOR.value in answer.sources
    assert len(answer.findings) == len(design.findings)


def test_deployment_answer_gives_the_real_commands(design):
    answer = ask("how do I deploy this?", design)
    for command in ("terraform init", "terraform plan", "terraform apply"):
        assert command in answer.text
    assert design.spec.region in answer.text


# --------------------------------------------------------------------------
# comparisons
# --------------------------------------------------------------------------

@pytest.mark.parametrize("question", [
    "compare EC2 vs ECS",
    "ECS versus EKS",
    "compare Aurora vs RDS",
    "Lambda vs ECS Fargate",
    "compare ALB and NLB",
])
def test_named_comparisons_are_answered(question, design):
    answer = ask(question, design)
    assert answer.intent == Intent.COMPARISON.value
    assert "Choose" in answer.text, question
    assert answer.deterministic


def test_every_comparison_states_when_to_pick_each_side():
    """"X is better" is never true without saying for what."""
    for comparison in COMPARISONS:
        assert len(comparison.choose_left) > 30, comparison.left
        assert len(comparison.choose_right) > 30, comparison.right
        assert comparison.rows, comparison.left


def test_a_comparison_grounds_itself_in_the_design(design):
    answer = ask("compare EC2 vs ECS", design)
    assert "In this design" in answer.text or not answer.resources


# --------------------------------------------------------------------------
# suggestions
# --------------------------------------------------------------------------

def test_suggestions_are_about_this_design(design):
    suggestions = ARCHITECT.suggestions(design)
    assert suggestions
    assert len(suggestions) <= 7
    assert any("Why was" in s for s in suggestions)


def test_every_suggestion_can_actually_be_answered(design):
    """A suggested question that produces nothing is a broken promise."""
    for suggestion in ARCHITECT.suggestions(design):
        answer = ask(suggestion, design)
        assert answer.text.strip(), suggestion
        assert answer.intent != Intent.UNKNOWN.value, suggestion


# ==========================================================================
# the API
# ==========================================================================

def test_the_ask_endpoint_answers(design):
    client = TestClient(app)
    response = client.post("/api/ask", json={
        "prompt": THREE_TIER,
        "question": "why was the NAT gateway added?",
    })
    assert response.status_code == 200
    payload = response.json()
    assert payload["deterministic"] is True
    assert payload["sources"]
    assert payload["text"]


def test_the_ask_endpoint_refuses_an_unsupported_provider():
    client = TestClient(app)
    response = client.post("/api/ask", json={
        "prompt": "an Azure virtual machine",
        "question": "explain this architecture",
    })
    assert response.status_code == 422


def test_the_ask_endpoint_refuses_an_empty_design():
    client = TestClient(app)
    response = client.post("/api/ask", json={
        "prompt": "hello there",
        "question": "explain this",
    })
    assert response.status_code == 422


def test_the_same_question_gives_the_same_answer():
    """No session state, so two identical requests must agree exactly."""
    client = TestClient(app)
    body = {"prompt": THREE_TIER, "question": "how can I reduce cost?"}
    first = client.post("/api/ask", json=body).json()
    second = client.post("/api/ask", json=body).json()
    assert first == second
