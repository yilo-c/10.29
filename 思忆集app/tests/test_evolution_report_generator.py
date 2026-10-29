from __future__ import annotations

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from evolution.tech_radar import LEVEL_CONFIG, LEVEL_FEATURE, LEVEL_KNOWLEDGE, RadarFinding
from evolution.report_generator import EvolutionReportGenerator, TechBriefingSection


def test_generate_empty_findings():
    section = EvolutionReportGenerator.generate([])
    assert section.items == []
    assert "暂无" in section.weekly_summary


def test_generate_with_findings():
    findings = [
        RadarFinding(name="chromadb", current_version="0.4.0",
                     latest_version="0.6.0", source="pypi",
                     category="dependency", level=LEVEL_CONFIG),
        RadarFinding(name="Ollama", current_version="0.1.0",
                     latest_version="0.3.0", source="github_release",
                     category="tool", level=LEVEL_FEATURE,
                     has_breaking_changes=True),
    ]
    section = EvolutionReportGenerator.generate(findings)
    assert len(section.items) > 0
    # 应该有 section_title
    assert any("section_title" in item for item in section.items)


def test_format_as_text():
    findings = [
        RadarFinding(name="FastAPI", current_version="0.100.0",
                     latest_version="0.110.0", source="pypi",
                     category="dependency", level=LEVEL_CONFIG),
    ]
    section = EvolutionReportGenerator.generate(findings)
    text = EvolutionReportGenerator.format_as_text(section)
    assert "技术雷达" in text
    assert "FastAPI" in text
    assert "Level" in text or "可用升级" in text


def test_format_empty_as_text():
    section = EvolutionReportGenerator.generate([])
    text = EvolutionReportGenerator.format_as_text(section)
    assert "技术雷达" in text
    assert "暂无" in text


def test_to_dict():
    section = EvolutionReportGenerator.generate([])
    d = section.to_dict()
    assert "items" in d
    assert "weekly_summary" in d
    assert "generated_at" in d
