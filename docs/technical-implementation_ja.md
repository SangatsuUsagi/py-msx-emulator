# 技術実装ドキュメント

[← README_ja.md](../README_ja.md)

本ドキュメントでは、MSX1/MSX2 エミュレータの内部構造——CPU 実行、割り込み管理、I/O ディスパッチ、VDP レンダリング、メモリサブシステム——を解説します。

- [CPU エミュレーション](#cpu-エミュレーション)
- [割り込み管理](#割り込み管理)
- [I/O バス](#io-バス)
- [VDP](#vdp)
- [オーディオ](#オーディオ)
- [メモリとスロットシステム](#メモリとスロットシステム)
- [フロッピーディスク（FDC）](#フロッピーディスクfdc)
- [マシンと拡張の YAML スキーマ](#マシンと拡張の-yaml-スキーマ)
- [マシン YAML ローダ](#マシン-yaml-ローダ)
- [移植性](#移植性)

---

## CPU エミュレーション

### 命令デコード

Z80 CPU は `msx/cpu/z80.py` に実装されています。オペコードディスパッチテーブル `_DISPATCH` は 256 個の callable を格納するフラットなリストで、インポート時に `opcodes_main._build_dispatch()` によって一度だけ構築され、`z80.py` のモジュールレベル定数としてバインドされます。

```python
_DISPATCH: list[Callable[[Z80], int]] = _opcodes_main._DISPATCH
```

命令実行は単一のインデックスアクセスで完結し、辞書検索も `match`/`case` も使いません。

```python
n = _DISPATCH[opcode](cpu)
```

CB、DD、ED、FD プレフィックステーブルも同じ方式です。メインディスパッチャがプレフィックスバイトを検出すると、次のバイトを読んで対応するプレフィックステーブルに委譲します。

### 実行ループ

`Z80.step() -> int` が 1 命令を処理します。

1. `nmi_pending` がセットされていれば：PC をプッシュして 0x0066 にジャンプし、11 T ステートを返す。
2. `ei_pending` がセットされていれば：クリアする（EI は直後の命令の後から有効になるため、割り込み受け付けを 1 ステップだけ抑制する）。
3. `iff1` と `int_pending` がともに true であれば：割り込みを受け付ける。モード 1 — PC をプッシュして 0x0038 にジャンプ、13 T ステート。モード 2 — PC をプッシュして `I<<8 | data_bus` からベクタアドレスを読み、ハンドラにジャンプ、19 T ステート。
4. `halted` であれば：オペコードを読まずに 4 T ステートだけ進める。
5. それ以外：`instruction_pc` に現在の PC を記録し、`_fetch()` でオペコードを取得してディスパッチ。

`_fetch()` は `read_byte(PC)` で 1 バイトを読み、PC をインクリメントし、R レジスタをインクリメントします。カウントされるのは bit 0–6 のみで、bit 7 は sticky であり `LD R,A` のようなレジスタ全体への書き込みでしか変化しません。

### タイミング

`step()` は消費した T ステート数を返します。`Machine.run_frame()` がこれをフレーム単位の合計と `machine.cycle_count` に積算します。NTSC フレーム予算は **59,659 T ステート**（Z80A クロック 3.579545 MHz ÷ 60 Hz）です。

#### MSX M1 ウェイトステート

実機 MSX は Z80 の M1 サイクル（オペコードフェッチ）ごとに 1 ウェイトを挿入します。コアは設定可能な `Z80.m1_wait_states` フィールドでこれをモデル化します（デフォルト `0` = 純正 Zilog Z80。非 MSX システムでも再利用可能なように）。MSX 機の YAML は `cpu.m1_wait_states: 1` を設定し、`machine_loader` が `Z80(...)` コンストラクタに渡します。

ウェイトは命令バイト数ではなく **M1 サイクルごと** に加算されます：`step()` が主オペコードフェッチに `m1_wait_states` を加え、各プレフィックスハンドラ（`_op_prefix_cb/dd/fd/ed`）が 2 番目の M1 に対して再度加えます。したがって無印命令はオペランド長に関係なく `+1`、`CB`/`ED`/`DD`/`FD` は `+2`、`DDCB`/`FDCB` は `+2`（変位バイトと最終オペコードは operand read で M1 ではない）。これは openMSX（`Z80.hh` の `WAIT_CYCLES = 1`、M1 ベース 5 対 データシート 4）と一致します。フレーム予算は 59,659 T ステートのままなので、ウェイトを有効にすると 1 フレームあたりの実行命令数が減り、CPU ディレイループでペーシングするコード（ディレイループ駆動のソフトウェア PCM 音声など）が約 13% 速くならず実機のレートで動作します。割り込み周期（VBlank）と PSG のピッチには影響しません。

### 既知の制限

OTIR/INIR 等のブロック I/O 命令はページ境界をまたぐ場合にサイクル精度でありません。R レジスタは `_fetch()` が読むすべてのバイトでインクリメントされます——`_fetch_word()` が `_fetch()` を 2 回呼ぶため、オペランド読み出しも含みます。実機は M1 オペコードフェッチ時のみリフレッシュするため、R を乱数源として読むコードは異なる系列を見ます。

---

## 割り込み管理

### MSX1：フレームベース VBlank

MSX1 では、TMS9918A VDP の割り込み源は 1 フレームごとの VBlank のみです。`Machine.__post_init__()` でこれをコールバックとして配線します。

```python
if not isinstance(self.vdp, V9938):
    self.vdp.on_interrupt = self._vblank_interrupt
```

`_vblank_interrupt` は `cpu.int_pending = True` をセットします。割り込みは `render_frame()` が `vdp._finalize()` を呼んだ際に 1 フレームに 1 回発火します。MSX1 パスにスキャンラインごとの割り込みはありません。

### MSX2：レベルベース IRQ

MSX2（V9938）では割り込みはレベルベースです。`Machine.run_frame()` の内部ループが毎命令境界で `vdp9938.irq` をサンプリングします。

```python
cpu.int_pending = vdp9938.irq
```

`V9938.irq` は `irq_pending()` の値を反映します。

```python
def irq_pending(self) -> bool:
    ie0 = bool(self.regs[1] & 0x20)   # IE0 = R#1 ビット 5
    f   = bool(self.status & 0x80)     # F   = S#0 ビット 7（VBlank）
    ie1 = bool(self.regs[0] & 0x10)   # IE1 = R#0 ビット 4
    fh  = bool(self._status1 & 0x01)  # FH  = S#1 ビット 0（H ライン）
    return (ie0 and f) or (ie1 and fh)
```

CPU はステータスフラグがポート 0x99 の読み出しでクリアされるまで割り込みレベルを true として見続けます——ハードウェアの動作と一致します。S#0（R#15 = 0）の読み出しで F がクリアされ、S#1（R#15 = 1）の読み出しで FH がクリアされます。

### 水平ライン割り込み

`Machine.run_frame()` はフレームをスキャンラインに分割し、各スキャンラインの T ステート予算の末尾で `vdp9938.begin_scanline(L)` を 1 回呼びます。

```python
for L in range(lpf):               # lpf = 262（NTSC ライン数）
    line_end = (L + 1) * cpf // lpf
    while total < line_end:
        cpu.int_pending = vdp9938.irq
        n = cpu_step()
        ...
    vdp9938.begin_scanline(L)
    cpu.int_pending = vdp9938.irq
```

`begin_scanline(line)` は有効 IRQ ラインを `(R#19 − R#23) & 0xFF` として計算し、`line == effective_irq_line` かつアクティブ表示域内であれば S#1 ビット 0（FH）をセットします。IE1（R#0 ビット 4）は FH が `irq` をアサートするかどうかを制御します。FH は IE1 に関わらずセットされるため、ソフトウェアは割り込みを有効にせずに S#1 をポーリングできます。

FH は S#1 が読まれるまで保持されます。読み出しで FH はクリアされ、`irq` が再評価されます。

VBlank フラグは `begin_scanline(display_height)`（アクティブ表示域の次のライン）でセットされます——MSX2 パスではフレーム末尾の finalize 処理をこれで代替しています。

---

## I/O バス

### 登録とディスパッチ

`IOBus`（`msx/io.py`）は読み出し用と書き込み用の 2 つの `(start, end, handler)` タプルリストを持ちます。デバイスはマシン構築時に自身を登録します。

```python
io.register_read(0x98, 0x99, vdp.read_port)
io.register_write(0x98, 0x9B, vdp.write_port)
```

`read_port` / `write_port` は Z80 の 16 ビットポートアドレスを 8 ビットにマスク（`port &= 0xFF`）し、線形スキャンを実行します。ポートをカバーする最初に登録されたハンドラが処理します。マッチするハンドラがなければ、読み出しは 0xFF（オープンバス）を返します。

### ログ

`_logger` が取り付けられている場合、読み出しはハンドラ返却後（値が確定した後）にログ記録され、書き込みはディスパッチ前（値が呼び出し元で固定されている）にログ記録されます。いずれのログエントリもポート、値、現在の命令 PC を記録します。

### 標準ポートマップ

| ポート    | デバイス                                    |
| --------- | ------------------------------------------- |
| 0x98–0x9B | VDP（TMS9918A または V9938）                |
| 0x9A      | V9938 パレット（MSX2 のみ）                 |
| 0xA0–0xA2 | PSG（AY-3-8910）                            |
| 0xA8–0xAB | PPI（i8255）                                |
| 0xB4–0xB5 | RTC（RP5C01、MSX2 のみ）                    |
| 0xFC–0xFF | RAM マッパーセグメントレジスタ（MSX2 のみ） |

---

## VDP

### TMS9918A（MSX1）

`msx/vdp/vdp.py` に実装され、レンダラは `msx/vdp/renderer.py` にあります。16 KB VRAM、8 個のコントロールレジスタ、スクリーンモード 0–3（テキスト、グラフィック 1/2、マルチカラー）をサポートします。256×192 のアクティブフレームは `Machine.run_frame()` の末尾で `render_frame()` によって描画され、VBlank フラグのセットと `on_interrupt` コールバックの呼び出しも行われます。その後レンダラは、共有ヘルパー `msx/vdp/_geometry.py` を通じてこのフレームを一定の 256×212 出力（上下に 10 行ずつのボーダー行）にパディングします。これにより、VDP のアクティブライン数によらず全フレームが安定した 4:3 のジオメトリを保ちます（後述の V9938 `display_height` を参照）。

### V9938（MSX2）

`msx/vdp/v9938.py` に実装され、レンダラは `msx/vdp/v9938_renderer.py` にあります。TMS9918A との主な違いは次のとおりです。

- 128 KB VRAM
- 28 個のコントロールレジスタ（R#0–R#27）と 15 個のコマンドエンジンレジスタ（R#32–R#46）
- プログラマブル 16 色パレット（9 ビット GRB333）；リセット時に `_MSX2_DEFAULT_PALETTE` をロード（V9938 データブックの電源投入時の値と一致）
- 3 つのステータスレジスタ：S#0（VBlank/スプライト）、S#1（水平ライン FH）、S#2（コマンドエンジン CE/TR、リトレース HR/VR）
- ハードウェアコマンドエンジン
- `irq_pending()` によるレベルベース IRQ

`display_height` プロパティは通常 192 を返しますが、R#9 ビット 7（LN）がセットされているときは 212 を返します。`display_width` プロパティは、ワイドモード（SCREEN 6/G5・SCREEN 7/G6、M5 セット・M4 クリア）またはTEXT2（SCREEN 0 WIDTH 80、M1・M4 セットかつM3 クリア）のとき 512 を返し、それ以外では 256 を返します。

### バンディングレンダラ

フレーム途中に VDP レジスタを書き換えるゲーム——パレットやスクリーンモードを表示領域ごとに切り替えるもの——では、その変更を正しいスキャンラインで反映する必要があります。

`V9938.write_port()` は表示関連レジスタへのすべての書き込みを `_reg_write_log` に記録します。

```python
_DISPLAY_REGS = frozenset({0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 18, 19, 23})
```

各エントリは次のいずれかです。

- `(display_line, reg, value)` — レジスタ書き込み
- `(display_line, -1, (palette_index, grb_value))` — パレット書き込み（ポート 0x9A）、センチネル `reg = -1` を使用

ログは `begin_scanline(0)`（フレーム先頭）でクリアされ、`render_frame()` ではクリアされません。そのため SDL2 フロントエンドが RGB 変換時にスキャンラインごとの正しいパレットを適用できます。

`render_frame_v9938(vdp)` は `_reg_write_log` を参照してバンド境界——前のバンドのスナップショットと表示関連の値が変わったライン——を特定します。連続するバンド `[y0, y1)` はその先頭で有効なレジスタ状態を使って描画されます。各バンドレンダラには `y_start` と `y_end` が渡され、各スキャンラインは全バンドにわたって正確に 1 回だけ描画されます。

ログが空の場合（フレーム途中の書き込みなし）、`render_frame_v9938` は単一パスのレンダリングにフォールバックし、出力もパフォーマンスもバンディング前のパスと同等です。

スプライトはスプライトアトリビュートテーブル（SAT）リージョン——SAT ベース（R#5/R#11）が同じ連続バンドの最大区間——ごとに 1 回描画されます。SAT ベースがフレーム全体で一定の場合（通常ケース）、スプライトパスは 1 回だけで単一パスのレンダリングと等価です。SAT ベースがフレーム途中で変わる場合（スプライトマルチプレクサ等）、各リージョンのスプライトは固有の SAT から描画されるため、領域ごとに異なるバッファを読んで同一スプライトの重複を避けられます。

### コマンドエンジン

V9938 のハードウェアコマンドエンジンはバイト・ピクセル単位の VRAM 操作をサポートします。

| コマンド | 説明                                             |
| -------- | ------------------------------------------------ |
| HMMV     | 矩形領域をバイト値で塗りつぶす                   |
| HMMM     | 矩形領域をコピーする（バイト粒度）               |
| HMMC     | CPU から VRAM にブロック転送（バイト粒度）       |
| LMMV     | 矩形領域を色値で塗りつぶす（ピクセル粒度）       |
| LMMM     | 矩形領域をコピーする（ピクセル粒度）             |
| LMCM     | VRAM から CPU にブロック転送                     |
| LMMC     | CPU から VRAM にブロック転送（ピクセル粒度）     |
| YMMM     | Y 軸のみで矩形領域をコピーする（バイト粒度）     |
| LINE     | 直線を描画する                                   |
| PSET     | 1 ピクセルを書き込む                             |
| POINT    | 1 ピクセルの値を読み出す（結果は S#7）           |
| SRCH     | スキャンライン上で色を検索する（結果は S#8/S#9） |
| ABRT     | アクティブなコマンドを中断する                   |

コマンドはディスパッチ時に即座に実行されます。コマンド時間をモデル化するため、近似サイクル予算（`_cmd_remaining`）を `V9938.tick(n)` で減算します（各命令の T ステート数を渡す）。予算は `_CYCLES_PER_BYTE = 8` T ステート/VRAM バイトで較正されており、openMSX ゴールデンログとの比較（128×212 塗りつぶしで約 230K T ステート）から導出されています。S#2 ビット 0（CE）をポーリングするソフトウェアは、予算が尽きた後に CE がクリアされるのを確認できます。

HMMC/LMMC の転送ラッチ（`_cmd_transfer`）は R#44（COL）への書き込みでセットされます。転送の最初のピクセル/バイトはディスパッチ時の自動ロードではなく保留中の COL 書き込みから取得されます——openMSX の動作と一致します。

論理演算（IMP、AND、OR、XOR、NOT）は `v9938.py` の `_apply_log()` でピクセルごとに適用されます。

### 既知の制限

- コマンドタイミングは近似値です。V9938 データブックに記載されたコマンドごとのサイクル数は再現されません。
- VRAM への結果はディスパッチ時に書き込まれ、増分的には行われません。コマンドが名目上実行中の間に VRAM を読むソフトウェアは、CE がクリアされる前に完了した結果を見る可能性があります。
- レンダリングは遅延型です：CPU が 1 フレーム分実行され、VDP コマンドは VRAM に即座に書き込まれ、フレーム全体を 1 回のレンダリングパスで描画します——V9938 では垂直帰線開始時点、TMS9918A ではフレーム末尾です。1 フレーム内でラスタに同期した VRAM 更新（ビームレースブリット、ダブルバッファタイトル画面）は忠実に再現されません。上記のバンド化レンダラが救えるのはフレーム途中の**レジスタ**変更であって、VRAM 内容の変更ではありません。

---

## オーディオ

PSG（`msx/psg.py`）、SCC（`msx/scc.py`）、FM-PAC の OPLL（`msx/opll.py`）、Majutsushi DAC はそれぞれ 44,100 Hz・1 フレーム 735 サンプルの符号付き 16 ビットモノラル PCM を生成します。SDL2 フロントエンドの `_mix_audio()` がマシンに載っているものを合算し、16 ビットにクランプして SDL にキューイングします。

### サブフレーム・ソフトウェア PCM

`PSG.generate_samples(n, frame_start, frame_end)` は、ボリュームレジスタの高速書き込みによるソフトウェア PCM を再現します。`write_port` は各レジスタ書き込みを `_get_cycle`（`machine.cycle_count` に接続）で `(cycle, reg, value)` としてタイムスタンプします。`generate_samples` はフレーム最初の書き込み時点のレジスタ/ジェネレータ状態まで巻き戻し、各書き込みのサンプル位置 `clamp((cycle - frame_start) * n // (frame_end - frame_start), 0, n)` で区切った連続セグメントとしてバッファを再レンダリングし、トーン/ノイズ/エンベロープ状態をセグメント間で引き継ぎます。書き込みが無いフレームは従来と同一（バイト単位で一致）の単一スナップショット高速パスを通るため、通常の音楽はコストゼロです。

### トーン積分（period-0 PCM キャリア）

ソフトウェア PCM はトーンジェネレータを period 0 で有効にしたまま使います — これは超音波（約 223 kHz）キャリアで、実機のアナログ出力では約 50% デューティの平均に平滑化され、ボリュームレジスタが PCM を担います。`tone_out` を 44.1 kHz で点サンプリングするとこのキャリアが full/zero のチョッピングにエイリアシングし、約半分のサンプルがゼロになります。そこで各チャンネルのトーンを **出力サンプル単位で積分** します：内側ループが矩形波の high だった PSG tick 数（`hi0/hi1/hi2`）を数え、ミキサがチャンネル振幅を `hi / ticks` でスケールします。可聴トーン（サンプル内でトグルしない）では `hi` は `ticks` か `0` なので点サンプリングとビット単位で同一。超音波キャリアのみデューティ平均に帯域制限されます。

### 出力ローパスフィルタ

`msx/audio_filter.py`（`BiquadLowPass`）は状態付き 2 極バターワースローパス（8 kHz、RBJ 係数、Direct Form I）で、フロントエンドで最終ミックスバッファに `SDL_QueueAudio` 前に適用され、実機 MSX 音声出力段の RC フィルタを模します。単一インスタンスが `x1/x2/y1/y2` 状態をフレーム間で保持します（オーディオデバイスオープン時にリセット）。点サンプリング合成が Nyquist 付近に残す高域のイメージング/エイリアシングを、音声処理コストの約 1% で除去します。実機 PSG 音楽のエネルギーはカットオフより十分低いため、可聴帯域と全体音量はほぼ不変です。

## メモリとスロットシステム

### MSX1 スロットレイアウト

`Memory`（`msx/memory.py`）はフラット 64 KB アドレス空間を 4 ページ × 4 スロットのディスパッチを通じてマッピングします。スロット選択レジスタ（ポート 0xA8、PPI 経由）が各 16 KB ページを担当するスロットを決定します。レジスタのビット `[2N+1 : 2N]` がページ N のスロットを選択します。

デフォルトの MSX1 レイアウト：

| スロット | 内容                                                   |
| -------- | ------------------------------------------------------ |
| 0        | BIOS ROM（読み取り専用）                               |
| 1        | マッパー経由のカートリッジ ROM                         |
| 2        | 第 2 カートリッジまたはオープンバス（読み出しは 0xFF） |
| 3        | ページ 2–3（0x8000–0xFFFF）の 32 KB RAM                |

### MSX2 サブスロット

MSX2 ではスロット 3 が拡張されます（`sub_slot_enabled = True`）。0xFFFF の読み出しは `~sub_slot_reg` を返し、ソフトウェアがサブスロット対応を検出できるようにします。サブスロットレジスタ（`sub_slot_reg`）は拡張プライマリ内の各ページを担当する 4 つのセカンダリスロットを選択します。

デフォルトの MSX2 レイアウト：

| サブスロット | 内容                            |
| ------------ | ------------------------------- |
| 3-0          | C-BIOS サブ ROM（読み取り専用） |
| 3-1          | 未使用                          |
| 3-2          | RAM マッパー経由の 128 KB RAM   |
| 3-3          | 未使用                          |

### RAM マッパー

`RamMapper`（`msx/ram_mapper.py`）は 128 KB を各 16 KB の 8 セグメントに分割します。ポート 0xFC–0xFF の 4 つのセグメントレジスタが各 CPU ページを独立して制御します。

| ポート | ページ | アドレス範囲  |
| ------ | ------ | ------------- |
| 0xFC   | 0      | 0x0000–0x3FFF |
| 0xFD   | 1      | 0x4000–0x7FFF |
| 0xFE   | 2      | 0x8000–0xBFFF |
| 0xFF   | 3      | 0xC000–0xFFFF |

レジスタにセグメント番号（0–7）を書き込むと、そのページのマッピングが即座に切り替わります。

---

## フロッピーディスク（FDC）

フロッピーサブシステムは汎用層（`msx/fdc/`）として実装されており、コントローラチップや接続方式を追加する際に `Memory` へ手を入れる必要はありません。現在はそれぞれ 2 種類——WD2793 と Sony/Philips 接続方式、TC8566AF とその専用接続方式——が配線済みです。

| 層 | ファイル | 役割 |
| --- | --- | --- |
| ディスクイメージ | `disk_image.py`（`DskDiskImage`） | `*.dsk` セクタイメージを読み込み、FAT12 BPB からジオメトリ（バイト/セクタ、総セクタ数、セクタ/トラック、ヘッド数）を導出。書き込みをバッファし、終了時にファイルへフラッシュ |
| ドライブ | `disk_drive.py`（`DiskDrive`） | 1 台の物理ドライブ：マウント中のイメージ保持、ヘッド位置管理、セクタ読み書き |
| コントローラ | `wd2793.py`（`WD2793`）、`tc8566af.py`（`TC8566AF`） | FDC チップ。`WD2793`：Type I–IV コマンドデコード、ステータスレジスタ、データ/トラック/セクタレジスタ、`abort()`。`TC8566AF`（uPD765 系）：コマンド/実行/結果フェーズモデル、Main Status Register、非 DMA 転送、直接アドレス指定できるトラック/セクタレジスタは持たない |
| インターフェース | `interface.py` | コントローラとメモリバスの接続方式。どちらも DISK ROM を 0x4000–0x7FFF にマップする。`SonyPhilipsInterface`（= openMSX PhilipsFDC）は WD2793 のレジスタを 0x7FF8–0x7FFF に置き、ディスク交換ビットを消費。`TC8566AFInterface` は 2 本の制御レジスタ・Main Status Register・Data Register を 0x7FF8–0x7FFB に置く。`swap()` による実行中のマウント/イジェクトは両者で共有 |

どのコントローラと接続方式を使うかはマシン YAML の `fdc:` ブロックで宣言するため、同梱の 2 機種はデータの違いだけで区別されます。Sony HB-F1XD（`hb_f1xd`）は WD2793 と DISK ROM をスロット 3 サブスロット 0 に置き、サブスロット 3 に 64 KB のフラット RAM を併設します。Panasonic FS-A1F（`fs_a1f`）は TC8566AF を使い、4 つのセカンダリスロットに役割を分散します——サブスロット 0 が RAM、1 が SUB ROM、2 が FDC です。`--fdd1`/`--fdd2` でドライブ A/B にイメージをマウントし、デバッガの `fdd1`/`fdd2` コマンドで実行中に入れ替えできます。本実装は Disk BASIC を起動し、`CALL FORMAT` に対応し、ファイルの読み書き（終了時に書き戻し）を行います。フロッピーインターフェースを持たないマシンでは `machine.fdc` は `None` です。

## マシンと拡張の YAML スキーマ

`msx/machine_loader.py` は 3 種類の YAML ——デバイス定義（`config/devices/*.yaml`）、マシン仕様（`config/machines/*.yaml`）、拡張オーバーレイ（`config/extensions/*.yaml`）——の唯一の正とするソースです。3 種類とも手書きで検証する `TypedDict` 形状（`DeviceEntryYaml`、`MachineEntryYaml`、`ExtensionOverlayYaml`、およびそれらのネストした形状）であり、スキーマ検証ライブラリは使っていません——本節で扱う各フィールドは、ローダのどこかにある明示的な `.get()` 呼び出しに対応しています。読まれていないキーは、YAML 側のコメントが何を主張していようと単なるドキュメントにすぎません。各プライマリスロットが何を保持できるかのユーザー向けまとめは
[README_extension.md の「Slot model」節](../README_extension.md#slot-model)（英語）を参照してください。本節ではそれを生成する YAML 構文そのものを解説します。

### デバイス YAML（`config/devices/*.yaml`）

| フィールド | 型 | 必須 | 読み出し箇所 |
| --- | --- | --- | --- |
| `id` | string | はい | ファイル名の stem と一致しなければ `load_device_registry` が `MachineLoadError` を送出 |
| `type` | string | はい | 存在チェックのみで値そのものは検証されない（慣例で `io_device` 等） |
| `implemented` | bool | いいえ（デフォルト `true`） | `false` の場合、`builtin_devices` 解決時に stderr 警告付きでスキップされる（ハード失敗ではない）——エミュレーションの実装より先にデバイス定義だけをコミットできる |
| `io_ports` | int のリスト | いいえ | 先頭要素と末尾要素がそのデバイスの `(start, end)` I/O レンジになる。キーが無ければ `_DEFAULT_IO_PORTS` にフォールバック |

それ以外のキー（`name`、`chip`、`controls`、`vram_kb`、`segment_size_kb`、
`keyboard_type` 等）は `_DeviceDef.raw` に格納されますが、汎用コードからは
**読まれません**。`dev.raw.get(...)` はコードベース全体でちょうど2箇所からしか
呼ばれていません——上記の `io_ports` と、下記の `keyboard_type`（`ppi8255`
のみ）です。`vdp_v9938.yaml` の `vram_kb: 128` はどこからも読まれません——
V9938 の VRAM サイズは `msx/vdp/v9938.py` にハードコードされた定数
`_VRAM_SIZE = 131072` で固定されており、このキー（および後述するマシン側の
`overrides: {vram_kb: 128}` という echo）は意図を記すだけで、実際に動作中の
エミュレータには何の効果もありません。

```yaml
id: vdp_v9938
type: io_device
implemented: true
name: Yamaha V9938 Video Display Processor (MSX2)
chip: v9938
io_ports: [0x98, 0x99, 0x9A, 0x9B]
controls: video_display_processor
vram_kb: 128    # ドキュメント目的のみ -- 上記の注記を参照
```

### マシン YAML（`config/machines/*.yaml`）

| フィールド | 型 | デフォルト | 備考 |
| --- | --- | --- | --- |
| `schema_version` | int | — | 必ず `1` |
| `id` | string | — | ファイル名の stem と一致しなければならない |
| `generation` | string | — | `msx1` または `msx2`。それ以外はエラー |
| `name` | string | `id` | 表示名のみ |
| `rom_base` | string | `roms/cbios` | すべての `rom:` ブロックの `file` を解決する基準ディレクトリ（プロジェクトルート相対） |
| `video_standard` | string | `ntsc` | `ntsc` → 1 フレーム 59,659 T ステート/262 ライン、`pal` → 71,364/313（`cbios_msx1_eu` が使用） |
| `cpu.m1_wait_states` | int | `0` | Z80 の M1 サイクルごとに追加される T ステート数 — 前述の[CPU エミュレーション](#cpu-エミュレーション)を参照 |
| `slots.primary` | マッピング | — | `0`〜`3` をキーとする。読まれるのは `0` と `3` のみ（後述） |
| `builtin_devices` | リスト | `[]` | 後述 |
| `io_device` | マッピング | — | 任意のマシンレベル・グローバル I/O デバイス。拡張オーバーレイの `io_device`（後述）と同じ形状 |

#### スロット 0（固定）

```yaml
0:
  content:
    - rom: {file: cbios_main_msx2.rom, size_kb: 32, pages: [0, 1], sha1: null}
    - rom: {file: cbios_logo_msx2.rom, size_kb: 16, pages: [2], sha1: null}
```

`content` は `{rom: {...}}` エントリのリストです。`_parse_slot0` はこの中から
`pages` に `0` または `1` を含むエントリ（メイン BIOS ROM——必須。無ければ
`MachineLoadError`）と、`pages` に `2` を含むエントリ（0x8000–0xBFFF の任意の
ロゴ ROM）を走査して取り出します。`sha1: null` はそのROMのハッシュ検証を
無効化します。それ以外の文字列値が指定されていれば、読み込んだファイルと
照合されます。

#### スロット 1・2：マシン YAML からは読まれない

すべてのマシン YAML は `slots.primary` に `1: {type: cartridge}` と
`2: {type: cartridge}` を宣言していますが、`load_machine_spec` は
`primary[1]` も `primary[2]` も読みません——読むのは `primary[0]` と
`primary[3]` だけです。この 2 つのエントリは宣言された意図を示すだけの
ものです。スロット 1 のカートリッジ（位置引数の ROM + `--mapper`）と
スロット 2 のカートリッジ/マッパー（`--slot2`/`--mapper2`）またはオーバーレイ
（`--extension`）は、すべて `build_machine()` 自身の引数から解決されます
——これは CLI 側の帰結であって、YAML の `slots.primary.1`/`.2` の中身とは
無関係です。

#### スロット 3 — MSX1

```yaml
3:
  size_kb: 32   # 任意、デフォルトは 32
```

`_parse_slot3_msx1` が読むのは `size_kb`（デフォルト `32`）のみです——
0x8000–0xFFFF のフラット RAM で、それ以上の構造はありません。

#### スロット 3 — MSX2

```yaml
3:
  expanded: true
  secondary:
    0:
      content:
        - rom: {file: cbios_sub.rom, size_kb: 32, pages: [0, 1], sha1: null}
    2:
      type: ram
      mapper: standard
      size_kb: 128
```

`expanded: true` によってスロット 3 は 4 つのセカンダリスロット
（`secondary.0`〜`.3`、`slots.primary` と同じキーの付け方）に切り替わります。
`_parse_slot3_msx2` はこれらを独立に走査し、以下を解決します。

- `content` を持ち、`rom.pages` に `0` または `1` を含む最初のサブスロット
  → SUB ROM（任意。そのサブスロット番号は固定ではなく記録される）
- `mapper: standard` を持つ最初のサブスロット → `RamMapper`。サイズは
  `size_kb`（デフォルト `128`。16 の正の倍数でなければならず、
  `_check_ram_mapper_size_kb` で検証、違反すると `MachineLoadError`）
- `type: ram` を持ち `mapper: standard` を持たない最初のサブスロット →
  フラット（非マッパー）RAM。サイズは `size_kb`（デフォルト `64`）——
  `hb_f1xd` の実機固定 64 KB で使用
- `fdc:` ブロックを持つ最初のサブスロット → [フロッピーディスク（FDC）](#フロッピーディスクfdc)を参照

`mapper: standard` サブスロットと `type: ram` サブスロットは `secondary`
マッピング全体で排他です（`Memory` は RAM マッパーとフラット RAM を同時に
ホストできません）——両方宣言すると `MachineLoadError`。フラット RAM が
SUB ROM や FDC と同じサブスロット番号を共有することも拒否されます。
`Memory` の書き込みパスにはどちらかへの誤った書き込みを防ぐガードが
無いためです。どのサブスロット番号も、担う役割に関わらず `0`〜`3`
（`_check_subslot_index`）でチェックされます。

`fdc:` ブロックのフィールド（スロット 3 のサブスロット内でのみ意味を持つ）：

| フィールド | 型 | デフォルト | 備考 |
| --- | --- | --- | --- |
| `rom` | `rom:` ブロック | — | 必須（DISK ROM） |
| `controller` | string | `wd2793` | `wd2793` または `tc8566af` |
| `connection_style` | string | `sony` | `sony` または `tc8566af` |
| `drives` | int | `1` | 正の値でなければならない |

`(controller, connection_style)` の組は `(wd2793, sony)` または
`(tc8566af, tc8566af)` のいずれかでなければなりません——それぞれの値が
個別には有効でも、組み合わせが違えばエラーになります（例：`wd2793` +
`tc8566af` は拒否される）。

#### `builtin_devices`

```yaml
builtin_devices:
  - ref: ppi8255
    overrides: {keyboard_type: jp}
  - ref: vdp_v9938
    overrides: {vram_kb: 128}   # 受理されるが読まれない（上記デバイス YAML の節を参照）
  - ref: psg_ay8910
  - ref: rtc_rp5c01
  - ref: memory_mapper_standard
```

各エントリの `ref` はデバイスレジストリ（`load_device_registry` の出力）に
対して解決できなければならず、できなければ `MachineLoadError` です。
`overrides` は自由形式のマッピングですが、`_parse_builtin_devices` が
実際に読むキーは 1 つだけ——`keyboard_type`（`int` または `jp`）、しかも
`ref: ppi8255` のときだけです。それ以外の `overrides` キーは、どの `ref`
に対しても受理されて無視されます。`ref: vdp_v9938`/`rtc_rp5c01`/`ppi8255`
の存在は `MachineSpec` が持つ `has_v9938`/`has_rtc`/キーボードレイアウトの
各フラグを設定します。それ以外の `ref` は自身の `io_ports` レンジのみを
提供します。

#### マシンレベルの `io_device`

```yaml
io_device:
  device: kanji_rom
  rom: {file: some_kanji_font.rom, size_kb: 256, sha1: null}
```

拡張オーバーレイの `io_device`（後述）と同じ形状です——マシン自身が
提供する、スロットに依存しないグローバル I/O デバイスで、同じ
`kanji_rom` のみの `_KNOWN_IO_DEVICES` 集合に対して検証されます。
現状、これを宣言している同梱マシン YAML はありません。`kanji_rom`
デバイスの現在の供給元は `--extension hbi_j1`/`msxdos2_512k_kanjirom`
のみで、いずれも拡張オーバーレイ自身の `io_device`（後述）経由です。

### 拡張オーバーレイ YAML（`config/extensions/*.yaml`）

| フィールド | 型 | デフォルト | 備考 |
| --- | --- | --- | --- |
| `schema_version` | int | — | `1` でなければならない |
| `id` | string | — | ドキュメント目的のみ——`load_extension_overlay` はこれを一切読まない。ファイルはファイル名の stem のみ（`config/extensions/<--extension の値>.yaml`）で特定される。マシン/デバイス YAML の `id` とは異なる |
| `slot` | int | `2` | `2` でなければならない——`--extension` が到達できる唯一のスロット |
| `shape` | string | `flat` | `flat` または `expanded` |
| `rom_base` | string | `""`（プロジェクトルート） | 以下の各 `rom:` ブロックの基準ディレクトリ |

#### フラット形状（`shape: flat`、または省略時）

```yaml
device: fmpac
rom: {file: fmpac.rom, size_kb: 64}
sram: {size_kb: 8, save_file: saves/sram/fmpac.sram}
```

`device` は `fmpac` または `scc_i_cart`（`_KNOWN_EXTENSION_DEVICES`）で
なければなりません——同じ `device` という名前でも、後述の拡張形状の
サブスロットデバイスとは別の名前空間です。`fmpac` は `rom:` ブロックが
必須、`scc_i_cart` は不要です（RAM は空の状態で開始し、ファイルはロード
されません）。フラットオーバーレイのデバイスは、プライマリスロット 2 の
マッパーを無条件かつ丸ごと置き換えます。

#### 拡張形状（`shape: expanded`）

```yaml
shape: expanded
slot: 2
rom_base: roms/hbi_j1
subslots:
  0:
    device: halnote
    rom: {file: hbi-j1_msx-je.rom, size_kb: 1024, sha1: null}
    sram: {size_kb: 16, save_file: saves/sram/hbi-j1_msx-je.sram}
  1:
    device: flat_rom
    rom: {file: hbi-j1_kanjibasic.rom, size_kb: 32, sha1: null}
io_device:
  device: kanji_rom
  rom: {file: hbi-j1_kanjifont.rom, size_kb: 256, sha1: null}
```

`subslots` は必須かつ空であってはならないマッピングで、`0`〜`3`
（MSX2 スロット 3 のサブスロットと同じ番号範囲・同じ
`_check_subslot_index`）をキーとします。各エントリの `device` は
`halnote`、`flat_rom`、`ram_mapper`、`ascii8`、`ascii16`
（`_KNOWN_EXPANDED_SUBSLOT_DEVICES`）のいずれかでなければなりません。

| `device` | 必須項目 | 構築されるもの |
| --- | --- | --- |
| `halnote` | `rom:` | `HalnoteMapper`（1 MB ROM、`sram:` は任意） |
| `flat_rom` | `rom:` | `FixedPageMapper(base=0x4000)`——0x4000–0xBFFF にのみ現れる |
| `ascii8` | `rom:` | `Ascii8Mapper` |
| `ascii16` | `rom:` | `Ascii16Mapper` |
| `ram_mapper` | `size_kb:`（エントリに直接指定、`rom:` は無し） | `RamMapper`。`size_kb` は 16 の正の倍数でなければならない |

`ram_mapper` サブスロットは 1 オーバーレイにつき最大 1 つまでです——
2 つあると標準メモリマッパー I/O ポート（0xFC–0xFF）の二重登録に
なるためです。拡張オーバーレイを MSX1 マシンに適用すると常に
`MachineLoadError` になります（メモリマッパー・サブスロットに対応する
MSX1 標準ハードウェアは存在せず、現在の拡張オーバーレイはいずれも
MSX2 世代のハードウェアをモデル化しているため）。`ram_mapper`
サブスロットは、既にスロット 3 に自前の RAM マッパーを持つマシン
（`has_ram_mapper=True`）とも衝突します——2 つのマッパーが同じポートを
奪い合うことになるためです。

任意のトップレベル `io_device` は、*3 つめの*独立したデバイス名前空間
`_KNOWN_IO_DEVICES = {kanji_rom}` に対して検証されます——`subslots`
の `device` として `kanji_rom` は使えず、`io_device` の `device` として
`halnote`（や他のサブスロットデバイス）も使えません。
マシンレベルの `io_device` と拡張オーバーレイの `io_device` の両方が
`kanji_rom` に解決された場合、`build_machine` はエラーを送出します
（ポート 0xD8–0xDB は 2 つの漢字ROMデバイスを同時にはサービスできない
ため）。

#### `rom:` と `sram:` ブロック

両形状とも、同じネストしたブロックスキーマを共有しています。これは
`_parse_rom_entry` によってパースされます（スロット 0 の ROM 群、MSX2 の
SUB ROM、FDC の DISK ROM でも使われる——ローダ内の ROM を扱うすべての
フィールドで共有される、唯一の実装です）。

| `rom:` のフィールド | 型 | 必須 | 備考 |
| --- | --- | --- | --- |
| `file` | string | はい | `rom_base` を基準に解決される |
| `size_kb` | int | いいえ（デフォルト `0`） | ドキュメント目的のみ——実際のファイルサイズとは照合されない |
| `pages` | int のリスト | いいえ（デフォルト `[]`） | スロット 0 と SUB ROM でのみ意味を持ち、どのエントリがメイン/ロゴ/SUB ROM かを選択する |
| `sha1` | string | いいえ | `null`/省略でハッシュ検証を無効化 |

| `sram:` のフィールド | 型 | 備考 |
| --- | --- | --- |
| `size_kb` | int | ドキュメント目的のみ——無視される。各デバイスの SRAM サイズは Python 側の定数（`HALNOTE_SRAM_SIZE`、`FMPAC_SRAM_SIZE` 等）で決まる |
| `save_file` | string | SRAM の永続化先パス（プロジェクトルート相対）。省略すると永続化しない |

## マシン YAML ローダ

### 2 パス解決

`msx/machine_loader.py` はハードウェアトポロジを 2 パスで解決します。

1. **デバイスレジストリパス。** `load_device_registry(config_dir)` が `config/devices/` 以下のすべての `*.yaml` ファイルをデバイス `id` をキーとする辞書に読み込みます。各ファイルは 1 つのハードウェア（VDP 種別、PSG、PPI、RTC、RAM マッパー）をマシンコンテキストなしに記述します。

2. **マシンスペックパス。** `load_machine_spec(machine_id, config_dir, device_registry, project_root)` が `config/machines/<machine_id>.yaml` を読み込み、必須フィールドを検証し、`builtin_devices` の各 `ref` をレジストリに対して解決し、`MachineSpec` データクラスを返します。未解決の `ref`、未知の `schema_version`、必須 ROM エントリの欠如はファイル名とフィールド名を添えた `MachineLoadError` を発生させます。

### build_machine()

`build_machine(spec, cartridge, mapper, ...)` は解決済みの `MachineSpec` から `Machine` を構築します。`spec.generation` フィールドが具体的な型を決定します。

- `"msx1"` — `VDP`（TMS9918A）、フラットなスロット 3 RAM、RTC および RAM マッパーなし
- `"msx2"` — `V9938`、サブ ROM と `RamMapper` を持つ拡張スロット 3、`RTC`

`implemented: false` と記述されたデバイス YAML エントリは、ロード時に stderr に警告を出してスキップされます。残りのマシンは正常に起動するため、エミュレーションが未実装のうちにデバイス定義だけをコミットしても起動に支障は出ません。現在 `config/devices/` にあるデバイスはすべて `implemented: true` です。

---

## 移植性

本実装は純粋な Python 3.10+ であり、SDL2 フロントエンドを除いて C 拡張やネイティブバインディングを持ちません。コアロジックを静的型付けのシステム言語（Rust、C++）に移植しやすくするために、いくつかの設計上の決定が行われています。該当する各ソースファイルには _Portability note_ コメントとして根拠が記録されており、本セクションはその要約です。

### 移植性を意識した設計

以下のパターンは静的型付けのターゲットへ直接マッピングできます。

- **オペコードディスパッチ**（`msx/cpu/z80.py`、`msx/cpu/opcodes_main.py`）— `_DISPATCH` はインポート時に一度だけ構築される 256 要素のフラットなリストで、各要素は plain な関数です。Rust への移植では関数ポインタの配列またはオペコードバイトへの `match` としてそのまま表現できます。
- **レジスタファイル**（`msx/cpu/registers.py`）— プライマリレジスタはすべての書き込み箇所で `& 0xFF` / `& 0xFFFF` の明示的なマスクを施した Python `int` フィールドとして格納されており、多倍長整数として扱われていません。シャドウレジスタは `A_`、`BC_` 等の個別の named フィールドです。
- **VRAM と RAM** — サイズ固定の `bytearray` オブジェクト（V9938 は `_VRAM_SIZE = 131072`、TMS9918A は 16384、RAM マッパーはセグメント配列）を使用し、全体的にスライス・インデックスアクセスで操作します。リストや辞書ベースのストレージは使いません。
- **I/O バスディスパッチ**（`msx/io.py`）— `list[tuple[int, int, Callable]]` に対する線形スキャンです。辞書検索もリフレクションも使いません。登録ハンドラ数はマシン設定ごとに少数で固定されています。
- **コンポーネント構造体** — すべてのハードウェアコンポーネントは `@dataclass(slots=True)` です。フィールドはすべて型付きで明示的に宣言されており、`__getattr__`/`__setattr__` のマジックはありません。`slots=True` によりインスタンスごとの `__dict__` が排除され、フィールドレイアウトが明示的になります。
- **タイミング定数** — `CYCLES_PER_FRAME`、`_TSTATES_PER_LINE`、`_HBLANK_START`、`_CYCLES_PER_BYTE` 等はモジュールレベルの整数定数であり、実行時に導出されません。

### 移植時に対応が必要な Python 固有パターン

以下のパターンは直接的な静的型付けの対応物を持たない Python の言語機能に依存しています。各パターンには Rust/C++ の移植で使うべき手法を示すコメントがソースに記述されています。

#### バスフックとしての再代入可能なバインドメソッド

`Z80` は `read_byte` / `write_byte` / `read_port` / `write_port` を `Callable` フィールドとして保持します。`Machine.__post_init__` がランタイムにバインドメソッドを代入して配線します。ウォッチポイントを有効にすると、`cpu.read_byte` / `cpu.write_byte` がプレーンなメモリハンドラとウォッチポイントトラップ版（`Machine._read_with_watch` / `_write_with_watch`）の間で、`Machine.set_watchpoints` により再度スワップされます。

Python では `Callable` フィールドが任意の `__call__` オブジェクトを保持できるためこれが可能ですが、Rust/C++ では構造体フィールドへのランタイムメソッドスワップはできません。移植では `trait MemoryBus`（または `enum { Normal, Watchpoint }`）としてバスを表現し、アクセスごとの分岐をなくします。

#### 16 ビットペアに対する computed property としてのレジスタ 8 ビット半値

16 ビットの `BC`、`DE`、`HL` を `int` フィールドとして保持しつつ、`Registers` はその半値 `B`/`C`、`D`/`E`、`H`/`L` を、シフト・マスクを行う `@property` getter/setter として公開します（`Registers.B`/`C` とその同類）。1 バイトの半値を読み書きする各オペコードがディスクリプタプロトコルを通り、ホットな命令パスにコールフレームが増加します。

Rust/C++ への移植では、8 ビット半値を plain な `u8` フィールド（またはインライン getter/setter メソッド）として格納し、16 ビットペアは必要時に導出することでプロパティコールのオーバーヘッドをなくします。

#### PSG エンベロープの多倍長整数

Python の整数は任意精度です：`-1 & 0x20 == 0x20`。PSG エンベロープジェネレータは `_env_step` が負になった際にこれを利用しています——ビット 5 が負の Python int でもアンダーフローフラグとして機能します（`PSG._env_step`）。

Rust/C++ の符号なし型はアンダーフロー時にラップするため、同じ式は `0x20` ではなく 0 を生成します。移植では符号付き型（`i32`）または明示的な `wrapping_sub` / ビットフィールドオーバーレイを使ってフラグを保持する必要があります。

#### レンダラの `bytes.translate()` LUT キャッシュ

V9938 の G4/G6 および G5 スキャンラインごとのピクセル展開器は、`bytes.translate()` テーブルを `(tp: bool, border: int)` をキーとする辞書 `_G46_LUT_CACHE` / `_G5_LUT_CACHE` にメモ化しています。`bytes.translate()` は CPython の組み込み関数であり、256 バイトのルックアップテーブルを C 側で処理するため、Python でこの変換を行う最速の手段です。辞書キャッシュにより、毎スキャンラインで同一テーブルを確保することを避けています。

`bytes.translate()` もキーごとの辞書ハッシュも、システム言語においては自然な構成要素ではありません。Rust/C++ への移植では、初期化時に固定の `[u8; 256]` 配列を事前計算します——`(tp, border)` の組み合わせは全 32 通りしかなく、ハッシュなしで直接インデックスアクセスできます。

#### 割り込み・トレーサフックの Callable フィールド

`VDP`（TMS9918A）と `V9938` はともに `on_interrupt`、`tracer`、`_get_pc`、`_get_cycle` を nullable な `Callable` フィールドとして保持します。これらは配線時に代入され、関連するイベントごとに呼び出されます。Python では `Callable[[], None] | None` として型付けできますが、静的型付けでの直接対応物はありません。

Rust/C++ への移植では `Option<Box<dyn Fn()>>` トレイトオブジェクト（または同等物）としてモデル化するか、コンパイル時ジェネリクスで機能フラグを立て、リリースビルドでのコール当たりの分岐をなくします。

#### フレームタイマーのスピンループ

`FrameTimer.tick()` はフレームデッドラインまでの最後の 1 ミリ秒未満の区間でビジーウェイトループに `time.perf_counter()` を使います。Python ではスケジューラへのヒントを出す手段がありません。

Rust への移植では同じループ内に `std::hint::spin_loop()` を挿入することで、スリープせずに CPU パイプラインヒントを出せ、SMT コアでの消費電力削減とタイミングジッターの低減が期待できます。
