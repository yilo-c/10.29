"""求职猎手简历子包。

提供简历文件解析、诊断、改写等能力。
"""

from __future__ import annotations

from .analyzer import DiagnosisResult, DiagnosisScores, ResumeAnalyzer
from .parser import SUPPORTED_EXTENSIONS, ResumeDocument, ResumeParser
from .rewriter import ResumeRewriter, RewriteResult

__all__ = [
    "DiagnosisResult",
    "DiagnosisScores",
    "ResumeAnalyzer",
    "ResumeDocument",
    "ResumeParser",
    "ResumeRewriter",
    "RewriteResult",
    "SUPPORTED_EXTENSIONS",
]
