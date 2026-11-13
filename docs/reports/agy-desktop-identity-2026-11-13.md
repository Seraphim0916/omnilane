# Antigravity 桌面版主控身分辨識實作報告（2026-11-13）

- **來源**：`agy-s`／MacStudio 被控端 4（`6b4380e1-1656-40ad-8671-272990f3552f`），主控端 `agy-s`（`09f3fe96-3a15-48a0-996d-61e5c3e7ec38`）協同驗收
- **時間**：2026-11-13 18:53 CST（於 `227f77c` 基底重跑全數驗收並更新）
- **依據任務書**：
  - `/Users/vincentw/.ssot/root/task-records/2026-11-13-replan/agy-s/TASK-AGY-DESKTOP-IDENTITY.md`
  - `/Users/vincentw/.ssot/root/task-records/2026-11-13-omnilane-agy-identity/IMPL-BRIEF.md`（SHA256[:16] `a8c0ed3e2f540220`）
- **工作樹與分支**：`/Users/vincentw/dev/omnilane-wt/agy-identity`（分支 `feat/agy-desktop-identity`，基底 `227f77c`（`0.62.0` 分支最新提交，含 `agy` 工作代理修正；已確認與 `227f77c` 無衝突並完整保留全部改動），**未提交、未推送**）

---

## 1. 實作摘要

1. **祖先鏈辨識（`scripts/lib/caller_identity.py`）**：
   - 新增 `_is_antigravity_desktop()`，僅比對 `os.path.realpath()`：目標行程實際路徑須正好為 `/Applications/Antigravity.app/Contents/Resources/bin/language_server`，且其直接父行程實際路徑須正好為 `/Applications/Antigravity.app/Contents/MacOS/Antigravity`，不比對行程短名稱。
2. **環境變數隔離、對話代號驗證與複合指令殼層提示**：
   - 擴充 `_thread_environment()`，僅從原始環境位元組比對擷取 `CODEX_THREAD_ID` 與 `ANTIGRAVITY_CONVERSATION_ID`（同一行程重複定義即拒絕），完全不解碼、不保存、不記錄 `ANTIGRAVITY_CSRF_TOKEN`、`ANTIGRAVITY_LS_ADDRESS` 等任何其他環境變數。
   - 驗證目前行程與 `language_server` 直接子行程初始環境的 `ANTIGRAVITY_CONVERSATION_ID` 皆須為標準 UUID 且一致，並檢查 `pid` 至 `child` 之間的中繼行程鏈無衝突值。
   - 當複合指令（`;`、`&&`、`|`、subshell 或 `VAR=val cmd`）使直接子行程停留在 `SHELLS`（如 `/bin/zsh`，macOS `CS_PLATFORM_BINARY` 會使 `KERN_PROCARGS2` 無法取得 `envp`）時，維持拒絕不跳過直接子行程，並附上 `; run the omnilane command as the only command of the tool call, with no `;`, `&&`, `|`, subshell or VAR=value shell prefix around it (use env VAR=value if needed)` 提示。
3. **唯讀 SQLite 與最小純 Python 協定緩衝區（protobuf）讀取器**：
   - 檢查 `~/.gemini/antigravity/conversations` 目錄與 `<uuid>.db` 檔案（非符號連結、一般檔案、擁有者 `st_uid == os.getuid()`），以 `file:...?mode=ro` 唯讀開啟，僅查詢 `SELECT data FROM executor_metadata ORDER BY idx DESC LIMIT 1`。
   - 新增 `_read_varint()`、`_iter_proto_fields()`、`_proto_path_values()`：安全處理 wire type `0`（varint）、`1`（64-bit 固定）、`2`（length-delimited）、`5`（32-bit 固定），對不支援之 wire type 或截斷溢位立即拒絕；嚴格取路徑 `(10, 1, 28)`，若結果數為 `0` 或 `> 1` 一律拒絕，絕不使用正規表示式掃描。
4. **模型拆解、非 Gemini 拒絕與 `0.63.0 FIX1` 相容**：
   - 拆解 `<model>-<effort>`；凡非 Gemini 模型（如 `claude-*`、`gpt-*`、`o1-*`、`o3-*`、`o4-*` 或拆解後非 `gemini-*`）一律拒絕並回傳包含「`Antigravity 的非 Gemini 模型尚未支援`」之訊息。
   - 抽出共用輔助函式 `is_authority_eligible_row(row)`（`not row.get("estimated") and bool(row.get("authority_eligible", True))`），於 `caller_identity.py` 與 `native_context.py` 共用，確保與 `0.63.0 FIX1` 無縫相容。
5. **Harness 命名、`native_context.py` 對齊與 `[redacted]` 誤遮罩修正**：
   - `caller_identity.py` 沿用既有 `write_context()` 格式（`caller-context/<vendor>--<model-effort>.json`，欄位與 Codex 相同，同使用者可寫之主機自述等級），並在 `source` 標記為 `antigravity-desktop conversation <uuid>`。
   - `scripts/lib/native_context.py` 接住 `read_caller()` 回傳的 `source`：當 `isinstance(source, str) and source.startswith("antigravity-desktop")` 且未指定 `--harness` 時，預設 harness 設為 `"antigravity-desktop"`（CLI `agy` 之 `source == ""` 維持 `"antigravity"`），且不寫入 `"caller_identity_verified": false`（與 Codex 同級），並套用 `is_authority_eligible_row(row)` 檢查。
   - `scripts/lib/refusal_evidence.py` 的 `caller_guidance()` 新增 Antigravity 專屬步驟分支，避免把 `>= 24` 字元之環境變數名或長模型字串原樣帶入 `human_line()` 而觸發 `sanitise_human_line()` 誤遮罩成 `[redacted]`。
6. **文件更新**：
   - `docs/native-executor.md`：`### Generating the capability file` 一節補上 Antigravity 桌面版辨識機制、`10.1.28` 路徑、單一指令／`env VAR=value` 注意事項與同使用者可寫之信任等級說明。
   - `skills/omnilane/SKILL.md`：僅更新 `| Antigravity |` 那一列的身分辨識說明，保留其餘內容與五語 `README*`、`CHANGELOG.md`、`config/aa-model-policy.json` 不動。

---

## 2. 驗收指令與輸出

```text
=== 1. python3 -m unittest tests.test_caller_identity_antigravity -v ===
Ran 12 tests in 0.114s
OK

=== 2. python3 -m unittest discover -s tests -p "test_caller_identity*.py" ===
Ran 168 tests in 6.821s
OK

=== 3. python3 -m unittest discover -s tests -p "test_native*.py" ===
Ran 115 tests in 48.500s
OK

=== 4. /usr/bin/python3 -m unittest tests.test_caller_identity_antigravity ===
Ran 12 tests in 0.148s
OK

=== 5. python3 -m unittest tests.test_refusal_consistency tests.test_agy_workspace_policy -v ===
Ran 16 tests in 34.532s
OK

=== 6. shasum -a 256 config/aa-model-policy.json ===
a95b811703c654115014122e8651578b70915d26736cfa754c3e0b2908bd8076  config/aa-model-policy.json

=== 7. git diff --stat（相對於 HEAD = 227f77c）===
 docs/native-executor.md         |  24 ++++
 scripts/lib/caller_identity.py  | 250 +++++++++++++++++++++++++++++++++++++++-
 scripts/lib/native_context.py   |  14 ++-
 scripts/lib/refusal_evidence.py |  27 +++++
 skills/omnilane/SKILL.md        |   2 +-
 5 files changed, 307 insertions(+), 10 deletions(-)

=== 8. git diff --check ===
（無輸出，結束碼 0）

=== 9. R1 補正驗收（補 import offline_env 後，2026-11-13 19:47 CST）===
# python3 -m unittest tests.test_000_isolation tests.test_caller_identity_antigravity -v
Ran 27 tests in 1.256s
OK

# /usr/bin/python3 -m unittest tests.test_000_isolation tests.test_caller_identity_antigravity
Ran 27 tests in 3.607s
OK
```

---

## 3. 實機對話實跑 `bin/omnilane whoami` 輸出

### 被控端 4（`6b4380e1-1656-40ad-8671-272990f3552f`，`227f77c` 基底 `18:52 CST` 重跑；單一指令 `env OMNILANE_HOME=/tmp/omnilane-smoke-6b4380e1 /Users/vincentw/dev/omnilane-wt/agy-identity/bin/omnilane whoami`，未改動 `~/.omnilane`）

```text
omnilane: caller is gemini/gemini-4-hydrogen-medium (score 41), read from pid 34612, antigravity-desktop conversation 6b4380e1-1656-40ad-8671-272990f3552f
/tmp/omnilane-smoke-6b4380e1/caller-context/gemini--gemini-4-hydrogen-medium.json
```
結束碼：`0`

### 主控端（`09f3fe96-3a15-48a0-996d-61e5c3e7ec38`，`227f77c` 基底 `18:51 CST` 重跑；單一指令 `env OMNILANE_HOME=/tmp/omnilane-smoke-09f3fe96 /Users/vincentw/dev/omnilane-wt/agy-identity/bin/omnilane whoami`，未改動 `~/.omnilane`）

```text
omnilane: caller is gemini/gemini-4-carbon-medium (score 58), read from pid 34612, antigravity-desktop conversation 09f3fe96-3a15-48a0-996d-61e5c3e7ec38
/tmp/omnilane-smoke-09f3fe96/caller-context/gemini--gemini-4-carbon-medium.json
```
結束碼：`0`（另實測 `bin/omnilane native-context --inherits-caller-runtime` 亦回 `0`，產出 `"harness": "antigravity-desktop"` 且無 `"caller_identity_verified": false`）。

---

## 4. AI-Native Gate 六項檢核

1. **基準測試（Baseline）**：實作前先跑 `python3 -m unittest discover -s tests -p "test_caller_identity*.py"`，156 項全過（6.402s）。
2. **紅綠測試驅動開發（Red-Green TDD）**：先寫 `tests/test_caller_identity_antigravity.py` 跑出 Red，實作後轉 Green（12/12 OK），並補齊殼層提示、`human_line` 無 `[redacted]` 及 `native_context` harness 對齊回歸測試。
3. **實機手動煙霧測試**：於真實 Antigravity 桌面版被控端對話以單一指令 `env OMNILANE_HOME=/tmp/omnilane-smoke-6b4380e1 /Users/vincentw/dev/omnilane-wt/agy-identity/bin/omnilane whoami` 執行，結束碼 `0`、正確讀出 `gemini/gemini-4-hydrogen-medium (score 41)`，且未改動正式 `~/.omnilane`。
4. **審查**：初審發現之 3 項細節已全部補齊，主控端已於最終版程式碼完成實機驗收，待 `agy-m` 第二方審查。
5. **可重用測試產物**：`tests/test_caller_identity_antigravity.py`（12 項全合成 SQLite 與合成 protobuf 測試，不讀取真實 `~/.gemini`）。
6. **未提交工作樹說明**：所有變更僅限 `/Users/vincentw/dev/omnilane-wt/agy-identity`（分支 `feat/agy-desktop-identity`），未執行 `git commit` 或 `git push`。

---

## 5. 未驗證項

1. Antigravity 對話中途於介面下拉選單切換模型後，`conversations/<uuid>.db` 的 `executor_metadata` 最新列是否同步更新為新模型（依原研究報告標記未驗證）。
2. 全套 `tests/run.sh`、MacMini 實跑與 `agy-m` 第二方審查（依任務書由後續流程執行）。

---

## 6. 改動檔清單與 SHA256

| 狀態 | 檔案路徑 | SHA256 |
|---|---|---|
| 修改 | `scripts/lib/caller_identity.py` | `63ef9d26d476598197825811e92d1dac3f94cb8455ba2ce95afef5c6e6ee098c` |
| 修改 | `scripts/lib/refusal_evidence.py` | `91d45ffaf54d51e2ca0d81ba0bcae7bcb0e99121ea1b68d3c86bf1f9a40afa2c` |
| 修改 | `scripts/lib/native_context.py` | `dd52384167fa1675918e3e18a94290dea23c4b5fcc04610d7b1580e4fc702706` |
| 新增 | `tests/test_caller_identity_antigravity.py` | `0897ecd274daf1b29c0bd832fbae08659345f8f0ee3c7b81683db57639450acd` |
| 修改 | `docs/native-executor.md` | `fe2bf04e586131f9e350aad5f5aed9f52ef0165dc542038e380fbefeffe8bf5d` |
| 修改 | `skills/omnilane/SKILL.md` | `10f66b489a5282c37a4b36f8debbaede72d7bb8cba8a0b64fca32748e3b7476d` |
| 未動（驗證不變） | `config/aa-model-policy.json` | `a95b811703c654115014122e8651578b70915d26736cfa754c3e0b2908bd8076` |
