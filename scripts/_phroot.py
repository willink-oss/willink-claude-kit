"""検査対象リポジトリのルート解決（proof-harness 共通）。

crew 内蔵時代のエンジンは `<repo>/scripts/x.py` に置かれていたため
`__file__/../..` を検査対象ルートとして使えた。ハーネスを独立配布すると
その導出は **ハーネス自身の install 先** を指してしまい、購入者のリポジトリを
一切見ないまま「0 件」を返す。これは `docs/incidents.md` の
「静かなゼロ」（走査 0 件と該当 0 件を区別できない）と同じ失敗形になる。

解決順（先に決まったものを採用）:
  1. 環境変数 `PH_TARGET_ROOT`
  2. cwd から `git rev-parse --show-toplevel`
  3. cwd

いずれも「ハーネスの install 先」ではなく **実行時のコンテキスト** を見る。
CLI の `--root` が渡された場合は常にそれが最優先（各エンジン側で処理）。
"""

import os
import subprocess


def target_root(fallback=None):
    env = os.environ.get("PH_TARGET_ROOT")
    if env:
        return os.path.abspath(os.path.expanduser(env))
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=10,
        )
        if out.returncode == 0 and out.stdout.strip():
            return os.path.abspath(out.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        pass
    return os.path.abspath(fallback or os.getcwd())


def harness_home():
    """ハーネス自身の install 先（同梱 fixture / docs を読む時だけ使う）。"""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
