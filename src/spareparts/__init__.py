"""The Spare Parts command line."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("spareparts-cli")
except PackageNotFoundError:
    __version__ = "0.0.0.dev0"
