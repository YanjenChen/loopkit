# loopkit

loopkit 是一個 Claude Code plugin，讓 `/goal` 或 `/loop` 在任何 repo 上反覆優化程式碼：每一輪分析目前的程式碼和演化歷史、提出一個改法、實作、用你的評分腳本評分，只有真的變好的版本才會留在前緣上。

它為長時間、無人值守的執行而設計，例如整夜優化一個 GPU placer（C++/CUDA/LibTorch/Python，CMake），也可以用在程式作業等任何有評分方式的專案。

- **固定格式的迭代**：每一輪由 `/loopkit:iter` 執行，開頭和結尾由框架固定，中段的 workflow 可以是單一 agent，或多個 analyst 加上一個 decider。
- **多目標**：支援多個優化目標，以容忍度判斷好壞，維護 Pareto 前緣。
- **你可以同時開發**：run 在獨立的 worktree 和資料目錄裡進行，不動你的工作目錄。你的 commit 也可以送進來評估，再決定要不要加入演化。
- **評分完整性**：評分腳本和 benchmark 在建立 run 時固定成快照；範圍檢查、權限規則、hook 和竄改偵測防止 agent 改到不該改的東西。
- **ledger 和 monitor**：所有結果寫進一份 append-only、有 hash chain 的 ledger；monitor 是一個 Claude artifact，顯示前緣、evolve tree 和每個候選的細節。

## 需求

- Claude Code 2.1.91 以上（plugin 的 `bin/` 會加進 Bash 的 PATH）
- git 2.31 以上、Python 3.8 以上，只用標準函式庫
- Linux 或 WSL；不需要 Docker

## 安裝

在 Claude Code（例如 VS Code extension）執行 `/plugins`，在 Marketplaces 分頁加入 marketplace，再以 user scope 安裝 `loopkit`：

```
/plugin marketplace add YanjenChen/loopkit
/plugin install loopkit@loopkit
```

- **開發 loopkit 本身**：改為加入本機路徑（這個 repo 的根目錄）：`/plugin marketplace add /path/to/loopkit`。安裝時，Claude Code 會把 plugin 複製到 `~/.claude/plugins/cache/`，所以修改 `claude-plugin/` 之後要更新：

  ```
  claude plugin marketplace update loopkit
  claude plugin update loopkit@loopkit      # project scope 安裝的話，加 --scope project
  ```

  然後重開 session。
- **從 GitHub 安裝的**：push 之後執行上面同樣的兩行指令。
- **scope**：建議用 user scope。用 project scope 也可以，run 的 worktree 會自行啟用 loopkit。

run 的資料放在 `~/loopkit-runs/<repo>/<run>/`（可以用環境變數 `LOOPKIT_DATA_DIR` 改到別的地方），不在 plugin 的資料目錄裡，所以更新、重裝或解除安裝 loopkit 都不會刪掉 run。要刪除 run，用 `loopkit run remove <name> --yes`。

## 一次 run 的流程

1. **設定**：在你的 repo 執行 `/loopkit:init <要優化什麼>`，例如 `/loopkit:init 優化hpwl與runtime`。init 會分析 repo、盤問你的描述、產生 loopkit 設定（`.loopkit/config.json`）和評分腳本、試跑、發佈 monitor 讓你確認，最後建立 run 並為 baseline（c000）評分。
2. **啟動**：用 init 印出的指令，在新的 VS Code 視窗開啟 run 的 agent worktree，用 auto mode 啟動 Claude Code，貼上 init 印出的 prompt 之一：

   ```
   /goal 重複執行 /loopkit:iter（停止條件：最多 20 輪、連續 5 輪沒有進步，或 hpwl 低於 1.0e6，由 loopkit 判斷），直到輸出出現 LOOPKIT-STOP。單一輪 REVERTED 或 FAILED 不代表目標不可能達成。
   /loop /loopkit:iter（停止條件：最多 50 輪或連續 8 輪沒有進步，由 loopkit 判斷；輸出出現 LOOPKIT-STOP 時停止 loop）
   ```

   停止條件由 loopkit 判斷：輪數上限、plateau（連續 N 輪沒有 KEPT）、優化目標的門檻。達成時框架印出 `LOOPKIT-STOP`，`/goal` 和 `/loop` 都會停下來。之後想再跑一批，就用不同的 prompt 再下一次。

   想提早停止時，在你自己的 session 執行 `/loopkit:stop`：run 會在幾秒內印出 `LOOPKIT-STOP` 並結束。也可以在 run 的視窗按 Esc 中斷目前這一輪，再輸入 `/goal clear`；agent 工作中直接輸入的 `/goal clear` 只會被當成一般訊息。
3. **同時開發**：你繼續在自己的工作目錄開發。想讓某個 commit 被評估，在你自己的 session 執行 `/loopkit:request-eval <commit>`；它會在下一輪開頭被評分，成為 `hNNN`，預設只觀察。要讓它加入演化，執行 `/loopkit:promote <hNNN>`。
4. **觀察**：從 monitor 看進度，或執行 `/loopkit:status`。
5. **取回結果**：`/loopkit:adopt <id>` 在你的 repo 建立一個指向該候選的 branch，由你自行檢視和合併。

要改優化目標或評分方式時，重新執行 `/loopkit:init`，它會建立新的 run；舊 run 的 ledger 和 ref 都會保留。

## 一輪 iter

| 階段 | 內容 |
|---|---|
| 開頭（固定） | 完整性檢查 → 登錄並檢查停止條件 → 處理你送來的請求 → 印出 evolve 狀態 → 選 parent（一個，或兩個做合併） |
| 中段（設定決定） | single：agent 自己分析並選一個改法。multi：多個唯讀 analyst 並行提案，decider 盲評選出一個，選用的 critic 再挑戰一次 |
| 結尾（固定） | 實作 → precheck（快速編譯、自我修復）→ 評分 → 記錄（一行 ITER）→ 更新 monitor |

每一輪結束時會印出一行 ITER，例如：

```
ITER 7/20 | c012<-c009 | hpwl 1.0231e6 (-0.80% better) | runtime 41.200s (+1.2% same) | KEPT | front=3
```

## 指令

在你自己的 session 使用的 slash command：

| 指令 | 作用 |
|---|---|
| `/loopkit:init <描述>` | 設定並建立 run |
| `/loopkit:request-eval <commit>` | 把你的 commit 送去評估 |
| `/loopkit:promote <hNNN>` | 讓人工候選加入演化 |
| `/loopkit:adopt <id>` | 在你的 repo 建立指向候選的 branch |
| `/loopkit:status` | run 的狀態 |
| `/loopkit:stop` | 提早結束目前的批次 |
| `/loopkit:report` | 收集除錯報告並診斷；報告的 `.tar.gz` 可以直接附到 GitHub issue |

`/loopkit:iter` 只在 run session 裡由 `/goal` 或 `/loop` 執行。

框架指令是一支 Python 程式 `loopkit`，負責所有 git 和 ledger 的寫入。常用的有 `loopkit summary`、`loopkit show <id> --log`、`loopkit lineage <id>`、`loopkit diff <a> <b>`、`loopkit status`、`loopkit run list`、`loopkit check`。完整清單見 `loopkit --help`，或 [skill 的說明](claude-plugin/skills/loopkit/SKILL.md)。

在 Claude Code 裡，`loopkit` 已經在 PATH 上。要在一般終端機使用，先執行一次 `python3 <plugin 路徑>/bin/loopkit shim`，它會在 `~/.local/bin/loopkit` 建立捷徑。

## 評分腳本

評分腳本由 init 依你的描述產生，run 建立時固定成快照。框架在乾淨的 eval worktree 裡執行它，只讀它寫出的結果 JSON：

```json
{"schema": 1, "status": "ok",
 "objectives": {"hpwl": 1.0231e6, "runtime": 41.2},
 "constraints": {"legal": {"pass": true, "value": 0, "detail": "0 overlaps"}},
 "extra": {"gpu_mem_mb": 5120}}
```

build、重複量測、彙整都是腳本的事。完整規格和隔離檢查清單見 [score-contract.md](claude-plugin/skills/loopkit/references/score-contract.md)，設定檔的每個欄位見 [config.md](claude-plugin/skills/loopkit/references/config.md)。

## 安全與評分完整性

run session 的 agent 可以執行任意指令，而且環境沒有 Docker，所以 loopkit 的目標是擋下意外和常見的誤用，並偵測竄改，不是保證絕對安全：

1. **隔離**：run 在獨立的 agent worktree 進行；評分在另一個 eval worktree，用 run 目錄裡的快照。
2. **範圍檢查**：只能改設定裡的 scope；`.loopkit/`、`.claude/`、`.gitignore`、`.gitattributes`、`.gitmodules`、submodule 和指到 tree 外的 symlink 一律不行。
3. **權限規則**：run 的 worktree 有禁止編輯你的 repo、eval worktree、run 資料和 `.git` 的規則。
4. **hook**：在 run session 中，git 只能讀，寫入由框架負責；會動到受保護位置的指令、無法解析的指令、背景執行和只給你用的指令都會被擋下。hook 只在 run session 中作用，你平常的 session 不受影響。
5. **竄改偵測**：每一輪開頭和每次評分前，檢查評分資產快照、ledger 的 hash chain、候選的 ref、佇列，以及可能在 checkout 時執行程式碼的 git 設定。有問題就停止 run。

## 開發

```
python3 -m unittest discover -s tests -p 'test_loopkit_*.py'   # 單元測試
bash tests/test-run-e2e.sh                                      # 用玩具 repo 跑完整流程
bash tests/test-hooks.sh                                        # hooks
```

見 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 授權與致謝

MIT，見 [LICENSE](LICENSE)。

loopkit fork 自 Udit Goenka 的 [uditgoenka/autoresearch](https://github.com/uditgoenka/autoresearch)（MIT），其構想來自 [Andrej Karpathy 的 autoresearch](https://github.com/karpathy/autoresearch)。
