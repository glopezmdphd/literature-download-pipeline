# Ensure project root is on sys.path for test imports
import sys, os
root = os.path.dirname(os.path.abspath(__file__))
if root not in sys.path:
    sys.path.insert(0, root)
