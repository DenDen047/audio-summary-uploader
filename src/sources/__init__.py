"""情報源の取得層（podcast / lecture 両モード共通）.

URL・ローカルPDFから本文と図を抽出する fetch と、公開テキストの
個人情報サニタイズを提供する。podcast モードは resolve_source の
出力型で「URL 直渡し」と「抽出テキスト投入」を切り替える。
"""
