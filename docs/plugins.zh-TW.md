# 撰寫面板

不適合放進 KudioStudio 本身的工具，可以放在你自己的套件裡，照樣出現在工具列上，排在內建工具下方的
分隔線之後。它的做法與這裡每個面板相同，分成兩半：一個 Python 類別，提供頁面呼叫的指令；
一個頁面模組，畫出控制項並呼叫那些指令。兩者用的都是內建面板用的那些元件，所以來自其他套件的面板，
看起來、用起來都和我寫的一樣。

這一頁從頭到尾做一個小面板：**分段 RMS**，逐個視窗量測選取範圍（或整段音訊）的音量。

## 視窗怎麼找到面板 {#how-the-window-finds-a-panel}

套件在 `kudiostudio.panels` 這個 entry point 群組下登記它的面板，寫法是 `name = "module:Class"`。
視窗啟動時，依這些名稱的順序逐一匯入，先登記它的圖示與翻譯，再在內建面板之後以 `Class(studio)`
建立它。外掛面板排在工具列最後，順序相同。

以下情況，面板會被略過，視窗照常開啟：

- 它的模組匯入失敗；
- entry point 指向的不是 `WebPanel` 的子類別；
- 它的 `icon_svg` 無法解析成 SVG、沒有配上 `icon` 名稱，或用了內建圖示的名稱；
- 它的建構子拋出例外；
- 它沒有 `id`，或它的 `id` 已經在工具列上：另一個外掛的，或內建工具的，包括 `inspect`；
- 它的 `id` 是 `markers`，這是視窗本身標記指令用的名稱。

視窗開啟時，狀態列會以警示色逐一指出被略過的外掛與錯誤：

```text
外掛 rms 未載入 — ModuleNotFoundError: No module named 'rms_panel'
```

「說明 → 關於」會列出成功載入的外掛。`kudiostudio --no-plugins`，或把環境變數
`KUDIOSTUDIO_NO_PLUGINS` 設成任何非空字串，就完全不載入外掛；懷疑是某個外掛出問題時，先這樣試。

## 範例 {#the-example}

```text
rms-panel/
├── pyproject.toml
└── src/rms_panel/
    ├── __init__.py       （空的）
    ├── panel.py          Python 這一半
    └── static/
        └── rms.js        頁面這一半
```

### pyproject.toml {#pyprojecttoml}

```toml
[build-system]
requires = ["setuptools>=77"]
build-backend = "setuptools.build_meta"

[project]
name = "rms-panel"
version = "0.1.0"
requires-python = ">=3.9"
dependencies = ["kudiostudio>=2.0", "numpy"]

[project.entry-points."kudiostudio.panels"]
rms = "rms_panel.panel:RmsPanel"

# the page module is not Python, so the wheel only carries it if asked to
[tool.setuptools.package-data]
rms_panel = ["static/*.js"]
```

`package-data` 那一行比看起來重要。少了它，這個套件打包出來的 wheel 只有 `panel.py`，沒有
`rms.js`：類別照樣載入，工具列上也有這個工具，但選了之後，控制項該出現的地方只會顯示面板名稱與
網頁元件的匯入錯誤。

### Python 這一半 {#the-python-half}

```python
from pathlib import Path

import numpy as np

from kudiostudio.web.commands import command
from kudiostudio.web.panels.base import WebPanel


def rms_per_window(y, sr, seconds, offset=0.0):
    """[start, level] for each window of *seconds*, as the table shows them."""
    step = max(1, int(round(seconds * sr)))
    rows = []
    for start in range(0, len(y), step):
        part = np.asarray(y[start:start + step], dtype=np.float64)
        rms = float(np.sqrt(np.mean(part ** 2)))
        level = f"{20 * np.log10(rms):+.1f} dBFS" if rms > 0 else "digital silence"
        rows.append([f"{offset + start / sr:.2f} s", level])
    return rows


class RmsPanel(WebPanel):
    id = "rms"
    title = "Window RMS"
    icon = "rms"
    icon_svg = ('<path d="M4 18v-6M8 18V7M12 18v-4M16 18V9M20 18v-6"/>'
                '<path d="M3 10.5h18" stroke-dasharray="1.5 2.5"/>')
    static_dir = Path(__file__).parent / "static"
    script = "rms.js"
    messages = {"zh_TW": {
        "Window RMS": "分段 RMS",
        "window": "視窗長度",
        "Measure": "量測",
        "Start": "起點",
        "Level": "音量",
        "digital silence": "數位靜音",
        "Measuring RMS …": "正在量測 RMS…",
        "Measured {} window(s).": "量測了 {} 個視窗。",
        "The audio changed while it was measured; measure again.":
            "量測期間音訊改變了，請重新量測。",
    }}

    def __init__(self, studio):
        super().__init__(studio)
        self._rows = []

    @command
    def describe(self):
        return {**super().describe(), "rows": self._rows}

    @command
    def measure(self, seconds=1.0):
        if not self.require_audio():
            return {"rows": self._rows}
        y, sr = self.session.selected_audio(), self.session.sr
        selection = self.session.selection()
        ran_on = self.session.y

        def done(rows):
            # the job ran off the lock; the clip may have been edited since
            if self.session.y is not ran_on:
                self.report("The audio changed while it was measured; measure again.")
                return
            self._rows = rows
            self.publish(rows=rows)
            self.report(f"Measured {len(rows)} window(s).")

        self.start_job("Measuring RMS …", rms_per_window, y, sr, float(seconds),
                       selection[0] if selection else 0.0, on_done=done)
        return {"rows": self._rows}

    def on_session_changed(self):
        self._rows = []
        self.publish(rows=[])
```

視窗在建立任何東西之前，會先讀這些類別屬性：

| 屬性 | 意義 |
|---|---|
| `id` | 面板的代號：頁面呼叫它的指令時，用的名稱是 `rms.describe` 與 `rms.measure`。請取一個自己的；內建工具的 id，包括 `inspect`，都已經被用掉了，`markers` 也不行，視窗本身的指令用了它 |
| `title` | 工具列上的名稱，用英文寫，由頁面翻譯 |
| `icon` | 工具列圖示的名稱 |
| `icon_svg` | 圖示本身，見[下文](#icon-and-translations) |
| `static_dir` | 放頁面模組的資料夾。視窗以 `/plugins/<id>/` 提供它，上層的任何東西都拿不到 |
| `script` | 頁面模組，相對於 `static_dir` |
| `messages` | 它的翻譯，見[下文](#icon-and-translations) |

#### 指令 {#commands}

標上 `@command` 的方法，頁面可以用 `<id>.<name>` 呼叫，其他方法都不行。`@command(name="...")`
則以另一個名稱公開。參數以 JSON 傳進來，指令回傳的值也以 JSON 送回：dict、list、字串與數字，
numpy 純量、numpy 陣列與 `Path` 會在途中轉換。指令拋出例外時，錯誤會以警示色顯示在狀態列，
視窗照常運作。

`describe` 是每個面板都有的指令。基底類別回傳 `id` 與面板是否忙碌，面板再加上頁面畫出自己所需的
東西，這裡是上一次量測的結果。

面板從 `WebPanel` 得到的輔助功能：

| 輔助功能 | 作用 |
|---|---|
| `self.session` | 目前的音訊：`y`、`sr`、以秒為單位的 `selection()`（沒有選取時是 `None`），以及 `selected_audio()`，即選取範圍或整段音訊 |
| `self.report(text)` | 狀態列上的一行，途中會翻譯 |
| `self.require_audio()` | 沒有開啟任何音訊時回傳 False，並在狀態列顯示「請先載入音訊檔。」 |
| `self.publish(**data)` | 送給這個面板的頁面模組的事件，由它的 `event` hook 接收 |
| `self.start_job(label, fn, *args, on_done=, on_failed=, **kwargs)` | 在獨立的執行緒上執行 `fn`；這個面板已有工作在執行時回傳 False |
| `self.busy` | 這個面板是否有工作在執行 |

#### 背景工作 {#jobs}

任何超過一瞬間的事都應該放進背景工作，否則視窗在它做完之前都不會回應。`fn` 在視窗的鎖之外執行，
所以不能碰工作階段：把它需要的音訊交給它，就像 `measure` 把 `y` 交出去那樣。`on_done` 收到結果，
`on_failed` 收到一行錯誤訊息，兩者都在鎖之內執行，在那裡讀取與修改工作階段都是安全的。
沒有 `on_failed` 時，錯誤會顯示在狀態列。

工作執行時，狀態列最左邊的小圓點會變成琥珀色，工作的標籤則出現在狀態列右端、版本號碼前面。
每個面板一次只有一個工作。
工作無法中途停止，因為 kudio 的呼叫沒有可以停下的地方，所以結束程式時會等它做完。
耗時較長的工作，可以用 `self.studio.jobs.progress(self.id, text)` 把目前進度送到頁面模組的
`progress` hook。

工作執行期間，音訊可能被改動。上面的 `done` 會比較它量測的陣列與工作階段目前持有的陣列；
編輯是替換整個陣列，而不是寫進原本的陣列，所以物件不同就表示音訊不同。

#### Hook {#hooks}

`on_session_changed()`
:   音訊改變了：一次編輯、一次復原、換了檔案。在舊音訊上量到的東西已經不能描述畫面上的音訊，
    所以範例把結果清掉。

`cancel_preview()`
:   有其他東西接手了播放。給用 `self.studio.start_preview(self.id, y, sr)` 試聽音訊的面板用，
    試聽會一直循環到這時候。

`shutdown()`
:   視窗正在關閉；面板開著的裝置要在這裡放掉。

#### 修改音訊 {#changing-the-audio}

要編輯音訊的面板，在指令或 `on_done` 裡（也就是鎖之內）呼叫 `set_audio`：

```python
self.session.set_audio(out, sr, undo_label="halve", keep_original=True,
                       code="y = y * 0.5")
```

`undo_label` 讓這一步以該名稱進入復原清單，`keep_original=True` 則保留剛開啟時的檔案，
供 A/B 比較與「還原為原始音訊」使用。`code` 是這一步的 Python 寫法，給
[匯出的腳本](editing.md#your-edits-as-a-script)用：執行時 `y` 與 `sr` 已經綁定，`np` 與 `kudio`
已經匯入，結果留在 `y`。沒有 `code` 的編輯照樣可以復原，但匯出的腳本會在那一步以 `raise` 停下，
而不是少了這一步就寫出檔案。

### 頁面這一半 {#the-page-half}

```js
// Window RMS: the level of the selection, or of the whole clip, window by window.

export async function mount(root, ctx) {
  const { ui, t } = ctx;
  const info = await ctx.call("describe");
  ctx.memory.seconds ??= 1.0;

  const box = ui.section("Window RMS");
  const form = ui.form();
  const seconds = ui.number({ value: ctx.memory.seconds, min: 0.05, max: 60, step: 0.05,
                              decimals: 2, suffix: " s",
                              onChange: (v) => { ctx.memory.seconds = v; } });
  form.row("window", seconds);
  const measure = ui.button("Measure", {
    primary: true,
    onClick: () => ctx.call("measure", [seconds.value]),
  });
  const table = ui.table(["Start", "Level"], { numeric: [1] });
  box.body.append(form.el, ui.buttons(measure));
  root.append(box.el, table);

  const show = (rows) => table.setRows(rows.map(([start, level]) => [start, t(level)]));
  show(info.rows);

  return {
    state(s) { measure.disabled = !s.has_audio || s.busy.includes(ctx.id); },
    event(e) { if (e.rows) show(e.rows); },
    busy(on) { measure.disabled = on; },
  };
}
```

模組匯出 `mount(root, ctx)`。第一次選到這個工具時，視窗會呼叫它，給它一個空的區塊來放控制項；
之後每次切換介面語言，都會再呼叫一次，用另一種語言重新畫出面板。它不匯入任何東西：需要的一切都在
`ctx` 裡；而且它是從 `/plugins/<id>/` 載入的，相對路徑的匯入只會找到你自己的資料夾。

| `ctx.` | 意義 |
|---|---|
| `call(name, args, kwargs)` | 執行這個面板的指令 `name`，結果為它的回傳值 |
| `window(name, args, kwargs)` | 執行視窗本身的指令，例如以 `[1.0, 2.5]` 呼叫 `set_selection` |
| `ui` | 控制項，見下文 |
| `t(text)` | 英文的 *text* 在視窗目前語言下的文字 |
| `memory` | 讓模組自由存放東西的物件。切換語言或切到別的工具時都會保留，關掉視窗就沒了 |
| `state()` | 視窗最新的狀態 |
| `status(text, warn)` | 從頁面本身在狀態列寫一行 |
| `view` | 蓋在圖上的一層，見下文 |
| `dialogs` | 視窗的提示框：`ask`、`askNumber`、`confirm` 與 `choosePath`，以及 `confirmReplace({ title, submit })`，也就是有東西要取代目前開啟的音訊之前，視窗會問的那個問題；`state().unsaved` 為 true 時就先問它 |
| `id` | 面板的 `id` |

`mount` 回傳它要被呼叫的 hook，以下任選：

| Hook | 傳入的內容 |
|---|---|
| `state(s)` | 視窗狀態有變化時的新狀態：`has_audio`、`sr`、`duration`、`selection`、`playing`、`busy`（有工作在執行的面板 id）、`unsaved`（目前的音訊有任何檔案都沒保存的內容）、`language` 等等 |
| `event(e)` | 這個面板的 Python 那一半所送出的東西 |
| `busy(on, label)` | 這個面板的工作開始與結束 |
| `progress(text)` | 來自 `jobs.progress` 的一行 |
| `preview(on)` | 這個面板的試聽因為其他東西接手播放而結束時，傳入 `false` |

#### 控制項 {#the-controls}

內建面板全都只用這些控制項組成，視窗看起來才會是一個整體。每個標籤與提示都以英文寫，經過 `t` 翻譯。

| `ui.` | 產生的東西 |
|---|---|
| `section(title, { tip })` | 有標題的區塊：`.el`、要填內容的 `.body`、`.setBusy(label)` |
| `form()` | 標籤與控制項的列：`.el`、`.row(label, control, { tip, wide })` |
| `number({ value, min, max, step, decimals, suffix, special, onChange })` | 數字欄。數值在最小值時，單位的位置改顯示 `special`，就像 seed 設為 −1 時顯示 `random` |
| `text({ value, placeholder, onChange })` | 一行文字 |
| `select(options, { value, translate, onChange })` | 下拉選單，選項是字串或 `[value, label]` |
| `segmented(options, { value, onChange })` | 一排按鈕、其中一個被選取，像 STFT / Mel / MFCC 那樣 |
| `check(text, { checked, onChange })` | 核取方塊；`.value` |
| `path({ kind, title, types, placeholder, onChange })` | 路徑欄加上「瀏覽…」，透過視窗自己的對話框。`kind` 是 `open`、`open_many`、`save` 或 `folder` |
| `button(text, { primary, toggle, tip, onClick })` | 按鈕 |
| `buttons(...items)` | 一排按鈕 |
| `callText()` | 以呼叫的顏色顯示 kudio 呼叫；設定 `.text` |
| `hint(text)` | 小字說明 |
| `readout(text)` | 一行結果；`.show(text, tone)`，tone 可用 `"warn"` 或 `"ok"` |
| `table(columns, { numeric })` | 表格；`.setRows(rows, classes)` |
| `params(list, { onChange })` | 從一串參數描述直接建出整個表單，「效果」面板的控制項就是這樣建的 |

#### 蓋在圖上的檢視 {#a-view-over-the-plots}

結果需要的空間比面板欄大時，例如一組圖或一張很寬的表格，可以蓋在主畫面上：
`ctx.view.show(title, node, { onClose })` 把 `node` 放上去，`ctx.view.hide()` 把它拿掉，
`ctx.view.open` 則說明目前是不是這個面板佔著它。同一時間只有一個面板能佔用這個檢視，
切到別的工具時它仍會留著。按它的關閉按鈕會把圖還回來，並呼叫 `onClose`。

### 圖示與翻譯 {#icon-and-translations}

`icon_svg` 是一段 SVG 的內容，畫在與內建圖示相同的 24 格座標上，以 2 單位寬的圓頭線條、文字顏色描繪。
加上 `fill="currentColor"` 的元素則是實心的。拿內建的「指標」圖示比較，它是
`<path d="M4 20h16M7 16.5v-4M12 16.5V6M17 16.5v-7"/>`。內容必須能解析成 SVG，而且內建圖示的名稱
會被拒絕，所以外掛無法重畫我的圖示。這兩種問題都會讓面板被略過；有 `icon_svg` 卻沒有 `icon`
名稱可以登記，也一樣。不寫 `icon_svg`，就是用名稱沿用某個內建圖示。

`messages` 把語言對應到一張翻譯表，鍵是程式碼中寫的英文原文，一字不差。執行時才組出來的訊息，
以它的範本為鍵，值的位置寫 `{}`，所以 `"Measured {} window(s)."` 涵蓋所有數量；翻譯需要調換值的
順序時，就為它們編號，例如 `{1}` 寫在 `{0}` 前面。頁面上的標籤與狀態列的訊息查的是同一張表。
沒有翻譯的字串以英文顯示；KudioStudio 自己的翻譯表已經有的字串，則以它的翻譯為準。

## 試用 {#trying-it}

```bash
cd rms-panel
pip install -e .
kudiostudio clip.wav
```

「分段 RMS」是工具列上的最後一個工具。按「量測」，表格就會填滿：

```text
起點      音量
0.00 s    -9.0 dBFS
1.00 s    -21.1 dBFS
2.00 s    數位靜音
```

這是一段 3 秒的音訊：一秒半滿刻度的正弦波、一秒八分之一滿刻度的正弦波，再加一秒的零。
選取 1.0 到 2.5 秒、視窗長度設 0.5 秒，各列就從 1.00 s 開始，用的是音訊本身的時間軸。

改了 `panel.py`，要重新啟動 `kudiostudio`。改了 `rms.js`，只要重新載入頁面，在 `--browser`
模式下最快：重新整理分頁即可。`--debug` 會開啟視窗的開發者工具，頁面模組載入或掛載失敗時，
錯誤會留在主控台裡。
