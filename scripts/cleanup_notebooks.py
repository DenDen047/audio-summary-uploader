"""Bulk delete leaked NotebookLM notebooks to recover quota.

過去のパイプライン障害で NotebookLM 側に残った notebook を一括削除する。
デフォルトは dry-run。--apply を付けたときだけ実際に削除する。

Caveat:
    NotebookLM はソース追加後にノートブック名を自動でリネームすることがあるため、
    `--prefix "Summary: "` (デフォルト) では auto-rename 後のリーク notebook を
    捕捉できない。`--prefix ""` で全所有ノートブックを対象にできるが、その場合は
    手動作成のものも巻き込むので `--keep N` を高めに設定すること。

Usage:
    # 安全モード: "Summary: " で始まるものだけ、最新 50 件は保護
    uv run python scripts/cleanup_notebooks.py --apply

    # 全モード: 全所有ノートブックを対象、最新 50 件を保護
    uv run python scripts/cleanup_notebooks.py --prefix "" --apply

    # まず 20 件だけ消して様子を見る
    uv run python scripts/cleanup_notebooks.py --prefix "" --max-delete 20 --apply
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from loguru import logger
from notebooklm import NotebookLMClient

DEFAULT_PREFIX = "Summary: "
DEFAULT_KEEP = 50
DEFAULT_STATE_PATH = Path("./data/state.json")
DELETE_DELAY_SECONDS = 0.5


def _load_active_notebook_ids(state_path: Path) -> set[str]:
    """state.json から「まだフライト中」の notebook_id を抽出する.

    `generating` 状態のみ保護対象とする。`video_ready`/`uploaded`/`failed` の
    notebook_id は既に削除済みか孤児なので、保護すると掃除できない。
    """
    if not state_path.exists():
        logger.warning(
            "State file not found: {} (no active jobs to protect)", state_path
        )
        return set()
    raw = json.loads(state_path.read_text())
    ids = {
        job.get("notebook_id")
        for job in raw.get("jobs", [])
        if job.get("notebook_id") and job.get("status") == "generating"
    }
    return ids


async def _list_candidates(
    client: NotebookLMClient, prefix: str, active_ids: set[str]
) -> list:
    """削除候補のノートブック一覧を返す.

    フィルタ条件:
    - is_owner=True (自分が作ったもののみ)
    - title が prefix から始まる (パイプライン作成物のみ)
    - notebook_id が active_ids に含まれない
    """
    notebooks = await client.notebooks.list()
    candidates = [
        nb
        for nb in notebooks
        if nb.is_owner
        and nb.title.startswith(prefix)
        and nb.id not in active_ids
    ]
    # created_at で降順 (新しい順)。None は末尾に。
    candidates.sort(
        key=lambda nb: (nb.created_at is None, nb.created_at), reverse=True
    )
    return candidates


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Bulk delete NotebookLM notebooks (dry-run by default)."
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="実際に削除する (デフォルトは dry-run)",
    )
    p.add_argument(
        "--prefix",
        default=DEFAULT_PREFIX,
        help=f"このプレフィックスで始まる title だけ対象 (default: {DEFAULT_PREFIX!r})",
    )
    p.add_argument(
        "--keep",
        type=int,
        default=DEFAULT_KEEP,
        help=f"新しい順に N 件を保護 (default: {DEFAULT_KEEP})",
    )
    p.add_argument(
        "--max-delete",
        type=int,
        default=None,
        help="一回の実行で削除する上限 (default: 上限なし)",
    )
    p.add_argument(
        "--state-path",
        type=Path,
        default=DEFAULT_STATE_PATH,
        help=f"state.json のパス (default: {DEFAULT_STATE_PATH})",
    )
    return p


async def main() -> None:
    args = _build_arg_parser().parse_args()

    active_ids = _load_active_notebook_ids(args.state_path)
    logger.info("Active notebook_ids in state.json: {}", len(active_ids))

    async with await NotebookLMClient.from_storage() as client:
        candidates = await _list_candidates(client, args.prefix, active_ids)
        logger.info(
            "Found {} notebooks matching prefix={!r} (excluding {} active)",
            len(candidates),
            args.prefix,
            len(active_ids),
        )

        # 新しい順に keep 件を保護、残りを削除対象に
        to_delete = candidates[args.keep :]
        if args.max_delete is not None:
            to_delete = to_delete[: args.max_delete]

        logger.info(
            "Keeping {} most recent, deleting {} (apply={})",
            min(args.keep, len(candidates)),
            len(to_delete),
            args.apply,
        )

        # 候補をプレビュー
        for nb in to_delete[:10]:
            logger.info(
                "  [delete] {} | {} | created_at={}",
                nb.id,
                nb.title[:80],
                nb.created_at,
            )
        if len(to_delete) > 10:
            logger.info("  ... and {} more", len(to_delete) - 10)

        if not args.apply:
            logger.info("DRY RUN: no notebooks were deleted. Re-run with --apply.")
            return

        if not to_delete:
            logger.info("Nothing to delete.")
            return

        deleted = 0
        failed = 0
        for nb in to_delete:
            try:
                await client.notebooks.delete(nb.id)
                deleted += 1
                logger.info(
                    "Deleted [{}/{}]: {} | {}",
                    deleted,
                    len(to_delete),
                    nb.id,
                    nb.title[:60],
                )
            except Exception as exc:
                failed += 1
                logger.warning("Failed to delete {}: {}", nb.id, exc)
            await asyncio.sleep(DELETE_DELAY_SECONDS)

        logger.info("Done: deleted={} failed={}", deleted, failed)


if __name__ == "__main__":
    asyncio.run(main())
