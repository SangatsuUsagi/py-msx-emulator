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
- [コアの境界と移植ガイド](#コアの境界と移植ガイド)
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

OTIR/INIR などのブロック I/O 命令は、ページ境界をまたぐとサイクル単位では正確でない。非公開のフラグ Y（bit 5）と X（bit 3）は、資料にある命令ごとの規則に従う。あまり一般的でない次の 3 つの要因も再現している：`BIT n,(HL)` は内部レジスタ MEMPTR から、繰り返し中の `LDIR`/`LDDR`/`CPIR`/`CPDR` は自身のアドレスから Y と X を取り、`SCF`/`CCF` は直前の命令がフラグを変えたかどうかで結果が変わる。`SCF`/`CCF` は Zilog NMOS の規則に従う。NEC 製や ST 製 CMOS の Z80 では規則が異なる（[Undocumented Flags](https://github.com/hoglet67/Z80Decoder/wiki/Undocumented-Flags)）。TODO: HB-F1XD と HB-10 がどの規則に従うかを実機で確認する。繰り返し中の `INIR`/`INDR`/`OTIR`/`OTDR` は、従来どおり 1 回ごとの単純なフラグのままである。

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

`Machine.run_frame()` はフレームをスキャンラインに分け、各スキャンラインの T ステート予算の終わりで `vdp9938.begin_scanline(L)` を 1 回呼ぶ。概略は次のとおり（`Machine._run_lines_fast`）。

```python
for line in range(first_line, end_line):   # NTSC は 1 フレーム 262 ライン
    line_end = (line + 1) * cpf // lpf
    while total < line_end:
        cpu.int_pending = vdp9938.irq
        n = cpu_step()                     # または step_halted_batch()（後述）
        total += n
        if vdp9938._cmd_active:
            vdp9938.tick(n)                # コマンドエンジンの実行中
        else:
            vdp9938._line_cycle += n       # 水平帰線のタイマーだけ
    vdp9938.begin_scanline(line)
```

`begin_scanline` によるレベルの変化は、CPU が次の命令の境界で読み取る。

ループは 2 つの区間に分けて回す。`vblank_start_line` までのラインと、フレームの残り。マシンはその間、VBlank の割り込みハンドラが動く前にフレームを描画する（[VDP](#vdp) を参照）。CPU が HALT 中で割り込みを受け付けられない間、ループは `step()` の代わりに `Z80.step_halted_batch()` を呼ぶ。1 回の呼び出しで、ラインの終わりまでの HALT 中の M1 サイクルをまとめて進め、R の値と T ステートの合計は同じになる。V9938 のコマンドの実行中はまとめない。コマンドエンジンがラインの途中で割り込みを起こすことがあるため。

`begin_scanline(line)` は、割り込みを起こすラインを `(R#19 − R#23) & 0xFF` で計算する。`line == effective_irq_line` で、IE1（R#0 bit 4）がセットされていれば、S#1 bit 0（FH）をセットする。比較の対象は 0〜255 のどのラインでもよく、ボーダーや垂直帰線期間のラインも含む。IE1 がクリアの間は FH をラッチしないので、後から IE1 をセットしても、すでに過ぎたラインの割り込みは起きない。

FH は S#1 を読むまでセットされたまま。読むと FH がクリアされ、`irq` が計算し直される。

`begin_scanline(vblank_start_line)` は VBlank フラグもセットする。`vblank_start_line` は表示ライン数（192 か 212）なので、これは表示領域のすぐ次のライン。MSX2 では、以前あったフレーム単位の終了処理をこれで置き換えている。

---

## I/O バス

### 登録と振り分け

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
_DISPLAY_REGS = frozenset({0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 18, 19, 23})
```

各エントリは次の 2 つの型のどちらか（`msx/vdp/_v9938_defs.py`）で、読む側は型で区別する。

- `_RegChange(line, reg, value)`：レジスタへの書き込み
- `_PaletteChange(line, idx, rgb)`：パレットへの書き込み（ポート 0x9A）。`rgb` はパレットが持つ 9 ビットの値で、並びは `r << 6 | g << 3 | b`

`begin_scanline(0)`（フレームの先頭）がログをクリアする。`render_frame()` はクリアしない。そのため `V9938.to_rgb24()` は、コアの中でフレームを RGB24 に変換するときにこのログを使い、スキャンラインごとに正しいパレットを当てられる。

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

エンジンは各コマンドをすぐに実行する。コマンドにかかる時間を表すため、`V9938.tick(n)` がおおよそのサイクル予算（`_cmd_remaining`）を減らす。コマンドの実行中、マシンは命令のたびに、その命令の T ステート数で `tick(n)` を呼ぶ。コマンドを実行していない間は、T ステート数を `_line_cycle` に足すだけ。`_line_cycle` は水平帰線のタイマーで、`tick(n)` もこれを進める（[水平ライン割り込み](#水平ライン割り込み) を参照）。S#2 bit 0（CE）を待つソフトウェアには、予算が尽きた後に CE がクリアされて見える。

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

音声を作る音源は 5 つある：PSG（`msx/psg.py`）、SCC（`msx/scc.py`）、FM-PAC の OPLL（`msx/opll.py`）、Majutsushi DAC、PPI のキークリック。どれも 44,100 Hz の符号付き 16 ビットモノラル PCM を作る。1 フレームあたり NTSC のマシンでは 735 サンプル、PAL のマシンでは 882 サンプル。`Machine.run_frame()` の最後に、`AudioMixer`（`msx/audio_mixer.py`）がマシンにあるチップの出力を足し、16 ビットに収める。フロントエンドの有無に関係なく行うので、ヘッドレスの実行でもチップの状態は同じように進む。SDL2 フロントエンドは `step_frame` の `FrameOutput` でそのバッファを受け取り（[フレームの実行](#フレームの実行) を参照）、出力ローパスフィルタを通して SDL のキューに積む。マシンが持つのは最新の 1 フレーム分だけなので、受け取られない音声がたまることはない。

Majutsushi DAC とキークリック（PPI ポートC bit 7）は、書き込みで動く DAC チャンネル 1 つ（`msx/dac.py` の `EventDac`）を共有する。持ち主は書き込みを符号付き 16 ビットのレベルに変え、書き込んだサイクルと一緒に渡す。レベルが変わらない書き込みは記録しない。`generate_samples` は各変化をサンプル位置 `(cycle - frame_start) * n // (frame_end - frame_start)` に置き、次の変化までレベルを保つ。変化がなくレベルが 0 のフレームでは `None` を返し、ミキサーはそのチャンネルを混ぜない。

### サブフレーム・ソフトウェア PCM

`PSG.generate_samples(n, frame_start, frame_end)` は、音量レジスタへの速い書き込みで鳴らすソフトウェア PCM を再現する。

1. `write_port` は各レジスタ書き込みを `(cycle, reg, value)` として記録する。サイクルは、`_machine` の参照を通して `machine.cycle_count` から読む。速いフレームループは、各スキャンラインの T ステートをラインの終わりで `cycle_count` に足す。そのため、書き込みの位置の分解能は 1 スキャンライン（228 T ステート、約 2.8 サンプル）。
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
| ディスクイメージ | `disk_image.py`（`DskDiskImage`） | host がファイルから読んだバイト列で作った `*.dsk` のセクタイメージを持つ。ジオメトリは FAT12 の BPB（BIOS Parameter Block：セクタあたりのバイト数、総セクタ数、トラックあたりのセクタ数、ヘッド数）から求める。書き込みはバッファにためてイメージを dirty にする。`host/media.py` が、終了時、ステートセーブの前、ディスクをドライブから外すときに、dirty のイメージをファイルへ書き戻す |
| ドライブ | `disk_drive.py`（`DiskDrive`） | 物理ドライブ 1 台。マウント中のイメージを持ち、ヘッドの位置を追い、セクタを読み書きする |
| コントローラ | `wd2793.py`（`WD2793`）、`tc8566af.py`（`TC8566AF`） | FDC チップ。`WD2793`：Type I–IV コマンドデコード、ステータスレジスタ、データ/トラック/セクタレジスタ、`abort()`。`TC8566AF`（uPD765 系）：コマンド/実行/結果フェーズモデル、Main Status Register、非 DMA 転送、直接アドレス指定できるトラック/セクタレジスタは持たない |
| インターフェース | `interface.py` | コントローラとメモリバスの接続方式。どちらも DISK ROM を 0x4000〜0x7FFF にマップする。`SonyPhilipsInterface`（openMSX の PhilipsFDC にあたる）は WD2793 のレジスタを 0x7FF8〜0x7FFF に置き、ディスク交換ビットを使う。`TC8566AFInterface` は 2 つの制御レジスタ、Main Status Register、Data Register を 0x7FF8〜0x7FFB に置く。実行中のマウントと取り出しを行う `swap()` は両方で共通 |

マシンのコントローラと接続方式は、マシン YAML の `fdc:` ブロックで決まる。同梱のフロッピー付きの 2 機種も、違うのはこのデータだけ。

- Sony HB-F1XD（`hb_f1xd`）は、WD2793 と DISK ROM をスロット 3 のサブスロット 0 に置き、サブスロット 3 に 64 KB のフラットな RAM を置く。
- Panasonic FS-A1F（`fs_a1f`）は TC8566AF を使い、4 つのセカンダリスロットに役割を分ける。サブスロット 0 が RAM、1 が SUB ROM、2 が FDC。

`--fdd1`/`--fdd2` でドライブ A/B にイメージをマウントする。`--fdd1` には複数のイメージを指定できる。実行中にディスクを交換する方法は 3 つあり、どれも `host/media.py` の `swap_disk()` を呼ぶ。`swap_disk()` は、外す側のイメージが dirty ならまずファイルへ書き戻し、それから `Machine.mount_disk()` を呼ぶ。`mount_disk()` はフロッピーインターフェースの `swap()` を呼ぶ。

- SDL2 フロントエンドの F6/Shift+F6 ホットキー。`--fdd1` の一覧を順に切り替えるか、ドライブ A のディスクを取り出し/再挿入する。
- デバッガの `fdd1`/`fdd2` コマンド。
- ソケット RPC サーバの `fdd.swap` メソッド。

この実装は Disk BASIC を起動でき、`CALL FORMAT` に対応し、ファイルを読み書きする。変更したセクタは、host が終了時、ステートセーブの前、ディスクをドライブから外すときに `.dsk` ファイルへ書き戻す。フロッピーインターフェースのないマシンでは `machine.fdc` は `None`。

## マシンと拡張の YAML スキーマ

`host/machine_config.py` は 3 種類の YAML を読む。デバイス定義（`config/devices/*.yaml`）、マシン仕様（`config/machines/*.yaml`）、拡張オーバーレイ（`config/extensions/*.yaml`）。3 種類とも、ローダが手で検証する `TypedDict` の形（`DeviceEntryYaml`、`MachineEntryYaml`、`ExtensionOverlayYaml` とその入れ子の形）で、スキーマのライブラリは使わない。名前の閉じた集合（マッパー、FDC のコントローラと接続方式、拡張・サブスロット・I/O のデバイス）は `msx/machine_config.py` で定義している。YAML のローダはそれを `_KNOWN_*`/`_SUPPORTED_*` の名前で import し、コアは MachineConfig の文書を解析するときに同じ集合でもう一度調べる。

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

`rom:` ブロックの `sha1` は、想定するダンプを記録するだけ。値が `null` でもハッシュでも、ローダは読み込んだファイルと照合しない。コアが調べるのは別の SHA1。host が読み込んだバイト列から計算し、MachineConfig の文書に書いたもの（[load_machine_config() と build_machine()](#load_machine_config-と-build_machine) を参照）。

`content` には `{ram: {size_kb, pages}}` のエントリを 1 つまで入れられる。ROM と並べてスロット 0 に置く、フラットな RAM を表す。たとえば Sony HB-10 のページ 3 にある 16 KB SRAM がこれにあたる。電池のない、電源を切ると消えるワーク RAM。

```yaml
    - ram: {size_kb: 16, pages: [3]}
```

`size_kb` は `16`、`pages` はちょうど 1 ページでなければならず、違えば `MachineLoadError` になる。
2 つ目の `ram` エントリも同じくエラーになる。

#### スロット 1・2：マシン YAML からは読まれない

どのマシン YAML も、`slots.primary` に `1: {type: cartridge}` と `2: {type: cartridge}`
を書いている。しかし `load_machine_spec` は `primary[1]` も `primary[2]` も読まず、
読むのは `primary[0]` と `primary[3]` だけ。この 2 つのエントリは意図を示すだけ。

スロット 1 と 2 の中身は、すべて `load_machine_config()` の引数から決まり、
MachineConfig の文書の `cartridges` と `extensions` に書かれる。スロット 1 は、
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
| `drives` | int | `1` | `1`〜`4`。YAML のローダが調べるのは正の値かどうかだけで、5 台以上の文書はコアが拒否する |

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
| `sha1` | string | いいえ | 説明のためだけ。ローダはファイルと照合しない（[スロット 0](#スロット-0固定) を参照） |

| `sram:` のフィールド | 型 | 備考 |
| --- | --- | --- |
| `size_kb` | int | 説明のためだけで、ローダは無視する。各デバイスの SRAM サイズは Python 側の定数（`HALNOTE_SRAM_SIZE`、`FMPAC_SRAM_SIZE` など）で決まる |
| `save_file` | string | SRAM の保存先のパス（プロジェクトルートからの相対）。省略すると保存しない |

## マシン YAML ローダ

### 2 パス解決

`host/machine_config.py` は、ハードウェアの構成を 2 段階で決める。

1. **デバイスレジストリの段階。** `load_device_registry(config_dir)` が、`config/devices/` のすべての `*.yaml` を、デバイスの `id` をキーにした辞書に読み込む。各ファイルは 1 つのハードウェア（VDP の種類、PSG、PPI、RTC、RAM マッパー）を、マシンとは関係なく単独で書く。

2. **マシン仕様の段階。** `load_machine_spec(machine_id, config_dir, device_registry, project_root)` が `config/machines/<machine_id>.yaml` を読み、必須のフィールドを検証する。`builtin_devices` の各 `ref` をレジストリで解決し、`MachineSpec` データクラスを返す。解決できない `ref`、知らない `schema_version`、必須の ROM エントリの欠けがあれば、ファイルとフィールドを示した `MachineLoadError` を出す。

### load_machine_config() と build_machine()

マシンの組み立ては host とコアで分担する。`host/machine_config.py` の `load_machine_config(spec, cartridge1, mapper1, ...)` が、マシンに必要なファイルをすべて読み、`MachineConfig` の値（`msx/boundary.py`）にまとめる。解決済みのマシン、カートリッジとそのマッパー、拡張を、1 つの正規化した JSON の文書（`msx/machine_config.py`、`msx/machine_config.schema.json`）に書く。`auto` のマッパーは、先に ROM データベースから決める。文書の中では、ROM はすべて `{name, size, sha1, pages}` の参照。ROM イメージは SHA1 をキーにして文書と一緒に渡す。実行時の入力（保存済みの SRAM イメージ、ディスクイメージ、RTC の開始時刻）も文書と一緒に渡す。

`msx/machine_loader.py` の `build_machine(config)` は文書を厳密に解析し、参照された各イメージのサイズと SHA1 を確かめる。そのうえで、そのバイト列だけから `Machine` を作る。ファイルを開かず、データベースも引かないので、同じ設定からは必ず同じマシンができる。`Machine` は文書の SHA-256 を持ち、スナップショットはそれを記録する（[MachineConfig の文書](#machineconfig-の文書) を参照）。`spec.generation` で具体的な型が決まる。

- `"msx1"`：`VDP`（TMS9918A）、フラットなスロット 3 の RAM。RTC と RAM マッパーはない
- `"msx2"`：`V9938`、サブ ROM と `RamMapper` を持つ拡張スロット 3、`RTC`

読み込み時、ローダは `implemented: false` のデバイス YAML を飛ばし、stderr に警告を出す。マシンのほかの部分はふつうに動くので、エミュレーションができる前にデバイス定義だけをコミットしても、起動は失敗しない。今 `config/devices/` にあるデバイスはすべて `implemented: true`。

---

## コアの境界と移植ガイド

エミュレータのコア（`msx/`）は、コアを組み込む側から切り離してあるので、別のフロントエンドから動かせる。また、コアだけを先に別の言語（Zig、C++、Rust）へ移植し、Python の host とフロントエンドを比較用に残すこともできる。この節では、そうした再利用の側から見た境界を説明する。言語に依存しない契約は `allium/core-frontend.allium`。要件は `openspec/specs/` の `machine-core`、`machine-loader`、`machine-state` の各 spec にある。`allium/` と `openspec/` は、まだ公開していない。

### 3 つの役割

| 役割 | コード | 担当 |
| --- | --- | --- |
| コア | `msx/` | エミュレートするマシン：CPU、VDP、音源チップ、メモリとスロット、FDC、RTC、PPI と入力線。自分の状態と渡されたデータだけで決まる純粋な関数 |
| host | `__main__.py`、`host/` | プロセス単位のサービス：YAML と ROM ファイルの読み込み、ROM データベース、SRAM・ディスクイメージ・ステートセーブ・スクリーンショットのファイル、デバッガ、ソケット RPC |
| フロントエンド | `frontend/` | 表示と入出力機器：SDL2 のウィンドウ、音声出力とそのローパスフィルタ、ホストの入力機器、フレームのペース配分とフレームスキップ、ホットキー |

コアはファイルを開かず、ホストの時計を読まず、`host/`、`frontend/`、`sdl2` のどれも import しない。`tests/test_core_frontend_boundary.py` がこれを強制する。`sdl2` を import できないインタプリタで `msx` のすべてのモジュールを import し、3 つの名前の import があれば失敗にする。つまり、コアが必要とするものはすべて値として入り、コアが作るものはすべて値として出る。

### 境界を通る値

次の値は、どれも `msx/boundary.py` か `msx/input.py` にある。

| 値 | 向き | 中身 |
| --- | --- | --- |
| `MachineConfig` | 入力、1 回 | `document`（正規化した MachineConfig の JSON、後述）、`roms`（小文字 16 進の SHA1 をキーにした ROM イメージ）、`media`（`MediaId` をキーにした保存済みの SRAM と CMOS のイメージ）、`disks`（マウントする `DiskMedium`。キーはドライブ id）、`rtc_initial_time` |
| `InputFrame` | 入力、毎フレーム | アクティブ Low のキーボードマトリクス 11 行、アクティブ Low の 6 ビットのジョイスティックポート 2 つ（bit 0 上、1 下、2 左、3 右、4 トリガ A、5 トリガ B）、そのフレームのマウスの移動量（MSX 側の単位）、マウスの 2 つのボタン。作った後は変更できず、コンストラクタがすべてのフィールドを調べる |
| `FrameOutput` | 出力、毎フレーム | `video`（`RgbFrame`。描画しなかったフレームでは `None`）、`audio`、`frame_number` |
| `RgbFrame` | 出力 | 行優先に詰めた RGB24。`width` は 256 か 512、`height` は常に 212（192 ラインのモードは、コアがボーダーの色で埋める） |
| 音声 | 出力 | 出力フィルタを通す前の、44,100 Hz・符号付き 16 ビット・リトルエンディアンのモノラル PCM。NTSC は 1 フレーム 735 サンプル、PAL は 882 サンプル |
| スナップショット | 入出力 | `msx.state.save_state()` の `bytes`（後述） |
| メディアのイメージ | 入出力 | `MediaId` で名前を付けた `bytes` |
| `DiskMedium` | 入力 | `data`、`write_protected`、`path`。`path` は host 側でのファイルの名前。コアは記録するだけで、ファイルは開かない |

`MediaId` は閉じた集合。値は `slot1.sram`、`slot2.sram`、`slot<n>.<sub>.sram`（拡張スロットのデバイス。スロット 1〜2、サブスロット 0〜3）、`fmpac.sram`、`rtc.cmos`、`drive_a`〜`drive_d`（FDC のドライブ 0〜3）。外部とやり取りするときは、この文字列の値を使う。Python のコードでは `MediaId` を `.value` で文字列にする。

### コアの API

`msx/machine_loader.py` の `build_machine(config)` は `Machine` を返す。下の操作が境界にあたる。`Machine` のそれ以外は、内部用かデバッガ用。

| 分類 | 操作 | 備考 |
| --- | --- | --- |
| 構築 | `build_machine(config)` | 文書に誤りがあるか、ROM イメージが欠けているか合わないときは、構築を始める前に `MachineLoadError`（`msx/machine_spec.py`）を出す。任意の引数 `logger`/`tracer` は診断用 |
| フレームの実行 | `step_frame(frame_input, render_video) -> FrameOutput` | ちょうど 1 フレーム実行する |
| | `apply_input(frame_input)` | フレームを動かさずに、入力線とマウスのボタンをすぐ置き換える。RPC のキー入力が使う |
| | `samples_per_frame()`、`video_standard` | 735 か 882。NTSC か PAL（`video_standard.target_fps` は 60 か 50） |
| 制御 | `reset()` | 電源投入時のリセット。RAM、VRAM、マウント中のメディアはそのまま |
| | `msx.state.save_state(machine) -> bytes` | スナップショット |
| | `msx.state.load_state(machine, data, source)` | スナップショットが合わなければ `ValueError`（`StateLoadError`、`SnapshotMismatchError`）を出し、何も変えない |
| メディア | `dirty_media()` | 前回ファイルへ書き戻した後に書き込まれたメディアの id |
| | `read_media(media_id) -> bytes` | メディアの現在のバイト列 |
| | `mark_persisted(media_id)` | host がバイト列を書いた後に呼ぶ |
| | `mount_disk(drive, medium) -> bytes \| None` | `medium` に `None` を渡すと取り出す。外したイメージに保存していない書き込みがあれば、そのバイト列を返す。マシンにないドライブや、サイズが 512 の正の倍数でないイメージには `ValueError` を出す |
| | `disk_mounted(drive)`、`disk_path(drive)` | ドライブの状態と、記録した host 側の名前 |
| デバイス | `attach_mouse(MouseDevice(), JoystickPort(n))` | MSX のマウスをジョイスティックポート 1 か 2 につなぐ |

ほかのメンバーは境界を通らない。ソフトウェアを動かすだけなら、移植する必要はない。

- `run_frame()` と `step()`：内部のフレームループと 1 命令の実行
- ブレークポイントとウォッチポイント
- `set_pause_hook()`：RPC サーバが一時停止に使う接点
- `attach_debugger()`
- VDP とマッパーのトレーサ

### MachineConfig の文書

マシンの定義は、2 つの層を経てコアに届く。

1. **手で書く YAML**（`config/machines`、`config/devices`、`config/extensions`）。ハードウェアについてのコメントを残せる。読むのは host だけ（[マシンと拡張の YAML スキーマ](#マシンと拡張の-yaml-スキーマ) を参照）。
2. **MachineConfig の文書**。`load_machine_config()` が YAML とコマンドラインの指定から作る。RFC 8785（JCS）で正規化した JSON（UTF-8、キーはソート済み、空白なし、整数のみ）。`format_version` は `1`。解決済みのマシン、スロットごとのカートリッジとそのマッパー（`auto` にはならない）、拡張オーバーレイを持つ。エンコードと解析は `msx/machine_config.py` が行い、形は `msx/machine_config.schema.json`（JSON Schema draft 2020-12）に書いてある。スキーマを読むのはテストで、コアは読まない。

文書の中の ROM は、どれも `{name, size, sha1, pages}` の参照。`name` はディレクトリを含まないファイル名で、表示用のラベルにすぎない。`sha1` は host が読んだバイト列の SHA1。イメージは、その SHA1 をキーにして `MachineConfig.roms` で渡す。解析は厳密で、次のものを拒否する。

- 知らないキーや欠けたキー、型や範囲の誤り、浮動小数点数、重複したキー
- ほかの `format_version`
- 閉じた集合にない名前（マッパー、FDC のコントローラ、接続方式、その 2 つの組、フラットな拡張のデバイス、サブスロットのデバイス、I/O のデバイス）
- ROM が必要なのに持たないデバイス、`size_kb` のない `ram_mapper`
- ない ROM イメージや、サイズか SHA1 が参照と違う ROM イメージ

SRAM と CMOS のイメージ、ディスク、RTC の開始時刻、host 側のすべてのパスは、実行時の入力。文書の外にあるので、文書の同一性には影響しない。この同一性は SHA-256 で表し、対象はコアが文書を解析してからエンコードし直した正規形。したがって、空白やキーの順序だけが違う文書は、同一性も同じになる。`Machine` はこの値を `machine_config_sha256` として持つ。

YAML のローダを持たない移植では、文書を直接書いてもよい。Python の host が作った文書を使うこともできる。`load_machine_config()` でエンコードし、`config.document` と `config.roms` を保存する。

### スナップショット

`save_state()` は、マシンの全状態を正規化した JSON（フォーマットバージョン 19）で返す。形は `msx/snapshot.schema.json` に書いてある。オブジェクトはコンポーネントごとに 1 つ（`cpu`、`memory`、`slots`、`vdp`、`psg`、`ppi`、`scc`、`fdc`、`rtc`、`mouse`）。キーは常にすべてそろい、マシンにない部分の値は `null`。バイト列はパディング付きの RFC 4648 base64 の文字列で、整数はどれも ±(2^53 − 1) の範囲に入る。

- `load_state()` は、空白もキーの順序も問わない。浮動小数点数、NaN や Infinity、重複したキー、ほかのフォーマットバージョンは拒否する。
- 最初にマシンの同一性（マシンの種類、マッパーの種類、スロットの配線、FDC、マウント中のディスク）を調べる。続いて `machine_config_sha256` を動作中のマシンのハッシュと比べる。このハッシュは、カートリッジの ROM のバイト列も対象に含む。つまり、スナップショットは同じ MachineConfig の文書から作ったコアにしか読み込めない。
- どれかの検査に失敗したスナップショットは、何も変えない。
- `save_state()`、`load_state()`、もう一度 `save_state()` の順に呼ぶと、同じバイト列になる。

スナップショットが持たない状態は 2 種類ある。1 つは漢字フォント ROM のアドレスと読み出しカウンタで、これは意図して保存しない（`machine-state` の spec を参照）。もう 1 つは音声フィルタの状態など、コアの外にあるもの。

### フレームの実行

`Machine.step_frame(frame_input, render_video)` は、コアを 1 フレーム進める呼び出し。`frame_input` を、PPI と PSG が読む入力線（`InputLines`）に反映するので、入力はフレームの間ずっと変わらず、マウスはそのフレームの移動量を 1 回だけ受け取る。その後フレームを実行し、`FrameOutput` を返す。`render_video` が決めるのは、コアが映像を作るかどうかだけ。映像を作らないフレームでもスプライトの処理は行うので、VDP のステータス、音声、次のスナップショットは、どちらでも同じになる。

入力は、コアの外にある `host/input_state.py` の `InputState` が組み立てる。キーボード、ゲームパッド、マウス、RPC の入力メソッドはそこに書き込む。各ポートのキーボード側とゲームパッド側の入力は、合成するまで別々に持つ。フロントエンドは毎フレーム `take_frame()` を `step_frame` に渡し、そのとき組み立て側のマウスの移動量も 0 に戻る。RPC の入力メソッドは `Machine.apply_input` を呼ぶ。`apply_input` はフレームを動かさずに入力線とマウスのボタンをすぐ反映するので、一時停止中のマシンでも次の命令から入力が見える。ヘッドレスの実行（`--count-frame`、`--benchmark`）は空の `InputFrame()` を渡す。

### 保証

- **決定性。** 同じ MachineConfig から 2 つのコアを作り、同じ `InputFrame` の列を与える。すると、映像、音声、スナップショットのバイト列は同じになる。何がコアを動かしても、`render_video` にどんな値を渡しても同じ。RTC はエミュレートしたサイクル数で進み、ホストの時計は使わない。
- **1 回の呼び出しで 1 フレーム。** ふつうの実行モードでは、`step_frame` の 1 回の呼び出しでちょうど 1 フレーム進み、1 フレーム分の音声を返す。1 フレームは NTSC で 59,736 T ステート、PAL で 71,364 T ステート（1 命令分の誤差の範囲で）。デバッガのブレークでフレームが途中で終わることがある。このモードは契約の外。
- **RGB 変換はコアの中。** コアは整数の表だけを使って、すべてのピクセルを RGB に変換する。フロントエンドがパレットや VDP のレジスタを知る必要はない。
- **フィルタ前の音声。** 音声は、マシンにあるすべての音源（PSG、SCC、Majutsushi DAC、キークリック、OPLL）の出力を、フィルタを通す前に足し合わせて範囲に収めたもの。フィルタ、リサンプリング、バッファリングはフロントエンドの担当。
- **出力の有効期間。** `FrameOutput.video` と `.audio` が有効なのは、次の `step_frame` まで。Python では毎回新しく作る、変更できない `bytes` なので、SDL2 フロントエンドは最後のフレームをコピーせずに持っている。ネイティブのコアはバッファを使い回してよい。その場合、フロントエンドは手元に長く残すフレームをコピーする。
- **メディア。** メディアは、エミュレートしたマシンが最初に書き込んだ時点から、`mark_persisted` が呼ばれるまで dirty。すでに入っている値と同じ値の書き込みは数えない。書き込み禁止のメディアは dirty にならない。SRAM の id の集合はコアが構築時に決め、その後は変わらない。ディスクを交換するときは、外す側のイメージが dirty なら先に書き戻し、それから `mount_disk` を呼ぶ。`host/media.py` の `swap_disk` はこの順で処理するので、書き戻しに失敗しても、ドライブは元の状態のまま。

### 組み込み例

この例は読み込みに Python の host を使い、コアをヘッドレスで動かす。別のフロントエンドに必要な最小限の形。ループの中身を、自前の表示、音声出力、入力の処理に置き換えて使う。

```python
from pathlib import Path

from host.machine_config import (
    load_device_registry, load_machine_config, load_machine_spec, sram_paths,
)
from host.media import persist_dirty_media
from msx.input import InputFrame
from msx.machine_loader import build_machine
from msx.state import load_state, save_state

root = Path(".")                                   # リポジトリのルート
registry = load_device_registry(root / "config")
spec = load_machine_spec("cbios_msx2", root / "config", registry, root)
config = load_machine_config(spec)                 # host：YAML とファイル -> MachineConfig
machine = build_machine(config)                    # コア：値だけを使う

for _ in range(120):
    out = machine.step_frame(InputFrame(), render_video=True)
    # out.video.pixels：width * 212 * 3 バイトの RGB24
    # out.audio：samples_per_frame() 個の符号付き 16 ビット LE モノラルのサンプル

snapshot = save_state(machine)                     # bytes。保存先は自由
load_state(machine, snapshot)
assert save_state(machine) == snapshot

persist_dirty_media(machine, sram_paths(config))   # SRAM/CMOS/ディスクの変更を書き戻す
```

SDL2 フロントエンド（`frontend/sdl2_frontend.py`）も同じ形で動く。毎フレーム、入力の組み立て側が作ったフレームを `step_frame` に渡す。コアがフレームを描画したときは `out.video` をアップロードする。`out.audio` は `BiquadLowPass`（`frontend/audio_filter.py`）に通して SDL のキューに積む。終了時には `__main__.py` が `persist_dirty_media` を呼ぶ。自前の host 側を持たないフロントエンドは、一度の実行で得た `config.document` と `config.roms` を取っておき、それをもとに `MachineConfig` を直接作ればよい。

### コアの移植

Python のコードは、`allium/core-frontend.allium` にある言語に依存しない契約と、次の点で違う（同ファイルの *Status* のコメントに一覧がある）。

- 制御の操作は、結果を表すレコードではなく `None` を返す。`save_state` と `load_state` は、マシンを引数に取る `msx/state.py` の関数。
- `eject_disk` は `mount_disk(drive, None)`。`read_media` はバイト列だけを返し、書き込み禁止のフラグはマウント中のイメージが持つ。
- フレームレートはメソッドではなく、`VideoStandard` のデータ。
- マウスはフロントエンドが実行時につなぐ（`attach_mouse`）。契約では設定のフィールドになっている。どちらの形にするかは未決定。
- `load_machine_config()` は、host の SRAM ファイルのパスを加えた `LoadedMachineConfig` を返す。コアが読むのは `MachineConfig` の部分だけ。

次の点は未決定で、移植する側が決める必要がある。

- **OPLL の表。** `msx/opll.py` は、log-sin、指数、キースケールの表を、インポート時に浮動小数点の計算で作る。移植先で計算し直すと最下位ビットが違うことがあり、そうなると FM-PAC の音声がバイト単位で一致しない。Python 側で作った表を整数のデータとして持たせる。
- **マウスの接続**（上記）。
- **デバッグの制御。** ブレークポイント、ウォッチポイント、1 命令の実行、RPC の一時停止は契約の外。最初の移植では省いてよい。

静的型付けの言語にそのままの対応がない Python のパターンもある。バスフック、レジスタのプロパティ、PSG エンベロープのアンダーフロー、レンダラの変換表、Callable のフック。[移植性](#移植性) を参照。

移植は、次の順で進めることを提案する。

1. **CPU、メモリとスロット、VDP、PSG**。MSX1 の C-BIOS マシン 1 台（`cbios_msx1`）を対象にする。MachineConfig の文書と ROM イメージは Python の host から取る。両方で、空の入力のまま N フレームをヘッドレスで実行する（`--count-frame` と同じ）。そのあと `save_state()` のバイト列を比べる。違いがあれば、どのコンポーネントのどのフィールドかがわかる。
2. **スナップショットの形式**。一方の実装で保存したスナップショットを、もう一方で読めるようにする。この段階を終えると、どちらの実装で保存した状態も、もう一方の開始状態に使える。そのため、プログラムのどの時点からでもテストを始められる。
3. **MSX2**：V9938 とそのコマンドエンジン、RAM マッパー、RTC、拡張スロット（`cbios_msx2`）。
4. **残りの音源とカートリッジ**：SCC、DAC とキークリック、表を持たせた OPLL、SRAM 付きのマッパー。
5. **FDC**（WD2793 と TC8566AF）とメディアの API。

どの段階でも、Python のコアとバイト列を比べる。同じ設定と同じ `InputFrame` の列から、同じ `RgbFrame.pixels`、音声、スナップショットのバイト列が得られなければならない。

## 移植性

実装は純粋な Python 3.10+ で、SDL2 フロントエンドを除いて C 拡張やネイティブのバインディングはない。コアの処理を静的型付けのシステム言語（Rust、C++）に移しやすくするため、いくつかの設計を決めている。各判断の理由は、対応するソースファイルに _Portability note_ として書いてあり、この節はその要約。

### 移植性を意識した設計

次のパターンは、静的型付けの言語にそのまま移せる。

- **オペコードの振り分け**（`msx/cpu/z80.py`、`msx/cpu/opcodes_main.py`）：`_DISPATCH` はインポート時に一度だけ作る、256 要素のフラットな callable のリスト。各要素はふつうの関数。Rust では関数ポインタの配列か、オペコードのバイトに対する `match` になる。
- **レジスタ**（`msx/cpu/registers.py`）：主レジスタは Python の `int` のフィールドで、書き込むたびに `& 0xFF` / `& 0xFFFF` で明示的にマスクする。多倍長の値には頼らない。裏レジスタは、`A_`、`BC_` などの名前の付いた個別のフィールドで、リフレクションは使わない。
- **VRAM と RAM**：サイズ固定の `bytearray`（V9938 は `_VRAM_SIZE = 131072`、TMS9918A は 16384、RAM マッパーはセグメントの配列）。アクセスはすべてスライスとインデックスで直接行う。int のリストや辞書を使った記憶域はない。
- **I/O バスの振り分け**（`msx/io.py`）：`list[tuple[int, int, Callable]]` を先頭から調べる。辞書の検索もリフレクションも使わない。登録するハンドラは少なく、マシン設定ごとに決まっている。
- **コンポーネントの構造体**：ハードウェアのコンポーネントはすべて `@dataclass` で、フィールドはすべて型付きで明示的に宣言する。`msx/` のどこにも `__getattr__`/`__setattr__` の仕掛けはない。最も頻繁に通る 2 つの構造体、`Z80` と `Registers` は `@dataclass(slots=True)` にして、インスタンスごとの `__dict__` をなくしている。`Memory` と、V9938 の小さな値の型いくつか（ログのエントリの `_RegChange`/`_PaletteChange`、`_RenderStatus`、`_SatSegment`）も同じ。ほかは素の `@dataclass`。頻繁に通らない場所では、フィールド配置の保証はそれほど重要でないため。
- **タイミングの定数**：`CYCLES_PER_LINE`、`NTSC_LINES_PER_FRAME` / `PAL_LINES_PER_FRAME`（`msx/vdp/_geometry.py`）、`_HBLANK_START`、`_TR_REASSERT_CYCLES` などは、モジュールレベルの整数定数。1 フレームの予算は `VideoStandard` 列挙型のプロパティから得る。移植では `match` / `const fn` として書く。

### 移植時に対応が必要な Python 固有パターン

次のパターンは、静的型付けの言語にそのままの対応がない Python の機能を使う。各パターンについて、Rust/C++ の移植で代わりに使うものをソースのコメントに書いてある。

#### バスフックとしての再代入可能な束縛メソッド

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
