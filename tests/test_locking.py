"""パイプライン排他ロックのテスト."""

import subprocess
import sys
from pathlib import Path

import pytest

from podcast.locking import PipelineBusyError, pipeline_lock

_HOLD_LOCK_SCRIPT = """
import sys, time
from pathlib import Path
sys.path.insert(0, {src!r})
from podcast.locking import pipeline_lock

with pipeline_lock(Path(sys.argv[1])):
    print("locked", flush=True)
    time.sleep(30)
"""


def test_lock_is_reentrant_within_process(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    with pipeline_lock(state_path), pipeline_lock(state_path):
        pass
    # 解放後は再取得できる
    with pipeline_lock(state_path):
        pass


def test_lock_blocks_other_process(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    src = str(Path(__file__).resolve().parent.parent / "src")
    holder = subprocess.Popen(
        [sys.executable, "-c", _HOLD_LOCK_SCRIPT.format(src=src), str(state_path)],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "locked"

        with pytest.raises(PipelineBusyError):
            with pipeline_lock(state_path):
                pass
    finally:
        holder.kill()
        holder.wait(timeout=10)

    # 相手が終われば取得できる
    with pipeline_lock(state_path):
        pass
