#!/usr/bin/env bash
# omnilane i18n — interactive-surface strings for install.sh / configure.sh.
# Language picked from OMNILANE_LANG, else LC_ALL/LANG, else macOS AppleLocale.
# bash 3.2 compatible (no associative arrays). LLM-facing text stays English.

detect_lang() {
  local l="${LC_ALL:-${LANG:-}}"
  if [[ -z "$l" ]] && command -v defaults >/dev/null 2>&1; then
    l="$(defaults read -g AppleLocale 2>/dev/null || true)"
  fi
  case "$l" in
    zh_TW*|zh-TW*|zh_Hant*|zh-Hant*|zh_HK*|zh-HK*) echo "zh-TW" ;;
    zh*) echo "zh-CN" ;;
    ja*) echo "ja" ;;
    ko*) echo "ko" ;;
    *)   echo "en" ;;
  esac
}

OMNILANE_LANG="${OMNILANE_LANG:-$(detect_lang)}"

msg() { # key -> localized string ({} = placeholder, see msgf)
  local L="$OMNILANE_LANG"
  case "$1" in
    found)
      case "$L" in zh-TW) echo "已偵測到";; zh-CN) echo "已检测到";; ja) echo "検出しました";; ko) echo "감지됨";; *) echo "found";; esac ;;
    run_manually)
      case "$L" in zh-TW) echo "已偵測到——請自行執行:";; zh-CN) echo "已检测到——请自行执行:";; ja) echo "検出——次を手動で実行:";; ko) echo "감지됨——직접 실행하세요:";; *) echo "found — run this yourself:";; esac ;;
    plugin_hint)
      case "$L" in zh-TW) echo "(選配:把本 repo 裝成 Claude Code 外掛,可用 /route 指令)";; zh-CN) echo "(可选:把本仓库装成 Claude Code 插件,获得 /route 命令)";; ja) echo "(任意:このリポジトリを Claude Code プラグインとして追加すると /route コマンドが使えます)";; ko) echo "(선택: 이 저장소를 Claude Code 플러그인으로 추가하면 /route 명령 사용 가능)";; *) echo "(optional: add this repo as a Claude Code plugin for /route commands)";; esac ;;
    linked)
      case "$L" in zh-TW) echo "已連結";; zh-CN) echo "已链接";; ja) echo "リンクしました";; ko) echo "링크됨";; *) echo "linked";; esac ;;
    removed)
      case "$L" in zh-TW) echo "已移除";; zh-CN) echo "已移除";; ja) echo "削除しました";; ko) echo "제거됨";; *) echo "removed";; esac ;;
    skip_exists)
      case "$L" in zh-TW) echo "跳過 {}(已存在且不是 symlink——請手動處理)";; zh-CN) echo "跳过 {}(已存在且不是 symlink——请手动处理)";; ja) echo "{} をスキップ(symlink 以外の実体があります——手動で解決してください)";; ko) echo "{} 건너뜀(symlink 가 아닌 항목 존재——수동으로 해결하세요)";; *) echo "skip {} (exists and is not a symlink — resolve manually)";; esac ;;
    path_hint)
      case "$L" in zh-TW) echo "(請確認 ~/.local/bin 在 PATH 上)";; zh-CN) echo "(请确认 ~/.local/bin 在 PATH 上)";; ja) echo "(~/.local/bin が PATH にあるか確認してください)";; ko) echo "(~/.local/bin 이 PATH 에 있는지 확인하세요)";; *) echo "(make sure ~/.local/bin is on your PATH)";; esac ;;
    no_cli)
      case "$L" in zh-TW) echo "PATH 上找不到支援的 CLI(claude/codex/grok/agy)";; zh-CN) echo "PATH 上找不到支持的 CLI(claude/codex/grok/agy)";; ja) echo "対応 CLI(claude/codex/grok/agy)が PATH に見つかりません";; ko) echo "PATH 에서 지원 CLI(claude/codex/grok/agy)를 찾을 수 없습니다";; *) echo "no supported CLI (claude/codex/grok/agy) on PATH";; esac ;;
    overrides_header)
      case "$L" in zh-TW) echo "本機覆寫檔都在 ~/.omnilane/(永不進版控):";; zh-CN) echo "本机覆盖文件都在 ~/.omnilane/(永不进版本控制):";; ja) echo "マシン固有の設定は ~/.omnilane/ に置かれます(コミットされません):";; ko) echo "머신별 설정은 ~/.omnilane/ 에 있습니다(커밋되지 않음):";; *) echo "Per-machine overrides live in ~/.omnilane/ (never committed):";; esac ;;
    overrides_routing)
      case "$L" in zh-TW) echo "routing.local.yaml — 覆寫任何通道(見 routing.local.yaml.example)";; zh-CN) echo "routing.local.yaml — 覆盖任意通道(见 routing.local.yaml.example)";; ja) echo "routing.local.yaml — 任意のレーンを上書き(routing.local.yaml.example 参照)";; ko) echo "routing.local.yaml — 레인 오버라이드(routing.local.yaml.example 참고)";; *) echo "routing.local.yaml — override any lane (see routing.local.yaml.example)";; esac ;;
    overrides_local)
      case "$L" in zh-TW) echo "local.sh           — 執行器用的執行檔路徑/環境變數(見 local.sh.example)";; zh-CN) echo "local.sh           — 执行器用的可执行文件路径/环境变量(见 local.sh.example)";; ja) echo "local.sh           — ランナー用のバイナリ/環境変数(local.sh.example 参照)";; ko) echo "local.sh           — 러너용 바이너리/환경 변수(local.sh.example 참고)";; *) echo "local.sh           — binaries/env for the runners (see local.sh.example)";; esac ;;
    effective)
      case "$L" in zh-TW) echo "這台機器的生效路由表(候選鏈已解析):";; zh-CN) echo "这台机器的生效路由表(候选链已解析):";; ja) echo "このマシンの実効ルーティング(フォールバック解決済み):";; ko) echo "이 머신의 실효 라우팅(폴백 해석 완료):";; *) echo "Effective routing on this machine (fallback chains resolved):";; esac ;;
    customize_prompt)
      case "$L" in zh-TW) echo "現在就自訂通道→模型配置嗎?[y/N] ";; zh-CN) echo "现在就自定义通道→模型配置吗?[y/N] ";; ja) echo "レーン→モデル割当を今カスタマイズしますか? [y/N] ";; ko) echo "지금 레인→모델 배정을 사용자화할까요? [y/N] ";; *) echo "Customize lane -> model assignments now? [y/N] ";; esac ;;
    hook_section)
      case "$L" in zh-TW) echo "選配:各 CLI 常駐路由提示(在指令檔尾端加一段有標記的區塊;--uninstall 可移除):";; zh-CN) echo "可选:各 CLI 常驻路由提示(在指令文件末尾加一段带标记的区块;--uninstall 可移除):";; ja) echo "任意:CLI ごとの常駐ルーティングリマインダー(指示ファイル末尾にマーカー付きブロックを追記;--uninstall で削除可):";; ko) echo "선택: CLI 별 상시 라우팅 리마인더(지침 파일 끝에 마커 블록 추가; --uninstall 로 제거 가능):";; *) echo "Optional: per-CLI persistent routing reminder (a marked block appended to each CLI's instruction file; reversed by --uninstall):";; esac ;;
    hook_prompt)
      case "$L" in zh-TW) echo "要把常駐路由提示寫進 {} 嗎?[y/N] ";; zh-CN) echo "要把常驻路由提示写进 {} 吗?[y/N] ";; ja) echo "{} に常駐リマインダーを書き込みますか? [y/N] ";; ko) echo "{} 에 상시 리마인더를 설치할까요? [y/N] ";; *) echo "Install the routing reminder into {}? [y/N] ";; esac ;;
    hook_written)
      case "$L" in zh-TW) echo "路由提示已寫入";; zh-CN) echo "路由提示已写入";; ja) echo "リマインダーを書き込みました:";; ko) echo "리마인더 기록됨:";; *) echo "routing reminder written to";; esac ;;
    hook_removed)
      case "$L" in zh-TW) echo "已移除路由提示:";; zh-CN) echo "已移除路由提示:";; ja) echo "リマインダーを削除しました:";; ko) echo "리마인더 제거됨:";; *) echo "routing reminder removed from";; esac ;;
    cfg_title)
      case "$L" in zh-TW) echo "omnilane 通道設定——目前生效路由:";; zh-CN) echo "omnilane 通道设置——当前生效路由:";; ja) echo "omnilane レーン設定 — 現在の実効ルーティング:";; ko) echo "omnilane 레인 설정 — 현재 실효 라우팅:";; *) echo "omnilane lane configurator — current effective routing:";; esac ;;
    cfg_pick_lane)
      case "$L" in zh-TW) echo "選要覆寫的通道編號,或按 Enter 結束:";; zh-CN) echo "选要覆盖的通道编号,或按 Enter 结束:";; ja) echo "上書きするレーン番号を選択、Enter で終了:";; ko) echo "오버라이드할 레인 번호 선택, Enter 로 종료:";; *) echo "Pick a lane number to override, or press Enter to finish:";; esac ;;
    cfg_pick_range)
      case "$L" in zh-TW) echo "輸入 1-{} 或 Enter";; zh-CN) echo "输入 1-{} 或 Enter";; ja) echo "1-{} か Enter を入力";; ko) echo "1-{} 또는 Enter";; *) echo "pick 1-{} or Enter";; esac ;;
    cfg_vendor_for)
      case "$L" in zh-TW) echo "'{}' 要用哪家:";; zh-CN) echo "'{}' 要用哪家:";; ja) echo "'{}' のベンダー:";; ko) echo "'{}' 의 벤더:";; *) echo "vendor for '{}':";; esac ;;
    cfg_model)
      case "$L" in zh-TW) echo "模型:";; zh-CN) echo "模型:";; ja) echo "モデル:";; ko) echo "모델:";; *) echo "model:";; esac ;;
    cfg_effort)
      case "$L" in zh-TW) echo "推理檔位:";; zh-CN) echo "推理档位:";; ja) echo "推論エフォート:";; ko) echo "추론 강도:";; *) echo "effort:";; esac ;;
    cfg_voters_count)
      case "$L" in zh-TW) echo "要幾個評審?(每評審每輪燒一次額度)";; zh-CN) echo "要几个评审?(每评审每轮烧一次额度)";; ja) echo "投票者は何人?(1 人につき 1 ラウンド 1 コール)";; ko) echo "투표자 수는? (1명당 라운드마다 1콜)";; *) echo "how many voters? (each costs one call per round)";; esac ;;
    cfg_voters_range)
      case "$L" in zh-TW) echo "評審數須為 1-4";; zh-CN) echo "评审数须为 1-4";; ja) echo "投票者は 1-4 人";; ko) echo "투표자는 1-4명";; *) echo "voters must be 1-4";; esac ;;
    cfg_voter_n)
      case "$L" in zh-TW) echo "評審 {}:";; zh-CN) echo "评审 {}:";; ja) echo "投票者 {}:";; ko) echo "투표자 {}:";; *) echo "voter {}:";; esac ;;
    cfg_rounds)
      case "$L" in zh-TW) echo "輪數(2 = 評審互駁):";; zh-CN) echo "轮数(2 = 评审互驳):";; ja) echo "ラウンド数(2 = 相互反論):";; ko) echo "라운드 수(2 = 상호 반박):";; *) echo "rounds (2 = voters rebut each other):";; esac ;;
    cfg_exec_path)
      case "$L" in zh-TW) echo "腳本路徑(參數:MODE WORKDIR EFFORT PROMPT_FILE OUTPUT_FILE): ";; zh-CN) echo "脚本路径(参数:MODE WORKDIR EFFORT PROMPT_FILE OUTPUT_FILE): ";; ja) echo "スクリプトパス(引数: MODE WORKDIR EFFORT PROMPT_FILE OUTPUT_FILE): ";; ko) echo "스크립트 경로(인자: MODE WORKDIR EFFORT PROMPT_FILE OUTPUT_FILE): ";; *) echo "script path (gets MODE WORKDIR EFFORT PROMPT_FILE OUTPUT_FILE): ";; esac ;;
    cfg_empty_path)
      case "$L" in zh-TW) echo "路徑空白,略過";; zh-CN) echo "路径空白,跳过";; ja) echo "パスが空のためスキップ";; ko) echo "경로가 비어 있어 건너뜀";; *) echo "empty path, skipped";; esac ;;
    cfg_custom)
      case "$L" in zh-TW) echo "  c) 自訂(自己輸入)";; zh-CN) echo "  c) 自定义(自己输入)";; ja) echo "  c) カスタム(自由入力)";; ko) echo "  c) 직접 입력";; *) echo "  c) custom (type your own)";; esac ;;
    cfg_custom_value)
      case "$L" in zh-TW) echo "自訂值: ";; zh-CN) echo "自定义值: ";; ja) echo "カスタム値: ";; ko) echo "직접 입력값: ";; *) echo "custom value: ";; esac ;;
    cfg_pick_or_c)
      case "$L" in zh-TW) echo "輸入 1-{} 或 c";; zh-CN) echo "输入 1-{} 或 c";; ja) echo "1-{} か c を入力";; ko) echo "1-{} 또는 c";; *) echo "pick 1-{} or c";; esac ;;
    cfg_aborted)
      case "$L" in zh-TW) echo "已中止";; zh-CN) echo "已中止";; ja) echo "中止しました";; ko) echo "중단됨";; *) echo "aborted";; esac ;;
    cfg_no_changes)
      case "$L" in zh-TW) echo "沒有變更。";; zh-CN) echo "没有变更。";; ja) echo "変更なし。";; ko) echo "변경 없음.";; *) echo "no changes.";; esac ;;
    cfg_wrote)
      case "$L" in zh-TW) echo "已寫入 {} ——目前生效路由:";; zh-CN) echo "已写入 {} ——当前生效路由:";; ja) echo "{} に書き込みました — 現在の実効ルーティング:";; ko) echo "{} 에 기록됨 — 현재 실효 라우팅:";; *) echo "wrote {} — effective routing now:";; esac ;;
    *) echo "$1" ;;
  esac
}

msgf() { # key, value -> localized string with {} replaced
  local m; m="$(msg "$1")"
  printf '%s' "${m/\{\}/$2}"
}
