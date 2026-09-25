"""Persian diacritization engines."""

from .base import (
    DiacritizationEngine,
    DiacritizationMode,
    DiacritizationResult,
    DiacritizationUnavailable,
)
from .canine_onnx import CanineOnnxDiacritizer, split_windows
from .lexicon import (
    BUILTIN_LEXICON,
    LexiconDiacritizer,
    ezafe_needed,
    export_lexicon,
)
from .service import (
    DiacritizationPipelineStage,
    DiacritizationService,
    DiacritizationSettings,
)

__all__ = [
    "BUILTIN_LEXICON",
    "CanineOnnxDiacritizer",
    "DiacritizationEngine",
    "DiacritizationMode",
    "DiacritizationPipelineStage",
    "DiacritizationResult",
    "DiacritizationService",
    "DiacritizationSettings",
    "DiacritizationUnavailable",
    "LexiconDiacritizer",
    "coverage_report",
    "export_lexicon",
    "ezafe_needed",
    "split_windows",
]
