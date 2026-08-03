"""パイプライン実行の排他制御."""

from __future__ import annotations

import fcntl
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from loguru import logger


class PipelineBusyError(RuntimeError):
    """別プロセスがパイプラインを実行中でロックを取得できなかった."""


_depth = 0


@contextmanager
def pipeline_lock(state_path: Path) -> Iterator[None]:
    """state.json を書き換えるパイプライン実行を1プロセスへ直列化する。

    CLI と Web UI のワーカーが同じジョブを同時に collect すると、片方が
    NotebookLM のノートブックを削除した時点でもう片方のアーティファクトが一覧から
    消え（`removed`）、chat も not found で拒否される。互いの成果を壊し合うため、
    ロックを取れない実行は待たずに諦める（生成は片方が進めている）。

    同一プロセス内の入れ子呼び出し（run_pipeline → submit/collect/upload）は
    素通しする。flock は fd 単位なので、同じプロセスで2枚目の fd を取ると
    自分自身とデッドロックするため。
    """
    global _depth

    if _depth > 0:
        _depth += 1
        try:
            yield
        finally:
            _depth -= 1
        return

    lock_path = state_path.with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise PipelineBusyError(
            f"別のパイプライン実行中です（ロック: {lock_path}）。"
            "collect / upload の二重実行はジョブを壊すため中止しました。"
        ) from None

    os.ftruncate(fd, 0)
    os.write(fd, f"{os.getpid()}\n".encode())
    _depth = 1
    logger.debug("Acquired pipeline lock: {} (pid={})", lock_path, os.getpid())
    try:
        yield
    finally:
        _depth = 0
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
        logger.debug("Released pipeline lock: {}", lock_path)
