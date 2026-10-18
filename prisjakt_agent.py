# prisjakt_agent.py - kept so old commands keep working; the code now lives in the bfa/ package.
#   python prisjakt_agent.py --categories TV Mobiltelefoner --max-per-category 20
# is the same as
#   python -m bfa run --categories TV Mobiltelefoner --max-per-category 20
import sys

from bfa.cli import main

if __name__ == "__main__":
    sys.exit(main(["run"] + sys.argv[1:]))
