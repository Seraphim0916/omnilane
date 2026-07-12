# omnilane

[English](README.md)

一張路由表,四個執行框架通用。omnilane 讓**任何**一個 agentic CLI——Claude Code、
OpenAI Codex、Grok Build、Google Antigravity——的主迴圈把子任務分類到通道(lane),
再自動把每條通道派工給該項工作最強的廠商 CLI,直接沿用你既有的訂閱登入。

```
            ┌────────────── routing.yaml(一張表)───────────────┐
 主迴圈    ─┤ hardest-coding → Codex Sol      taste-final → Claude │
 (任一 CLI) │ bulk-mechanical → Codex Terra   long-context → Gemini│
            │ triage → Codex Luna             live-search → Grok   │
            └────────────── scripts/dispatch.sh ───────────────────┘
```

## 運作方式

- **`routing.yaml`** — 通道 → 廠商+模型+推理檔位。任何一條通道都可以在
  `~/.omnilane/routing.local.yaml` 覆寫(本機優先)。
- **候選鏈(fallback chain)** — 一條通道可以列多個候選
  (`codex … | claude … | off`),派工時自動採用本機**實際裝了**的第一個廠商 CLI。
  所以只訂一、兩家的人用同一張表也能跑,缺的通道自動降級或關閉,不會撞牆。
- **`scripts/dispatch.sh <通道> "<任務>"`** — 查表後以無頭(headless)方式呼叫
  對應廠商的 CLI。長任務加 `--background`,之後用 `scripts/jobs.sh` 查狀態、取結果;
  工作端需要改檔案時用 `--mode work --workdir 目錄`。
- **`skills/omnilane/SKILL.md`** — 一份技能(skill)四個執行框架都能載入:
  先認出自己是哪個模型,自己通道的活自己做,其餘派出去。

## 通道一覽(預設值;實際生效值跑 `scripts/dispatch.sh --list` 看)

| 通道 | 首選模型 | 用途 |
|---|---|---|
| hardest-coding | GPT-5.6 Sol (xhigh) | 最難的實作、深度除錯、正確性攸關的修改 |
| bulk-mechanical | GPT-5.6 Terra (max) | 重構、搬遷、測試、大面積掃描——機械耐力活 |
| triage | GPT-5.6 Luna (medium) | 高量初篩、第一輪過濾 |
| hard-judgment | GPT-5.6 Sol (max) | 架構仲裁、深度推理、第二意見 |
| taste-final | Claude Opus 4.8 | 對外文字、prompt 與文件打磨、風格終審 |
| ui-draft | GPT-5.6 Sol (xhigh) | 有設計規範/參考圖時的 UI 出稿;開放式視覺品味交給 taste-final |
| long-context | Gemini 3.1 Pro (High) | 百萬 token 長文整合——僅限分析,不派 agentic 長鏈 |
| fast-agentic | Gemini 3.5 Flash (High) | 快速多步驟 agentic 迴圈、多模態檢查 |
| live-search | Grok 4.5 | 即時 X/網路搜尋與社群脈絡 |
| coding-overflow | Grok 4.5 | Codex 額度吃緊時的中量級編碼溢流道;事實性宣稱須另行查證 |
| arbitrate | (預設關閉) | 多模型互審閘門——接你自己的審查機制 |

每條通道的完整候選鏈寫在 `routing.yaml`;`--list` 會標出哪條通道正在用
備援候選(`# fallback`)、哪條因為缺 CLI 而關閉。

## 安裝

前置需求:想路由到的廠商 CLI(`codex`、`claude`、`grok`、`agy`)已登入且在 `PATH` 上
——**有幾家裝幾家就好**,缺的通道會自動降級。

最快:`./install.sh` — 自動偵測本機有哪些 CLI,幫你接好技能、列出其餘的
外掛(plugin)安裝指令,最後印出這台機器的生效路由表(`--uninstall` 可逆)。
手動接線:

- **Claude Code**:以外掛安裝(附 `/route`、`/route-jobs` 指令),
  或把 `skills/omnilane` 放進 `~/.claude/skills/`。
- **Codex**:把 `skills/omnilane` 放進或連結到 `~/.codex/skills/`。
- **Grok Build**:`grok plugin install <本 repo 路徑> --trust`
- **Antigravity**:`agy plugin install <本 repo 路徑>`(先用
  `agy plugin validate <本 repo 路徑>` 檢查)

機器專屬的執行檔路徑、proxy、認證包裝寫在 `~/.omnilane/local.sh`
(每個執行器(runner)都會載入;永不進版控)——參考 `local.sh.example` 與
`routing.local.yaml.example`。

## 內建安全機制

- 工作端**預設唯讀**(advise 模式),要改檔案必須明說 `--mode work`。
- **禁止巢狀派工**:被派出去的工作端不得再往外派(深度守衛,違反即拒絕執行)。
- 同一個工作目錄的 codex 派工**自動序列化**(避免並行互撞);持鎖程序死亡會自動接管,不留死鎖。
- 任務酬載長度上限,防止爆量。

## 預設值與資料來源

預設通道配置依據 Artificial Analysis 2026-07 的編碼/智力資料與公開的
對比評測;這些是意見不是定律——照你的偏好改 `routing.yaml`。
`arbitrate` 通道預設關閉:有自己的多模型審查閘就接上去。

## 狀態

早期版本。執行器介面已穩定;Grok/Antigravity 的指令殼行為可能隨 CLI
版本變動。歡迎回報 issue 與 PR。
