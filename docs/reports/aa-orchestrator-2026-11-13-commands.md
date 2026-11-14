# B 節驗收命令與原始輸出

來源：codex-s／MacStudio 工人；工作樹 aa-orchestrator；離線執行。

```sh
shasum -a 256 config/aa-model-policy.json
```
```text
fb5d05954fb69787be1c3b7f81bc44894adbfb72d67cc54636e34ff4bc7a9828  config/aa-model-policy.json

exit=0
```

```sh
python3 -m unittest tests.test_aa_policy tests.test_aa_routing_update tests.test_aa_orchestrator
```
```text
...............................s..........F.............
======================================================================
FAIL: test_direct_api_refuses_work_before_provider_call (tests.test_aa_routing_update.AARoutingUpdateTests.test_direct_api_refuses_work_before_provider_call)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/vincentw/dev/omnilane-wt/aa-orchestrator/tests/test_aa_routing_update.py", line 247, in test_direct_api_refuses_work_before_provider_call
    self.assertEqual(2, result.returncode, result.stdout + result.stderr)
    ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
AssertionError: 2 != 1 : omnilane: ps cannot read process start times here, so no job was started. A sandbox usually causes this; rerun outside the sandbox (from a normal terminal, or by approving the unsandboxed run).


----------------------------------------------------------------------
Ran 56 tests in 10.513s

FAILED (failures=1, skipped=1)

exit=1
```

```sh
/usr/bin/python3 -m unittest tests.test_aa_policy tests.test_aa_routing_update
```
```text
...............................s..........F.....
======================================================================
FAIL: test_direct_api_refuses_work_before_provider_call (tests.test_aa_routing_update.AARoutingUpdateTests)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/vincentw/dev/omnilane-wt/aa-orchestrator/tests/test_aa_routing_update.py", line 247, in test_direct_api_refuses_work_before_provider_call
    self.assertEqual(2, result.returncode, result.stdout + result.stderr)
AssertionError: 2 != 1 : omnilane: ps cannot read process start times here, so no job was started. A sandbox usually causes this; rerun outside the sandbox (from a normal terminal, or by approving the unsandboxed run).


----------------------------------------------------------------------
Ran 48 tests in 7.844s

FAILED (failures=1, skipped=1)

exit=1
```

```sh
for f in scripts/*.py scripts/lib/*.py; do /usr/bin/python3 -c "import ast;ast.parse(open('$f').read(),feature_version=(3,9))"; done
```
```text

exit=0
```

```sh
for lane in $(bash scripts/dispatch.sh --help 2>/dev/null | true); do :; done
```
```text

exit=0
```

```sh
bash bin/omnilane list | grep -c orchestrator
```
```text
0

exit=1
```

```sh
git diff --stat f984308
```
```text
 CHANGELOG.md                        |    3 +
 README.ja.md                        |    1 +
 README.ko.md                        |    1 +
 README.md                           |    1 +
 README.zh-CN.md                     |    1 +
 README.zh-TW.md                     |    1 +
 config/aa-model-policy.json         | 1563 ++++++++++++++++++++---------------
 config/transport-compatibility.json |    2 +-
 docs/model-capabilities-2026-09.md  |    9 +
 scripts/aa_rebaseline.py            |   68 +-
 scripts/lib/aa_policy.py            |   10 +-
 scripts/lib/build_overlay.py        |   15 +-
 scripts/lib/transport_compat.py     |    2 +-
 skills/omnilane/SKILL.md            |    2 +
 tests/test_aa_policy.py             |    6 +-
 tests/test_origin_integration.py    |    4 +-
 16 files changed, 1002 insertions(+), 687 deletions(-)

exit=0
```

```sh
git diff f984308 -- routing.yaml | wc -l
```
```text
       0

exit=0
```

```sh
git diff --check
```
```text

exit=0
```

```sh
python3 -m unittest tests.test_aa_orchestrator tests.test_aa_build_shape tests.test_aa_value_pool tests.test_aa_compat_entry tests.test_native_hydrogen_regression
```
```text
......................................
----------------------------------------------------------------------
Ran 38 tests in 20.626s

OK

exit=0
```

```sh
python3 scripts/aa_rebaseline.py compat-entry
```
```text
compat-entry: aa-v5.0.1-2026-11-13-v2 matches the archive

exit=0
```

## 產生與相容封存

```sh
python3 scripts/aa_rebaseline.py build --extract docs/reports/aa-v5.0.1-extract-2026-11-13.json --as-of 2026-11-13 --base .omnilane-tmp/aa-orchestrator-backup/aa-model-policy.json --approval approved --revision 2
```
```text
build: 98 scored (23 estimated), 60 unknown, dropped []
build: sha256 fb5d05954fb69787be1c3b7f81bc44894adbfb72d67cc54636e34ff4bc7a9828
exit=0
```

```sh
python3 scripts/aa_rebaseline.py compat-entry --write
```
```text
compat-entry: wrote aa-v5.0.1-2026-11-13-v2; archive sha256 79fda667b8b6813201f5f6f60d5cb60436d1ab74555bbd4b1b7221dae978b274
exit=0
```

```sh
python3 scripts/aa_rebaseline.py report --old .omnilane-tmp/aa-orchestrator-backup/aa-model-policy.json
```
```text
report: wrote 4 vendor reports under /Users/vincentw/dev/omnilane-wt/aa-orchestrator/docs/reports
exit=0
```

另外以相同參數、`REGISTRY` 指向 `.omnilane-tmp/aa-orchestrator-rebuilt.json` 重建並逐位元組比較正式產物，輸出 `Rebuild byte equality: PASS`。既有 75 列的 score 與 score_raw 比對輸出 `Original measured score changes: []`。

`grep -c orchestrator` 輸出 0 且退出碼 1，符合「無命中」的預期。`git diff --stat` 不含尚未追蹤的新檔；完整新檔見主報告與 `git status --short`。

## 受保護檔案與歷史封存

```sh
python3 -m unittest tests.test_origin_integration.IntegrationTests.test_protected_files_are_byte_identical_to_base tests.test_origin_integration.IntegrationTests.test_protected_files_match_base_commit_when_history_is_available
```
```text
..
----------------------------------------------------------------------
Ran 2 tests in 2.239s

OK
exit=0
```

舊相容封存項逐項比對：`Historical compatibility entries unchanged: PASS`。

程式圖譜的補充查詢曾回覆：`MCP tool call requires approval, but approval policy is never`。未嘗試取得權限或改走另一主機；剩餘工作只使用本工作樹既有可讀檔案與已授權測試。

## 完整套件與最後修正

```sh
bash tests/run.sh > .omnilane-tmp/aa-orchestrator-full-suite.txt 2>&1
```
```text
94 passed, 60 failed
exit=1
```

此為整套首次執行的原始結果；非 overlay 失敗存在，未達指定驗收標準。完整輸出保留於 `.omnilane-tmp/aa-orchestrator-full-suite.txt`。其中 Argon 探測清單的舊預期已修正為 high、low、medium 三列，後續重跑整個模組：

```sh
python3 -m unittest tests.test_resign
```
```text
...........................................................................
----------------------------------------------------------------------
Ran 75 tests in 30.196s

OK
exit=0
```

未再重跑已知受沙箱阻塞的整套測試，不能把模組補驗換算成整套通過。

## 最終工作樹差異

```sh
git diff --stat f984308
```
```text
 CHANGELOG.md                        |    3 +
 README.ja.md                        |    1 +
 README.ko.md                        |    1 +
 README.md                           |    1 +
 README.zh-CN.md                     |    1 +
 README.zh-TW.md                     |    1 +
 config/aa-model-policy.json         | 1563 ++++++++++++++++++++---------------
 config/transport-compatibility.json |    2 +-
 docs/model-capabilities-2026-09.md  |    9 +
 scripts/aa_rebaseline.py            |   68 +-
 scripts/lib/aa_policy.py            |   10 +-
 scripts/lib/build_overlay.py        |   15 +-
 scripts/lib/transport_compat.py     |    2 +-
 skills/omnilane/SKILL.md            |    2 +
 tests/test_aa_policy.py             |    6 +-
 tests/test_origin_integration.py    |    4 +-
 tests/test_resign.py                |    6 +-
 17 files changed, 1006 insertions(+), 689 deletions(-)
exit=0
```

```sh
git diff f984308 -- routing.yaml | wc -l
```
```text
       0
exit=0
```

```sh
git diff --check
```
```text
exit=0
```

```sh
git status --short
```
```text
 M CHANGELOG.md
 M README.ja.md
 M README.ko.md
 M README.md
 M README.zh-CN.md
 M README.zh-TW.md
 M config/aa-model-policy.json
 M config/transport-compatibility.json
 M docs/model-capabilities-2026-09.md
 M scripts/aa_rebaseline.py
 M scripts/lib/aa_policy.py
 M scripts/lib/build_overlay.py
 M scripts/lib/transport_compat.py
 M skills/omnilane/SKILL.md
 M tests/test_aa_policy.py
 M tests/test_origin_integration.py
 M tests/test_resign.py
?? .omnilane-cache/
?? docs/reports/aa-claude-evidence-2026-11-13-v2.md
?? docs/reports/aa-codex-evidence-2026-11-13-v2.md
?? docs/reports/aa-gemini-evidence-2026-11-13-v2.md
?? docs/reports/aa-grok-evidence-2026-11-13-v2.md
?? docs/reports/aa-orchestrator-2026-11-13-commands.md
?? docs/reports/aa-orchestrator-2026-11-13.md
?? docs/reports/aa-rebaseline-2026-11-13-v2.md
?? tests/test_aa_orchestrator.py
exit=0
```
