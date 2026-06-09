from pathlib import Path


def test_required_docs_exist_and_cover_security():
    root = Path(__file__).resolve().parents[1]
    two_box = root / "docs" / "two-box-lan-test.md"
    security = root / "docs" / "security-checklist.md"
    assert two_box.exists()
    assert security.exists()
    assert "box A" in two_box.read_text() or "Box A" in two_box.read_text()
    text = security.read_text().lower()
    for phrase in ["pairing required", "signed requests", "nonce replay", "timestamp skew", "no shell"]:
        assert phrase in text
