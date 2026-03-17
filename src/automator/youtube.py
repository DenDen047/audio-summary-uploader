"""YouTube API 操作: 認証・アップロード・サムネイル設定."""

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from loguru import logger

_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]


@dataclass
class YouTubeUploadParams:
    file_path: Path
    title: str
    description: str
    tags: list[str] = field(default_factory=list)
    category_id: str = "27"
    privacy_status: str = "unlisted"
    default_language: str = "ja"
    thumbnail_path: Path | None = None
    playlist_id: str | None = None


def authenticate(client_secret_path: Path, token_path: Path) -> Credentials:
    """YouTube API の OAuth 認証を行い Credentials を返す."""
    creds: Credentials | None = None

    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), _SCOPES)

    if creds and creds.expired and creds.refresh_token:
        logger.info("Refreshing YouTube token")
        try:
            creds.refresh(Request())
            token_path.write_text(creds.to_json(), encoding="utf-8")
        except RefreshError as exc:
            logger.warning("Token refresh failed ({}), re-authenticating", exc)
            token_path.unlink(missing_ok=True)
            creds = None
    if not creds or not creds.valid:
        logger.info("Starting YouTube OAuth flow")
        if not client_secret_path.exists():
            msg = f"Client secret not found: {client_secret_path}"
            raise FileNotFoundError(msg)
        flow = InstalledAppFlow.from_client_secrets_file(
            str(client_secret_path), _SCOPES
        )
        creds = flow.run_local_server(port=0)
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(creds.to_json(), encoding="utf-8")
        logger.info("YouTube token saved to {}", token_path)

    return creds


def _upload_video_sync(creds: Credentials, params: YouTubeUploadParams) -> str:
    """動画をアップロードし YouTube URL を返す（同期）."""
    youtube = build("youtube", "v3", credentials=creds)

    body = {
        "snippet": {
            "title": params.title,
            "description": params.description,
            "tags": params.tags,
            "categoryId": params.category_id,
            "defaultLanguage": params.default_language,
        },
        "status": {
            "privacyStatus": params.privacy_status,
            "selfDeclaredMadeForKids": False,
            "containsSyntheticMedia": True,
        },
    }

    media = MediaFileUpload(
        str(params.file_path),
        mimetype="video/mp4",
        resumable=True,
    )

    logger.info("Uploading video: {!r}", params.title)
    request = youtube.videos().insert(
        part="snippet,status", body=body, media_body=media
    )

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            logger.debug("Upload progress: {:.0%}", status.progress())

    video_id = response["id"]
    youtube_url = f"https://youtu.be/{video_id}"
    logger.info("Video uploaded: {}", youtube_url)

    # サムネイル設定
    if params.thumbnail_path and params.thumbnail_path.exists():
        logger.info("Setting custom thumbnail")
        youtube.thumbnails().set(
            videoId=video_id,
            media_body=MediaFileUpload(
                str(params.thumbnail_path), mimetype="image/png"
            ),
        ).execute()

    # プレイリストに追加
    if params.playlist_id:
        logger.info("Adding to playlist {}", params.playlist_id)
        youtube.playlistItems().insert(
            part="snippet",
            body={
                "snippet": {
                    "playlistId": params.playlist_id,
                    "resourceId": {
                        "kind": "youtube#video",
                        "videoId": video_id,
                    },
                },
            },
        ).execute()

    return youtube_url


async def upload_video(creds: Credentials, params: YouTubeUploadParams) -> str:
    """動画をアップロードし YouTube URL を返す（async ラッパー）."""
    return await asyncio.to_thread(_upload_video_sync, creds, params)
