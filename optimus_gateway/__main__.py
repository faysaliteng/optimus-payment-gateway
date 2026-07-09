"""Allow `python -m optimus_gateway` (and the `optimus-gateway` console script) to run
the same CLI as `python run.py`."""
from run import main

if __name__ == "__main__":
    main()
