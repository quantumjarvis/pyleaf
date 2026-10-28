import sys
from pathlib import Path

M2_DIR = Path(__file__).resolve().parent
M1_DIR = M2_DIR.parent / "m1"

# This dir holds the m2 helpers (webbench); m1's dir holds benchgen, whose
# synthetic-bean-graph generators the actuator benchmark reuses.
sys.path.insert(0, str(M2_DIR))
sys.path.insert(0, str(M1_DIR))
