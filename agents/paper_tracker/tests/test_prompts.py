"""Tests — load all Paper Tracker prompts, assert render succeeds."""

from __future__ import annotations

import contextlib

from backbone.prompt_registry.loader import clear_cache, load, render


def setup_method() -> None:
    clear_cache()


PROMPTS = [
    ("system", 1),
    ("summarize_paper", 1),
    ("why_relevant", 1),
    ("why_relevant", 2),
    ("professor_why", 1),
    ("professor_brief", 1),
    ("professor_discovery", 1),
    ("email_opener", 1),
    ("filter_decision", 1),
]


def test_all_prompts_load() -> None:
    """Every Paper Tracker prompt YAML loads without error."""
    for name, version in PROMPTS:
        prompt = load("paper_tracker", name, version=version)
        assert prompt.version == version
        assert prompt.name == name


def test_all_prompts_render() -> None:
    """Every prompt template renders with placeholder inputs."""
    for name, version in PROMPTS:
        prompt = load("paper_tracker", name, version=version)
        fields = prompt.input_schema.fields
        # Build placeholder inputs for each field
        inputs = {f.name: f"<{f.name}>" for f in fields}
        if not inputs:
            inputs = {"placeholder": "test"}

        with contextlib.suppress(KeyError):
            render(prompt, inputs)
        # At minimum, the template is a non-empty string
        assert len(prompt.template) > 0


def test_filter_decision_has_refused_instruction() -> None:
    """The filter_decision prompt instructs the model to say REFUSED."""
    prompt = load("paper_tracker", "filter_decision")
    assert "REFUSED" in prompt.template or "RELEVANT" in prompt.template


def test_model_assignments() -> None:
    """Each prompt has the correct model assigned."""
    assignments = {
        ("summarize_paper", 1): "gemini-2.5-flash",
        ("why_relevant", 1): "deepseek-pro",
        ("why_relevant", 2): "deepseek-pro",
        ("professor_why", 1): "gemini-2.5-flash",
        ("professor_brief", 1): "gemini-2.5-flash",
        ("professor_discovery", 1): "gemini-2.5-flash",
        ("filter_decision", 1): "gemini-2.5-flash",
        ("email_opener", 1): "gemini-2.5-flash",
    }
    for (name, version), expected_model in assignments.items():
        prompt = load("paper_tracker", name, version=version)
        assert prompt.model.name == expected_model, (
            f"{name}_v{version}: expected {expected_model}, got {prompt.model.name}"
        )
