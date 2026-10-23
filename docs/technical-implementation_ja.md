# 技術実装ドキュメント

[← README_ja.md](../README_ja.md)

この文書は MSX1/MSX2 エミュレータの内部構造を説明する。CPU の実行、割り込みの処理、I/O の振り分け、VDP の描画、メモリまわりを扱う。

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

Z80 CPU は `msx/cpu/z80.py` に実装している。オペコードの振り分け表 `_DISPATCH` は、256 個の callable を並べたフラットなリスト。`msx/cpu/opcodes_main.py` の `opcodes_main._build_dispatch()` がインポート時に一度だけ作り、`z80.py` がモジュールレベルの定数として束縛する。

```python
_DISPATCH: list[Callable[[Z80], int]] = _opcodes_main._DISPATCH
```

命令の実行は 1 回のインデックス呼び出しで済む。辞書の検索も `match`/`case` も使わない。

```python
n = _DISPATCH[opcode](cpu)
```

CB、DD、ED、FD のプレフィックス表も同じ方式。メインの振り分けがプレフィックスのバイトを見つけると、次のバイトを読んで対応するプレフィックス表に渡す。

### 実行ループ

`Z80.step() -> int` は 1 命令を実行する。以下のコストはすべて T ステート（Z80 の基本クロック単位）で表す。

1. `nmi_pending` がセットされていれば、PC をプッシュして 0x0066 にジャンプし、11 T ステートを返す。
2. `iff1` と `int_pending` がどちらも true で、`ei_pending` がセットされていなければ、割り込みを受け付ける。モード 1 は PC をプッシュして 0x0038 にジャンプし、13 T ステート。モード 2 は PC をプッシュし、`I<<8 | data_bus` からベクタを読んでハンドラにジャンプし、19 T ステート。
3. `ei_pending` がセットされていれば、クリアする。EI が割り込みを有効にするのは、その次の命令の後から。手順 2 が `ei_pending` を見て、この手順がクリアするので、手順 2 で受け付けるはずの割り込みはちょうど 1 ステップ待つ。
4. `halted` がセットされていれば、オペコードを読まずに `4 + m1_wait_states` T ステート進める。純粋な Z80 コアなら 4 で、MSX の M1 ウェイトもここで加わる。
5. それ以外は、`instruction_pc` に現在の PC を記録し、`_fetch_opcode()` でオペコードを読んで振り分ける。

`_fetch()` は `read_byte(PC)` で 1 バイト読み、PC を 1 進める。`_fetch_opcode()` は同じことをしたうえで、M1 サイクルごとに 1 回 R を進める。M1 サイクルにあたるのは、主オペコードと、CB/DD/ED/FD プレフィックスの次のオペコードバイト。オペランドとディスプレースメントのバイトは R を変えない。`DDCB`/`FDCB` のディスプレースメントと最後のオペコードも同じ。R が数えるのは bit 0〜6 だけ。bit 7 は保持され、`LD R,A` のようなレジスタ全体への書き込みでしか変わらない。実機の SONY HB-F1XD と HB-10 でも R の進み方は同じ。

### タイミング

`step()` はその命令が使った T ステート数を返す。`Machine.run_frame()` はこの値を、フレームごとの合計と `machine.cycle_count` に足す。NTSC の 1 フレームの予算は **59,736 T ステート**で、1 ラインあたり 228 T ステート × 262 ライン。1 ライン 228 T ステートは実機の TMS9918A/V99x8 のラスタタイミングで、openMSX の共通のラスタクロックモデルでも両方の VDP 系列でこの値になっている。

この予算は、Z80A のクロック（3.579545 MHz）を 60 Hz で割った値とは違う。実機の MSX の NTSC のフィールドレートは、ちょうど 60 Hz ではなく約 59.92 Hz。PAL のマシンは 1 フレーム 313 ラインで、予算は 71,364 T ステート（[マシン YAML](#マシン-yamlconfigmachinesyaml) の `video_standard` を参照）。

#### MSX M1 ウェイトステート

実機の MSX は、Z80 の M1 サイクル（オペコードフェッチ）ごとに 1 ウェイトを入れる。コアはこれを、設定できる `Z80.m1_wait_states` フィールドで表す。デフォルトは `0` で、データシートどおりの純粋な Z80 になるので、MSX 以外のシステムにもコアを使える。MSX のマシン YAML は `cpu.m1_wait_states: 1` を設定し、`machine_loader` がこの値を `Z80(...)` のコンストラクタに渡す。

ウェイトは命令のバイトごとではなく、M1 サイクルごとに足す。`step()` は主オペコードのフェッチに `m1_wait_states` を足し、各プレフィックスのハンドラ（`_op_prefix_cb/dd/fd/ed`）が 2 回目の M1 にもう一度足す。命令ごとの増え方は次のようになる。

- プレフィックスのない命令は、オペランドの長さに関係なく `+1`。
- `CB`/`ED`/`DD`/`FD` の命令は `+2`。
- `DDCB`/`FDCB` も `+2`。ディスプレースメントと最後のオペコードは、M1 サイクルではなくオペランドの読み出しだから。

この数え方は openMSX（`Z80.hh` の `WAIT_CYCLES = 1`、M1 の基本が 5、データシートでは 4）と同じ。フレームの予算は 59,736 T ステートのままなので、ウェイトの分だけ 1 フレームに実行できる命令が減る。おかげで、CPU のループで時間を計るコード（遅延ループで鳴らすソフトウェア PCM など）が約 13% 速く動いてしまうことはなく、実機と同じ速さになる。ウェイトは割り込みの周期（VBlank）にも PSG の音程にも影響しない。

### 既知の制限

OTIR/INIR などのブロック I/O 命令は、ページ境界をまたぐとサイクル単位では正確でない。非公開のフラグ Y（bit 5）と X（bit 3）は、資料にある命令ごとの規則に従う。Y と X を決める次の 3 つの要因はモデル化していない：内部レジスタ MEMPTR（`BIT n,(HL)` では 0 のまま）、繰り返すブロック命令の PC、直前の命令（`SCF`/`CCF`）。

---

## 割り込み管理

### MSX1：レベルベース VBlank IRQ

MSX1 の TMS9918A VDP の割り込み源は 1 つだけで、垂直帰線期間の始まりに立つ VBlank。割り込み線は `F && IE0`（S#0 bit 7 と R#1 bit 5）のレベル。`VDP.irq` がこのレベルを保持し、どちらかの入力が変わると VDP が計算し直す。

- `Machine.run_frame()` が `display_height` のスキャンライン境界でフレームを描画した後、`VDP.begin_vblank()` が F をセットする。ブレークや一時停止で途中で終わったフレームではセットしない。
- S#0 を読むと F がクリアされる。
- R#1 への書き込みで IE0 が変わる。

そのため、F が立ったままプログラムが IE0 をセットすると、実機と同じくすぐに割り込み線が立つ。`Machine.run_frame()` は、下の MSX2 と同じく、命令の境界ごとに割り込み線を読む。

```python
cpu.int_pending = vdp.irq
```

MSX1 にはスキャンラインごとの割り込みはない。

### MSX2：レベルベース IRQ

MSX2（V9938）の割り込みもレベルで扱う。`Machine.run_frame()` の内側のループは、命令の境界ごとに VDP の `irq` を読む。使うのは、上の MSX1 と同じ `cpu.int_pending = vdp.irq` の 1 行。

`V9938.irq` は `irq_pending()` の結果を返す。

```python
def irq_pending(self) -> bool:
    ie0 = bool(self.regs[1] & 0x20)   # IE0 = R#1 ビット 5
    f   = bool(self.status & 0x80)     # F   = S#0 ビット 7（VBlank）
    ie1 = bool(self.regs[0] & 0x10)   # IE1 = R#0 ビット 4
    fh  = bool(self._status1 & 0x01)  # FH  = S#1 ビット 0（H ライン）
    return (ie0 and f) or (ie1 and fh)
```

実機と同じく、ポート 0x99 の読み出しでステータスフラグがクリアされるまで、CPU には割り込みのレベルが true に見える。S#0（R#15 = 0）を読むと F が、S#1（R#15 = 1）を読むと FH がクリアされる。

### 水平ライン割り込み

`Machine.run_frame()` はフレームをスキャンラインに分け、各スキャンラインの T ステート予算の終わりで `vdp9938.begin_scanline(L)` を 1 回呼ぶ。

```python
for L in range(lpf):               # lpf = 262（NTSC ライン数）
    line_end = (L + 1) * cpf // lpf
    while total < line_end:
        cpu.int_pending = vdp9938.irq
        n = cpu_step()
        ...
    vdp9938.begin_scanline(L)
```

`begin_scanline` によるレベルの変化は、CPU が次の命令の境界で読み取る。

`begin_scanline(line)` は、割り込みを起こすラインを `(R#19 − R#23) & 0xFF` で計算する。`line == effective_irq_line` で、IE1（R#0 bit 4）がセットされていれば、S#1 bit 0（FH）をセットする。比較の対象は 0〜255 のどのラインでもよく、ボーダーや垂直帰線期間のラインも含む。IE1 がクリアの間は FH をラッチしないので、後から IE1 をセットしても、すでに過ぎたラインの割り込みは起きない。

FH は S#1 を読むまでセットされたまま。読むと FH がクリアされ、`irq` が計算し直される。

`begin_scanline(display_height)`、つまり表示領域のすぐ次のラインでは、VBlank フラグもセットする。MSX2 では、以前あったフレーム単位の終了処理をこれで置き換えている。

---

## I/O バス

### 登録とディスパッチ

`IOBus`（`msx/io.py`）は、`(start, end, handler)` のタプルのリストを 2 つ持つ。読み出し用と書き込み用。デバイスはマシンの構築時に自分を登録する。

```python
io.register_read(0x98, 0x99, vdp.read_port)
io.register_write(0x98, 0x9B, vdp.write_port)
```

`read_port` と `write_port` は、Z80 の 16 ビットのポートアドレスを 8 ビットにマスクし（`port &= 0xFF`）、リストを先頭から調べる。`[start, end]` の範囲にポートを含む、最初に登録したハンドラが処理する。合うハンドラがなければ、読み出しは 0xFF（オープンバス）を返す。

### ログ

`_logger` があるとき、バスは読み出しをハンドラが戻った後に記録する。値はそのとき初めてわかるから。書き込みは振り分けの前に記録する。値は呼び出し側ですでに決まっているから。どちらの記録にも、ポート、値、現在の命令の PC が入る。

### 標準ポートマップ

| ポート    | デバイス                                    |
| --------- | ------------------------------------------- |
| 0x98–0x9B | VDP（TMS9918A または V9938）                |
| 0x9A      | V9938 パレット（MSX2 のみ）                 |
| 0xA0–0xA2 | PSG（AY-3-8910）                            |
| 0x7C–0x7D | FM-PAC の OPLL（`--extensionN fmpac` のとき） |
| 0xA8–0xAB | PPI（i8255）                                |
| 0xB4–0xB5 | RTC（RP5C01、MSX2 のみ）                    |
| 0xD8–0xDB | 漢字 ROM（`hbi_j1` などの拡張）             |
| 0xFC–0xFF | RAM マッパーセグメントレジスタ（MSX2 のみ） |

---

## VDP

### TMS9918A（MSX1）

`msx/vdp/vdp.py` が TMS9918A を実装し、レンダラは `msx/vdp/renderer.py` にある。16 KB の VRAM と 8 個のコントロールレジスタを持ち、スクリーンモード 0〜3（テキスト、グラフィック 1/2、マルチカラー）に対応する。

`render_frame()` は 256×192 の表示フレームを、`Machine.run_frame()` の途中、垂直帰線期間の始まりで描画する。続いて `Machine.run_frame()` が `VDP.begin_vblank()` を呼び、VBlank フラグをセットして割り込み線（`VDP.irq`）を更新する。レンダラは共通のヘルパー `msx/vdp/_geometry.py` でフレームを 256×212 の一定の出力にそろえる（上下に 10 行ずつボーダーを足す）。VDP の表示ライン数が 192 でも 212 でも、どのフレームも同じ 4:3 の形になる（後述の V9938 の `display_height` を参照）。

### V9938（MSX2）

`msx/vdp/v9938.py` が V9938 を実装し、レンダラは `msx/vdp/v9938_renderer.py` にある。TMS9918A との主な違いは次のとおり。

- 128 KB VRAM
- 28 個のコントロールレジスタ（R#0–R#27）と 15 個のコマンドエンジンレジスタ（R#32–R#46）
- 16 色のプログラマブルパレット（9 ビット GRB333）。リセット時に読み込む `_MSX2_DEFAULT_PALETTE` は、V9938 データブックの電源投入時の値と同じ
- 3 つのステータスレジスタ：S#0（VBlank/スプライト）、S#1（水平ライン FH）、S#2（コマンドエンジン CE/TR、リトレース HR/VR）
- ハードウェアコマンドエンジン
- `irq_pending()` によるレベルベース IRQ

`display_height` プロパティはふだん 192 を返し、R#9 bit 7（LN）がセットされていると 212 を返す。`display_width` プロパティは次のモードで 512 を返し、ほかのモードでは 256 を返す。

- 横に広いモード SCREEN 6/G5 と SCREEN 7/G6（M5 がセット、M4 がクリア）。
- TEXT2、SCREEN 0 WIDTH 80（M1 と M4 がセット、M3 がクリア）。

### バンディングレンダラ

フレームの途中で VDP のレジスタを書き換えるゲームがある。たとえば、表示領域ごとにパレットやスクリーンモードを切り替える。レンダラはこの変更を正しいスキャンラインで反映しなければならない。

`V9938.write_port()` は、表示に関係するレジスタへの書き込みをすべて `_reg_write_log` に記録する。

```python
_DISPLAY_REGS = frozenset({0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 18, 19, 23})
```

各エントリは次のどちらかの形になる。

- `(display_line, reg, value)`：レジスタへの書き込み
- `(display_line, -1, (palette_index, grb_value))`：パレットへの書き込み（ポート 0x9A）。`reg = -1` を目印に使う

`begin_scanline(0)`（フレームの先頭）がログをクリアする。`render_frame()` はクリアしないので、SDL2 フロントエンドは RGB への変換でこのログを使い、スキャンラインごとに正しいパレットを当てられる。

`render_frame_v9938(vdp)` は `_reg_write_log` を調べて、バンドの境界を見つける。境界は、表示に関係する値が前のバンドのスナップショットと変わるライン。連続するバンド `[y0, y1)` は、その先頭のレジスタ状態で描画する。バンドごとのレンダラには `y_start` と `y_end` を明示的に渡し、どのスキャンラインもちょうど 1 回だけ描画される。

ログが空（フレーム途中の書き込みがない）なら、`render_frame_v9938` は 1 回で全体を描画する。出力も速度も、バンド処理を入れる前と同じ。

スプライトは、スプライトアトリビュートテーブル（SAT）の領域ごとに 1 回描画する。SAT の領域とは、SAT のベース（R#5/R#11）が同じバンドが続く最大の範囲。SAT のベースがフレームを通して変わらなければ（ふつうの場合）、スプライトの描画は 1 回で、1 回で全体を描く場合と同じになる。SAT のベースがフレームの途中で変わると（スプライトの多重表示など）、各領域のスプライトはその領域の SAT から描く。領域ごとに違うバッファを読むので、同じスプライトが 2 回出ることはない。

### コマンドエンジン

V9938 のハードウェアコマンドは、バイト単位とピクセル単位の VRAM 操作に対応する。

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

エンジンは各コマンドをすぐに実行する。コマンドにかかる時間を表すため、`V9938.tick(n)` がおおよそのサイクル予算（`_cmd_remaining`）を減らす。マシンは命令のたびに、その命令の T ステート数で `tick(n)` を呼ぶ。S#2 bit 0（CE）を待つソフトウェアには、予算が尽きた後に CE がクリアされて見える。

ブロックコマンドごとに、1 要素あたりのコスト（T ステート）がある。

| 定数 | コスト | 単位 |
| --- | --- | --- |
| `_HMMV_CYCLES_PER_BYTE` | 10 | VRAM 1 バイトあたり |
| `_HMMM_CYCLES_PER_BYTE` | 21 | VRAM 1 バイトあたり |
| `_YMMM_CYCLES_PER_BYTE` | 18 | VRAM 1 バイトあたり |
| `_LMMV_CYCLES_PER_DOT` | 21 | 1 ドットあたり |
| `_LMMM_CYCLES_PER_DOT` | 30 | 1 ドットあたり |

これらの値は、実機の SONY HB-F1XD で表示とスプライトをオンにした状態（最も遅い条件）で、1 画面分のコマンドの時間を測って求めた。表示やスプライトの VRAM アクセスが少ないときに速くなる分は、エミュレータではモデル化していない。

R#44（COL）への書き込みが、HMMC/LMMC の転送ラッチ（`_cmd_transfer`）をセットする。転送の最初のピクセルまたはバイトは、まだ使っていない COL の書き込みから取る。開始時に自動では読み込まない。openMSX も同じ動作をする。

`v9938.py` の `_apply_log()` が、論理演算（IMP、AND、OR、XOR、NOT）をピクセルごとに適用する。

### 既知の制限

- コマンドのタイミングは近似。エンジンは要素ごとの実測コスト（上記）を使う。V9938 データブックのコマンドごとのサイクル数は使わない。
- エンジンは結果を開始時にまとめて VRAM に書き、少しずつは書かない。コマンドの実行中とされる間に VRAM を読むソフトウェアには、CE がクリアされる前に完了後の結果が見えることがある。
- エミュレータは、CPU が 1 フレーム動いた後にそのフレームを描画する。CPU が 1 フレーム分動き、VDP コマンドはすぐに VRAM に書き、最後に 1 回の描画でフレーム全体を描く。V9938 も TMS9918A も、この描画は垂直帰線期間の始まりに行う。1 フレームの中でラスタに合わせて VRAM を書き換える処理（ビームに合わせた転送や、ダブルバッファのタイトル画面）は正しく再現しない。上のバンド処理で再現できるのはフレーム途中の*レジスタ*の変更だけで、VRAM の中身の変更は再現できない。

---

## オーディオ

音声を作る音源は 5 つある：PSG（`msx/psg.py`）、SCC（`msx/scc.py`）、FM-PAC の OPLL（`msx/opll.py`）、Majutsushi DAC、PPI のキークリック。どれも 44,100 Hz の符号付き 16 ビットモノラル PCM を作る。1 フレームあたり NTSC のマシンでは 735 サンプル、PAL のマシンでは 882 サンプル。`Machine.run_frame()` の最後に、`AudioMixer`（`msx/audio_mixer.py`）がマシンにあるチップの出力を足し、16 ビットに収める。フロントエンドの有無に関係なく行うので、ヘッドレスの実行でもチップの状態は同じように進む。SDL2 フロントエンドは `Machine.take_audio()` でそのバッファを受け取り、出力ローパスフィルタを通して SDL のキューに積む。マシンが持つのは最新の 1 フレーム分だけなので、受け取られない音声がたまることはない。

Majutsushi DAC とキークリック（PPI ポートC bit 7）は、書き込みで動く DAC チャンネル 1 つ（`msx/dac.py` の `EventDac`）を共有する。持ち主は書き込みを符号付き 16 ビットのレベルに変え、書き込んだサイクルと一緒に渡す。レベルが変わらない書き込みは記録しない。`generate_samples` は各変化をサンプル位置 `(cycle - frame_start) * n // (frame_end - frame_start)` に置き、次の変化までレベルを保つ。変化がなくレベルが 0 のフレームでは `None` を返し、ミキサーはそのチャンネルを混ぜない。

### サブフレーム・ソフトウェア PCM

`PSG.generate_samples(n, frame_start, frame_end)` は、音量レジスタへの速い書き込みで鳴らすソフトウェア PCM を再現する。

1. `write_port` は各レジスタ書き込みを `(cycle, reg, value)` として記録する。サイクルは、`machine.cycle_count` を読む `_get_cycle` コールバックから得る。
2. `generate_samples` は、フレームの最初の書き込みの時点のレジスタとジェネレータの状態に戻る。
3. バッファを、続いた区間の並びとして描き直す。各書き込みは、サンプル位置 `clamp((cycle - frame_start) * n // (frame_end - frame_start), 0, n)` から新しい区間を始める。
4. トーン、ノイズ、エンベロープの状態は区間をまたいで引き継ぐ。

書き込みのないフレームは、以前と同じ 1 つのスナップショットで描く速い経路を通る。出力はバイト単位で以前と同じなので、ふつうの音楽に余分なコストはかからない。

### トーン積分（period-0 PCM キャリア）

ソフトウェア PCM は、トーンジェネレータを周期 0 で有効にしたまま使う。トーンは約 223 kHz の超音波のキャリアになり、実機のアナログ出力では約 50% のデューティの平均にならされる。PCM の波形を運ぶのは、音量レジスタへの書き込みのほうになる。`tone_out` を 44.1 kHz で点サンプリングすると、キャリアが 0 と最大の間を行き来する形にエイリアスし、サンプルの約半分が 0 になる。

そこで、各チャンネルのトーンを**出力サンプルごとに積分する**。内側のループが、矩形波が High だった PSG の tick 数（`hi0/hi1/hi2`）を数え、ミキサがチャンネルの振幅を `hi / ticks` 倍する。聞こえる音程のトーン（1 サンプルの中で切り替わらない）では `hi` は `ticks` か `0` なので、結果は点サンプリングとビット単位で同じ。変わるのは超音波のキャリアだけで、帯域制限されたデューティの平均になる。

### 出力ローパスフィルタ

`frontend/audio_filter.py`（`BiquadLowPass`）は、状態を持つ 2 極バターワースのローパスフィルタ（8 kHz、RBJ の係数、Direct Form I）。フロントエンドが、マシンから受け取った毎フレームの PCM にこのフィルタをかける。実機 MSX の音声出力の RC フィルタを模している。浮動小数点を使うのでコアには置かない。こうすることで、コアの音声出力はどのホストでも同じになる。フロントエンドが 1 つのインスタンスを持ち、`x1/x2/y1/y2` の状態をフレームをまたいで保つ。リセットのホットキーで状態を消す。

フィルタは、点サンプリングの合成がナイキスト周波数の近くに残す高域のイメージングとエイリアシングを取り除く。コストは 1 フレームの音声処理の約 1%。実際の PSG の音楽のエネルギーはカットオフよりずっと低いので、聞こえる帯域と全体の音量はほとんど変わらない。

## メモリとスロットシステム

### MSX1 スロットレイアウト

`Memory`（`msx/memory.py`）は、フラットな 64 KB のアドレス空間を 4 ページ × 4 スロットで振り分ける。スロット選択レジスタ（ポート 0xA8、PPI 経由）が、16 KB の各ページのスロットを選ぶ。レジスタの bit `[2N+1 : 2N]` がページ N のスロットを選ぶ。

MSX1 のデフォルトの配置：

| スロット | 内容                                                   |
| -------- | ------------------------------------------------------ |
| 0        | BIOS ROM（読み取り専用）。一部の機種はページ 3 に 16 KB RAM を持つ（例：`hb_10`） |
| 1        | マッパー経由のカートリッジ ROM                         |
| 2        | 第 2 カートリッジまたはオープンバス（読み出しは 0xFF） |
| 3        | ページ 2〜3（0x8000〜0xFFFF）の 32 KB RAM。サイズは機種ごとに設定する |

### MSX2 サブスロット

MSX2 ではスロット 3 が拡張される（`sub_slot_enabled = True`）。0xFFFF を読むと `~sub_slot_reg` が返り、ソフトウェアはこれでサブスロットがあることを知る。サブスロットレジスタ（`sub_slot_reg`）は、拡張したプライマリスロットの中で、各ページを 4 つのセカンダリスロットのどれにするかを選ぶ。

MSX2 のデフォルトの配置：

| サブスロット | 内容                            |
| ------------ | ------------------------------- |
| 3-0          | C-BIOS サブ ROM（読み取り専用） |
| 3-1          | 未使用                          |
| 3-2          | RAM マッパー経由の RAM（デフォルト 128 KB） |
| 3-3          | 未使用                          |

### RAM マッパー

`RamMapper`（`msx/ram_mapper.py`）は RAM を 16 KB のセグメントに分ける。サイズは 16 KB の正の倍数で、マシンまたは拡張の YAML の `size_kb` で決める。デフォルトは 128 KB（8 セグメント）。`hb_f1xd_256k` は 256 KB、512 KB の拡張は 512 KB を使う。ポート 0xFC〜0xFF の 4 つのセグメントレジスタが、CPU の各ページに見えるセグメントを独立に選ぶ。

| ポート | ページ | アドレス範囲  |
| ------ | ------ | ------------- |
| 0xFC   | 0      | 0x0000–0x3FFF |
| 0xFD   | 1      | 0x4000–0x7FFF |
| 0xFE   | 2      | 0x8000–0xBFFF |
| 0xFF   | 3      | 0xC000–0xFFFF |

レジスタにセグメント番号を書くと、そのページの割り当てがすぐに切り替わる。マッパーは番号を `bank_mask` でマスクする。`bank_mask` はセグメント数を覆う最小の 2 のべき乗から 1 を引いた値（128 KB なら 7）。読み出すと、セグメント番号の使わない上位ビットを 1 にした値が返る。

---

## フロッピーディスク（FDC）

フロッピーまわりは汎用の層（`msx/fdc/`）になっている。新しいコントローラチップや接続方式は、`Memory` を変えずにこの層につなげる。今は 2 つのコントローラと 2 つの接続方式がある。WD2793 と Sony/Philips 方式、TC8566AF とその専用の方式。

| 層 | ファイル | 役割 |
| --- | --- | --- |
| ディスクイメージ | `disk_image.py`（`DskDiskImage`） | `*.dsk` のセクタイメージを読む。ジオメトリは FAT12 の BPB（BIOS Parameter Block：セクタあたりのバイト数、総セクタ数、トラックあたりのセクタ数、ヘッド数）から求める。書き込みはバッファにためて、終了時にファイルに書き戻す |
| ドライブ | `disk_drive.py`（`DiskDrive`） | 物理ドライブ 1 台。マウント中のイメージを持ち、ヘッドの位置を追い、セクタを読み書きする |
| コントローラ | `wd2793.py`（`WD2793`）、`tc8566af.py`（`TC8566AF`） | FDC チップ。`WD2793`：Type I–IV コマンドデコード、ステータスレジスタ、データ/トラック/セクタレジスタ、`abort()`。`TC8566AF`（uPD765 系）：コマンド/実行/結果フェーズモデル、Main Status Register、非 DMA 転送、直接アドレス指定できるトラック/セクタレジスタは持たない |
| インターフェース | `interface.py` | コントローラとメモリバスの接続方式。どちらも DISK ROM を 0x4000〜0x7FFF にマップする。`SonyPhilipsInterface`（openMSX の PhilipsFDC にあたる）は WD2793 のレジスタを 0x7FF8〜0x7FFF に置き、ディスク交換ビットを使う。`TC8566AFInterface` は 2 つの制御レジスタ、Main Status Register、Data Register を 0x7FF8〜0x7FFB に置く。実行中のマウントと取り出しを行う `swap()` は両方で共通 |

マシンのコントローラと接続方式は、マシン YAML の `fdc:` ブロックで決まる。同梱のフロッピー付きの 2 機種も、違うのはこのデータだけ。

- Sony HB-F1XD（`hb_f1xd`）は、WD2793 と DISK ROM をスロット 3 のサブスロット 0 に置き、サブスロット 3 に 64 KB のフラットな RAM を置く。
- Panasonic FS-A1F（`fs_a1f`）は TC8566AF を使い、4 つのセカンダリスロットに役割を分ける。サブスロット 0 が RAM、1 が SUB ROM、2 が FDC。

`--fdd1`/`--fdd2` でドライブ A/B にイメージをマウントする。`--fdd1` には複数のイメージを指定できる。実行中にディスクを交換する方法は 3 つあり、どれも `FloppyDisk.swap()` を呼ぶ。

- SDL2 フロントエンドの F6/Shift+F6 ホットキー。`--fdd1` の一覧を順に切り替えるか、ドライブ A のディスクを取り出し/再挿入する。
- デバッガの `fdd1`/`fdd2` コマンド。
- ソケット RPC サーバの `fdd.swap` メソッド。

この実装は Disk BASIC を起動でき、`CALL FORMAT` に対応し、ファイルを読み書きする（終了時にファイルへ書き戻す）。フロッピーインターフェースのないマシンでは `machine.fdc` は `None`。

## マシンと拡張の YAML スキーマ

`msx/machine_loader.py` は、3 種類の YAML の唯一の基準になる。デバイス定義（`config/devices/*.yaml`）、マシン仕様（`config/machines/*.yaml`）、拡張オーバーレイ（`config/extensions/*.yaml`）。3 種類とも、ローダが手で検証する `TypedDict` の形（`DeviceEntryYaml`、`MachineEntryYaml`、`ExtensionOverlayYaml` とその入れ子の形）で、スキーマのライブラリは使わない。

この節の各フィールドは、ローダの中の明示的な `.get()` 呼び出しに対応する。ローダが読まないキーは、YAML のコメントに何と書いてあっても説明のためだけのもの。各プライマリスロットに何を入れられるかの利用者向けのまとめは、[README_extension_ja.md の「スロットモデル」節](README_extension_ja.md#スロットモデル)を参照。この節では、その結果を生む YAML の書き方を説明する。

### デバイス YAML（`config/devices/*.yaml`）

| フィールド | 型 | 必須 | 読み出し箇所 |
| --- | --- | --- | --- |
| `id` | string | はい | ファイル名（拡張子を除く）と一致しなければ、`load_device_registry` が `MachineLoadError` を出す |
| `type` | string | はい | キーがあるかだけを調べ、値は調べない（慣例で `io_device`） |
| `implemented` | bool | いいえ（デフォルト `true`） | `false` なら、`builtin_devices` の解決時に stderr に警告を出して飛ばす（起動は失敗しない）。そのため、エミュレーションより先にデバイス定義だけを入れられる |
| `io_ports` | int のリスト | いいえ | 先頭と末尾の要素が、そのデバイスの `(start, end)` の I/O 範囲になる。キーがなければ `_DEFAULT_IO_PORTS` を使う |

それ以外のキー（`name`、`chip`、`controls`、`vram_kb`、`segment_size_kb`、
`keyboard_type` など）は `_DeviceDef.raw` に入るが、汎用のコードはこれらを*読まない*。
コード全体で `dev.raw.get(...)` で読むキーは 3 つだけ。上の `io_ports` と、下の
`keyboard_type` と `control_read`（`ppi8255` だけ）。

`vdp_v9938.yaml` の `vram_kb: 128` はどこからも読まれない。V9938 の VRAM サイズは、
`msx/vdp/v9938.py` にハードコードした定数 `_VRAM_SIZE = 131072` で決まる。このキーと、
マシン側でそれを写した `overrides: {vram_kb: 128}`（後述）は、意図を書いているだけで、
動作中のエミュレータには何の効果もない。

```yaml
id: vdp_v9938
type: io_device
implemented: true
name: Yamaha V9938 Video Display Processor (MSX2)
chip: v9938
io_ports: [0x98, 0x99, 0x9A, 0x9B]
controls: video_display_processor
vram_kb: 128    # 説明のためだけ。上の注記を参照
```

### マシン YAML（`config/machines/*.yaml`）

| フィールド | 型 | デフォルト | 備考 |
| --- | --- | --- | --- |
| `schema_version` | int | （なし） | 必ず `1` |
| `id` | string | （なし） | ファイル名（拡張子を除く）と一致しなければならない |
| `generation` | string | （なし） | `msx1` または `msx2`。それ以外はエラー |
| `name` | string | `id` | 表示名のみ |
| `rom_base` | string | `roms/cbios` | すべての `rom:` ブロックの `file` の基準ディレクトリ（プロジェクトルートからの相対） |
| `video_standard` | string | `ntsc` | `ntsc` は 1 フレーム 59,736 T ステート / 262 ライン、`pal` は 71,364 / 313（`cbios_msx1_eu` と `cbios_msx2_eu` が使う）。SDL2 フロントエンドの実時間の目標（NTSC 60 fps / PAL 50 fps）もこれで決まる |
| `cpu.m1_wait_states` | int | `0` | Z80 の M1 サイクルごとに足す T ステート数。上の [CPU エミュレーション](#cpu-エミュレーション)を参照 |
| `slots.primary` | マッピング | （なし） | キーは `0`〜`3`。ローダが読むのは `0` と `3` だけ（後述） |
| `builtin_devices` | リスト | `[]` | 後述 |
| `io_device` | マッピング | （なし） | マシン全体の I/O デバイス（任意）。拡張オーバーレイの `io_device`（後述）と同じ形 |

#### スロット 0（固定）

```yaml
0:
  content:
    - rom: {file: cbios_main_msx2.rom, size_kb: 32, pages: [0, 1], sha1: null}
    - rom: {file: cbios_logo_msx2.rom, size_kb: 16, pages: [2], sha1: null}
```

`content` は `{rom: {...}}` エントリのリスト。`_parse_slot0` はこの中から 2 つのエントリを探す。

- `pages` に `0` か `1` を含むエントリ：メイン BIOS ROM。必須で、なければ `MachineLoadError` になる。
- `pages` に `2` を含むエントリ：0x8000〜0xBFFF のロゴ ROM（任意）。

`sha1: null` はその ROM のハッシュ検証を止める。ほかの文字列なら、読み込んだファイルと照合する。

`content` には `{ram: {size_kb, pages}}` のエントリを 1 つまで入れられる。ROM と並べてスロット 0 に置く、フラットな RAM を表す。たとえば Sony HB-10 のページ 3 にある 16 KB SRAM がこれにあたる。電池のない、電源を切ると消えるワーク RAM。

```yaml
    - ram: {size_kb: 16, pages: [3]}
```

`pages` はちょうど 1 ページでなければならず、違えば `MachineLoadError` になる。
2 つ目の `ram` エントリも同じくエラーになる。

#### スロット 1・2：マシン YAML からは読まれない

どのマシン YAML も、`slots.primary` に `1: {type: cartridge}` と `2: {type: cartridge}`
を書いている。しかし `load_machine_spec` は `primary[1]` も `primary[2]` も読まず、
読むのは `primary[0]` と `primary[3]` だけ。この 2 つのエントリは意図を示すだけ。

`build_machine()` は、スロット 1 と 2 の中身をすべて自分の引数から決める。スロット 1 は、
カートリッジとマッパー（位置引数か `--slot1`、と `--mapper1`）か、オーバーレイ
（`--extension1`）。スロット 2 は、カートリッジとマッパー（`--slot2`/`--mapper2`）か、
オーバーレイ（`--extension2`）。これらの値は CLI が決め、YAML の
`slots.primary.1`/`.2` の中身は影響しない。

#### スロット 3：MSX1

```yaml
3:
  size_kb: 32   # 任意、デフォルトは 32
```

`_parse_slot3_msx1` が読むのは `size_kb`（デフォルト `32`）だけ。0xFFFF で終わる
フラットな RAM で、それ以上の構造はない。

- 32 KB なら 0x8000〜0xFFFF。
- 64 KB（`hb_10_64k`）なら 4 ページすべて。
- `size_kb: 0`（`hb_10`）ならスロット 3 はオープンバス。

`3:` を省略すると、オープンバスではなくデフォルトの 32 KB になる。

#### スロット 3：MSX2

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

`expanded: true` で、スロット 3 は 4 つのセカンダリスロット（`secondary.0`〜`.3`、
キーの書き方は `slots.primary` と同じ）になる。`_parse_slot3_msx2` はそれぞれを独立に
調べ、次のものを見つける。

- `content` を持ち、`rom.pages` に `0` か `1` を含む最初のサブスロット → SUB ROM。
  任意。ローダはそのサブスロット番号を記録する（0 に固定ではない）。
- `mapper: standard` を持つ最初のサブスロット → `RamMapper`。サイズは `size_kb`
  （デフォルト `128`）。16 の正の倍数でなければならない。`_check_ram_mapper_size_kb`
  がこれを調べ、違えば `MachineLoadError` を出す。
- `type: ram` を持ち、`mapper: standard` を持たない最初のサブスロット → フラットな
  （マッパーでない）RAM。サイズは `size_kb`（デフォルト `64`）。`hb_f1xd` は実機の
  固定 64 KB にこれを使う。
- `fdc:` ブロックを持つ最初のサブスロット → [フロッピーディスク（FDC）](#フロッピーディスクfdc)を参照

`mapper: standard` のサブスロットと `type: ram` のサブスロットは、`secondary` 全体で
どちらか一方しか置けない。`Memory` は RAM マッパーとフラットな RAM を同時に持てない
ため。両方あると `MachineLoadError` になる。フラットな RAM を SUB ROM や FDC と同じ
サブスロット番号に置くこともエラーになる。`Memory` の書き込みの経路は、どちらかへの
誤った書き込みがフラットな RAM に入るのを止めないため。どのサブスロット番号も、役割に
関係なく `0`〜`3` の範囲かを調べる（`_check_subslot_index`）。

`fdc:` ブロックのフィールド（スロット 3 のサブスロット内でのみ意味を持つ）：

| フィールド | 型 | デフォルト | 備考 |
| --- | --- | --- | --- |
| `rom` | `rom:` ブロック | （なし） | 必須（DISK ROM） |
| `controller` | string | `wd2793` | `wd2793` または `tc8566af` |
| `connection_style` | string | `sony` | `sony` または `tc8566af` |
| `drives` | int | `1` | 正の値でなければならない |

`(controller, connection_style)` の組は、`(wd2793, sony)` か `(tc8566af, tc8566af)`
でなければならない。それぞれの値が単独では正しくても、ほかの組み合わせはエラーになる
（例：`wd2793` + `tc8566af`）。

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

各エントリの `ref` は、デバイスレジストリ（`load_device_registry` の出力）で解決できな
ければならず、できなければ `MachineLoadError` になる。`overrides` は自由な形の
マッピングだが、`_parse_builtin_devices` が読むキーは 2 つだけで、それも `ref: ppi8255`
のときだけ。

- `keyboard_type`：`int` か `jp`。
- `control_read`：`control`、`open_bus`、`bus_latch` のどれか。ポート 0xAB を読んだときの値。

古いキー `control_readback` は、
デバイスファイルにあっても `overrides` にあっても `MachineLoadError` になる。ほかの
`overrides` のキーは、どの `ref` でも受け付けて無視する。

`ref: vdp_v9938`/`rtc_rp5c01`/`ppi8255` があると、`MachineSpec` の
`has_v9938`/`has_rtc`/キーボード配列の各フラグが設定される。ほかの `ref` は、自分の
`io_ports` の範囲を足すだけ。

#### マシンレベルの `io_device`

```yaml
io_device:
  device: kanji_rom
  rom: {file: some_kanji_font.rom, size_kb: 256, sha1: null}
```

拡張オーバーレイの `io_device`（後述）と同じ形。マシン自身が持つ、スロットに依存しない
I/O デバイスを表す。ローダは同じ `_KNOWN_IO_DEVICES`（`kanji_rom` だけ）で検証する。
今のところ、これを書いている同梱のマシン YAML はない。`kanji_rom` デバイスを持つのは、
今は `hbi_j1` と `msxdos2_512k_kanjirom` の拡張（`--extension1`/`--extension2`）だけで、
どちらも拡張オーバーレイの `io_device`（後述）を使う。

### 拡張オーバーレイ YAML（`config/extensions/*.yaml`）

| フィールド | 型 | デフォルト | 備考 |
| --- | --- | --- | --- |
| `schema_version` | int | （なし） | `1` でなければならない |
| `id` | string | （なし） | 説明のためだけ。`load_extension_overlay` はこれを読まない。ローダはファイル名（`config/extensions/<--extension1/--extension2 の値>.yaml`）だけでファイルを探す。マシンやデバイスの YAML の `id` とは違う |
| `slot` | int | `2` | `1` か `2` でなければならない。説明のためだけで、YAML の作者が想定したスロットを記録する。実際のスロットは CLI のフラグ（`--extension1` か `--extension2`）が決める |
| `shape` | string | `flat` | `flat` または `expanded` |
| `rom_base` | string | `""`（プロジェクトルート） | 以下の各 `rom:` ブロックの基準ディレクトリ |

#### フラット形状（`shape: flat`、または省略時）

```yaml
device: fmpac
rom: {file: fmpac.rom, size_kb: 64}
sram: {size_kb: 8, save_file: saves/sram/fmpac.sram}
```

`device` は `fmpac` か `scc_i_cart`（`_KNOWN_EXTENSION_DEVICES`）でなければならない。
同じ `device` というキーでも、後述の拡張形状のサブスロットのデバイスとは別の名前空間。
`fmpac` には `rom:` ブロックが要る。`scc_i_cart` には要らない。RAM は空で始まり、
ローダはファイルを読まないため。フラットなオーバーレイのデバイスは、対象のプライマリ
スロット（スロット 1 か 2）のマッパーを常にまるごと置き換える。

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

`subslots` は必須で、空でないマッピング。キーは `0`〜`3` で、MSX2 のスロット 3 の
サブスロットと同じ範囲を同じ `_check_subslot_index` で調べる。各エントリの `device` は、
`halnote`、`flat_rom`、`ram_mapper`、`ascii8`、`ascii16`
（`_KNOWN_EXPANDED_SUBSLOT_DEVICES`）のどれかでなければならない。

| `device` | 必須項目 | 構築されるもの |
| --- | --- | --- |
| `halnote` | `rom:` | `HalnoteMapper`（1 MB ROM、`sram:` は任意） |
| `flat_rom` | `rom:` | `FixedPageMapper(base=0x4000)`。0x4000〜0xBFFF にだけ見える |
| `ascii8` | `rom:` | `Ascii8Mapper` |
| `ascii16` | `rom:` | `Ascii16Mapper` |
| `ram_mapper` | `size_kb:`（エントリに直接書く。`rom:` はない） | `RamMapper`。`size_kb` は 16 の正の倍数でなければならない |

`ram_mapper` のサブスロットは、1 つのオーバーレイに 1 つまで。2 つあると、標準の
メモリマッパーの I/O ポート（0xFC〜0xFF）を 2 回登録してしまう。拡張形状のオーバーレイを
MSX1 のマシンに使うと、ローダは必ず `MachineLoadError` を出す。メモリマッパーの
サブスロットにあたる MSX1 標準のハードウェアはなく、今の拡張形状のオーバーレイはどれも
MSX2 時代のハードウェアを表しているため。`ram_mapper` のサブスロットは、スロット 3 に
自前の RAM マッパーを持つマシン（`has_ram_mapper=True`）とも衝突する。2 つのマッパーが
同じポートを使うことになるため。

任意のトップレベルの `io_device` は、*3 つめの*デバイスの名前空間
`_KNOWN_IO_DEVICES = {kanji_rom}` で検証する。ほかの 2 つとは重ならない。`subslots` で
`device: kanji_rom` は使えず、`io_device` で `device: halnote`（ほかのサブスロットの
デバイスも）は使えない。マシンの `io_device` と拡張オーバーレイの `io_device` がどちらも
`kanji_rom` になると、`build_machine` はエラーを出す。ポート 0xD8〜0xDB は、2 つの
漢字 ROM デバイスを同時に扱えないため。

#### `rom:` と `sram:` ブロック

どちらの形も、同じ入れ子のブロックの書き方を使う。`_parse_rom_entry` がこれを解析する。
ローダは、スロット 0 の ROM、MSX2 の SUB ROM、FDC の DISK ROM にもこの関数を使う。
ローダで ROM を扱うすべてのフィールドが共有する、ただ 1 つの実装。

| `rom:` のフィールド | 型 | 必須 | 備考 |
| --- | --- | --- | --- |
| `file` | string | はい | `rom_base` を基準に探す |
| `size_kb` | int | いいえ（デフォルト `0`） | 説明のためだけ。実際のファイルサイズとは照合しない |
| `pages` | int のリスト | いいえ（デフォルト `[]`） | スロット 0 と SUB ROM でだけ意味があり、どのエントリがメイン/ロゴ/SUB ROM かを決める |
| `sha1` | string | いいえ | `null` か省略で、ハッシュ検証をしない |

| `sram:` のフィールド | 型 | 備考 |
| --- | --- | --- |
| `size_kb` | int | 説明のためだけで、ローダは無視する。各デバイスの SRAM サイズは Python 側の定数（`HALNOTE_SRAM_SIZE`、`FMPAC_SRAM_SIZE` など）で決まる |
| `save_file` | string | SRAM の保存先のパス（プロジェクトルートからの相対）。省略すると保存しない |

## マシン YAML ローダ

### 2 パス解決

`msx/machine_loader.py` は、ハードウェアの構成を 2 段階で決める。

1. **デバイスレジストリの段階。** `load_device_registry(config_dir)` が、`config/devices/` のすべての `*.yaml` を、デバイスの `id` をキーにした辞書に読み込む。各ファイルは 1 つのハードウェア（VDP の種類、PSG、PPI、RTC、RAM マッパー）を、マシンとは関係なく単独で書く。

2. **マシン仕様の段階。** `load_machine_spec(machine_id, config_dir, device_registry, project_root)` が `config/machines/<machine_id>.yaml` を読み、必須のフィールドを検証する。`builtin_devices` の各 `ref` をレジストリで解決し、`MachineSpec` データクラスを返す。解決できない `ref`、知らない `schema_version`、必須の ROM エントリの欠けがあれば、ファイルとフィールドを示した `MachineLoadError` を出す。

### build_machine()

`build_machine(spec, cartridge, mapper, ...)` は、解決した `MachineSpec` から `Machine` を作る。`spec.generation` で具体的な型が決まる。

- `"msx1"`：`VDP`（TMS9918A）、フラットなスロット 3 の RAM。RTC と RAM マッパーはない
- `"msx2"`：`V9938`、サブ ROM と `RamMapper` を持つ拡張スロット 3、`RTC`

読み込み時、ローダは `implemented: false` のデバイス YAML を飛ばし、stderr に警告を出す。マシンのほかの部分はふつうに動くので、エミュレーションができる前にデバイス定義だけをコミットしても、起動は失敗しない。今 `config/devices/` にあるデバイスはすべて `implemented: true`。

---

## 移植性

実装は純粋な Python 3.10+ で、SDL2 フロントエンドを除いて C 拡張やネイティブのバインディングはない。コアの処理を静的型付けのシステム言語（Rust、C++）に移しやすくするため、いくつかの設計を決めている。各判断の理由は、対応するソースファイルに _Portability note_ として書いてあり、この節はその要約。

### 移植性を意識した設計

次のパターンは、静的型付けの言語にそのまま移せる。

- **オペコードの振り分け**（`msx/cpu/z80.py`、`msx/cpu/opcodes_main.py`）：`_DISPATCH` はインポート時に一度だけ作る、256 要素のフラットな callable のリスト。各要素はふつうの関数。Rust では関数ポインタの配列か、オペコードのバイトに対する `match` になる。
- **レジスタ**（`msx/cpu/registers.py`）：主レジスタは Python の `int` のフィールドで、書き込むたびに `& 0xFF` / `& 0xFFFF` で明示的にマスクする。多倍長の値には頼らない。裏レジスタは、`A_`、`BC_` などの名前の付いた個別のフィールドで、リフレクションは使わない。
- **VRAM と RAM**：サイズ固定の `bytearray`（V9938 は `_VRAM_SIZE = 131072`、TMS9918A は 16384、RAM マッパーはセグメントの配列）。アクセスはすべてスライスとインデックスで直接行う。int のリストや辞書を使った記憶域はない。
- **I/O バスの振り分け**（`msx/io.py`）：`list[tuple[int, int, Callable]]` を先頭から調べる。辞書の検索もリフレクションも使わない。登録するハンドラは少なく、マシン設定ごとに決まっている。
- **コンポーネントの構造体**：ハードウェアのコンポーネントはすべて `@dataclass` で、フィールドはすべて型付きで明示的に宣言する。`msx/` のどこにも `__getattr__`/`__setattr__` の仕掛けはない。最も頻繁に通る 2 つの構造体、`Z80` と `Registers` は `@dataclass(slots=True)` にして、インスタンスごとの `__dict__` をなくしている。ほかは素の `@dataclass`。頻繁に通らない場所では、フィールド配置の保証はそれほど重要でないため。
- **タイミングの定数**：`CYCLES_PER_LINE`、`NTSC_LINES_PER_FRAME` / `PAL_LINES_PER_FRAME`（`msx/vdp/_geometry.py`）、`_HBLANK_START`、`_TR_REASSERT_CYCLES` などは、モジュールレベルの整数定数。1 フレームの予算は `VideoStandard` 列挙型のプロパティから得る。移植では `match` / `const fn` として書く。

### 移植時に対応が必要な Python 固有パターン

次のパターンは、静的型付けの言語にそのままの対応がない Python の機能を使う。各パターンについて、Rust/C++ の移植で代わりに使うものをソースのコメントに書いてある。

#### バスフックとしての再代入可能なバインドメソッド

`Z80` は `read_byte` / `write_byte` / `read_port` / `write_port` を `Callable` のフィールドとして持つ。`Machine.__post_init__` が実行時に束縛メソッドを代入してつなぐ。あとでプログラムがウォッチポイントを有効にすると、`Machine.set_watchpoints` が `cpu.read_byte` / `cpu.write_byte` をもう一度差し替える。ふつうのメモリハンドラと、ウォッチポイントを捕まえる版（`Machine._read_with_watch` / `_write_with_watch`）を切り替える。

Python では、`Callable` のフィールドは `__call__` を持つどんなオブジェクトでも入れられる場所なので、これができる。Rust と C++ には、構造体のフィールドのメソッドを実行時に差し替える仕組みはない。移植では、バスを `trait MemoryBus`（または `enum { Normal, Watchpoint }`）で表す。具体的な実装をフラグで一度選ぶので、アクセスごとの振り分けに分岐はない。

#### レジスタの 8 ビットの半分を計算するプロパティ

`Registers` は `BC`、`DE`、`HL` を 16 ビットの `int` フィールドとして持つ。`B`/`C`、`D`/`E`、`H`/`L` へのアクセスは、16 ビットの組をシフトしてマスクする `@property` の getter/setter（`Registers.B`/`C` など）を通る。8 ビットの半分を読み書きするオペコードはどれもディスクリプタの仕組みを経由することになり、頻繁に通る命令の経路に呼び出しが 1 段増える。

Rust/C++ の移植では、8 ビットの半分をふつうの `u8` フィールド（または明示的なインラインの getter/setter）として持ち、16 ビットの組は必要なときに計算する。これでプロパティ呼び出しのコストがなくなる。

#### PSG エンベロープの多倍長整数

Python の整数は精度に上限がない：`-1 & 0x20 == 0x20`。PSG のエンベロープジェネレータは、`_env_step` が負になったときにこれを使う。負の Python の int でも、bit 5 がアンダーフローのフラグとして働く（`PSG._env_step`）。

Rust/C++ の符号なしの型はアンダーフローで折り返し、符号を広げないので、同じ式は `0x20` ではなく 0 になる。移植では、符号付きの型（`i32`）か、明示的な `wrapping_sub` / ビットフィールドでフラグを保たなければならない。

#### レンダラの `bytes.translate()` LUT キャッシュ

V9938 の G4/G6 と G5 の、スキャンラインごとのピクセル展開は、`bytes.translate()` の表を `_G46_LUT_CACHE` / `_G5_LUT_CACHE` にためておく。どちらも `(tp: bool, border: int)` をキーにした辞書。`bytes.translate()` は CPython の組み込みで、256 バイトの変換表を C で一度に適用するので、Python でこの変換をする最も速い方法になる。辞書のキャッシュで、スキャンラインごとに同じ表を作り直さずに済む。

`bytes.translate()` も呼び出しごとの辞書のハッシュも、システム言語では自然な書き方ではない。Rust/C++ の移植では、固定の `[u8; 256]` の配列を初期化時に計算しておき、直接インデックスで引く。`(tp, border)` の組み合わせは全部で 32 通りしかない。

#### トレーサのフックの Callable フィールド

`VDP`（TMS9918A）と `V9938` は、どちらも `tracer`、`_get_pc`、`_get_cycle` を None にできる `Callable` のフィールドとして持つ。マシンが配線のときに代入し、VDP は対応するイベントのたびに呼ぶ。Python ではこれを `Callable[[], None] | None` と型付けするが、静的型付けの言語にそのままの対応はない。

Rust/C++ の移植では、`Option<Box<dyn Fn()>>` のトレイトオブジェクト（または同等のもの）で表す。別の方法として、機能フラグ付きのコンパイル時ジェネリクスにすれば、リリースビルドで呼び出しごとの振り分けがなくなる。

#### フレームタイマーのスピンループ

`FrameTimer.tick()` は、フレームの締め切り前の最後の 1 ミリ秒未満を、`time.perf_counter()` を使った待ちループで待つ。Python には、このループがスピンであることをスケジューラに伝える方法がない。

Rust の移植では、同じループに `std::hint::spin_loop()` を入れる。スリープせずに CPU のパイプラインにヒントを出せる。SMT のコアで消費電力を減らし、タイミングのぶれを小さくできることがある。

早送り中、フロントエンドは `tick()` の代わりに `FrameTimer.tick_unpaced()` を呼ぶ。fps の計測のためにフレームを数え、締め切りも設定し直すが、待ちはしない。移植でも同じ 2 つの入口を残す。
