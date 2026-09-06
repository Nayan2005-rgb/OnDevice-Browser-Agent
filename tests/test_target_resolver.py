"""Tests for TargetResolver candidate generation, ranking, and safety."""

from agent.target_resolver import (
    MIN_VISUAL_CONFIDENCE,
    TargetResolver,
)


def _obs(dom=None, ui_map=None, viewport=None):
    page_context = {}
    if viewport:
        page_context["viewport_width"] = viewport["width"]
        page_context["viewport_height"] = viewport["height"]
    return {
        "ui_elements": dom or [],
        "visual_ui_map": ui_map,
        "page_context": page_context,
        "page": {"elements": []},
    }


SUBMIT_DOM = {
    "tag": "button",
    "text": "Submit",
    "selector": "#submit",
    "id": "submit",
    "type": None,
    "sensitive": False,
    "box": {"x": 10, "y": 10, "width": 80, "height": 30},
}


def test_exact_selector_wins():
    resolver = TargetResolver()
    obs = _obs(
        dom=[
            SUBMIT_DOM,
            {
                "tag": "button",
                "text": "Submit form now",
                "selector": "#other",
                "id": "other",
                "sensitive": False,
            },
        ]
    )
    result = resolver.resolve(obs, "Click #submit")
    assert result.status == "ready"
    assert result.target.strategy == "selector"
    assert result.target.selector == "#submit"
    assert result.target.source == "dom"


def test_semantic_dom_match_works():
    resolver = TargetResolver()
    obs = _obs(dom=[SUBMIT_DOM])
    result = resolver.resolve(obs, "Click the Submit button")
    assert result.status == "ready"
    assert result.target.selector == "#submit"
    assert result.target.strategy == "selector"


def test_fused_preferred_over_vision_only():
    resolver = TargetResolver()
    ui_map = {
        "dimensions": {"width": 800, "height": 600},
        "elements": [
            {
                "id": "ui_vis",
                "type": "button",
                "box": {"x": 100, "y": 100, "width": 80, "height": 40},
                "interactive": True,
                "source": "vision",
                "confidence": 0.80,
                "text": "Go",
            },
            {
                "id": "ui_fused",
                "type": "button",
                "box": {"x": 200, "y": 100, "width": 80, "height": 40},
                "interactive": True,
                "source": "dom+vision",
                "confidence": 0.92,
                "text": "Go",
                "selector": "#go-fused",
            },
        ],
        "privacy_safe": True,
    }
    result = resolver.resolve(
        _obs(ui_map=ui_map, viewport={"width": 800, "height": 600}),
        "Click Go",
    )
    assert result.status == "ready"
    assert result.target.id == "ui_fused"
    assert result.target.source == "dom+vision"
    assert result.target.strategy == "selector"
    assert result.target.selector == "#go-fused"


def test_low_confidence_rejected():
    resolver = TargetResolver()
    ui_map = {
        "dimensions": {"width": 400, "height": 300},
        "elements": [
            {
                "id": "ui_low",
                "type": "button",
                "box": {"x": 50, "y": 50, "width": 60, "height": 30},
                "interactive": True,
                "source": "vision",
                "confidence": 0.40,
                "text": "Maybe",
            }
        ],
        "privacy_safe": True,
    }
    result = resolver.resolve(
        _obs(ui_map=ui_map, viewport={"width": 400, "height": 300}),
        "Click Maybe",
    )
    assert result.status == "no_action"


def test_sensitive_candidate_rejected():
    resolver = TargetResolver()
    obs = _obs(
        dom=[
            {
                "tag": "input",
                "text": "",
                "selector": "#password",
                "id": "password",
                "type": "password",
                "name": "password",
                "sensitive": True,
            }
        ]
    )
    result = resolver.resolve(obs, "Click password")
    assert result.status == "no_action"


def test_ambiguous_candidates_handled():
    resolver = TargetResolver()
    obs = _obs(
        dom=[
            {
                "tag": "button",
                "text": "Option",
                "selector": "#opt-a",
                "id": "opt-a",
                "sensitive": False,
            },
            {
                "tag": "button",
                "text": "Option",
                "selector": "#opt-b",
                "id": "opt-b",
                "sensitive": False,
            },
        ]
    )
    result = resolver.resolve(obs, "Click Option")
    # Still picks deterministically (stable id order after score tie-break)
    assert result.status == "ready"
    assert result.target.selector in ("#opt-a", "#opt-b")
    # Ambiguity penalty applied but still above threshold for DOM semantic
    assert result.target.score > 0


def test_vision_coordinate_target():
    resolver = TargetResolver(min_visual_confidence=MIN_VISUAL_CONFIDENCE)
    ui_map = {
        "dimensions": {"width": 800, "height": 600},
        "elements": [
            {
                "id": "ui_vision",
                "type": "button",
                "box": {"x": 100, "y": 200, "width": 80, "height": 40},
                "interactive": True,
                "source": "vision",
                "confidence": 0.86,
                "text": "Vision Control",
            }
        ],
        "privacy_safe": True,
    }
    # scale 2 screenshot vs viewport
    result = resolver.resolve(
        _obs(
            ui_map=ui_map,
            viewport={"width": 400, "height": 300},
        ),
        "Click Vision Control",
    )
    assert result.status == "ready"
    assert result.target.strategy == "coordinates"
    assert result.target.coordinates is not None
    # center screenshot (140, 220) / scale(2,2) → (70, 110)
    assert result.target.coordinates["x"] == 70
    assert result.target.coordinates["y"] == 110


def test_destructive_requires_confirmation():
    resolver = TargetResolver()
    result = resolver.resolve(_obs(dom=[SUBMIT_DOM]), "Delete account")
    assert result.status == "requires_confirmation"


def test_tiny_target_rejected():
    resolver = TargetResolver()
    ui_map = {
        "dimensions": {"width": 400, "height": 300},
        "elements": [
            {
                "id": "ui_tiny",
                "type": "button",
                "box": {"x": 10, "y": 10, "width": 4, "height": 4},
                "interactive": True,
                "source": "vision",
                "confidence": 0.95,
                "text": "Tiny",
            }
        ],
        "privacy_safe": True,
    }
    result = resolver.resolve(
        _obs(ui_map=ui_map, viewport={"width": 400, "height": 300}),
        "Click Tiny",
    )
    assert result.status == "no_action"


def test_performance_metrics_present():
    resolver = TargetResolver()
    result = resolver.resolve(_obs(dom=[SUBMIT_DOM]), "Click Submit")
    assert "target_resolution_ms" in result.performance
    assert "candidate_generation_ms" in result.performance
    assert "candidate_ranking_ms" in result.performance
    assert result.performance["target_resolution_ms"] >= 0
