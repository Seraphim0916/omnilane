# AA 政策檔同日快照 v2：退役三列 GPT-5.4 mini

狀態：`snapshot.approval.status = approved`；操作者明示核准退役這三列。
工作樹：`feat/aa-2026-10-02`。本變更未提交、未推送，沒有執行供應商程式、連網或主機重簽。

## v1 與 v2 的差異

- 快照：`aa-v4.3.2-2026-10-02-v1` → `aa-v4.3.2-2026-10-02-v2`。
- 使用相同抽取檔 `docs/reports/aa-v4.3.2-extract-2026-10-02.json`；AA 指數版本 `4.3.2`、日期 `2026-10-02` 都不變。沒有重新抓取或重新測量。
- **計分表只少了以下三列；沒有任何分數改變。其餘 114 列的識別碼、整數分數、原始分數、估計旗標及排序與 v1 完全相同。**
- 原因是 Codex 搭配 ChatGPT 帳號回傳 HTTP 400：`not supported when using Codex with a ChatGPT account`。AA 仍列出模型，不是假稱 AA 撤下；核准退役日 `2026-10-02` 與原因寫入 `RETIRED_ROWS` 及每一筆未知列。

| 退役的計分列 | v1 整數／原始分數 | v2 未知列識別碼 |
|---|---|---|
| `codex/gpt-5-4-mini` | 24／24.07 | `codex/gpt-5.4-mini/xhigh/reasoning` |
| `codex/gpt-5-4-mini-medium` | 20／19.75（估計） | `codex/gpt-5.4-mini/medium/reasoning` |
| `codex/gpt-5-4-mini-non-reasoning` | 11／11.14（估計） | `codex/gpt-5.4-mini/none/non-reasoning` |

未知列沿用既有 AA 撤下列的格式：`score: null`、`authority_eligible: false`、`mapping_status: unknown`、`runtime_verified: false`。不沿用舊分數，也不授予派工權限。

| 覆蓋數量 | v1 | v2 |
|---|---:|---:|
| scored_configs | 117 | 114 |
| estimated scored rows | 26 | 24 |
| unknown_configs | 8 | 11 |
| aliases | 52 | 51 |
| reference_configs | 1 | 1 |
| codex | 57 | 54 |
| claude | 40 | 40 |
| gemini | 8 | 8 |
| grok | 12 | 12 |

`gpt-5.4-mini` 的設定目錄別名完全移除，其他別名保留；兩處 `PROVEN` 登錄及設定選單項目同步移除。Spark 在 Codex 選單由第 12 項變成第 11 項，對應測試已更新。

## 重建與證據

```sh
python3 scripts/aa_rebaseline.py build --base config/aa-model-policy.json --extract docs/reports/aa-v4.3.2-extract-2026-10-02.json --as-of 2026-10-02 --revision 2 --approval approved
bash -c 'python3 scripts/aa_rebaseline.py report --old <(git show 7eee58d:config/aa-model-policy.json 2>/dev/null)'
shasum -a 256 config/aa-model-policy.json
```

實際結果（均退出碼 0）：

```text
build: 114 scored (24 estimated), 11 unknown, dropped ['codex/gpt-5-4-mini', 'codex/gpt-5-4-mini-medium', 'codex/gpt-5-4-mini-non-reasoning']
build: sha256 f3209025b97849e2eec17f9676e3f530b1acdcf45c6f8424e1f04a618c916ffb
report: wrote 4 vendor reports under /Users/vincentw/.openclaw/staging/2026-10-02-aa-snapshot-branch/docs/reports
f3209025b97849e2eec17f9676e3f530b1acdcf45c6f8424e1f04a618c916ffb  config/aa-model-policy.json
```

`APPROVED_REGISTRY_SHA256` 已更新為上述值；`APPROVED_BENCHMARK_VERSION` 與 `APPROVED_AS_OF` 不變。

以 `git show 7eee58d:config/aa-model-policy.json` 的 v1 計分序列作差異比對：

```diff
--- v1
+++ v2
@@ -95 +94,0 @@
-codex/gpt-5-4-mini score=24 raw=24.07 estimated=False
@@ -105 +103,0 @@
-codex/gpt-5-4-mini-medium score=20 raw=19.75 estimated=True
@@ -114 +111,0 @@
-codex/gpt-5-4-mini-non-reasoning score=11 raw=11.14 estimated=True
```

v2 的四份供應商證據均使用 `-v2` 檔名：

- `docs/reports/aa-codex-evidence-2026-10-02-v2.md`
- `docs/reports/aa-claude-evidence-2026-10-02-v2.md`
- `docs/reports/aa-gemini-evidence-2026-10-02-v2.md`
- `docs/reports/aa-grok-evidence-2026-10-02-v2.md`

v1 報告與原抽取檔保持原樣。`routing.yaml` 沒有修改：`grep -n 'gpt[-.]5[-.]4[-.]mini' routing.yaml` 退出碼 1（沒有命中）；`python3 scripts/aa_rebaseline.py lanes --extract docs/reports/aa-v4.3.2-extract-2026-10-02.json` 退出碼 0，11 條車道的完整輸出與使用 v1 政策檔時逐字相同。

## 實作與測試

- `RETIRED_ROWS` 與 `NEW_ROWS` 重疊時，在任何輸出寫入前明確失敗。退役列離開計分表與別名候選，報告將操作者退役與 AA 撤下分開描述。
- 重建目前 v1 時發現既有問題：已在未知列的 `claude-sonnet-5-5-low` 會被 `NEW_ROWS` 再次要求，導致 `build: claude-sonnet-5-5-low is not in the extract`。現已沿用既有未知列狀態，不重新加入；v2 重複重建維持相同位元組。
- 新增 `tests/test_aa_retired_rows.py` 四項回歸測試，涵蓋 AA 仍列出的退役列、原因與權限、別名與探測計畫、分數排序不變、重複重建及衝突時不寫檔。
- `tests/test_aa_policy.py` 的 Codex 樣本改成既有低分且已登錄 `PROVEN` 的 `codex/gpt-5-6-luna-low`（21 分）；計分、供應商與估計列數同步更新。
- `tests/test_aa_model_coverage.py` 保留歷史覆蓋資料，但比較現行選單時排除明示退役模型。`tests/test_probe_identity.py` 的 mini 名稱只屬模擬 HTTP 400 記錄，與計分表無關，因此不改。

所有測試使用工作樹既有 `.sandbox-tmp` 下的拋棄式家目錄，不讀取真實 `~/.omnilane`。執行前設定：

```sh
export TMPDIR="$PWD/.sandbox-tmp"
export OMNILANE_HOME="$(mktemp -d "$TMPDIR/aa-v2-home.XXXXXX")"
```

以下 Python 指令均以前綴執行：

```sh
env -i PATH=/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin TMPDIR="$TMPDIR" HOME="$OMNILANE_HOME" OMNILANE_HOME="$OMNILANE_HOME" OMNILANE_REPO="$PWD" PYTHONDONTWRITEBYTECODE=1
```

| 實際執行指令 | 結果 |
|---|---|
| `python3 -m unittest discover -s tests -p 'test_aa*.py'` | 退出碼 0；`Ran 67 tests in 9.413s`；`OK (skipped=1)` |
| `python3 tests/test_skill_lane_table.py` | 退出碼 0；`Ran 2 tests in 0.008s`；`OK` |
| `python3 tests/test_overlay_unscored_rows.py` | 退出碼 0；`Ran 4 tests in 0.000s`；`OK` |
| `python3 tests/test_probe_identity.py` | 退出碼 0；`Ran 24 tests in 0.225s`；`OK (skipped=2)` |
| `count=0; failed=0; for file in bin/omnilane scripts/*.sh scripts/lib/*.sh scripts/runners/*.sh install.sh; do bash -n "$file" || failed=1; count=$((count+1)); done; printf 'bash -n: checked=%s failed=%s\n' "$count" "$failed"; exit "$failed"` | 退出碼 0；`bash -n: checked=30 failed=0` |
| `shellcheck -S warning bin/omnilane scripts/*.sh scripts/lib/*.sh scripts/runners/*.sh install.sh` | 退出碼 0；stdout/stderr 都空白 |
| `git diff --check -- '*.sh' '*.py' '*.pl' '*.yaml' '*.yml' '*.json' '*.md' bin/omnilane` | 退出碼 0；stdout 空白 |

第一次 AA 測試跑出歷史覆蓋比對失敗：`test_aa_model_coverage.AAModelCoverageTest.test_every_catalog_value_and_alias_has_mapping_or_exception`，`AssertionError: Items in the first set but not the second: ('codex', 'gpt-5.4-mini')`；修正上述現行選單比對後重跑通過，不把它歸咎於主機重簽。

### 主機狀態限制與跳過項目

- `test_aa_policy.TransportOverlayEvidenceTests.test_live_overlay_loads_and_verifies_every_unstale_mapping`：未修改；在隔離家目錄跳過，`skipped 'host-local transport overlay is unavailable'`。真正主機的 v1 覆蓋檔預期需要重新簽署，否則載入會有 `transport overlay snapshot mismatch`；此預期沒有用真實主機檔案實測。
- `test_probe_identity.VerdictTests.test_evidence_predating_the_tier_field_reads_as_selector_only` 與 `test_probe_identity.VerdictTests.test_nine_historical_evidence_regressions`：未修改；`skipped 'historical evidence is host-local and read-only'`，未讀主機歷史證據。
- `ps -axo command=` 的啟動遭沙箱拒絕：`PermissionError: [Errno 1] Operation not permitted: 'ps'`。依操作者規則，**未執行整套 Python 測試或 `bash tests/run.sh </dev/null`**，不推測其他測試程序是否正在執行。
- 每台主機都必須由操作者執行 `omnilane resign`，因為快照識別碼已改變。本工作不執行重簽，不宣稱真實供應商或主機覆蓋檔驗收完成。

狀態：離線重建、釘選、車道比對與指定個別檢查 **PASS**；整套測試與主機重簽 **未執行／未驗證**。逐步證據亦記錄在工作樹根目錄 `PROGRESS-AA.md`。
