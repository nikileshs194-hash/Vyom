"""
GigPilot - smoke test for the backup Streamlit app, driven headlessly.
"""

import sys
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

BACKUP_DIR = Path(__file__).resolve().parents[1] / "backup_streamlit"
APP = str(BACKUP_DIR / "app.py")


@pytest.fixture(autouse=True)
def backup_modules(monkeypatch):
    """The backup has its own data.py / agents.py; make sure the app imports
    those and not the website's modules of the same name."""
    for name in ("data", "agents"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    monkeypatch.syspath_prepend(str(BACKUP_DIR))
    yield
    for name in ("data", "agents"):
        sys.modules.pop(name, None)


def click(at, label):
    next(b for b in at.button if b.label == label).click().run()


def headline(at):
    return next(m.value for m in at.markdown if m.value.startswith("###"))


def current_zone(at):
    return next(m.value for m in at.metric if m.label == "Current zone")


def test_full_demo_flow():
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception
    assert not at.metric  # dashboard hidden until a goal is set

    click(at, "Start / Update Goal")
    assert current_zone(at) == "Koramangala"
    assert headline(at) == "### Move to Zone C - HSR Layout"

    click(at, "Accept")
    assert current_zone(at) == "HSR Layout"
    assert headline(at) == "### Stay in Zone C - HSR Layout"
    assert "Moved to Zone C - HSR Layout" in [s.value for s in at.success]

    click(at, "Simulate traffic spike")
    click(at, "Simulate incentive activation")
    click(at, "Ignore")
    click(at, "Reset all zone conditions")
    assert not at.exception
    assert at.warning[0].value == "Last event: All zone conditions reset to normal"
    assert list(at.dataframe[-1].value["outcome"]) == ["Accepted", "Ignored"]


def test_hours_worked_cannot_exceed_hours_available():
    at = AppTest.from_file(APP, default_timeout=30).run()
    next(n for n in at.number_input if n.label == "Hours available").set_value(2.0)
    click(at, "Start / Update Goal")
    assert at.error[0].value == "Hours already worked cannot exceed hours available."
    assert not at.metric
