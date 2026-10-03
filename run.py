"""Launcher used by `python run.py` and by PyInstaller."""

from app.main import main

if __name__ == "__main__":
    raise SystemExit(main())
