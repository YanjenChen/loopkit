# 參與開發 loopkit

## 開發環境

1. clone 這個 repo。
2. 在 Claude Code 執行 `/plugins`，在 Marketplaces 分頁加入本機路徑（repo 根目錄），再以 user scope 安裝 `loopkit`。
3. 安裝時 plugin 會被複製到 `~/.claude/plugins/cache/`。修改 `claude-plugin/` 並 commit 之後，把 `plugin.json` 和 `marketplace.json` 的版本號加一，再執行 `claude plugin marketplace update loopkit` 和 `claude plugin update loopkit@loopkit`，然後重開 session。

需要 git 2.31 以上和 Python 3.8 以上。框架和 hooks 都只用 Python 標準函式庫，請維持這一點，也請維持和 3.8 相容的語法。

## 目錄結構

```
.claude-plugin/marketplace.json   marketplace 定義
claude-plugin/
  .claude-plugin/plugin.json      plugin 定義
  bin/loopkit                     CLI 入口（Claude Code 會把 bin/ 加進 Bash 的 PATH）
  loopkit/                        框架：ledger、git 操作、範圍、前緣、評分、報表、monitor
  commands/                       /loopkit:init、iter、request-eval、promote、adopt、status
  agents/                         analyst、analyst-web、decider、critic（loopkit:<name>）
  skills/loopkit/                 參考說明：設定、評分腳本規格、分析範本、monitor
  monitor/monitor.html            monitor 頁面範本
  hooks/                          只在 run session 中作用的 hooks
tests/
  test_loopkit_core.py            純邏輯模組的單元測試
  test_loopkit_git.py             和 git 有關的模組（快照、範圍、資產、佇列、完整性）
  test-run-e2e.sh                 用玩具 repo 跑完整流程
  test-hooks.sh                   hooks
```

## 設計原則

- **ledger 是唯一的資料來源。** 任何狀態都要能從 ledger 推導；`work/` 只放進行中那一輪的暫存狀態。
- **只有框架寫 git 和 ledger。** agent 只改檔案；新的寫入操作放進 `loopkit/`，不要讓指令檔教 agent 直接下 git 指令。
- **hook 只在 run session 中作用。** plugin 以 user scope 安裝，hook 會跑在使用者所有的 session 裡；判斷不是 run session 時必須直接放行、不輸出任何東西。
- **給 agent 的輸出要可讀、可 grep。** ITER、HUMAN、RESULT、PENDING、`LOOPKIT-STOP` 等行的格式是介面，改動時要同步更新指令檔和測試。

## 測試

沒有 CI，送出修改前請在本機跑完三組測試：

```
python3 -m unittest discover -s tests -p 'test_loopkit_*.py'
bash tests/test-run-e2e.sh
bash tests/test-hooks.sh
```

指令檔、agents 和 monitor 的行為只能在真的 Claude Code session 裡驗證：用本機 marketplace 安裝後，在一個玩具 repo 執行 `/loopkit:init`，再開 agent worktree 跑幾輪短的 `/goal` 和 `/loop`。

## Commit 訊息

使用 [Conventional Commits](https://www.conventionalcommits.org/)：`feat:`、`fix:`、`docs:`、`refactor:`、`chore:`、`test:`。
