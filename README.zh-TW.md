# KudioStudio

![KudioStudio](https://raw.githubusercontent.com/recklight/KudioStudio/master/docs/assets/banner.webp)

[English](https://github.com/recklight/KudioStudio/blob/master/README.md) | **繁體中文**

[![PyPI](https://img.shields.io/pypi/v/kudiostudio.svg)](https://pypi.org/project/kudiostudio/)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/)
[![GUI](https://img.shields.io/badge/GUI-pywebview-1fc6f0.svg)](https://pywebview.flowrl.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://github.com/recklight/KudioStudio/blob/master/LICENSE.txt)
[![kudio](https://img.shields.io/pypi/v/kudio.svg?label=kudio)](https://pypi.org/project/kudio/)

**[`kudio`](https://pypi.org/project/kudio/) 音訊工具組的桌面工作台。**

開啟一段音訊，觀察它、修改它、量測它，並用它建立資料集——不必先寫腳本。
每個控制項都對應到一個有文件說明的 `kudio` 函式，每個面板下方的說明文字會
顯示它即將執行的確切呼叫，所以你在這裡試出來的任何做法，都能直接轉成程式碼。

![KudioStudio](https://raw.githubusercontent.com/recklight/KudioStudio/master/docs/images/main.zh-TW.png)

## 安裝

```bash
pip install kudiostudio
kudiostudio
```

需要 **Python 3.9+**。視窗是程式自己提供的一個網頁，透過 [pywebview](https://pywebview.flowrl.com/)
用系統本來就有的網頁元件畫出來：Windows 10 與 11 是 WebView2，macOS 是 WebKit。在 Linux 上
pywebview 要靠 GTK 或 Qt 來畫，它以 extra 的方式安裝（`pip install "pywebview[gtk]"`，系統套件見它的
[安裝說明](https://pywebview.flowrl.com/guide/installation.html)）；兩者都沒有時，`kudiostudio` 會改在
預設瀏覽器開啟同一個頁面。在 Linux 上，播放與錄音另外需要 PortAudio 的函式庫（`sudo apt install libportaudio2`），
大多數桌面環境已經有了。所有較重的元件都是選用的 extra：

| Extra | 加入的功能 |
|---|---|
| `pip install "kudiostudio[eval]"` | STOI / SDR 指標 |
| `pip install "kudio[pesq]"` | PESQ（ITU-T P.862）。它自成一個 extra，因為它要從原始碼編譯——需要 C 編譯器，在 Windows 上則需要 MSVC Build Tools——所以刻意不放進 `[eval]` 與 `[all]`，否則那兩者會裝不起來 |
| `pip install "kudiostudio[dnsmos]"` | DNSMOS 主觀分數預測（onnxruntime；權重未隨附） |
| `pip install "kudiostudio[enhance]"` | 學習式降噪，基於 PyTorch，透過 [kudioenhance](https://pypi.org/project/kudioenhance/)。在沒有 NVIDIA GPU 的 Linux 上，請先裝 CPU 版 PyTorch，做法見 kudioenhance 的 README |
| `pip install "kudiostudio[audio]"` | 在 Linux 與 macOS 上「錄到按停止」的錄音。它需要 PyAudio，而 PyPI 只有 Windows 的預先編譯版，其他系統要由 pip 現場編譯：請先裝 PortAudio 的標頭檔（`sudo apt install portaudio19-dev`，或 `brew install portaudio`）。Windows 的基本安裝就已經包含 |
| `pip install "kudiostudio[all]"` | 以上全部，PESQ 除外 |

基本安裝保持輕量：除非你要求，否則不會安裝 PyTorch，也完全不需要 GUI 工具組。

要修改 KudioStudio 本身，請從原始碼安裝：

```bash
git clone https://github.com/recklight/KudioStudio.git
cd KudioStudio
pip install -e ".[dev]"
```

```bash
kudiostudio                    # 空白工作階段
kudiostudio clip.wav           # 啟動時開啟一個檔案
kudiostudio manifest.json      # 語料庫的 manifest 會在 Manifest 面板開啟
kudiostudio --browser          # 改在預設瀏覽器開啟同一個頁面
kudiostudio --no-plugins       # 不載入其他套件加入的面板
kudiostudio --lang zh_TW       # 以繁體中文介面啟動
kudiostudio --reset            # 忘記儲存的設定
```

介面提供**英文**與**繁體中文**。右上角的選單可以立即切換，已開啟的面板也會跟著換，
選擇會被記住。若沒有儲存過選擇，會在有對應翻譯時使用系統語言。清單裡的值、`kudio` 參數名稱與
呼叫說明文字維持程式中的寫法，讓你讀到的內容仍與 API 一致。

## 各面板的功能

| 面板 | 背後的 `kudio` | 你能得到什麼 |
|---|---|---|
| **Analysis**（中央） | `STFT`, `melspectrogram`, `mfcc`, `audio_report`, `loudness_over_time`, `true_peak` | 波形 + 對數功率 STFT、對數 mel 或 MFCC，時間軸連動，可即時調整 `n_fft` / `hop_length` / `n_mels` / `n_mfcc`，一條畫在獨立 LUFS 軸上的**響度曲線**，以及含 true peak 與響度範圍的即時 **Levels** 讀數 |
| **Effects** | `kudio.effects`, `kudio.rir`, `kudio.apply_rir`, `kudio.rt60`, `kudio.effects.channel` | 淡入淡出、`reverse`、`remove_dc`、四種 Butterworth 濾波器、`gain`、`pitch_shift`、`time_stretch`、`reverb`、**`room`**（真正的脈衝響應，可指定 seed、可量測）、**`measured_room`**（來自檔案的脈衝響應，並讀出其 T30、EDT、C50 與 DRR）、**`telephone`** / **`bit_depth`** / **`dropouts`**（傳輸通道，分散或成串的掉包）、`add_noise_snr`、`trim_silence`、`vad_trim`、`split_on_silence`——並有不會提交任何變更的循環 **Preview** |
| **Enhance** | `kudio.spectral_enhance` + `kudioenhance` | 七種統計式降噪器 × 四種雜訊估計器、`wavelet_low_pass_filter`，以及來自 [kudioenhance](https://pypi.org/project/kudioenhance/) 訓練結果的**學習式模型** |
| **Compare** | `kudio.compare_enhancers` | 在同一段音訊上跑每種方法 × 每種雜訊估計器並排名，可逐列試聽，或**把多列接連播放**——訓練好的模型也在同一張表裡 |
| **Record** | `kudio.record`, `kudio.StreamRecorder`, `kudio.StreamEnhancer`, `kudio.SpectrogramStream` | 固定長度錄音，**或錄到你按停止為止**，附即時音量表、**滾動波形與頻譜圖**，以及選用的**即時降噪監聽** |
| **Synthesize** | `kudio.Synthesizer` | 乾淨語音 × 雜訊 × SNR **× 房間** 的資料集生成，可選擇**經過傳輸鏈路**（電話 / VoIP），並可在混合音旁輸出**含殘響的目標音**；以 seed 保證可重現，並寫出記錄每個檔案來源的 manifest |
| **Manifest** | `kudio.load_manifest` | 把那份 manifest 讀回成可排序的表格——noisy、clean、noise、SNR、room、link、target——可依雜訊、SNR、房間與鏈路篩選，**標記並計數遺失的檔案**，用 **雜訊 × SNR 覆蓋格**顯示缺口，每列的 noisy、clean 與 target 檔案一鍵就能試聽 |
| **Pitch** | `kudio.f0` | pYIN 基頻估計，**附帶**有聲/無聲判斷——中位數、以 Hz 與半音表示的範圍、畫在頻譜圖上的輪廓，以及作為標記的有聲區段 |
| **Metrics** | `kudio.audio_report`, `kudio.dnsmos`, `kudio.core.evaluator`, `kudio.find_delay` | **不需要參考訊號**：削波、音量、DC、底噪、實際頻寬，以及預測的 **DNSMOS** 主觀分數。**有參考訊號時**：一律提供 SNR / SI-SDR / 分段 SNR，若有安裝則再加上 PESQ / STOI / SDR |
| **Batch** | `kudio.convert_folder`, `kudio.enhance_folder`, `kudio.loudness`, `kudio.true_peak`, `kudio.rir` | 整個資料夾的重新取樣、位元深度、混為單聲道、修剪、峰值**或 LUFS** 正規化，**以及降噪**——並保留輸入的資料夾結構——附一份**響度報告**，列出每個無法達到目標的檔案與原因，還能在乾聲樹旁輸出一份**殘響副本** |

有選取範圍時，編輯只套用在**選取範圍**上，否則套用到整段音訊——而且每個編輯都能復原。
`Revert to original` 會一次捨棄全部編輯。

### 不只是處理，還能編輯

剪下、複製、貼上、刪除、插入靜音與裁切，全部可復原，全部作用在選取範圍上。
沒有選取時，貼上會落在播放頭的位置，而剪貼簿會記得**它自己的取樣率**——
把一段 8 kHz 的片段貼進 16 kHz 的音訊時，會先重新取樣，而不是以兩倍速播放。

`Save selection as…` 只寫出反白的範圍；`Export analysis image…` 會依你當下看到的
樣子擷取波形與頻譜圖，連縮放狀態也一樣。

**每個接合處都會交叉淡化**，預設 5 ms，因為硬切會在波形上留下一個階梯，而階梯就是喀聲。
它預設開啟，而且**從不默默進行**：每次編輯的狀態列都會說明套用了多少，包括答案是「沒有」的時候。
悄悄改動音訊比一個喀聲更糟，所以這個設定是看得見的，而不是藏起來的。

長度設定**就在傳輸列上**，而不是藏在對話框後面，因為合適的長度因編輯而異——
一個字中間的 5 ms 與兩段錄音之間的 200 ms 是不同的工作，而一個得開對話框才能改的設定，
就是沒人會去改的設定。`Edit → Splice crossfade…` 可以在同一個控制項輸入精確的數值。
工作階段 API 也可以逐次指定，所以 `session.delete(crossfade_ms=200)` 只會在那一次接合使用
200 ms，預設值保持不變。

對選取範圍套用*效果*則刻意不做交叉淡化——它是用處理後的版本取代該範圍，
若把邊緣再混回去，兩端就會各留下 5 ms 沒有套到效果。

### 把你的編輯匯出成腳本

`File → Export edits as Python script…` 會把復原歷程寫成一份普通的 Python 腳本。
它以 KudioStudio 讀取原始檔案的方式讀取檔案，依序重做每個編輯——每一步都標註它的
復原標籤，每一步都是當初實際執行的 `kudio` 呼叫或切片——然後儲存結果。在原始檔案上執行，
它產生的是**與工作階段中完全相同的樣本**，而不是近似值；測試套件會在另一個直譯器中
執行匯出的腳本，正是檢查這一點。

```python
# 2. highpass
head, y, tail = y[:4800], y[4800:14400], y[14400:]  # 0.300–0.900 s
y = kudio.highpass(y, sr, cutoff=120.0, order=4, zero_phase=True)
y = np.concatenate([head, y, tail])

clip = y[8000:11200].copy()  # pasted in step 5

# 3. cut
y = kudio.splice([y[:3200], y[6400:]], sr, 0.03)  # 0.200–0.400 s
```

- **復原會把該步驟從腳本中拿掉**；Revert、開啟另一個檔案以及開始新的錄音，都會讓歷程重新開始。
  舊到已超出復原堆疊的編輯仍會保留——它仍然存在於音訊裡。
- 每個接合處都帶著它實際使用的交叉淡化長度，而對選取範圍套用的效果，則照套用時的方式不加淡化接回。
- **貼上的是當初複製的內容**，而不是腳本執行到那裡時那些樣本位置上的東西：`clip = …`
  這一行會放在當初複製的位置。若剪貼簿是在一個之後被復原的編輯之前複製的，或來自另一個檔案，
  會先從那個檔案重建。若是從先前某次錄音複製的，則沒有檔案可以重建——每次錄音都是獨立的，
  不論多麼相像——所以腳本會在那裡停下，匯出時也會說明原因。
- **腳本會檢查它處理的對象。** 它帶有編輯所依據檔案的 SHA-256，若 `SOURCE` 的內容不同就會停止。
  `Save As` 預設會建議你開啟的那個檔案，而一旦編輯後的音訊覆寫了它，讀回該檔案的腳本就會把每個編輯
  再套用一次；匯出時只要能判斷出來，也會對此發出警告。請把 `SOURCE` 指向原始檔案的副本，
  或刪掉這個檢查，以便在另一個檔案上執行相同的編輯。學習式模型的檔案也以相同方式檢查，
  所以若執行目錄在編輯之後又重新訓練過，腳本會停下，而不是用一個不同的模型去降噪。
- 使用**隨機** seed 的效果，會把 Apply 當時實際抽到的 seed 寫進呼叫中，讓腳本重現那次結果，
  而不是重新擲骰。降噪也會被記錄——學習式模型會寫成 kudioenhance 的
  `Enhancer.load(run_dir).enhance(y)`，包含重新取樣。
- **不會默默略過任何東西。** 無法寫成程式碼的編輯會在該步驟以 `raise` 讓腳本停止，
  腳本開頭會列出它，匯出時也會在狀態列說明；少了一步的腳本會寫出不同的檔案，卻看不出任何異狀。
  從來不是檔案的錄音會得到一個佔位用的 `SOURCE` 與相同的警告。檔名與復原標籤寫進腳本註解時
  會經過跳脫處理，所以名稱中的換行字元無法結束註解、把後面的內容變成程式碼。

在程式中，`session.edit_script()` 會回傳相同的文字，`session.history` 則是它背後的各個步驟。

### 標記

選取範圍只有一個、沒有名稱，而且一做新的選取就消失了。標記則是會保留下來的具名時間點與範圍：
點一下跳過去，雙擊範圍即可選取它，而 **`Find speech`** 會用 `kudio.vad` 填滿清單。

<img src="https://raw.githubusercontent.com/recklight/KudioStudio/master/docs/images/sidebar_markers.zh-TW.png" width="260" alt="標記清單">

標記是*時間*上的位置，所以改變長度的編輯會移動它們——刪掉兩秒，後面的一切都會往左移。
復原會把它們放回原位，因為標記清單是跟著復原堆疊一起存放的，而不是重新計算出來的。

**它們能跨工作階段保留。** `Save` 會在音訊旁寫出 `clip.wav.labels.json`，再次開啟該檔案時
會直接把標記讀回來。`Export…` 則會改寫成 **Audacity 標籤軌**，那只是三欄純文字，
音訊界大部分的工具都已經能讀。

<img src="https://raw.githubusercontent.com/recklight/KudioStudio/master/docs/images/panel_effects.zh-TW.png" width="330" alt="Effects 面板">
<img src="https://raw.githubusercontent.com/recklight/KudioStudio/master/docs/images/panel_synthesize.zh-TW.png" width="330" alt="Synthesize 面板">

### 與原始音訊做 A/B 比較

`Ctrl+B` 會把整個工作台——播放、波形與頻譜圖——在你的編輯版本與剛載入時的檔案之間切換。
它*在播放中*也能用：播放會在另一個緩衝區的同一時刻接續，所以切換是無縫的，
你比較的是同一個瞬間，而不是同一次按鍵。

**Match loudness** 預設開啟，而且它比聽起來重要得多。在同一段音訊的兩個版本之間，
較大聲的那個幾乎總是被偏好，不論品質如何——所以沒有對齊響度的 A/B 有一部分其實是在比響度。
原始音訊會用 `kudio.match_loudness`（ITU-R BS.1770）調整到與編輯版本相同的響度，
狀態列也一律會標示套用的增益，所以一個只是改變了音量的效果，聽起來會正確地完全相同，
而不是莫名其妙地變好：

```
A/B — the original, levelled to the edit (-17.6 LU applied)
A/B — the original, +17.6 LU against the edit; turn on Match loudness to compare fairly
```

當你要評判的*正是*音量時，就把它關掉。

兩者之間的座標軸刻意**不會**重新縮放：一個同時把音量減半的降噪器，看起來就該只有一半高，
而不是被拉伸回去填滿整張圖。縮放狀態在切換時會保留，也是同樣的理由。

<img src="https://raw.githubusercontent.com/recklight/KudioStudio/master/docs/images/ab_original.zh-TW.png" width="420" alt="A/B：原始的含噪音訊">
<img src="https://raw.githubusercontent.com/recklight/KudioStudio/master/docs/images/ab_edited.zh-TW.png" width="420" alt="A/B：經過 trad_enhance 之後">

*同一段音訊、同樣的座標軸、同一個瞬間——`trad_enhance` 讓背景雜訊變稀薄，並保留了 chirp。*

各項工具始終作用在編輯版本上——比較是監聽，而不是一個你可能不小心在裡面處理音訊的模式。

### 提交之前先預覽

**Preview** 會試聽效果而且**不改變任何東西**：沒有編輯、沒有復原步驟。
它會循環播放，所以找出合適的 `top_db` 是靠聽，而不是套用—聽—復原—再來一次。
Preview 與 Apply 在同樣的音訊上執行同樣的程式碼，所以你聽到的就是 Apply 將會提交的結果。

- **Effects** 在你轉動旋鈕時重新算繪，最後一次轉動後 120 ms 開始。
- **Enhance** 不在視窗的執行緒上算——降噪要花的是秒，不是毫秒——它會像 Denoise 一樣處理整段音訊，
  然後循環播放選取的部分。算的時候再轉旋鈕，接著算的是最新的數值，中間經過的會略過。

任何其他要求播放的動作——Play、Stop、一次編輯——都會結束試聽並釋放按鈕。

### 別人實際量測過的房間

`room` 用 RT60 與 DRR 建立一個統計式房間：可重現、只有一個旋鈕，而且沒有早期反射，
這正是資料增強要的，卻不是某個特定廳堂的聲音。**`measured_room`** 則改從檔案讀取脈衝響應，
並用 `kudio.apply_rir` 套用。它的讀取方式與記錄下來的呼叫一致——以音訊的取樣率、單聲道
呼叫 `kudio.file_load`——若音訊的取樣率改變則會重新讀取，所以 Preview、Apply 與匯出的腳本
都使用相同的樣本：

```python
y = kudio.apply_rir(y, kudio.file_load('/rooms/hall.wav', sr=sr)[0][192:])
```

`[192:]` 就是 **`start at the direct sound`**，預設開啟。量測到的響應開頭是聲音傳到麥克風
所花的時間，若保留下來，就會延遲音訊——處理過的選取範圍會比前後的音訊晚出現。
它讓響應從最大的那個樣本開始，也就是 `kudio.rir` 放置直達路徑的位置。

在選擇器下方，`kudio.rt60` 會依**實際套用時的樣子**讀取響應——重新取樣、混為單聲道、修剪之後——
所以一個在 48 kHz 量測、以 8 kHz 套用的廳堂，描述的是它剩下的部分：

```
0.91 s at 16000 Hz, direct sound at 12.0 ms — applied from there
T30 0.59 s (EDT 0.54 s)  ·  C50 +9.4 dB  ·  DRR +3.0 dB  ·  fit 1.000
```

不是脈衝響應的檔案仍然會算出一個 T30。當衰減不是直線，或 EDT 與 T30 不一致時，
讀數會說明這一點並變成紅色。

### 這段錄音到底能不能用？

Metrics 面板的每個指標都需要在劣化訊號旁邊放一個乾淨的參考訊號。合成資料集有參考訊號。
**真實錄音永遠沒有**——它的「原始版本」*就是*含噪的那個，所以拿它來評分，量到的是你改動了
音訊多少，而不是你有沒有改善它。

`kudio.audio_report` 不需要參考訊號，所以 Inspect 工具上的 **Levels** 讀數永遠是即時的：
峰值、響度、頻寬，以及錄音本身有什麼問題。Metrics 面板的 **Inspect** 會給出完整的一組數據。

<img src="https://raw.githubusercontent.com/recklight/KudioStudio/master/docs/images/sidebar_levels.zh-TW.png" width="300" alt="Levels 讀數">

它能抓出那些默默浪費掉一個資料集的問題：

```
· 4692 samples clipped (9.78% of the clip) — the recording was already
  too hot; no processing gets that back
· content stops at 3812 Hz although the file claims 16000 Hz — it was
  very likely upsampled from 8000 Hz, and the extra rate is empty
```

**Record** 面板會對每一次錄音執行同樣的檢查，趁麥克風還架著、還來得及重錄的時候。

這只說明音訊在技術上沒問題，不代表它聽起來好。至於另一半，**DNSMOS**（ITU-T P.835）
會預測聽者會怎麼評價：

```
SIG 2.94 · BAK 4.12 · OVRL 3.01
poor (OVRL 3.01) — the background is cleaner than the speech, which is
what over-aggressive denoising looks like
```

在降噪時 SIG 與 BAK 會往相反方向移動——壓得越用力，背景越乾淨，語音卻被吃掉——
所以三個數字都會顯示，而不是平均成一個。需要 `pip install -e ".[dnsmos]"` **以及**
DNS-Challenge 的權重，kudio 與本程式都沒有隨附；面板會告訴你去哪裡取得。

### 到底哪裡有人在說話？

`trim_silence` 與 `split_on_silence` 只依音量切割，這在錄音室的錄音上可行，
但只要背景有風扇就會失效。雜訊超過某個程度之後，沒有任何固定的 `top_db`
能既保留輕聲的子音又去掉房間的聲音。

`kudio.vad` 以**這段錄音自己的底噪**為基準逐幀判斷，並用頻譜平坦度分辨人聲與嘶嘶聲。
它出現在兩個地方：**`vad_trim`** 效果，以及標記清單上方的 **`Find speech`**，
後者會把每個偵測到的區段變成可以逐一點選的標記。

### 分析視圖

對數功率 STFT、對數 mel 與 MFCC，使用的是訓練腳本也會用的同一組 `kudio` 呼叫——
控制項下方的說明文字就是確切的呼叫：

<img src="https://raw.githubusercontent.com/recklight/KudioStudio/master/docs/images/analysis_stft.zh-TW.png" width="270" alt="STFT 視圖">
<img src="https://raw.githubusercontent.com/recklight/KudioStudio/master/docs/images/analysis_mel.zh-TW.png" width="270" alt="Mel 視圖">
<img src="https://raw.githubusercontent.com/recklight/KudioStudio/master/docs/images/analysis_mfcc.zh-TW.png" width="270" alt="MFCC 視圖">

同一組圖也負責呈現兩樣*不是*這段音訊本身的東西：錄音進行中的滾動畫面，
以及畫在頻譜圖上的 f0 輪廓。兩者都在下面說明。

### 音量跑到哪裡去了？

<img src="https://raw.githubusercontent.com/recklight/KudioStudio/master/docs/images/analysis_loudness.zh-TW.png" width="600" alt="畫在波形上的短期響度">

Levels 讀數對整段音訊給出一個整合 LUFS 數值——那是你做正規化的依據，但對於找出
某人離麥克風遠了一點的那一刻毫無幫助。**Loudness over time** 會在波形上、以獨立的 LUFS 軸
畫出短期響度曲線（EBU Tech 3341，3 秒視窗，100 ms 跳距），所以「這裡是 -31 LUFS」
是一個你能據以行動的數字，而不是「大約三分之一高」。

上圖是一段音量漂移了 13 dB 又回來的錄音。Levels 讀數的結果一致：`range 13.0 LU`。

它不需要額外的量測。Levels 讀數本來就會為了報告掃過整段訊號，所以曲線、true peak 與範圍
都出自同一次掃描——一次量測得到四個答案，這也代表它們不可能描述的是某次編輯的四個不同時刻。

有兩點是刻意這樣設計的：

- **曲線不做閘控。** 閘控是為了不讓停頓拉低*整合*數值；一條在閘門觸發處被打出洞的曲線，
  描述的會是一段在字與字之間不存在的錄音。真正的靜音仍然會讓線條中斷，因為那裡的音量確實是零——
  也就是低於 BS.1770 的絕對閘限 -70 LUFS 的地方，聲音之後的停頓在那裡量到的只是加權濾波器衰減的尾巴。
- **比 3 秒視窗還短的音訊沒有曲線**，核取方塊會說明這一點，而不是打勾了卻什麼都沒發生。
  退而使用 400 ms 視窗，會在不知不覺間回答另一個問題。

**True peak** 與取樣峰值並列在 Levels 讀數中，兩者不一致的情況比你想的還常見。
樣本是一條曲線上的點，而這條曲線會從點與點之間穿過，並且會超出——所以一個沒有任何樣本達到
滿刻度的檔案，在轉換器或有損編碼器中仍可能削波。`audio_report` 看不到這一點（樣本本身沒有任何
問題），所以它以 dBTP 另外回報，並在常見的 -1 dBTP 交付上限發出警告。

### 同一段錄音的兩個檔案，相差八毫秒

Metrics 面板的每個指標都是拿參考訊號的第 *i* 個樣本與目前音訊的第 *i* 個樣本比較。
一段音訊拿**自己**評分會得到 +145 dB；延遲一個樣本就少掉 134 dB，而一毫秒的延遲得分
比完全不處理還差。產生的數字裡沒有任何東西能分辨「你的處理很差」與「這兩個沒對齊」，
而這兩者的意義正好相反。

所以面板**每次**都會用 `kudio.find_delay` 量測偏移、把它放進表格，並且除非你另行指定，
否則在評分前先把偏移去除：

```
Alignment          lags by 128 samples (8.0 ms), correlation 0.982
SI-SDR             +14.210 dB          ...instead of -7.890 dB
```

取消勾選 `Line up with the reference first`，它仍會回報偏移，並警告下方的分數主要是在量這個偏移。

當參考訊號看起來根本不是同一段錄音時，它會說明這一點，並且**不會**把兩者對齊——
把兩個無關的訊號對齊，會產生一個看起來很有把握、實際上毫無意義的數字，
這比一個明顯很差的數字更糟。

### 這是什麼音高？

頻譜圖會顯示諧波堆疊，但它不會告訴你*哪一條*線是基頻，而且一旦那條線很弱就幫不上忙了——
電話頻帶、高通濾波、低沉的聲音落在最前面幾個頻帶之下。

<img src="https://raw.githubusercontent.com/recklight/KudioStudio/master/docs/images/panel_pitch.zh-TW.png" width="270" alt="Pitch 面板">
<img src="https://raw.githubusercontent.com/recklight/KudioStudio/master/docs/images/analysis_pitch.zh-TW.png" width="480" alt="畫在頻譜圖上的 f0 輪廓">

**Pitch** 面板執行 `kudio.f0`（pYIN），回報中位數、以 Hz *及*半音表示的 5–95% 範圍——
半音才是人實際感知音高的單位——以及音訊中有多少比例是有聲的。輪廓畫在 STFT 視圖上，
**沒有音高的地方會留空**：問估計器「這裡的音高是多少」，它一定會回答，連靜音和 `s` 音也一樣，
而一條穿過這些答案連起來的線，畫的是不存在的東西。

有兩點是刻意這樣設計的：

- **搜尋範圍是一個具名的選擇**，而不是隱藏的預設值。低於 `fmin` 的聲音不會被回報為缺失——
  而是被回報成範圍內某個看似合理卻錯誤的值，所以預設選項會明確說出它們涵蓋的範圍。
- **在 mel 或 MFCC 圖上會隱藏輪廓**，因為在那裡「180」代表第 180 個頻帶。
  畫在錯誤位置的曲線比沒有曲線更糟。

`Voiced spans → markers` 會把結果轉成標記範圍，之後就能像其他標記一樣存在檔案旁，並匯出成標籤軌。

### 選擇降噪器，而不是用猜的

套用一種方法、聽一聽、復原、再試下一種——這樣最後留下的，只會是你剛好最後試的那一種。
**Compare** 面板會在完全相同的音訊、以完全相同的分幀跑過全部七種方法，逐一量測，
並讓你試聽任何一列而不必提交。

<img src="https://raw.githubusercontent.com/recklight/KudioStudio/master/docs/images/panel_compare.zh-TW.png" width="420" alt="Compare 面板">

這七種方法是同樣三個決策的不同組合——估計雜訊、估計先驗 SNR、把它轉成增益——
所以固定雜訊估計器就能單獨比較增益規則。**估計器與增益規則一樣是重要的槓桿**：
在完整的網格上量測，增益規則造成的平均 SI-SDR 差距為 5.8 dB，估計器則是 6.4 dB。
兩者都不佔絕對主導，這就是為什麼兩者都是控制項，也是為什麼面板可以掃描其中一個，或兩個都掃。

**兩個軸都能掃描。** 增強包含兩個決策——增益規則與雜訊估計器——所以面板可以在固定估計器下比較
增益規則、在固定增益規則下比較估計器，或跑完整的 7 × 4 網格。接著它會回報兩者之中哪一個對
*這段音訊*影響較大，或在差距只有 0.1 dB 時說兩者相當，而不是硬選出一個贏家。

把 `Learned model` 指向一個 [kudioenhance](https://pypi.org/project/kudioenhance/)
執行目錄，它也會得到一列，在同樣的音訊上以同樣的參考訊號評分——「模型真的比 log-MMSE 好嗎」
不是兩份分開的輸出能回答的問題。模型會在開始計時之前載入，並先在一小段音訊上跑一次，
之後保留給同一個執行目錄的下一次比較使用，所以它的 `ms` 是降噪本身——而不是匯入 PyTorch
與讀取 checkpoint 所花的那幾秒。保留的是*磁碟上當下*的那個執行結果：若又在同一個目錄重新訓練，
就會重新載入。

選取兩列以上，**`♪ Audition A→B`** 會把它們接連循環播放，每個接縫處有三分之一秒的靜音，
所以比較不會被一聲喀聲和一次滑鼠移動打斷。它們依**表格的順序**由上到下播放，與先點哪一列無關——
播放中重新排序會改變循環的順序，所以「上面那個聽起來比較好」是一句關於排名的話。
`Apply this one` 需要恰好一列未失敗的結果，並把它記錄成能從該段音訊與計算範圍重新算出結果的程式碼——
若比較執行期間音訊被編輯了，該次比較會被捨棄而不顯示，因為它的各列描述的是已經不存在的音訊。

面板會明確說出三件事，因為這三件事都很容易弄錯：

- **沒有乾淨的參考訊號，就沒有品質分數。** 剩下的只有殘留的底噪，而達到極低底噪最簡單的方法，
  就是把語音連同雜訊一起刪掉。排名只是一份候選名單；`♪ Audition` 才是最終判決。
- **拿含噪的原始音訊評分，量到的是你改動了音訊多少，而不是你有沒有改善它。**
  當每種方法的結果都比什麼都不做還差時，面板會直接告訴你這一點，而不是選出一個「好」了 −1 dB 的贏家。

- **這些方法適用於真正含噪的音訊。** 大約超過 10 dB SNR 時，它們帶來的失真比去除的雜訊代價更高，
  量測結果都會比不處理還差。

### 錄音時就聽到降噪效果

Record 面板的 **`monitor through`** 會在錄音進來時，讓它通過 `kudio.StreamEnhancer`——
即時顯示*降噪後*訊號的滾動波形，以及此刻正被去除多少的讀數。延遲為一個分析幀，預設為 32 ms。

錄音本身是以**原始**狀態提交的。監聽就是監聽，與 A/B 遵循同樣的規則：你聽到的是一個檢視，
而不是一次編輯。只有四種逐幀方法能串流處理；其他方法需要整張頻譜圖，所以放在 Enhance 中。

Enhance 面板的控制項是**由 `kudio.METHODS` 產生的**——範圍、預設值、單位與說明文字都來自函式庫，
所以函式庫新增的參數會直接出現在這裡，不需要任何人修改 GUI。

### 看著錄音進來

<img src="https://raw.githubusercontent.com/recklight/KudioStudio/master/docs/images/analysis_live.zh-TW.png" width="600" alt="錄音進行中的滾動頻譜圖">

波形監看從一開始就有；頻譜圖則沒有，因為每秒重算好幾次 STFT、只為了畫出一個早已捲走的視窗，
代價比它呈現的資訊還高。`kudio.SpectrogramStream` 正確地解決了這個問題：一個由欄位組成的
環形緩衝區，每一欄都只在其樣本到達時計算**一次**。

Record 面板每 50 ms 從錄音器取出一次資料，把新音訊全部送進轉換；每送進四次，頁面就收到一張畫面。
分析永遠不會被略過——那會遺失音訊——會被略過的永遠是繪圖。錄音中改了 `n_fft` 或視圖，畫面會重新開始，
而不是讓視窗左半用一種方式算、右半用另一種方式算。

有三個細節很容易出錯，而一旦出錯就看得出來：

- **用 `drain()`，絕不用 `tail()`。** 滑動視窗在兩次呼叫之間會重疊，所以每個重疊的樣本都會在
  兩個不同的時間點被畫兩次。
- **色彩範圍是絕對的**（每個頻帶的 dBFS），而不是從視窗中挑出來的。從一個每秒捲動二十次的東西
  重新計算出來的範圍，會是一個毫無意義、卻又一直在變的亮度。
- **兩張圖都靠右對齊在*現在*。** 它們共用時間軸，所以若視窗還在填滿時從零開始畫波形，
  同一個瞬間就會出現在兩個不同的位置。

開啟 `monitor through`，畫面就會是*降噪後*的串流，而錄音仍然以原始狀態提交。

### 學習式降噪

Enhance 面板開箱即可執行傳統 DSP。用 `pip install "kudiostudio[enhance]"` 裝好
[kudioenhance](https://pypi.org/project/kudioenhance/) 之後，面板也能載入它訓練出的模型：把 `run_dir` 指向一個執行目錄，
它就會取得模型、正規化統計量，以及訓練時使用的 STFT 參數——所以推論不可能在不知不覺中與訓練不一致。

<img src="https://raw.githubusercontent.com/recklight/KudioStudio/master/docs/images/panel_enhance.zh-TW.png" width="330" alt="選取了學習式模型的 Enhance 面板">

只有在真正使用該方法時才會匯入 PyTorch，若沒有安裝，面板會告訴你如何安裝，而不是直接失敗。
用它 0.1 版訓練的執行目錄早於改用 PyTorch，無法載入：面板會告訴你必須重新訓練。

### 資料集合成

指定一個乾淨音訊資料夾與一個雜訊資料夾，選好 SNR，它就會寫出混合音。`regular` 為每個乾淨檔案
輸出一份；`inc` 會寫出每一種 乾淨 × 雜訊 × SNR 的組合。設定 seed 後結果可以逐位元組重現——
同樣的雜訊偏移、同樣的配對。

### 把語料庫讀回來

<img src="https://raw.githubusercontent.com/recklight/KudioStudio/master/docs/images/panel_manifest.zh-TW.png" width="420" alt="Manifest 面板">

Synthesize 面板會在每個語料庫旁寫出 `manifest.json`，而其中一半的內容——房間、鏈路、目標音——
不在任何檔名裡。`File → Open manifest…`——或在命令列執行 `kudiostudio manifest.json`——
會用 `kudio.load_manifest` 讀取它並以列呈現：noisy、clean、noise、SNR、RT60、link 與 target。
任何欄位都可排序（SNR 與 RT60 依數值，而非文字），可依雜訊、SNR、房間與鏈路篩選，選取一列後：
**Open** 會像 `File → Open` 一樣載入它的 noisy 檔案以供編輯，而 **♪ noisy / ♪ clean / ♪ target**
會循環播放其中一邊，不必載入任何東西。

它能回答在用語料庫訓練任何東西之前值得先問的幾個問題：

- **每個檔案都還在嗎？** manifest 中的路徑常常是相對路徑——相對於當初執行合成的位置，
  而那個位置沒有被記錄下來。程式會先在目前目錄下尋找，再找 manifest 自己所在的資料夾及其上層的
  每個資料夾，也就是 `Synthesizer(..., out_path='data/mixed')` 的路徑起點——而且**整份 manifest
  會以其中一個資料夾為準來讀取**：第一個包含它列出的所有檔案的資料夾，若沒有，就是包含最多檔案的那個。
  絕不逐檔判斷：用同一批乾淨檔案建立的兩個專案，混合音的命名會很相似，逐路徑查找會把一個專案的
  混合音與另一個專案的乾淨檔案配在一起。摘要會說明使用的資料夾。在那裡找不到檔案的列會被標記並計數，
  有一個核取方塊可以只列出這些列——此時覆蓋格計算的是損壞發生在哪裡，而不代表語料庫的覆蓋情況。
- **這真的是 manifest 嗎？** kudio 讀取 manifest 時不會檢查數值；手動編輯過的 manifest 可能在
  SNR 的位置放了一個 list。這種情況會被拒絕，並指出是哪一列、哪個欄位，原本顯示的內容則保持不變。
- **雜訊 × SNR 網格都覆蓋到了嗎？** 表格下方的網格會計算每種組合顯示的列數，所以套用房間篩選時
  顯示的是該房間的覆蓋情況。缺口是紅色的格子，附註會說明是哪一種缺口：列數少於組合數，
  代表這是一個需要更多乾淨檔案的 `reg` 語料庫；列數足夠卻仍有缺口，則是任何單次執行都不會寫出的
  情況——有列遺失了，或這份 manifest 是兩次執行合併而成的。雙擊格子可列出它的各列。
  只有某列實際出現的 SNR 才會有一欄——manifest 記錄的是寫出了什麼，而不是當初要求了什麼。

讀取 manifest 與檢查磁碟都在工作執行緒上進行，而表格只建立畫面上可見的儲存格，
所以數萬列的語料庫也不會讓視窗卡住。

### 一整棵目錄樹調到 -23 LUFS，以及沒能達標的檔案

`kudio.convert_folder(lufs=-23)` 會對每個檔案套用增益，卻不說明結果如何。大家實際的工作是
「把這棵樹正規化，並告訴我哪些檔案沒能達標」，所以設定 LUFS 目標時，Batch 面板會
**把每個寫出的檔案讀回來**並量測——先 `kudio.loudness`，再 `kudio.true_peak`——
對照明訂的容許誤差（±1 LU，即 `kudio loudness --target` 允許的範圍）與 true peak 上限
（-1 dBTP，EBU R 128 的標準）。每個沒達標的檔案都會列出，並附上原因：

```
could not get there:
  in/b/short.wav               —  too short to measure — BS.1770 needs 0.4 s, so it was not written
  out/a/spiky.wav     -35.4 LUFS  clipped at full scale — -23 LUFS needs a peak above 0 dBFS; 12.4 LU short
  out/b/silent.wav             —  silent — there is no level to raise
on target, too little headroom:
  out/c/plosive.wav   -23.0 LUFS  true peak +0.5 dBTP, over the -1.0 dBTP ceiling
```

量測的是磁碟上的檔案，而不是交給 `save_wave` 的資料，因為出錯的地方正是磁碟：
定點格式的檔案會把超過滿刻度的部分全部削掉，所以一個被拉高音量的安靜檔案讀回來時峰值已被削平。
削波的檔案即使響度落在目標附近也會被列出——它是靠削波才達標的。`FLOAT` 樹會保留峰值，
並說明哪些檔案只有浮點檔案才裝得下。開啟降噪時，**音量調整會等降噪完成**：轉換時不動音量，
先跑降噪，再對每個檔案調整音量——降噪器在去除雜訊的同時也會拿掉一些音量，所以先調音量的樹
最後會落在要求的目標之下。

### 為整棵樹加上房間，並保留兩份

勾選 **Reverberant copy**，每個轉換後的檔案都會在一個房間中再寫出一次，存到另一棵結構相同的樹——
預設為 `<output>_reverberant`，位在輸出旁邊，絕不在輸出之內——所以乾聲檔案與它的濕聲副本可依路徑
配對：作為去殘響的輸入與目標，或同一語料庫的兩種條件。

每個檔案都有**屬於自己的房間**：同樣的 RT60 與 DRR，殘響尾巴則由該次執行的 seed 與檔案路徑決定，
就像 `kudio.Synthesizer` 為每個檔案產生房間的方式——在整個語料庫重複使用同一個房間，就只是一種
聲學條件，而用它訓練的模型學到的就是那個房間。相同的 seed 會重建相同的房間，隨機 seed 則在 Run
時抽出並回報。`apply_rir` 會保留長度與峰值，所以濕聲與乾聲保持樣本對齊；設定 LUFS 目標時，
副本也會被調整到目標並與其他檔案一起讀回檢查。

批次執行可能要花一段時間，而且中途無法停止。**在執行中結束程式不會中斷它**：視窗會保持開啟，
說明它在等什麼，並在工作完成時自行關閉——永遠不會留下寫到一半的目錄樹。

### 客觀指標

SNR、SI-SDR 與分段 SNR 是純 numpy 實作，永遠可用。PESQ、STOI 與 BSS-SDR 需要選用的後端：

```bash
pip install "kudio[eval]"      # STOI 與 BSS-SDR
pip install "kudio[pesq]"      # PESQ — 從原始碼編譯，需要 C 編譯器
```

PESQ 有兩種不同的尺度。kudio 預設回報 MOS-LQO；語音增強的論文大多引用原始的窄頻 P.862 分數，
而兩者不能互換——原始分數 2.03 大約等於 1.65 MOS-LQO。Metrics 面板會標明目前顯示的是哪一種，
並讓你切換。

面板會回報哪些後端已安裝，而不是直接失敗。

## 快捷鍵

| 按鍵 | 動作 |
|---|---|
| `Ctrl+O` / `Ctrl+Shift+S` | 開啟 / 另存新檔 |
| `Ctrl+Q` | 結束（等執行中的工作做完，絕不在中途） |
| `Ctrl+Shift+E` | 把選取範圍存成獨立檔案 |
| `Ctrl+Z` | 復原上一個編輯 |
| `Ctrl+X` / `Ctrl+C` / `Ctrl+V` / `Del` | 剪下 / 複製 / 貼上 / 刪除選取範圍 |
| `Ctrl+T` | 裁切至選取範圍 |
| `Ctrl+A` / `Esc` | 全選 / 清除選取 |
| `Space` | 播放 / 停止 |
| `Home` | 播放頭回到開頭 |
| `Ctrl+L` | 循環播放上次播放的範圍 |
| `Ctrl+B` | 與原始音訊做 A/B 比較 |
| `Ctrl+E` / `Ctrl+F` | 縮放至選取範圍 / 縮小至整段音訊 |
| `+` / `-` | 以播放頭為中心放大 / 縮小 |
| `←` / `→` | 沿著音訊捲動 |

在**任一張圖**上拖曳即可選取，或在 **Selection** 欄位輸入精確的範圍。點擊任何位置即可移動播放頭——
播放中點擊會從該處繼續播放。滾輪以游標為中心縮放，`Shift` + 滾輪或觸控板橫向滑動則是捲動。
波形與頻譜圖共用時間軸、選取範圍、播放頭與標記，所以你對其中一張做的任何操作都會顯示在另一張上。

只有在焦點不在可輸入文字、數字或選項的欄位時，按鍵才會送到視窗：視窗層級的 `Ctrl+C` 會讓
工具面板中的每個路徑欄位都無法複製，`Space` 則會讓你無法輸入含空格的檔名。

## 專案結構

```
kudiostudio/
├── __main__.py       # `kudiostudio`——web/app.py 的 main
├── session.py        # AudioSession — 音訊、復原、選取、剪貼簿、
│                     #   標記、接合交叉淡化、A/B、響度對齊，
│                     #   以及以腳本形式呈現的編輯歷程
├── events.py         # AudioSession 宣告變更所用的訊號
├── tools/            # 各工具的控制項、檢查、呼叫與文字：
│                     #   effects、enhance、compare、record、synth、manifest、
│                     #   pitch、metrics、batch
├── measure.py        # Levels 讀數：報告、true peak、響度曲線
├── i18n.py           # 翻譯，搭配 locales/zh_TW.py
├── plugins.py        # 其他套件透過 entry point 加入的面板
├── theme.py          # 色彩，頁面以 CSS 變數讀取
├── shapes.py         # 線條圖示，以 SVG 表示
├── assets/           # 視窗與工作列圖示，與 kudio 共用同一個標誌
└── web/
    ├── app.py        # 視窗：先開本機伺服器，再用 pywebview 開在它上面
    ├── server.py     # 只接受 127.0.0.1、每次執行一組 token；提供頁面與資料
    ├── studio.py     # Studio — 視窗的指令、一個工作階段、播放
    ├── editing.py    ├── markers.py    ├── monitor.py    ├── analysis.py
    ├── jobs.py       # 每個面板一個工作，不佔用伺服器的執行緒
    ├── panels/       # 各工具的 Python 那一半
    └── static/       # 頁面：index.html、css/、js/ 與 js/panels/
```

頁面是套件裡直接提供的純 HTML、CSS 與 JavaScript 模組：沒有建置步驟，也沒有打包工具，
所以你讀到的就是實際執行的程式碼。它透過同一條路徑 `POST /api/<command>` 向 Python 端要所有東西，
波形與頻譜圖以二進位的形式只取畫面上的那一段，變更則以 server-sent events 送來。
伺服器只回應 `127.0.0.1` 上帶著這次執行 token 的請求，所以別的瀏覽器裡的別的網頁無法操作它。

每個工具分成兩半。Python 那一半是一個 `WebPanel`，它的 `@command` 方法由頁面以 `<id>.<name>`
呼叫；頁面那一半是一個模組，用 `ui.js` 建出控制項並呼叫這些指令。兩者都依靠 `tools/`，
各工具顯示的參數、檢查與 `kudio` 呼叫都只寫在那裡一次。面板之間從不直接溝通：它們操作共用的
`AudioSession`，視窗在它變更時重繪。

不適合放進這個專案的工具，面板也可以放在它自己的套件裡。那個套件在 `kudiostudio.panels`
這個 entry point 群組下登記它的 `WebPanel` 子類別，視窗就會把它放上工具側欄，排在內建工具下方的分隔線之後：

```toml
[project.entry-points."kudiostudio.panels"]
spectra = "mypackage.web:SpectraPanel"
```

類別要說明它的頁面模組在哪裡：`static_dir` 是套件裡的一個資料夾，視窗以 `/plugins/<id>/` 提供它，
`script` 則是其中的模組。這個模組和內建的一樣掛載，用同一組控制項、以 `ctx.call` 呼叫自己的指令，
結果需要的空間比面板欄大時，可以用 `ctx.view` 在圖上蓋一個自己的檢視。載入時還會讀兩個類別屬性：
`icon_svg` 是側欄圖示，以 SVG 內容畫在 `shapes.py` 用的 24 格座標上；`messages` 是它的翻譯，
格式與 `locales/zh_TW.py` 相同。匯入或建構失敗的外掛不會出現在側欄上，狀態列會指出是哪一個，
視窗照常開啟；`--no-plugins` 則完全不載入外掛。

## 測試

```bash
pytest -q
```

774 個測試，涵蓋：工作階段的復原/選取/剪貼簿/標記運算；匯出的編輯腳本在另一個直譯器中執行時，
能得到與工作階段完全相同的樣本；一份來自真實合成執行的 manifest，不論視窗從哪裡啟動都能找到它的檔案
並計算覆蓋情況；正規化後讀回的目錄樹，每個沒達標的檔案都以正確的原因列出；殘響副本完全等於以 seed
產生的房間；不會延遲音訊的量測房間；響度對齊；播放頭的時序；預覽與播放的循環；同一段音訊編輯時保持不變的
色階與振幅軸；比較表的排名，以及在參考訊號不乾淨時拒絕選出贏家；學習式模型那一列的計時不含載入模型的那幾秒；
即時畫面保證每一欄只計算一次；音高輪廓拒絕繪製的位置；各面板的指令；伺服器拒絕其他來源與其他 token；
其他套件的面板放上側欄，或在載入失敗時被略過並說明原因；頁面上每一句都有中文；以及整個程式在工作進行中
也能乾淨地結束，這一項在獨立的直譯器中執行。測試完全不需要螢幕：Python 端透過視窗自己的指令驅動，
決定哪些控制項變灰、選了什麼、什麼時候要重新要資料的頁面模組，則在 `node` 下對著替身文件執行，
沒有安裝 `node` 時會略過。學習式降噪的測試（標記為 `learned`）會真的訓練一個 epoch 的模型，
並在 kudioenhance 或 PyTorch 未安裝或無法匯入時乾淨地略過——除非設定了 `KUDIOSTUDIO_REQUIRE_LEARNED=1`，
此時會改為失敗，供那些專門安裝了兩者來跑這些測試的 CI 工作使用。無限長度的錄音器則透過假的裝置驅動，
因為 CI 沒有麥克風。

`tools/smoke.py` 是另一半：在單一程序中執行 162 項端對端檢查，涵蓋單元測試無法涵蓋的部分——
真實檔案、非預設的取樣率、長達一分鐘的輸入、`kudio` 命令列，以及驅動真實工作的視窗伺服器。
任何一項失敗都會以非零狀態碼結束。

README 的截圖是程式產生的，不是手動擷取的——修改視窗之後請重新執行
`python tools/screenshots.py`（中文 README 的截圖加上 `--lang zh_TW`），並提交有變動的圖片。
它用無頭模式的 Chrome、Chromium 或 Edge 畫出視窗，裝了哪一個就用哪一個。

## 相關專案

- **[kudio](https://pypi.org/project/kudio/)** — 底層的音訊工具組：I/O、特徵、效果、合成、增強、指標。
- **[kudioenhance](https://pypi.org/project/kudioenhance/)** — 訓練本程式所載入的降噪模型。

## 授權

MIT — 請見 [LICENSE.txt](https://github.com/recklight/KudioStudio/blob/master/LICENSE.txt)。
