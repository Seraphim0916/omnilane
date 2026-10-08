# Claude Haiku 5.5／AA 多頁核准快照證據（2026-10-08）

來源：codex-s／MacStudio；工作樹 `feat/aa-haiku-5-5`，基底 `cc1aecc`。

狀態：**操作者已於 2026-10-08 核准快照，主機尚未部署**。資料來自主控提供的離線抽取檔，沒有重新連線抓取。
原始 HTML 未隨任務提供，所以頁面雜湊沿用抽取檔記載，本輪未重新計算 HTML 雜湊。

## 1. 快照與可重現性

- 快照：`aa-v4.3.2-2026-10-08-v1`；`approval.status=approved`。
- 政策檔 SHA256：`3a7455858d4c2eba255eb57bb1fcf8235fd91cecc4685d9c965a99fb16736ad5`。
- 合併抽取檔 SHA256：`91450bcb7442b4d6699d779d4951c900485db8058db053c4dbd0880536aba415`。
- 39 頁、231 筆廠商資料；120 列計分、24 列估計分數、10 列未知。
- 兩次連續執行下列 build 指令，政策檔 SHA256 相同。
- `snapshot.source` 保存整份抽取檔雜湊與各頁資訊，沒有把多頁雜湊冒稱為單頁雜湊。

```sh
python3 scripts/aa_rebaseline.py merge --inputs docs/reports/aa-pages-2026-10-08/*.json --out docs/reports/aa-v4.3.2-extract-2026-10-08.json
python3 scripts/aa_rebaseline.py build --extract docs/reports/aa-v4.3.2-extract-2026-10-08.json --as-of 2026-10-08 --approval approved
python3 scripts/aa_rebaseline.py build --extract docs/reports/aa-v4.3.2-extract-2026-10-08.json --as-of 2026-10-08 --approval proposed
```

## 2. 合併規則與來源

規則 `own-page-then-latest-populated-v1`：每個欄位先取非空值，再依下列順序選擇。

1. 模型自己的頁面優先：網址最後一節須等於完整 AA slug，包含強度尾碼。
2. 沒有自己的非空值時，取抓取時間較晚的非空值，比較含時區的實際時間。
3. 同時刻以來源網址、頁面 SHA256 的字典順序裁決，輸入順序不影響結果。
4. 全為空值時維持空值；不跨日期補值，不把空值當零。

抓取時間只是可稽核的裁決依據，不能證明 AA 伺服器資料版本較新。此次兩筆分數衝突都有
模型自己的頁面，不依賴頁面樣式或筆數猜新舊。`record_sources.preferred_page` 是首選頁，
`fields` 逐欄列出真正採用頁面；稀疏頁面需要補值時兩者可能不同。
相同捕捉重複輸入會去重；來源／時間／雜湊相同但資料不同、基準版本不同或無時區，均拒絕合併。

共有 304 個欄位差異：17 個有不同非空值（2 個分數、15 個名稱），另外 287 個是缺值／有值差異。第 6 節完整列出。

| 頁面 | 來源網址 | 抓取時間 | 原頁／廠商筆數 | 頁面 SHA256 |
|---|---|---|---|---|
| page-001 | https://artificialanalysis.ai/models/claude-4-5-haiku | 2026-10-08T10:43:21+08:00 | 192 / 21 | `680731e7b9d88c875ff320ec882cbe3069211a52a055ca70f8d8a5859906d322` |
| page-002 | https://artificialanalysis.ai/models/claude-4-5-sonnet | 2026-10-08T10:43:25+08:00 | 696 / 231 | `4753c303fb4062accdb6b1d0cb0ccd462941b81e9ed50e1d6b58b7016315178c` |
| page-003 | https://artificialanalysis.ai/models/claude-4-5-sonnet-thinking | 2026-10-08T10:43:29+08:00 | 696 / 231 | `d1f16728cad6214d0445ceb2c60b217297d31d2af840cf1f4ad70ad7e43e57c2` |
| page-004 | https://artificialanalysis.ai/models/claude-fable-5 | 2026-10-08T10:43:33+08:00 | 696 / 231 | `0d6a925a8f20fd9bb545dc92924b497f1dbfce4e711a8c81e9e3e8560cd03c18` |
| page-005 | https://artificialanalysis.ai/models/claude-fable-5-1 | 2026-10-08T10:43:36+08:00 | 202 / 33 | `b709449f1df5bde0878e440874fede2859595b138ff29380555178f6eed0eb60` |
| page-006 | https://artificialanalysis.ai/models/claude-haiku-5-5 | 2026-10-08T10:43:38+08:00 | 196 / 26 | `ddc4431e249520b3011fff51b63ba7b3ed9050986c0f0962fbc6be0e174925c2` |
| page-007 | https://artificialanalysis.ai/models/claude-opus-4-5 | 2026-10-08T10:43:45+08:00 | 696 / 231 | `bb0dfbda4f12c270bac5198446b7f2df1c317034e27a0d14b8e8b974c923fe19` |
| page-008 | https://artificialanalysis.ai/models/claude-opus-4-5-thinking | 2026-10-08T10:43:50+08:00 | 696 / 231 | `2a30a1fdd21f5b59801cf58097b4bc63d8c2e00a3d5797fa667bfc0c3716c92d` |
| page-009 | https://artificialanalysis.ai/models/claude-opus-4-6 | 2026-10-08T10:43:56+08:00 | 696 / 231 | `2b7e0f9553b134166a93117437392fd97b86782bfb41b7a93f86c193703c2417` |
| page-010 | https://artificialanalysis.ai/models/claude-opus-4-7 | 2026-10-08T10:44:02+08:00 | 696 / 231 | `a577cdd298ea0e39cfe48ee2d704e6b7cc2a81e86ed9094d2b57810120311592` |
| page-011 | https://artificialanalysis.ai/models/claude-opus-4-8 | 2026-10-08T10:44:06+08:00 | 696 / 231 | `740f19e8f1a510a8d81a28c86dd3d659a60d270040650faeb361b293535df10c` |
| page-012 | https://artificialanalysis.ai/models/claude-opus-5 | 2026-10-08T10:44:10+08:00 | 696 / 231 | `1831cc0cf504155b8d2d9402cafd4b9b7addb081e1fcc3733de363543765874f` |
| page-013 | https://artificialanalysis.ai/models/claude-opus-5-5 | 2026-10-08T10:44:13+08:00 | 202 / 33 | `7bf29191687e6d6eda2f446240cdc416a0a2dc94baf6a0d948a5e5f039d111cb` |
| page-014 | https://artificialanalysis.ai/models/claude-sonnet-4-6 | 2026-10-08T10:44:18+08:00 | 696 / 231 | `bbbd57c14f6952dc77a421ba13e27f30b24a4421886dc71c3f2bf723967e6035` |
| page-015 | https://artificialanalysis.ai/models/claude-sonnet-4-6-non-reasoning-low-effort | 2026-10-08T10:44:25+08:00 | 696 / 231 | `d274202e279fe61e0ed74f0e3c1ec23cd94e424cbe880f3bf5bb5d6e83fe358a` |
| page-016 | https://artificialanalysis.ai/models/claude-sonnet-5 | 2026-10-08T10:44:29+08:00 | 696 / 231 | `5be04e83791fed46eacb0317070a034b5d8949ebd51351a0f14a8aa6975edff2` |
| page-017 | https://artificialanalysis.ai/models/claude-sonnet-5-5 | 2026-10-08T10:44:32+08:00 | 202 / 33 | `aef39b6fc9eee9b17123ae4232f9aa94a39576cbda3b72dfd5e63d2439338be7` |
| page-018 | https://artificialanalysis.ai/models/gemini-3-1-pro-preview | 2026-10-08T10:44:36+08:00 | 696 / 231 | `bcbcbb54165915a53b25071139c1f628b5d51b56cb0fc10331298ba94299d776` |
| page-019 | https://artificialanalysis.ai/models/gemini-3-5-flash-lite | 2026-10-08T10:44:41+08:00 | 696 / 231 | `2f6fb4dc98e86dfb204fcc12a01bc467472666e6b3435400eb15a53a43a47100` |
| page-020 | https://artificialanalysis.ai/models/gemini-3-6-flash | 2026-10-08T10:44:45+08:00 | 696 / 231 | `88da46422d0e8f81c50f9d0251c01910b940772a12dbc0fa775089f82fa05c45` |
| page-021 | https://artificialanalysis.ai/models/gemini-3-7-flash | 2026-10-08T10:44:49+08:00 | 696 / 231 | `46ac18a20c1e6edcf3013d5882cb72a86584acc3a9e0db817d1d618102b4e222` |
| page-022 | https://artificialanalysis.ai/models/gemini-3-8-flash | 2026-10-08T10:44:52+08:00 | 201 / 32 | `78023462e470dea4c4607716e10c0dc825399b1c78cd1ca4ef3378e9744b1915` |
| page-023 | https://artificialanalysis.ai/models/gpt-5-3-codex | 2026-10-08T10:44:55+08:00 | 696 / 231 | `0c2a0f7752c90a7419f8b7f248c7d0643f8de5fdf85b77ed1cee311012d65e2f` |
| page-024 | https://artificialanalysis.ai/models/gpt-5-4 | 2026-10-08T10:44:58+08:00 | 696 / 231 | `c5a4ad20e7dfdfc1928afaaef5d654eebba64e45ad09701e1fa2930feedafbd8` |
| page-025 | https://artificialanalysis.ai/models/gpt-5-5 | 2026-10-08T10:45:02+08:00 | 696 / 231 | `82add539bc4a96f93c110f3a0fe56e3827ef05d0cc71e813cdb182bfbb9916e7` |
| page-026 | https://artificialanalysis.ai/models/gpt-5-5-instant-06-26 | 2026-10-08T10:45:05+08:00 | 696 / 231 | `423a02e9ff11dc3300fe13615c9a94df3ce3272afbd5474c05491ada5aed844a` |
| page-027 | https://artificialanalysis.ai/models/gpt-5-6-luna | 2026-10-08T10:45:09+08:00 | 696 / 231 | `fd57beee5b73a498a60e65c4558f51be40ec64d67b7f4fa7364f87028d20ea6f` |
| page-028 | https://artificialanalysis.ai/models/gpt-5-6-sol | 2026-10-08T10:45:12+08:00 | 696 / 231 | `2b5dc42c8dc2b3c8040b565d0a4bcbcd7f0dc632d6a193ca131e90d618bc9253` |
| page-029 | https://artificialanalysis.ai/models/gpt-5-6-terra | 2026-10-08T10:45:15+08:00 | 201 / 32 | `9c6a610e0c25d93af94dd745bc8eb496f88ffd1cbe827d6d5a8f78a354560757` |
| page-030 | https://artificialanalysis.ai/models/gpt-6-1-sol | 2026-10-08T10:45:17+08:00 | 202 / 33 | `965ce892ea48b7e91f61e78e256dc2d638d724b39c946732adaf41db3d3d98c0` |
| page-031 | https://artificialanalysis.ai/models/gpt-6-astra | 2026-10-08T10:45:19+08:00 | 202 / 33 | `6740fdc0ab1bc5db4d8246a8b378bc85c50cc6c8618b676996bc6eeac59306bc` |
| page-032 | https://artificialanalysis.ai/models/gpt-6-luna | 2026-10-08T10:45:22+08:00 | 196 / 26 | `7240ff11a96a00cae86315e828a1ba264252a20ea47fc4563584b18007f6582d` |
| page-033 | https://artificialanalysis.ai/models/gpt-6-sol | 2026-10-08T10:45:26+08:00 | 696 / 231 | `924a8565a709b8be6fde5a64473ae8fb41b07d5e174e97d8f7f00c9d4e337ddd` |
| page-034 | https://artificialanalysis.ai/models/gpt-oss-120b | 2026-10-08T10:45:28+08:00 | 189 / 18 | `3d28f67b2091b131af4f55db792051866c52872b4bdd1f142b21bc554eb8d30e` |
| page-035 | https://artificialanalysis.ai/models/gpt-oss-20b | 2026-10-08T10:45:31+08:00 | 191 / 18 | `c90774a5639821d7d2d808d3b04c51858a487688c2b7d064ebbd2589ef4f839a` |
| page-036 | https://artificialanalysis.ai/models/grok-4-3 | 2026-10-08T10:45:40+08:00 | 696 / 231 | `e97d398ceae0052bda96715ac7893fc7a3d70546c2d8ec24508118db1fa7b1da` |
| page-037 | https://artificialanalysis.ai/models/grok-4-5 | 2026-10-08T10:45:45+08:00 | 696 / 231 | `c4b5e150511d97355d62b2d845c93593121c1e72ee02e8f293f128a385b9c274` |
| page-038 | https://artificialanalysis.ai/models/grok-4-6 | 2026-10-08T10:45:51+08:00 | 696 / 231 | `67a6988554af7fba02a44b39299c9a79b4ee04020d4e977f188665de4729a613` |
| page-039 | https://artificialanalysis.ai/models/grok-4-7 | 2026-10-08T10:45:53+08:00 | 202 / 33 | `901127922f3ad6b86d24664234bb3a281dc7273d8f16aab269c7b6e8d37de692` |

## 3. Haiku 5.5 五列與欄位歸屬

| 強度 | slug | 原始分數 | 整數 | AA 強度 | 發布日 | 輸入／輸出每百萬價格 | 計分來源 |
|---|---|---|---|---|---|---|---|
| max | claude-haiku-5-5 | 43.40 | 43 | max | 2026-10-07 | 0.1 / 0.5 | page-006 |
| xhigh | claude-haiku-5-5-xhigh | 41.25 | 41 | xhigh | 2026-10-07 | 0.1 / 0.5 | page-038 |
| high | claude-haiku-5-5-high | 37.82 | 38 | high | 2026-10-07 | 0.1 / 0.5 | page-038 |
| medium | claude-haiku-5-5-medium | 34.46 | 34 | medium | 2026-10-07 | 0.1 / 0.5 | page-038 |
| low | claude-haiku-5-5-low | 29.45 | 29 | low | 2026-10-07 | 0.1 / 0.5 | page-038 |

- 分數、估計標記、價格、發布日與測驗欄位取自 AA。整數採十進位四捨五入，原始分數留兩位。
- `NEW_ROWS` 使用 Sonnet 5.5 的五強度形狀；max／xhigh／high／medium 複製同強度。
- 基底的 Sonnet 5.5 low 不計分，因此 Haiku low 複製 Sonnet max 的共通形狀，再明示覆寫
  effort、模型、slug、網址、分數與傳輸候選。Haiku 4.5 是 `effort=None, reasoning=reasoning`，
  不適合直接當五強度模板。
- 主控已於 2026-10-08 查證官方文件：模型代號為 `claude-haiku-5-5`，五種強度
  `low`、`medium`、`high`、`xhigh`、`max` 均受支援；Claude Code 自 v2.1.293 起支援。
  來源：[Model IDs](https://platform.claude.com/docs/en/models/haiku-5-5/overview)、
  [Effort](https://platform.claude.com/docs/en/build-with-claude/effort)、
  [Model configuration](https://code.claude.com/docs/en/model-config)。
  `reasoning=adaptive` 仍是比照既有形狀的提案，各強度實際送出的請求形狀尚未驗證。
- 五列維持 `transport_mapping.status=unknown`、`runtime_verified=false`；探測清單僅新增候選。
- `NEW_ALIASES` 複製 Sonnet 5.5 的別名形狀。歷史 v4.2 覆蓋清單只增加目錄登錄
  （`catalogEntries` 119 → 120），沒有偽造 v4.2 的 AA 列。

逐欄來源如下：

- `claude-haiku-5-5`：page-006 [apexAgents, automationBenchPartialScore, briefcaseBreakdown, contextWindowTokens, critpt, deprecated, effort_label, gdpvalNormalized, gpqa, hle, ifbench, intelligenceIndex, intelligenceIndexCost, intelligenceIndexIsEstimated, intelligenceIndexOutputTokensPerTask, intelligenceIndexTimePerTask, itBenchSre, lcr, mlcrOverall, mmmuPro, name, omniscience, omniscienceBreakdown, price1mBlended7To2To1, price1mInputTokens, price1mOutputTokens, releaseDate, scicode, tau2, tauBanking, terminalBench21, terminalBench40, terminalbenchHard, timeToFirstAnswerToken]
- `claude-haiku-5-5-xhigh`：page-038 [apexAgents, automationBenchPartialScore, briefcaseBreakdown, contextWindowTokens, critpt, deprecated, effort_label, gdpvalNormalized, gpqa, hle, ifbench, intelligenceIndex, intelligenceIndexCost, intelligenceIndexIsEstimated, intelligenceIndexOutputTokensPerTask, intelligenceIndexTimePerTask, itBenchSre, lcr, mlcrOverall, mmmuPro, name, omniscience, omniscienceBreakdown, price1mBlended7To2To1, price1mInputTokens, price1mOutputTokens, releaseDate, scicode, tau2, tauBanking, terminalBench21, terminalBench40, terminalbenchHard, timeToFirstAnswerToken]
- `claude-haiku-5-5-high`：page-038 [apexAgents, automationBenchPartialScore, briefcaseBreakdown, contextWindowTokens, critpt, deprecated, effort_label, gdpvalNormalized, gpqa, hle, ifbench, intelligenceIndex, intelligenceIndexCost, intelligenceIndexIsEstimated, intelligenceIndexOutputTokensPerTask, intelligenceIndexTimePerTask, itBenchSre, lcr, mlcrOverall, mmmuPro, name, omniscience, omniscienceBreakdown, price1mBlended7To2To1, price1mInputTokens, price1mOutputTokens, releaseDate, scicode, tau2, tauBanking, terminalBench21, terminalBench40, terminalbenchHard, timeToFirstAnswerToken]
- `claude-haiku-5-5-medium`：page-038 [apexAgents, automationBenchPartialScore, briefcaseBreakdown, contextWindowTokens, critpt, deprecated, effort_label, gdpvalNormalized, gpqa, hle, ifbench, intelligenceIndex, intelligenceIndexCost, intelligenceIndexIsEstimated, intelligenceIndexOutputTokensPerTask, intelligenceIndexTimePerTask, itBenchSre, lcr, mlcrOverall, mmmuPro, name, omniscience, omniscienceBreakdown, price1mBlended7To2To1, price1mInputTokens, price1mOutputTokens, releaseDate, scicode, tau2, tauBanking, terminalBench21, terminalBench40, terminalbenchHard, timeToFirstAnswerToken]
- `claude-haiku-5-5-low`：page-038 [apexAgents, automationBenchPartialScore, briefcaseBreakdown, contextWindowTokens, critpt, deprecated, effort_label, gdpvalNormalized, gpqa, hle, ifbench, intelligenceIndex, intelligenceIndexCost, intelligenceIndexIsEstimated, intelligenceIndexOutputTokensPerTask, intelligenceIndexTimePerTask, itBenchSre, lcr, mlcrOverall, mmmuPro, name, omniscience, omniscienceBreakdown, price1mBlended7To2To1, price1mInputTokens, price1mOutputTokens, releaseDate, scicode, tau2, tauBanking, terminalBench21, terminalBench40, terminalbenchHard, timeToFirstAnswerToken]

## 4. 新舊政策逐列差異

新增／恢復 6 列；原有分數變動 6 列；撤回 0 列。GPT-5.4 mini 三個操作者停用列維持未知，未因 AA 有分數而重新啟用。

| 原有列 | 舊原始／整數 | 新原始／整數 |
|---|---|---|
| claude/claude-4-5-haiku | 15.41 / 15 | 16.88 / 17 |
| claude/claude-sonnet-5-5 | 55.98 / 56 | 56.00 / 56 |
| claude/claude-sonnet-5-5-high | 46.74 / 47 | 46.75 / 47 |
| claude/claude-sonnet-5-5-medium | 40.74 / 41 | 40.84 / 41 |
| claude/claude-sonnet-5-5-xhigh | 51.85 / 52 | 51.90 / 52 |
| codex/gpt-oss-20b | 8.97 / 9 | 9.95 / 10 |

新增／恢復：

- `claude/claude-haiku-5-5`：43.40 → 43。
- `claude/claude-haiku-5-5-high`：37.82 → 38。
- `claude/claude-haiku-5-5-low`：29.45 → 29。
- `claude/claude-haiku-5-5-medium`：34.46 → 34。
- `claude/claude-haiku-5-5-xhigh`：41.25 → 41。
- `claude/claude-sonnet-5-5-low`：35.87 → 36。

## 5. 車道計算與變更理由

路由正本為根目錄 `routing.yaml`；任務書提到的 `config/routing.yaml` 不存在。

- `hardest-coding`：Haiku xhigh 在 Opus 5.5 low 後、Sonnet 5.5 medium 前，承接 41 分區段。
- `bulk-mechanical`：低分區段新增 Haiku xhigh → high → medium → low，首選維持 GPT-6.1 Sol medium。
- `triage`：GPT-6 Luna high 的整體指數執行成本比 Haiku medium 低，首選不變；
  Claude 新增 Haiku medium → low，置於 Sonnet 5 low 前。此車道原本以成本、分數與
  跨廠商可用性人工判讀，沒有 `VALUE_LANES` 數值帶寬，本輪沒有另造排名公式。
  Haiku medium 比舊 Claude 分類候選分數更高、成本更低、時間更短，low 提供更便宜的低權限階梯。
  high／xhigh 增加的成本不適合大量分類用途。
- Haiku 4.5 被新 Haiku low 在分數、成本、時間上全面超越，不再是價值優先候選；
  仍保留分類尾端，遵循既有『新模型尚未證實前保留舊列』及低權限後援規則。
  本輪未以 AA 分數冒稱主機可用性，也未切斷舊主機最後的 Claude 候選。
- `taste-final`：Sonnet 5.5 xhigh 符合相對 Opus 5.5 xhigh 的 30／100 Elo 近似帶寬且更便宜，
  改為首選，Opus 留在後面。Haiku xhigh／high 加入低分區段、放在 Flash 前。
- `fast-agentic` 不加入 Haiku：只有 low 符合首字 10 秒限制，但自動化成績低於現有候選，
  差距超出價值規則允許的兩倍品質帶寬；其餘強度均超時。控制者下限不是目標模型的最低分數，未用它排除 low。
- `ui-draft`／`long-context` 缺 MMMU-Pro／mlcrOverall，沒有資料支持加入。
  `hard-judgment` 的價值計算不選 Haiku；`consult` 保留高能力直接諮詢；
  `live-search` 需要原生搜尋脈絡；`coding-overflow` 保留現有非 Codex 額度後援。
- max 不進自動候選鏈，仍可明示選擇。其他車道不變；舊列保留作未證實新模型的主機後援。
- 計算器的 ceiling 差異表示靜態候選鏈無法完全表達非單調的最便宜解。例如 bulk 的
  41 分上限偏好 Haiku high，靜態鏈仍先給能力較高的 xhigh。本輪不擴張為改寫派工演算法。

### 5.1 價值計算原始輸出

數字直接由 `value` 產生，沒有手抄。


**hardest-coding** — Terminal-Bench 4.0, band 0.02; SciCode within 0.05; near-ties only

- codex gpt-6-astra xhigh (score 52)
- codex gpt-6.1-sol xhigh (score 51)
- codex gpt-6.1-sol high (score 50)
- codex gpt-6.1-sol medium (score 48)
- claude claude-sonnet-5-5 high (score 47)
- codex gpt-6-astra low (score 46)
- claude claude-opus-5 medium (score 45)
- codex gpt-6-sol xhigh (score 44)
- claude claude-opus-5-5 low (score 42)
- claude claude-haiku-5-5 xhigh (score 41)
- claude claude-opus-5 low (score 39)

**bulk-mechanical** — Terminal-Bench 4.0, band 0.06; minutes / task within 2.0; wide band

- codex gpt-6.1-sol medium (score 48)
- codex gpt-6.1-sol high (score 50)
- claude claude-sonnet-5-5 high (score 47)
- codex gpt-6.1-sol low (score 42)
- claude claude-haiku-5-5 xhigh (score 41)
- claude claude-haiku-5-5 high (score 38)
- claude claude-haiku-5-5 medium (score 34)
- claude claude-haiku-5-5 low (score 29)
- ceiling 51: the chain gives codex/gpt-6-1-sol-medium, the rule prefers codex/gpt-6-1-sol-high
- ceiling 45: the chain gives codex/gpt-6-1-sol-low, the rule prefers claude/claude-haiku-5-5-xhigh
- ceiling 41: the chain gives claude/claude-haiku-5-5-xhigh, the rule prefers claude/claude-haiku-5-5-high
- ceiling 40: the chain gives claude/claude-haiku-5-5-high, the rule prefers claude/claude-haiku-5-5-medium
- ceiling 39: the chain gives claude/claude-haiku-5-5-high, the rule prefers claude/claude-haiku-5-5-medium
- ceiling 38: the chain gives claude/claude-haiku-5-5-high, the rule prefers claude/claude-haiku-5-5-low

**hard-judgment** — HLE, band 0.02; Briefcase analytical Elo within 150; near-ties only

- claude claude-opus-5-5 xhigh (score 56)
- claude claude-fable-5-1 xhigh (score 53)
- claude claude-opus-5-5 medium (score 51)
- claude claude-fable-5-1 medium (score 49)
- claude claude-opus-5 high (score 48)
- claude claude-opus-5 medium (score 45)
- claude claude-opus-5-5 low (score 42)
- gemini gemini-3.8-flash high (score 41)
- codex gpt-5.6-sol medium (score 39)

**taste-final** — Briefcase overall Elo, band 30; Briefcase presentation Elo within 100; near-ties only

- claude claude-sonnet-5-5 xhigh (score 52)
- claude claude-sonnet-5-5 high (score 47)
- grok grok-4.7 high (score 46)
- grok grok-4.6 high (score 44)
- claude claude-haiku-5-5 xhigh (score 41)
- claude claude-haiku-5-5 high (score 38)

**ui-draft** — MMMU-Pro, band 0.01; Terminal-Bench 4.0 within 0.05; wide band

- claude claude-opus-5-5 high (score 54)
- codex gpt-6.1-sol high (score 50)
- codex gpt-6.1-sol medium (score 48)
- claude claude-opus-5-5 low (score 42)
- gemini gemini-3.8-flash high (score 41)
- gemini gemini-3.8-flash medium (score 40)
- ceiling 55: the chain gives claude/claude-opus-5-5-high, the rule prefers codex/gpt-6-1-sol-high
- ceiling 54: the chain gives claude/claude-opus-5-5-high, the rule prefers codex/gpt-6-1-sol-high
- ceiling 50: the chain gives codex/gpt-6-1-sol-high, the rule prefers codex/gpt-6-1-sol-medium

**fast-agentic** — AutomationBench, band 0.03; minutes / task within 1.0; wide band

- codex gpt-6.1-sol medium (score 48)
- codex gpt-6-sol low (score 34)
- codex gpt-6.1-sol low (score 42)
- ceiling 45: the chain gives codex/gpt-6-sol-low, the rule prefers codex/gpt-6-1-sol-low
- ceiling 44: the chain gives codex/gpt-6-sol-low, the rule prefers codex/gpt-6-1-sol-low
- ceiling 43: the chain gives codex/gpt-6-sol-low, the rule prefers codex/gpt-6-1-sol-low
- ceiling 42: the chain gives codex/gpt-6-sol-low, the rule prefers codex/gpt-6-1-sol-low

**long-context** — mlcrOverall, band 0.02; near-ties only

- claude claude-opus-5 high (score 48)
- claude claude-opus-5 medium (score 45)
- claude claude-opus-5 low (score 39)
- codex gpt-5.6-terra xhigh (score 38)
- codex gpt-5.6-luna xhigh (score 35)
- codex gpt-5.6-luna high (score 32)
- codex gpt-5.6-terra medium (score 30)


### 5.2 各車道實際數值（由 lanes 產生）


**hardest-coding**

| # | candidate | score | Terminal-Bench 4.0 | SciCode | hallucination rate | index run cost ($) |
|---|---|---|---|---|---|---|
| 1 | codex gpt-6-astra xhigh | 52 | 0.596 | 0.557 | not published | 3803 |
| 2 | codex gpt-6.1-sol xhigh | 51 | 0.540 | 0.557 | not published | 662 |
| 3 | claude claude-opus-5-5 medium | 51 | 0.525 | 0.593 | not published | 1627 |
| 4 | codex gpt-6.1-sol high | 50 | 0.515 | 0.558 | not published | 521 |
| 5 | codex gpt-6-astra medium | 50 | 0.495 | 0.542 | not published | 2434 |
| 6 | codex gpt-6.1-sol medium | 48 | 0.480 | 0.532 | not published | 361 |
| 7 | claude claude-fable-5-1 medium | 49 | 0.449 | 0.564 | not published | 3983 |
| 8 | claude claude-opus-5 high | 48 | 0.460 | 0.554 | not published | 4332 |
| 9 | claude claude-sonnet-5-5 high | 47 | 0.439 | 0.537 | not published | 1027 |
| 10 | codex gpt-6-astra low | 46 | 0.419 | 0.541 | not published | 1537 |
| 11 | claude claude-opus-5 medium | 45 | 0.343 | 0.515 | not published | 2732 |
| 12 | claude claude-opus-5-5 low | 42 | 0.313 | 0.586 | not published | 860 |
| 13 | claude claude-haiku-5-5 xhigh | 41 | 0.293 | 0.517 | not published | 158 |
| 14 | claude claude-sonnet-5-5 medium | 41 | 0.298 | 0.529 | not published | 622 |
| 15 | claude claude-opus-5 low | 39 | 0.263 | 0.492 | not published | 1561 |
| 16 | grok grok-4.7 high | 46 | 0.247 | 0.578 | not published | 3881 |
| 17 | grok grok-4.6 high | 44 | 0.212 | 0.565 | not published | 1963 |
| 18 | gemini gemini-3.8-flash-high | 41 | 0.197 | 0.566 | not published | 1623 |

**bulk-mechanical**

| # | candidate | score | Terminal-Bench 4.0 | minutes / task | index run cost ($) |
|---|---|---|---|---|---|
| 1 | codex gpt-6.1-sol medium | 48 | 0.480 | 2.6 | 361 |
| 2 | claude claude-opus-5-5 medium | 51 | 0.525 | 3.4 | 1627 |
| 3 | codex gpt-6-astra medium | 50 | 0.495 | 3.5 | 2434 |
| 4 | codex gpt-6.1-sol high | 50 | 0.515 | 4.3 | 521 |
| 5 | claude claude-sonnet-5-5 high | 47 | 0.439 | 3.8 | 1027 |
| 6 | codex gpt-6.1-sol low | 42 | 0.308 | 1.3 | 250 |
| 7 | codex gpt-6-astra low | 46 | 0.419 | 1.6 | 1537 |
| 8 | codex gpt-6-sol high | 42 | 0.263 | 2.0 | 605 |
| 9 | codex gpt-5.6-sol high | 42 | 0.207 | 3.0 | 1487 |
| 10 | claude claude-opus-5-5 low | 42 | 0.313 | 1.4 | 860 |
| 11 | claude claude-haiku-5-5 xhigh | 41 | 0.293 | 4.9 | 158 |
| 12 | claude claude-sonnet-5-5 medium | 41 | 0.298 | 2.1 | 622 |
| 13 | claude claude-haiku-5-5 high | 38 | 0.217 | 3.5 | 94 |
| 14 | claude claude-haiku-5-5 medium | 34 | 0.152 | 2.3 | 56 |
| 15 | claude claude-haiku-5-5 low | 29 | 0.126 | 1.0 | 34 |
| 16 | codex gpt-6-sol medium | 40 | 0.187 | not published | 416 |
| 17 | claude claude-opus-5 low | 39 | 0.263 | 2.7 | 1561 |
| 18 | codex gpt-6-sol low | 34 | 0.091 | 0.6 | 269 |
| 19 | gemini gemini-3.8-flash-high | 41 | 0.197 | 8.9 | 1623 |

**triage**

| # | candidate | score | index | index run cost ($) | minutes / task |
|---|---|---|---|---|---|
| 1 | codex gpt-6-luna high | 33 | 32.9 | 48 | 2.9 |
| 2 | codex gpt-5.6-luna high | 32 | 32.1 | 108 | 2.0 |
| 3 | gemini gemini-3.8-flash-low | 33 | 33.5 | not published | not published |
| 4 | claude claude-haiku-5-5 medium | 34 | 34.5 | 56 | 2.3 |
| 5 | claude claude-haiku-5-5 low | 29 | 29.4 | 34 | 1.0 |
| 6 | claude claude-sonnet-5 low | 24 | 24.3 | 653 | 2.4 |
| 7 | claude claude-haiku-4-5 | 17 | 16.9 | 594 | 2.6 |

**hard-judgment**

| # | candidate | score | HLE | Briefcase analytical Elo | CritPt | hallucination rate | index run cost ($) |
|---|---|---|---|---|---|---|---|
| 1 | claude claude-opus-5-5 xhigh | 56 | 0.575 | 2107 | 0.317 | not published | 4057 |
| 2 | claude claude-fable-5-1 xhigh | 53 | 0.587 | 1935 | 0.311 | not published | 9063 |
| 3 | claude claude-opus-5-5 medium | 51 | 0.547 | 1884 | 0.277 | not published | 1627 |
| 4 | claude claude-fable-5-1 medium | 49 | 0.538 | 1776 | 0.291 | not published | 3983 |
| 5 | claude claude-opus-5 high | 48 | 0.528 | 1818 | 0.283 | not published | 4332 |
| 6 | claude claude-opus-5 medium | 45 | 0.513 | 1640 | 0.269 | not published | 2732 |
| 7 | claude claude-opus-5-5 low | 42 | 0.483 | 1442 | 0.177 | not published | 860 |
| 8 | codex gpt-6.1-sol xhigh | 51 | 0.526 | 1720 | 0.317 | not published | 662 |
| 9 | codex gpt-6-astra high | 51 | 0.531 | 1703 | 0.289 | not published | 2925 |
| 10 | grok grok-4.7 high | 46 | 0.423 | 1933 | 0.180 | not published | 3881 |
| 11 | gemini gemini-3.8-flash-high | 41 | 0.478 | 1142 | 0.183 | not published | 1623 |

**taste-final**

| # | candidate | score | Briefcase overall Elo | Briefcase presentation Elo | GDPval | index run cost ($) |
|---|---|---|---|---|---|---|
| 1 | claude claude-sonnet-5-5 xhigh | 52 | 1751 | 1643 | 0.615 | 2177 |
| 2 | claude claude-opus-5-5 xhigh | 56 | 1768 | 1640 | 0.669 | 4057 |
| 3 | claude claude-opus-5-5 high | 54 | 1689 | 1549 | 0.603 | 2172 |
| 4 | claude claude-sonnet-5-5 high | 47 | 1639 | 1490 | 0.525 | 1027 |
| 5 | claude claude-opus-5-5 medium | 51 | 1628 | 1499 | 0.543 | 1627 |
| 6 | grok grok-4.7 high | 46 | 1633 | 1503 | 0.605 | 3881 |
| 7 | grok grok-4.6 high | 44 | 1536 | 1520 | 0.561 | 1963 |
| 8 | codex gpt-6-astra xhigh | 52 | 1544 | 1503 | 0.508 | 3803 |
| 9 | claude claude-haiku-5-5 xhigh | 41 | 1533 | 1393 | 0.505 | 158 |
| 10 | claude claude-haiku-5-5 high | 38 | 1443 | 1303 | 0.460 | 94 |
| 11 | gemini gemini-3.8-flash-high | 41 | 1203 | 1219 | 0.468 | 1623 |

**consult**

| # | candidate | score | index | index run cost ($) |
|---|---|---|---|---|
| 1 | codex gpt-6-astra xhigh | 52 | 52.4 | 3803 |
| 2 | claude claude-opus-5-5 xhigh | 56 | 56.0 | 4057 |
| 3 | grok grok-4.7 high | 46 | 46.3 | 3881 |
| 4 | grok grok-4.6 high | 44 | 44.3 | 1963 |
| 5 | gemini gemini-3.8-flash-high | 41 | 40.9 | 1623 |

**ui-draft**

| # | candidate | score | MMMU-Pro | Terminal-Bench 4.0 | index run cost ($) |
|---|---|---|---|---|---|
| 1 | claude claude-opus-5-5 high | 54 | 0.858 | 0.566 | 2172 |
| 2 | codex gpt-6.1-sol high | 50 | 0.849 | 0.515 | 521 |
| 3 | claude claude-opus-5-5 medium | 51 | 0.857 | 0.525 | 1627 |
| 4 | codex gpt-6-astra medium | 50 | 0.851 | 0.495 | 2434 |
| 5 | codex gpt-6.1-sol medium | 48 | 0.839 | 0.480 | 361 |
| 6 | codex gpt-6.1-sol low | 42 | 0.831 | 0.308 | 250 |
| 7 | claude claude-opus-5-5 low | 42 | 0.847 | 0.313 | 860 |
| 8 | codex gpt-6-astra low | 46 | 0.846 | 0.419 | 1537 |
| 9 | codex gpt-6-sol medium | 40 | 0.804 | 0.187 | 416 |
| 10 | gemini gemini-3.8-flash-high | 41 | 0.856 | 0.197 | 1623 |

**long-context**

| # | candidate | score | mlcrOverall | AA-LCR | index run cost ($) |
|---|---|---|---|---|---|
| 1 | claude claude-opus-5 high | 48 | 0.594 | 0.790 | 4332 |
| 2 | claude claude-opus-5 medium | 45 | 0.561 | 0.820 | 2732 |
| 3 | claude claude-opus-5 low | 39 | 0.539 | 0.813 | 1561 |
| 4 | codex gpt-5.6-terra xhigh | 38 | 0.217 | 0.790 | 1187 |
| 5 | gemini gemini-3.8-flash-high | 41 | 0.217 | 0.813 | 1623 |

**fast-agentic**

| # | candidate | score | AutomationBench | minutes / task | first answer token (s) | index run cost ($) |
|---|---|---|---|---|---|---|
| 1 | codex gpt-6.1-sol medium | 48 | 0.626 | 2.6 | 9 | 361 |
| 2 | codex gpt-6-astra medium | 50 | 0.646 | 3.5 | 6 | 2434 |
| 3 | codex gpt-6.1-sol low | 42 | 0.526 | 1.3 | 3 | 250 |
| 4 | codex gpt-6-sol low | 34 | 0.539 | 0.6 | 2 | 269 |
| 5 | codex gpt-6-astra low | 46 | 0.591 | 1.6 | 3 | 1537 |
| 6 | gemini gemini-3.8-flash-medium | 40 | 0.609 | not published | not published | 1100 |
| 7 | claude claude-sonnet-5-5 medium | 41 | 0.549 | 2.1 | 2 | 622 |
| 8 | claude claude-opus-5-5 low | 42 | 0.529 | 1.4 | 9 | 860 |

**live-search**

| # | candidate | score | knowledge (omniscience) | hallucination rate | index run cost ($) |
|---|---|---|---|---|---|
| 1 | grok grok-4.7 high | 46 | 30.9 | not published | 3881 |
| 2 | grok grok-4.6 high | 44 | 30.5 | not published | 1963 |
| 3 | gemini gemini-3.8-flash-high | 41 | 29.6 | not published | 1623 |
| 4 | claude claude-opus-5-5 low | 42 | 38.9 | not published | 860 |

**coding-overflow**

| # | candidate | score | Terminal-Bench 4.0 | SciCode | index run cost ($) |
|---|---|---|---|---|---|
| 1 | grok grok-4.7 high | 46 | 0.247 | 0.578 | 3881 |
| 2 | grok grok-4.6 high | 44 | 0.212 | 0.565 | 1963 |
| 3 | gemini gemini-3.8-flash-high | 41 | 0.197 | 0.566 | 1623 |
| 4 | kimi kimi-k3 | not scored | — | — | — |
| 5 | qwen qwen3-coder-plus | not scored | — | — | — |
| 6 | opencode - | not scored | — | — | — |


## 6. 每個欄位衝突與裁決

完整列出不同值與觀察頁面；missing 是沒有欄位，null 是欄位有空值。
`page-001..004` 代表 001 到 004；所選值可依「採用頁」在觀察欄逐筆核對。
完整機器可讀資料位於合併抽取檔 `conflicts`，每列均記錄相同裁決規則。

| # | slug | 欄位 | 不同觀察值與來源 | 採用頁 |
|---|---|---|---|---|
| 1 | claude-4-5-haiku | contextWindowTokens | `200000` @ page-001..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-001 |
| 2 | claude-4-5-haiku | critpt | `0` @ page-001..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-001 |
| 3 | claude-4-5-haiku | deprecated | `false` @ page-001..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-001 |
| 4 | claude-4-5-haiku | gpqa | `0.646464646464646` @ page-001..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-001 |
| 5 | claude-4-5-haiku | hle | `0.0421686746987952` @ page-001..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-001 |
| 6 | claude-4-5-haiku | ifbench | `0.420408163265306` @ page-001..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-001 |
| 7 | claude-4-5-haiku | intelligenceIndex | `15.410949885381` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `16.8822352291856` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-001 |
| 8 | claude-4-5-haiku | intelligenceIndexIsEstimated | `null` @ page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039; `true` @ page-001..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038 | page-001 |
| 9 | claude-4-5-haiku | lcr | `0.496666666666667` @ page-001..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-001 |
| 10 | claude-4-5-haiku | mlcrOverall | `0.0444444444444444` @ page-001..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-001 |
| 11 | claude-4-5-haiku | mmmuPro | `0.551445086705202` @ page-001..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-001 |
| 12 | claude-4-5-haiku | name | `"Claude 4.5 Haiku (Non-reasoning)"` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `"Claude 4.5 Haiku"` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-001 |
| 13 | claude-4-5-haiku | omniscience | `-7.566666666666666` @ page-001..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-001 |
| 14 | claude-4-5-haiku | price1mBlended7To2To1 | `0.77` @ page-001..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-001 |
| 15 | claude-4-5-haiku | price1mInputTokens | `1` @ page-001..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-001 |
| 16 | claude-4-5-haiku | price1mOutputTokens | `5` @ page-001..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-001 |
| 17 | claude-4-5-haiku | tau2 | `0.324561403508772` @ page-001..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-001 |
| 18 | claude-4-5-haiku | terminalbenchHard | `0.272727272727273` @ page-001..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-001 |
| 19 | claude-4-5-haiku | timeToFirstAnswerToken | `null` @ page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039; `{"input":0.547027927000045,"reasoning":0,"total":0.547027927000045}` @ page-001..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038 | page-001 |
| 20 | claude-fable-5-1 | name | `"Claude Fable 5.1 (Max, Default Fallback)"` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `"Claude Fable 5.1"` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-005 |
| 21 | claude-haiku-5-5 | automationBenchPartialScore | `0.35411015132783624` @ page-002..004,page-006..012,page-014..016,page-018..021,page-023..028,page-032..033,page-036..038; `null` @ page-001,page-005,page-013,page-017,page-022,page-029..031,page-034..035,page-039 | page-006 |
| 22 | claude-haiku-5-5 | briefcaseBreakdown | `null` @ page-001,page-005,page-013,page-017,page-022,page-029..031,page-034..035,page-039; `{"analyticalQuality":{"elo":1907.63,"lower95ci":1887.43,"upper95ci":1927.82},"overall":{"elo":1577.72,"lower95ci":1567.67,"upper95ci":1587.77},"presentation":{"elo":1429.1,"lower95ci":1412.13,"upper95ci":1446.07},"rubricPassRate":0.5232323232323233}` @ page-002..004,page-006..012,page-014..016,page-018..021,page-023..028,page-032..033,page-036..038 | page-006 |
| 23 | claude-haiku-5-5 | contextWindowTokens | `1000000` @ page-002..004,page-006..012,page-014..016,page-018..021,page-023..028,page-032..033,page-036..038; `null` @ page-001,page-005,page-013,page-017,page-022,page-029..031,page-034..035,page-039 | page-006 |
| 24 | claude-haiku-5-5 | critpt | `0.188571428571429` @ page-002..004,page-006..012,page-014..016,page-018..021,page-023..028,page-032..033,page-036..038; `null` @ page-001,page-005,page-013,page-017,page-022,page-029..031,page-034..035,page-039 | page-006 |
| 25 | claude-haiku-5-5 | deprecated | `false` @ page-002..004,page-006..012,page-014..016,page-018..021,page-023..028,page-032..033,page-036..038; `null` @ page-001,page-005,page-013,page-017,page-022,page-029..031,page-034..035,page-039 | page-006 |
| 26 | claude-haiku-5-5 | effort_label | `"max"` @ page-002..004,page-006..012,page-014..016,page-018..021,page-023..028,page-032..033,page-036..038; `null` @ page-001,page-005,page-013,page-017,page-022,page-029..031,page-034..035,page-039 | page-006 |
| 27 | claude-haiku-5-5 | gdpvalNormalized | `0.560025` @ page-002..004,page-006..012,page-014..016,page-018..021,page-023..028,page-032..033,page-036..038; `null` @ page-001,page-005,page-013,page-017,page-022,page-029..031,page-034..035,page-039 | page-006 |
| 28 | claude-haiku-5-5 | hle | `0.443929564411492` @ page-002..004,page-006..012,page-014..016,page-018..021,page-023..028,page-032..033,page-036..038; `null` @ page-001,page-005,page-013,page-017,page-022,page-029..031,page-034..035,page-039 | page-006 |
| 29 | claude-haiku-5-5 | intelligenceIndexCost | `null` @ page-001,page-005,page-013,page-017,page-022,page-029..031,page-034..035,page-039; `{"answer":18.7988375,"cacheRead":74.19116663714301,"cacheWrite":34.9382039107124,"input":112.66229994785542,"nonCacheInput":3.5329294,"output":217.59164299999998,"reasoning":198.7928055,"total":330.2539429478554}` @ page-002..004,page-006..012,page-014..016,page-018..021,page-023..028,page-032..033,page-036..038 | page-006 |
| 30 | claude-haiku-5-5 | intelligenceIndexIsEstimated | `false` @ page-002..004,page-006..012,page-014..016,page-018..021,page-023..028,page-032..033,page-036..038; `null` @ page-001,page-005,page-013,page-017,page-022,page-029..031,page-034..035,page-039 | page-006 |
| 31 | claude-haiku-5-5 | intelligenceIndexOutputTokensPerTask | `null` @ page-001,page-005,page-013,page-017,page-022,page-029..031,page-034..035,page-039; `{"answer":33117.63980014857,"output":162164.1932085826,"reasoning":129046.55340843403}` @ page-002..004,page-006..012,page-014..016,page-018..021,page-023..028,page-032..033,page-036..038 | page-006 |
| 32 | claude-haiku-5-5 | intelligenceIndexTimePerTask | `425.8882589805649` @ page-002..004,page-006..012,page-014..016,page-018..021,page-023..028,page-032..033,page-036..038; `null` @ page-001,page-005,page-013,page-017,page-022,page-029..031,page-034..035,page-039 | page-006 |
| 33 | claude-haiku-5-5 | lcr | `0.826666666666667` @ page-002..004,page-006..012,page-014..016,page-018..021,page-023..028,page-032..033,page-036..038; `null` @ page-001,page-005,page-013,page-017,page-022,page-029..031,page-034..035,page-039 | page-006 |
| 34 | claude-haiku-5-5 | name | `"Claude Haiku 5.5 (Max)"` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `"Claude Haiku 5.5"` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-006 |
| 35 | claude-haiku-5-5 | omniscience | `10.666666666666666` @ page-002..004,page-006..012,page-014..016,page-018..021,page-023..028,page-032..033,page-036..038; `null` @ page-001,page-005,page-013,page-017,page-022,page-029..031,page-034..035,page-039 | page-006 |
| 36 | claude-haiku-5-5 | price1mBlended7To2To1 | `0.077` @ page-002..004,page-006..012,page-014..016,page-018..021,page-023..028,page-032..033,page-036..038; `null` @ page-001,page-005,page-013,page-017,page-022,page-029..031,page-034..035,page-039 | page-006 |
| 37 | claude-haiku-5-5 | price1mInputTokens | `0.1` @ page-002..004,page-006..012,page-014..016,page-018..021,page-023..028,page-032..033,page-036..038; `null` @ page-001,page-005,page-013,page-017,page-022,page-029..031,page-034..035,page-039 | page-006 |
| 38 | claude-haiku-5-5 | price1mOutputTokens | `0.5` @ page-002..004,page-006..012,page-014..016,page-018..021,page-023..028,page-032..033,page-036..038; `null` @ page-001,page-005,page-013,page-017,page-022,page-029..031,page-034..035,page-039 | page-006 |
| 39 | claude-haiku-5-5 | scicode | `0.549768518518518` @ page-002..004,page-006..012,page-014..016,page-018..021,page-023..028,page-032..033,page-036..038; `null` @ page-001,page-005,page-013,page-017,page-022,page-029..031,page-034..035,page-039 | page-006 |
| 40 | claude-haiku-5-5 | terminalBench40 | `0.328282828282828` @ page-002..004,page-006..012,page-014..016,page-018..021,page-023..028,page-032..033,page-036..038; `null` @ page-001,page-005,page-013,page-017,page-022,page-029..031,page-034..035,page-039 | page-006 |
| 41 | claude-haiku-5-5 | timeToFirstAnswerToken | `null` @ page-001,page-005,page-013,page-017,page-022,page-029..031,page-034..035,page-039; `{"input":432.2091159655,"reasoning":0,"total":432.2091159655}` @ page-002..004,page-006..012,page-014..016,page-018..021,page-023..028,page-032..033,page-036..038 | page-006 |
| 42 | claude-opus-5-5 | name | `"Claude Opus 5.5 (Max, Default Fallback)"` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `"Claude Opus 5.5"` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-013 |
| 43 | claude-sonnet-5-5 | automationBenchPartialScore | `0.7176882128193995` @ page-002..005,page-007..031,page-033,page-036..039; `null` @ page-001,page-006,page-032,page-034..035 | page-017 |
| 44 | claude-sonnet-5-5 | briefcaseBreakdown | `null` @ page-001,page-006,page-032,page-034..035; `{"analyticalQuality":{"elo":2132.02,"lower95ci":2109.6,"upper95ci":2156.39},"overall":{"elo":1823.49,"lower95ci":1811.83,"upper95ci":1836.04},"presentation":{"elo":1715.99,"lower95ci":1694.99,"upper95ci":1738.99},"rubricPassRate":0.6515151515151515}` @ page-002..005,page-007..031,page-033,page-036..039 | page-017 |
| 45 | claude-sonnet-5-5 | contextWindowTokens | `1000000` @ page-002..005,page-007..031,page-033,page-036..039; `null` @ page-001,page-006,page-032,page-034..035 | page-017 |
| 46 | claude-sonnet-5-5 | critpt | `0.314285714285714` @ page-002..005,page-007..031,page-033,page-036..039; `null` @ page-001,page-006,page-032,page-034..035 | page-017 |
| 47 | claude-sonnet-5-5 | deprecated | `false` @ page-002..005,page-007..031,page-033,page-036..039; `null` @ page-001,page-006,page-032,page-034..035 | page-017 |
| 48 | claude-sonnet-5-5 | effort_label | `"max"` @ page-002..005,page-007..031,page-033,page-036..039; `null` @ page-001,page-006,page-032,page-034..035 | page-017 |
| 49 | claude-sonnet-5-5 | gdpvalNormalized | `0.66955` @ page-002..005,page-007..031,page-033,page-036..039; `null` @ page-001,page-006,page-032,page-034..035 | page-017 |
| 50 | claude-sonnet-5-5 | hle | `0.549582947173309` @ page-002..005,page-007..031,page-033,page-036..039; `null` @ page-001,page-006,page-032,page-034..035 | page-017 |
| 51 | claude-sonnet-5-5 | intelligenceIndexCost | `null` @ page-001,page-006,page-032,page-034..035; `{"answer":578.24089,"cacheRead":1814.6288379882358,"cacheWrite":1216.2412277940998,"input":3101.8027057823356,"nonCacheInput":70.93264,"output":4157.69956,"reasoning":3579.45867,"total":7259.502265782336}` @ page-002..005,page-007..031,page-033,page-036..039 | page-017 |
| 52 | claude-sonnet-5-5 | intelligenceIndexIsEstimated | `false` @ page-002..005,page-007..031,page-033,page-036..039; `null` @ page-001,page-006,page-032,page-034..035 | page-017 |
| 53 | claude-sonnet-5-5 | intelligenceIndexOutputTokensPerTask | `null` @ page-001,page-006,page-032,page-034..035; `{"answer":50796.88280987353,"output":197430.37161011933,"reasoning":146633.4888002458}` @ page-002..005,page-007..031,page-033,page-036..039 | page-017 |
| 54 | claude-sonnet-5-5 | intelligenceIndexTimePerTask | `968.3342270422802` @ page-002..005,page-007..031,page-033,page-036..039; `null` @ page-001,page-006,page-032,page-034..035 | page-017 |
| 55 | claude-sonnet-5-5 | lcr | `0.826666666666667` @ page-002..005,page-007..031,page-033,page-036..039; `null` @ page-001,page-006,page-032,page-034..035 | page-017 |
| 56 | claude-sonnet-5-5 | mlcrOverall | `0.75` @ page-002..005,page-007..031,page-033,page-036..039; `null` @ page-001,page-006,page-032,page-034..035 | page-017 |
| 57 | claude-sonnet-5-5 | name | `"Claude Sonnet 5.5 (Max, Default Fallback)"` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `"Claude Sonnet 5.5"` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-017 |
| 58 | claude-sonnet-5-5 | omniscience | `32.3` @ page-002..005,page-007..031,page-033,page-036..039; `null` @ page-001,page-006,page-032,page-034..035 | page-017 |
| 59 | claude-sonnet-5-5 | price1mBlended7To2To1 | `1.47` @ page-002..005,page-007..031,page-033,page-036..039; `null` @ page-001,page-006,page-032,page-034..035 | page-017 |
| 60 | claude-sonnet-5-5 | price1mInputTokens | `2` @ page-002..005,page-007..031,page-033,page-036..039; `null` @ page-001,page-006,page-032,page-034..035 | page-017 |
| 61 | claude-sonnet-5-5 | price1mOutputTokens | `10` @ page-002..005,page-007..031,page-033,page-036..039; `null` @ page-001,page-006,page-032,page-034..035 | page-017 |
| 62 | claude-sonnet-5-5 | scicode | `0.609953703703704` @ page-002..005,page-007..031,page-033,page-036..039; `null` @ page-001,page-006,page-032,page-034..035 | page-017 |
| 63 | claude-sonnet-5-5 | terminalBench40 | `0.636363636363636` @ page-002..005,page-007..031,page-033,page-036..039; `null` @ page-001,page-006,page-032,page-034..035 | page-017 |
| 64 | claude-sonnet-5-5 | timeToFirstAnswerToken | `null` @ page-001,page-006,page-032,page-034..035; `{"input":460.744885192,"reasoning":0,"total":460.744885192}` @ page-002..005,page-007..031,page-033,page-036..039 | page-017 |
| 65 | gemini-3-1-pro-preview | apexAgents | `0.320058997050148` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-018 |
| 66 | gemini-3-1-pro-preview | automationBenchPartialScore | `0.3542771839809836` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-018 |
| 67 | gemini-3-1-pro-preview | briefcaseBreakdown | `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039; `{"analyticalQuality":{"elo":289.71,"lower95ci":274.7,"upper95ci":305.29},"overall":{"elo":456.12,"lower95ci":447.7,"upper95ci":464.16},"presentation":{"elo":351.74,"lower95ci":335.58,"upper95ci":367.57},"rubricPassRate":0.12424242424242424}` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038 | page-018 |
| 68 | gemini-3-1-pro-preview | contextWindowTokens | `1000000` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-018 |
| 69 | gemini-3-1-pro-preview | critpt | `0.177142857142857` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-018 |
| 70 | gemini-3-1-pro-preview | deprecated | `false` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-018 |
| 71 | gemini-3-1-pro-preview | gdpvalNormalized | `0.14723000000000003` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-018 |
| 72 | gemini-3-1-pro-preview | gpqa | `0.941414141414141` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-018 |
| 73 | gemini-3-1-pro-preview | hle | `0.470342910101946` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-018 |
| 74 | gemini-3-1-pro-preview | ifbench | `0.771428571428571` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-018 |
| 75 | gemini-3-1-pro-preview | intelligenceIndexCost | `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039; `{"answer":113.083284,"cacheRead":143.4575491095552,"cacheWrite":945.909356904448,"input":1130.4864500140031,"nonCacheInput":41.119544,"output":804.903852,"reasoning":691.820568,"total":1935.3903020140033}` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038 | page-018 |
| 76 | gemini-3-1-pro-preview | intelligenceIndexIsEstimated | `false` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-018 |
| 77 | gemini-3-1-pro-preview | intelligenceIndexOutputTokensPerTask | `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039; `{"answer":5622.60726322975,"output":18112.30078500516,"reasoning":12489.693521775413}` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038 | page-018 |
| 78 | gemini-3-1-pro-preview | intelligenceIndexTimePerTask | `146.81557615911277` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-018 |
| 79 | gemini-3-1-pro-preview | itBenchSre | `0.303309120258273` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-018 |
| 80 | gemini-3-1-pro-preview | lcr | `0.82` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-018 |
| 81 | gemini-3-1-pro-preview | mlcrOverall | `0.155555555555556` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-018 |
| 82 | gemini-3-1-pro-preview | mmmuPro | `0.824277456647399` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-018 |
| 83 | gemini-3-1-pro-preview | omniscience | `31.883333333333333` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-018 |
| 84 | gemini-3-1-pro-preview | price1mBlended7To2To1 | `1.7399999999999998` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-018 |
| 85 | gemini-3-1-pro-preview | price1mInputTokens | `2` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-018 |
| 86 | gemini-3-1-pro-preview | price1mOutputTokens | `12` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-018 |
| 87 | gemini-3-1-pro-preview | scicode | `0.586805555555556` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-018 |
| 88 | gemini-3-1-pro-preview | tau2 | `0.956140350877193` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-018 |
| 89 | gemini-3-1-pro-preview | tauBanking | `0.214432989690722` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-018 |
| 90 | gemini-3-1-pro-preview | terminalBench21 | `0.737827715355805` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-018 |
| 91 | gemini-3-1-pro-preview | terminalBench40 | `0.0404040404040404` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-018 |
| 92 | gemini-3-1-pro-preview | terminalbenchHard | `0.537878787878788` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-018 |
| 93 | gemini-3-1-pro-preview | timeToFirstAnswerToken | `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039; `{"input":23.1540204669998,"reasoning":0,"total":23.1540204669998}` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038 | page-018 |
| 94 | gemini-3-5-flash-lite | automationBenchPartialScore | `0.25011270511840955` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-019 |
| 95 | gemini-3-5-flash-lite | briefcaseBreakdown | `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039; `{"analyticalQuality":{"elo":389.57,"lower95ci":371.96,"upper95ci":405.79},"overall":{"elo":648.11,"lower95ci":638.93,"upper95ci":656.19},"presentation":{"elo":758.93,"lower95ci":741.99,"upper95ci":774.38},"rubricPassRate":0.1484848484848485}` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038 | page-019 |
| 96 | gemini-3-5-flash-lite | contextWindowTokens | `1000000` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-019 |
| 97 | gemini-3-5-flash-lite | critpt | `0` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-019 |
| 98 | gemini-3-5-flash-lite | deprecated | `false` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-019 |
| 99 | gemini-3-5-flash-lite | effort_label | `"high"` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-019 |
| 100 | gemini-3-5-flash-lite | gdpvalNormalized | `0.24287999999999998` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-019 |
| 101 | gemini-3-5-flash-lite | gpqa | `0.838383838383838` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-019 |
| 102 | gemini-3-5-flash-lite | hle | `0.188137164040778` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-019 |
| 103 | gemini-3-5-flash-lite | intelligenceIndexCost | `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039; `{"answer":26.589145,"cacheRead":19.104187967132667,"cacheWrite":191.30079042867334,"input":216.946020295806,"nonCacheInput":6.5410419,"output":148.6879075,"reasoning":122.0987625,"total":365.633927795806}` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038 | page-019 |
| 104 | gemini-3-5-flash-lite | intelligenceIndexIsEstimated | `false` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-019 |
| 105 | gemini-3-5-flash-lite | intelligenceIndexOutputTokensPerTask | `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039; `{"answer":7101.546561550058,"output":17506.699128048065,"reasoning":10405.152566498009}` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038 | page-019 |
| 106 | gemini-3-5-flash-lite | intelligenceIndexTimePerTask | `47.65556567901026` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-019 |
| 107 | gemini-3-5-flash-lite | lcr | `0.76` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-019 |
| 108 | gemini-3-5-flash-lite | mlcrOverall | `0.0722222222222222` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-019 |
| 109 | gemini-3-5-flash-lite | mmmuPro | `0.790173410404624` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-019 |
| 110 | gemini-3-5-flash-lite | omniscience | `5.233333333333333` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-019 |
| 111 | gemini-3-5-flash-lite | price1mBlended7To2To1 | `0.331` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-019 |
| 112 | gemini-3-5-flash-lite | price1mInputTokens | `0.3` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-019 |
| 113 | gemini-3-5-flash-lite | price1mOutputTokens | `2.5` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-019 |
| 114 | gemini-3-5-flash-lite | scicode | `0.413194444444444` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-019 |
| 115 | gemini-3-5-flash-lite | tauBanking | `0.175257731958763` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-019 |
| 116 | gemini-3-5-flash-lite | terminalBench21 | `0.535580524344569` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-019 |
| 117 | gemini-3-5-flash-lite | terminalBench40 | `0.0101010101010101` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-019 |
| 118 | gemini-3-5-flash-lite | timeToFirstAnswerToken | `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039; `{"input":8.62225337599989,"reasoning":0,"total":8.62225337599989}` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038 | page-019 |
| 119 | gemini-3-8-flash | automationBenchPartialScore | `0.5993009432118243` @ page-002..004,page-007..012,page-014..016,page-018..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-029..032,page-034..035,page-039 | page-022 |
| 120 | gemini-3-8-flash | briefcaseBreakdown | `null` @ page-001,page-005..006,page-013,page-017,page-029..032,page-034..035,page-039; `{"analyticalQuality":{"elo":1141.62,"lower95ci":1126.2,"upper95ci":1157.38},"overall":{"elo":1202.91,"lower95ci":1194.52,"upper95ci":1211.1},"presentation":{"elo":1219.01,"lower95ci":1203.58,"upper95ci":1234.46},"rubricPassRate":0.4212121212121212}` @ page-002..004,page-007..012,page-014..016,page-018..028,page-033,page-036..038 | page-022 |
| 121 | gemini-3-8-flash | contextWindowTokens | `1000000` @ page-002..004,page-007..012,page-014..016,page-018..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-029..032,page-034..035,page-039 | page-022 |
| 122 | gemini-3-8-flash | critpt | `0.182857142857143` @ page-002..004,page-007..012,page-014..016,page-018..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-029..032,page-034..035,page-039 | page-022 |
| 123 | gemini-3-8-flash | deprecated | `false` @ page-002..004,page-007..012,page-014..016,page-018..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-029..032,page-034..035,page-039 | page-022 |
| 124 | gemini-3-8-flash | effort_label | `"high"` @ page-002..004,page-007..012,page-014..016,page-018..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-029..032,page-034..035,page-039 | page-022 |
| 125 | gemini-3-8-flash | gdpvalNormalized | `0.46752` @ page-002..004,page-007..012,page-014..016,page-018..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-029..032,page-034..035,page-039 | page-022 |
| 126 | gemini-3-8-flash | gpqa | `0.952525252525253` @ page-002..004,page-007..012,page-014..016,page-018..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-029..032,page-034..035,page-039 | page-022 |
| 127 | gemini-3-8-flash | hle | `0.478220574606117` @ page-002..004,page-007..012,page-014..016,page-018..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-029..032,page-034..035,page-039 | page-022 |
| 128 | gemini-3-8-flash | intelligenceIndexCost | `null` @ page-001,page-005..006,page-013,page-017,page-029..032,page-034..035,page-039; `{"answer":135.90560250000001,"cacheRead":389.25144973864946,"cacheWrite":571.6260811135048,"input":976.8698213521543,"nonCacheInput":15.9922905,"output":645.85655625,"reasoning":509.95095375,"total":1622.7263776021543}` @ page-002..004,page-007..012,page-014..016,page-018..028,page-033,page-036..038 | page-022 |
| 129 | gemini-3-8-flash | intelligenceIndexIsEstimated | `false` @ page-002..004,page-007..012,page-014..016,page-018..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-029..032,page-034..035,page-039 | page-022 |
| 130 | gemini-3-8-flash | intelligenceIndexOutputTokensPerTask | `null` @ page-001,page-005..006,page-013,page-017,page-029..032,page-034..035,page-039; `{"answer":28275.424690494336,"output":71002.56693639373,"reasoning":42727.142245899406}` @ page-002..004,page-007..012,page-014..016,page-018..028,page-033,page-036..038 | page-022 |
| 131 | gemini-3-8-flash | intelligenceIndexTimePerTask | `536.8195928553656` @ page-002..004,page-007..012,page-014..016,page-018..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-029..032,page-034..035,page-039 | page-022 |
| 132 | gemini-3-8-flash | itBenchSre | `0.525423728813559` @ page-002..004,page-007..012,page-014..016,page-018..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-029..032,page-034..035,page-039 | page-022 |
| 133 | gemini-3-8-flash | lcr | `0.813333333333333` @ page-002..004,page-007..012,page-014..016,page-018..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-029..032,page-034..035,page-039 | page-022 |
| 134 | gemini-3-8-flash | mlcrOverall | `0.216666666666667` @ page-002..004,page-007..012,page-014..016,page-018..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-029..032,page-034..035,page-039 | page-022 |
| 135 | gemini-3-8-flash | mmmuPro | `0.85606936416185` @ page-002..004,page-007..012,page-014..016,page-018..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-029..032,page-034..035,page-039 | page-022 |
| 136 | gemini-3-8-flash | name | `"Gemini 3.8 Flash (High)"` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `"Gemini 3.8 Flash"` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-022 |
| 137 | gemini-3-8-flash | omniscience | `29.55` @ page-002..004,page-007..012,page-014..016,page-018..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-029..032,page-034..035,page-039 | page-022 |
| 138 | gemini-3-8-flash | price1mBlended7To2To1 | `0.5775` @ page-002..004,page-007..012,page-014..016,page-018..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-029..032,page-034..035,page-039 | page-022 |
| 139 | gemini-3-8-flash | price1mInputTokens | `0.75` @ page-002..004,page-007..012,page-014..016,page-018..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-029..032,page-034..035,page-039 | page-022 |
| 140 | gemini-3-8-flash | price1mOutputTokens | `3.75` @ page-002..004,page-007..012,page-014..016,page-018..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-029..032,page-034..035,page-039 | page-022 |
| 141 | gemini-3-8-flash | scicode | `0.565972222222222` @ page-002..004,page-007..012,page-014..016,page-018..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-029..032,page-034..035,page-039 | page-022 |
| 142 | gemini-3-8-flash | tauBanking | `0.449484536082474` @ page-002..004,page-007..012,page-014..016,page-018..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-029..032,page-034..035,page-039 | page-022 |
| 143 | gemini-3-8-flash | terminalBench21 | `0.876404494382023` @ page-002..004,page-007..012,page-014..016,page-018..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-029..032,page-034..035,page-039 | page-022 |
| 144 | gemini-3-8-flash | terminalBench40 | `0.196969696969697` @ page-002..004,page-007..012,page-014..016,page-018..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-029..032,page-034..035,page-039 | page-022 |
| 145 | gemini-3-8-flash | timeToFirstAnswerToken | `null` @ page-001,page-005..006,page-013,page-017,page-029..032,page-034..035,page-039; `{"input":24.3262215479999,"reasoning":0,"total":24.3262215479999}` @ page-002..004,page-007..012,page-014..016,page-018..028,page-033,page-036..038 | page-022 |
| 146 | gemini-4-argon | name | `"Gemini 4 Argon (High)"` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `"Gemini 4 Argon"` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-039 |
| 147 | gpt-5-3-codex | briefcaseBreakdown | `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039; `{"analyticalQuality":{"elo":852.91,"lower95ci":840.65,"upper95ci":866.05},"overall":{"elo":867.73,"lower95ci":860.95,"upper95ci":875.39},"presentation":{"elo":841.03,"lower95ci":827.63,"upper95ci":854.36},"rubricPassRate":0.21818181818181817}` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038 | page-023 |
| 148 | gpt-5-3-codex | contextWindowTokens | `400000` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-023 |
| 149 | gpt-5-3-codex | critpt | `0.168571428571429` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-023 |
| 150 | gpt-5-3-codex | deprecated | `false` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-023 |
| 151 | gpt-5-3-codex | effort_label | `"xhigh"` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-023 |
| 152 | gpt-5-3-codex | gpqa | `0.915151515151515` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-023 |
| 153 | gpt-5-3-codex | hle | `0.424930491195551` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-023 |
| 154 | gpt-5-3-codex | ifbench | `0.753741496598639` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-023 |
| 155 | gpt-5-3-codex | intelligenceIndexIsEstimated | `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039; `true` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038 | page-023 |
| 156 | gpt-5-3-codex | lcr | `0.833333333333333` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-023 |
| 157 | gpt-5-3-codex | mmmuPro | `0.784971098265896` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-023 |
| 158 | gpt-5-3-codex | name | `"GPT-5.3 Codex (Xhigh)"` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `"GPT-5.3 Codex"` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-023 |
| 159 | gpt-5-3-codex | omniscience | `10.866666666666667` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-023 |
| 160 | gpt-5-3-codex | price1mBlended7To2To1 | `1.8725` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-023 |
| 161 | gpt-5-3-codex | price1mInputTokens | `1.75` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-023 |
| 162 | gpt-5-3-codex | price1mOutputTokens | `14` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-023 |
| 163 | gpt-5-3-codex | tau2 | `0.859649122807018` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-023 |
| 164 | gpt-5-3-codex | terminalbenchHard | `0.53030303030303` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-023 |
| 165 | gpt-5-3-codex | timeToFirstAnswerToken | `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039; `{"input":74.6172217389999,"reasoning":0,"total":74.6172217389999}` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038 | page-023 |
| 166 | gpt-5-5-instant-06-26 | automationBenchPartialScore | `0.36436554387118747` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-026 |
| 167 | gpt-5-5-instant-06-26 | briefcaseBreakdown | `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039; `{"analyticalQuality":{"elo":1050.84,"lower95ci":1035.38,"upper95ci":1067.37},"overall":{"elo":1085.43,"lower95ci":1076.66,"upper95ci":1093.72},"presentation":{"elo":1198.29,"lower95ci":1181.36,"upper95ci":1213.3},"rubricPassRate":0.2696969696969697}` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038 | page-026 |
| 168 | gpt-5-5-instant-06-26 | contextWindowTokens | `400000` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-026 |
| 169 | gpt-5-5-instant-06-26 | critpt | `0` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-026 |
| 170 | gpt-5-5-instant-06-26 | deprecated | `false` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-026 |
| 171 | gpt-5-5-instant-06-26 | gdpvalNormalized | `0.005909999999999997` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-026 |
| 172 | gpt-5-5-instant-06-26 | gpqa | `0.823232323232323` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-026 |
| 173 | gpt-5-5-instant-06-26 | hle | `0.199258572752549` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-026 |
| 174 | gpt-5-5-instant-06-26 | intelligenceIndexCost | `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039; `{"answer":237.80358,"cacheRead":258.6267805518297,"cacheWrite":498.4975294817031,"input":866.4088800335328,"nonCacheInput":109.28457,"output":317.92725,"reasoning":80.12367,"total":1184.3361300335328}` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038 | page-026 |
| 175 | gpt-5-5-instant-06-26 | intelligenceIndexIsEstimated | `false` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-026 |
| 176 | gpt-5-5-instant-06-26 | intelligenceIndexOutputTokensPerTask | `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039; `{"answer":3832.170126214263,"output":5040.486542289461,"reasoning":1208.3164160751978}` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038 | page-026 |
| 177 | gpt-5-5-instant-06-26 | intelligenceIndexTimePerTask | `41.311460953293846` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-026 |
| 178 | gpt-5-5-instant-06-26 | lcr | `0.7` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-026 |
| 179 | gpt-5-5-instant-06-26 | omniscience | `3.7` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-026 |
| 180 | gpt-5-5-instant-06-26 | price1mBlended7To2To1 | `4.35` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-026 |
| 181 | gpt-5-5-instant-06-26 | price1mInputTokens | `5` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-026 |
| 182 | gpt-5-5-instant-06-26 | price1mOutputTokens | `30` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-026 |
| 183 | gpt-5-5-instant-06-26 | scicode | `0.525462962962963` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-026 |
| 184 | gpt-5-5-instant-06-26 | tauBanking | `0.117525773195876` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-026 |
| 185 | gpt-5-5-instant-06-26 | terminalBench21 | `0.348314606741573` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-026 |
| 186 | gpt-5-5-instant-06-26 | terminalBench40 | `0.126262626262626` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-026 |
| 187 | gpt-5-5-instant-06-26 | timeToFirstAnswerToken | `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039; `{"input":0.961419767000052,"reasoning":15.540635046828688,"total":16.50205481382874}` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038 | page-026 |
| 188 | gpt-5-6-terra | apexAgents | `0.389380530973451` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..029,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-030..032,page-034..035,page-039 | page-029 |
| 189 | gpt-5-6-terra | automationBenchPartialScore | `0.5964995802534495` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..029,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-030..032,page-034..035,page-039 | page-029 |
| 190 | gpt-5-6-terra | briefcaseBreakdown | `null` @ page-001,page-005..006,page-013,page-017,page-022,page-030..032,page-034..035,page-039; `{"analyticalQuality":{"elo":1344.14,"lower95ci":1328.23,"upper95ci":1361.75},"overall":{"elo":1333.48,"lower95ci":1325.49,"upper95ci":1341.82},"presentation":{"elo":1528.02,"lower95ci":1514.23,"upper95ci":1542.77},"rubricPassRate":0.3414141414141414}` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..029,page-033,page-036..038 | page-029 |
| 191 | gpt-5-6-terra | contextWindowTokens | `1000000` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..029,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-030..032,page-034..035,page-039 | page-029 |
| 192 | gpt-5-6-terra | critpt | `0.3` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..029,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-030..032,page-034..035,page-039 | page-029 |
| 193 | gpt-5-6-terra | deprecated | `false` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..029,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-030..032,page-034..035,page-039 | page-029 |
| 194 | gpt-5-6-terra | effort_label | `"max"` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..029,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-030..032,page-034..035,page-039 | page-029 |
| 195 | gpt-5-6-terra | gdpvalNormalized | `0.476605` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..029,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-030..032,page-034..035,page-039 | page-029 |
| 196 | gpt-5-6-terra | gpqa | `0.925252525252525` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..029,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-030..032,page-034..035,page-039 | page-029 |
| 197 | gpt-5-6-terra | hle | `0.429101019462465` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..029,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-030..032,page-034..035,page-039 | page-029 |
| 198 | gpt-5-6-terra | ifbench | `0.712244897959184` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..029,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-030..032,page-034..035,page-039 | page-029 |
| 199 | gpt-5-6-terra | intelligenceIndexCost | `null` @ page-001,page-005..006,page-013,page-017,page-022,page-030..032,page-034..035,page-039; `{"answer":210.80343600000003,"cacheRead":648.4629313120057,"cacheWrite":332.48598859993035,"input":1038.719841911936,"nonCacheInput":57.770922,"output":1461.995868,"reasoning":1251.192432,"total":2500.715709911936}` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..029,page-033,page-036..038 | page-029 |
| 200 | gpt-5-6-terra | intelligenceIndexIsEstimated | `false` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..029,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-030..032,page-034..035,page-039 | page-029 |
| 201 | gpt-5-6-terra | intelligenceIndexOutputTokensPerTask | `null` @ page-001,page-005..006,page-013,page-017,page-022,page-030..032,page-034..035,page-039; `{"answer":12853.54225872057,"output":38897.46393199385,"reasoning":26043.92167327328}` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..029,page-033,page-036..038 | page-029 |
| 202 | gpt-5-6-terra | intelligenceIndexTimePerTask | `352.3279078517086` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..029,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-030..032,page-034..035,page-039 | page-029 |
| 203 | gpt-5-6-terra | itBenchSre | `0.510357815442561` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..029,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-030..032,page-034..035,page-039 | page-029 |
| 204 | gpt-5-6-terra | lcr | `0.83` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..029,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-030..032,page-034..035,page-039 | page-029 |
| 205 | gpt-5-6-terra | mlcrOverall | `0.316666666666667` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..029,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-030..032,page-034..035,page-039 | page-029 |
| 206 | gpt-5-6-terra | mmmuPro | `0.806936416184971` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..029,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-030..032,page-034..035,page-039 | page-029 |
| 207 | gpt-5-6-terra | name | `"GPT-5.6 Terra (Max)"` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `"GPT-5.6 Terra"` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-029 |
| 208 | gpt-5-6-terra | omniscience | `0.05` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..029,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-030..032,page-034..035,page-039 | page-029 |
| 209 | gpt-5-6-terra | price1mBlended7To2To1 | `1.7399999999999998` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..029,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-030..032,page-034..035,page-039 | page-029 |
| 210 | gpt-5-6-terra | price1mInputTokens | `2` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..029,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-030..032,page-034..035,page-039 | page-029 |
| 211 | gpt-5-6-terra | price1mOutputTokens | `12` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..029,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-030..032,page-034..035,page-039 | page-029 |
| 212 | gpt-5-6-terra | scicode | `0.549768518518518` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..029,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-030..032,page-034..035,page-039 | page-029 |
| 213 | gpt-5-6-terra | tau2 | `0.862573099415205` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..029,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-030..032,page-034..035,page-039 | page-029 |
| 214 | gpt-5-6-terra | tauBanking | `0.402061855670103` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..029,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-030..032,page-034..035,page-039 | page-029 |
| 215 | gpt-5-6-terra | terminalBench21 | `0.880149812734082` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..029,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-030..032,page-034..035,page-039 | page-029 |
| 216 | gpt-5-6-terra | terminalBench40 | `0.353535353535354` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..029,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-030..032,page-034..035,page-039 | page-029 |
| 217 | gpt-5-6-terra | terminalbenchHard | `0.575757575757576` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..029,page-033,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-030..032,page-034..035,page-039 | page-029 |
| 218 | gpt-5-6-terra | timeToFirstAnswerToken | `null` @ page-001,page-005..006,page-013,page-017,page-022,page-030..032,page-034..035,page-039; `{"input":136.9501396495,"reasoning":0,"total":136.9501396495}` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..029,page-033,page-036..038 | page-029 |
| 219 | gpt-6-1-sol | name | `"GPT-6.1 Sol (Max)"` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `"GPT-6.1 Sol"` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-030 |
| 220 | gpt-6-astra | name | `"GPT-6 Astra (Max)"` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `"GPT-6 Astra"` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-031 |
| 221 | gpt-6-luna | automationBenchPartialScore | `0.5316814034483175` @ page-002..004,page-006..012,page-014..016,page-018..021,page-023..028,page-032..033,page-036..038; `null` @ page-001,page-005,page-013,page-017,page-022,page-029..031,page-034..035,page-039 | page-032 |
| 222 | gpt-6-luna | briefcaseBreakdown | `null` @ page-001,page-005,page-013,page-017,page-022,page-029..031,page-034..035,page-039; `{"analyticalQuality":{"elo":1379.99,"lower95ci":1359.95,"upper95ci":1400.57},"overall":{"elo":1336.1,"lower95ci":1326.44,"upper95ci":1345.89},"presentation":{"elo":1394.13,"lower95ci":1378.14,"upper95ci":1410.23},"rubricPassRate":0.4121212121212121}` @ page-002..004,page-006..012,page-014..016,page-018..021,page-023..028,page-032..033,page-036..038 | page-032 |
| 223 | gpt-6-luna | contextWindowTokens | `1000000` @ page-002..004,page-006..012,page-014..016,page-018..021,page-023..028,page-032..033,page-036..038; `null` @ page-001,page-005,page-013,page-017,page-022,page-029..031,page-034..035,page-039 | page-032 |
| 224 | gpt-6-luna | critpt | `0.194285714285714` @ page-002..004,page-006..012,page-014..016,page-018..021,page-023..028,page-032..033,page-036..038; `null` @ page-001,page-005,page-013,page-017,page-022,page-029..031,page-034..035,page-039 | page-032 |
| 225 | gpt-6-luna | deprecated | `false` @ page-002..004,page-006..012,page-014..016,page-018..021,page-023..028,page-032..033,page-036..038; `null` @ page-001,page-005,page-013,page-017,page-022,page-029..031,page-034..035,page-039 | page-032 |
| 226 | gpt-6-luna | effort_label | `"max"` @ page-002..004,page-006..012,page-014..016,page-018..021,page-023..028,page-032..033,page-036..038; `null` @ page-001,page-005,page-013,page-017,page-022,page-029..031,page-034..035,page-039 | page-032 |
| 227 | gpt-6-luna | gdpvalNormalized | `0.468535` @ page-002..004,page-006..012,page-014..016,page-018..021,page-023..028,page-032..033,page-036..038; `null` @ page-001,page-005,page-013,page-017,page-022,page-029..031,page-034..035,page-039 | page-032 |
| 228 | gpt-6-luna | hle | `0.385078776645042` @ page-002..004,page-006..012,page-014..016,page-018..021,page-023..028,page-032..033,page-036..038; `null` @ page-001,page-005,page-013,page-017,page-022,page-029..031,page-034..035,page-039 | page-032 |
| 229 | gpt-6-luna | intelligenceIndexCost | `null` @ page-001,page-005,page-013,page-017,page-022,page-029..031,page-034..035,page-039; `{"answer":8.0721885,"cacheRead":29.803356157696253,"cacheWrite":17.274954528796837,"input":49.86388668649309,"nonCacheInput":2.7855760000000003,"output":72.190497,"reasoning":64.1183085,"total":122.05438368649308}` @ page-002..004,page-006..012,page-014..016,page-018..021,page-023..028,page-032..033,page-036..038 | page-032 |
| 230 | gpt-6-luna | intelligenceIndexIsEstimated | `false` @ page-002..004,page-006..012,page-014..016,page-018..021,page-023..028,page-032..033,page-036..038; `null` @ page-001,page-005,page-013,page-017,page-022,page-029..031,page-034..035,page-039 | page-032 |
| 231 | gpt-6-luna | intelligenceIndexOutputTokensPerTask | `null` @ page-001,page-005,page-013,page-017,page-022,page-029..031,page-034..035,page-039; `{"answer":11122.009971258729,"output":50011.69043054027,"reasoning":38889.68045928154}` @ page-002..004,page-006..012,page-014..016,page-018..021,page-023..028,page-032..033,page-036..038 | page-032 |
| 232 | gpt-6-luna | intelligenceIndexTimePerTask | `388.56052182638547` @ page-002..004,page-006..012,page-014..016,page-018..021,page-023..028,page-032..033,page-036..038; `null` @ page-001,page-005,page-013,page-017,page-022,page-029..031,page-034..035,page-039 | page-032 |
| 233 | gpt-6-luna | lcr | `0.833333333333333` @ page-002..004,page-006..012,page-014..016,page-018..021,page-023..028,page-032..033,page-036..038; `null` @ page-001,page-005,page-013,page-017,page-022,page-029..031,page-034..035,page-039 | page-032 |
| 234 | gpt-6-luna | mlcrOverall | `0.161111111111111` @ page-002..004,page-006..012,page-014..016,page-018..021,page-023..028,page-032..033,page-036..038; `null` @ page-001,page-005,page-013,page-017,page-022,page-029..031,page-034..035,page-039 | page-032 |
| 235 | gpt-6-luna | mmmuPro | `0.796531791907514` @ page-002..004,page-006..012,page-014..016,page-018..021,page-023..028,page-032..033,page-036..038; `null` @ page-001,page-005,page-013,page-017,page-022,page-029..031,page-034..035,page-039 | page-032 |
| 236 | gpt-6-luna | name | `"GPT-6 Luna (Max)"` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `"GPT-6 Luna"` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-032 |
| 237 | gpt-6-luna | omniscience | `0.65` @ page-002..004,page-006..012,page-014..016,page-018..021,page-023..028,page-032..033,page-036..038; `null` @ page-001,page-005,page-013,page-017,page-022,page-029..031,page-034..035,page-039 | page-032 |
| 238 | gpt-6-luna | price1mBlended7To2To1 | `0.077` @ page-002..004,page-006..012,page-014..016,page-018..021,page-023..028,page-032..033,page-036..038; `null` @ page-001,page-005,page-013,page-017,page-022,page-029..031,page-034..035,page-039 | page-032 |
| 239 | gpt-6-luna | price1mInputTokens | `0.1` @ page-002..004,page-006..012,page-014..016,page-018..021,page-023..028,page-032..033,page-036..038; `null` @ page-001,page-005,page-013,page-017,page-022,page-029..031,page-034..035,page-039 | page-032 |
| 240 | gpt-6-luna | price1mOutputTokens | `0.5` @ page-002..004,page-006..012,page-014..016,page-018..021,page-023..028,page-032..033,page-036..038; `null` @ page-001,page-005,page-013,page-017,page-022,page-029..031,page-034..035,page-039 | page-032 |
| 241 | gpt-6-luna | scicode | `0.546296296296296` @ page-002..004,page-006..012,page-014..016,page-018..021,page-023..028,page-032..033,page-036..038; `null` @ page-001,page-005,page-013,page-017,page-022,page-029..031,page-034..035,page-039 | page-032 |
| 242 | gpt-6-luna | terminalBench40 | `0.126262626262626` @ page-002..004,page-006..012,page-014..016,page-018..021,page-023..028,page-032..033,page-036..038; `null` @ page-001,page-005,page-013,page-017,page-022,page-029..031,page-034..035,page-039 | page-032 |
| 243 | gpt-6-luna | timeToFirstAnswerToken | `null` @ page-001,page-005,page-013,page-017,page-022,page-029..031,page-034..035,page-039; `{"input":111.305940601,"reasoning":0,"total":111.305940601}` @ page-002..004,page-006..012,page-014..016,page-018..021,page-023..028,page-032..033,page-036..038 | page-032 |
| 244 | gpt-oss-120b | apexAgents | `0.0309734513274336` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033..034,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-035,page-039 | page-034 |
| 245 | gpt-oss-120b | automationBenchPartialScore | `0.0019906990691605595` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033..034,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-035,page-039 | page-034 |
| 246 | gpt-oss-120b | briefcaseBreakdown | `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-035,page-039; `{"analyticalQuality":{"elo":0,"lower95ci":0,"upper95ci":0},"overall":{"elo":0,"lower95ci":0,"upper95ci":0},"presentation":{"elo":0,"lower95ci":0,"upper95ci":0},"rubricPassRate":0.023232323232323233}` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033..034,page-036..038 | page-034 |
| 247 | gpt-oss-120b | contextWindowTokens | `131072` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033..034,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-035,page-039 | page-034 |
| 248 | gpt-oss-120b | critpt | `0.0114285714285714` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033..034,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-035,page-039 | page-034 |
| 249 | gpt-oss-120b | deprecated | `false` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033..034,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-035,page-039 | page-034 |
| 250 | gpt-oss-120b | effort_label | `"high"` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033..034,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-035,page-039 | page-034 |
| 251 | gpt-oss-120b | gdpvalNormalized | `0.055725000000000025` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033..034,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-035,page-039 | page-034 |
| 252 | gpt-oss-120b | gpqa | `0.781818181818182` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033..034,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-035,page-039 | page-034 |
| 253 | gpt-oss-120b | hle | `0.196014828544949` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033..034,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-035,page-039 | page-034 |
| 254 | gpt-oss-120b | ifbench | `0.689795918367347` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033..034,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-035,page-039 | page-034 |
| 255 | gpt-oss-120b | intelligenceIndexCost | `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-035,page-039; `{"answer":5.91199616,"cacheRead":0,"cacheWrite":69.92531819999999,"input":72.21144254999999,"nonCacheInput":2.2861243499999997,"output":50.080706725,"reasoning":44.168710565,"total":122.29214927499999}` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033..034,page-036..038 | page-034 |
| 256 | gpt-oss-120b | intelligenceIndexIsEstimated | `false` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033..034,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-035,page-039 | page-034 |
| 257 | gpt-oss-120b | intelligenceIndexOutputTokensPerTask | `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-035,page-039; `{"answer":11869.499232430167,"output":26953.971576042284,"reasoning":15084.472343612117}` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033..034,page-036..038 | page-034 |
| 258 | gpt-oss-120b | intelligenceIndexTimePerTask | `136.66973985895828` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033..034,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-035,page-039 | page-034 |
| 259 | gpt-oss-120b | itBenchSre | `0.0564971751412429` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033..034,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-035,page-039 | page-034 |
| 260 | gpt-oss-120b | lcr | `0.52` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033..034,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-035,page-039 | page-034 |
| 261 | gpt-oss-120b | mlcrOverall | `0.0111111111111111` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033..034,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-035,page-039 | page-034 |
| 262 | gpt-oss-120b | name | `"gpt-oss-120b (High)"` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `"gpt-oss-120b"` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-034 |
| 263 | gpt-oss-120b | omniscience | `-49.25` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033..034,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-035,page-039 | page-034 |
| 264 | gpt-oss-120b | price1mBlended7To2To1 | `0.177` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033..034,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-035,page-039 | page-034 |
| 265 | gpt-oss-120b | price1mInputTokens | `0.15` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033..034,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-035,page-039 | page-034 |
| 266 | gpt-oss-120b | price1mOutputTokens | `0.595` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033..034,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-035,page-039 | page-034 |
| 267 | gpt-oss-120b | scicode | `0.340277777777778` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033..034,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-035,page-039 | page-034 |
| 268 | gpt-oss-120b | tau2 | `0.657894736842105` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033..034,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-035,page-039 | page-034 |
| 269 | gpt-oss-120b | tauBanking | `0.127835051546392` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033..034,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-035,page-039 | page-034 |
| 270 | gpt-oss-120b | terminalBench21 | `0.262172284644195` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033..034,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-035,page-039 | page-034 |
| 271 | gpt-oss-120b | terminalBench40 | `0` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033..034,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-035,page-039 | page-034 |
| 272 | gpt-oss-120b | terminalbenchHard | `0.234848484848485` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033..034,page-036..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-035,page-039 | page-034 |
| 273 | gpt-oss-120b | timeToFirstAnswerToken | `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-035,page-039; `{"input":0.871495570999969,"reasoning":10.913394011529103,"total":11.784889582529072}` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033..034,page-036..038 | page-034 |
| 274 | gpt-oss-20b | apexAgents | `0.00737463126843658` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-035..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034,page-039 | page-035 |
| 275 | gpt-oss-20b | automationBenchPartialScore | `0.0019111706206154657` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-035..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034,page-039 | page-035 |
| 276 | gpt-oss-20b | briefcaseBreakdown | `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034,page-039; `{"analyticalQuality":{"elo":0,"lower95ci":0,"upper95ci":0},"overall":{"elo":0,"lower95ci":0,"upper95ci":0},"presentation":{"elo":0,"lower95ci":0,"upper95ci":0},"rubricPassRate":0.024242424242424242}` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-035..038 | page-035 |
| 277 | gpt-oss-20b | contextWindowTokens | `131072` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-035..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034,page-039 | page-035 |
| 278 | gpt-oss-20b | critpt | `0.0142857142857143` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-035..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034,page-039 | page-035 |
| 279 | gpt-oss-20b | deprecated | `false` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-035..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034,page-039 | page-035 |
| 280 | gpt-oss-20b | effort_label | `"high"` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-035..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034,page-039 | page-035 |
| 281 | gpt-oss-20b | gdpvalNormalized | `0` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-035..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034,page-039 | page-035 |
| 282 | gpt-oss-20b | gpqa | `0.687878787878788` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-035..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034,page-039 | page-035 |
| 283 | gpt-oss-20b | hle | `0.10982391102873` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-035..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034,page-039 | page-035 |
| 284 | gpt-oss-20b | ifbench | `0.651020408163265` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-035..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034,page-039 | page-035 |
| 285 | gpt-oss-20b | intelligenceIndex | `8.9675171856126` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `9.95295342568922` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-035 |
| 286 | gpt-oss-20b | intelligenceIndexCost | `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034,page-039; `{"answer":0.71915724,"cacheRead":0,"cacheWrite":17.79764665,"input":18.84262688,"nonCacheInput":1.04498023,"output":12.5627931,"reasoning":11.843635860000001,"total":31.40541998}` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-035..038 | page-035 |
| 287 | gpt-oss-20b | intelligenceIndexIsEstimated | `false` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-035..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034,page-039 | page-035 |
| 288 | gpt-oss-20b | intelligenceIndexOutputTokensPerTask | `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034,page-039; `{"answer":2000.674950298791,"output":18108.80158174822,"reasoning":16108.126631449428}` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-035..038 | page-035 |
| 289 | gpt-oss-20b | intelligenceIndexTimePerTask | `85.47996549403233` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-035..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034,page-039 | page-035 |
| 290 | gpt-oss-20b | lcr | `0.346666666666667` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-035..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034,page-039 | page-035 |
| 291 | gpt-oss-20b | mlcrOverall | `0.00555555555555556` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-035..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034,page-039 | page-035 |
| 292 | gpt-oss-20b | name | `"gpt-oss-20b (High)"` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `"gpt-oss-20b"` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-035 |
| 293 | gpt-oss-20b | omniscience | `-63.05` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-035..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034,page-039 | page-035 |
| 294 | gpt-oss-20b | price1mBlended7To2To1 | `0.081` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-035..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034,page-039 | page-035 |
| 295 | gpt-oss-20b | price1mInputTokens | `0.07` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-035..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034,page-039 | page-035 |
| 296 | gpt-oss-20b | price1mOutputTokens | `0.18` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-035..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034,page-039 | page-035 |
| 297 | gpt-oss-20b | scicode | `0.388888888888889` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-035..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034,page-039 | page-035 |
| 298 | gpt-oss-20b | tau2 | `0.60233918128655` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-035..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034,page-039 | page-035 |
| 299 | gpt-oss-20b | tauBanking | `0.0701030927835052` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-035..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034,page-039 | page-035 |
| 300 | gpt-oss-20b | terminalBench21 | `0.138576779026217` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-035..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034,page-039 | page-035 |
| 301 | gpt-oss-20b | terminalBench40 | `0` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-035..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034,page-039 | page-035 |
| 302 | gpt-oss-20b | terminalbenchHard | `0.106060606060606` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-035..038; `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034,page-039 | page-035 |
| 303 | gpt-oss-20b | timeToFirstAnswerToken | `null` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034,page-039; `{"input":0.866186908000032,"reasoning":10.767258294928437,"total":11.633445202928469}` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-035..038 | page-035 |
| 304 | grok-4-7 | name | `"Grok 4.7 (Xhigh)"` @ page-002..004,page-007..012,page-014..016,page-018..021,page-023..028,page-033,page-036..038; `"Grok 4.7"` @ page-001,page-005..006,page-013,page-017,page-022,page-029..032,page-034..035,page-039 | page-039 |

## 7. 驗證邊界

- 指定測試均已執行；完整失敗清單、基底對照及逐檔統計見 [WORKER-REPORT.md](../../WORKER-REPORT.md)。
- AA 專項在 Python 3.14.5 與 3.9.6 均為 75 過、0 敗、1 跳過；3.9 語法檢查 20 檔通過。
- 殼層全套 129 過、3 敗；Python 3.14 全套 834 項（14 失敗、33 錯誤、4 跳過）；
  Python 3.9 全套 843 項（15 失敗、33 錯誤、17 跳過）。未達全綠，狀態為 PARTIAL。
- 33 個錯誤均為權限拒絕：26 個程序查詢、7 個本機監聽；所有斷言失敗已在未修改基底重現，
  包含 3.9 額外的系統 Python 快取寫入。不是任務書原先預期的傳輸覆蓋檔快照不符。
- 工作樹沙箱重播既有探測得到 79 筆映射、9 個證據錨點；Haiku 五列仍是未探測。
  這份重播檔只用於離線格式／載入驗證，不代表目前主機或新模型的端到端可用性。
- Anthropic 模型識別字與五種強度已由上述三份官方文件確認（2026-10-08）；
  本機五列仍是「not probed on host」，各強度實際請求形狀與真實派送能力尚未驗證。
  價格僅沿用 AA 列值，未另行確認官方定價。
- 未 commit、push、打標籤、發布、操作服務；未修改主工作目錄或使用者設定。
- 操作者已於 2026-10-08 核准本快照；正式主機重簽及新模型探測由主控／操作者接續，本工人未執行 `omnilane resign`。
