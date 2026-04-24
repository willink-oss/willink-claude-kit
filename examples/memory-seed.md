# dev-reviewer MEMORY.md 初期テンプレ

各プロジェクトの `.claude/agent-memory/dev-reviewer/MEMORY.md` を新規作成する際の雛形。最初は空でもよいが、明らかな anti-pattern が分かっている場合は seed しておくと dev-reviewer の初期精度が上がる。

---

## 雛形

```markdown
# dev-reviewer MEMORY for <PROJECT_NAME>

> 200 行上限。超えたらテーマ別に整理して圧縮する。

## Recurring anti-patterns

### auth
- (空: 蓄積されたら追記)

### data layer
- (空)

### UI
- (空)

## Architecture decisions worth remembering

- (例: Riverpod を採用しているが、global provider は禁止。feature scope に閉じる)

## Convention corrections from CEO/main Claude

- (例: i18n 文字列の直接埋め込みを CEO に指摘されて修正した。今後は必ず l10n.yaml 経由)

## Last updated: YYYY-MM-DD
```

---

## 運用ルール

1. **dev-reviewer が新しい pattern を発見**したら、レビュー後に MEMORY.md を更新（agent prompt にこの指示を埋め込み済み）
2. **CEO や main Claude から訂正**があれば、それも記録
3. **月次で見直し**: 解消済みの pattern は削除、残るものはセクション整理
4. **200 行上限**: 超えたら distillation（似た指摘をまとめる、解消済みを削除）
5. **git 管理対象**: チームと共有する knowledge なので version control する
