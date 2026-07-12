<div align="center">

# omnilane

**一張路由表,四個執行框架通用。**

把每個子任務自動派給最強的模型——<br/>
**Claude Code · Codex · Grok Build · Antigravity**,直接用你既有的訂閱。

[![ci](https://github.com/Seraphim0916/omnilane/actions/workflows/ci.yml/badge.svg)](https://github.com/Seraphim0916/omnilane/actions/workflows/ci.yml)
[![license](https://img.shields.io/github/license/Seraphim0916/omnilane)](LICENSE)
[![version](https://img.shields.io/github/v/tag/Seraphim0916/omnilane?label=version)](https://github.com/Seraphim0916/omnilane/tags)

[English](README.md) · **繁體中文** · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · [한국어](README.ko.md)

</div>

---

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

- **`routing.yaml`** — 通道 → 廠商+模型+推理檔位。一個檔案,四個執行框架共用。
- **候選鏈** — 一條通道可以列多個候選(`codex … | claude … | off`),
  派工時自動採用本機**實際裝了**的第一個廠商 CLI。只訂一、兩家也能用同一張表。
- **`scripts/dispatch.sh <通道> "<任務>"`** — 查表後以無頭方式呼叫對應廠商的 CLI。
- **`skills/omnilane/SKILL.md`** — 一份技能四個框架都能載入:
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

## 安裝

前置需求:想路由到的廠商 CLI(`codex`、`claude`、`grok`、`agy`)已登入且在
`PATH` 上——**有幾家裝幾家就好**,缺的通道會自動降級。

最快:`./install.sh` — 自動偵測本機的 CLI、接好技能、列出其餘的外掛安裝指令、
印出這台機器的生效路由表,最後問你要不要進入互動設定選單(`--uninstall` 可逆)。
手動接線:

- **Claude Code**:以外掛安裝(附 `/route`、`/route-jobs` 指令),
  或把 `skills/omnilane` 放進 `~/.claude/skills/`。
- **Codex**:把 `skills/omnilane` 放進或連結到 `~/.codex/skills/`。
- **Grok Build**:`grok plugin install <本 repo 路徑> --trust`
- **Antigravity**:`agy plugin install <本 repo 路徑>`(先用
  `agy plugin validate` 檢查)

## 自訂設定

三層,全部選用:

1. **互動選單** — `scripts/configure.sh` 列出全部通道,讓你逐條選
   廠商 → 模型 → 推理檔位(有建議清單,也可自由輸入未來的新模型名),
   寫進 `~/.omnilane/routing.local.yaml`。`install.sh` 裝完會主動問要不要跑。
2. **`~/.omnilane/routing.local.yaml`** — 手改覆寫檔,格式同 `routing.yaml`,
   本機優先。參考 `routing.local.yaml.example`。
3. **`~/.omnilane/local.sh`** — 機器專屬的執行檔路徑、proxy、認證包裝;
   每個執行器都會載入,永不進版控。參考 `local.sh.example`。

隨時檢查結果:

```
scripts/dispatch.sh --list     # 生效表,標出候選鏈降級與關閉的通道
```

## 指令參考

```
dispatch.sh [--background] [--mode advise|work] [--workdir 目錄]
            [--model M] [--effort E] 通道 "任務"    # "-" 表示從 stdin 讀任務
dispatch.sh --list
jobs.sh list | status 工作ID | result 工作ID
configure.sh                                        # 互動通道選單
```

退出碼:`2` 用法錯誤(通道不存在/mode 拼錯)、`3` 通道已關閉、
`4` 候選鏈裡沒有任何已安裝的 CLI、`86` 拒絕巢狀派工、`87` 等鎖逾時;
其餘直接透傳工作端自己的退出碼。

## 模式

- **advise(預設)** — 唯讀工作端。Codex 跑唯讀沙箱;Claude 只給
  Read/Glob/Grep;Grok 跑 plan 模式。適合審查、提問、第二意見。
- **work** — 允許改檔案,僅限你指定的 `--workdir`。Codex 給
  workspace-write 沙箱;Claude 自動接受編輯;Gemini 跑 accept-edits 模式。

## 內建安全機制

- **禁止巢狀派工** — 工作端不得再往外派(`OMNILANE_DEPTH` 守衛,退出碼 86),
  杜絕 AI 叫 AI 的額度連環燒。
- **Codex 排隊鎖** — 同一目標目錄的 codex 派工自動序列化(鎖以正規化後的
  workdir 為鍵);崩潰殘留的鎖以擁有者 PID 偵測後安全接管。
- **看門狗** — 每個工作端跑在 `timeout`/`gtimeout` 之下,兩者皆無時退到
  perl-alarm 後備(原生 macOS 就是這情況),卡死的 CLI 不會掛整晚
  (`OMNILANE_TIMEOUT`,預設 600 秒)。
- **背景工作生命週期** — `--background` 的工作端跑在自己的 process group,
  呼叫端退出也不受影響;被殺會落盤退出碼,`jobs.sh status` 會報 `dead`
  而不是永遠顯示 `running`。
- **任務酬載上限** — 過大的任務文字自動頭尾截斷,防止撐爆工作端脈絡。

## 預設值與資料來源

預設通道配置依據 Artificial Analysis 2026-07 快照(已對 AA 站上原始紀錄與
各廠官方定價頁交叉核對)加上公開對比評測;這些是意見不是定律——
設定選單和 `routing.local.yaml` 就是讓你不同意用的。`arbitrate` 通道預設
關閉:有自己的多模型審查閘就接上去。

## 已知限制

- **Antigravity 的 print 模式工具呼叫在現行 CLI 版本不穩定**(可能被拒或
  回無效引數)。long-context 通道的設計本來就是「把內容貼進任務」的長文
  整合,不受影響;要*讀取 repo* 的諮詢請用 claude/codex 候選。
- **Grok 沒有推理檔位開關**;effort 欄位僅為介面一致而保留,實際忽略。
- Codex 的 work 模式在非 git 目錄曾出現卡死;在 git 工作目錄(正常情況)
  使用,待查明前先避開非 git 目錄。

## 狀態

早期但審過:shell 核心經外部模型審查(11 項發現全修)加對抗式驗證。
執行器介面已穩定;Grok/Antigravity 指令殼行為可能隨 CLI 版本變動。
歡迎回報 issue 與 PR。
