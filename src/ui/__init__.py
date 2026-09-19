"""Pygame desktop UI package for the Sangokusha prototype.

Components only draw and hit-test; every rule decision stays in the engine.
Submodules are imported lazily (``from src.ui import theme``), so importing a
light module like ``src.ui.theme`` never drags the engine in.
"""

__all__ = [
    "cards",
    "fx",
    "layout",
    "overlay",
    "player",
    "prompt",
    "seats",
    "table",
    "theme",
    "widgets",
]
