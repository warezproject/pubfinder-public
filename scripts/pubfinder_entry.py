"""exe 진입점. PyInstaller가 이 파일에서 시작한다(패키지 안 모듈은 상대 import라 직접 쓰지 않는다)."""

from pubfinder.app import main

raise SystemExit(main())
