# AfterFlow closeout evidence

This directory contains sanitized, reproducible evidence for the project closeout. All business data is from the repository's Mock providers; no production customer, payment, or logistics data is included.

## Baseline

- Captured: 2026-09-15 (Asia/Shanghai)
- Git HEAD: `0531b2bc9eab444e60cbc2296d68e87c2ea07686`
- Working tree: 77 tracked/untracked status entries at capture; SHA-256 of the newline-joined `git status --porcelain=v1` output: `b6a816c7ca99e10fba79ee2d862319993a14f5cdb76b8d638db9fd8cd3c8d6b1`
- Scope: the dirty tree is the inherited upgrade baseline. HEAD alone does not identify the evaluated code.
- Runtime: Windows 10.0.26200.0; AMD Ryzen 9 7945HX; 33,474,768,896 bytes RAM; Python 3.13.11; Node 24.14.1; pnpm 10.33.0; uv 0.11.6.
- Deployment boundary under test: one Gateway process, SQLite, repository Mock OMS/payment/logistics providers.

## Input hashes at baseline

```text
70eb90bdc0d002880fc664b33d6116d4fe4d87391efe3b5f1e248c06720ca57a  backend/scripts/evaluate_afterflow_agent.py
049202926d555c278156fb72f1fe73392829a0d5de86e65f417d7eb8dbf80137  backend/scripts/evaluate_afterflow_baseline.py
031cbb0a106e533f40cdc54db64d08d652e9c66bf144e737482dfbab2ae1d45d  backend/tests/fixtures/after_sales_agent_tasks.json
89f12edebfe7dbfc7559f49842bc5650a34bafd85b09cf008767f6da203fd4a6  backend/tests/fixtures/after_sales_evaluation.json
a06e2ad2b9e3c4e0c5182dc8040bc7eafe62b0af3e1b14e055f5ddd219ef21b8  skills/public/after-sales-intake/SKILL.md
bdca1a1c4d105e3313669bffc8abf43906bb2befdfcca254f3a02afbffc28d3c  skills/public/after-sales-resolution/SKILL.md
82151316ccf22dc785f69aba3a983d844ea6f100e35ab7dc9194602164164ec2  backend/app/after_sales/mock_data.py
```

## Historical real-model anchor

The copied artifacts preserve the previous 12-task × 3-attempt run; they are not a new run and must not be presented as current-code results.

```text
6bc5ac4e3b24f93cba01c6e1b5e55b9294348fc6b4c1ded6bb3491b2579a152  historical-agent/runs-20260915T052956Z.jsonl
902a2bbc5b918af13171fb08d9be4c779c0ff2f1c72ec0e477c6b88490f9995e  historical-agent/summary-20260915T052956Z.json
```

Historical records can be rescored for persisted Case fields/stage and the saved/model reply text. They do not contain complete Action/event/provider-ledger snapshots or per-model-call usage, so those metrics remain unavailable rather than inferred.

## Reproduction commands

```powershell
git rev-parse HEAD
git status --porcelain=v1
Get-FileHash -Algorithm SHA256 <path>

Set-Location backend
$env:PYTHONPATH = ".;packages/harness"
uv run pytest tests/test_after_sales_router_idempotency.py tests/test_after_sales_actions.py tests/test_after_sales_tools.py -q
```

Additional experiment outputs are added in named subdirectories with their command, input hashes, code state, and limitations.

## Closeout evidence added

- `evaluator-v5/`: final offline rescore of the historical 36 records. It adds strict task completion, Tool distribution, compound-intake redundancy, latency p50/p95, and Token coverage while keeping unavailable persisted-side-effect evidence null.
- `agent-real/interview-20260916-v1/`: authorized current-code run of 18 tasks × 3 attempts with `deepseek-v4-flash`; all 54 raw trajectories, Tool results/latencies, terminal Case/Action/events, Token usage, hashes, and failures are retained.
- `agent-real/interview-20260916-v3-final/`: same-model/config/task post-optimization run. All 54 raw trajectories are retained; the final offline rescore corrects a tested negated-claim false positive without another model call.
- `agent-real/smoke-*`: two one-task diagnostics used to fix online summary/stream accounting and validate the compound-Tool direction; they are not part of the 54-run headline result.
- `performance/approval-list.json`: 600 raw observations comparing the former 1+N approval-list query with the joined path; 100 rows, 100 samples × 3 rounds per strategy, no removed failures.
- Runnable recovery evidence remains in `backend/tests/test_after_sales_actions.py`; the exact injection test and its limitation are recorded in `docs/OPERATIONS.md`.

## Final input hashes

```text
7e1ecafaa040e41b28f1abad89ebbddcabe47f784872c3d4ed01ee2c3964f2469  backend/scripts/evaluate_afterflow_agent.py
b88340c74147840e975407c461836b0a369c59f1d40bc89a840a1d93ce069910  backend/app/after_sales/intake.py
7cbd276a0bad3d4c1602a50ce5fcbbfe80181509a91064931c11721fe47d09aa  backend/tests/fixtures/after_sales_agent_tasks.json
a9c6239a586fcf37b52d03b095e92b3ea1b7f3e0725f0011700bb02ee90b1a11  skills/public/after-sales-intake/SKILL.md
515193f70716e34a6dc8b0a2ae3f780544da045c752b3bd41869f268b79ed936  skills/public/after-sales-evidence/SKILL.md
bdca1a1c4d105e3313669bffc8abf43906bb2befdfcca254f3a02afbffc28d3c  skills/public/after-sales-resolution/SKILL.md
8b49584d41ed7a8d35d7604e4e0e2b722791ee7fd7b61138fce3eff92b09beb3  performance/approval-list.json
5ff24b11db9191990a0f73b7c92768563069939e32094db45399160a7a581374  agent-real/interview-20260916-v1/runs.jsonl
fe34e00d305650bcaadecbc3898af0dab9610d03c37598aee5ef0b652c6c0a18  agent-real/interview-20260916-v1/summary.json
6294594fe8138dcb58c248b3bbc9332e707d32c09d685cbb536ca6d942d83770  agent-real/interview-20260916-v3-final/runs.jsonl
6bce5ee9ad4429ca989d85b4f70c0e27a08f05ee51b4366e52e2e311e271d569  agent-real/interview-20260916-v3-final/summary-20260915T174823Z.json
```

## Final verification

- AfterFlow backend selection: 463 passed, 1 skipped.
- Frontend type/lint check: passed.
- Frontend unit tests: 58 files, 535 tests passed.
- Ruff check and format check: passed; `git diff --check`: passed.
- Agent evaluator and intake regressions: 22 passed; final offline rescore preserves all 54 optimized-run attempts.
- Repository-wide backend probe: stopped at the configured 20-failure threshold after 1,587 passed and 17 skipped. Most failures require POSIX `sh`, which is unavailable in this Windows run; the other observed failures are unrelated platform/path assertions. This probe is not reported as a passing full suite.

## Initial finding status

| Finding | Baseline state | Current evidence |
| --- | --- | --- |
| F01 cross-user HTTP replay | reproduced | fixed by authenticated cache scope; HTTP regression added |
| F02 concurrent first request | reproduced | fixed for the documented single-process deployment by per-key serialization; HTTP regression added |
| F03 provider success / Action save failure | reproduced | shared-operation fault injection recovers one Provider result without a second refund |
| F04 return-required refund without receipt | reproduced | shared execution boundary refuses it; complete RMA remains manual |
| F05 unused budget/cancellation stores | static finding | configured Runtime Guardrail construction verified; generic Runtime owns budget/cancel, but no new real-model E2E run |
| F06 split Action/Case/event commits | static finding | Action-save window injected; Case/event window and cross-process recovery remain limitations |
| F07 route-centric metrics and cleanup | static finding | actual `/metrics` auth/Nginx exposure documented; background reaper remains optional/unimplemented |
| F08 web intake differs from Agent evaluation | verified design boundary | docs corrected; browser verification remains manual |
| F09 approval-list N+1 candidate | reproduced by 300×2 observations | replaced 101-query path with one JOIN; raw benchmark retained |
