"""EEG integration - Emotiv Cortex API, mental state detection."""

from .integration import EEGBridge, MentalState
from .emotiv_client import EmotivCortexClient

__all__ = ["EEGBridge", "MentalState", "EmotivCortexClient"]
