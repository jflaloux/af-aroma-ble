"""Local Bluetooth control for AF Aroma waterless scent diffusers."""
from .client import AFAroma, AFAromaError, discover, with_retries
from .protocol import Group, Options

__all__ = ["AFAroma", "AFAromaError", "Group", "Options", "discover", "with_retries"]
__version__ = "0.1.0"
