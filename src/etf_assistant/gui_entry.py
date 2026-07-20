"""PyInstaller entry point for the desktop application."""

import multiprocessing


def main() -> int:
    # PyInstaller child processes reuse this executable. Dispatch them before
    # importing Qt, otherwise a resource tracker opens a second app window.
    multiprocessing.freeze_support()
    from etf_assistant.gui import main as gui_main

    return gui_main()


if __name__ == "__main__":
    raise SystemExit(main())
