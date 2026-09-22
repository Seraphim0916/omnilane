# AA v4.3.2 補列證據：Claude Opus 5.5 及另外 7 個未收錄 slug（2026-09-23）

狀態：`approval.status = approved`（Vincent 於 2026-09-23 明示核准；核准版雜湊見下方）。

## 來源

| 項目 | 值 |
|---|---|
| 抽取檔（入庫副本） | `docs/reports/aa-v4.3.2-extract-2026-09-23.json` |
| 抽取檔原件 | `/Users/vincentw/dev/omnilane-wt/packets/aa-extract-2026-09-23.json`（兩者 sha256 同為 `8d91857fe44f7f24dd2b351551b425eaa459391963e39c8df526a0d32f99cd2d`） |
| 來源網址 | https://artificialanalysis.ai/models/grok-4-7（整頁內嵌全部模型；各列個別頁見 `source_urls`） |
| 抓取時間 | 2026-09-23T01:27:48+08:00 |
| page_sha256 | `f3156f8739d6f11fa15fc2bfbfed78df19db2a270419dfb9de0bdf3fc9efc25c` |
| AA 版本 | Intelligence Index v4.3.2（與前一快照 `aa-v4.3.2-2026-09-22-v1` 同版） |
| 頁上紀錄數 / 保留廠商紀錄數 | 661 / 202 |

抽取檔複製進 repo 的原因：`aa_rebaseline.py build` 以 `Path(extract).resolve().relative_to(REPO)` 寫入 `snapshot.source.extract`，工作樹外的路徑會拋 `ValueError`；前一快照同樣把抽取檔放在 `docs/reports/`。

廠商自報圖 `~/Desktop/Pasted 2026-09-23 at 00.54.36.png`（327512 bytes，2026-09-23 00:54）僅作佐證，不作分數來源，未複製進 repo。

## 新增 12 列

| config id | vendor | model | effort | reasoning | score_raw | score | estimated | candidate_model_ids | 指數執行成本（USD） |
|---|---|---|---|---|---|---|---|---|---|
| claude/claude-opus-5-5 | claude | claude-opus-5-5 | max | adaptive | 57.62 | 58 | no | claude-opus-5-5 | 8708 |
| claude/claude-opus-5-5-xhigh | claude | claude-opus-5-5 | xhigh | adaptive | 55.99 | 56 | no | claude-opus-5-5 | 4057 |
| claude/claude-opus-5-5-high | claude | claude-opus-5-5 | high | adaptive | 53.58 | 54 | no | claude-opus-5-5 | 2172 |
| claude/claude-opus-5-5-medium | claude | claude-opus-5-5 | medium | adaptive | 51.24 | 51 | no | claude-opus-5-5 | 1627 |
| claude/claude-opus-5-5-low | claude | claude-opus-5-5 | low | adaptive | 42.31 | 42 | no | claude-opus-5-5 | 860 |
| codex/gpt-5-3-codex | codex | gpt-5.3-codex | xhigh | reasoning | 32.50 | 33 | yes | gpt-5.3-codex | — |
| codex/gpt-5-5-instant-06-26 | codex | gpt-5.5-instant | （無） | unspecified | 26.01 | 26 | no | gpt-5.5-instant | — |
| gemini/gemini-3-5-flash-lite | gemini | gemini-3.5-flash-lite | high | unspecified | 22.17 | 22 | no | gemini-3.5-flash-lite-high | — |
| codex/gpt-oss-120b | codex | gpt-oss-120b | high | reasoning | 11.60 | 12 | no | gpt-oss-120b | — |
| codex/gpt-oss-120b-low | codex | gpt-oss-120b | low | reasoning | 10.21 | 10 | yes | gpt-oss-120b | — |
| codex/gpt-oss-20b-low | codex | gpt-oss-20b | low | reasoning | 9.95 | 10 | yes | gpt-oss-20b | — |
| codex/gpt-oss-20b | codex | gpt-oss-20b | high | reasoning | 8.97 | 9 | no | gpt-oss-20b | — |

12 列的 `transport_mapping` 全部是 `status: unknown`、`runtime_verified: false`，不宣稱任何 CLI 叫得到。

列外形的來源：Opus 5.5 各列複製自同努力等級的 `claude/claude-opus-5*`；gpt-5.3-codex、gpt-oss 高等級列複製自 `codex/gpt-5-4`，gpt-oss low 列複製自 `codex/gpt-5-4-low`；GPT-5.5 Instant 複製自 `codex/gpt-5-5-non-reasoning`；Flash-Lite 複製自 `gemini/gemini-3-7-flash`。

歸屬與命名判斷：
- OpenAI 系列（含 gpt-oss）依任務書歸 `codex`；Google 歸 `gemini`。
- model id 照 AA 名稱與既有列寫法：`gpt-5.3-codex`、`gpt-5.5-instant`、`gemini-3.5-flash-lite`、`gpt-oss-120b`、`gpt-oss-20b`。
- GPT-5.5 Instant：AA 沒標努力等級，也沒標是否推理，所以 effort 設 `null`、reasoning 設 `unspecified`，不猜成 non-reasoning。
- Gemini 的既有候選 id 會把努力等級接在 model 後面（`gemini-3.8-flash-high`），`target_row` 也這樣比對；build 原本一律寫 `[model]`，已改成新 gemini 列寫 `model-effort`。這行只影響新增的 gemini 列。

Opus 5.5 缺的欄位（五列都是 null，AA 尚未公布）：`mlcrOverall`、`intelligenceIndexTimePerTask`、`timeToFirstAnswerToken`，另外 `gpqa`、`terminalBench21` 也是 null。`omniscienceBreakdown`（幻覺率）在 09-23 抽取檔 202 筆與 09-22 抽取檔 197 筆裡全部是 null，屬既有缺口，不是這次抓取遺失。

別名：`scripts/configure.sh` 的 `CLAUDE_MODELS`／`CODEX_MODELS`／`GEMINI_MODELS` 都沒有這 12 列的 model，依「別名鏡射 configure.sh 目錄」的規則沒有新增別名，aliases 與舊檔相同（48 筆）。`GEMINI_MODELS` 有 `gpt-oss-120b-medium`（Antigravity 目錄），但 AA 沒有 medium 列，所以沒對上。

## 其他列的變動

以同一份抽取檔重新計分，既有 83 列的 score、score_raw、estimated 都沒變；只有 `as_of` 改成 2026-09-23，`evidence_report` 改指 `docs/reports/aa-{vendor}-evidence-2026-09-23.md`（build 的既有行為）。unknown_configs 7 筆的 id 不變。coverage：scored 從 83 變 95；各廠商 codex 34→40、claude 31→36、gemini 7→8、grok 11 不變。

## 雜湊

| 項目 | sha256 |
|---|---|
| 舊政策檔（main `c196c71`，v0.45.0） | `a1109913b9928d943bc440787d26caaf7818e40abdaae32899750d65e5eb5fdd` |
| 新政策檔（proposed） | `ca1cdfe03b89e6779e33c8373409aa232adfc5490463a615dae0b34b4be55aaf` |
| 新政策檔（approved，最終） | `b891f5030eb9a1cb4726233195f8835f2fa6e82260223392cd5e3216bfd779cc` |

可重現性：proposed 版以舊檔副本為 base 連跑兩次，再用 `--base config/aa-model-policy.json` 連跑兩次，四次產物的 sha256 都是 `ca1cdfe0…5aaf`。核准版以舊檔副本為 base、`--approval approved` 連跑兩次，兩次都是 `b891f503…79cc`；與 proposed 版只差 `snapshot.approval.status`（proposed→approved）與 `snapshot.approval.estimated_scores`（provisional_pending_review→approved_provisional）。

`scripts/lib/aa_policy.py` 的釘選已同步：`APPROVED_AS_OF = "2026-09-23"`、`APPROVED_REGISTRY_SHA256 = "b891f503…79cc"`；`APPROVED_BENCHMARK_VERSION` 維持 `4.3.2`。

## report 輸出

```
$ python3 scripts/aa_rebaseline.py report --old packets/scratch-opus55/aa-model-policy.base.json
report: wrote 4 vendor reports under /Users/vincentw/dev/omnilane-wt/aa-opus55/docs/reports
```

產出 `docs/reports/aa-{claude,codex,gemini,grok}-evidence-2026-09-23.md`；12 列新增在各廠商表中標為 `new`，其餘列的 previous 與新分數都相同。

## 已知差異

- 快照的 `approval.source` 指向 `docs/reports/aa-rebaseline-2026-09-23.md`（build 固定產生的名稱），已補建該摘要檔並連回本檔。
- `aa_policy.py` 不檢查 `approval.status` 欄位，生效與否只看釘選雜湊；核准版已重建並同步釘選。
- gpt-oss-20b 的 low（9.95，估計值）分數高於 high（8.97，非估計值），這是 AA 的原始資料，照收不修。
