# omnilane 0.63.5 發版提交前驗收報告

<!-- source: codex-s / MacStudio / feat/aa-20261119-gemini-4-neon / 2026-11-19 -->

**整體驗收狀態為 `PARTIAL`。** 工作樹已備妥 0.63.5。核准版政策檔兩次建置逐位元組相同，工作樹的命令列入口回報 `omnilane 0.63.5`。核准後共執行 93 個測試，91 個通過、1 個因沙箱限制失敗、1 個依測試設計略過。嚴格發版稽核只列出工作樹尚未乾淨。

| 項目 | 結果 |
|---|---|
| 已核准的快照 | `aa-v5.0.1-2026-11-19-v1` |
| 政策檔 SHA-256 | `2e8ba8b802fac7ddd18f137ce72a5e5b040f10e67a8b2bb84ac0c7a4572b319f` |
| 傳輸相容性封存 SHA-256 | `315eaea7841dfba23c9fc7d94e951d7612d3d55f19c01cea03a70e87752235a6` |
| 建置基底 | 上一份報告指定的 11 月 18 日政策檔，SHA-256 為 `42ac3db913098e902762f27cb76d6b83ca4f0223e77553cc9c18ed4dd14a9673` |
| 開工時的候選版 | SHA-256 為 `52092fd817370c338064f9712a67d777378e12eccf6437e44aca9b934de27628`，用於核准前後比對 |

## 已驗證

Vincent 於 2026-11-19 核准更新 AA 基準。主控轉交的原話為「Codex 信任核准好了 AA基準有更新就更新／然後我核准直接做到上線」。本輪受託範圍到隔離工作樹的提交前準備為止。

- **政策檔。** 兩次建置使用同一份基底和抽取檔，`cmp` 回傳 0。與本輪開工時的候選政策相比，完整文字差異只有 `snapshot.approval.status` 從 `proposed` 改為 `approved`、`snapshot.approval.estimated_scores` 從 `provisional_pending_review` 改為 `approved_provisional`。另外逐欄比對過整份 JSON。實際載入函式 `aa_policy.load_registry()` 驗過新雜湊。
- **傳輸相容性。** 先用 `compat-entry` 找到當期封存中舊的 `registry_sha256`，再以原工具重建。新舊封存逐欄比對只差當期政策雜湊，所有歷史快照與選取器資料相同。封存雜湊、程式釘值與兩個受保護檔案的測試釘值均已對齊。
- **車道。** 實際載入政策，確認 Neon xhigh 61、high 60、medium 55、low 44。逐條比較目前工作樹與 0.63.4 起點的 `routing.yaml`，十一條啟用車道只在 Hydrogen high 後新增 Neon high，十二條首選與所有既有候選順序相同，`arbitrate` 維持停用。
- **發版資料。** 五個檔案共六個版本值全是 0.63.5。五語 README 的 0.63.5 段落均緊接在 0.63.4 前方，首項為升級後執行 `omnilane resign`，末項為 `npm i -g omnilane@0.63.5`。與本輪備份相比，五份文件的所有表格列均逐行相同。變更紀錄的版號、日期與兩條比較連結通過稽核。
- **測試與稽核。** 平行度最高為 2。以下次數分別計算，開工基線不納入核准後合計。

| 檢查 | 通過 | 失敗 | 略過 | 結果 |
|---|---:|---:|---:|---|
| 開工基線，版本與 Neon 測試 | 8 | 0 | 0 | 候選版 |
| 指定的四個測試模組 | 52 | 1 | 1 | 共 54 個，細項見下節 |
| 本輪修改的三個測試模組 | 30 | 0 | 0 | Neon、未對應型號、來源整合 |
| 加驗相容性封存及技能／五語車道表 | 9 | 0 | 0 | 包含封存歷史資料與目前條目 |
| 核准後測試合計 | **91** | **1** | **1** | **93 個** |

**失敗與略過的逐項原因**

1. `tests.test_aa_routing_update.AARoutingUpdateTests.test_direct_api_refuses_work_before_provider_call` 需要確認供應商呼叫前的政策拒絕碼為 2。沙箱內無法透過 `ps` 讀取程序啟動時間，程式在建立工作前就回傳 1。候選階段報告也記錄同一個測試及相同錯誤。這一項在本輪仍為失敗。
2. `test_live_overlay_loads_and_verifies_every_unstale_mapping` 需要明示設定 `OMNILANE_TEST_LIVE_OVERLAY=1` 才會讀取主機的傳輸覆寫檔（overlay）與引用的傳輸驗證紀錄。指定命令走預設略過分支。此項沒有執行主機驗證。
3. `bash scripts/release-audit.sh` 回傳 1，唯一失敗項目為 `dirty-worktree`。工作樹保留前一件成果與本次尚未提交的變更。用同一支腳本的 `--allow-dirty` 再跑，回傳 0。兩次皆因未設定 `OMNILANE_USAGE_DOC` 而略過 `usage-doc-drift`，並提示 `release-tag-not-required`。清單雜湊取自 Git 索引，封存預演取自 `HEAD`；當時兩者都尚未包含本輪修改。

## 本輪變更與回復

以下以本輪接手的候選工作樹為比對起點。原有檔案 24 個已備份，另新增本報告 1 個。

| 用途 | 檔案 |
|---|---|
| 版本 | `VERSION`、`package.json`、`plugin.json`、`.claude-plugin/plugin.json`、`.claude-plugin/marketplace.json` |
| 版本說明 | `CHANGELOG.md`、`README.md`、`README.zh-TW.md`、`README.zh-CN.md`、`README.ja.md`、`README.ko.md` |
| 政策與相容性封存 | `config/aa-model-policy.json`、`config/transport-compatibility.json`、`scripts/lib/aa_policy.py`、`scripts/lib/transport_compat.py` |
| 車道與模型說明 | `routing.yaml`（首行註解）、`skills/omnilane/SKILL.md`、`docs/aa-model-coverage-2026-09-05.json`（四筆 Neon 說明）、`docs/model-capabilities-2026-09.md` |
| 測試 | `tests/test_aa_neon.py`、`tests/test_aa_unmapped_model.py`、`tests/test_origin_integration.py` |
| 報告 | `docs/reports/aa-rebaseline-2026-11-19.md`、`docs/reports/neon-rebaseline-2026-11-19-REPORT.md`（開頭補核准更新，保留候選版實測輸出）、`docs/reports/neon-release-prep-2026-11-19-REPORT.md`（本檔） |

備份在本工作樹的 `.test-scratch/neon-release-prep-1119/backup/`，檔名沿用原始相對路徑。若要回到接手時的狀態，可先核對後續是否有人修改，再從備份逐檔還原。用 Git 的 `HEAD` 還原會連前一件尚未提交的成果一併覆寫。建置器預設產生候選版，歷史日期的測試也仍保留各自的候選輸入。

## 推論

從相同基底兩次重建、候選版與核准版的完整差異，以及政策載入與相容性封存的實測，可判斷這次核准只更新了授權資料及相依雜湊。沙箱在目標政策分支之前就擋下程序檢查，該失敗無法判定沙箱外此項測試的核准後結果。

## 未驗證

- 主控回報在本輪修改前已於沙箱外以新快照重建的主機覆寫檔跑過 `tests/run.sh`（154 通過、0 失敗）及 `python3 -m unittest discover`（2807 個、結果 OK、略過 14 個）。這是候選工作樹的主控回報。本輪未重跑兩套完整測試，核准版在沙箱外的完整測試尚未驗證。
- 主機重簽、真實供應商傳輸、乾淨提交的發版稽核、標籤及正式上線均不在本次驗收範圍。尚未暫存或提交變更。這次依工人任務書自行執行，沒有另外委派審查。
- 本輪使用既有發版技能第 0a、1 節、既有建置／封存／查核工具、開工基線、命令列與政策載入實測、變更紀錄及本報告。相容性封存先觀察到舊雜湊失敗，再以原工具重建驗證通過。

## 指令與實際輸出

以下命令皆以本工作樹為工作目錄，使用 `TMPDIR="$PWD/.test-scratch/neon-release-prep-1119/tmp"`、`PYTHONDONTWRITEBYTECODE=1` 與 `GIT_OPTIONAL_LOCKS=0`。每個步驟的指令、完整標準輸出／錯誤與退出碼也分別保存在 `.test-scratch/neon-release-prep-1119/out/` 下的 `.cmd`、`.txt`、`.rc`。部分負向測試會刻意列出參數錯誤，其套件結論見各次輸出的最後一行。

### 開工基線

```sh
python3 -m unittest tests.test_release_version tests.test_aa_neon
```

退出碼 `0`。

```text
........
----------------------------------------------------------------------
Ran 8 tests in 0.057s

OK
```

### 第一次建置

```sh
python3 scripts/aa_rebaseline.py build --extract docs/reports/aa-v5.0.1-extract-2026-11-19.json --base .test-scratch/neon-1119/backup/config/aa-model-policy.json --as-of 2026-11-19 --approval approved
```

退出碼 `0`。

```text
build: 112 scored (22 estimated), 60 unknown, dropped []
build: sha256 2e8ba8b802fac7ddd18f137ce72a5e5b040f10e67a8b2bb84ac0c7a4572b319f
```

### 第一次雜湊

```sh
shasum -a 256 config/aa-model-policy.json
```

退出碼 `0`。

```text
2e8ba8b802fac7ddd18f137ce72a5e5b040f10e67a8b2bb84ac0c7a4572b319f  config/aa-model-policy.json
```

### 保留第一次輸出

```sh
cp config/aa-model-policy.json .test-scratch/neon-release-prep-1119/approved-first.json
```

退出碼 `0`。沒有輸出。

### 第二次建置

```sh
python3 scripts/aa_rebaseline.py build --extract docs/reports/aa-v5.0.1-extract-2026-11-19.json --base .test-scratch/neon-1119/backup/config/aa-model-policy.json --as-of 2026-11-19 --approval approved
```

退出碼 `0`。

```text
build: 112 scored (22 estimated), 60 unknown, dropped []
build: sha256 2e8ba8b802fac7ddd18f137ce72a5e5b040f10e67a8b2bb84ac0c7a4572b319f
```

### 第二次雜湊

```sh
shasum -a 256 config/aa-model-policy.json
```

退出碼 `0`。

```text
2e8ba8b802fac7ddd18f137ce72a5e5b040f10e67a8b2bb84ac0c7a4572b319f  config/aa-model-policy.json
```

### 位元組比對

```sh
cmp .test-scratch/neon-release-prep-1119/approved-first.json config/aa-model-policy.json
```

退出碼 `0`。沒有輸出。

### 候選與核准政策的完整差異

```sh
diff -u .test-scratch/neon-release-prep-1119/backup/config/aa-model-policy.json config/aa-model-policy.json
```

退出碼 `1`。

```text
--- .test-scratch/neon-release-prep-1119/backup/config/aa-model-policy.json	2026-11-19 10:25:20
+++ config/aa-model-policy.json	2026-11-19 11:45:21
@@ -8,10 +8,10 @@
     "timezone": "Asia/Taipei",
     "frozen": true,
     "approval": {
-      "status": "proposed",
+      "status": "approved",
       "scope": "aa-v5.0.1-rebaseline",
       "source": "docs/reports/aa-rebaseline-2026-11-19.md",
-      "estimated_scores": "provisional_pending_review"
+      "estimated_scores": "approved_provisional"
     },
     "automatic_refresh_grants_authority": false,
     "source": {
```

### 相容性封存重建前

```sh
python3 scripts/aa_rebaseline.py compat-entry
```

退出碼 `1`。

```text
compat-entry: aa-v5.0.1-2026-11-19-v1 differs in registry_sha256
```

### 重建相容性封存

```sh
python3 scripts/aa_rebaseline.py compat-entry --write
```

退出碼 `0`。

```text
compat-entry: wrote aa-v5.0.1-2026-11-19-v1; archive sha256 315eaea7841dfba23c9fc7d94e951d7612d3d55f19c01cea03a70e87752235a6
```

### 封存重建後

```sh
python3 scripts/aa_rebaseline.py compat-entry
```

退出碼 `0`。

```text
compat-entry: aa-v5.0.1-2026-11-19-v1 matches the archive
```

### 同步版本、釘值、測試與文件

```sh
python3 .test-scratch/neon-release-prep-1119/prepare.py
```

退出碼 `0`。

```text
scripts/lib/aa_policy.py
scripts/lib/transport_compat.py
tests/test_origin_integration.py
tests/test_aa_neon.py
tests/test_aa_unmapped_model.py
VERSION
package.json
plugin.json
.claude-plugin/plugin.json
.claude-plugin/marketplace.json
routing.yaml
docs/aa-model-coverage-2026-09-05.json
skills/omnilane/SKILL.md
docs/model-capabilities-2026-09.md
docs/reports/aa-rebaseline-2026-11-19.md
docs/reports/neon-rebaseline-2026-11-19-REPORT.md
CHANGELOG.md
```

### 五語說明

```sh
python3 .test-scratch/neon-release-prep-1119/readmes.py
```

退出碼 `0`。

```text
README.md ## What's new in v0.63.5
README.zh-TW.md ## v0.63.5 新功能
README.zh-CN.md ## v0.63.5 新功能
README.ja.md ## v0.63.5 の新機能
README.ko.md ## v0.63.5 새 기능
```

### 指定的四個測試模組

```sh
python3 -m unittest tests.test_release_version tests.test_aa_policy tests.test_aa_routing_update tests.test_aa_build_shape
```

退出碼 `1`。

```text
...................................s..........F.......
======================================================================
FAIL: test_direct_api_refuses_work_before_provider_call (tests.test_aa_routing_update.AARoutingUpdateTests.test_direct_api_refuses_work_before_provider_call)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/vincentw/dev/omnilane-wt/feat-neon-aa-1119/tests/test_aa_routing_update.py", line 263, in test_direct_api_refuses_work_before_provider_call
    self.assertEqual(2, result.returncode, result.stdout + result.stderr)
    ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
AssertionError: 2 != 1 : omnilane: ps cannot read process start times here, so no job was started. A sandbox usually causes this; rerun outside the sandbox (from a normal terminal, or by approving the unsandboxed run).


----------------------------------------------------------------------
Ran 54 tests in 7.680s

FAILED (failures=1, skipped=1)
```

### 修改過的測試模組

```sh
python3 -m unittest tests.test_aa_neon tests.test_aa_unmapped_model tests.test_origin_integration
```

退出碼 `0`。

```text
..........usage: python3 -m unittest [-h] [--trust-origin VENDOR |
                           --trust-location VENDOR |
                           --revoke-origin VENDOR KIND] [--team TEAM]
                           [--check] [--init] [--retry-failed]
                           [--vendor {claude,codex,gemini,grok}]
                           [--approve {claude,codex,gemini,grok}]
                           [--trust-adhoc VENDOR] [--record-signers]
                           [--allow-shrink] [--no-smoke] [--json] [--daily]
python3 -m unittest: error: unrecognized arguments: --yes
................usage: python3 -m unittest [-h] [--trust-origin VENDOR |
                           --trust-location VENDOR |
                           --revoke-origin VENDOR KIND] [--team TEAM]
                           [--check] [--init] [--retry-failed]
                           [--vendor {claude,codex,gemini,grok}]
                           [--approve {claude,codex,gemini,grok}]
                           [--trust-adhoc VENDOR] [--record-signers]
                           [--allow-shrink] [--no-smoke] [--json] [--daily]
python3 -m unittest: error: origin commands cannot be combined with other actions
usage: python3 -m unittest [-h] [--trust-origin VENDOR |
                           --trust-location VENDOR |
                           --revoke-origin VENDOR KIND] [--team TEAM]
                           [--check] [--init] [--retry-failed]
                           [--vendor {claude,codex,gemini,grok}]
                           [--approve {claude,codex,gemini,grok}]
                           [--trust-adhoc VENDOR] [--record-signers]
                           [--allow-shrink] [--no-smoke] [--json] [--daily]
python3 -m unittest: error: --team requires --trust-origin
....
----------------------------------------------------------------------
Ran 30 tests in 27.725s

OK
```

### 標準發版稽核

```sh
bash scripts/release-audit.sh
```

退出碼 `1`。

```text
PASS repository-boundary
FAIL dirty-worktree
SKIP usage-doc-drift OMNILANE_USAGE_DOC is not configured
PASS version-file
PASS wrapper-version
PASS changelog-release
PASS changelog-unreleased-link
PASS changelog-release-link
PASS runner-pins-current
PASS tracked-VERSION
PASS tracked-LICENSE
PASS tracked-CHANGELOG.md
PASS tracked-README.md
PASS tracked-README.zh-TW.md
PASS tracked-README.zh-CN.md
PASS tracked-README.ja.md
PASS tracked-README.ko.md
PASS tracked-install.sh
PASS tracked-routing.yaml
PASS tracked-bin/omnilane
PASS tracked-scripts/dispatch.sh
PASS tracked-scripts/jobs.sh
PASS tracked-scripts/doctor.sh
PASS tracked-scripts/ui.py
PASS tracked-scripts/release-audit.sh
PASS tracked-skills/omnilane/SKILL.md
PASS executable-install.sh
PASS executable-bin/omnilane
PASS executable-scripts/dispatch.sh
PASS executable-scripts/jobs.sh
PASS executable-scripts/doctor.sh
PASS executable-scripts/ui.py
PASS executable-scripts/release-audit.sh
PASS executable-scripts/lib/job-timeout.pl
PASS executable-scripts/lib/job-worker.sh
PASS executable-scripts/runners/run-claude.sh
PASS executable-scripts/runners/run-codex.sh
PASS executable-scripts/runners/run-exec.sh
PASS executable-scripts/runners/run-gemini.sh
PASS executable-scripts/runners/run-grok.sh
PASS executable-scripts/runners/run-vote.sh
PASS no-forbidden-tracked-paths
PASS safe-tracked-path-names
PASS no-tracked-symlinks
PASS no-private-key-material
PASS rollback-documented
PASS manifest-hash
PASS archive-dry-run
WARN release-tag-not-required
release-audit: FAIL target=0.63.5 findings=1 tracked=740 manifest_sha256=262abfcc650e0d7879351c9ded9fa08e85f8a83d1e014321738540f28d7f3dec archive_sha256=90f34b7c580c6e87a06be0fa20c7b8bc84be806b66bf4ad2c0c8600826daa701
```

### 允許未提交變更的發版稽核

```sh
bash scripts/release-audit.sh --allow-dirty
```

退出碼 `0`。

```text
PASS repository-boundary
WARN dirty-worktree-allowed
SKIP usage-doc-drift OMNILANE_USAGE_DOC is not configured
PASS version-file
PASS wrapper-version
PASS changelog-release
PASS changelog-unreleased-link
PASS changelog-release-link
PASS runner-pins-current
PASS tracked-VERSION
PASS tracked-LICENSE
PASS tracked-CHANGELOG.md
PASS tracked-README.md
PASS tracked-README.zh-TW.md
PASS tracked-README.zh-CN.md
PASS tracked-README.ja.md
PASS tracked-README.ko.md
PASS tracked-install.sh
PASS tracked-routing.yaml
PASS tracked-bin/omnilane
PASS tracked-scripts/dispatch.sh
PASS tracked-scripts/jobs.sh
PASS tracked-scripts/doctor.sh
PASS tracked-scripts/ui.py
PASS tracked-scripts/release-audit.sh
PASS tracked-skills/omnilane/SKILL.md
PASS executable-install.sh
PASS executable-bin/omnilane
PASS executable-scripts/dispatch.sh
PASS executable-scripts/jobs.sh
PASS executable-scripts/doctor.sh
PASS executable-scripts/ui.py
PASS executable-scripts/release-audit.sh
PASS executable-scripts/lib/job-timeout.pl
PASS executable-scripts/lib/job-worker.sh
PASS executable-scripts/runners/run-claude.sh
PASS executable-scripts/runners/run-codex.sh
PASS executable-scripts/runners/run-exec.sh
PASS executable-scripts/runners/run-gemini.sh
PASS executable-scripts/runners/run-grok.sh
PASS executable-scripts/runners/run-vote.sh
PASS no-forbidden-tracked-paths
PASS safe-tracked-path-names
PASS no-tracked-symlinks
PASS no-private-key-material
PASS rollback-documented
PASS manifest-hash
PASS archive-dry-run
WARN release-tag-not-required
release-audit: PASS target=0.63.5 tracked=740 manifest_sha256=262abfcc650e0d7879351c9ded9fa08e85f8a83d1e014321738540f28d7f3dec archive_sha256=90f34b7c580c6e87a06be0fa20c7b8bc84be806b66bf4ad2c0c8600826daa701
```

### 封存歷史與文件車道表

```sh
python3 -m unittest tests.test_aa_compat_entry tests.test_skill_lane_table
```

退出碼 `0`。

```text
.........
----------------------------------------------------------------------
Ran 9 tests in 1.917s

OK
```

### 工作樹命令列版本

```sh
bash bin/omnilane --version
```

退出碼 `0`。

```text
omnilane 0.63.5
```

### 實際載入政策並比對核准欄位及封存

```sh
python3 - <<'PY'
from pathlib import Path
import hashlib, json, sys
sys.path.insert(0, "scripts/lib")
import aa_policy, transport_compat
path = Path("config/aa-model-policy.json")
backup = Path(".test-scratch/neon-release-prep-1119/backup")
registry, digest = aa_policy.load_registry(path)
sid = registry["snapshot"]["id"]
print(f'{sid} {registry["snapshot"]["approval"]["status"]} {digest}')
assert registry["snapshot"]["approval"]["status"] == "approved"
before = json.loads((backup / path).read_bytes())
after = json.loads(path.read_bytes())
a = before["snapshot"].pop("approval")
b = after["snapshot"].pop("approval")
assert before == after
assert {k for k in a.keys() | b.keys() if a.get(k) != b.get(k)} == {"status", "estimated_scores"}
print("政策檔差異僅有 snapshot.approval.status、snapshot.approval.estimated_scores")
archive = Path("config/transport-compatibility.json")
assert hashlib.sha256(archive.read_bytes()).hexdigest() == transport_compat.APPROVED_ARCHIVE_SHA256
before = json.loads((backup / archive).read_bytes())
after = json.loads(archive.read_bytes())
entry = before["snapshots"][sid]
assert entry["registry_sha256"] == hashlib.sha256((backup / path).read_bytes()).hexdigest()
entry["registry_sha256"] = digest
assert before == after
print("相容性封存只變更當期 registry_sha256；政策與封存雜湊均與程式釘值相符")
for row in registry["scored_configs"]:
    if row["model"] == "gemini-4-neon":
        print(row["id"], row["effort"], row["score"])
PY
```

退出碼 `0`。

```text
aa-v5.0.1-2026-11-19-v1 approved 2e8ba8b802fac7ddd18f137ce72a5e5b040f10e67a8b2bb84ac0c7a4572b319f
政策檔差異僅有 snapshot.approval.status、snapshot.approval.estimated_scores
相容性封存只變更當期 registry_sha256；政策與封存雜湊均與程式釘值相符
gemini/gemini-4-neon xhigh 61
gemini/gemini-4-neon-high high 60
gemini/gemini-4-neon-medium medium 55
gemini/gemini-4-neon-low low 44
```

### 相對於 0.63.4 的完整車道比較

```sh
python3 - <<'PY'
from pathlib import Path
import re, subprocess
def lanes(text):
    return {m[1]: [v.strip() for v in m[2].split("|")] for line in text.splitlines()
            if (m := re.match(r"^([a-z][a-z-]*):\s*(.+)$", line.split("#")[0].rstrip()))}
old = lanes(subprocess.check_output(["git", "show", "HEAD:routing.yaml"], text=True))
new = lanes(Path("routing.yaml").read_text())
assert old.keys() == new.keys() and len(new) == 12
neon = "gemini gemini-4-neon-high -"
active = []
for name, chain in new.items():
    assert old[name][0] == chain[0]
    if name == "arbitrate":
        assert chain == old[name] == ["off - -"]
        continue
    assert chain.count(neon) == 1 and chain.index(neon) == chain.index("gemini gemini-4-hydrogen-high -") + 1
    assert [row for row in chain if row != neon] == old[name]
    active.append(name)
print("十二條首選與 HEAD 相同，arbitrate 停用；下列十一條僅在 Hydrogen high 後增加 Neon high")
print(", ".join(active))
PY
```

退出碼 `0`。

```text
十二條首選與 HEAD 相同，arbitrate 停用；下列十一條僅在 Hydrogen high 後增加 Neon high
hardest-coding, bulk-mechanical, triage, hard-judgment, taste-final, consult, ui-draft, long-context, fast-agentic, live-search, coding-overflow
```

### 版本與五語說明結構

```sh
python3 - <<'PY'
from pathlib import Path
import json, re
version = Path("VERSION").read_text().strip()
def obj(path): return json.loads(Path(path).read_bytes())
market = obj(".claude-plugin/marketplace.json")
versions = [version] + [obj(p)["version"] for p in ["package.json", "plugin.json", ".claude-plugin/plugin.json"]] + [market["metadata"]["version"], market["plugins"][0]["version"]]
assert versions == ["0.63.5"] * 6
print("五個版本檔，共六個值", *versions)
backup = Path(".test-scratch/neon-release-prep-1119/backup")
for p in sorted(Path().glob("README*.md")):
    s = p.read_text()
    headings = re.findall(r"^## .*v0\.63\.[45].*$", s, re.M)
    assert len(headings) == 2 and "0.63.5" in headings[0] and "0.63.4" in headings[1]
    body = s.split(headings[0], 1)[1].split(headings[1], 1)[0]
    items = [line for line in body.splitlines() if line.startswith("- ")]
    assert "omnilane resign" in items[0] and "aa-v5.0.1-2026-11-19-v1" in items[0]
    assert "npm i -g omnilane@0.63.5" in items[-1]
    assert [line for line in s.splitlines() if line.startswith("|")] == [line for line in (backup/p).read_text().splitlines() if line.startswith("|")]
    print(p, "重簽指引在首項、升級指令在末項、表格無異動")
PY
```

退出碼 `0`。

```text
五個版本檔，共六個值 0.63.5 0.63.5 0.63.5 0.63.5 0.63.5 0.63.5
README.ja.md 重簽指引在首項、升級指令在末項、表格無異動
README.ko.md 重簽指引在首項、升級指令在末項、表格無異動
README.md 重簽指引在首項、升級指令在末項、表格無異動
README.zh-CN.md 重簽指引在首項、升級指令在末項、表格無異動
README.zh-TW.md 重簽指引在首項、升級指令在末項、表格無異動
```

### 差異格式與暫存區

```sh
git diff --check && git diff --cached --name-only
```

退出碼 `0`。沒有輸出。

### 建置輸入與產物雜湊

```sh
shasum -a 256 .test-scratch/neon-1119/backup/config/aa-model-policy.json docs/reports/aa-v5.0.1-extract-2026-11-19.json .test-scratch/neon-release-prep-1119/backup/config/aa-model-policy.json config/aa-model-policy.json config/transport-compatibility.json scripts/lib/transport_compat.py
```

退出碼 `0`。

```text
42ac3db913098e902762f27cb76d6b83ca4f0223e77553cc9c18ed4dd14a9673  .test-scratch/neon-1119/backup/config/aa-model-policy.json
6d3429c6a1f2079938b9aaec3110f297025a64468df1eb8d502c83bf07324670  docs/reports/aa-v5.0.1-extract-2026-11-19.json
52092fd817370c338064f9712a67d777378e12eccf6437e44aca9b934de27628  .test-scratch/neon-release-prep-1119/backup/config/aa-model-policy.json
2e8ba8b802fac7ddd18f137ce72a5e5b040f10e67a8b2bb84ac0c7a4572b319f  config/aa-model-policy.json
315eaea7841dfba23c9fc7d94e951d7612d3d55f19c01cea03a70e87752235a6  config/transport-compatibility.json
87ff18aabc786c24f5e16d92545309584dfcdeed838445a2af9e7c6bc332df8d  scripts/lib/transport_compat.py
```

### 主機、工作樹與身分

```sh
pwd -P && hostname -s && git branch --show-current && git rev-parse HEAD && bash ~/.ssot/root/shared-memory/scripts/whoami-operator.sh codex
```

退出碼 `0`。

```text
/Users/vincentw/dev/omnilane-wt/feat-neon-aa-1119
MacStudio
feat/aa-20261119-gemini-4-neon
2b7368a8d9b8e6180a8127a6fda9c4078aabfec6
codex-s
```
