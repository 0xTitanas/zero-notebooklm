"""Allow running ``python -m notebooklm``."""

from .cli import console

if __name__ == "__main__":
    raise SystemExit(console())
