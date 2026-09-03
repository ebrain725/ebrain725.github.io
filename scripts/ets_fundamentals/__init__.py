"""Independent ETS electricity and energy source-data collector."""

from .core import (
    CollectorError,
    DatasetSpec,
    FieldDefinition,
    NormalizedDataset,
    SourceRun,
    publish_dataset,
)
from .kpx_supply import KpxSupplyTodayAdapter

__all__ = [
    "CollectorError",
    "DatasetSpec",
    "FieldDefinition",
    "KpxSupplyTodayAdapter",
    "NormalizedDataset",
    "SourceRun",
    "publish_dataset",
]

__version__ = "0.1.0"
