# 失敗モードと対策

公式 Claude Code ブログ・ドキュメントから抽出した既知の失敗モード。kit はこれらを構造的に防止する設計。

> Sources:
> - https://claude.com/blog/subagents-in-claude-code
> - https://claude.com/blog/building-multi-agent-systems-when-and-how-to-use-them
> - https://code.claude.com/docs/en/sub-agents

---

## 1. Early Victory Problem

**症状**: dev-tester / dev-reviewer が「テスト 1-2 本で成功と判定」してしまい、未検証部分のバグを見逃す

**原因**: subagent が `--bail` 等で early exit したのを「全件 pass」と誤認

**kit の対策**:
- `agents/dev-tester.md` の prompt に「Run the full test suite before marking as passed」を明示
- skipped/pending 件数を別カウントで報告させる
- build artifact 生成も確認項目に含める

**運用側の対策**: dev-tester の出力で「commands run」が想定数あるか目視確認

---

## 2. Telephone Game

**症状**: 順序的同一作業を subagent 連鎖に分割すると、ハンドオフ毎に忠実度が低下

**原因**: 計画→実装→テストを別 agent に渡すと、各 agent が前提を勝手に解釈

**kit の対策**:
- Phase 3（実装）と Phase 5（修正）は **メイン Claude が担当**
- subagent 化するのは Phase 1（探索）/ 2（計画）/ 4（並列検証）のみ
- `dev-fixer` / `dev-implementer` は意図的に定義しない

---

## 3. Options Flooding

**症状**: 大量の subagent を定義すると automatic delegation の信頼性が下がる（公式: "flooding Claude with options makes automatic delegation less reliable"）

**原因**: Claude が description マッチで迷う

**kit の対策**:
- agent は **4 本に厳選**（dev-explorer / dev-planner / dev-tester / dev-reviewer）
- 追加は CHANGELOG レビューを通す（kit 月次レビュー）
- description は「いつ呼ぶか」を明記

---

## 4. 同一ファイル並列編集

**症状**: 複数 agent が同じファイルを並列編集してコンフリクト

**kit の対策**:
- 並列化するのは Phase 4 の **read-only** agent のみ（dev-tester は Bash 実行のみ・dev-reviewer は read-only）
- write 権限を持つのはメイン Claude だけ（並列化しない）

---

## 5. Subagent コスト爆発

**症状**: マルチエージェント実装は単一エージェントの **3-10x のトークン**を消費（公式）

**kit の対策**:
- Phase 1 並列は **3 軸以上の独立性**がある時のみ・最大 3 並列
- 小タスクは Phase 1/2 を skip（commands/build.md 早見表参照）
- skip 判断はメイン Claude の判断・迷ったら skip

---

## 6. Context Pollution（自身のコンテキストが膨張）

**症状**: subagent の verbose 出力がメインに戻ってきてメインの context を圧迫

**kit の対策**:
- 各 agent の output format を厳密に指定（要約のみ返す）
- explorer は「Summary / Key files / Conventions / Open questions / Recommended follow-up」の固定フォーマット
- tester は「Verdict / Commands run / Failures / Suggested fix scope」の固定フォーマット

---

## 7. Subagent の無限ネスト

**症状**: Subagent が他 subagent を spawn しようとして失敗

**根本対策**: **公式仕様で subagent は他 subagent を spawn できない**（https://code.claude.com/docs/en/sub-agents 参照）

**kit の対策**:
- メイン Claude のみが orchestrator
- 各 agent の prompt に「No nested subagents」を明記

---

## 8. project-standards 欠落

**症状**: kit を導入したリポジトリに `project-standards` skill がない

**原因**: 導入手順 step 2 を skip

**kit の対策**:
- 公式仕様: missing skill は warning ログのみで動作継続
- adoption-guide.md で必須 step として明記
- 雛形（examples/project-standards-template/）を提供

---

## 9. Plugin バージョン不整合

**症状**: kit を v0.2 に上げたら下流リポで挙動が変わって混乱

**kit の対策**:
- 各リポで `@version` pin 推奨（adoption-guide.md）
- CHANGELOG に破壊的変更を必ず明記
- v1.0.0 までは破壊的変更ありうる旨を README に明示

---

## 10. dev-reviewer memory の腐敗

**症状**: MEMORY.md にゴミ pattern が溜まり review 品質が下がる

**kit の対策**:
- 200 行上限を agent prompt に明記（公式の自動 curate 機構と同じ閾値）
- 月次で MEMORY.md を確認・古い pattern を削除（kit 月次レビュー手順に追加予定）
