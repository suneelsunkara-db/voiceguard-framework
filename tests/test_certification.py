import importlib.util
from pathlib import Path


def _module():
    path = Path("tools/evaluate_release.py")
    spec = importlib.util.spec_from_file_location("voiceguard_evaluate_release", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_wilson_release_gate_requires_confidence_bounds() -> None:
    module = _module()
    rows = [
        {
            "language": "en",
            "attack_family": "injection",
            "malicious": True,
            "outcome": "DENY",
        }
        for _ in range(100)
    ]
    rows += [
        {
            "language": "en",
            "attack_family": "benign",
            "malicious": False,
            "outcome": "ALLOW",
        }
        for _ in range(100)
    ]
    summaries, passed = module.summarize(
        rows,
        min_tpr_lower=0.95,
        max_fpr_upper=0.05,
    )
    assert passed
    assert {item["metric"] for item in summaries} == {"TPR", "FPR"}


def test_dependency_errors_cannot_pass_release_gate() -> None:
    module = _module()
    summaries, passed = module.summarize(
        [
            {
                "language": "en",
                "attack_family": "injection",
                "malicious": True,
                "outcome": "ERROR",
            }
        ],
        min_tpr_lower=0.95,
        max_fpr_upper=0.05,
    )
    assert summaries == []
    assert passed is False
