# -*- coding: utf-8 -*-
"""Traditional Chinese (Taiwan) translations of the interface text.

Keys are the English strings exactly as the code writes them; an
f-string is keyed by its template, with ``{}`` where each value goes.
A translation that needs the values in another order numbers them
(``{1} ... {0}``). Anything missing here is shown in English.
"""

MESSAGES = {
    'Desktop workbench for the kudio audio toolkit.':
        'kudio 音訊工具組的桌面工作台。',
    'audio files to open on start-up; a corpus manifest (.json) opens in the Manifest panel':
        '啟動時要開啟的音訊檔；語料庫 manifest（.json）會在 Manifest 面板中開啟',
    'debug logging':
        '除錯記錄',
    'A/B — the original, {} LU against the edit; turn on Match loudness to compare fairly':
        'A/B — 原始音訊，比編輯版本 {} LU；開啟「響度對齊」才能公平比較',
    'Files':
        '檔案',
    'Markers':
        '標記',
    '&File':
        '檔案(&F)',
    '&Open…':
        '開啟(&O)…',
    'Open &manifest…':
        '開啟 manifest(&M)…',
    'Save &As…':
        '另存新檔(&A)…',
    'Save &selection as…':
        '將選取範圍另存為(&S)…',
    '&Export analysis image…':
        '匯出分析圖(&E)…',
    'Export edits as &Python script…':
        '將編輯匯出為 &Python 腳本…',
    '&Quit':
        '結束(&Q)',
    '&Edit':
        '編輯(&E)',
    '&Undo':
        '復原(&U)',
    '&Revert to original':
        '還原為原始音訊(&R)',
    'Cu&t\tCtrl+X':
        '剪下(&T)\tCtrl+X',
    '&Copy\tCtrl+C':
        '複製(&C)\tCtrl+C',
    '&Paste\tCtrl+V':
        '貼上(&P)\tCtrl+V',
    '&Delete\tDel':
        '刪除(&D)\tDel',
    'Crop to selectio&n':
        '裁切至選取範圍(&N)',
    '&Insert silence…':
        '插入靜音(&I)…',
    'Splice cross&fade…':
        '接合交叉淡化(&F)…',
    'Select &All':
        '全選(&A)',
    '&Clear selection':
        '清除選取(&C)',
    '&Playback':
        '播放(&P)',
    '&Play / Stop\tSpace':
        '播放 / 停止(&P)\tSpace',
    'Playhead to &start\tHome':
        '播放頭回到開頭(&S)\tHome',
    '&Loop':
        '循環(&L)',
    '&A/B compare original':
        '與原始音訊 &A/B 比較',
    '&View':
        '檢視(&V)',
    'Zoom to &selection':
        '縮放至選取範圍(&S)',
    'Zoom &out to whole clip':
        '縮小至整段音訊(&O)',
    '&Help':
        '說明(&H)',
    'Open audio':
        '開啟音訊',
    'Open a corpus manifest':
        '開啟語料庫 manifest',
    'Save audio as':
        '音訊另存為',
    'WAV (*.wav)':
        'WAV 檔 (*.wav)',
    'Save selection as':
        '選取範圍另存為',
    'Export analysis image':
        '匯出分析圖',
    'PNG (*.png)':
        'PNG 圖片 (*.png)',
    'Exported {}':
        '已匯出 {}',
    'Export edits as Python script':
        '將編輯匯出為 Python 腳本',
    'Python (*.py)':
        'Python 腳本 (*.py)',
    'Exported {} edit(s) to {}':
        '已將 {} 個編輯匯出到 {}',
    'Insert silence':
        '插入靜音',
    'Seconds:':
        '秒數：',
    'Splice crossfade':
        '接合交叉淡化',
    'Milliseconds blended at each cut and paste.\n0 disables it, at the cost of a click where two pieces meet.':
        '每次剪下與貼上時混合的毫秒數。\n設為 0 即停用，代價是兩段接合處會出現喀聲。',
    'A/B — monitoring the edit':
        'A/B — 監聽編輯版本',
    'A/B — the original, level as-is (too short to measure loudness)':
        'A/B — 原始音訊，音量維持原樣（太短，無法量測響度）',
    'A/B — the original, levelled to the edit ({} LU applied)':
        'A/B — 原始音訊，已對齊編輯版本的響度（套用 {} LU）',
    'Plugin: {}':
        '外掛：{}',
    'Plugin {} was left out — {}':
        '外掛 {} 未載入 — {}',
    'ValueError: it has no id':
        'ValueError: 它沒有 id',
    'ValueError: its id {} is already on the rail':
        'ValueError: 它的 id {} 已經在工具列上',
    "ValueError: its id {} names the window's own commands":
        'ValueError: 它的 id {} 是視窗本身指令的名稱',
    'Record over the open clip?':
        '要錄音取代目前開啟的音訊嗎？',
    'It replaces the clip that is open, and what no file holds goes with it: its edits since it was last saved, or the whole of a take never written to a file.':
        '目前開啟的音訊會被取代，沒有任何檔案保存的部分會跟著不見：上次儲存之後的編輯，或從沒寫成檔案的整段錄音。',
    'Open another file?':
        '要開啟另一個檔案嗎？',
    '&User guide':
        '使用說明(&U)',
    'The user guide opens in your browser: {}':
        '使用說明會在瀏覽器開啟：{}',
    '&About':
        '關於(&A)',
    'Nothing to save.':
        '沒有可儲存的內容。',
    'Select a range first — Ctrl+A selects everything.':
        '請先選取一個範圍 — Ctrl+A 可全選。',
    'Nothing to export.':
        '沒有可匯出的內容。',
    'Could not write {}':
        '無法寫入 {}',
    'No edits to export yet.':
        '目前還沒有可匯出的編輯。',
    ' (and {} more, listed at the top of the script)':
        '（另有 {} 項，列在腳本開頭）',
    'Exported {} edit(s) to {}, but it will not reproduce them as it stands — {}{}':
        '已將 {} 個編輯匯出到 {}，但以目前狀態無法重現這些編輯 — {}{}',
    'Select a range to cut — Ctrl+A selects everything.':
        '請選取要剪下的範圍 — Ctrl+A 可全選。',
    'Select a range to copy.':
        '請選取要複製的範圍。',
    'The clipboard is empty.':
        '剪貼簿是空的。',
    'Select a range to delete.':
        '請選取要刪除的範圍。',
    'Splices now crossfade over {} ms.':
        '接合處現在會以 {} ms 交叉淡化。',
    'Splices are now hard cuts — expect a click at every join.':
        '接合處現在是硬切 — 每個接點都可能出現喀聲。',
    '{}  ·  {}s  ·  {} Hz  ·  {} samples':
        '{}  ·  {} 秒  ·  {} Hz  ·  {} 個樣本',
    'Quitting when {} finishes — a running job cannot be stopped half-way.':
        '將在{}完成後結束 — 執行中的工作無法中途停止。',
    'Quitting when {} and {} finish — a running job cannot be stopped half-way.':
        '將在{}與{}完成後結束 — 執行中的工作無法中途停止。',
    'Stopping when {} finishes — a running job cannot be stopped half-way. Ctrl+C again stops now, with the job unfinished.':
        '將在{}完成後停止 — 執行中的工作無法中途停止。再按一次 Ctrl+C 會立刻停止，工作做到哪裡就停在哪裡。',
    'Stopping when {} and {} finish — a running job cannot be stopped half-way. Ctrl+C again stops now, with the jobs unfinished.':
        '將在{}與{}完成後停止 — 執行中的工作無法中途停止。再按一次 Ctrl+C 會立刻停止，工作做到哪裡就停在哪裡。',
    'Could not open {} — {}: {}':
        '無法開啟 {} — {}: {}',
    'Save failed — {}: {}':
        '儲存失敗 — {}: {}',
    'Export failed — {}: {}':
        '匯出失敗 — {}: {}',
    'Load an audio file first.':
        '請先載入音訊檔。',
    'Batch':
        '批次',
    '{}/{} file(s) written to {}':
        '已將 {}/{} 個檔案寫入 {}',
    'Batch complete — {}/{} written, {} failed':
        '批次完成 — 已寫入 {}/{} 個，{} 個失敗',
    'Batch complete — {}/{} written, {} failed.':
        '批次完成 — 已寫入 {}/{} 個，{} 個失敗。',
    'Batch complete — {}/{} written, {} failed, {} missed the loudness target.':
        '批次完成 — 已寫入 {}/{} 個，{} 個失敗，{} 個未達響度目標。',
    '{} LU over':
        '高了 {} LU',
    '{} LU under':
        '低了 {} LU',
    'Reverberant copies: {}/{} written to {} — each file its own room, from seed {}; the same seed makes the same rooms again.':
        '殘響副本：已將 {}/{} 個寫入 {} — 每個檔案各有自己的房間，seed 為 {}；相同的 seed 會再產生相同的房間。',
    'within ±{} LU of {} LUFS, true peak at or under {} dBTP':
        '在 {1} LUFS 的 ±{0} LU 內，true peak 不超過 {2} dBTP',
    'Loudness, read back from disk: {} of {} file(s) {}. {} — listed below.':
        '從磁碟讀回的響度：{} / {} 個檔案{}。{} — 列於下方。',
    'a reverberant pass needs a seed; draw one first':
        '殘響處理需要 seed；請先抽一個',
    'too short to measure — {} s, and BS.1770 needs {} s':
        '太短無法量測 — {} 秒，而 BS.1770 需要 {} 秒',
    'silent — there is no level to raise':
        '靜音 — 沒有音量可以拉高',
    '; {} LU short':
        '；差 {} LU',
    'clipped at full scale — {} LUFS needs a peak above 0 dBFS{}':
        '在滿刻度削波 — {} LUFS 需要高於 0 dBFS 的峰值{}',
    'peaks at {} dBFS — kept in a float file, clipped in any fixed-point copy':
        '峰值 {} dBFS — 浮點檔案可保留，任何定點格式的副本都會削波',
    'true peak {} dBTP, over the {} dBTP ceiling':
        'true peak {} dBTP，超過 {} dBTP 上限',
    'input folder':
        '輸入資料夾',
    'output folder':
        '輸出資料夾',
    'downmix to mono':
        '混為單聲道',
    'trim leading/trailing silence':
        '修剪開頭與結尾的靜音',
    'Peak scales by the loudest sample, which says nothing about how\nloud a file sounds: one stray transient normalises a whole clip\nto a whisper. LUFS (ITU-R BS.1770) levels by perceived loudness,\nwhich is what a dataset of mixed sources actually needs.':
        '峰值正規化依最大的樣本縮放，這與檔案聽起來多大聲無關：\n一個突發的瞬態就會把整段音訊正規化成耳語。LUFS（ITU-R BS.1770）\n依感知響度調整，這才是混合來源的資料集真正需要的。',
    'How far from the target a file may land and still count as on\nit. 1 LU is what `kudio loudness --target` allows.':
        '檔案落點離目標多遠仍算達標。\n1 LU 是 `kudio loudness --target` 允許的範圍。',
    "The true-peak ceiling. A quiet file levelled up can be at the\ntarget with its waveform overshooting full scale between the\nsamples, and that clips in the next converter or codec it meets.\n-1 dBTP is EBU R 128's ceiling.":
        'true peak 上限。被拉高音量的安靜檔案可能響度達標，\n但波形在樣本之間超出滿刻度，遇到下一個轉換器或編解碼器時就會削波。\n-1 dBTP 是 EBU R 128 的上限。',
    'Denoise every file on the way through (kudio.enhance_folder).\nA separate pass from the conversion, because it reads the whole\nfile to estimate the noise — and because a folder of already-clean\naudio comes out worse, not better.':
        '在處理過程中為每個檔案降噪（kudio.enhance_folder）。\n這是與轉換分開的一輪，因為它要讀取整個檔案來估計雜訊 —\n而且一個本來就乾淨的資料夾，處理後只會更差，不會更好。',
    'Reverberant copy — keep the dry tree too':
        '殘響副本 — 同時保留乾聲樹',
    'Write every converted file again, in a room, to a second tree\nwith the same layout. The dry files stay where they are, so the\ntwo pair by path: input and target for dereverberation, or two\nconditions of one corpus.':
        '把每個轉換後的檔案在一個房間中再寫一次，存到結構相同的\n第二棵樹。乾聲檔案留在原處，所以兩者可依路徑配對：\n作為去殘響的輸入與目標，或同一語料庫的兩種條件。',
    'How long the tail takes to fall 60 dB. A treated studio is under\n0.3 s, a living room around 0.5, a hall over 1.5.':
        '殘響尾巴衰減 60 dB 所需的時間。處理過的錄音室低於 0.3 秒，\n客廳約 0.5 秒，音樂廳超過 1.5 秒。',
    'Direct-to-reverberant ratio — distance, in a readable unit.\nA close microphone is +10 dB or more; a metre or two away is\naround 0; the far end of a hall is negative.':
        '直達聲與殘響比 — 以容易理解的單位表示距離。\n近距離麥克風為 +10 dB 以上；一兩公尺外約為 0；\n音樂廳的遠端則是負值。',
    "Every file gets a room of its own, drawn from this seed and the\nfile's path, so the same seed rebuilds the same rooms. Random\ndraws one at Run, and the result says which.":
        '每個檔案都有自己的房間，由這個 seed 與檔案路徑決定，\n所以相同的 seed 會重建相同的房間。選擇隨機時會在執行時\n抽一個，結果中會註明是哪一個。',
    'beside the output: <output>{}':
        '位於輸出旁：<output>{}',
    'Run':
        '執行',
    'Batch conversion':
        '批次轉換',
    'Converting …':
        '轉換中…',
    'Noise floor down {} dB on average — a measurement of what was removed, not of whether it sounds better. Open one and listen.':
        '底噪平均降低 {} dB — 這是量測去除了多少，不代表聽起來更好。請開一個來聽聽看。',
    'Levelled to {} LUFS after the denoise pass, which takes level out with the noise.':
        '已在降噪之後調整到 {} LUFS，因為降噪會連同雜訊拿掉一些音量。',
    'Peak-normalised to {} after the denoise pass, which takes level out with the noise.':
        '已在降噪之後把峰值正規化到 {}，因為降噪會連同雜訊拿掉一些音量。',
    ', {} missed the loudness target':
        '，{} 個未達響度目標',
    'Loudness, read back from disk: all {} file(s) {}.':
        '從磁碟讀回的響度：全部 {} 個檔案{}。',
    'Batch failed — {}':
        '批次失敗 — {}',
    'too short to measure — BS.1770 needs {} s, so it was not written':
        '太短無法量測 — BS.1770 需要 {} 秒，因此沒有寫出',
    'could not be read back — {}':
        '無法讀回 — {}',
    'true peak ≤':
        'true peak ≤',
    # the Batch form's own labels; sr, subtype, top_db and the rest are
    # kudio's names and stay as they are
    'input':
        '輸入',
    'output':
        '輸出',
    'check':
        '檢查',
    'within':
        '容許',
    'to':
        '存到',
    "Output mirrors the input's folder structure, so two files with the same name in different subfolders do not collide. Existing files are renamed rather than overwritten (kudio.check_file).":
        '輸出會保留輸入的資料夾結構，所以不同子資料夾中的同名檔案不會互相衝突。既有檔案會被重新命名而不是覆寫（kudio.check_file）。',
    'With a LUFS target every written file is read back and measured, and the ones that did not get there are listed with the reason.':
        '設定 LUFS 目標時，每個寫出的檔案都會被讀回並量測，未達標的檔案會連同原因列出。',
    'Running…':
        '執行中…',
    'Select a valid input folder.':
        '請選擇有效的輸入資料夾。',
    'Select an output folder.':
        '請選擇輸出資料夾。',
    'The reverberant copies need a folder of their own — not the output folder, nor one inside it or around it.':
        '殘響副本需要自己的資料夾 — 不能是輸出資料夾，也不能在它裡面或包住它。',
    'A batch job is already running.':
        '已有批次工作在執行中。',
    '… and {} more':
        '… 另有 {} 個',
    '{} failed:':
        '{} 個失敗：',
    '{} could not get there':
        '{} 個未能達標',
    '{} got there with too little headroom':
        '{} 個達標但餘裕不足',
    '{} could not get there and {} got there with too little headroom':
        '{} 個未能達標，{} 個達標但餘裕不足',
    'could not get there':
        '未能達標',
    'on target, too little headroom':
        '已達標，但餘裕不足',
    'could not get there:':
        '未能達標：',
    'on target, too little headroom:':
        '已達標，但餘裕不足：',
    '\n\n{} failed:\n':
        '\n\n{} 個失敗：\n',
    ' and ':
        ' 與',
    'Add files…':
        '加入檔案…',
    'Remove':
        '移除',
    'Clear':
        '清除',
    'Added {} file(s).':
        '已加入 {} 個檔案。',
    'none — a real recording has no clean version':
        '無 — 真實錄音沒有乾淨版本',
    "the original, before this session's edits":
        '原始音訊（本次工作階段編輯之前）',
    'a file…':
        '檔案…',
    'gain rules, one noise estimator':
        '增益規則（固定一種雜訊估計器）',
    'noise estimators, one gain rule':
        '雜訊估計器（固定一種增益規則）',
    'everything — the full grid':
        '全部 — 完整網格',
    'floor dBFS':
        '底噪 dBFS',
    'Compare':
        '比較',
    'The {} moved this clip more than the {} did ({} dB against {} dB).':
        '這段音訊受{}的影響比{}大（{} dB 對 {} dB）。',
    'no reference file selected':
        '未選擇參考檔案',
    'Browse…':
        '瀏覽…',
    'Which axis to vary. Enhancement is two choices — a gain rule and a noise estimator — and which of them moves the result more depends on the recording; the full grid measures both and says which it was for this clip.':
        '要變動哪一個軸。增強包含兩個選擇 — 增益規則與雜訊估計器 — 哪一個對結果影響較大要看錄音；完整網格兩者都會量，並說明這一段是哪一個。',
    'optional: a kudioenhance run directory':
        '選用：kudioenhance 的執行目錄',
    "Rank a trained model in the same table as the statistical methods. 'Is the model actually better than log-MMSE' is not a question two separate printouts answer.":
        '把訓練好的模型與統計式方法排在同一張表裡。「模型真的比 log-MMSE 好嗎」不是兩份分開的輸出能回答的問題。',
    'PESQ / STOI (slow; STOI needs kudio[eval], PESQ needs kudio[pesq])':
        'PESQ / STOI（較慢；STOI 需要 kudio[eval]，PESQ 需要 kudio[pesq]）',
    'compare only the selected range':
        '只比較選取範圍',
    'Faster, and lets you judge one difficult passage. Noise estimation sees only that range, which is a real difference in the result.':
        '較快，也能讓你評判某一段困難的片段。雜訊估計只看得到那個範圍，這會讓結果確實不同。',
    'Run comparison':
        '執行比較',
    '♪  Audition':
        '♪  試聽',
    'Apply this one':
        '套用這一個',
    'Save all…':
        '全部儲存…',
    'Reference':
        '參考訊號',
    'File':
        '檔案',
    'Gain rule':
        '增益規則',
    'Noise estimate':
        '雜訊估計',
    'Learned model':
        '學習式模型',
    'Select the clean reference':
        '選擇乾淨的參考訊號',
    'Running {} combination(s) …':
        '正在執行 {} 種組合…',
    'Comparison failed — {}':
        '比較失敗 — {}',
    'Comparison finished — {}/{} combination(s) succeeded.':
        '比較完成 — {}/{} 種組合成功。',
    'The gain rule and the noise estimator moved this clip about equally ({} dB against {} dB), so neither is the one to spend the effort on.':
        '增益規則與雜訊估計器對這段音訊的影響差不多（{} dB 對 {} dB），所以兩者都不特別值得花力氣。',
    'noise estimator':
        '雜訊估計器',
    'gain rule':
        '增益規則',
    'Applied {}. A/B it against the original with Ctrl+B.':
        '已套用 {}。用 Ctrl+B 與原始音訊做 A/B 比較。',
    'Save every result to':
        '將所有結果儲存到',
    'Wrote {} file(s) to {}':
        '已將 {} 個檔案寫入 {}',
    'Select a kudioenhance run directory':
        '選擇 kudioenhance 執行目錄',
    'Every method sees identical audio and identical framing, so the differences are the methods. Click a row to hear it — a low noise floor is easiest to reach by deleting the speech too.\n\nThese are for noisy audio. The cleaner the recording, the less noise there is to take out and the more the distortion a method adds can cost: on a clean one the weaker methods measure worse than doing nothing, and with a clean reference the ΔSNR column shows which. They also need to hear the noise on its own: a clip with no pauses gives the noise estimator almost nothing to work from.':
        '每種方法看到的音訊與分幀完全相同，所以差異都來自方法本身。點一列即可試聽 — 要達到很低的底噪，最簡單的方式就是連語音一起刪掉。\n\n這些方法適用於含噪音訊。錄音越乾淨，可去除的雜訊越少，方法本身帶來的失真代價就越明顯：在乾淨的錄音上，較弱的方法量起來會比不處理還差；有乾淨的參考訊號時，ΔSNR 那一欄會顯示是哪幾個。它們也需要聽到單獨的雜訊：沒有停頓的音訊幾乎無法讓雜訊估計器發揮。',
    'Comparing…':
        '比較中…',
    'This session has no original to compare against.':
        '此工作階段沒有可比較的原始音訊。',
    'Too short to compare — select at least 512 samples.':
        '太短無法比較 — 請選取至少 512 個樣本。',
    'A comparison is already running.':
        '已有比較在執行中。',
    'The audio changed while the comparison ran, so its results describe audio that is no longer there — run it again.':
        '比較執行期間音訊被改動了，所以結果描述的是已不存在的音訊 — 請重新執行。',
    'Every method failed.':
        '所有方法都失敗了。',
    'Comparison finished — nothing succeeded.':
        '比較完成 — 沒有任何一項成功。',
    'No reference, so there is no quality score. The quietest residue is <b>{} + {}</b> at {} dBFS — which is also what you get by deleting the speech. Audition before believing it.':
        '沒有參考訊號，所以沒有品質分數。殘留最小的是 <b>{} + {}</b>，為 {} dBFS — 這也正是把語音刪掉會得到的結果。相信之前請先試聽。',
    'Select one row to apply it.':
        '請選一列來套用。',
    '♪  Audition A→B':
        '♪  試聽 A→B',
    'Auditioning {} — nothing has been committed.':
        '試聽中：{} — 尚未提交任何變更。',
    'Select a reference file first.':
        '請先選擇參考檔案。',
    '<b>No method beat leaving it alone</b> — the closest was {} + {} at {} dB ΔSNR. That usually means the reference is not clean: scoring against the noisy recording measures how much you changed it, not whether you improved it.':
        '<b>沒有任何方法勝過不處理</b> — 最接近的是 {} + {}，ΔSNR 為 {} dB。這通常表示參考訊號不乾淨：拿含噪錄音評分，量到的是你改動了多少，而不是有沒有改善。',
    ' — nothing has been committed.':
        ' — 尚未提交任何變更。',
    'Saved {} of {} — {}: {}':
        '已儲存 {} / {} — {}: {}',
    'Best against the reference: <b>{} + {}</b> at {} dB SI-SDR, {} dB better than leaving it alone.':
        '相對於參考訊號最佳：<b>{} + {}</b>，SI-SDR {} dB，比不處理好 {} dB。',
    'Best against the reference: <b>{} + {}</b> at {} dB SI-SDR, though at {} dB ΔSNR it did not beat leaving it alone.':
        '相對於參考訊號最佳：<b>{} + {}</b>，SI-SDR {} dB，但 ΔSNR 為 {} dB，沒有勝過不處理。',
    'Best against the reference: <b>{} + {}</b> at {} dB SI-SDR.':
        '相對於參考訊號最佳：<b>{} + {}</b>，SI-SDR {} dB。',
    '<b>{} + {}</b> beat leaving it alone by the most, {} dB ΔSNR.':
        '勝過不處理最多的是 <b>{} + {}</b>，ΔSNR {} dB。',
    'No learned row: {} is not a folder.':
        '沒有學習式模型那一列：{} 不是資料夾。',
    'Auditioning ':
        '試聽中：',
    'Effects':
        '效果',
    '{} s at {} Hz, direct sound at {} ms — applied from there':
        '{} 秒，{} Hz，直達聲在 {} ms — 從那裡開始套用',
    '{} s at {} Hz, direct sound at {} ms':
        '{} 秒，{} Hz，直達聲在 {} ms',
    '♪  Preview':
        '♪  預覽',
    'Hear the effect on the selection without applying it.\nLoops, and re-renders as you change the parameters.':
        '試聽效果套用在選取範圍上的結果而不實際套用。\n會循環播放，並在你調整參數時重新算繪。',
    'Apply':
        '套用',
    'Undo':
        '復原',
    'Revert to original':
        '還原為原始音訊',
    'Effect':
        '效果',
    'zero-phase (no time shift)':
        '零相位（無時間偏移）',
    ' semitones':
        ' 半音',
    'The same seed is the same room, so a dataset built from it can\nbe rebuilt. -1 draws a new one each time.':
        '相同的 seed 就是相同的房間，所以用它建立的資料集可以重建。\n-1 表示每次都抽一個新的。',
    'pick an impulse response .wav …':
        '選擇脈衝響應 .wav …',
    'start at the direct sound':
        '從直達聲開始',
    'A measured response begins with the time the sound took to reach\nthe microphone. Left in, it delays the audio by that much, and a\nprocessed selection comes back late against its neighbours.\nThis starts the response at its loudest sample, which is the\ndirect sound in any ordinary room -- where kudio.rir puts it.':
        '量測到的響應開頭是聲音傳到麥克風所花的時間。\n若保留，音訊會被延遲同樣的時間，處理過的選取範圍會比前後晚出現。\n這個選項讓響應從最大的樣本開始，在一般房間中那就是直達聲\n — 也就是 kudio.rir 放置它的位置。',
    'G.711 companding. `none` band-limits and resamples without it,\nwhich separates what the line did from what the codec did.':
        'G.711 壓擴。`none` 只做頻帶限制與重新取樣而不壓擴，\n可以把線路造成的影響與編解碼器造成的影響分開。',
    'Add triangular dither, two steps wide, before rounding. It raises\nthe noise floor and decorrelates the error from the signal,\nwhich is what stops quiet passages sounding granular.':
        '在量化前加入兩個步階寬的三角形抖動。它會提高底噪，\n並讓誤差與訊號去相關，這能避免安靜段落聽起來有顆粒感。',
    'Fraction of packets to drop. Achieved exactly, so a corpus\nlabelled 5% carries 5%.':
        '要丟棄的封包比例。會精確達成，所以標示 5% 的語料庫\n就真的有 5%。',
    'Packet length. 20 ms is G.711 over RTP. The loss rate is a\nfraction of packets, so this changes whether the damage arrives\nas many short holes or a few long ones — not how much of it.':
        '封包長度。20 ms 是 RTP 上的 G.711。遺失率是封包的比例，\n所以這改變的是損壞以許多短洞還是少數長洞的形式出現 —\n而不是損壞的總量。',
    ' pkt':
        ' 個封包',
    'Mean run length. Real links lose packets in runs — a fade, a\nqueue overflowing — and a run is far harder to conceal than the\nsame packets scattered about, because there is no recent speech\nleft to repeat.\n\n`independent` is not runs of 1: independent draws land side by\nside sometimes, so they already average 1/(1-loss) — 1.05\npackets at 5%. Asking for 1 is asking for something less\nclustered than leaving it alone.':
        '平均連續長度。真實鏈路會成串遺失封包 — 訊號衰落、佇列溢位 —\n而一串遺失遠比同樣數量的分散遺失難以補償，\n因為已經沒有最近的語音可以重複。\n\n`independent` 並不是長度 1：獨立的抽樣有時會相鄰，\n所以平均已是 1/(1-loss) — 在 5% 時為 1.05 個封包。\n要求 1 等於要求比不設定還要分散。',
    "`hold` repeats the last packet that arrived, the crudest\nconcealment a decoder does. Whether it measures better or worse\nthan a hole is decided by whether the repeat lands in phase —\nan accident of the talker's pitch against the packet length —\nso a waveform metric cannot choose a concealment strategy.\n\nAcross a burst it tiles one 20 ms packet over the whole gap,\nand there it is unambiguously worse.":
        '`hold` 會重複最後一個收到的封包，這是解碼器最粗糙的補償方式。\n它量起來比空洞好還是差，取決於重複的內容是否剛好同相 —\n那是說話者音高與封包長度之間的巧合 —\n所以波形指標無法用來選擇補償策略。\n\n遇到成串遺失時，它會把一個 20 ms 的封包鋪滿整個空洞，\n此時就明顯更差。',
    'pick a noise .wav …':
        '選擇雜訊 .wav …',
    'Save segments…':
        '儲存片段…',
    'Write each non-silent segment as its own .wav.':
        '把每個非靜音片段各自寫成一個 .wav。',
    'Select a noise file':
        '選擇雜訊檔',
    'Select an impulse response':
        '選擇脈衝響應',
    ' — applied from there':
        ' — 從那裡開始套用',
    'Applied {}':
        '已套用 {}',
    'Save segments to':
        '將片段儲存到',
    'Wrote {} segment(s) to {}':
        '已將 {} 個片段寫入 {}',
    'Applies to the selected range when there is a selection, otherwise to the whole clip. Every Apply is undoable — Preview does not change anything at all.':
        '有選取範圍時套用到選取範圍，否則套用到整段音訊。每次套用都可以復原 — 預覽則完全不會改變任何東西。',
    '{} is silent — there is no room in it to apply.':
        '{} 是靜音 — 裡面沒有房間可以套用。',
    'no noise file selected':
        '未選擇雜訊檔',
    '{} segment(s)':
        '{} 個片段',
    "split_on_silence → {} segment(s), {}s of non-silent audio (nothing modified) — 'Save segments…' writes them out":
        'split_on_silence → {} 個片段，共 {} 秒非靜音音訊（未修改任何內容）— 用「儲存片段…」寫出',
    'Run split_on_silence first.':
        '請先執行 split_on_silence。',
    'Nothing to undo.':
        '沒有可復原的動作。',
    'No original to revert to.':
        '沒有可還原的原始音訊。',
    '{} does not change the audio, so there is nothing to preview.':
        '{} 不會改變音訊，所以沒有東西可以預覽。',
    'Could not read {} — {}: {}':
        '無法讀取 {} — {}: {}',
    '{}\ncannot be measured — {}':
        '{}\n無法量測 — {}',
    'no impulse response loaded':
        '未載入脈衝響應',
    'the impulse response is at {} Hz and the audio at {} Hz':
        '脈衝響應為 {} Hz，而音訊為 {} Hz',
    '{} failed — {}: {}':
        '{} 失敗 — {}: {}',
    'Preview failed — {}: {}':
        '預覽失敗 — {}: {}',
    'Plays the selection backwards.':
        '反向播放選取範圍。',
    'Subtracts the mean, per channel. An offset that drifts over a long recording is a very low frequency — use highpass for that.':
        '減去平均值（逐聲道）。在長時間錄音中漂移的偏移屬於極低頻 — 那種情況請用 highpass。',
    "Measured against this recording's own noise floor, so it survives a room trim_silence cannot handle.":
        '以這段錄音自己的底噪為基準量測，所以 trim_silence 處理不了的房間它也能應付。',
    'Learned models need the kudioenhance package and PyTorch: pip install "kudiostudio[enhance]" installs both':
        '學習式模型需要 kudioenhance 套件與 PyTorch：執行 pip install "kudiostudio[enhance]" 就會兩個一起安裝',
    'Enhance':
        '增強',
    'trad_enhance (fixed spectral subtraction)':
        'trad_enhance（固定頻譜減法）',
    'learned model (kudioenhance)':
        '學習式模型（kudioenhance）',
    'Analysis frame. Longer frames resolve the noise better and smear transients more.':
        '分析幀。較長的幀能更準確地解析雜訊，但會讓瞬態更模糊。',
    'run directory from kudioenhance, e.g. runs/exp1':
        'kudioenhance 的執行目錄，例如 runs/exp1',
    "resample to the model's rate if they differ":
        '若取樣率不同，重新取樣到模型的取樣率',
    'The clip is resampled to the rate the model was trained at, then the result is resampled back, so the session keeps its own rate.':
        '音訊會先重新取樣到模型訓練時的取樣率，結果再取樣回來，所以工作階段保有原本的取樣率。',
    'Hear the denoise without committing it. Runs on the whole clip,\nexactly as Denoise will, then loops the selected part.':
        '試聽降噪結果而不提交。會像「降噪」一樣處理整段音訊，\n然後循環播放選取的部分。',
    'Denoise':
        '降噪',
    'Method':
        '方法',
    'How much it changed the input':
        '對輸入的改變程度',
    'Rendering a preview of {} …':
        '正在算繪 {} 的預覽…',
    'Previewing — nothing has been committed.':
        '預覽中 — 尚未提交任何變更。',
    'Preview failed — {}':
        '預覽失敗 — {}',
    'Running {} …':
        '正在執行 {}…',
    'SNR {} dB   ·   SI-SDR {} dB   ·   segSNR {} dB\nThese measure the distance from the audio as it was before this run — they are not quality scores, and a good denoise moves them away from zero. For quality, use the Metrics panel with a clean reference.':
        'SNR {} dB   ·   SI-SDR {} dB   ·   segSNR {} dB\n這些量的是與本次執行前音訊的差距 — 它們不是品質分數，好的降噪會讓它們遠離零。要評估品質，請在「指標」面板搭配乾淨的參考訊號使用。',
    '{} finished.':
        '{} 完成。',
    'The audio changed while {} ran, so its result was dropped: it was made from the audio before the change. Run it again.':
        '{} 執行期間音訊被改動了，所以結果已捨棄：它是從改動之前的音訊算出來的。請重新執行。',
    '{} failed — {}':
        '{} 失敗 — {}',
    'this model was trained at {} Hz but the clip is {} Hz — enable resampling, or convert it in the Batch panel':
        '此模型以 {} Hz 訓練，但音訊為 {} Hz — 請啟用重新取樣，或在「批次」面板轉換',
    'Denoising always processes the whole clip — noise estimation needs the full signal. Preview does the same, then loops whatever is selected, and commits nothing.':
        '降噪一律處理整段音訊 — 雜訊估計需要完整的訊號。預覽也一樣，之後循環播放選取的部分，且不提交任何變更。',
    "kudioenhance is installed — point 'run_dir' at a directory it produced (it holds the model, its normalisation statistics and the STFT geometry they were trained with).":
        '已安裝 kudioenhance — 把「run_dir」指向它產生的目錄（裡面有模型、正規化統計量，以及訓練時使用的 STFT 參數）。',
    'Denoising…':
        '降噪中…',
    'learned model ({})':
        '學習式模型（{}）',
    'A denoising job is already running.':
        '已有降噪工作在執行中。',
    'noise estimate':
        '雜訊估計',
    'Select a kudioenhance run directory first.':
        '請先選擇 kudioenhance 執行目錄。',
    'Manifest (*.json);;All files (*)':
        'Manifest (*.json);;所有檔案 (*)',
    'Manifest':
        'Manifest',
    'Every one of the {} combinations has rows, but unevenly — {} to {} each.':
        '全部 {} 種組合都有資料列，但不平均 — 每種 {} 到 {} 列。',
    'Loop the {} file without loading it.':
        '循環播放 {} 檔案而不載入。',
    'Nothing to cover.':
        '沒有可涵蓋的內容。',
    '{} of {} noise × SNR combinations have no rows, although {} rows are enough to cover every one. One run never leaves holes like that: rows have gone missing, or this manifest is more than one run.':
        '{} / {} 種 雜訊 × SNR 組合沒有資料列，雖然 {} 列已足以涵蓋全部。單次執行不會留下這種缺口：有資料列遺失了，或這份 manifest 不只一次執行。',
    '{} of {} noise × SNR combinations have no rows, although {} rows per room are enough to cover every one. One run never leaves holes like that: rows have gone missing, or this manifest is more than one run.':
        '{} / {} 種 雜訊 × SNR 組合沒有資料列，雖然每個房間 {} 列已足以涵蓋全部。單次執行不會留下這種缺口：有資料列遺失了，或這份 manifest 不只一次執行。',
    '{} of {} noise × SNR combinations have no rows — {} rows in {} rooms are {} per room, and each room repeats the same combinations, so they cannot cover {}; in reg mode that takes at least {} clean files.':
        '{} / {} 種 雜訊 × SNR 組合沒有資料列 — {} 列分在 {} 個房間，每個房間 {} 列，而每個房間重複的是同樣的組合，所以涵蓋不了 {} 種；在 reg 模式下至少需要 {} 個乾淨檔案。',
    'Every one of the {} combinations has {} row(s).':
        '全部 {} 種組合都有 {} 列。',
    'Every one of the {} combinations has {} or {} rows.':
        '全部 {} 種組合都有 {} 或 {} 列。',
    'not on disk: {}':
        '不在磁碟上：{}',
    'no manifest open':
        '未開啟 manifest',
    'Open…':
        '開啟…',
    'Reload':
        '重新載入',
    'Read the manifest again and look for every file again — after a synthesis run has rewritten it, or a folder has been moved back.':
        '重新讀取 manifest 並再次尋找每個檔案 — 用於合成執行改寫了它之後，或資料夾被移回原位之後。',
    'Open a manifest.json — the Synthesize panel writes one beside every corpus.':
        '開啟 manifest.json — 「合成」面板會在每個語料庫旁寫出一份。',
    'only rows with a file missing':
        '只顯示有檔案遺失的列',
    'Open':
        '開啟',
    "Load this row's noisy file into the session, as File → Open would. Double-clicking a row does the same.":
        '把這一列的 noisy 檔案載入工作階段，就像「檔案 → 開啟」一樣。雙擊一列也有相同效果。',
    'Rows per noise × SNR among the rows shown. Double-click a cell to show just those rows.':
        '目前顯示的列中，每種 雜訊 × SNR 的列數。雙擊格子即可只顯示那些列。',
    'Rows per noise × SNR':
        '每種 雜訊 × SNR 的列數',
    'Reading {} and looking for its files …':
        '正在讀取 {} 並尋找其檔案…',
    '{} row(s)':
        '{} 列',
    'Could not read {} — {}':
        '無法讀取 {} — {}',
    'Select a row to hear it.':
        '請選一列來試聽。',
    'This row has no target — the corpus was written without reverberant targets.':
        '這一列沒有 target — 這個語料庫寫出時沒有殘響目標音。',
    'Not on disk: {}':
        '不在磁碟上：{}',
    'Auditioning the {} file, {} — nothing has been loaded.':
        '試聽 {} 檔案 {} — 未載入任何東西。',
    '{} of {} noise × SNR combinations have no rows — {} row(s) cannot cover {} combinations; in reg mode that takes at least {} clean files.':
        '{} / {} 種 雜訊 × SNR 組合沒有資料列 — {} 列無法涵蓋 {} 種組合；在 reg 模式下至少需要 {} 個乾淨檔案。',
    'paths shown relative to {}':
        '路徑相對於 {} 顯示',
    'not on disk: {}, looked for under {}':
        '不在磁碟上：{}，已在 {} 下尋找',
    "Relative paths are looked for under the current folder, then the manifest's folder and the folders above it. Auditioning plays a file without loading it; Open loads the noisy one for editing.":
        '相對路徑會先在目前資料夾下尋找，再找 manifest 所在的資料夾與其上層資料夾。試聽會播放檔案而不載入；「開啟」會載入 noisy 檔案以供編輯。',
    'A manifest is still being read.':
        '仍在讀取 manifest。',
    'Reading…':
        '讀取中…',
    '{} with a file missing':
        '{} 列有檔案遺失',
    'relative paths found under {}':
        '相對路徑在 {} 下找到',
    '{} — {} row(s).':
        '{} — {} 列。',
    '{} — {} row(s), {} with a file missing.':
        '{} — {} 列，{} 列有檔案遺失。',
    'showing {} of {}':
        '顯示 {} / {}',
    "Rows with a file missing, per noise × SNR: {} in all. Untick 'only rows with a file missing' for the corpus's own coverage.":
        '每種 雜訊 × SNR 中有檔案遺失的列：共 {} 列。取消勾選「只顯示有檔案遺失的列」即可看語料庫本身的覆蓋情況。',
    'Select a row first.':
        '請先選一列。',
    '{} is not on disk — nothing to open.':
        '{} 不在磁碟上 — 沒有東西可開啟。',
    'row {}: {} is {}, expected a path':
        '第 {} 列：{} 是 {}，應為路徑',
    'row {}: {} is {}, expected text or null':
        '第 {} 列：{} 是 {}，應為文字或 null',
    'row {}: {} is {}, expected a number or null':
        '第 {} 列：{} 是 {}，應為數字或 null',
    'every file on disk':
        '每個檔案都在磁碟上',
    'Could not play {} — {}: {}':
        '無法播放 {} — {}: {}',
    'Metrics':
        '指標',
    'Original (before edits)':
        '原始音訊（編輯之前）',
    'File…':
        '檔案…',
    'pip install "kudio[pesq]" (builds from source: needs a C compiler — on Windows, the MSVC Build Tools)':
        'pip install "kudio[pesq]"（從原始碼編譯：需要 C 編譯器 — 在 Windows 上需要 MSVC Build Tools）',
    'could not be computed for this pair':
        '這一對無法計算',
    'Line up with the reference first':
        '先與參考訊號對齊',
    'Every metric here but PESQ compares sample i of one signal with\nsample i of the other. A clip scored against itself reads +145 dB; one\nsample of delay costs 134 of them, and a millisecond scores worse\nthan not processing at all — with nothing in the number to say\nwhich of the two happened.\n\nThe delay is measured and reported either way. This decides\nwhether it is taken out before scoring.':
        '這裡除了 PESQ，每個指標都是拿一個訊號的第 i 個樣本與另一個的第 i 個樣本比較。\n一段音訊拿自己評分會得到 +145 dB；延遲一個樣本就少掉 134 dB，\n延遲一毫秒的分數比完全不處理還差 — 而數字本身\n無法告訴你是哪一種情況。\n\n延遲無論如何都會被量測並回報。這個選項決定\n評分前是否先把它去除。',
    'Evaluate':
        '評估',
    'Against a clean reference':
        '與乾淨參考訊號比較',
    'Which PESQ scale the table reports.\n\nMOS-LQO is what kudio reports by default. raw P.862 is the\nnarrowband score before the P.862.1 mapping. The two are\ndifferent scales, not different roundings: raw 2.03 is about\n1.66 MOS-LQO. Papers quote both, so check which one a paper used.':
        '表格回報的 PESQ 尺度。\n\nMOS-LQO 是 kudio 預設回報的分數。raw P.862 是\n經過 P.862.1 映射之前的窄頻分數。兩者是不同的尺度，\n不是不同的捨入方式：raw 2.03 大約等於 1.66 MOS-LQO。\n論文兩種都有人用，比較之前先確認對方用的是哪一種。',
    'PESQ scale':
        'PESQ 尺度',
    'Inspect this audio':
        '檢查這段音訊',
    'Predict opinion score (DNSMOS)':
        '預測主觀分數（DNSMOS）',
    'ITU-T P.835: what a listener would say, with no reference.\nNeeds onnxruntime and the DNS-Challenge weights, which kudio\ndoes not ship.':
        'ITU-T P.835：不需參考訊號，預測聽者會怎麼評價。\n需要 onnxruntime 與 DNS-Challenge 權重，kudio\n沒有隨附。',
    'No reference needed':
        '不需要參考訊號',
    'Running DNSMOS …':
        '正在執行 DNSMOS…',
    'DNSMOS — {}':
        'DNSMOS — {}',
    'DNSMOS unavailable — see the panel for what is missing.':
        'DNSMOS 無法使用 — 缺少的項目請見面板說明。',
    'n/a (shorter than 400 ms)':
        '不適用（短於 400 ms）',
    'Inspection complete.':
        '檢查完成。',
    'not installed':
        '未安裝',
    'not defined at {} Hz (PESQ is 8 or 16 kHz)':
        '在 {} Hz 下沒有定義（PESQ 僅支援 8 或 16 kHz）',
    'Computing PESQ / STOI / SDR and the rest …':
        '正在計算 PESQ / STOI / SDR 與其餘指標…',
    'Estimate reverberation and SNR (SRMR, WADA-SNR)':
        '估計殘響與 SNR（SRMR、WADA-SNR）',
    'Two more things a recording can say about itself.\n\nSRMR: how reverberant it is, as the energy of the slow\nmodulations syllables make over the faster ones a room adds.\nHigher is drier, and a figure means something only next to the\nsame speech somewhere else. It is scored at 8 and 16 kHz only,\nso other rates are measured on a 16 kHz copy.\n\nWADA-SNR: how far the speech sits above its noise, read from how\npeaked the samples are. Good for sorting recordings; read\nanything above 30 dB as clean rather than as a figure.':
        '錄音還能自己回答的兩件事。\n\nSRMR：殘響有多重，以音節造成的慢速調變能量，\n除以房間加上去的較快調變能量來表示。\n越高表示越乾，數字只有和同一段語音在別處的版本\n並列時才有意義。它只在 8 與 16 kHz 下計算，\n其他取樣率會在 16 kHz 的複本上量測。\n\nWADA-SNR：語音比它的雜訊高出多少，從樣本值\n有多集中來讀出。適合用來排序錄音；30 dB 以上\n請當成「乾淨」，不要當成一個數字來讀。',
    'Estimating reverberation and SNR …':
        '正在估計殘響與 SNR…',
    'Estimate complete.':
        '估計完成。',
    'Estimate failed — {}':
        '估計失敗 — {}',
    'SRMR (higher is drier)':
        'SRMR（越高越乾）',
    'SRMR (on a {} kHz copy; higher is drier)':
        'SRMR（在 {} kHz 的複本上量測；越高越乾）',
    'n/a — the reference is silent':
        '不適用 — 參考訊號是靜音',
    'n/a — its pauses are digital silence':
        '不適用 — 停頓是數位靜音',
    'n/a — too short to measure':
        '不適用 — 太短，無法量測',
    'n/a — the noise is not Gaussian, or drowns the speech':
        '不適用 — 雜訊不是高斯分布，或蓋過了語音',
    'n/a — next to no noise to measure':
        '不適用 — 幾乎沒有雜訊可量',
    'Cepstral distance':
        '倒頻譜距離',
    'not defined at {} Hz (8 or 16 kHz only)':
        '在 {} Hz 下沒有定義（僅限 8 或 16 kHz）',
    'needs PESQ, which is not installed':
        '需要 PESQ，但沒有安裝',
    'CSIG (signal)':
        'CSIG（訊號）',
    'CBAK (background)':
        'CBAK（背景）',
    'COVL (overall)':
        'COVL（整體）',
    'PESQ inside them (raw P.862)':
        '其中所用的 PESQ（raw P.862）',
    'PESQ inside them (wideband MOS-LQO)':
        '其中所用的 PESQ（寬頻 MOS-LQO）',
    'CSII (high level)':
        'CSII（高位準）',
    'CSII (mid level)':
        'CSII（中位準）',
    'CSII (low level)':
        'CSII（低位準）',
    'not defined at {} Hz (8 kHz or more)':
        '在 {} Hz 下沒有定義（需要 8 kHz 以上）',
    'n/a — no frame of the reference is at this level':
        '不適用 — 參考訊號沒有音框落在這一級',
    'n/a — one frame at this level, too few':
        '不適用 — 這一級只有一個音框，太少',
    'n/a — this clip is silent':
        '不適用 — 這段音訊是靜音',
    'This clip has NaN or inf samples; repair it before scoring.':
        '這段音訊有 NaN 或 inf 樣本；請先修復再評分。',
    'The reference has NaN or inf samples; repair it before scoring.':
        '參考訊號有 NaN 或 inf 樣本；請先修復再評分。',
    'This clip {}; lined up with the reference before scoring.':
        '這段音訊比參考訊號{}；評分前已對齊。',
    'Evaluation complete.':
        '評估完成。',
    'Evaluation failed — {}':
        '評估失敗 — {}',
    'Metric':
        '指標',
    'Value':
        '數值',
    'Clipping, level, DC offset, noise floor and the bandwidth the content really occupies — the questions you can still ask about a recording that has no clean version anywhere.':
        '削波、音量、DC 偏移、底噪，以及內容實際佔用的頻寬 — 對一段沒有任何乾淨版本的錄音，你仍然可以問的問題。',
    'Another job is already running.':
        '已有其他工作在執行中。',
    'Nothing wrong with the recording itself. This says the audio is technically sound, not that it sounds good — for that, try the DNSMOS button below.':
        '錄音本身沒有問題。這只表示音訊在技術上沒問題，不代表它聽起來好 — 若要評估聽感，請試試下方的 DNSMOS 按鈕。',
    'PESQ, STOI and SDR back-ends available.':
        'PESQ、STOI 與 SDR 後端皆可使用。',
    'no original audio in this session':
        '此工作階段沒有原始音訊',
    'Sample-rate mismatch: reference {} Hz vs current {} Hz.':
        '取樣率不符：參考訊號 {} Hz，目前音訊 {} Hz。',
    'Nothing to compare.':
        '沒有可比較的內容。',
    'Segmental SNR':
        '分段 SNR',
    'Compared samples':
        '比較的樣本數',
    'An evaluation is already running.':
        '已有評估在執行中。',
    'Alignment':
        '對齊',
    'The reference does not look like this recording.':
        '參考訊號看起來不像這段錄音。',
    'SIG (speech)':
        'SIG（語音）',
    'BAK (background)':
        'BAK（背景）',
    'OVRL (overall)':
        'OVRL（整體）',
    'Windows averaged':
        '平均的視窗數',
    'Inspection failed — {}: {}':
        '檢查失敗 — {}: {}',
    'Peak':
        '峰值',
    'Loudness (BS.1770)':
        '響度（BS.1770）',
    'Clipped samples':
        '削波樣本數',
    'DC offset':
        'DC 偏移',
    'Silence':
        '靜音',
    'Noise floor':
        '底噪',
    'Estimated SNR':
        '估計 SNR',
    'Bandwidth':
        '頻寬',
    '{} Hz of {} Hz available':
        '可用 {1} Hz 中的 {0} Hz',
    '{} unavailable — {}':
        '{} 無法使用 — {}',
    'Reference unavailable — {}':
        '參考訊號無法使用 — {}',
    '⚠ Reference':
        '⚠ 參考訊號',
    'these do not look like the same recording — the scores below are about two unrelated signals':
        '這兩者看起來不像同一段錄音 — 下方分數描述的是兩個無關的訊號',
    '⚠ Not aligned':
        '⚠ 未對齊',
    '{} ms of offset is left in; the scores below are mostly measuring it':
        '保留了 {} ms 的偏移；下方分數主要是在量測這個偏移',
    'Pitch':
        '音高',
    'Speech (65–400 Hz)':
        '語音（65–400 Hz）',
    'Low voice (60–250 Hz)':
        '低音聲線（60–250 Hz）',
    'High voice (150–600 Hz)':
        '高音聲線（150–600 Hz）',
    'Singing / wide (50–1000 Hz)':
        '歌唱 / 寬範圍（50–1000 Hz）',
    'Custom':
        '自訂',
    "pYIN compares a frame against itself one period later, so the\nframe has to hold two periods of the lowest pitch you ask for.\nkudio refuses the combination rather than returning an empty\ntrack, which would read as 'this recording has no pitch'.":
        'pYIN 會把一幀與自己晚一個週期的版本比較，所以\n幀長必須容納你要求的最低音高的兩個週期。\nkudio 會拒絕不合的組合，而不是回傳空的軌跡，\n否則看起來會像「這段錄音沒有音高」。',
    'Draw the contour over the spectrogram':
        '在頻譜圖上繪製輪廓',
    'Only on the STFT view — on a mel or MFCC image the vertical axis\nis a band index, so a curve in Hz would land in the wrong place.':
        '只在 STFT 視圖上 — 在 mel 或 MFCC 圖上，縱軸是頻帶索引，\n以 Hz 表示的曲線會落在錯誤的位置。',
    'Track pitch':
        '追蹤音高',
    'Voiced spans → markers':
        '有聲區段 → 標記',
    'Add each voiced run to the marker list, where it can be renamed,\nsaved beside the file, or exported as a label track.':
        '把每個有聲區段加到標記清單，可以在那裡重新命名、\n存在檔案旁，或匯出為標籤軌。',
    'Tracking pitch — pYIN runs over the whole clip at once.':
        '正在追蹤音高 — pYIN 會一次處理整段音訊。',
    'median {} Hz\n5–95%  {}–{} Hz ({} semitones)\n{} of frames voiced, {} span(s)':
        '中位數 {} Hz\n5–95%  {}–{} Hz（{} 個半音）\n{} 的幀為有聲，{} 個區段',
    'Pitch: {}':
        '音高：{}',
    'Added {} marker(s) for the voiced spans.':
        '已為有聲區段新增 {} 個標記。',
    'Added {} marker(s) for the voiced spans, in place of the {} added last time.':
        '已為有聲區段新增 {} 個標記，取代上次加入的 {} 個。',
    'Pitch tracking failed — {}':
        '音高追蹤失敗 — {}',
    'A selection is tracked on its own if there is one, so you can ask about one phrase rather than the whole take. The contour is measured from the audio as it is now — any edit drops it, because a curve left on screen describing audio that has been replaced is worse than no curve.':
        '若有選取範圍，會只追蹤選取範圍，讓你可以針對某一句而不是整段錄音提問。輪廓是根據目前的音訊量測的 — 任何編輯都會讓它消失，因為畫面上留著一條描述已被取代音訊的曲線，比沒有曲線更糟。',
    'Tracking…':
        '追蹤中…',
    'the whole clip':
        '整段音訊',
    'the selection, {}–{} s':
        '選取範圍，{}–{} 秒',
    'Another job is still running.':
        '仍有其他工作在執行中。',
    'Nothing voiced in this range. A voice below fmin is not found — it is reported as something wrong above it, so an empty answer is the honest one. Try a wider range.':
        '這個範圍內沒有有聲的部分。低於 fmin 的聲音不會被找到 — 它會被回報成高於 fmin 的某個錯誤值，所以空的答案才是誠實的。請試試更寬的範圍。',
    'No voiced frames found.':
        '找不到有聲的幀。',
    'voiced {}':
        '有聲 {}',
    'fixed length':
        '固定長度',
    'until stopped':
        '錄到停止為止',
    'Record':
        '錄音',
    'System default':
        '系統預設',
    'Last input device (PyAudio names no default)':
        '最後一個輸入裝置（PyAudio 沒有指定預設輸入）',
    'off — show what the microphone sends':
        '關閉 — 顯示麥克風送來的原始訊號',
    'Denoise the rolling display as it arrives (kudio.StreamEnhancer).\nMonitoring only — the take is committed exactly as recorded.\nOnly the per-frame methods can stream; the rest need the whole\nspectrogram and are in the Enhance panel.':
        '在滾動畫面收到音訊時即時降噪（kudio.StreamEnhancer）。\n僅供監聽 — 錄音會完全照錄製時的樣子提交。\n只有逐幀方法能串流處理；其他方法需要整張頻譜圖，\n請在「增強」面板使用。',
    '● Record':
        '● 錄音',
    'Refresh devices':
        '重新整理裝置',
    'no take yet':
        '尚未錄音',
    'Mode':
        '模式',
    'Device':
        '裝置',
    'monitor through':
        '監聽經過',
    'Recording {}s at {} Hz …':
        '正在以 {1} Hz 錄製 {0} 秒…',
    '■ Stop':
        '■ 停止',
    'Recording at {} Hz — press Stop when done.':
        '正在以 {} Hz 錄音 — 完成時請按停止。',
    '● Start':
        '● 開始',
    'Recorded {}s at {} Hz.':
        '已以 {1} Hz 錄製 {0} 秒。',
    'peak {} dBFS':
        '峰值 {} dBFS',
    'Recording failed — {}':
        '錄音失敗 — {}',
    'Playback failed — {}':
        '播放失敗 — {}',
    'The recording replaces the current audio and becomes the new baseline for Undo and Metrics. Needs kudio[audio]. Every take is checked for clipping and level as soon as it lands — a clipped take is best redone, since nothing downstream can undo it.':
        '錄音會取代目前的音訊，並成為復原與指標的新基準。需要 kudio[audio]。每次錄音一完成就會檢查削波與音量 — 削波的錄音最好重錄，因為後續任何處理都無法挽回。',
    'PyAudio device indices (CheckDevice)':
        'PyAudio 裝置索引（CheckDevice）',
    'sounddevice device indices (kudio.list_devices)':
        'sounddevice 裝置索引（kudio.list_devices）',
    'No input devices found — is kudio[audio] installed?':
        '找不到輸入裝置 — 是否已安裝 kudio[audio]？',
    'Recording…':
        '錄音中…',
    'A recording is already in progress.':
        '已有錄音在進行中。',
    'Nothing was captured — check the input device.':
        '沒有錄到任何東西 — 請檢查輸入裝置。',
    "Reached the recorder's length cap — stopping.":
        '已達錄音器的長度上限 — 停止中。',
    '{} — removing {} dB right now  ·  {} ms behind':
        '{} — 目前去除 {} dB  ·  延遲 {} ms',
    'Recorded, but: {}':
        '已錄音，但：{}',
    'live monitoring stopped — see the log':
        '即時監聽已停止 — 請見記錄',
    '  ·  take looks clean':
        '  ·  錄音看起來沒有問題',
    'Live monitoring unavailable — {}: {}':
        '即時監聽無法使用 — {}: {}',
    'Synthesize':
        '合成',
    'no SNR values given':
        '未提供 SNR 值',
    'folder of clean audio':
        '乾淨音訊資料夾',
    'folder of noise audio':
        '雜訊音訊資料夾',
    'output folder for the mixture':
        '混合音的輸出資料夾',
    'Signal-to-noise ratios in dB, one corpus axis per value.\n\nFractional values are allowed: 2.5 is written `_2p5dB`, because\na decimal point in a file name reads as the start of the suffix.\nA whole number keeps its plain name, so `0` is still `_0dB`.':
        '以 dB 為單位的訊雜比，每個值是語料庫的一個軸。\n\n可以使用小數：2.5 會寫成 `_2p5dB`，因為\n檔名中的小數點會被當成副檔名的開頭。\n整數則保留原本的名稱，所以 `0` 仍是 `_0dB`。',
    'no rooms — e.g. 0.3, 0.6, 0.9':
        '無房間 — 例如 0.3, 0.6, 0.9',
    'Reverberation times in seconds. A fourth axis: every output the\nmode writes is written once per room, so the count multiplies by\nhowever many you give, in regular mode as in inc.\n\nThe room acts on the speech before the noise is mixed in, which\nis the order a microphone meets them — so the SNR is measured\nagainst the reverberant speech. The clean file stays the target,\nso a model trained on this is asked to undo both.':
        '以秒為單位的殘響時間。這是第四個軸：該模式寫出的每個輸出\n都會在每個房間各寫一次，所以輸出數量會乘上你給的數量，\nregular 與 inc 模式都一樣。\n\n房間作用在混入雜訊之前的語音上，這正是麥克風\n接收到它們的順序 — 所以 SNR 是相對於含殘響的語音量測的。\n乾淨檔案仍是目標，所以用這個訓練的模型要同時去除兩者。',
    'Direct-to-reverberant ratio — distance, in a readable unit.\n+10 and above is a close microphone; 0 is a metre or two;\nnegative is the far end of a hall.':
        '直達聲與殘響比 — 以容易理解的單位表示距離。\n+10 以上是近距離麥克風；0 是一兩公尺外；\n負值是音樂廳的遠端。',
    'What the mixtures are written as.\n\nPCM_16 is the usual choice and clips anything past full scale —\nkudio says how many files and how far over, and they are listed\nunder the result.\n\nFLOAT keeps values past 1.0 instead of clipping them, which is\nwhat you want when a loud talker at a low SNR would otherwise\nlose its peaks. PCM_24 still clips, with a finer step.':
        '混合音的寫出格式。\n\nPCM_16 是常見的選擇，會把超過滿刻度的部分削掉 —\nkudio 會說明有幾個檔案、超出多少，並列在結果下方。\n\nFLOAT 會保留超過 1.0 的值而不削波，當大聲的說話者\n在低 SNR 下會失去峰值時，這正是你要的。\nPCM_24 仍會削波，只是步階更細。',
    'Send every mixture down a link — the third kind of degradation,\nand the one that is neither additive nor convolutive.\n\ntelephone: 300–3400 Hz at 8 kHz through G.711\nvoip: G.711 with 5% of packets lost in runs of 4, held\nmu_law / a_law: the companding without the band-limiting\n\nApplied to the mixture, noise and all, because a wire carries\nwhatever reached the microphone. Not an axis: it is constant\nfor the run and is recorded in manifest.json, not in the names.':
        '讓每個混合音都經過一條傳輸鏈路 — 第三種劣化，\n既不是加成性也不是摺積性的。\n\ntelephone：8 kHz 下 300–3400 Hz，經過 G.711\nvoip：G.711，5% 的封包以每串 4 個遺失並保持\nmu_law / a_law：只有壓擴，不做頻帶限制\n\n套用在混合音上，連同雜訊一起，因為線路傳送的是\n麥克風收到的一切。它不是一個軸：在一次執行中固定不變，\n記錄在 manifest.json 中，而不在檔名裡。',
    'also write reverberant targets (TARGETS/)':
        '同時寫出殘響目標音（TARGETS/）',
    'Write the reverberant-but-clean signal beside each mixture.\n\nThe clean file is the dry target, so a model trained on it is\nasked to undo the room as well as the noise. This is the same\nspeech with the room left on, for training that should remove\nthe noise and leave the room — a different and easier problem.\n\nNeeds rt60: with no room the clean file already is that signal.':
        '在每個混合音旁寫出含殘響但乾淨的訊號。\n\n乾淨檔案是乾聲目標，所以用它訓練的模型要同時去除\n房間與雜訊。這則是保留房間的同一段語音，用於只去除\n雜訊而保留房間的訓練 — 一個不同且較簡單的問題。\n\n需要 rt60：沒有房間時，乾淨檔案本身就是這個訊號。',
    'regular: one output per clean file\ninc: every clean × noise × SNR combination':
        'regular：每個乾淨檔案輸出一份\ninc：每一種 乾淨 × 雜訊 × SNR 的組合',
    'also copy the clean files (is_add_clean)':
        '同時複製乾淨檔案（is_add_clean）',
    'delete everything in out_path first (overwrite)':
        '先刪除 out_path 裡的所有東西（overwrite）',
    "syn(overwrite=True): if out_path holds anything at all, the whole\nfolder is deleted before the run, files that have nothing to do\nwith the corpus included. One folder holds one run, and the\nmanifest beside it describes every file in it.\n\nOff, a folder that is not empty is left as it is and nothing is\nwritten. kudio will not empty a drive's root, or a folder that\nholds the working directory, your home folder or an input.":
        'syn(overwrite=True)：只要 out_path 裡有任何東西，執行前整個\n資料夾都會被刪除，與語料庫無關的檔案也一樣。一個資料夾\n只裝一次執行，旁邊的 manifest 描述的就是裡面的每一個檔案。\n\n取消勾選時，不是空的資料夾會原封不動，什麼都不寫。\nkudio 不會清空磁碟根目錄，也不會清空包含工作目錄、\n你的家目錄或任何輸入檔的資料夾。',
    'Synthesizing …':
        '合成中…',
    '  ·  manifest.json beside them':
        '  ·  manifest.json 在旁邊',
    '  ·  down a {} link':
        '  ·  經過 {} 鏈路',
    '  ·  {} reverberant target(s) in TARGETS/':
        '  ·  {} 個殘響目標音在 TARGETS/',
    '  ·  {} clipped (worst peak {})':
        '  ·  {} 個削波（最大峰值 {}）',
    '  ·  seed {}':
        '  ·  seed 為 {}',
    '{} file(s) written to {}{}':
        '已將 {} 個檔案寫入 {}{}',
    'Synthesis failed — {}':
        '合成失敗 — {}',
    'ValueError: No wave files found in clean path: {}':
        'ValueError: 乾淨語音的路徑裡找不到任何 WAV 檔：{}',
    'ValueError: No wave files found in noise path: {}':
        'ValueError: 雜訊的路徑裡找不到任何 WAV 檔：{}',
    'SynthesisError: refusing to empty {} before synthesising: {}. Point out_path= at a folder of its own.':
        'SynthesisError: 合成前不會清空 {}：{}。請把 out_path= 指到一個專用的資料夾。',
    'it is the root of a filesystem':
        '那是檔案系統的根目錄',
    'it holds the working directory':
        '目前的工作目錄在它裡面',
    'it holds the home directory':
        '使用者的家目錄在它裡面',
    'it holds the input {}':
        '輸入檔 {} 在它裡面',
    '{} is not a usable SNR':
        '{} 不是可用的 SNR',
    'Set a seed to make the mixture reproducible — the same seed gives the same noise offsets, rooms and lost packets. Which noise and SNR each clean file gets follows the file order, not the seed. Left at None, a seed is drawn and the result names it.':
        '設定 seed 讓混合音可以重現 — 相同的 seed 會得到相同的雜訊偏移、房間與遺失的封包。每個乾淨檔案配到哪種雜訊與 SNR，依檔案的順序決定，與 seed 無關。保持 None 時會抽一個 seed，結果會寫出是哪一個。',
    'Synthesizing…':
        '合成中…',
    'Reverberant targets need rt60 — with no room the clean file already is that signal.':
        '殘響目標音需要 rt60 — 沒有房間時，乾淨檔案本身就是那個訊號。',
    'A synthesis job is already running.':
        '已有合成工作在執行中。',
    "{} of {} file(s) went past full scale and were clipped as {} (worst peak {}, +{} dBFS). Lower the clean level, raise the SNR, or write subtype='FLOAT' to keep them.":
        "{} / {} 個檔案超過滿刻度，以 {} 寫出時被削波（最大峰值 {}，+{} dBFS）。請降低乾淨音訊的音量、提高 SNR，或改寫成 subtype='FLOAT' 以保留它們。",
    'Synthesis complete — {} file(s), but {} were asked for: {} output(s) shared a name and overwrote each other. Give the clashing files distinct names, or keep mkdir_parents on so the folder layout separates them.':
        '合成完成 — {} 個檔案，但要求的是 {} 個：有 {} 個輸出同名而互相覆寫。請給衝突的檔案不同的名稱，或保持 mkdir_parents 開啟，讓資料夾結構把它們分開。',
    'Synthesis complete — {} file(s).':
        '合成完成 — {} 個檔案。',
    'Synthesis complete — {} file(s) from seed {}; the same seed writes the same corpus again.':
        '合成完成 — {} 個檔案，seed 為 {}；相同的 seed 會再寫出相同的語料庫。',
    'Synthesis complete — {} file(s), and {} warning(s) from kudio, listed under the form.':
        '合成完成 — {} 個檔案，另有 {} 個 kudio 的警告，列在表單下方。',
    '{} is not empty, so nothing was synthesised. Pass overwrite=True to replace it, or point out_path= somewhere else.':
        '{} 不是空的，所以什麼都沒有合成。傳入 overwrite=True 以清空它，或把 out_path= 指到別的地方。',
    '{} output(s) share a name with another and will overwrite it, e.g. {}':
        '有 {} 個輸出與另一個同名，會覆寫它，例如 {}',
    'Synthesizer skipped {} input(s) that are not existing .wav files, e.g. {}':
        'Synthesizer 略過了 {} 個不是既有 .wav 檔的輸入，例如 {}',
    'reverberation times must be positive, got {}':
        '殘響時間必須為正值，收到的是 {}',
    'Bad SNR list — {}':
        'SNR 清單有誤 — {}',
    'Bad rt60 list — {}':
        'rt60 清單有誤 — {}',
    'Select a valid {} folder.':
        '請選擇有效的 {} 資料夾。',
    '{} has changed on disk since it was opened, so the script stops rather than edit another file; point it at a copy of the original':
        '{} 在開啟之後已在磁碟上變更，所以腳本會停止而不去編輯另一個檔案；請指向原始檔案的副本',
    'Loaded {} — {}s @ {} Hz{}':
        '已載入 {} — {} 秒 @ {} Hz{}',
    'Undo: {}':
        '復原：{}',
    'Reverted to the original audio':
        '已還原為原始音訊',
    ' (no crossfade — hard cut)':
        '（無交叉淡化 — 硬切）',
    ' ({} ms crossfade)':
        '（{} ms 交叉淡化）',
    ' · {} marker(s)':
        ' · {} 個標記',
    'Copied {}s':
        '已複製 {} 秒',
    'Cut {}s{}':
        '已剪下 {} 秒{}',
    'Deleted {}s{}':
        '已刪除 {} 秒{}',
    'Pasted {}s at {}s{}':
        '已在 {1} 秒處貼上 {0} 秒{2}',
    'Inserted {}s of silence{}':
        '已插入 {} 秒靜音{}',
    'Cropped to {}s':
        '已裁切為 {} 秒',
    'Saved {} marker(s) to {}':
        '已將 {} 個標記儲存到 {}',
    'Saved {}':
        '已儲存 {}',
    'Saved {}s to {}':
        '已將 {} 秒儲存到 {}',
    'is not the file these edits were made on: it has changed since. Point the script at a copy of the original, or delete this check to run the edits on it anyway.':
        '不是這些編輯所依據的檔案：它之後已被變更。請把腳本指向原始檔案的副本，或刪除這個檢查以照樣執行編輯。',
    "{} is gone, and the script reads the edits' starting point from it":
        '{} 已不存在，而腳本要從它讀取編輯的起點',
    '{} was saved over and now holds the edited audio, so the script would apply every edit a second time; it stops instead. Point it at a copy of the original':
        '{} 已被覆寫，現在存的是編輯後的音訊，所以腳本會把每個編輯再套用一次；因此它會停止。請指向原始檔案的副本',
    'nothing to save':
        '沒有可儲存的內容',
    'nothing selected':
        '沒有選取任何範圍',
    'nothing loaded':
        '沒有載入任何音訊',
    'this audio was recorded, not opened from a file, so SOURCE is a placeholder: point it at the take as it was recorded':
        '這段音訊是錄製的，不是從檔案開啟的，所以 SOURCE 只是佔位符：請把它指向錄製時的那段音訊',
    'step {} ({}) was not recorded as code':
        '第 {} 步（{}）沒有被記錄為程式碼',
    '{} cannot be read back — {}':
        '{} 無法讀回 — {}',
    'insert silence':
        '插入靜音',
    '{} could not be read — {}: {}':
        '{} 無法讀取 — {}: {}',
    'the clipboard came from a recording, which the script cannot read back':
        '剪貼簿來自一段錄音，腳本無法讀回',
    'Levels':
        '音量',
    'true peak':
        'true peak',
    'nothing wrong with the recording itself':
        '錄音本身沒有問題',
    'true peak {} dBTP — the waveform between the samples is at or above the usual -1 dBTP ceiling, so this can clip on conversion':
        'true peak {} dBTP — 樣本之間的波形達到或超過常見的 -1 dBTP 上限，因此在轉換時可能削波',
    '＋ At playhead':
        '＋ 在播放頭',
    '＋ Selection':
        '＋ 選取範圍',
    'Find speech':
        '尋找語音',
    'Rename':
        '重新命名',
    'Save':
        '儲存',
    'Write them beside the audio as clip.wav.labels.json, which is picked up automatically next time the file is opened.':
        '在音訊旁寫成 clip.wav.labels.json，下次開啟該檔案時會自動讀取。',
    'Export…':
        '匯出…',
    'Write a .txt Audacity label track, or a .json file elsewhere.':
        '寫成 .txt 的 Audacity 標籤軌，或另存為 .json 檔。',
    'Import…':
        '匯入…',
    'Marker at {}':
        '標記於 {}',
    'Marked {}':
        '已標記 {}',
    'Rename marker':
        '重新命名標記',
    'Markers cleared.':
        '已清除標記。',
    'Export markers':
        '匯出標記',
    'Import markers':
        '匯入標記',
    'Imported {} marker(s) from {}':
        '已從 {1} 匯入 {0} 個標記',
    'marker {}':
        '標記 {}',
    'Select a range first.':
        '請先選取一個範圍。',
    'This audio has no file to sit beside — use Export… instead.':
        '這段音訊沒有對應的檔案可以放在旁邊 — 請改用「匯出…」。',
    'Click to jump here, double-click to select the range':
        '點一下跳到這裡，雙擊選取範圍',
    'Click to jump here':
        '點一下跳到這裡',
    'vad failed — {}: {}':
        'vad 失敗 — {}: {}',
    'Could not save markers — {}: {}':
        '無法儲存標記 — {}: {}',
    'Import failed — {}: {}':
        '匯入失敗 — {}: {}',
    'speech {}':
        '語音 {}',
    'A / B  ·  edited':
        'A / B  ·  編輯版本',
    'A / B  ·  original':
        'A / B  ·  原始音訊',
    'Play from the cursor, or the selection (Space)':
        '從游標或選取範圍開始播放（Space）',
    'Selection only':
        '只播選取範圍',
    'Play just the highlighted range when there is a selection.':
        '有選取範圍時只播放反白的範圍。',
    'Loop':
        '循環',
    "Repeat what was last played (Ctrl+L).\nA panel's Preview always loops; this is for ordinary playback.":
        '重複上次播放的內容（Ctrl+L）。\n面板的預覽一律會循環；這是給一般播放用的。',
    'Hold the edit against the audio you started with (Ctrl+B).\nViews and playback follow; the tools keep working on the edit.':
        '把編輯版本與一開始的音訊對照（Ctrl+B）。\n視圖與播放會跟著切換；工具仍然作用在編輯版本上。',
    'Match loudness':
        '響度對齊',
    'Level the original to the edit (ITU-R BS.1770) before comparing.\nOf two versions of a clip the louder is preferred whatever its\nquality, so an unmatched A/B is partly a loudness test.':
        '比較前先把原始音訊的響度調到與編輯版本相同（ITU-R BS.1770）。\n同一段音訊的兩個版本中，較大聲的總是較受偏好，不論品質如何，\n所以沒有對齊響度的 A/B 有一部分其實是在比響度。',
    'Milliseconds blended at each cut, paste and insert.\n0 is a hard cut, at the cost of a click where two pieces meet.\nEvery edit says in the status bar how much it applied.':
        '每次剪下、貼上與插入時混合的毫秒數。\n0 為硬切，代價是兩段接合處會出現喀聲。\n每次編輯都會在狀態列說明套用了多少。',
    'Gain':
        '增益',
    'Splice xf':
        '接合淡化',
    'Short-term loudness (EBU Tech 3341) over the waveform, on its own LUFS\naxis. The integrated figure in the Levels box is one number for the whole\nclip; this is where it moved.':
        '在波形上以獨立的 LUFS 軸顯示短期響度（EBU Tech 3341）。\nLevels 區塊中的整合數值是整段音訊的一個數字；\n這條曲線顯示它在哪裡變動。',
    'Frequency':
        '頻率',
    'Loudness over time':
        '響度隨時間變化',
    'Analysis':
        '分析',
    'Selection':
        '選取範圍',
    'MFCC coefficient':
        'MFCC 係數',
    'Mel band':
        'Mel 頻帶',
    'The clip is shorter than the {} s short-term window (EBU Tech 3341), so there is no curve to draw.':
        '音訊短於 {} 秒的短期視窗（EBU Tech 3341），所以沒有曲線可畫。',
    '{} s selected':
        '已選取 {} 秒',
    'peak':
        '峰值',
    'loudness':
        '響度',
    'range':
        '範圍',
    'bandwidth':
        '頻寬',
    'start':
        '起點',
    'end':
        '終點',
    'Inspect':
        '檢查',
    'Waveform':
        '波形',
    'Select all':
        '全選',
    'Cancel':
        '取消',
    'Ready.':
        '就緒。',
    'n/a':
        '不適用',
    'silence':
        '靜音',
    'Open a recording to begin':
        '先開啟一段錄音',
    'Drop audio files here, or open them from disk.':
        '把音訊檔拖進來，或從磁碟開啟。',
    'Open audio…':
        '開啟音訊…',
    'Opened files appear here.':
        '開過的檔案會列在這裡。',
    'No markers. A sidecar file next to the audio is read when it opens.':
        '沒有標記。開檔時會一併讀取音訊旁的標記檔。',
    'Open audio files (Ctrl+O)':
        '開啟音訊檔（Ctrl+O）',
    'Undo (Ctrl+Z)':
        '復原（Ctrl+Z）',
    'Zoom to selection (Ctrl+E)':
        '縮放至選取範圍（Ctrl+E）',
    'Zoom out to whole clip (Ctrl+F)':
        '縮小至整段音訊（Ctrl+F）',
    'Remove from the list':
        '從清單移除',
    'Path of an audio file on this computer':
        '這台電腦上音訊檔的路徑',
    'Dropping files needs the KudioStudio window; use Open here.':
        '拖放檔案要在 KudioStudio 視窗裡才行，這裡請用「開啟」。',
    'OK':
        '確定',
    'Close':
        '關閉',
    'Path':
        '路徑',
    'Save audio as (Ctrl+Shift+S)':
        '另存音訊（Ctrl+Shift+S）',
    'A desktop workbench for the kudio audio toolkit.':
        'kudio 音訊工具組的桌面工作台。',
    'Close the browser tab to leave; the server stops with Ctrl+C.':
        '要離開請關掉瀏覽器分頁；伺服器用 Ctrl+C 停止。',
    'kudio.vad — mark every span that contains speech':
        'kudio.vad — 把每一段含有語音的範圍標記起來',
    'Rendering a preview…':
        '正在產生試聽…',
    'silent':
        '無聲',
    # kudio's own sentences, which the window shows as they come: the
    # report's findings, the alignment, a room's measurement, DNSMOS and a
    # pitch track.
    # tests/test_i18n.py makes each one from the installed kudio, so a
    # rewording there fails a test here instead of showing English.
    '{} samples are not finite (NaN or inf) — the clip is corrupt, not quiet, and every measurement here is meaningless. Repair it or drop it from the set':
        '有 {} 個樣本不是有限值（NaN 或 inf）— 這段音訊是壞的，不是安靜，這裡的每一項量測都沒有意義。請修好它，或把它從資料集中拿掉',
    'the clip is digital silence':
        '這段音訊是數位靜音',
    '{} samples clipped ({}% of the clip) — the recording was already too hot; no processing gets that back':
        '有 {} 個樣本削波（佔整段的 {}%）— 錄音時就已經太大聲，任何處理都救不回來',
    'peaks at {} dBFS, past full scale — it will clip the moment it is written to an integer format':
        '峰值 {} dBFS，超過滿刻度 — 一寫成整數格式就會削波',
    'peaks at {} dBFS, right against the ceiling — one more gain stage will clip it':
        '峰值 {} dBFS，緊貼上限 — 再多一級增益就會削波',
    'peaks at only {} dBFS — very quiet, so the usable bit depth is small':
        '峰值只有 {} dBFS — 非常小聲，能用到的位元深度很少',
    'DC offset of {} — the waveform is not centred on zero, which wastes headroom':
        'DC 偏移 {} — 波形沒有以零為中心，浪費了動態餘裕',
    'content stops at {} Hz although the file claims {} Hz — it was very likely {}, and the extra rate is empty':
        '內容到 {} Hz 就沒了，檔案卻標示 {} Hz — 很可能是{}，多出來的取樣率是空的',
    'upsampled from {} Hz':
        '從 {} Hz 升頻而來',
    'band-limited to about {} Hz':
        '被限頻在約 {} Hz',
    '{}% of the clip is silence':
        '整段有 {}% 是靜音',
    'estimated SNR around {} dB — the noise floor is close to the content':
        '估計 SNR 約 {} dB — 底噪很接近內容',
    '{}, correlation {}':
        '{}，相關係數 {}',
    '{}, correlation {}, polarity inverted':
        '{}，相關係數 {}，極性相反',
    'already aligned':
        '已經對齊',
    'lags by {} samples ({} ms)':
        '落後 {} 個樣本（{} ms）',
    'lags by {} sample ({} ms)':
        '落後 {} 個樣本（{} ms）',
    'leads by {} samples ({} ms)':
        '超前 {} 個樣本（{} ms）',
    'leads by {} sample ({} ms)':
        '超前 {} 個樣本（{} ms）',
    'no usable decay (fit {}) — the tail never falls 35 dB, so there is nothing to extrapolate':
        '沒有可用的衰減（fit {}）— 殘響尾巴從來沒有衰減到 35 dB，沒有東西可以外推',
    'T30 {} s ({})  ·  C50 {} dB  ·  DRR {} dB  ·  fit {}  — but EDT and T30 disagree, so this may not be an impulse response':
        'T30 {} s（{}）  ·  C50 {} dB  ·  DRR {} dB  ·  fit {}  — 但 EDT 與 T30 不一致，這可能不是脈衝響應',
    'T30 {} s ({})  ·  C50 {} dB  ·  DRR {} dB  ·  fit {}  — but the decay is not straight, so read it with care':
        'T30 {} s（{}）  ·  C50 {} dB  ·  DRR {} dB  ·  fit {}  — 但衰減不是一條直線，讀的時候要小心',
    'T30 {} s ({})  ·  C50 {} dB  ·  DRR {} dB  ·  fit {}':
        'T30 {} s（{}）  ·  C50 {} dB  ·  DRR {} dB  ·  fit {}',
    'no EDT: the direct sound alone falls 10 dB':
        '沒有 EDT：光是直達聲就衰減了 10 dB',
    'median {} Hz  ·  {}-{} Hz ({} semitones)  ·  {} voiced':
        '中位數 {} Hz  ·  {}-{} Hz（{} 個半音）  ·  {} 的幀為有聲',
    'no voiced frames in {} ({}-{} Hz)':
        '{} 幀當中找不到有聲的幀（{}-{} Hz）',
    'SIG {} · BAK {} · OVRL {}  ({} window(s))':
        'SIG {} · BAK {} · OVRL {}（{} 個視窗）',
    'clean (OVRL {})':
        '乾淨（OVRL {}）',
    'usable (OVRL {})':
        '可用（OVRL {}）',
    'poor (OVRL {})':
        '差（OVRL {}）',
    'bad (OVRL {})':
        '很差（OVRL {}）',
    '{} — the background is cleaner than the speech, which is what over-aggressive denoising looks like':
        '{} — 背景比語音還乾淨，這正是降噪下手太重的樣子',
    '{} — the speech survived but the background is still intrusive':
        '{} — 語音保住了，但背景仍然干擾',
}
