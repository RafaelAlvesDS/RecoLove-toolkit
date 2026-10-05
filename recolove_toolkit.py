"""
Entry point of RecoLoveToolkit.exe:
  * no arguments  -> graphical interface
  * arguments     -> command line (see cli.py, or run with --help)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _attach_console():
    """The .exe is a windowed app; when started from a terminal, print there."""
    if not getattr(sys, "frozen", False) or os.name != "nt":
        return
    try:
        import ctypes
        if ctypes.windll.kernel32.AttachConsole(-1):
            sys.stdout = open("CONOUT$", "w", encoding="utf-8", errors="replace")
            sys.stderr = sys.stdout
            print()
    except Exception:
        pass


def main():
    if len(sys.argv) > 1:
        _attach_console()
        import cli
        try:
            cli.main(sys.argv[1:])
        except SystemExit:
            raise
        except Exception as e:
            print("ERROR: %s" % e)
            sys.exit(1)
        return
    import app
    app.main()


if __name__ == "__main__":
    main()
