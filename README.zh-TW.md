<div align="center">

# omnilane

### 一張路由表,四個執行框架通用。

*讓主迴圈不再猜要用哪個模型。*<br/>
從 **Claude Code · Codex · Grok Build · Antigravity** 任一框架開車,每個子任務都派給<br/>
真正最擅長它的模型——Codex、Claude、Grok、Gemini、Kimi、Qwen、OpenCode,<br/>
或經 OpenRouter 直達任何託管模型——用你已經在付的訂閱,或一把 API 金鑰。

<img src="docs/hero.zh-TW.png" alt="omnilane 把每個子任務派給 Claude Code、Codex、Grok、Antigravity 中最擅長的模型" width="820"/>

[![ci](https://github.com/Seraphim0916/omnilane/actions/workflows/ci.yml/badge.svg)](https://github.com/Seraphim0916/omnilane/actions/workflows/ci.yml)
[![license](https://img.shields.io/github/license/Seraphim0916/omnilane)](LICENSE)
[![version](https://img.shields.io/github/v/tag/Seraphim0916/omnilane?label=version)](https://github.com/Seraphim0916/omnilane/tags)

[English](README.md) · **繁體中文** · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · [한국어](README.ko.md)

</div>

---

## v0.8.3 新功能

- **MCP server** — `omnilane mcp` 啟動零依賴的 stdio MCP server,任何支援
  MCP 的宿主(Claude Code、Codex、Gemini CLI、Cursor、OpenCode……)不必安裝
  skill 就能發現並呼叫 omnilane:提供 `route`、`jobs_status`、`jobs_result`、
  `list_lanes` 四個工具。`route` 預設唯讀 advise 模式;work 模式必須明確
  指定 workdir。

## v0.8.2 新功能

- **`openrouter` vendor** — 只要 `curl` 加一把 `OPENROUTER_API_KEY`,
  就能直連 OpenRouter API 派工:任何 omnilane 安裝都摸得到數百個
  託管模型,不必再裝任何代理 CLI。僅限 advise/consult(不能改檔,
  work 模式會明確報錯指路),模型 slug 必填,例如
  `dispatch.sh --vendor openrouter --model anthropic/claude-sonnet-5 consult "..."`。
- **`opencode` vendor** — 透過 OpenCode 多供應商聚合 CLI 無頭派工
  (`opencode run`)。advise 模式鎖定內建唯讀 `plan` agent;work 模式
  用 `--auto`。加入預設 `coding-overflow` 鏈作為最後備援。

## v0.8.1 新功能

- **Claude Code 外掛開場自動載入路由提醒** — 外掛新增 `SessionStart`
  hook(`hooks/hooks.json`),於開場(`startup|resume|clear`)自動注入
  路由提醒,裝外掛即生效,不必修改 `~/.claude/CLAUDE.md`。其他 CLI
  仍走 `install.sh` 的指令檔提醒。

## v0.8.0 新功能

- **兩個新派工 vendor** — `kimi`(Moonshot Kimi Code CLI)與 `qwen`
  (Alibaba Qwen Code CLI)加入,沿用統一 runner 契約:advise 唯讀、
  work 自動核准、剝除 API key 環境變數改用 CLI 自身訂閱登入、空輸出
  視為失敗。可用 `--vendor kimi|qwen` 直接點名。
- **coding-overflow 長出備援鏈** — 額度溢流道改為 grok → kimi → qwen
  再到 `off`,三家裝任一家即可用。runner 以假執行檔完成契約測試;
  歡迎回報真實模型實測結果。

## v0.7.1 新功能

- **路由表更新(2026-07 模型數據)** — hardest-coding 首選改為 GPT-5.6 Sol
  **max** 檔位:Artificial Analysis Coding Agent Index v1.1 測得 Sol (max)
  80 分為現任最高,汰換舊的「xhigh 勝 max」快照。
- **Claude 備援升檔** — hardest-coding 與 hard-judgment 的 Claude Opus 4.8
  備援改為 **xhigh**,依 Anthropic 官方對困難任務與長時間工作的建議。

## v0.7.0 新功能

- **先預覽再派工** — `--dry-run` 印出完整解析後的派工計畫(vendor、模型、
  模式、逾時、副作用判定),不呼叫模型、不建立工作狀態。
- **版本化 JSON 自動化** — `--list`/`--explain`/`--validate` 與
  `jobs list|status|result|stats` 都有 `--json` 信封;另有唯讀 `jobs wait`、
  `jobs audit`,以及帶可重現 manifest 的離線 `omnilane release-audit` 發佈稽核。
- **本機工作一條龍** — `jobs tail` 窺看即時輸出、`jobs retry` 以 fail-closed
  方式重派已完成工作、`prune --older-than` 依時間清理,`--help` 覆蓋所有指令。
- **安裝與補全更安全** — `install.sh --check`/`--dry-run` 唯讀回報漂移,
  `omnilane completion bash|zsh` 提供安全的 tab 補全,並修復五個 macOS 原生
  Bash 3.2 崩潰。

## v0.6.0 新功能

- **離線看懂並驗證路由** — 用 `--explain` 查看每個備援候選，或用
  `--validate` 檢查完整生效路由表；都不會呼叫模型或建立工作狀態。
- **用機器可讀資料觀察本機狀態** — `jobs.sh stats` 提供有界統計，
  `omnilane doctor --json` 提供健康檢查，又不會洩漏任務或結果正文。
- **在 Live Board 比較兩筆工作** — 把一筆已載入工作釘成只存在記憶體的
  參考快照，並排比較模型路徑與公開結果。
- **讓鎖恢復更安靜** — 擁有者檔案在檢查與讀取間消失時，不再洩漏容易誤判的
  缺檔診斷，同時維持 fail-closed。

## v0.5.1 新功能

- **在非 Git 目錄使用 Codex work** — 一般資料夾仍完整支援；Omnilane 不要求、
  也絕不會自動執行 `git init`。
- **乾淨停止非 Git 卡死** — 未設定整體上限時，解析後的單次看門狗會自動成為
  程序群組保險絲，同時保留手動 timeout 的優先序與退出碼語意。
- **讓版本顯示可信** — `VERSION` 現在統一供應 `omnilane --version` 與兩份
  plugin manifest，CI 會檢查變更紀錄和五語 README 是否一致。

## ⚡ 60 秒上手

```bash
git clone https://github.com/Seraphim0916/omnilane && cd omnilane
./install.sh          # 偵測你的 CLI、接好技能、說你的語言
omnilane route hardest-coding "修掉會間歇失敗的 auth token 更新測試"
omnilane ui start     # 選配:在瀏覽器即時看派工
```

## 🧭 運作方式

omnilane 讓**任何**一個 agentic CLI 的主迴圈把子任務分類到通道(lane),
再以無頭方式把每條通道派工給該項工作最強的廠商——直接沿用你既有的訂閱登入
(`openrouter` vendor 例外:免裝任何 CLI,一把 API 金鑰直連):

```mermaid
flowchart LR
    M["主迴圈<br/><i>你在用的任一 CLI</i>"] --> T{{"routing.yaml<br/>一張共用路由表"}}
    T -->|hardest-coding| C1["Codex — GPT-5.6 Sol"]
    T -->|bulk-mechanical| C2["Codex — GPT-5.6 Terra"]
    T -->|taste-final| C3["Claude — Opus 4.8"]
    T -->|long-context| C4["Gemini — 3.1 Pro"]
    T -->|live-search| C5["Grok — 4.5"]
    T -->|"arbitrate(選配)"| C6["vote — 1-4 模型評審團"]
```

- **`routing.yaml`** — 通道 → 廠商+模型+推理檔位。一個檔案,四個執行框架共用。
- **候選鏈** — 一條通道可以列多個候選(`codex … | claude … | off`),
  派工時自動採用本機**實際裝了**的第一個廠商 CLI。只訂一、兩家也能用同一張表。
- **`scripts/dispatch.sh [--vendor V] <通道> "<任務>"`** — 查表後以無頭方式
  呼叫對應廠商的 CLI。`--vendor` 會鎖定點名廠商，不做降級。
- **`skills/omnilane/SKILL.md`** — 一份技能四個框架都能載入:
  先認出自己是哪個模型,自己通道的活自己做,其餘派出去。
- **`omnilane mcp`** — 同一套路由改以 MCP stdio server 提供,
  給走 MCP 而非 skill 整合的宿主。

<div align="center">

| | | |
|:---:|:---:|:---:|
| 🧭 **一張表**<br/>四個執行框架共用 | 🪂 **候選鏈**<br/>自動降級到你有裝的 CLI | 🗳️ **意見評審團**<br/>重大決定多模型投票 |
| 🔒 **安全機制**<br/>排隊鎖 · 看門狗 · 禁巢狀 | 🌏 **五種語言**<br/>安裝器說你的母語 | ↩️ **完全可逆**<br/>`--uninstall` 一鍵還原 |

</div>

## 🛤️ 通道一覽(預設值;實際生效值跑 `scripts/dispatch.sh --list` 看)

| 通道 | 首選模型 | 備選模型 | 用途 |
|---|---|---|---|
| 🔥 hardest-coding | GPT-5.6 Sol (max) | Claude Opus 4.8 (xhigh) | 最難的實作、深度除錯、正確性攸關的修改 |
| 🏗️ bulk-mechanical | GPT-5.6 Terra (max) | Claude Sonnet 5 (high) | 重構、搬遷、測試、大面積掃描——機械耐力活 |
| 🧹 triage | GPT-5.6 Luna (medium) | Gemini 3.5 Flash (Low) | 高量初篩、第一輪過濾 |
| ⚖️ hard-judgment | GPT-5.6 Sol (max) | Claude Opus 4.8 (xhigh) | 架構仲裁、深度推理、第二意見 |
| ✒️ taste-final | Claude Opus 4.8 (high) | GPT-5.6 Sol (max) | 對外文字、prompt 與文件打磨、風格終審 |
| 💬 consult | 明確點名的廠商/模型 | —(不降級) | 自然語言直接諮詢;必須保留 `--vendor` |
| 🎨 ui-draft | GPT-5.6 Sol (xhigh) | Claude Opus 4.8 (high) | 有設計規範/參考圖時的 UI 出稿;開放式視覺品味交給 taste-final |
| 📚 long-context | Gemini 3.1 Pro (High) | Claude Opus 4.8 (high) | 百萬 token 長文整合——僅限分析,不派 agentic 長鏈 |
| ⚡ fast-agentic | Gemini 3.5 Flash (High) | GPT-5.6 Luna (high) | 快速多步驟 agentic 迴圈、多模態檢查 |
| 📡 live-search | Grok 4.5 | —(off) | 即時 X/網路搜尋與社群脈絡 |
| 🚰 coding-overflow | Grok 4.5 | Kimi K3 → Qwen3 Coder Plus → OpenCode | Codex 額度吃緊時的中量級編碼溢流道;事實性宣稱須另行查證 |
| 🗳️ arbitrate | off(選配評審團) | — | 內建意見評審團,重大決定用——預設關閉,要用在 `routing.local.yaml` 開;每評審每輪燒一次額度 |

**備選模型**是候選鏈的下一位——首選那家的廠商 CLI 沒裝時,派工就降到它。

> **Claude Fable 5 去哪了?** 預設表刻意不放:Claude 頂級檔通常就是*主迴圈本人*,
> 不是被派發的工人,而且定價高於 Opus。設定選單的模型清單有列它——
> 不同意就自己路由過去(例如在 `routing.local.yaml` 寫
> `taste-final: claude claude-fable-5 high`)。

### 自然語言諮詢

透過 `omnilane` 技能或 `/route`,你可以直接說: **「請 Opus 挑戰這個架構。」**
自然語言是由 Agent Skill 判讀,不是在 `dispatch.sh` 裡做自由文字 shell 解析。

- 只問「哪個模型適合」時,回答相符通道目前第一個可用模型,不發出模型呼叫。
- 只點廠商名時,使用該廠商在 `consult` 通道裡設定的候選模型。
- 點標準模型別名(例如 Opus)時,會鎖定技能表裡的確切模型家族。明確目標
  不存在或 CLI 不可用時會清楚失敗,不會暗中換廠商或模型家族。

<details>
<summary><b>👉 哪些通道你自己跑?選你的主控模型</b></summary>

<br/>

上面那張表跟廠商無關——一條通道的*最佳*模型不會因為誰在主控而改變。會變的是
你哪些通道**自己做**(你本來就是那個模型,省一次呼叫)、哪些**派出去**。你 CLI 裡
的 `omnilane` 技能會自動套對的那一列,這裡是給人看的版本。

- **Claude Code · Fable 5** — 自己做:hard-judgment、taste-final、最吃正確性的硬修。派出去:機械編碼量 → Codex、長文 → Gemini、即時搜尋 → Grok。
- **Claude Code · Opus 4.8** — 自己做:taste-final。hard-judgment 派給 Codex Sol(智力分高於 Opus)、所有編碼走 Codex 通道、長文 → Gemini、即時搜尋 → Grok。
- **Codex · Sol** — 自己做:hardest-coding、hard-judgment、ui-draft。派出去:taste-final → Claude、長文 → Gemini、即時搜尋 → Grok、粗活 → Codex Terra。
- **Codex · Terra** — 自己做:bulk-mechanical。真正最硬的往上升給 Sol;taste → Claude、長文 → Gemini、即時搜尋 → Grok。
- **Grok Build · Grok 4.5** — 自己做:live-search、coding-overflow(中量級編碼)。所有硬活派給 Codex/Claude/Gemini——先驗每個 API 簽章與引用事實。
- **Antigravity · Gemini** — 自己做:long-context(3.1 Pro)、fast-agentic(Flash)。編碼/判斷/文字派給 Codex/Claude;即時搜尋 → Grok。3.1 Pro 絕不接 agentic 工具長鏈。

</details>

## 🖥️ Live Board

每一次派工——不論前景或 `--background`——都是落盤的一筆 job。Live Board
是架在這個 job 儲存上、選配且唯讀的本機工作台:每個模型被問了什麼、答了
什麼、怎麼路由、是否還在執行,一眼看完。

<div align="center">

<img src="docs/live-board.png" alt="Omnilane Live Board 桌面版——左側為工作清單,右側為選定工作的任務、公開結果與模型路徑" width="820"/>

<img src="docs/live-board-mobile.png" alt="Omnilane Live Board 手機版——可搜尋的工作清單與狀態篩選" width="280"/>

</div>

```bash
omnilane ui start    # 啟動或沿用伺服器，印出通過驗證的網址
omnilane ui status   # 查看本機伺服器狀態
omnilane ui url      # 印出目前通過驗證的網址
omnilane ui stop     # 正常停止
```

桌機版的工作清單與詳細內容可各自捲動;手機版使用清單／詳細內容切換，支援返回
鍵與 Esc。伺服器傳送事件(SSE)會即時更新，又不會重建目前聚焦的工作列;短暫
斷線時保留最後畫面並自動重連。可把任何已載入的工作釘成參考，再選另一筆工作，
並排比較模型路徑與公開結果;參考快照只留在瀏覽器記憶體，關頁即消失。服務只綁
`127.0.0.1`、用隨機 token 保護、全程唯讀。畫面只顯示 `task.txt` 與公開的
`out.txt`，不顯示工作端或廠商原始 log。

核心路由不需要 Python;只有這個介面需要 Python 3.9 以上。

## 📦 安裝

前置需求:想路由到的廠商 CLI(`codex`、`claude`、`grok`、`agy`,另可選
`kimi`、`qwen`、`opencode`)已登入且在 `PATH` 上——**有幾家裝幾家就好**,
缺的通道會自動降級。`openrouter` vendor 是例外:不需要任何 CLI,只要
`curl` 和環境變數裡的 `OPENROUTER_API_KEY`。

最快:`./install.sh` — 自動偵測本機的 CLI、接好技能、列出其餘的外掛安裝指令、
印出這台機器的生效路由表,最後問你要不要進入互動設定選單(`--uninstall` 可逆)。
安裝介面依系統語言自動切換(英/繁中/簡中/日/韓,可用 `OMNILANE_LANG=zh-TW`
強制)。另提供選配的各 CLI **常駐路由提示**:在各 CLI 指令檔尾端加一段有
標記、可逆的區塊(`~/.claude/CLAUDE.md`、`~/.codex/AGENTS.md`、
`~/.grok/Agents.md`、`~/.gemini/GEMINI.md`——路徑可能隨 CLI 版本不同),
讓主迴圈記得查路由表;非互動安裝可帶 `OMNILANE_HOOKS=all|none|claude,codex`。
`./install.sh --check` 可唯讀檢查漂移；安裝或 `--uninstall` 加上
`--dry-run`，可先預覽每個由這份 checkout 擁有的檔案動作。
手動接線:

要回滾安裝器擁有的連結與標記提示，執行 `./install.sh --uninstall`。

- **Claude Code**:以外掛安裝(附 `/route`、`/route-jobs` 指令,並內建
  `SessionStart` hook,開場自動注入路由提醒,不必修改 CLAUDE.md),
  或把 `skills/omnilane` 放進 `~/.claude/skills/`。
- **Codex**:把 `skills/omnilane` 放進或連結到 `~/.codex/skills/`。
- **Grok Build**:`grok plugin install <本 repo 路徑> --trust`
- **Antigravity**:`agy plugin install <本 repo 路徑>`(先用
  `agy plugin validate` 檢查)

### MCP server

`omnilane mcp` 會啟動零依賴、跑在本機的 MCP stdio server,讓任何支援 MCP
的宿主不必安裝 skill、也不用加路由提醒,就能發現並呼叫 omnilane。在宿主
設定裡指向已安裝的 CLI 即可:

```json
{
  "mcpServers": {
    "omnilane": {
      "command": "omnilane",
      "args": ["mcp"]
    }
  }
}
```

Server 提供 `route`、`jobs_status`、`jobs_result`、`list_lanes` 四個工具。
`route` 預設唯讀 `advise` 模式;選 `work` 的呼叫必須同時提供明確的
`workdir`。

唯一的執行需求是 Node.js(不裝任何 npm 套件);也可以直接
`npm install -g omnilane`,CLI 連同 MCP server 一起裝好。

## ⚙️ 自訂設定

三層,全部選用:

1. **互動選單** — `scripts/configure.sh` 列出可設定的通道,讓你逐條選
   廠商 → 模型 → 推理檔位(有建議清單,也可自由輸入未來的新模型名),
   寫進 `~/.omnilane/routing.local.yaml`。多廠商 `consult` 會刻意略過,
   要改請手動編輯。`install.sh` 裝完會主動問要不要跑。
2. **`~/.omnilane/routing.local.yaml`** — 手改覆寫檔,格式同 `routing.yaml`,
   本機優先。參考 `routing.local.yaml.example`。
3. **`~/.omnilane/local.sh`** — 機器專屬的執行檔路徑、proxy、認證包裝;
   每個執行器都會載入,永不進版控。參考 `local.sh.example`。

隨時檢查結果:

```
scripts/dispatch.sh --list     # 生效表,標出候選鏈降級與關閉的通道
```

## 📖 指令參考

```
omnilane list | route … | jobs … | configure   # 全域指令,任何目錄都能用
                                               # (install.sh 會連結進 ~/.local/bin)
eval "$(omnilane completion bash)"             # 在目前 Bash 啟用補全
source <(omnilane completion zsh)               # 在目前 Zsh 啟用補全
omnilane ui start                              # 啟動或沿用本機 Live UI,印出網址
omnilane ui status                             # 查看 Live UI 是否運作中
omnilane ui url                                # 印出目前通過驗證的本機網址
omnilane ui stop                               # 停止 Live UI
omnilane doctor [--json]                       # 唯讀檢查路由與本機執行環境
dispatch.sh [--background] [--dry-run] [--mode advise|work] [--workdir 目錄]
            [--vendor V] [--model M] [--effort E] [--timeout SEC] [--job-timeout SEC]
            通道 "任務"                              # "-" 表示從 stdin 讀任務
dispatch.sh [--json] --list [--json]
dispatch.sh [--json] --explain 通道 [--json]       # 離線逐候選解釋路由決策
dispatch.sh [--json] --validate [--json]           # 離線檢查生效路由，不呼叫模型
jobs.sh [--json] {list | status 工作ID | result 工作ID} # JSON 結果只回中繼資料，不回本文
jobs.sh [--json] list [--lane L] [--vendor V] [--status running|done]  # 過濾清單
jobs.sh wait 工作ID [--timeout N]                  # 工作結束碼；124 逾時；125 工作者消失
jobs.sh [--json] stats [--last N] [--lane L] [--vendor V]  # 本機成功率與路由彙整
jobs.sh audit [--last N] [--json]                  # 唯讀檢查工作完整性與隱私
jobs.sh prune [--keep N] [--apply]                # 預設只預覽；只清理已完成工作
omnilane mcp                                   # MCP stdio server(需 Node.js)
omnilane release-audit [--target 版本] [--json]     # 離線、唯讀的發布閘門
configure.sh                                        # 互動通道選單
```

**重大決定可以開評審團,不是問一個人。**`arbitrate` 通道**預設關閉**——
評審團每評審每輪燒一次額度,所以做成選配。要用就在 `routing.local.yaml`
寫 `arbitrate: vote codex,claude,grok -`,或跑設定選單,從
codex/claude/grok/gemini 自選 1-4 個評審。開了之後,同一個問題丟給每個
評審,意見並排回來,由發問的主控模型當主席下裁決。檔位欄填 `2` 開辯論輪
——每個評審看完整個評審團的意見,只針對分歧互駁。進階使用者可用
`exec` 廠商換成自己的閘門:`arbitrate: exec /路徑/腳本 -`,腳本收
`MODE WORKDIR EFFORT PROMPT_FILE OUTPUT_FILE`、把裁決寫進 `OUTPUT_FILE`
(見 `scripts/runners/run-exec.sh`)。

退出碼:`2` 用法錯誤(包含廠商值不合法,或指定廠商不在該通道)、`3` 通道已關閉、
`4` 候選鏈沒有可用 CLI,或指定廠商已設定但其 CLI 不可用、
`5` 第一輪成功評審太少、`6` 第二輪沒有任何反駁成功、`86` 拒絕巢狀派工、
`87` 等鎖逾時、`124` 整體任務逾時;
其餘直接透傳工作端自己的退出碼。

## 🎭 模式

- **advise(預設)** — 唯讀工作端。Codex 跑唯讀沙箱;Claude 只給
  Read/Glob/Grep;Grok 跑 plan 模式;Kimi 與 OpenCode 鎖各自的唯讀
  plan 模式;OpenRouter 天生只做 advise(純推論)。適合審查、提問、第二意見。
- **work** — 允許改檔案,僅限你指定的 `--workdir`。Codex 給
  workspace-write 沙箱;Claude 自動接受編輯;Gemini 跑 accept-edits 模式。
  `openrouter` vendor 會明確拒絕 work 模式——改檔請走代理式 CLI vendor。

## 🔒 內建安全機制

- **禁止巢狀派工** — 工作端不得再往外派(`OMNILANE_DEPTH` 守衛,退出碼 86),
  杜絕 AI 叫 AI 的額度連環燒。
- **Codex 排隊鎖** — 同一目標目錄的 codex 派工自動序列化(鎖以正規化後的
  workdir 為鍵);崩潰殘留的鎖以擁有者 PID 偵測後安全接管。
- **看門狗** — 每個工作端跑在 `timeout`/`gtimeout` 之下,兩者皆無時退到
  perl-alarm 後備(原生 macOS 就是這情況),卡死的 CLI 不會掛整晚。
  上限作用於**每次 CLI 呼叫**,優先序由高到低:`--timeout SECONDS` > 單一通道
  `OMNILANE_TIMEOUT_<LANE>`(通道名大寫、`-` 換成 `_`,如
  `OMNILANE_TIMEOUT_HARD_JUDGMENT`) > 全域 `OMNILANE_TIMEOUT`(預設 600 秒)。
  它是單次呼叫的防卡死看門狗,不是整個任務的時間預算:會重試的 vendor(grok)
  或 vote 面板(評審 × 輪次)會發起多次呼叫,總耗時可能是該值的數倍。
- **整體任務保險絲** — 選配的 `--job-timeout SECONDS` 用同一個程序群組監工,
  一次涵蓋等鎖、重試、所有評審與輪次。優先序為旗標 >
  `OMNILANE_JOB_TIMEOUT_<LANE>` > `OMNILANE_JOB_TIMEOUT` > 關閉；唯一的自動例外
  是 Codex 在 Git worktree 外執行 `work` 時，若未設定整體上限，就沿用解析後的
  單次呼叫看門狗作為整體保險絲，上限為監工支援的 999999999 秒。到期會清掉
  受監工的程序群組並回傳 124。這個自動保險絲需要內附的 Perl 監工；若環境
  無法使用，派工會警告但仍透過原有單次呼叫看門狗路徑執行非 Git 工作；若連
  單次看門狗工具都沒有，該路徑會另外警告。
  像 fubon-autotrade 規模的完整深度審查,建議先從
  2–4 小時(7200–14400 秒)起跳,單次呼叫看門狗可先設 30 分鐘;這只是建議值,
  不會寫死成預設。
- **背景工作生命週期** — `--background` 的工作端跑在自己的 process group,
  呼叫端退出也不受影響;被殺會落盤退出碼,`jobs.sh status` 會報 `dead`
  而不是永遠顯示 `running`。
- **任務酬載上限** — 過大的任務文字自動頭尾截斷,防止撐爆工作端脈絡。

## 📊 預設值與資料來源

預設通道配置依據 Artificial Analysis 2026-07 快照(已對 AA 站上原始紀錄與
各廠官方定價頁交叉核對)加上公開對比評測;這些是意見不是定律——
設定選單和 `routing.local.yaml` 就是讓你不同意用的。

## ⚠️ 已知限制

- **Antigravity 的 print 模式工具呼叫在現行 CLI 版本不穩定**(可能被拒或
  回無效引數)。long-context 通道的設計本來就是「把內容貼進任務」的長文
  整合,不受影響;要*讀取 repo* 的諮詢請用 claude/codex 候選。
- **Grok 沒有推理檔位開關**;effort 欄位僅為介面一致而保留,實際忽略。
- **非 Git 的 Codex work 仍受支援。** 部分 Codex CLI 版本可能在 Git worktree
  外卡住，因此上面的自動保險絲會限制這個情境並清掉受監工的程序群組。Omnilane
  不會自動執行 `git init`，也不要求使用者建立 repo。

## 🌱 狀態

v0.8.3 共有八個派工 vendor——四個框架原生(codex、claude、grok、gemini)、
三個聚合/溢流 CLI(kimi、qwen、opencode),加上免 CLI 的 `openrouter` 直連
API vendor——全部走統一 runner 契約並附 contract 測試,另有 Claude Code
`SessionStart` 自動提醒與 MCP stdio server 介面(`omnilane mcp`)。kimi、qwen、opencode、openrouter 的 runner 以假
執行檔做過契約測試;歡迎回報真實模型使用經驗。Grok/Antigravity 指令殼行為
仍可能隨 CLI 版本變動。歡迎回報 issue 與 PR。

專案文件：[貢獻指南](CONTRIBUTING.md) · [安全政策](SECURITY.md) ·
[變更紀錄](CHANGELOG.md)
