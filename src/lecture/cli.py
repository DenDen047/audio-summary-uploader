"""講義動画パイプライン CLI。

使い方 (リポジトリルートで):
    uv run lecture generate <URL>
    uv run lecture generate <URL> --script <path>
    uv run lecture render <job_dir>
"""

from pathlib import Path

import click

from lecture.pipeline import (
    DEFAULT_OUT_DIR,
    generate_lecture,
    render_lecture,
)
from lecture.pipeline import (
    EYECATCH_ASSETS as EYECATCH_ASSETS,
)


@click.group()
def main() -> None:
    """講義動画パイプライン（クロノIT方式）。specs/LECTURE_SPEC.md 参照。"""


@main.command()
@click.argument("url")
@click.option("--out-dir", type=click.Path(path_type=Path), default=DEFAULT_OUT_DIR)
@click.option(
    "--script",
    "script_path",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="既存の台本 JSON を使う (codex execをスキップ)",
)
def generate(url: str, out_dir: Path, script_path: Path | None) -> None:
    """URL から動画・サムネイル・投稿情報を生成する。"""
    artifacts = generate_lecture(url, out_dir, script_path=script_path)
    click.echo(f"\n完成: {artifacts.video_path}")
    click.echo(f"サムネイル: {artifacts.thumbnail_path}")
    click.echo(f"投稿情報: {artifacts.upload_metadata_path}")
    click.echo(f"タイトル: {artifacts.title}")


@main.command()
@click.argument("job_dir", type=click.Path(exists=True, path_type=Path))
def render(job_dir: Path) -> None:
    """既存ジョブの script.json から再レンダリングする (台本手直し用)。"""
    import json

    script = json.loads((job_dir / "script.json").read_text(encoding="utf-8"))
    rendered = render_lecture(script, job_dir)
    click.echo(f"\n完成: {rendered.video_path}")


if __name__ == "__main__":
    main()
