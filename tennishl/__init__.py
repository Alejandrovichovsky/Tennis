"""tennishl - automatic tennis highlights from a static camera.

Desktop reference implementation of the pipeline described in docs/. The
iOS app is a port of the same stages onto AVFoundation/Vision; see
docs/IOS_PLAN.md.
"""

from .config import Config

__all__ = ["Config"]
__version__ = "0.1.0"
