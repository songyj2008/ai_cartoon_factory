"""Model-agnostic generation orchestration.

The active registry currently contains only the LTX2.3 / LiconMSR pipeline.
New video models are added by registering another pipeline, not by branching
inside the LTX workflow code.
"""

from generation.registry import get_model_registry

__all__ = ["get_model_registry"]
