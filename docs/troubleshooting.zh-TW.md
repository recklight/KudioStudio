# 疑難排解

每一則都說明你看到什麼、為什麼會這樣，以及該怎麼處理。訊息照視窗上顯示的樣子引用，路徑、數字或
其他函式庫的錯誤訊息以 `…` 代替。大部分訊息出現在狀態列，其餘的在啟動 `kudiostudio` 的終端機裡；
出問題時，最好讓那個終端機開著。

## 啟動 {#starting}

### 頁面開在瀏覽器裡，而不是視窗 {#the-page-opens-in-a-browser-not-a-window}

預設瀏覽器開了一個分頁，視窗的內容在裡面，終端機則寫著：

```text
no window to draw in (…); opening the browser instead
KudioStudio … on http://127.0.0.1:…/?token=…
Ctrl+C to stop.
```

pywebview 找不到可以用來繪製的網頁元件。Windows 與 macOS 內建就有；Linux 上 pywebview 要透過
GUI 工具組來畫，而工具組得以它的 extra 另外安裝。找不到的時候，程式不會就這樣沒有畫面，而是在瀏覽器
開啟同一個頁面；除了[下文](#in-a-browser-tab)列出的幾點，其他功能都照常可用。

要有視窗，就安裝 GTK 版：`pip install "pywebview[gtk]"`，再加上 pywebview 安裝說明列出的系統套件。
[安裝](getting-started.md#installing)一節的 Linux 部分有它的連結。

### 瀏覽器分頁沒有反應 {#a-browser-tab-that-does-nothing}

頁面在，但什麼都開不了、播不了，狀態列最左邊的小圓點是警示色。

每次執行都在自己的連接埠上提供頁面，並為這次執行產生一個 token，沒有帶 token 的請求一律拒絕。
上次執行留下的分頁，連的是已經不在的伺服器；手動輸入的網址若少了 `?token=…`，
正在執行的伺服器也會拒絕它。

請用這次執行開啟的分頁，或它印出的完整網址，連同 token。若是在視窗裡看到小圓點變成警示色，
表示頁面背後的程式已經停了，原因寫在終端機裡。

### 連接埠已被佔用 {#the-port-is-already-in-use}

`kudiostudio --port …` 在啟動時就停下，還沒出現任何視窗，traceback 的最後是一個 `OSError`，
說這個位址已經被使用。是別的程式佔用了那個連接埠，或是另一個 KudioStudio。

不加 `--port` 時，由系統挑一個空的連接埠，就不會發生這種事。拿掉它，或換一個連接埠。

### 看出哪裡出錯 {#seeing-what-went-wrong}

狀態列只放得下一行，而失敗的原因常常不只一行。從終端機以 `kudiostudio -v` 啟動，會以 debug 層級
記錄：失敗的背景工作會在那裡留下完整的 traceback，狀態列只顯示它的最後一行。`--debug` 會開啟視窗的
開發者工具，主控台裡有頁面本身的錯誤，例如載入失敗的面板模組。在瀏覽器分頁裡，用瀏覽器自己的
開發者工具即可。

## 在瀏覽器分頁裡 {#in-a-browser-tab}

使用 `--browser`，或沒有網頁元件時，因為頁面外面沒有視窗，有幾件事會不一樣。

把檔案拖到頁面上，會看到：

```text
拖放檔案要在 KudioStudio 視窗裡才行，這裡請用「開啟」。
```

瀏覽器不會告訴網頁被拖進來的檔案在磁碟上的哪裡，而程式是靠路徑開啟檔案的。這時請用「開啟」
（++ctrl+o++）。

開啟與儲存的對話框是視窗自己的，所以在分頁裡，每個對話框都變成一個輸入路徑的方塊，
裡面寫著「這台電腦上音訊檔的路徑」。一次只能輸入一個路徑：要把多個檔案放進「檔案」清單，
就一個一個開。

「檔案 → 結束」（++ctrl+q++）的回答是：

```text
要離開請關掉瀏覽器分頁；伺服器用 Ctrl+C 停止。
```

關掉分頁之後程式仍在執行，要在它的終端機按 ++ctrl+c++ 才會停。它和關閉視窗一樣，會先等執行中的
工作做完，並在終端機與狀態列說明：

```text
將在批次完成後停止 — 執行中的工作無法中途停止。再按一次 Ctrl+C 會立刻停止，工作做到哪裡就停在哪裡。
```

再按一次 ++ctrl+c++ 就會立刻停止。只有在等下去更糟時才這麼做：做到一半被停下的批次處理，
會留下寫了一半的輸出目錄。

## 檔案與設定 {#files-and-settings}

### 開不了的檔案 {#a-file-that-will-not-open}

```text
無法開啟 clip.m4a — AudioIOError: Could not load audio file: … (… Format not recognised.)
```

檔案是用 soundfile 讀取的，它能讀 WAV、FLAC、OGG、MP3 與 AIFF 等格式，開啟對話框列出的也就是這些。
M4A（也就是 AAC）不在其中；在 `All files (*.*)` 下選它，或把它拖進視窗，就會這樣失敗。
損壞的檔案，或副檔名是 `.wav` 但內容不是的檔案，也一樣。

用其他程式轉成 WAV 或 FLAC，再開啟轉好的檔案。

### 從乾淨的設定重新開始 {#starting-from-clean-settings}

視窗會記住介面語言、分析圖的種類與參數、上次開啟與存檔所在的資料夾、上次開著的檔案，
以及[設定](window.md#settings)裡的一切。這些都存在一個小小的 JSON 檔裡：

- Windows：`%APPDATA%\Kudio\KudioStudio\window.json`
- 其他系統：`$XDG_CONFIG_HOME/Kudio/KudioStudio/window.json`，沒有設定這個變數時是
  `~/.config/Kudio/KudioStudio/window.json`

`kudiostudio --reset` 會把它清空，視窗就像第一次啟動時一樣。內容不是有效 JSON 的檔案會被忽略，
下次設定有變動時重新寫入。資料夾無法寫入時，終端機會出現 `settings not saved: …`，視窗照常運作，
只是記不住設定。

## 播放與錄音 {#playback-and-recording}

### 無法播放 {#playback-fails}

按下播放，狀態列寫著：

```text
播放失敗 — DependencyError: 'PyAudio' is required for this feature. Install it with: pip install "kudio[audio]"
```

播放是透過 sounddevice。當 sounddevice 無法播放時，可能是沒有輸出裝置，或在 Linux 上缺少 PortAudio
的函式庫，kudio 就接著試 PyAudio，而在 Linux 與 macOS 上，PyAudio 只有裝了 `[audio]` extra 才會有。
所以訊息講的是 PyAudio，真正的問題通常是裝置。若 PyAudio 已經裝了、仍然沒有東西可以播放，
訊息的結尾會變成 `can't be played by pyaudio`。

先確認系統有一個其他程式能正常播放的輸出裝置。在 Linux 上，安裝 PortAudio 的函式庫：
`sudo apt install libportaudio2`。裝 `[audio]` extra 只會改變訊息，不會補上缺少的裝置。

### 錄到停止為止的錄音失敗 {#recording-until-you-stop-fails}

「模式」設為「錄到停止為止」，按「● 開始」，會看到：

```text
錄音失敗 — DependencyError: 'PyAudio' is required for this feature. Install it with: pip install "kudio[audio]"
```

「裝置」清單裡只有一個選項，狀態列寫著「找不到輸入裝置 — 是否已安裝 kudio[audio]？」。

不限長度的錄音由 `kudio.StreamRecorder` 執行，它透過 PyAudio 讀取麥克風。PyPI 只有 Windows 的
PyAudio 預先編譯版，所以 Windows 的基本安裝就已經有；Linux 與 macOS 則要靠 `[audio]` extra，
由 pip 對著 PortAudio 的標頭檔編譯。「固定長度」是透過 sounddevice 錄音，不需要 PyAudio。

```bash
sudo apt install portaudio19-dev     # macOS: brew install portaudio
pip install "kudiostudio[audio]"
```

編譯時還需要 C 編譯器。

若 PyAudio 已經裝了，清單是空的時候會改說：

```text
PyAudio 已經安裝，但系統沒有提供任何輸入裝置給它：請確認麥克風已接上，並在音效設定中啟用。
```

這時再裝什麼都沒有用。清單列出 PortAudio 看得到的每個輸入，並標出各自的 host API：在沒有設定
預設錄音裝置的 Windows 上，MME、DirectSound 與 WASAPI 一個也不提供，麥克風只出現在
Windows WDM-KS 底下。

### 沒有錄到任何東西 {#nothing-was-captured}

「沒有錄到任何東西 — 請檢查輸入裝置。」表示停止時這次錄音裡沒有任何音訊：選的裝置什麼都沒送來。

兩種模式的裝置編號不同，「固定長度」用 sounddevice 的編號，「錄到停止為止」用 PyAudio 的，
所以「裝置」清單會跟著模式換，下方那一行會說明目前顯示的是哪一種編號，兩種模式也各自記得自己選的裝置。
請確認選到的是你要的那支麥克風，插上麥克風之後則按「重新整理裝置」。

## 指標 {#metrics}

### DNSMOS 無法使用 {#dnsmos-is-unavailable}

「預測主觀分數（DNSMOS）」下方那一行以警示色顯示：

```text
FeatureError: find_dnsmos_model(): sig_bak_ovr.onnx not found. kudio does not ship
the DNSMOS models; clone https://github.com/microsoft/DNS-Challenge and pass
model_dir='DNS-Challenge/DNSMOS', or set KUDIO_DNSMOS_DIR to that folder
```

狀態列則寫著「DNSMOS 無法使用 — 缺少的項目請見面板說明。」

模型權重屬於 Microsoft 的 DNS-Challenge，也要從那裡取得。面板沒有可以填資料夾的欄位，
所以是透過環境變數 `KUDIO_DNSMOS_DIR` 找到它，這個變數必須在 `kudiostudio` 啟動之前設好：

```bash
git clone https://github.com/microsoft/DNS-Challenge
export KUDIO_DNSMOS_DIR="$PWD/DNS-Challenge/DNSMOS"
kudiostudio
```

在 PowerShell 中，第二行改成 `$env:KUDIO_DNSMOS_DIR = "$PWD\DNS-Challenge\DNSMOS"`。

找到模型但缺少 onnxruntime 時，那一行則會寫：

```text
DependencyError: 'onnxruntime' is required for this feature. Install it with:
pip install "kudio[dnsmos]"
```

用 `pip install "kudiostudio[dnsmos]"` 安裝即可。

### PESQ 裝不起來 {#pesq-will-not-install}

`pip install "kudio[pesq]"` 停在編譯錯誤。`pesq` 套件在安裝時要從原始碼編譯，所以需要 C 編譯器：
Windows 上是 Microsoft C++ Build Tools，macOS 上是命令列工具（`xcode-select --install`），
Debian 與 Ubuntu 上則是 `build-essential` 與 `python3-dev`。裝好其中一個，再執行一次 pip。

「指標」面板的其他部分都不需要它，只有建立在 PESQ 之上的 CSIG、CBAK 與 COVL 例外，沒有它時
那一列會寫「需要 PESQ，但沒有安裝」。STOI 與 SDR 來自 `kudiostudio[eval]`，安裝時不需要編譯器。

### 裝了，卻仍顯示未安裝 {#installed-and-still-not-installed}

`pip install` 之後，「指標」面板仍然把某個後端列為無法使用。後端只在每次啟動時檢查一次，
所以請重新啟動 `kudiostudio`。若還是一樣，就是 pip 裝到了另一個 Python，而不是執行 `kudiostudio`
的那一個：請用那個 Python 的 `python -m pip` 安裝。

PESQ 那一列顯示「在 44100 Hz 下沒有定義（PESQ 僅支援 8 或 16 kHz）」則不是安裝問題。PESQ 只定義在
這兩種取樣率，其他取樣率的音訊就沒有 PESQ。Hu 與 Loizou 的指標、CSIG、CBAK、COVL 與 NCM
旁邊的「在 44100 Hz 下沒有定義（僅限 8 或 16 kHz）」也一樣。要用它們評分，先在
[批次](panels/batch.md)面板把音訊所在的資料夾轉成 16 kHz，再開啟轉好的檔案；參考檔案本來就會以
音訊的取樣率讀入。

## 增強 {#enhance}

### 學習式模型無法使用 {#learned-models-are-unavailable}

「增強」面板的說明，以及按「降噪」時的狀態列，都寫著：

```text
學習式模型需要 kudioenhance 套件與 PyTorch：執行 pip install "kudiostudio[enhance]" 就會兩個一起安裝
```

兩者都只查找、不匯入。kudioenhance 只在載入模型時才匯入 PyTorch，所以只有其中一個的安裝，
例如以 `--no-deps` 裝的 kudioenhance，會在第一次降噪時才失敗；這裡直接把它當成沒裝。

執行 `pip install "kudiostudio[enhance]"`。沒有 NVIDIA GPU 的 Linux，請先裝 CPU 版 PyTorch，
見[選用套件組](getting-started.md#extras)。

### 載入不了的執行目錄 {#a-run-directory-that-will-not-load}

「請先選擇 kudioenhance 執行目錄。」表示 `run_dir` 沒有選資料夾，或選的資料夾不存在。

資料夾存在、但不是一次訓練的執行目錄時，會在載入模型時失敗：

```text
學習式模型（exp1） 失敗 — FileNotFoundError: …
```

執行目錄就是 kudioenhance 訓練時寫出的那個資料夾：裡面有 `config.yaml`、存放正規化統計量的
`stats.npz`，以及模型檢查點，一個 `.pt` 檔。請選那個資料夾本身，而不是它的上一層。
「比較」面板的「學習式模型」欄位，也是填同一個資料夾。

模型的訓練取樣率與音訊不同、又關掉了重新取樣時，也會這樣失敗：

```text
學習式模型（exp1） 失敗 — ValueError: 此模型以 16000 Hz 訓練，但音訊為 48000 Hz — 請啟用重新取樣，或在「批次」面板轉換
```

勾選「若取樣率不同，重新取樣到模型的取樣率」，音訊就會以模型的取樣率送進去，再以原本的取樣率回來。

## Manifest {#manifest}

### 有檔案遺失的列 {#rows-with-a-file-missing}

manifest 路徑下方那一行以警示色顯示：

```text
12000 列 · 12000 列有檔案遺失
```

游標停在標示出來的格子上，會指出哪個檔案不在：

```text
不在磁碟上：noisy/0001.wav
```

manifest 裡的相對路徑，是相對於執行合成時所在的資料夾，而沒有任何地方記錄那是哪個資料夾。
面板會先在目前資料夾（也就是啟動 `kudiostudio` 的資料夾）下找，再找 manifest 所在的資料夾與
它上面的每一層，並把整份 manifest 對著同一個資料夾讀：第一個找得到所有檔案的資料夾，
找不到的話就用找到最多的那一個。只要找到任何一個檔案，那一行最後就會加上「相對路徑在 … 下找到」，
寫出這個資料夾，遺失檔案的提示也會以「已在 … 下尋找」結尾。像上面那樣兩處都沒有寫出資料夾，
表示沒有任何一個相對路徑在這些資料夾下找得到，通常就是這些資料夾都不對。絕對路徑則照寫的樣子查找，
所以在另一台機器上產生、寫著絕對路徑的 manifest，在這裡什麼都找不到。

在執行合成時所在的資料夾啟動 `kudiostudio`，再開一次 manifest。若只有部分列遺失，就是檔案本身不見了：
勾選「只顯示有檔案遺失的列」列出它們，檔案找回來之後按「重新載入」。對這樣的列按「開啟」，
會看到「… 不在磁碟上 — 沒有東西可開啟。」

## 外掛 {#plugins}

### 外掛未載入 {#a-plugin-was-left-out}

視窗開啟時，狀態列以警示色顯示：

```text
外掛 spectra 未載入 — ModuleNotFoundError: No module named 'mypackage'
```

每個外掛一則，以分號隔開。終端機裡也有同樣的內容：`plugin spectra left out -- …`。

來自其他套件的面板，在以下情況會被排除在工具列之外：它的模組無法匯入、它指向的不是面板、它的圖示
無法登記、建立它時拋出例外，或它的 id 不能用。圖示無法登記，是指 `icon_svg` 不是有效的 SVG、沒有配上
`icon` 名稱，或用了內建圖示的名稱。id 不能用，是指沒有 id、id 已經在工具列上（包括「檢查」的
`inspect`），或 id 是視窗本身指令用的 `markers`：

```text
外掛 spectra 未載入 — ValueError: 它的 id 'inspect' 已經在工具列上
```

無論哪一種，視窗都會照常開啟。破折號後面的錯誤會說明是哪一種；請更新或重新安裝提供它的套件，
或把它移除。外掛要遵守的規則見[撰寫面板](plugins.md)。

### 不載入外掛啟動 {#starting-without-plugins}

出了問題、懷疑是外掛造成的時候，先不載入任何外掛啟動：

```bash
kudiostudio --no-plugins
```

環境變數 `KUDIOSTUDIO_NO_PLUGINS` 設成任何非空字串，效果相同，對設了它的每一次啟動都有效：

```bash
KUDIOSTUDIO_NO_PLUGINS=1 kudiostudio
```

在 PowerShell 中，先執行 `$env:KUDIOSTUDIO_NO_PLUGINS = "1"`，再執行 `kudiostudio`。
