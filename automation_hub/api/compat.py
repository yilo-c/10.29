# -*- coding: utf-8 -*-
"""Pydantic v1/v2 兼容层"""

try:
    # Pydantic v2
    from pydantic import ConfigDict, field_validator, model_validator, RootModel
    PYDANTIC_V2 = True
except ImportError:
    # Pydantic v1 fallback
    ConfigDict = None
    field_validator = None
    model_validator = None
    RootModel = None
    PYDANTIC_V2 = False