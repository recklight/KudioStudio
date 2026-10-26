# 入門

## 安裝 {#installing}

```bash
pip install kudiostudio
```

KudioStudio 需要 Python 3.9 以上。視窗是程式自己提供的一個網頁，透過
[pywebview](https://pywebview.flowrl.com/) 用系統本來就有的網頁元件畫出來：Windows 10 與 11
是 WebView2，macOS 是 WebKit。這兩個系統都不必另外安裝 GUI 工具組。

Linux 另外需要兩樣 pip 裝不了的東西：

- pywebview 用的網頁元件。它要靠 GTK 或 Qt 來畫，這兩者以 extra 的方式安裝
  （`pip install "pywebview[gtk]"`，需要的系統套件見它的
  [安裝說明](https://pywebview.flowrl.com/guide/installation.html)）。兩者都沒有時，
  `kudiostudio` 會改在預設瀏覽器開啟同一個頁面，程式照樣能用，只是在瀏覽器分頁裡。
- PortAudio 的函式庫，播放與錄音要用：`sudo apt install libportaudio2`。
  大多數桌面環境已經有了。

### 選用套件組 {#extras}

所有較重的元件都是 extra，基本安裝保持輕量：除非你要求，否則不會裝 PyTorch。

| Extra | 加入的功能 |
|---|---|
| `pip install "kudiostudio[eval]"` | 「指標」面板的 STOI 與 SDR |
| `pip install "kudio[pesq]"` | PESQ（ITU-T P.862）。它要從原始碼編譯，需要 C 編譯器，Windows 上則是 MSVC Build Tools，所以我把它排除在 `[eval]` 與 `[all]` 之外，否則沒有編譯器的機器連那兩組都裝不起來 |
| `pip install "kudiostudio[dnsmos]"` | DNSMOS 主觀分數預測。這只會裝 onnxruntime，模型權重沒有隨附，「指標」面板會告訴你去哪裡取得 |
| `pip install "kudiostudio[enhance]"` | 學習式降噪，基於 PyTorch，透過 [kudioenhance](https://pypi.org/project/kudioenhance/)。沒有 NVIDIA GPU 的 Linux 請先裝 CPU 版 PyTorch，做法見 kudioenhance 的 README |
| `pip install "kudiostudio[audio]"` | 在 Linux 與 macOS 上錄到按停止為止。它需要 PyAudio，而 PyPI 只有 Windows 的預先編譯版，其他系統要由 pip 現場編譯：請先裝 PortAudio 的標頭檔（`sudo apt install portaudio19-dev`，或 `brew install portaudio`）。Windows 的基本安裝就已經包含 |
| `pip install "kudiostudio[all]"` | 以上全部，PESQ 除外 |

需要某個 extra 的面板，在它沒裝時會說出缺的是哪一個，而不是直接失敗。

## 啟動 {#starting-it}

```bash
kudiostudio                    # 空白工作階段
kudiostudio clip.wav           # 啟動時開啟一個檔案
kudiostudio a.wav b.wav        # 多個檔案：全部列進檔案清單，開啟第一個
kudiostudio manifest.json      # 語料庫的 manifest 會在 Manifest 面板開啟
kudiostudio --browser          # 改在預設瀏覽器開啟同一個頁面
kudiostudio --no-plugins       # 不載入其他套件加入的面板
kudiostudio --lang zh_TW       # 以繁體中文介面啟動
kudiostudio --reset            # 清掉儲存的設定
```

`--port` 指定頁面使用的本機連接埠（預設任選一個空的），`--debug` 開啟視窗的開發者工具，
`-v` 則以 debug 層級記錄日誌。

在瀏覽器裡，頁面能做的事跟視窗一樣，只有兩件事非得靠視窗不可：開檔與存檔的對話框會變成
一個讓你輸入路徑的方塊，拖進頁面的檔案也不會開啟，這時請用「開啟」。真的這樣做了，
狀態列也會提醒。

`--browser` 模式沒有視窗可關：關掉分頁，再到啟動它的終端機按 ++ctrl+c++ 結束程式。
它和關閉視窗一樣會等執行中的工作做完；再按一次 ++ctrl+c++ 就不等了。

### 介面語言 {#language}

介面有英文與繁體中文。右上角的選單可以立即切換，已開啟的面板也會跟著換，選擇會被記住。
沒有儲存過選擇時，若系統語言有對應的翻譯，就用系統語言。

兩種語言下，kudio 參數名稱、kudio 本身接受的值（方法名稱、雜訊估計器、編解碼器、
取樣格式）與呼叫說明文字都維持程式中的寫法，所以畫面上讀到的內容仍與 API 一致。
屬於 KudioStudio 自己的選項則會翻譯，例如「音高」面板的預設範圍、錄音模式、
「比較」與「指標」裡的參考訊號等等。

### 儲存的設定 {#saved-settings}

視窗會記住介面語言、分析圖的種類與參數，以及上次開啟或存檔所在的資料夾。
這些都存在一個小小的 JSON 檔裡：

- Windows：`%APPDATA%\Kudio\KudioStudio\window.json`
- 其他系統：`$XDG_CONFIG_HOME/Kudio/KudioStudio/window.json`，沒有設定這個變數時是
  `~/.config/Kudio/KudioStudio/window.json`

`kudiostudio --reset` 會把它清空。

## 第一次操作 {#a-first-session}

1. 開啟檔案：把它拖進視窗、按上方的「開啟」，或用「檔案 → 開啟…」（++ctrl+o++）。
   WAV、FLAC、OGG 與 MP3 都能開。一次開多個檔案時，它們會列在左側的「檔案」，
   點一下就能切換。
2. 上方是波形，下方是 STFT，兩者上面那一條是整段音訊的總覽。滾輪以游標為中心縮放，
   ++shift++ + 滾輪則是捲動。其餘操作見[視窗導覽](window.md)。
3. 在任一張圖上拖曳選取一段範圍，按 ++space++。「只播選取範圍」預設開啟，此時播放的是
   選取範圍；沒有選取時，從播放頭開始播。點一下圖可以移動播放頭。
4. 在工具列選「效果」，選 `highpass`，按「♪ 預覽」：效果會在選取範圍上循環播放，
   轉動 cutoff 時會重新算繪。按「套用」之前什麼都不會改變。按鈕上方那一行就是「套用」
   會執行的 kudio 呼叫，++ctrl+z++ 可以復原。
5. ++ctrl+b++ 在你的編輯版本與剛開啟時的檔案之間切換，播放中也可以，而且兩者會先對齊響度，
   不會因為比較大聲就勝出。
6. 「檔案 → 另存新檔…」（++ctrl+shift+s++）寫出 16 位元的 WAV；「檔案 → 將編輯匯出為
   Python 腳本…」則把你做過的步驟寫成一份腳本，從原始檔案產生完全相同的樣本。

[編輯與標記](editing.md)會把這些講得更完整：剪貼簿、每個接合處的交叉淡化、標記，
以及匯出的腳本在執行前會檢查什麼。

## 修改 KudioStudio 本身 {#working-on-kudiostudio-itself}

```bash
git clone https://github.com/recklight/KudioStudio.git
cd KudioStudio
pip install -e ".[dev]"
pytest -q
```

這份說明用 `pip install -e ".[docs]"` 與 `mkdocs serve` 建置。裡面的截圖來自
`python tools/screenshots.py`（中文截圖加上 `--lang zh_TW`），它用無頭模式的 Chrome、
Chromium 或 Edge 畫出視窗，所以截圖是重新產生的，不是手動擷取的。
