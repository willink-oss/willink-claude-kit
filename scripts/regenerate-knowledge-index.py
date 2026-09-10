#!/usr/bin/env python3
"""assets/knowledge-base.md の AUTO-INDEX 区画を assets/knowledge/ の実体から再生成する。

2026-06-11 ドキュメント整合性監査 M12/M13 対応:
- 手動 index はカバー率 24.5%・リンク切れ 26% まで腐敗していたため、機械生成に切替。
- 手動維持するのは AUTO-INDEX マーカーの外側 (カテゴリ表・使い方) のみ。
- リンクは生成時に実体パスから作るため構造的に切れない。

usage:
  python3 scripts/regenerate-knowledge-index.py          再生成して書き込む（従来動作）
  python3 scripts/regenerate-knowledge-index.py --check   読取専用: 索引が実体と一致し
                                                          broken link=0 なら exit 0、
                                                          drift or broken>0 なら exit 1
  （どちらもリポジトリルートで実行）
"""
import os
import re
import sys
from collections import defaultdict

import os as _os_ph
import sys as _sys_ph
_sys_ph.path.insert(0, _os_ph.path.dirname(_os_ph.path.abspath(__file__)))
import _phroot  # harness: 検査対象ルート解決

ROOT = _phroot.target_root()
# 走査対象は配布先ごとに違うため、env / CLI で差し替え可能にしておく。
# 既定は <root>/assets/knowledge と <root>/assets/knowledge-base.md。
KDIR = os.path.join(ROOT, os.environ.get('PH_KNOWLEDGE_DIR', 'assets/knowledge'))
INDEX = os.path.join(ROOT, os.environ.get('PH_KNOWLEDGE_INDEX', 'assets/knowledge-base.md'))
BASE = os.path.dirname(KDIR)   # index 内の相対リンクの基準
START = '<!-- AUTO-INDEX:START (regenerate-knowledge-index.py が再生成・手動編集禁止) -->'
END = '<!-- AUTO-INDEX:END -->'


def _configure(kdir, index):
    """走査対象を差し替える（--self-test / CLI オプション用）。"""
    global KDIR, INDEX, BASE
    KDIR = kdir
    INDEX = index
    BASE = os.path.dirname(kdir)


def title_of(path):
    """md ファイルの先頭 H1 をタイトルとして取る (無ければファイル名)。"""
    try:
        with open(path, encoding='utf-8') as f:
            for line in f:
                if line.startswith('# '):
                    return line[2:].strip()[:80]
                if line.strip() and not line.startswith(('>', '---', '<!--')):
                    break
    except OSError:
        pass
    return os.path.splitext(os.path.basename(path))[0]


def collect():
    groups = defaultdict(list)  # group key -> [(relpath, name, title)]
    for dirpath, dirnames, filenames in os.walk(KDIR):
        dirnames.sort()
        rel_dir = os.path.relpath(dirpath, KDIR)
        for fn in sorted(filenames, reverse=True):
            if not fn.endswith('.md'):
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, BASE)
            if rel_dir == '.':
                m = re.match(r'^(\d{4})-(\d{2})-\d{2}', fn)
                key = f'root/{m.group(1)}-{m.group(2)}' if m else 'root/その他 (日付なし)'
            else:
                key = rel_dir.split(os.sep)[0]
            groups[key].append((rel, fn, title_of(full)))
    return groups


def render(groups):
    total = sum(len(v) for v in groups.values())
    out = [START, '',
           f'> 自動生成 index: 全 {total} ファイル。週次 (日曜) に再生成。手動でファイルを追加した場合も次回実行で自動掲載される。', '']
    # root の月別 (新しい月から)
    root_keys = sorted([k for k in groups if k.startswith('root/')], reverse=True)
    dir_keys = sorted([k for k in groups if not k.startswith('root/')])
    for key in root_keys:
        label = key.split('/', 1)[1]
        items = groups[key]
        out.append(f'### {label}（{len(items)} 件）')
        out.append('')
        for rel, fn, title in items:
            out.append(f'- [{title}]({rel})')
        out.append('')
    for key in dir_keys:
        items = groups[key]
        note = {'serendipity': '異分野探索シリーズ', 'wellbeing': 'Wellbeing シリーズ',
                'routine-health': '週次 routine 健全性レポート（消費導線: /harness-review H 項）',
                'archive': '四半期アーカイブ'}.get(key, '')
        out.append(f'### {key}/（{len(items)} 件）{"— " + note if note else ""}')
        out.append('')
        if key in ('serendipity', 'wellbeing', 'routine-health', 'archive') and len(items) > 15:
            for rel, fn, title in items[:10]:
                out.append(f'- [{title}]({rel})')
            out.append(f'- …他 {len(items) - 10} 件（`assets/knowledge/{key}/` を直接参照）')
        else:
            for rel, fn, title in items:
                out.append(f'- [{title}]({rel})')
        out.append('')
    out.append(END)
    return '\n'.join(out)


def check_links(text, base):
    """index 内の相対リンク切れを検出して返す。"""
    broken = []
    for m in re.finditer(r'\]\(([^)#http][^)]*)\)', text):
        target = os.path.normpath(os.path.join(base, m.group(1)))
        if not os.path.exists(target):
            broken.append(m.group(1))
    return broken


def rebuild(src):
    """committed の索引文字列 src から、実体に基づく再生成後の文字列を組み立てて返す。
    ファイルには一切書き込まない（純関数）。"""
    block = render(collect())
    if START in src and END in src:
        pat = re.compile(re.escape(START) + '.*?' + re.escape(END), re.S)
        new = pat.sub(lambda _: block, src, count=1)
    else:
        new = src.rstrip('\n') + '\n\n---\n\n## ナレッジ全ファイル索引（自動生成）\n\n' + block + '\n'
    return new


def main():
    if not os.path.isdir(KDIR):
        print(f'対象無・観測継続 — knowledge ディレクトリ不在: {KDIR}')
        return 0
    src = open(INDEX, encoding='utf-8').read()
    new = rebuild(src)
    open(INDEX, 'w', encoding='utf-8').write(new)
    broken = check_links(new, BASE)
    total = sum(len(v) for v in collect().values())
    print(f'regenerated. 走査 {total} 件 / broken links: {len(broken)}')
    for b in broken[:20]:
        print('  MISSING:', b)
    return 1 if broken else 0


def check():
    """読取専用: 索引が実体と一致し broken link=0 なら 0、drift or broken>0 なら 1。
    ファイルには一切書き込まない。"""
    if not os.path.isdir(KDIR):
        print(f'対象無・観測継続 — knowledge ディレクトリ不在: {KDIR}')
        return 0
    src = open(INDEX, encoding='utf-8').read()
    new = rebuild(src)
    broken = check_links(new, BASE)
    drift = (new != src)
    total = sum(len(v) for v in collect().values())
    # 分母つきで出す（走査 0 件と「0 件該当」を区別する）
    print(f'走査 {total} 件 / broken links: {len(broken)}')
    if total == 0:
        print('❗ 走査対象 0 件 — 正常系ではなく異常として扱う')
    if drift:
        print(f'DRIFT: {INDEX} の AUTO-INDEX が実体と不一致（再生成が必要）')
    for b in broken[:20]:
        print('  MISSING:', b)
    if drift or broken:
        return 1
    print('OK: 索引は実体と一致・broken link=0')
    return 0


def self_test():
    """hermetic 自己検証。一時ディレクトリの fixture のみを触り、実データを読まない。

    ハードコード成功を禁じるため (a) drift 検出 (b) broken link 検出
    (c) 再生成後の一致 (d) 対象欠如の観測継続 の 4 挙動をすべて assert する。
    """
    import shutil
    import tempfile

    fails = []
    tmp = tempfile.mkdtemp(prefix='regen-idx-st-')
    try:
        base = os.path.join(tmp, 'assets')
        kdir = os.path.join(base, 'knowledge')
        index = os.path.join(base, 'knowledge-base.md')
        os.makedirs(kdir)
        with open(os.path.join(kdir, '2026-01-02-alpha.md'), 'w', encoding='utf-8') as fh:
            fh.write('# アルファ\n\n本文\n')
        with open(os.path.join(kdir, '2026-01-03-beta.md'), 'w', encoding='utf-8') as fh:
            fh.write('# ベータ\n\n本文\n')
        # 索引は空の AUTO-INDEX 区画のみ = 実体と不一致（drift あり）
        with open(index, 'w', encoding='utf-8') as fh:
            fh.write('# 索引\n\n' + START + '\n' + END + '\n')

        _configure(kdir, index)

        # (a) drift を検出できる
        if check() != 1:
            fails.append('drift ありの fixture で exit 1 にならなかった')

        # (c) 再生成すると一致し、以後 exit 0
        if main() != 0:
            fails.append('再生成が broken link を報告した（fixture に切れリンクは無い）')
        if check() != 0:
            fails.append('再生成後も一致と判定されなかった')

        # 生成物に両ファイルが載っていること（空索引でも通る実装を弾く）
        body = open(index, encoding='utf-8').read()
        for want in ('アルファ', 'ベータ'):
            if want not in body:
                fails.append(f'再生成後の索引に {want} が載っていない')

        # (b) 実体を消すと broken link を検出できる
        os.remove(os.path.join(kdir, '2026-01-02-alpha.md'))
        broken = check_links(body, base)
        if len(broken) != 1:
            fails.append(f'broken link 検出数が想定と違う: {len(broken)} (期待 1)')

        # (d) 対象欠如は「観測継続」で exit 0（欠如は失敗ではない）
        _configure(os.path.join(tmp, 'nonexistent'), index)
        if check() != 0:
            fails.append('対象欠如で exit 0（観測継続）にならなかった')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if fails:
        print('regenerate-knowledge-index.py --self-test: FAIL')
        for f in fails:
            print('  -', f)
        return 1
    print('regenerate-knowledge-index.py --self-test: PASS (4 観点)')
    return 0


if __name__ == '__main__':
    args = sys.argv[1:]
    if '--self-test' in args:
        sys.exit(self_test())
    for flag, idx in (('--knowledge-dir', 0), ('--index', 1)):
        if flag in args:
            val = args[args.index(flag) + 1]
            if idx == 0:
                _configure(os.path.abspath(val), INDEX)
            else:
                _configure(KDIR, os.path.abspath(val))
    if '--check' in args:
        sys.exit(check())
    sys.exit(main())
