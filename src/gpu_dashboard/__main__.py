"""Module entrypoint — `python3 -m gpu_dashboard` starts the HTTP server."""
import sys
from .server import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
