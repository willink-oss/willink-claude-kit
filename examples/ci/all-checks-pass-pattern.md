# The "All checks passed" summary-job pattern

A single summary job that aggregates every CI job, meant to be the **only** required
status check in branch protection. Battle-tested across Flutter, Next.js and monorepo
CIs (willink-labs tsuu / fit-ai / clublink-platform, 2026-07).

## Why one summary job instead of requiring each job

- **Robust to renames** — add/rename/split CI jobs freely; branch protection keeps
  pointing at one stable context (`All checks passed`).
- **Path-filtered monorepos work** — GitHub treats a *skipped* check as satisfied, so
  jobs skipped via `dorny/paths-filter` (untouched areas) don't block the PR, while a
  *failed* job does.
- **No silent green** — the summary job inspects each `needs` result explicitly and
  exits 1 on `failure`/`cancelled`; `if: always()` guarantees it reports even when
  upstream jobs fail (otherwise it would be *skipped* — which counts as satisfied!).

## Template (append to your workflow's `jobs:`)

```yaml
  all-checks-pass:
    name: All checks passed
    runs-on: ubuntu-latest
    needs: [lint, test, build]        # ← list EVERY job in this workflow
    if: always()                      # ← REQUIRED: must run even when a need fails
    steps:
      - name: Verify no needed job failed
        run: |
          results='${{ join(needs.*.result, ',') }}'
          echo "needs results: $results"
          case ",$results," in
            *,failure,*|*,cancelled,*)
              echo "One or more checks failed"
              exit 1
              ;;
          esac
          echo "All checks passed"
```

For a small non-path-filtered CI you can be stricter — require `success` explicitly
(skipped also fails):

```yaml
      - name: Verify all jobs succeeded
        run: |
          if [ "${{ needs.lint.result }}" != "success" ] || \
             [ "${{ needs.build.result }}" != "success" ]; then
            echo "One or more checks failed"; exit 1
          fi
          echo "All checks passed"
```

## Branch protection (free tier; no paid add-ons)

```bash
gh api -X PUT "repos/OWNER/REPO/branches/main/protection" --input - <<'EOF'
{
  "required_status_checks": { "strict": false, "contexts": ["All checks passed"] },
  "enforce_admins": false,
  "required_pull_request_reviews": {
    "dismiss_stale_reviews": true,
    "require_code_owner_reviews": false,
    "required_approving_review_count": 0
  },
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false
}
EOF
```

Pitfalls learned in production:

- Existing open PRs need a branch update / CI re-run before the new required context is
  reported — Dependabot PRs pick it up on rebase.
- Verify your `lint` script actually works before gating on it. (A repo we gated had
  `next lint` silently broken for months — the very first CI run caught it.)
- Loosening or removing a required check is a governance decision, not a convenience —
  treat it like removing a lock.
