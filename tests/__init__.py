import sys
from pathlib import Path

# 설치하지 않고 `python -m unittest discover -s tests -t .`로 돌릴 수 있게 src를 경로에 넣는다.
_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
