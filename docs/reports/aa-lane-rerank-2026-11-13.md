<!-- 來源：agy-s／MacStudio｜2026-11-13 -->
# Omnilane 0.63.0 全車道依 AA v5.0.1 重排白話對照報告（2026-11-13）

## 一、重點結論（一分鐘看完）

1. **只有一條車道改了第 1 順位首選：`bulk-mechanical`** 由原本的 `codex gpt-6.1-sol medium`（分數 46）改為 **`codex gpt-6.1-sol xhigh`**（分數 52），後面依序接 `gpt-6.1-sol high`（50）、`medium`（46）、`gpt-6.1-terra high`（42）、`medium`（38）。天花板在 `52` 以上的主控派 `bulk-mechanical` 會改走 `gpt-6.1-sol xhigh`；天花板 `48`（例如 `Opus 5.5 medium`）仍走原本的 `gpt-6.1-sol medium`。
2. **其餘五條價值車道（`hardest-coding`、`hard-judgment`、`taste-final`、`ui-draft`、`long-context`）首選不變，但中段順位依 `aa_rebaseline.py value` 補齊重排**：其中最有感的是 `hardest-coding` 第 3 順位（緊接在 `Carbon high`、`Carbon low` 後方）由 `claude-fable-5-2 xhigh`（62）換成性價比勝出的 **`claude-opus-5-5 high`**（52），因此在**尚未完成 Carbon 傳輸證明的主機**上，天花板 `52` 以上派 `hardest-coding` 第一個選到的會變成 `claude-opus-5-5 high`。
3. **`coding-overflow` 移除三筆沒有 AA 分數的尾段列**（`kimi kimi-code/k3`、`qwen qwen3.8-max`、`opencode`），保留原本四筆計分列（`grok-4.7 high` → `gemini-4-argon-high` → `gemini-4-hydrogen-high` → `gemini-3.8-flash-high` → `off`）；真要用那三家仍可用 `--vendor` 明確指定。
4. **`fast-agentic`、`triage`、`consult`、`live-search`、`arbitrate` 五條車道順序完全不變**，所有 `Grok` 列全數保留，並一併清掉 `routing.yaml` 裡 `0.62.0` 留下的「`--vendor gemini` 不往後找」過時註解。
5. **衍生分數列不進車道**：本工作樹同批加入的 `Antigravity Orchestrator`（`gemini-pro-orchestrator-g1-pro`，代理上限 `54 = max(Argon high 50, Hydrogen xhigh 54)`）與 22 筆低強度上限列（`score_source: upper-bound`）均被 `value_pool()` 排除，只能明確點名使用，不影響任何車道計算結果（但提醒：若日後本機變體改成由 `Carbon` 負責規劃，代理上限須同步改取 `Carbon high` 的 `60`，由 Vincent 決定）。

---

## 二、每條車道「改前 vs 改後」首選與鏈長總覽

| 車道 | 改前首選（0.62.0） | 改後首選（0.63.0） | 候選數（改前→改後） | 變動說明 |
|---|---|---|---|---|
| `hardest-coding` | `gemini gemini-4-carbon-high -` | `gemini gemini-4-carbon-high -` | 35 → 35 | 首選不變（`gemini-4-carbon-high`）；第 3 順位由 `claude-fable-5-2 xhigh` 改為 `value` 選出的 `claude-opus-5-5 high`（52），前 10 列改為完整 `value` 鏈，其餘 25 列計分備援維持原相對順序 |
| `bulk-mechanical` | `codex gpt-6.1-sol medium` | **【變更】 `codex gpt-6.1-sol xhigh`** | 15 → 17 | **【首選變更】** 前 5 列改為完整 `value` 鏈（`gpt-6.1-sol xhigh` → `high` → `medium` → `gpt-6.1-terra high` → `medium`），新增 `xhigh`（52）與 `high`（50），備援 12 列維持原相對順序 |
| `triage` | `codex gpt-6-luna high` | `codex gpt-6-luna high` | 7 → 7 | 用途排序車道，整條維持不變 |
| `hard-judgment` | `claude claude-fable-5-2 xhigh` | `claude claude-fable-5-2 xhigh` | 28 → 28 | 首選與前 4 列不變；第 5～8 順位改為 `value` 選出的 `claude-opus-5-5 medium` 與 `claude-fable-5-1 high/medium/low`，原 `gpt-6.1-sol xhigh`、`argon-high`、`hydrogen-high` 移至備援前段（第 9～11 順位） |
| `taste-final` | `claude claude-sonnet-5-5 xhigh` | `claude claude-sonnet-5-5 xhigh` | 21 → 21 | 首選不變（`claude-sonnet-5-5 xhigh`）；第 2～3 順位改為 `value` 選出的 `claude-sonnet-5-5 high` 與 `grok-4.7 high`，其餘 18 列計分備援維持原相對順序 |
| `consult` | `codex gpt-6-astra xhigh` | `codex gpt-6-astra xhigh` | 6 → 6 | 用途排序車道，整條維持不變 |
| `ui-draft` | `gemini gemini-4-carbon-medium -` | `gemini gemini-4-carbon-medium -` | 18 → 19 | 首選與第 2 順位不變；第 3 順位新增 `value` 選出的 `gpt-6.1-sol high`（50），前 7 列改為完整 `value` 鏈，其餘 12 列計分備援維持原相對順序 |
| `long-context` | `codex gpt-6-astra medium` | `codex gpt-6-astra medium` | 15 → 16 | 首選與前 4 列不變；第 5 順位新增 `value` 選出的 `gpt-6-luna high`（21），前 6 列改為完整 `value` 鏈，其餘 10 列計分備援維持原相對順序 |
| `fast-agentic` | `codex gpt-6.1-sol medium` | `codex gpt-6.1-sol medium` | 11 → 11 | AA v5.0.1 未公布首字延遲（`first answer token (s)`），維持原順序不變 |
| `live-search` | `grok grok-4.7 high` | `grok grok-4.7 high` | 6 → 6 | 用途排序車道，整條維持不變（尾端保留 `off`） |
| `coding-overflow` | `grok grok-4.7 high` | `grok grok-4.7 high` | 8 → 5 | 首選與前 4 列計分列維持原順序，移除未計分的 `kimi kimi-code/k3`、`qwen qwen3.8-max`、`opencode -`（尾端保留 `off`） |
| `arbitrate` | `off - -` | `off - -` | 1 → 1 | 選配評審團（預設 `off`），維持不變 |

---

## 三、各呼叫端天花板（35 / 48 / 52 / 54 / 57 / 62 / 65）選到的第一個模型對照

### 3A. 全部模型已完成主機傳輸證明時（純依天花板分數解析）

> 註：代表各天花板的常見主控——`35`：`Grok 4.7 high`／`Opus 5.5 low`／`GPT-6.1 Sol low`；`48`：`Opus 5.5 medium`；`52`：`Opus 5.5 high`／`GPT-6.1 Sol xhigh`／`Astra medium`；`54`：`Fable 5.2 low`／`Carbon low`／`Hydrogen xhigh`；`57`：`Opus 5.5 xhigh`；`62`：`Fable 5.2 xhigh`／`high`；`65`：`Fable 5.2 max`。有變動的格子皆標註 **【變更】**。

| 車道 | 天花板 35 | 天花板 48 | 天花板 52 | 天花板 54 | 天花板 57 | 天花板 62 | 天花板 65 |
|---|---|---|---|---|---|---|---|
| `hardest-coding` | `claude claude-opus-5-5 low (35)` | `claude claude-opus-5-5 medium (48)` | `claude claude-opus-5-5 high (52)` | `gemini gemini-4-carbon-low - (54)` | `gemini gemini-4-carbon-low - (54)` | `gemini gemini-4-carbon-high - (60)` | `gemini gemini-4-carbon-high - (60)` |
| `bulk-mechanical` | `codex gpt-6.1-sol low (35)` | `codex gpt-6.1-sol medium (46)` | **【變更】** `codex gpt-6.1-sol medium (46)` → **`codex gpt-6.1-sol xhigh (52)`** | **【變更】** `codex gpt-6.1-sol medium (46)` → **`codex gpt-6.1-sol xhigh (52)`** | **【變更】** `codex gpt-6.1-sol medium (46)` → **`codex gpt-6.1-sol xhigh (52)`** | **【變更】** `codex gpt-6.1-sol medium (46)` → **`codex gpt-6.1-sol xhigh (52)`** | **【變更】** `codex gpt-6.1-sol medium (46)` → **`codex gpt-6.1-sol xhigh (52)`** |
| `triage` | `codex gpt-6-luna high (21)` | `codex gpt-6-luna high (21)` | `codex gpt-6-luna high (21)` | `codex gpt-6-luna high (21)` | `codex gpt-6-luna high (21)` | `codex gpt-6-luna high (21)` | `codex gpt-6-luna high (21)` |
| `hard-judgment` | `claude claude-opus-5-5 low (35)` | `claude claude-opus-5-5 medium (48)` | `claude claude-fable-5-1 xhigh (50)` | `claude claude-fable-5-2 low (54)` | `claude claude-fable-5-2 medium (56)` | `claude claude-fable-5-2 xhigh (62)` | `claude claude-fable-5-2 xhigh (62)` |
| `taste-final` | `grok grok-4.7 high (35)` | `claude claude-sonnet-5-5 high (42)` | `claude claude-sonnet-5-5 xhigh (50)` | `claude claude-sonnet-5-5 xhigh (50)` | `claude claude-sonnet-5-5 xhigh (50)` | `claude claude-sonnet-5-5 xhigh (50)` | `claude claude-sonnet-5-5 xhigh (50)` |
| `consult` | `grok grok-4.7 high (35)` | `grok grok-4.7 high (35)` | `grok grok-4.7 high (35)` | `grok grok-4.7 high (35)` | `codex gpt-6-astra xhigh (55)` | `codex gpt-6-astra xhigh (55)` | `codex gpt-6-astra xhigh (55)` |
| `ui-draft` | `claude claude-opus-5-5 low (35)` | `codex gpt-6.1-sol medium (46)` | `claude claude-opus-5-5 high (52)` | `claude claude-opus-5-5 high (52)` | `claude claude-opus-5-5 high (52)` | `gemini gemini-4-carbon-medium - (58)` | `gemini gemini-4-carbon-medium - (58)` |
| `long-context` | `codex gpt-6.1-sol low (35)` | `codex gpt-6.1-sol medium (46)` | `codex gpt-6-astra medium (52)` | `codex gpt-6-astra medium (52)` | `codex gpt-6-astra medium (52)` | `codex gpt-6-astra medium (52)` | `codex gpt-6-astra medium (52)` |
| `fast-agentic` | `codex gpt-6.1-terra low (29)` | `codex gpt-6.1-sol medium (46)` | `codex gpt-6.1-sol medium (46)` | `codex gpt-6.1-sol medium (46)` | `codex gpt-6.1-sol medium (46)` | `codex gpt-6.1-sol medium (46)` | `codex gpt-6.1-sol medium (46)` |
| `live-search` | `grok grok-4.7 high (35)` | `grok grok-4.7 high (35)` | `grok grok-4.7 high (35)` | `grok grok-4.7 high (35)` | `grok grok-4.7 high (35)` | `grok grok-4.7 high (35)` | `grok grok-4.7 high (35)` |
| `coding-overflow` | `grok grok-4.7 high (35)` | `grok grok-4.7 high (35)` | `grok grok-4.7 high (35)` | `grok grok-4.7 high (35)` | `grok grok-4.7 high (35)` | `grok grok-4.7 high (35)` | `grok grok-4.7 high (35)` |

### 3B. 主機尚未完成 Carbon／Hydrogen 傳輸證明時（跳過 Carbon 與 Hydrogen 後的實際首選）

> 註：在還沒跑過 Google AI Ultra `omnilane resign --vendor gemini` 證明 `Carbon`／`Hydrogen` 的主機上，`hardest-coding` 與 `ui-draft` 開頭的 `Carbon` 會自動跳過；此時 `hardest-coding` 在天花板 `57 / 62 / 65` 也會因第 3 順位改為 `claude-opus-5-5 high (52)` 而改變（天花板 `52 / 54` 改前改後本來就都落在 `claude-opus-5-5 high (52)`）。

| 車道 | 天花板 35 | 天花板 48 | 天花板 52 | 天花板 54 | 天花板 57 | 天花板 62 | 天花板 65 |
|---|---|---|---|---|---|---|---|
| `hardest-coding` | `claude claude-opus-5-5 low (35)` | `claude claude-opus-5-5 medium (48)` | `claude claude-opus-5-5 high (52)` | `claude claude-opus-5-5 high (52)` | **【變更】** `codex gpt-6-astra xhigh (55)` → **`claude claude-opus-5-5 high (52)`** | **【變更】** `claude claude-fable-5-2 xhigh (62)` → **`claude claude-opus-5-5 high (52)`** | **【變更】** `claude claude-fable-5-2 xhigh (62)` → **`claude claude-opus-5-5 high (52)`** |
| `bulk-mechanical` | `codex gpt-6.1-sol low (35)` | `codex gpt-6.1-sol medium (46)` | **【變更】** `codex gpt-6.1-sol medium (46)` → **`codex gpt-6.1-sol xhigh (52)`** | **【變更】** `codex gpt-6.1-sol medium (46)` → **`codex gpt-6.1-sol xhigh (52)`** | **【變更】** `codex gpt-6.1-sol medium (46)` → **`codex gpt-6.1-sol xhigh (52)`** | **【變更】** `codex gpt-6.1-sol medium (46)` → **`codex gpt-6.1-sol xhigh (52)`** | **【變更】** `codex gpt-6.1-sol medium (46)` → **`codex gpt-6.1-sol xhigh (52)`** |
| `triage` | `codex gpt-6-luna high (21)` | `codex gpt-6-luna high (21)` | `codex gpt-6-luna high (21)` | `codex gpt-6-luna high (21)` | `codex gpt-6-luna high (21)` | `codex gpt-6-luna high (21)` | `codex gpt-6-luna high (21)` |
| `hard-judgment` | `claude claude-opus-5-5 low (35)` | `claude claude-opus-5-5 medium (48)` | `claude claude-fable-5-1 xhigh (50)` | `claude claude-fable-5-2 low (54)` | `claude claude-fable-5-2 medium (56)` | `claude claude-fable-5-2 xhigh (62)` | `claude claude-fable-5-2 xhigh (62)` |
| `taste-final` | `grok grok-4.7 high (35)` | `claude claude-sonnet-5-5 high (42)` | `claude claude-sonnet-5-5 xhigh (50)` | `claude claude-sonnet-5-5 xhigh (50)` | `claude claude-sonnet-5-5 xhigh (50)` | `claude claude-sonnet-5-5 xhigh (50)` | `claude claude-sonnet-5-5 xhigh (50)` |
| `consult` | `grok grok-4.7 high (35)` | `grok grok-4.7 high (35)` | `grok grok-4.7 high (35)` | `grok grok-4.7 high (35)` | `codex gpt-6-astra xhigh (55)` | `codex gpt-6-astra xhigh (55)` | `codex gpt-6-astra xhigh (55)` |
| `ui-draft` | `claude claude-opus-5-5 low (35)` | `codex gpt-6.1-sol medium (46)` | `claude claude-opus-5-5 high (52)` | `claude claude-opus-5-5 high (52)` | `claude claude-opus-5-5 high (52)` | `claude claude-opus-5-5 high (52)` | `claude claude-opus-5-5 high (52)` |
| `long-context` | `codex gpt-6.1-sol low (35)` | `codex gpt-6.1-sol medium (46)` | `codex gpt-6-astra medium (52)` | `codex gpt-6-astra medium (52)` | `codex gpt-6-astra medium (52)` | `codex gpt-6-astra medium (52)` | `codex gpt-6-astra medium (52)` |
| `fast-agentic` | `codex gpt-6.1-terra low (29)` | `codex gpt-6.1-sol medium (46)` | `codex gpt-6.1-sol medium (46)` | `codex gpt-6.1-sol medium (46)` | `codex gpt-6.1-sol medium (46)` | `codex gpt-6.1-sol medium (46)` | `codex gpt-6.1-sol medium (46)` |
| `live-search` | `grok grok-4.7 high (35)` | `grok grok-4.7 high (35)` | `grok grok-4.7 high (35)` | `grok grok-4.7 high (35)` | `grok grok-4.7 high (35)` | `grok grok-4.7 high (35)` | `grok grok-4.7 high (35)` |
| `coding-overflow` | `grok grok-4.7 high (35)` | `grok grok-4.7 high (35)` | `grok grok-4.7 high (35)` | `grok grok-4.7 high (35)` | `grok grok-4.7 high (35)` | `grok grok-4.7 high (35)` | `grok grok-4.7 high (35)` |

---

## 四、草案四項待決問題的套用方式（依保守預設裁定）

1. **`coding-overflow` 車道**：維持原本計分列順序（`grok-4.7 high` → `gemini-4-argon-high` → `gemini-4-hydrogen-high` → `gemini-3.8-flash-high` → `off`），只移除沒有 AA 分數的 `kimi`、`qwen`、`opencode` 三列，不另造新的價值排序規則。
2. **六條價值車道的備援尾段**：前段完整放入 `scripts/aa_rebaseline.py value` 算出的候選鏈；其餘原本就在該車道裡的計分備援列，全部保留原本的相對順序接在後面（不刪任何計分列、也不把未計分或衍生分數列塞進車道）。
3. **固定候選鏈與個別天花板最佳解的分歧（`bulk-mechanical`、`ui-draft`、`long-context`）**：因為一條車道只有一條固定順序鏈，少數中間天花板會先碰到排在前面且分數合格的高階列（例如 `bulk-mechanical` 在天花板 `52～59` 鏈上給 `gpt-6.1-sol xhigh`，單點規則偏好更便宜的 `high` 或 `medium`；`ui-draft` 在 `50～56`、`long-context` 在 `30` 亦有類似分歧）。依裁定完全照 `aa_rebaseline.py value` 輸出直套，不做人工微調。
4. **`fast-agentic` 車道**：因 AA v5.0.1 未公布首字延遲（`first answer token (s)`），`value` 延遲門檻暫停計選，維持現有順序不動。

---

## 五、資料來源與檔案雜湊（SHA-256）

- 評分政策檔 `config/aa-model-policy.json`（快照 `aa-v5.0.1-2026-11-13-v2`，75 筆實測 + 1 筆代理 + 22 筆衍生上限）：`fb5d05954fb69787be1c3b7f81bc44894adbfb72d67cc54636e34ff4bc7a9828`
  - 與草案工作樹（`aa-v5.0.1-2026-11-13-v1`，`a95b811703c654115014122e8651578b70915d26736cfa754c3e0b2908bd8076`）相比，本工作樹多了 23 筆帶 `score_source` 的明確指定列；因 `aa_rebaseline.py` 的 `value_pool()` 明確排除 `score_source` 列，兩邊跑出的 `value` 候選鏈與分歧提示逐字完全一致。
- AA 擷取檔 `docs/reports/aa-v5.0.1-extract-2026-11-13.json`：`6544aa0e3ae5ab5b73aeeac38a34e22290a551bf09615aea654f3bdbe73fc475`
- 計算腳本 `scripts/aa_rebaseline.py`：`5e46cf9e2f20662a810d120aed937dbca3d975692fb7dd5ad6ef211d5667e6ca`
- `routing.yaml` 改前 SHA-256：`665fa0fd06960af83a31b3053c381b01e7825114ad6cc9f06ce9e11ae1e300bc`
- `routing.yaml` 改後 SHA-256：`f9f5be46492bfadf99e3ff1b62aa6bc04a99b93de6a980f6d3625bf0a968da36`
