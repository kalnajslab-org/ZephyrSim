"""zephyrsim package."""

from importlib.metadata import version, PackageNotFoundError

try:
    __version__ = version("zephyrsim")
except PackageNotFoundError:
    __version__ = "unknown"

__all__ = []
