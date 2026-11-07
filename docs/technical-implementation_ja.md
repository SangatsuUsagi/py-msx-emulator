# 技術実装ドキュメント

[← README_ja.md](../README_ja.md)

この文書は MSX1/MSX2 エミュレータの内部構造を説明します。CPU の実行、割り込みの処理、I/O の振り分け、VDP の描画、メモリまわりを扱います。

- [CPU エミュレーション](#cpu-エミュレーション)
- [割り込み管理](#割り込み管理)
- [I/O バス](#io-バス)
- [VDP](#vdp)
- [オーディオ](#オーディオ)
- [フレームの速度調整（フロントエンド）](#フレームの速度調整フロントエンド)
- [メモリとスロットシステム](#メモリとスロットシステム)
- [フロッピーディスク（FDC）](#フロッピーディスクfdc)
- [マシンと拡張の YAML スキーマ](#マシンと拡張の-yaml-スキーマ)
- [マシン YAML ローダ](#マシン-yaml-ローダ)
- [コアの境界と移植ガイド](#コアの境界と移植ガイド)
- [移植性](#移植性)

---

## CPU エミュレーション

### 命令デコード

Z80 CPU は `msx/cpu/z80.py` に実装しています。オペコードの振り分け表 `_DISPATCH` は、256 個の callable を並べたフラットなリストです。`msx/cpu/opcodes_main.py` の `opcodes_main._build_dispatch()` がインポート時に一度だけ作り、`z80.py` がモジュールレベルの定数として束縛します。

```python
_DISPATCH: list[Callable[[Z80], int]] = _opcodes_main._DISPATCH
```

命令の実行は 1 回のインデックス呼び出しで済みます。辞書の検索も `match`/`case` も使いません。

```python
n = _DISPATCH[opcode](cpu)
```

CB、DD、ED、FD のプレフィックス表も同じ方式で動きます。メインの振り分けがプレフィックスのバイトを見つけると、次のバイトを読んで対応するプレフィックス表に渡します。

### 実行ループ

`Z80.step() -> int` は 1 命令を実行します。以下のコストはすべて T ステート（Z80 の基本クロック単位）で表します。

1. `nmi_pending` がセットされていれば、PC をプッシュして 0x0066 にジャンプし、11 T ステートを返します。
2. `iff1` と `int_pending` がどちらも true で、`ei_pending` がセットされていなければ、割り込みを受け付けます。モード 1 は PC をプッシュして 0x0038 にジャンプし、13 T ステートを返します。モード 2 は PC をプッシュし、`I<<8 | data_bus` からベクタを読んでハンドラにジャンプし、19 T ステートを返します。
3. `ei_pending` がセットされていれば、クリアします。EI が割り込みを有効にするのは、その次の命令の後からです。手順 2 が `ei_pending` を見て、この手順がクリアするので、手順 2 で受け付けるはずの割り込みはちょうど 1 ステップ待つ。
4. `halted` がセットされていれば、オペコードを読まずに `4 + m1_wait_states` T ステート進めます。純粋な Z80 コアなら 4 で、MSX の M1 ウェイトもここで加わります。
5. それ以外は、clock 記録（`msx/clock.py`）の `instruction_pc` に現在の PC を記録し、`q_next` を `q` に移して `q_next` をクリアし（Qです。`SCF`/`CCF` が読む）、`_fetch_opcode()` でオペコードを読んで振り分けます。

`_fetch()` は `read_byte(PC)` で 1 バイト読み、PC を 1 進めます。`_fetch_opcode()` は同じことをしたうえで、M1 サイクルごとに 1 回 R を進めます。M1 サイクルにあたるのは、主オペコードと、CB/DD/ED/FD プレフィックスの次のオペコードバイトです。オペランドとディスプレースメントのバイトは R を変えません。`DDCB`/`FDCB` のディスプレースメントと最後のオペコードも同じです。R が数えるのは bit 0〜6 だけです。bit 7 は保持され、`LD R,A` のようなレジスタ全体への書き込みでしか変わりません。実機の SONY HB-F1XD と HB-10 でも R の進み方は同じです。

### タイミング

`step()` はその命令が使った T ステート数を返します。`Machine.run_frame()` はこの値を、フレームごとの合計と `machine.cycle_count` に足します。NTSC の 1 フレームの予算は **59,736 T ステート**で、これは 1 ライン 228 T ステート × 262 ラインにあたります。1 ライン 228 T ステートは、実機の TMS9918A/V99x8 のラスタタイミングです。openMSX の共通のラスタクロックモデルでも、両方の VDP 系列でこの値になっています。

この予算は、Z80A のクロック（3.579545 MHz）を 60 Hz で割った値とは違います。実機の MSX の NTSC のフィールドレートは、ちょうど 60 Hz ではなく約 59.92 Hz です。PAL のマシンは 1 フレーム 313 ラインで、予算は 71,364 T ステートになります（[マシン YAML](#マシン-yamlconfigmachinesyaml) の `video_standard` を参照）です。

#### MSX M1 ウェイトステート

実機の MSX は、Z80 の M1 サイクル（オペコードフェッチ）ごとに 1 ウェイトを入れます。コアはこれを、設定できる `Z80.m1_wait_states` フィールドで表します。デフォルトは `0` で、データシートどおりの純粋な Z80 になるので、MSX 以外のシステムにもコアを使えます。MSX のマシン YAML は `cpu.m1_wait_states: 1` を設定し、`machine_loader` がこの値を `Z80(...)` のコンストラクタに渡します。

ウェイトは命令のバイトごとではなく、M1 サイクルごとに足します。`step()` は主オペコードのフェッチに `m1_wait_states` を足し、各プレフィックスのハンドラ（`_op_prefix_cb/dd/fd/ed`）が 2 回目の M1 にもう一度足します。命令ごとの増え方は次のようになります。

- プレフィックスのない命令は、オペランドの長さに関係なく `+1`
- `CB`/`ED`/`DD`/`FD` の命令は `+2`
- `DDCB`/`FDCB` も `+2` です。ディスプレースメントと最後のオペコードは、M1 サイクルではなくオペランドの読み出しだからです。

この数え方は openMSX（`Z80.hh` の `WAIT_CYCLES = 1`、M1 の基本が 5、データシートでは 4）と同じです。フレームの予算は 59,736 T ステートのままなので、ウェイトの分だけ 1 フレームに実行できる命令が減ります。おかげで、CPU のループで時間を計るコード（遅延ループで鳴らすソフトウェア PCM など）が約 13% 速く動いてしまうことはなく、実機と同じ速さになります。ウェイトは割り込みの周期（VBlank）にも PSG の音程にも影響しません。

### 命令テスト

CPU コアは、Frank D. Cringle による命令テスト zexdoc と zexall をすべて通ります（どちらも 67 項目すべて）。`tools/run_z80_exercisers.py` は、MSX のマシンを組まずに `Z80` コアだけでこれらを動かします。

- `.com` ファイルは CP/M のプログラムとして動かします。64 KB の平坦な RAM の 0x0100 に読み込み、BDOS の機能 2 と 9 でコンソールに出力し、0x0000 へのジャンプで終了します。`ERROR` を含む行があれば失敗とみなします。
- `.tap` ファイルは Patrik Rak の [z80test](https://github.com/raxoft/z80test) として動かします。テストが使う少数の ZX Spectrum ROM の機能は、ツールが代わりのコードで置き換えます。出力に `tests failed` があれば失敗とみなします。

zexdoc と zexall は GPL なので、テストプログラムはこのリポジトリに含めていません。実行時にパスを渡します。

```bash
venvs/pypy-msx-emulator/bin/python tools/run_z80_exercisers.py \
    path/to/zexdoc.com path/to/zexall.com path/to/z80full.tap
```

どれかのプログラムが失敗を報告すると、終了ステータスは 1 になります。期待される結果は、zexdoc、zexall、z80test のすべての種類が通ることです。z80test が繰り返し中の動作を調べるのは `INIR` と `INDR` だけ（`INIR->NOP'`、`INDR->NOP'`）です。`OTIR`/`OTDR` の同じ規則は、ユニットテストで確かめています。PyPy では、zexdoc と zexall はそれぞれ Apple M5 Pro で約 1.5 分かかります。CPython ではさらに長くなります。

### 1 命令ごとのテスト（SingleStepTests）

[SingleStepTests/z80](https://github.com/SingleStepTests/z80)（MIT）は、1604 個の JSON ファイルからなります。ファイルはオペコードごとに 1 つあり、プレフィックス付きの命令もすべて含みます（`00.json`、`cb 00.json`、`dd cb __ 06.json`、`ed b0.json` など）です。各ファイルは 1000 件のテストを持ちます。1 件には、1 命令を実行する前（`initial`）と後（`final`）の CPU の状態と RAM が入っています。I/O の読み書き（`ports`）と、サイクルごとのバスの状態（`cycles`）も入っています。作者は、Ares の Z80 コアを移植したものでテストを作り、あとから誤りを直しました。テストは実機から取ったものではありません。

`tools/run_singlestep_tests.py` は、このテストを `Z80` コア単体で動かします。テストファイル（1.3 GB）はこのリポジトリに含めていません。実行時にパスを渡します。

```bash
venvs/pypy-msx-emulator/bin/python tools/run_singlestep_tests.py \
    path/to/SingleStepTests/z80/v1/*.json
```

- `--limit N` は、各ファイルの先頭 N 件だけを動かします。PyPy では全件が Apple M5 Pro で約 3 分かかります。CPython では `--limit` を使います。
- `--divergences PATH` は、既知の食い違いの一覧に別のファイルを使います（後述）。
- 環境変数 `PY_MSX_ROOT` で、どの作業ツリーのコアを試すかを選べる。既定はツールがあるリポジトリです。

このツールは 1 件ごとに、64 KB の平坦な RAM を持ち M1 ウェイトのない `Z80` を作ります。テストに載っていない RAM のバイトは 0 にします。`initial` を読み込んで `step()` を 1 回実行し、結果を `final` と比べます。

| JSON の項目 | コアの状態 |
| --- | --- |
| `a f b c d e h l i r` | `A`、`F`、`BC`/`DE`/`HL` の各バイト、`I`、`R` |
| `pc sp ix iy wz` | `PC`、`SP`、`IX`、`IY`、`WZ`（MEMPTR） |
| `af_ bc_ de_ hl_` | 裏レジスタ |
| `iff1 iff2 im` | `iff1`、`iff2`、`im` |
| `ei` | `ei_pending`（EI の直後で、次の命令の前には割り込みを受け付けない） |
| `q` | `q_next`（次の命令が見るフラグのラッチ。`step()` が命令の前にこれを `q` に移す） |
| `ram` | テストに載っている RAM のバイト |

ポートへの読み書きも、アドレス、値、読みか書きかを順に `ports` と比べます。`step()` が返す T ステート数は、`cycles` の件数と比べます。ポートを読むと、`ports` にそのアドレスの値があればその値を、なければ 0xFF を返します。失敗したときは、テストの名前と、値が違った項目を表示します。

サイクルごとのバスの状態は比べません。`p`（直前の命令が `LD A,I`/`LD A,R` だったか）も比べません。この状態を使う割り込みの癖を、コアが再現していないためです。

`tools/singlestep_known_divergences.json` には、コアが意図して別の結果を出すテストを載せています。各項目は次のキーを持ちます。

- `id`：項目の名前
- `files`：対象のオペコードのファイル
- `fields`：値が違う項目
- `core` と `tests`：コアとテストのそれぞれの動作
- `reference`：根拠

載っているファイルのテストが失敗し、違った項目がすべてその項目の `fields` に含まれていれば、既知の食い違いとして数えます。動かしたファイルのどのテストにも当てはまらない項目は、役目を終えた項目（stale）として報告します。このため、コアを直したときは一覧も直す必要があります。終了ステータスは、ほかの失敗も役目を終えた項目もなければ 0、あれば 1 になります。コマンドラインの誤りは 2 になります。

結果として、コアは 1,604,000 件のうち、2 つのグループを除いてすべて一致します。この 2 つは、どちらも既知の食い違いです。

- `ldxr-cpxr-repeat-memptr`：繰り返し中の `LDIR`/`LDDR`/`CPIR`/`CPDR`です。コアは openMSX と同じく、MEMPTR を命令のアドレス + 2 にします。テストは + 1 にします。対象は 3997 件です。`CPIR` の 3 件はすぐに終わるので一致します。
- `block-out-port-high-byte`：`OUTI`/`OUTD`/`OTIR`/`OTDR`。コアは openMSX と同じく、減らす前の B をポートアドレスの上位バイトに出します。テストは減らした後の B を出します。対象は 4000 件です。MSX は下位バイトしか見ません。

テストはエミュレータのコアから作られているので、どちらの値が正しいかの根拠にはなりません。どちらも未解決のままです。

コアを他の言語に移植したときも、同じ JSON ファイルと同じ食い違いの一覧を使えます。MSX のマシンも OS の代わりのコードもなしに、1 命令ずつ確かめられます。サイクル単位まで正確に作る移植なら、`cycles` でバスの動きも確かめられます。このツールは `cycles` を使いません。

### 既知の制限

OTIR/INIR などのブロック I/O 命令は、ページ境界をまたぐとサイクル単位では正確ではありません。非公開のフラグ Y（bit 5）と X（bit 3）は、資料にある命令ごとの規則に従います。あまり一般的でない次の 3 つの要因も再現しています。`BIT n,(HL)` は、内部レジスタ MEMPTR から Y と X を取ります。繰り返し中のブロック命令（`LDIR`/`LDDR`/`CPIR`/`CPDR`/`INIR`/`INDR`/`OTIR`/`OTDR`）は、自身のアドレスから Y と X を取ります。`SCF`/`CCF` は、直前の命令がフラグを変えたかどうかで結果が変わります。`SCF`/`CCF` は Zilog NMOS の規則に従います。NEC 製や ST 製 CMOS の Z80 では規則が異なります（[Undocumented Flags](https://github.com/hoglet67/Z80Decoder/wiki/Undocumented-Flags)）。エミュレータは Zilog 互換の Z80 を対象にしています。TODO: 一部の MSX が使う NEC 製や Sharp 製の Z80 互換 CPU（たとえば HB-10 や HB-F1XD のもの）での違いを、実機で確認したうえで記録します。繰り返し中の `INIR`/`INDR`/`OTIR`/`OTDR` は、さらに H と P/V を変え、MEMPTR を自身のアドレス + 1 にします（[redcode/Z80](https://github.com/redcode/Z80)）。

---

## 割り込み管理

### MSX1：レベルベース VBlank IRQ

MSX1 の TMS9918A VDP の割り込み源は 1 つだけで、垂直帰線期間の始まりに立つ VBlank です。割り込み線のレベルは `F && IE0`（S#0 bit 7 と R#1 bit 5）で決まります。`VDP.irq` がこのレベルを保持し、どちらかの入力が変わると VDP が計算し直します。

- `Machine.run_frame()` が `display_height` のスキャンライン境界でフレームを描画した後、`VDP.begin_vblank()` が F をセットします。ブレークや一時停止で途中で終わったフレームではセットしません。
- S#0 を読むと F がクリアされます。
- R#1 への書き込みで IE0 が変わります。

そのため、F が立ったままプログラムが IE0 をセットすると、実機と同じくすぐに割り込み線が立ちます。`Machine.run_frame()` は、下の MSX2 と同じく、命令の境界ごとに割り込み線を読みます。

```python
cpu.int_pending = vdp.irq
```

MSX1 にはスキャンラインごとの割り込みはありません。

### MSX2：レベルベース IRQ

MSX2（V9938）の割り込みもレベルで扱います。`Machine.run_frame()` の内側のループは、命令の境界ごとに VDP の `irq` を読みます。使うのは、上の MSX1 と同じ `cpu.int_pending = vdp.irq` の 1 行です。

`V9938.irq` は `irq_pending()` の結果を返します。

```python
def irq_pending(self) -> bool:
    ie0 = bool(self.regs[1] & 0x20)   # IE0 = R#1 ビット 5
    f   = bool(self.status & 0x80)     # F   = S#0 ビット 7（VBlank）
    ie1 = bool(self.regs[0] & 0x10)   # IE1 = R#0 ビット 4
    fh  = bool(self._status1 & 0x01)  # FH  = S#1 ビット 0（H ライン）
    return (ie0 and f) or (ie1 and fh)
```

実機と同じく、ポート 0x99 の読み出しでステータスフラグがクリアされるまで、CPU には割り込みのレベルが true に見えます。S#0（R#15 = 0）を読むと F が、S#1（R#15 = 1）を読むと FH がクリアされます。

### 水平ライン割り込み

`Machine.run_frame()` はフレームをスキャンラインに分け、各スキャンラインの T ステート予算の終わりで `vdp9938.begin_scanline(L)` を 1 回呼びます。概略は次のとおりです（`Machine._run_lines_fast`）。

```python
for line in range(first_line, end_line):   # NTSC は 1 フレーム 262 ライン
    line_end = (line + 1) * cpf // lpf
    while total < line_end:
        cpu.int_pending = vdp9938.irq
        n = cpu_step()                     # または step_idle_batch()（後述）
        total += n
        if vdp9938._cmd_active:
            vdp9938.tick(n)                # コマンドエンジンの実行中
        else:
            vdp9938._line_cycle += n       # 水平帰線のタイマーだけ
    vdp9938.begin_scanline(line)
```

`begin_scanline` によるレベルの変化は、CPU が次の命令の境界で読み取ります。

ループは 2 つの区間に分けて回します。`vblank_start_line` までのラインと、フレームの残りです。マシンは 2 つの区間のあいだで、VBlank の割り込みハンドラが動く前にフレームを描画します（[VDP](#vdp) を参照）です。ブレークすると、ループは命令の境目で止まります。マシンはラインと T ステート数を覚えておき、次の `run_frame` がそこから続けます。そのため、描画、数え上げ、音声の合成は、どのフレームでも 1 回ずつになります。

CPU が待機中で割り込みを受け付けられない間、ループは `step()` の代わりに `Z80.step_idle_batch()` を呼びます。待機中とは、HALT 中か、`JR $`（`18 FE`、自分自身へのジャンプ）を実行した直後を指します。`JR $` は多くのゲームが VBlank 割り込みを待つのに使うループです。`step_idle_batch()` は 1 回の呼び出しで、ラインの終わりまでの待機をまとめて進めます。R の値と T ステートの合計は 1 命令ずつ実行した場合と同じになります。`JR $` をまとめる前には、PC の 2 バイトがまだ `18 FE` であることを確かめます。V9938 のコマンドの実行中はまとめません。コマンドエンジンがラインの途中で割り込みを起こすことがあるからです。

`begin_scanline(line)` は、割り込みを起こすラインを `(R#19 − R#23) & 0xFF` で計算します。`line == effective_irq_line` で、IE1（R#0 bit 4）がセットされていれば、S#1 bit 0（FH）をセットします。比較の対象は 0〜255 のどのラインでもよく、ボーダーや垂直帰線期間のラインも含みます。IE1 がクリアの間は FH をラッチしないので、後から IE1 をセットしても、すでに過ぎたラインの割り込みは起きません。

FH は S#1 を読むまでセットされたままになります。読むと FH がクリアされ、`irq` が計算し直されます。

`begin_scanline(vblank_start_line)` は VBlank フラグもセットします。`vblank_start_line` は表示ライン数（192 か 212）なので、これは表示領域のすぐ次のラインにあたります。MSX2 では、以前あったフレーム単位の終了処理をこれで置き換えています。

---

## I/O バス

### 登録と振り分け

`IOBus`（`msx/io.py`）は、`(start, end, handler)` のタプルのリストを、読み出し用と書き込み用に 1 つずつ持ちます。デバイスはマシンの構築時に自分を登録します。

```python
io.register_read(0x98, 0x99, vdp.read_port)
io.register_write(0x98, 0x9B, vdp.write_port)
```

`read_port` と `write_port` は、Z80 の 16 ビットのポートアドレスを 8 ビットにマスクし（`port &= 0xFF`）、リストを先頭から調べます。`[start, end]` の範囲にポートを含む、最初に登録したハンドラが処理します。合うハンドラがなければ、読み出しは 0xFF（オープンバス）を返します。

### ログ

`_logger` があるとき、バスは読み出しをハンドラが戻った後に記録します。値がそのとき初めてわかるからです。書き込みは振り分けの前に記録します。値が呼び出し側ですでに決まっているからです。どちらの記録にも、ポート、値、現在の命令の PC が入ります。

### 標準ポートマップ

| ポート    | デバイス                                      |
| --------- | --------------------------------------------- |
| 0x98–0x9B | VDP（TMS9918A または V9938）                  |
| 0x9A      | V9938 パレット（MSX2 のみ）                   |
| 0xA0–0xA2 | PSG（AY-3-8910）                              |
| 0x7C–0x7D | FM-PAC の OPLL（`--extensionN fmpac` のとき） |
| 0x90–0x91 | プリンタポート（`hb_10`、`hb_10_64k`）        |
| 0x90–0x97 | プリンタポート（S-1985 のマシン）             |
| 0xA8–0xAB | PPI（i8255）                                  |
| 0xB4–0xB5 | RTC（RP5C01、MSX2 のみ）                      |
| 0xD8–0xDB | 漢字 ROM（`hbi_j1` などの拡張）               |
| 0xFC–0xFF | RAM マッパーセグメントレジスタ（MSX2 のみ）   |

---

## VDP

### TMS9918A（MSX1）

`msx/vdp/vdp.py` が TMS9918A を実装し、レンダラは `msx/vdp/renderer.py` にあります。16 KB の VRAM と 8 個のコントロールレジスタを持ち、スクリーンモード 0〜3（テキスト、グラフィック 1/2、マルチカラー）に対応します。

`render_frame()` は 256×192 の表示フレームを、`Machine.run_frame()` の途中、垂直帰線期間の始まりで描画します。続いて `Machine.run_frame()` が `VDP.begin_vblank()` を呼び、VBlank フラグをセットして割り込み線（`VDP.irq`）を更新します。レンダラは共通のヘルパー `msx/vdp/_geometry.py` でフレームを 256×212 の一定の出力にそろえます（上下に 10 行ずつボーダーを足す）。VDP の表示ライン数が 192 でも 212 でも、どのフレームも同じ 4:3 の形になります（後述の V9938 の `display_height` を参照）です。

### V9938（MSX2）

`msx/vdp/v9938.py` が V9938 を実装し、レンダラは `msx/vdp/v9938_renderer.py` にあります。TMS9918A との主な違いは次のとおりです。

- 128 KB VRAM
- 28 個のコントロールレジスタ（R#0–R#27）と 15 個のコマンドエンジンレジスタ（R#32–R#46）
- 16 色のプログラマブルパレット（9 ビット GRB333）。リセット時に読み込みます `_MSX2_DEFAULT_PALETTE` は、V9938 データブックの電源投入時の値と同じ
- 3 つのステータスレジスタ：S#0（VBlank/スプライト）、S#1（水平ライン FH）、S#2（コマンドエンジン CE/TR、リトレース HR/VR）
- ハードウェアコマンドエンジン
- `irq_pending()` によるレベルベース IRQ

`display_height` プロパティはふだん 192 を返し、R#9 bit 7（LN）がセットされていると 212 を返します。`display_width` プロパティは次のモードで 512 を返し、ほかのモードでは 256 を返します。

- 横に広いモード SCREEN 6/G5 と SCREEN 7/G6（M5 がセット、M4 がクリア）。
- TEXT2、SCREEN 0 WIDTH 80（M1 と M4 がセット、M3 がクリア）。

### バンディングレンダラ

フレームの途中で VDP のレジスタを書き換えるゲームがあります。たとえば、表示領域ごとにパレットやスクリーンモードを切り替えます。レンダラはこの変更を正しいスキャンラインで反映しなければなりません。

`V9938.write_port()` は、表示に関係するレジスタへの書き込みをすべて `_reg_write_log` に記録します。

```python
_DISPLAY_REGS = frozenset({0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 18, 19, 23})
```

各エントリは次の 2 つの型のどちらか（`msx/vdp/_v9938_defs.py`）で、読む側は型で区別します。

- `_RegChange(line, reg, value)`：レジスタへの書き込み
- `_PaletteChange(line, idx, rgb)`：パレットへの書き込み（ポート 0x9A）。`rgb` はパレットが持つ 9 ビットの値で、並びは `r << 6 | g << 3 | b`

`begin_scanline(0)`（フレームの先頭）がログをクリアします。`render_frame()` はクリアしません。そのため `V9938.to_rgb24()` は、コアの中でフレームを RGB24 に変換するときにこのログを使い、スキャンラインごとに正しいパレットを当てられます。

`render_frame_v9938(vdp)` は `_reg_write_log` を調べて、バンドの境界を見つけます。境界は、表示に関係する値が前のバンドのスナップショットと変わるラインです。連続するバンド `[y0, y1)` は、その先頭のレジスタ状態で描画します。バンドごとのレンダラには `y_start` と `y_end` を明示的に渡し、どのスキャンラインもちょうど 1 回だけ描画されます。

ログが空（フレーム途中の書き込みがない）なら、`render_frame_v9938` は 1 回で全体を描画します。出力も速度も、バンド処理を入れる前と同じです。

スプライトは、スプライトアトリビュートテーブル（SAT）の領域ごとに 1 回描画します。SAT の領域とは、SAT のベース（R#5/R#11）が同じバンドが続く最大の範囲を指します。SAT のベースがフレームを通して変わらなければ（ふつうの場合）、スプライトの描画は 1 回で、1 回で全体を描く場合と同じになります。SAT のベースがフレームの途中で変わると（スプライトの多重表示など）、各領域のスプライトはその領域の SAT から描きます。領域ごとに違うバッファを読むので、同じスプライトが 2 回出ることはありません。

### コマンドエンジン

V9938 のハードウェアコマンドは、バイト単位とピクセル単位の VRAM 操作に対応します。

| コマンド | 説明                                               |
| -------- | -------------------------------------------------- |
| HMMV     | 矩形領域をバイト値で塗りつぶします                 |
| HMMM     | 矩形領域をコピーします（バイト粒度）               |
| HMMC     | CPU から VRAM にブロック転送（バイト粒度）         |
| LMMV     | 矩形領域を色値で塗りつぶします（ピクセル粒度）     |
| LMMM     | 矩形領域をコピーします（ピクセル粒度）             |
| LMCM     | VRAM から CPU にブロック転送                       |
| LMMC     | CPU から VRAM にブロック転送（ピクセル粒度）       |
| YMMM     | Y 軸のみで矩形領域をコピーします（バイト粒度）     |
| LINE     | 直線を描画します                                   |
| PSET     | 1 ピクセルを書き込みます                           |
| POINT    | 1 ピクセルの値を読み出します（結果は S#7）         |
| SRCH     | スキャンライン上で色を検索します（結果は S#8/S#9） |
| ABRT     | アクティブなコマンドを中断します                   |

エンジンは各コマンドをすぐに実行します。コマンドにかかる時間を表すため、`V9938.tick(n)` がおおよそのサイクル予算（`_cmd_remaining`）を減らします。コマンドの実行中、マシンは命令のたびに、その命令の T ステート数で `tick(n)` を呼びます。コマンドを実行していない間は、T ステート数を `_line_cycle` に足すだけです。`_line_cycle` は水平帰線のタイマーで、`tick(n)` もこれを進めます（[水平ライン割り込み](#水平ライン割り込み) を参照）。S#2 bit 0（CE）を待つソフトウェアには、予算が尽きた後に CE がクリアされて見えます。

ブロックコマンドごとに、1 要素あたりのコスト（T ステート）があります。

| 定数                    | コスト | 単位                |
| ----------------------- | ------ | ------------------- |
| `_HMMV_CYCLES_PER_BYTE` | 10     | VRAM 1 バイトあたり |
| `_HMMM_CYCLES_PER_BYTE` | 21     | VRAM 1 バイトあたり |
| `_YMMM_CYCLES_PER_BYTE` | 18     | VRAM 1 バイトあたり |
| `_LMMV_CYCLES_PER_DOT`  | 21     | 1 ドットあたり      |
| `_LMMM_CYCLES_PER_DOT`  | 30     | 1 ドットあたり      |

これらの値は、実機の SONY HB-F1XD で表示とスプライトをオンにした状態（最も遅い条件）で、1 画面分のコマンドの時間を測って求めました。表示やスプライトの VRAM アクセスが少ないときに速くなる分は、エミュレータではモデル化していません。

R#44（COL）への書き込みが、HMMC/LMMC の転送ラッチ（`_cmd_transfer`）をセットします。転送の最初のピクセルまたはバイトは、まだ使っていない COL の書き込みから取ります。開始時に自動では読み込みません。openMSX も同じ動作をします。

`v9938.py` の `_apply_log()` が、論理演算（IMP、AND、OR、XOR、NOT）をピクセルごとに適用します。

### 既知の制限

- コマンドのタイミングは近似です。エンジンは要素ごとの実測コスト（上記）を使います。V9938 データブックのコマンドごとのサイクル数は使いません。
- エンジンは結果を開始時にまとめて VRAM に書き、少しずつは書きません。コマンドの実行中とされる間に VRAM を読むソフトウェアには、CE がクリアされる前に完了後の結果が見えることがあります。
- エミュレータは、CPU が 1 フレーム動いた後にそのフレームを描画します。CPU が 1 フレーム分動き、VDP コマンドはすぐに VRAM に書き、最後に 1 回の描画でフレーム全体を描きます。V9938 も TMS9918A も、この描画は垂直帰線期間の始まりに行います。1 フレームの中でラスタに合わせて VRAM を書き換える処理（ビームに合わせた転送や、ダブルバッファのタイトル画面）は正しく再現しません。上のバンド処理で再現できるのはフレーム途中の*レジスタ*の変更だけで、VRAM の中身の変更は再現できません。

---

## オーディオ

音声を作る音源は 5 つあります：PSG（`msx/psg.py`）、SCC（`msx/scc.py`）、FM-PAC の OPLL（`msx/opll.py`）、Majutsushi DAC、PPI のキークリックです。どれも 44,100 Hz の符号付き 16 ビットモノラル PCM を作ります。1 フレームあたり、NTSC のマシンでは 735 サンプル、PAL のマシンでは 882 サンプルになります。`Machine.run_frame()` の最後に、`AudioMixer`（`msx/audio_mixer.py`）がマシンにあるチップの出力を足し、16 ビットに収めます。フロントエンドの有無に関係なく行うので、ヘッドレスの実行でもチップの状態は同じように進みます。SDL2 フロントエンドは `step_frame` の `FrameOutput` でそのバッファを受け取り（[フレームの実行](#フレームの実行) を参照）、出力ローパスフィルタを通して SDL のキューに積みます。マシンが持つのは最新の 1 フレーム分だけなので、受け取られない音声がたまることはありません。

Majutsushi DAC とキークリック（PPI ポートC bit 7）は、書き込みで動く DAC チャンネル 1 つ（`msx/dac.py` の `EventDac`）を共有します。チャンネルを使う側は、書き込みを符号付き 16 ビットのレベルに変え、書き込んだサイクルと一緒に渡します。レベルが変わらない書き込みは記録しません。`generate_samples` は各変化をサンプル位置 `(cycle - frame_start) * n // (frame_end - frame_start)` に置き、次の変化までレベルを保ちます。変化がなくレベルが 0 のフレームでは `None` を返し、ミキサーはそのチャンネルを混ぜません。

### サブフレーム・ソフトウェア PCM

`PSG.generate_samples(n, frame_start, frame_end)` は、音量レジスタへの速い書き込みで鳴らすソフトウェア PCM を再現します。

1. `write_port` は各レジスタ書き込みを `(cycle, reg, value)` として記録します。サイクルは、マシンの clock 記録（`msx/clock.py`）から読む。その値が `machine.cycle_count` です。速いフレームループは、各スキャンラインの T ステートをラインの終わりで `cycle_count` に足します。そのため、書き込みの位置の分解能は 1 スキャンライン（228 T ステート、約 2.8 サンプル）になります。
2. `generate_samples` は、フレームの最初の書き込みの時点のレジスタとジェネレータの状態に戻ります。
3. バッファを、連続した区間の並びとして描き直します。各書き込みは、サンプル位置 `clamp((cycle - frame_start) * n // (frame_end - frame_start), 0, n)` から新しい区間を始めます。
4. トーン、ノイズ、エンベロープの状態は区間をまたいで引き継ぎます。

書き込みのないフレームは、以前と同じ 1 つのスナップショットで描く速い経路を通ります。出力はバイト単位で以前と同じなので、ふつうの音楽に余分なコストはかかりません。

### トーン積分（period-0 PCM キャリア）

ソフトウェア PCM は、トーンジェネレータを周期 0 で有効にしたまま使います。トーンは約 223 kHz の超音波のキャリアになり、実機のアナログ出力では約 50% のデューティの平均にならされます。PCM の波形を運ぶのは、音量レジスタへの書き込みのほうになります。仮に `tone_out` を 44.1 kHz で点サンプリングすると、キャリアが 0 と最大の間を行き来する形にエイリアスし、サンプルの約半分が 0 になってしまいます。

そこで、各チャンネルのトーンを**出力サンプルごとに積分する**です。内側のループが、矩形波が High だった PSG の tick 数（`hi0/hi1/hi2`）を数え、ミキサがチャンネルの振幅を `hi / ticks` 倍します。聞こえる音程のトーン（1 サンプルの中で切り替わらない）では `hi` は `ticks` か `0` なので、結果は点サンプリングとビット単位で同じになります。変わるのは超音波のキャリアだけで、帯域制限されたデューティの平均になります。

### 出力ローパスフィルタ

`frontend/audio_filter.py`（`BiquadLowPass`）は、状態を持つ 2 極バターワースのローパスフィルタです（8 kHz、RBJ の係数、Direct Form I）。フロントエンドが、マシンから受け取った毎フレームの PCM にこのフィルタをかけます。実機 MSX の音声出力の RC フィルタを模しています。浮動小数点を使うのでコアには置きません。こうすることで、コアの音声出力はどのホストでも同じになります。フロントエンドが 1 つのインスタンスを持ち、`x1/x2/y1/y2` の状態をフレームをまたいで保ちます。リセットのホットキーで状態を消します。

フィルタは、点サンプリングの合成がナイキスト周波数の近くに残す高域のイメージングとエイリアシングを取り除きます。コストは 1 フレームの音声処理の約 1% です。実際の PSG の音楽のエネルギーはカットオフよりずっと低いので、聞こえる帯域と全体の音量はほとんど変わりません。

## フレームの速度調整（フロントエンド）

コアは自分の時計を持たず、`step_frame` はホストが出せる速さで 1 フレームを実行します。SDL2 フロントエンドは、`FrameTimer`（`frontend/frame_timer.py`）でフレームを実時間に合わせます。ヘッドレスの実行（`--count-frame`、`--benchmark`）はタイマーを使いません。

目標のレートは、マシンの名目の `VideoStandard.target_fps`（NTSC は 60、PAL は 50）に `--speed` を掛けた値です。フレームの間隔は `1 / (fps * speed)` で、レートが 0 以下（たとえば `--speed 0`）のときは 60 Hz の間隔を使います。調整には実機の約 59.92 Hz のフィールドレートではなく名目のレートを使い、音声も同じ名目のレートで作ります（NTSC は 1 フレーム 735 サンプル、PAL は 882 サンプル）。そのため、音声のキューは、デバイスが消費するのと同じ速さで埋まります。

`tick()` は、現在のフレームの期限まで待ちます。期限の 2 ミリ秒前までは `sleep` で待ち、残りはビジーウェイトで待ちます（[フレームタイマーのスピンループ](#フレームタイマーのスピンループ) を参照）です。次の期限は、前の期限に 1 間隔を足した時刻です。`sleep` が遅れて戻ると次の待ちが短くなるので、平均のレートは目標どおりに保たれます。その次の期限もすでに過ぎていれば、そのフレームは 1 間隔をまるごと超えたことになります。このときは、期限を現在時刻に 1 間隔を足した時刻からやり直します。遅れを取り戻すために、フレームを待たずに続けて実行することはありません。`sleep` が毎回 3 ミリ秒遅れても、60 fps での 600 回の `tick()` は、1 間隔の誤差の範囲で 10 秒になります（`tests/test_frame_timer.py`）。

`tick()` は、前の `tick()` からの実際の時間を返します。`--frame-skip auto`（デフォルト）では、フロントエンドがこの時間を間隔と比べます。1 フレームが間隔の `_FRAME_OVERRUN_RATIO`（1.05）倍を超えたら、スキップのカウンタを 1 増やします（上限は `_MAX_FRAME_SKIP`、4）。そうでなければ 1 減らします。カウンタが 0 より大きい間は、次のフレームを `render_video=False` で実行します。コアはピクセルの描画を省きますが、CPU、VDP のステータス、音声は通常どおり進めます。フロントエンドは前の画面をもう一度表示します。

早送り中（F7、Shift+F7）は、フロントエンドは `tick()` の代わりに `tick_unpaced()` を呼びます。`tick_unpaced()` は fps の計測のためにフレームを数え、期限を現在時刻から設定し直しますが、待ちはしません。フロントエンドは、前に表示したフレームから 1 表示間隔以上たったときだけフレームを描画し、音声はキューに積みません。そのため、早送りの後の最初の `tick()` は、1 間隔をまるごと待つ。

`fps_measured` は、直前の計測区間（1 秒以上）の `tick()` の回数を、その区間の長さで割った値です。ウィンドウのタイトルに表示します。

## メモリとスロットシステム

### MSX1 スロットレイアウト

`Memory`（`msx/memory.py`）は、フラットな 64 KB のアドレス空間を 4 ページ × 4 スロットで振り分けます。スロット選択レジスタ（ポート 0xA8、PPI 経由）が、16 KB の各ページのスロットを選びます。レジスタの bit `[2N+1 : 2N]` がページ N のスロットを選びます。

MSX1 のデフォルトの配置：

| スロット | 内容 |
| --- | --- |
| 0 | BIOS ROM（読み取り専用）。一部の機種はページ 3 に 16 KB RAM を持ちます（例：`hb_10`） |
| 1 | マッパー経由のカートリッジ ROM |
| 2 | 第 2 カートリッジまたはオープンバス（読み出しは 0xFF） |
| 3 | ページ 2〜3（0x8000〜0xFFFF）の 32 KB RAM。サイズは機種ごとに設定します |

### MSX2 サブスロット

MSX2 ではスロット 3 が拡張されます（`sub_slot_enabled = True`）。0xFFFF を読むと `~sub_slot_reg` が返り、ソフトウェアはこれでサブスロットがあることを知ります。サブスロットレジスタ（`sub_slot_reg`）は、拡張したプライマリスロットの中で、各ページを 4 つのセカンダリスロットのどれにするかを選びます。

MSX2 のデフォルトの配置：

| サブスロット | 内容                                        |
| ------------ | ------------------------------------------- |
| 3-0          | C-BIOS サブ ROM（読み取り専用）             |
| 3-1          | 未使用                                      |
| 3-2          | RAM マッパー経由の RAM（デフォルト 128 KB） |
| 3-3          | 未使用                                      |

### RAM マッパー

`RamMapper`（`msx/ram_mapper.py`）は RAM を 16 KB のセグメントに分けます。サイズは 16 KB の正の倍数で、マシンまたは拡張の YAML の `size_kb` で決めます。デフォルトは 128 KB（8 セグメント）です。`hb_f1xd_256k` は 256 KB、512 KB の拡張は 512 KB を使います。ポート 0xFC〜0xFF の 4 つのセグメントレジスタが、CPU の各ページに見えるセグメントを独立に選びます。

| ポート | ページ | アドレス範囲  |
| ------ | ------ | ------------- |
| 0xFC   | 0      | 0x0000–0x3FFF |
| 0xFD   | 1      | 0x4000–0x7FFF |
| 0xFE   | 2      | 0x8000–0xBFFF |
| 0xFF   | 3      | 0xC000–0xFFFF |

レジスタにセグメント番号を書くと、そのページの割り当てがすぐに切り替わります。マッパーは番号を `bank_mask` でマスクします。`bank_mask` はセグメント数を覆う最小の 2 のべき乗から 1 を引いた値です（128 KB なら 7）です。読み出すと、セグメント番号の使わない上位ビットを 1 にした値が返ります。セグメント数が 2 のべき乗でないとき、マスクした番号がセグメント数以上になることがあります。その番号は openMSX と同じく、セグメント `番号 & (セグメント数 - 1)` を選びます（48 KB なら、番号 3 はセグメント 2 を選ぶ）です。

---

## フロッピーディスク（FDC）

フロッピーまわりは汎用の層（`msx/fdc/`）になっています。新しいコントローラチップや接続方式は、`Memory` を変えずにこの層につなげる。今は、WD2793 と Sony/Philips 方式、TC8566AF とその専用の方式という、2 つのコントローラと 2 つの接続方式があります。

| 層 | ファイル | 役割 |
| --- | --- | --- |
| ディスクイメージ | `disk_image.py`（`DskDiskImage`） | host がファイルから読んだバイト列で作った `*.dsk` のセクタイメージを持ちます。ジオメトリは FAT12 の BPB（BIOS Parameter Block：セクタあたりのバイト数、総セクタ数、トラックあたりのセクタ数、ヘッド数）から求めます。書き込みはバッファにためてイメージを dirty にします。`host/media.py` が、終了時、ステートセーブの前、ディスクをドライブから外すときに、dirty のイメージをファイルへ書き戻します |
| ドライブ | `disk_drive.py`（`DiskDrive`） | 物理ドライブ 1 台。マウント中のイメージを持ち、ヘッドの位置を追い、セクタを読み書きします |
| コントローラ | `wd2793.py`（`WD2793`）、`tc8566af.py`（`TC8566AF`） | FDC チップ。`WD2793`：Type I–IV コマンドデコード、ステータスレジスタ、データ/トラック/セクタレジスタ、`abort()`。`TC8566AF`（uPD765 系）：コマンド/実行/結果フェーズモデル、Main Status Register、非 DMA 転送、直接アドレス指定できるトラック/セクタレジスタは持ちません |
| インターフェース | `interface.py` | コントローラとメモリバスの接続方式。どちらも DISK ROM を 0x4000〜0x7FFF にマップします。`SonyPhilipsInterface`（openMSX の PhilipsFDC にあたる）は WD2793 のレジスタを 0x7FF8〜0x7FFF に置き、ディスク交換ビットを使います。`TC8566AFInterface` は 2 つの制御レジスタ、Main Status Register、Data Register を 0x7FF8〜0x7FFB に置きます。実行中のマウントと取り出しを行う `swap()` は両方で共通 |

マシンのコントローラと接続方式は、マシン YAML の `fdc:` ブロックで決まります。同梱のフロッピー付きの 2 機種も、このデータが違うだけです。

- Sony HB-F1XD（`hb_f1xd`）は、WD2793 と DISK ROM をスロット 3 のサブスロット 0 に置き、サブスロット 3 に 64 KB のフラットな RAM を置きます。
- Panasonic FS-A1F（`fs_a1f`）は TC8566AF を使い、4 つのセカンダリスロットに役割を分けます。サブスロット 0 が RAM、1 が SUB ROM、2 が FDC になります。

`--fdd1`/`--fdd2` でドライブ A/B にイメージをマウントします。`--fdd1` には複数のイメージを指定できます。実行中にディスクを交換する方法は 3 つあり、どれも `host/media.py` の `swap_disk()` を通ります。`swap_disk()` は、外す側のイメージが dirty ならまずファイルへ書き戻し、それから `Machine.mount_disk()` 経由でフロッピーインターフェースの `swap()` を呼びます。成功すると、3 つの方法が共有する `DiskFiles` の表に新しいファイルを記録します。

- SDL2 フロントエンドの F6/Shift+F6 ホットキー。`--fdd1` の一覧を順に切り替えるか、ドライブ A のディスクを取り出し/再挿入します。
- デバッガの `fdd1`/`fdd2` コマンド。
- ソケット RPC サーバの `fdd.swap` メソッド。

この実装は Disk BASIC を起動でき、`CALL FORMAT` に対応し、ファイルを読み書きします。変更したセクタは、host が終了時、ステートセーブの前、ディスクをドライブから外すときに `.dsk` ファイルへ書き戻します。フロッピーインターフェースのないマシンでは `machine.fdc` は `None` になります。

## マシンと拡張の YAML スキーマ

`host/machine_config.py` は、次の 3 種類の YAML を読みます：デバイス定義（`config/devices/*.yaml`）、マシン仕様（`config/machines/*.yaml`）、拡張オーバーレイ（`config/extensions/*.yaml`）。3 種類とも、ローダが手で検証する `TypedDict` の形（`DeviceEntryYaml`、`MachineEntryYaml`、`ExtensionOverlayYaml` とその入れ子の形）で、スキーマのライブラリは使いません。名前の閉じた集合（マッパー、FDC のコントローラと接続方式、拡張・サブスロット・I/O のデバイス）は `msx/machine_config.py` で定義しています。YAML のローダはそれを import し、コアは MachineConfig の文書を解析するときに同じ集合でもう一度調べる。

この節の各フィールドは、ローダの中の明示的な `.get()` 呼び出しに対応します。ローダが読まないキーは、YAML のコメントに何と書いてあっても説明のためだけにあります。各プライマリスロットに何を入れられるかの利用者向けのまとめは、[README_extension_ja.md の「スロットモデル」節](README_extension_ja.md#スロットモデル)を参照してください。この節では、その結果を生む YAML の書き方を説明します。

### デバイス YAML（`config/devices/*.yaml`）

| フィールド | 型 | 必須 | 読み出し箇所 |
| --- | --- | --- | --- |
| `id` | string | はい | ファイル名（拡張子を除く）と一致しなければ、`load_device_registry` が `MachineLoadError` を出します |
| `type` | string | はい | キーがあるかだけを調べ、値は調べません（慣例で `io_device`） |
| `implemented` | bool | いいえ（デフォルト `true`） | `false` なら、`builtin_devices` の解決時に stderr に警告を出して飛ばします（起動は失敗しない）。そのため、エミュレーションより先にデバイス定義だけを入れられます |
| `io_ports` | int のリスト | いいえ | 先頭と末尾の要素が、そのデバイスの `(start, end)` の I/O 範囲になります。キーがなければ `_DEFAULT_IO_PORTS` を使います |

それ以外のキー（`name`、`chip`、`controls`、`vram_kb`、`segment_size_kb`、`keyboard_type` など）は `_DeviceDef.raw` に入りますが、汎用のコードはこれらを*読まない*です。コード全体で `dev.raw.get(...)` で読むキーは、上の `io_ports`、下の `keyboard_type` と `control_read`（`ppi8255` だけ）、`form`（`printer_port` だけ）の 4 つだけです。

`vdp_v9938.yaml` の `vram_kb: 128` はどこからも読まれません。V9938 の VRAM サイズは、`msx/vdp/v9938.py` にハードコードした定数 `_VRAM_SIZE = 131072` で決まります。このキーと、マシン側でそれを写した `overrides: {vram_kb: 128}`（後述）は、意図を書いているだけで、動作中のエミュレータには何の効果もありません。

```yaml
id: vdp_v9938
type: io_device
implemented: true
name: Yamaha V9938 Video Display Processor (MSX2)
chip: v9938
io_ports: [0x98, 0x99, 0x9A, 0x9B]
controls: video_display_processor
vram_kb: 128 # 説明のためだけ。上の注記を参照
```

### マシン YAML（`config/machines/*.yaml`）

| フィールド | 型 | デフォルト | 備考 |
| --- | --- | --- | --- |
| `schema_version` | int | （なし） | 必ず `1` |
| `id` | string | （なし） | ファイル名（拡張子を除く）と一致しなければなりません |
| `generation` | string | （なし） | `msx1` または `msx2`。それ以外はエラー |
| `name` | string | `id` | 表示名のみ |
| `rom_base` | string | `roms/cbios` | すべての `rom:` ブロックの `file` の基準ディレクトリ（プロジェクトルートからの相対） |
| `video_standard` | string | `ntsc` | `ntsc` は 1 フレーム 59,736 T ステート / 262 ライン、`pal` は 71,364 / 313（`cbios_msx1_eu` と `cbios_msx2_eu` が使う）。SDL2 フロントエンドの実時間の目標（NTSC 60 fps / PAL 50 fps）もこれで決まります |
| `cpu.m1_wait_states` | int | `0` | Z80 の M1 サイクルごとに足す T ステート数。上の [CPU エミュレーション](#cpu-エミュレーション)を参照 |
| `slots.primary` | マッピング | （なし） | キーは `0`〜`3`。ローダが読むのは `0` と `3` だけ（後述） |
| `builtin_devices` | リスト | `[]` | 後述 |
| `io_device` | マッピング | （なし） | マシン全体の I/O デバイス（任意）。拡張オーバーレイの `io_device`（後述）と同じ形 |

#### スロット 0（固定）

```yaml
0:
  content:
    - rom: { file: cbios_main_msx2.rom, size_kb: 32, pages: [0, 1], sha1: null }
    - rom: { file: cbios_logo_msx2.rom, size_kb: 16, pages: [2], sha1: null }
```

`content` は `{rom: {...}}` エントリのリストです。`_parse_slot0` はこの中から 2 つのエントリを探します。

- `pages` に `0` か `1` を含むエントリ：メイン BIOS ROM。必須で、なければ `MachineLoadError` になります。
- `pages` に `2` を含むエントリ：0x8000〜0xBFFF のロゴ ROM（任意）。

`rom:` ブロックの `sha1` は、想定するダンプを記録するだけです。値が `null` でもハッシュでも、ローダは読み込んだファイルと照合しません。コアが調べるのは別の SHA1 です。これは、host が読み込んだバイト列から計算して MachineConfig の文書に書いた値です（[load_machine_config() と build_machine()](#load_machine_config-と-build_machine) を参照）です。

`content` には `{ram: {size_kb, pages}}` のエントリを 1 つまで入れられます。ROM と並べてスロット 0 に置く、フラットな RAM を表します。たとえば Sony HB-10 のページ 3 にある 16 KB SRAM がこれにあたります。これは電池のないワーク RAM で、電源を切ると消えます。

```yaml
- ram: { size_kb: 16, pages: [3] }
```

`size_kb` は `16`、`pages` はちょうど 1 ページでなければならず、違えば `MachineLoadError` になります。2 つ目の `ram` エントリも同じくエラーになります。

#### スロット 1・2：マシン YAML からは読まれない

どのマシン YAML も、`slots.primary` に `1: {type: cartridge}` と `2: {type: cartridge}` を書いています。しかし `load_machine_spec` が読むのは `primary[0]` と `primary[3]` だけで、`primary[1]` も `primary[2]` も読みません。この 2 つのエントリは意図を示すだけです。

スロット 1 と 2 の中身は、すべて `load_machine_config()` の引数から決まり、MachineConfig の文書の `cartridges` と `extensions` に書かれます。スロット 1 に入るのは、カートリッジとマッパー（位置引数か `--slot1` と、`--mapper1`）か、オーバーレイ（`--extension1`）です。スロット 2 に入るのは、カートリッジとマッパー（`--slot2`/`--mapper2`）か、オーバーレイ（`--extension2`）です。これらの値は CLI が決め、YAML の `slots.primary.1`/`.2` の中身は影響しません。

#### スロット 3：MSX1

```yaml
3:
  size_kb: 32 # 任意、デフォルトは 32
```

`_parse_slot3_msx1` が読むのは `size_kb`（デフォルト `32`）だけです。0xFFFF で終わるフラットな RAM で、それ以上の構造はありません。

- 32 KB なら 0x8000〜0xFFFF。
- 64 KB（`hb_10_64k`）なら 4 ページすべて。
- `size_kb: 0`（`hb_10`）ならスロット 3 はオープンバス。

`3:` を省略すると、オープンバスではなくデフォルトの 32 KB になります。

#### スロット 3：MSX2

```yaml
3:
  expanded: true
  secondary:
    0:
      content:
        - rom: { file: cbios_sub.rom, size_kb: 32, pages: [0, 1], sha1: null }
    2:
      type: ram
      mapper: standard
      size_kb: 128
```

`expanded: true` で、スロット 3 は 4 つのセカンダリスロット（`secondary.0`〜`.3`、キーの書き方は `slots.primary` と同じ）になります。`_parse_slot3_msx2` はそれぞれを独立に調べ、次のものを見つけます。

- `content` を持ち、`rom.pages` に `0` か `1` を含む最初のサブスロット → SUB ROM。このサブスロットは任意です。ローダはそのサブスロット番号を記録します（0 に固定ではない）。
- `mapper: standard` を持つ最初のサブスロット → `RamMapper`。サイズは `size_kb`（デフォルト `128`）で決まります。16 の正の倍数でなければなりません。`_check_ram_mapper_size_kb` がこれを調べ、違えば `MachineLoadError` を出します。
- `type: ram` を持ち、`mapper: standard` を持たない最初のサブスロット → フラットな（マッパーでない）RAMです。サイズは `size_kb`（デフォルト `64`）で決まります。`hb_f1xd` は実機の固定 64 KB にこれを使います。
- `fdc:` ブロックを持つ最初のサブスロット → [フロッピーディスク（FDC）](#フロッピーディスクfdc)を参照

`mapper: standard` のサブスロットと `type: ram` のサブスロットは、`secondary` 全体でどちらか一方しか置けません。`Memory` は RAM マッパーとフラットな RAM を同時に持てないからです。両方あると `MachineLoadError` になります。フラットな RAM を SUB ROM や FDC と同じサブスロット番号に置くこともエラーになります。`Memory` の書き込みの経路は、どちらかへの誤った書き込みがフラットな RAM に入るのを止めないからです。どのサブスロット番号も、役割に関係なく `0`〜`3` の範囲かを調べます（`_check_subslot_index`）。

`fdc:` ブロックのフィールド（スロット 3 のサブスロット内でのみ意味を持つ）：

| フィールド | 型 | デフォルト | 備考 |
| --- | --- | --- | --- |
| `rom` | `rom:` ブロック | （なし） | 必須（DISK ROM） |
| `controller` | string | `wd2793` | `wd2793` または `tc8566af` |
| `connection_style` | string | `sony` | `sony` または `tc8566af` |
| `drives` | int | `1` | `1`〜`4`。YAML のローダが調べるのは正の値かどうかだけで、5 台以上の文書はコアが拒否します |

`(controller, connection_style)` の組は、`(wd2793, sony)` か `(tc8566af, tc8566af)` でなければなりません。それぞれの値が単独では正しくても、ほかの組み合わせはエラーになります（例：`wd2793` + `tc8566af`）。

#### `builtin_devices`

```yaml
builtin_devices:
  - ref: ppi8255
    overrides: { keyboard_type: jp }
  - ref: vdp_v9938
    overrides: { vram_kb: 128 } # 受理されるが読まれない（上記デバイス YAML の節を参照）
  - ref: psg_ay8910
  - ref: rtc_rp5c01
  - ref: memory_mapper_standard
```

各エントリの `ref` は、デバイスレジストリ（`load_device_registry` の出力）で解決できなければならず、できなければ `MachineLoadError` になります。`overrides` は自由な形のマッピングですが、`_parse_builtin_devices` が読むキーは 2 つだけで、それも `ref: ppi8255` のときに限ります。

- `keyboard_type`：`int` か `jp`。
- `control_read`：`control`、`open_bus`、`bus_latch` のどれかです。ポート 0xAB を読んだときの値を決めます。

古いキー `control_readback` は、デバイスファイルにあっても `overrides` にあっても `MachineLoadError` になります。ほかの `overrides` のキーは、どの `ref` でも受け付けて無視します。

`ref: vdp_v9938`/`rtc_rp5c01`/`ppi8255` があると、`MachineSpec` の `has_v9938`/`has_rtc`/キーボード配列の各フラグが設定されます。ほかの `ref` は、自分の `io_ports` の範囲を足すだけです。

#### マシンレベルの `io_device`

```yaml
io_device:
  device: kanji_rom
  rom: { file: some_kanji_font.rom, size_kb: 256, sha1: null }
```

マシンレベルの `io_device` は、拡張オーバーレイの `io_device`（後述）と同じ形をとります。マシン自身が持つ、スロットに依存しない I/O デバイスを表します。ローダは同じ `IO_DEVICES`（`kanji_rom` だけ）で検証します。今のところ、これを書いている同梱のマシン YAML はありません。`kanji_rom` デバイスを持つのは、今は `hbi_j1` と `msxdos2_512k_kanjirom` の拡張（`--extension1`/`--extension2`）だけで、どちらも拡張オーバーレイの `io_device`（後述）を使います。

### 拡張オーバーレイ YAML（`config/extensions/*.yaml`）

| フィールド | 型 | デフォルト | 備考 |
| --- | --- | --- | --- |
| `schema_version` | int | （なし） | `1` でなければなりません |
| `id` | string | （なし） | 説明のためだけ。`load_extension_overlay` はこれを読みません。ローダはファイル名（`config/extensions/<--extension1/--extension2 の値>.yaml`）だけでファイルを探します。マシンやデバイスの YAML の `id` とは違います |
| `slot` | int | `2` | `1` か `2` でなければなりません。説明のためだけで、YAML の作者が想定したスロットを記録します。実際のスロットは CLI のフラグ（`--extension1` か `--extension2`）が決めます |
| `shape` | string | `flat` | `flat` または `expanded` |
| `rom_base` | string | `""`（プロジェクトルート） | 以下の各 `rom:` ブロックの基準ディレクトリ |

#### フラット形状（`shape: flat`、または省略時）

```yaml
device: fmpac
rom: { file: fmpac.rom, size_kb: 64 }
sram: { size_kb: 8, save_file: saves/sram/fmpac.sram }
```

`device` は `fmpac` か `scc_i_cart`（`EXTENSION_DEVICES`）でなければなりません。同じ `device` というキーでも、後述する拡張形状でサブスロットに置くデバイスとは、名前空間が別です。`fmpac` には `rom:` ブロックが要ります。`scc_i_cart` には要りません。RAM は空で始まり、ローダはファイルを読まないからです。フラットなオーバーレイのデバイスは、対象のプライマリスロット（スロット 1 か 2）のマッパーを常にまるごと置き換えます。

#### 拡張形状（`shape: expanded`）

```yaml
shape: expanded
slot: 2
rom_base: roms/hbi_j1
subslots:
  0:
    device: halnote
    rom: { file: hbi-j1_msx-je.rom, size_kb: 1024, sha1: null }
    sram: { size_kb: 16, save_file: saves/sram/hbi-j1_msx-je.sram }
  1:
    device: flat_rom
    rom: { file: hbi-j1_kanjibasic.rom, size_kb: 32, sha1: null }
io_device:
  device: kanji_rom
  rom: { file: hbi-j1_kanjifont.rom, size_kb: 256, sha1: null }
```

`subslots` は必須で、1 つ以上のエントリを持つマッピングです。キーは `0`〜`3` で、MSX2 のスロット 3 のサブスロットと同じ範囲を同じ `_check_subslot_index` で調べます。各エントリの `device` は、`halnote`、`flat_rom`、`ram_mapper`、`ascii8`、`ascii16`（`SUBSLOT_DEVICES`）のどれかでなければなりません。

| `device` | 必須項目 | 構築されるもの |
| --- | --- | --- |
| `halnote` | `rom:` | `HalnoteMapper`（1 MB ROM、`sram:` は任意） |
| `flat_rom` | `rom:` | `FixedPageMapper(base=0x4000)`。0x4000〜0xBFFF にだけ見えます |
| `ascii8` | `rom:` | `Ascii8Mapper` |
| `ascii16` | `rom:` | `Ascii16Mapper` |
| `ram_mapper` | `size_kb:`（エントリに直接書きます。`rom:` はない） | `RamMapper`。`size_kb` は 16 の正の倍数でなければなりません |

`ram_mapper` のサブスロットは、1 つのオーバーレイに 1 つまでです。2 つあると、標準のメモリマッパーの I/O ポート（0xFC〜0xFF）を 2 回登録してしまいます。拡張形状のオーバーレイを MSX1 のマシンに使うと、ローダは必ず `MachineLoadError` を出します。メモリマッパーのサブスロットにあたる MSX1 標準のハードウェアはなく、今の拡張形状のオーバーレイはどれも MSX2 時代のハードウェアを表しているからです。`ram_mapper` のサブスロットは、スロット 3 に自前の RAM マッパーを持つマシン（`has_ram_mapper=True`）とも衝突します。2 つのマッパーが同じポートを使うことになるからです。

任意のトップレベルの `io_device` は、*3 つめの*デバイスの名前空間 `IO_DEVICES = {kanji_rom}` で検証します。ほかの 2 つとは重なりません。`subslots` で `device: kanji_rom` は使えず、`io_device` で `device: halnote`（ほかのサブスロットのデバイスも）は使えません。マシンの `io_device` と拡張オーバーレイの `io_device` がどちらも `kanji_rom` になると、`build_machine` はエラーを出します。ポート 0xD8〜0xDB は、2 つの漢字 ROM デバイスを同時に扱えないからです。

#### `rom:` と `sram:` ブロック

どちらの形も、同じ入れ子のブロックの書き方を使います。`_parse_rom_entry` がこれを解析します。ローダは、スロット 0 の ROM、MSX2 の SUB ROM、FDC の DISK ROM にもこの関数を使います。この関数は、ローダで ROM を扱うすべてのフィールドが共有するただ 1 つの実装です。

| `rom:` のフィールド | 型 | 必須 | 備考 |
| --- | --- | --- | --- |
| `file` | string | はい | `rom_base` を基準に探します |
| `size_kb` | int | いいえ（デフォルト `0`） | 説明のためだけ。実際のファイルサイズとは照合しません |
| `pages` | int のリスト | いいえ（デフォルト `[]`） | スロット 0 と SUB ROM でだけ意味があり、どのエントリがメイン/ロゴ/SUB ROM かを決めます |
| `sha1` | string | いいえ | 説明のためだけ。ローダはファイルと照合しません（[スロット 0](#スロット-0固定) を参照） |

| `sram:` のフィールド | 型 | 備考 |
| --- | --- | --- |
| `size_kb` | int | 説明のためだけで、ローダは無視します。各デバイスの SRAM サイズは Python 側の定数（`HALNOTE_SRAM_SIZE`、`FMPAC_SRAM_SIZE` など）で決まります |
| `save_file` | string | SRAM の保存先のパス（プロジェクトルートからの相対）。省略すると保存しません |

## マシン YAML ローダ

### 2 パス解決

`host/machine_config.py` は、ハードウェアの構成を 2 段階で決めます。

1. **デバイスレジストリの段階。** `load_device_registry(config_dir)` が、`config/devices/` のすべての `*.yaml` を、デバイスの `id` をキーにした辞書に読み込みます。各ファイルは 1 つのハードウェア（VDP の種類、PSG、PPI、RTC、RAM マッパー）を、マシンとは関係なく単独で書きます。

2. **マシン仕様の段階。** `load_machine_spec(machine_id, config_dir, device_registry, project_root)` が `config/machines/<machine_id>.yaml` を読み、必須のフィールドを検証します。`builtin_devices` の各 `ref` をレジストリで解決し、`MachineSpec` データクラスを返します。解決できない `ref`、知らない `schema_version`、必須の ROM エントリが欠けていれば、ファイルとフィールドを示した `MachineLoadError` を出します。

### load_machine_config() と build_machine()

マシンの組み立ては host とコアで分担します。`host/machine_config.py` の `load_machine_config(spec, cartridge1, mapper1, ...)` が、マシンに必要なファイルをすべて読み、`MachineConfig` の値（`msx/boundary.py`）にまとめます。解決済みのマシン、カートリッジとそのマッパー、拡張を、1 つの正規化した JSON の文書（`msx/machine_config.py`、`msx/machine_config.schema.json`）に書きます。`auto` のマッパーは、先に ROM データベースから決めます。文書の中では、ROM はすべて `{name, size, sha1, pages}` の参照で表します。ROM イメージは SHA1 をキーにして文書と一緒に渡します。実行時の入力（保存済みの SRAM イメージ、ディスクイメージ、RTC の開始時刻）も文書と一緒に渡します。

パーサは、`ram_size_kb` と `flat_ram_size_kb` を 0〜64、`ram_mapper_size_kb` とサブスロットのデバイスの `size_kb` を 0 または 16〜4096 に制限します。`device_io_ports` のデバイス名と、各範囲の最初のポートが最後のポートより大きくないことも確かめます。

`msx/machine_loader.py` の `build_machine(config)` は文書を厳密に解析し、参照された各イメージのサイズと SHA1 を確かめます。そのうえで、そのバイト列だけから `Machine` を作ります。ファイルを開かず、データベースも引かないので、同じ設定からは必ず同じマシンができます。`Machine` は文書の SHA-256 を持ち、スナップショットはそれを記録します（[MachineConfig の文書](#machineconfig-の文書) を参照）です。`spec.generation` で具体的な型が決まります。

- `"msx1"`：`VDP`（TMS9918A）、フラットなスロット 3 の RAM。RTC と RAM マッパーはありません
- `"msx2"`：`V9938`、サブ ROM と `RamMapper` を持つ拡張スロット 3、`RTC`

読み込み時、ローダは `implemented: false` のデバイス YAML を飛ばし、stderr に警告を出します。マシンのほかの部分はふつうに動くので、エミュレーションができる前にデバイス定義だけをコミットしても、起動は失敗しません。今 `config/devices/` にあるデバイスはすべて `implemented: true` です。

---

## コアの境界と移植ガイド

エミュレータのコア（`msx/`）は、コアを組み込む側から切り離してあるので、別のフロントエンドから動かせます。また、コアだけを先に別の言語（Zig、C++、Rust）へ移植し、Python の host とフロントエンドを比較用に残すこともできます。この節では、そうした再利用の側から見た境界を説明します。言語に依存しない契約は `allium/core-frontend.allium` にあります。要件は `openspec/specs/` の `machine-core`、`machine-loader`、`machine-state` の各 spec にあります。`allium/` と `openspec/` は、まだ公開していません。

### 3 つの役割

| 役割 | コード | 担当 |
| --- | --- | --- |
| コア | `msx/` | エミュレートするマシン：CPU、VDP、音源チップ、メモリとスロット、FDC、RTC、PPI と入力線。自分の状態と渡されたデータだけで決まる純粋な関数 |
| host | `__main__.py`、`host/` | プロセス単位のサービス：YAML と ROM ファイルの読み込み、ROM データベース、SRAM・ディスクイメージ・ステートセーブ・スクリーンショットのファイル、デバッガ、ソケット RPC |
| フロントエンド | `frontend/` | 表示と入出力機器：SDL2 のウィンドウ、音声出力とそのローパスフィルタ、ホストの入力機器、フレームのペース配分とフレームスキップ、ホットキー |

コアはファイルを開かず、ホストの時計を読まず、`host/`、`frontend/`、`sdl2` のどれも import しません。`tests/test_core_frontend_boundary.py` がこれを強制します。`sdl2` を import できないインタプリタで `msx` のすべてのモジュールを import し、3 つの名前の import があれば失敗にします。つまり、コアが必要とするものはすべて値として入り、コアが作るものはすべて値として出ます。

同じテストは逆向きも調べます。`host/`、`frontend/`、`tools/`、`__main__.py` のコードは、マシンをコアの API（後述）だけで使います。マシンのコアの部品（`.cpu`、`.vdp`、`.memory`、`.fdc` など）やプライベートなフィールドを読んではなりません。例外はありません。デバッガのトレース用コマンド（`te`/`td`、`ce`/`cd`）と `--vdp-trace`/`--mapper-trace` のフラグは、コアの API でトレースの種類を設定します。`drain_trace()` が返すイベントは `host/trace.py` が文字列にします。したがって、コアを移植するときは `Machine` を置き換えても、デバッガ、RPC サーバ、フロントエンドはそのまま使えます。

このテストは、host のコードが `msx/` から何を import するかも調べます。import してよいのは、`msx/boundary.py`、`msx/core_api.py`、`msx/input.py` の `InputFrame`、`msx/machine_loader.py` の `create_machine` と `release_machine`、マシン記述層の公開された名前だけです。マシン記述層は `msx/machine_spec.py` と `msx/machine_config.py` で、マシンのモデル、語彙、MachineConfig の文書のエンコーダとパーサからなります。デバイスのモジュールを import しないので、コアがネイティブになっても Python のまま残ります。host はこのエンコーダを使い続け、ネイティブのコアは同じ文書（`msx/machine_config.schema.json`）を自分のパーサで読みます。host はコアを作る前にセーブファイルを作るので、固定のメディアのサイズ（`FMPAC_SRAM_SIZE`、`RTC_CMOS_SIZE`、`HALNOTE_SRAM_SIZE`、カートリッジの SRAM マッパーごとの定数）は境界の定数にしています。

### 境界を通る値

次の値は、どれも `msx/boundary.py` か `msx/input.py` にあります。

| 値 | 向き | 中身 |
| --- | --- | --- |
| `MachineConfig` | 入力、1 回 | `document`（正規化した MachineConfig の JSON、後述）、`roms`（`RomImage` のレコード。イメージと、その小文字 16 進の SHA1）、`media`（`MediaImage` のレコード。保存済みの SRAM か CMOS のイメージと、その `MediaId`）、`disks`（`DiskMount` のレコード。マウントするイメージ、ドライブ番号、書き込み禁止のフラグ）、`rtc_initial_time`（既定値は `RTC_DEFAULT_START`、2000-01-01 00:00:00）。3 つのイメージのフィールドはタプルで、1 つの id やドライブにレコードは 1 つまで |
| `InputFrame` | 入力、毎フレーム | フラットなレコード。`key_matrix` はアクティブ Low のキーボードマトリクス 11 行の 11 バイト、`joy1` と `joy2` はアクティブ Low の 6 ビットのジョイスティックポート（bit 0 上、1 下、2 左、3 右、4 トリガ A、5 トリガ B）、`mouse_dx` と `mouse_dy` はそのフレームのマウスの移動量（MSX 側の単位）、`mouse_buttons` はビットフラグ `MOUSE_LEFT`（1）と `MOUSE_RIGHT`（2）。作った後は変更できません。コンストラクタが調べるのは、`key_matrix` が 11 バイトであることだけ。ポートとボタンはコアがマスクし、移動量は -32768〜32767 の範囲に収めます |
| `FrameOutput` | 出力、毎フレーム | フラットなレコード。`video_width`、`video_height`、`video_pixels`（描画しなかったフレームでは 0、0、`b""`）、`audio`、`frame_number`、`stop_reason`、`frame_complete`（1。フレームが次の呼び出しに続くときは 0） |
| `RgbFrame` | 出力 | `render_preview()` の結果。行優先に詰めた RGB24。`width` は 256 か 512、`height` は常に 212（192 ラインのモードは、コアがボーダーの色で埋める）。`FrameOutput` の映像のフィールドも同じ並び |
| 音声 | 出力 | 出力フィルタを通す前の、44,100 Hz・符号付き 16 ビット・リトルエンディアンのモノラル PCM。NTSC は 1 フレーム 735 サンプル、PAL は 882 サンプル |
| スナップショット | 入出力 | `Machine.save_state()` の `bytes`（後述） |
| `CpuRegisters` | 出力 | レジスタのクエリの結果：AF、BC、DE、HL、裏レジスタ、IX、IY、SP、PC、I、R、IFF1 と IFF2（0 か 1）、IM |
| `VdpInfo` | 出力 | VDP のクエリの結果：VDP の種類、コントロールレジスタとコマンドレジスタ、ステータスレジスタ、パレット、コマンドの進み具合 |
| メディアのイメージ | 入出力 | `MediaId` で名前を付けた `bytes` |

`MediaId` は整数の id の閉じた集合です。値は 0〜15 で、順に `SLOT1_SRAM`、`SLOT2_SRAM`、`SLOT<n>_SUB<sub>_SRAM`（拡張スロットのデバイスです。スロット 1〜2、サブスロット 0〜3）、`FMPAC_SRAM`、`RTC_CMOS`、`DRIVE_A`〜`DRIVE_D`（FDC のドライブ 0〜3）です。id は整数として境界を越えます。Python のコードでは `MediaId` を `.name` で文字列にします。

`VideoStandard` と `KeyboardLayout` も整数の列挙型です。マシンの YAML と正規化した MachineConfig の文書では、`spelling` プロパティと `from_spelling` クラスメソッドを通して、テキスト（`ntsc` と `pal`、`int` と `jp`）で書きます。

### コアの API

`msx/machine_loader.py` の `create_machine(config)` が `Machine` を作ります。`msx/core_api.py` の `CoreApi` は、`msx/` の外のコードが使う操作をすべて並べた `typing.Protocol` です。`Machine` がこれを実装し、mypy がそれを確かめます。host とフロントエンドは、マシンを `CoreApi` として持ちます。下の操作が境界にあたります。`Machine` のそれ以外のメンバーは、内部用かデバッガの診断用です。

| 分類 | 操作 | 備考 |
| --- | --- | --- |
| 構築 | `create_machine(config) -> BuildResult` | `status`、`machine`、`error_text` を返します。文書に誤りがあるとき、ROM イメージが欠けているか合わないとき、イメージのレコードが矛盾するとき、デバイスが受け付けないサイズ（ディスクイメージ、RAM マッパー）のとき、RTC の開始時刻が 0〜9999 年の外か実在しない日時のときは、`STATUS_BAD_CONFIG`（4）とメッセージを返し、マシンは返しません。新しいマシンの外は何も変わりません。`build_machine(config)` はテストと Python への組み込み用のラッパーで、同じメッセージの `MachineLoadError`（`msx/machine_spec.py`）を出します。host のコードは `create_machine` を呼びます。どちらもロガーとトレーサは引数に取りません。デバッグのログ、VDP とマッパーのトレースは、後述のトレースのメンバーを使います |
| フレームの実行 | `step_frame(frame_input, render_video) -> FrameOutput` | 1 フレーム、または止まったフレームの残りを実行します。ブレークすると途中で戻り、`stop_reason` が入り、`frame_complete` が 0 になります |
|  | `apply_input(frame_input) -> int` | フレームを動かさずに、入力線とマウスのボタンをすぐ置き換えます。RPC のキー入力が使います。常に `STATUS_OK` を返します |
|  | `samples_per_frame()`、`video_standard`、`keyboard_layout` | 735 か 882。映像の方式とキー配列はただの整数。host が `msx/boundary.py` の `VideoStandard` と `KeyboardLayout` の列挙型に変えます（`VideoStandard(core.video_standard).target_fps` は 60 か 50） |
| 制御 | `reset() -> int` | 電源投入時のリセット。RAM、VRAM、マウント中のメディアはそのまま。常に `STATUS_OK` を返します |
|  | `save_state() -> bytes` | スナップショット（`msx/state.py`） |
|  | `load_state(data) -> int` | `STATUS_OK`（0）を返します。スナップショットが合わなければ `STATUS_STATE_REJECTED`（1）を返し、何も変えません |
|  | `write_memory(addr, data) -> int` | CPU と同じように書き、0xFFFF で折り返します。RPC サーバは一時停止中にだけ使います。常に `STATUS_OK` を返します |
| メディア | `dirty_media()` | 前回ファイルへ書き戻した後に書き込まれたメディアの id |
|  | `read_media(media_id) -> bytes` | メディアの現在のバイト列。マシンにない id には、どの整数でも `b""`（メディアが空になることはない） |
|  | `mark_persisted(media_id) -> int` | host がバイト列を書いた後に呼びます。`STATUS_OK`（0）を返します。マシンにない id には `STATUS_NO_MEDIUM`（5）を返します |
|  | `mount_disk(drive, data, write_protected) -> int` | `STATUS_OK`（0）を返します。マシンにないドライブには `STATUS_NO_DRIVE`（2）、サイズが 512 の正の倍数でないイメージには `STATUS_BAD_IMAGE`（3）、0 と 1 以外の `write_protected` には `STATUS_BAD_ARGUMENT`（6）を返します。失敗したときは何も変えません。外すイメージに保存していない書き込みがあれば、host が呼び出しの前にファイルへ書き戻します |
|  | `eject_disk(drive) -> int` | ドライブを空にします。`STATUS_OK`（0）を返し、マシンにないドライブには `STATUS_NO_DRIVE`（2）を返します |
|  | `last_error_text() -> bytes` | ステータスを返すメンバー（と空の値を返した `read_media`、-1 の値を返した `slot_info`）のうち、最後に失敗した呼び出しのメッセージ（UTF-8）。成功した後は `b""`。コアの API のメンバーは、不正な引数に例外を出しません。`host/state_files.load_state` と `host/media.swap_disk` は、これを `ValueError("<ファイル>: <メッセージ>")` として出します |
|  | `disk_mounted(drive)` | ドライブがあり、イメージが入っているか |
| デバイス | `attach_mouse(port) -> int` | MSX のマウスをジョイスティックポート 1（`0`）か 2（`1`）につなぎます。マウスのデバイスはコアが作ります。ほかのポートには `STATUS_BAD_ARGUMENT`（6）を返します。host が呼ぶのは 1 回まで、最初の `step_frame` と最初の `load_state` の前。マウス付きで保存したステートは、マウスをつないだマシンでだけ読み込めます。取り外しはありません |
|  | `attach_printer() -> int` | プリンタポートにプリンタをつなぎます。呼ぶ時期は問いません。ポートのないマシンには `STATUS_NO_PRINTER_PORT`（7）を返します。接続の有無はステートに入りません。取り外しはありません |
|  | `drain_printer() -> bytes` | ゲストが STROBE で送ったバイトを順に返し、`PRINTER_BUFFER_SIZE`（4096）バイトのバッファを空にします。バッファが満杯のときに送られたバイトは捨てますが、ステータスポートは準備完了のままです。そのため、ホストがどの間隔で取り出しても、ゲストが読む値は変わりません。実行ループは `step_frame` のたびに取り出し、1 フレームで 4096 バイトを送ることはできません。リセットとステートの読み込みではバッファは変わりません |
| 状態の読み出し | `cpu_registers() -> CpuRegisters` | レジスタ一式 |
|  | `read_memory(addr, length) -> bytes` | 今のスロットの選択で、CPU と同じように読みます。0xFFFF で折り返します。ウォッチポイントには反応しません。メモリに割り当てられたデバイスのレジスタは、副作用も含めて CPU と同じように読みます。長さが 0〜0x10000 の外なら `b""` を返し、`last_error_text()` を設定します |
|  | `vram_size()`、`read_vram(addr, length) -> bytes` | 0x4000 か 0x20000。読み出しはアドレスの下位ビットで VRAM のサイズに折り返すので、負のアドレスは末尾に折り返します。長さが 0〜VRAM のサイズの外なら `b""` を返し、`last_error_text()` を設定します |
|  | `vdp_info() -> VdpInfo` | ステータスのフラグを消しません |
|  | `frame_number()`、`drive_count()`、`main_ram_range()` | フロッピーのインターフェースがなければ `drive_count()` は 0 |
|  | `slot_layout() -> SlotLayout` | ポート 0xA8 の値、拡張されたプライマリスロットのビットマスク、4 つのサブスロットレジスタ（拡張されていないスロットは 0）。デバッガの `sl` と `st` が使います |
|  | `slot_info(primary, secondary, page) -> SlotInfo` | 1 つのスロットを整数で返します。`sl` と `st` が使います。内容の種類（`SLOT_CONTENT_*`）、カートリッジの `MapperId`、KB 単位のサイズ、バンクの種類（`BANK_*`）、2 つのウィンドウ（それぞれ `WINDOW_*` の種類と値）、Halnote のサブバンクとその ROM 上の位置からなります。文字列は `host/debugger/slots.py` が組み立てるので、表示の書式は境界を越えません。`secondary` と `page` は、なしのとき -1。0-3 の外の値には、内容、マッパー、サイズに -1 を返します |
|  | `render_preview() -> RgbFrame` | 今のフレーム。スクリーンショットとステートセーブのサムネイルに使います |
| デバッグの制御 | `set_breakpoints()`、`set_watchpoints()` などのブレークの設定、`breakpoints()`、`watchpoints()`、`break_halt_di()` | ブレークの設定と、その読み出し。ウォッチポイントは `(addr, kind)` の組で、kind は `WATCH_READ`、`WATCH_WRITE`、またはその両方。デバッガ、RPC、CLI の `r`、`w`、`rw` との変換は `host/watch.py` が行います。各設定は `STATUS_OK` を返します。`MAX_BREAKPOINTS` または `MAX_WATCHPOINTS`（4）を超える件数、0x0000〜0xFFFF の外のアドレスや SP の値、不正な kind、-1 より小さいフレーム、下限が上限より大きい SP の範囲には `STATUS_BAD_ARGUMENT`（6）を返し、設定を変えません。-1 は実行先のアドレスとフレームのブレークポイントを消し、`set_sp_range(-1, -1)` は SP の範囲を消します。`set_break_halt_di()` は 0 か 1 を取り、それ以外の値には `STATUS_BAD_ARGUMENT` を返して設定を変えません |
|  | `set_sprites_hidden(hidden) -> int`、`sprites_hidden()` | デバッガの `ds` が使うスプライト表示のフラグ。立っている間、描画はスプライトの処理を飛ばします。リセットでは消えず、ステートセーブにも入りません。設定は 0 か 1 を取り、それ以外の値には `STATUS_BAD_ARGUMENT` を返してフラグを変えません |
|  | `set_trace_kinds(kinds) -> int`、`trace_kinds()`、`trace_sources()` | 有効なトレースの種類（ビットフラグ `TRACE_VDP`、`TRACE_MAPPER`、`TRACE_DEBUG`。`TRACE_DEBUG` は `--debug` のログ）と、このマシンが出せる種類。`TRACE_MAPPER` には、スロット 1 か 2 にバンク切り替えのある ROM マッパーが要ります。ほかのビットには `STATUS_BAD_ARGUMENT`（6）を返します。リセットでは消えず、ステートセーブにも入りません |
|  | `drain_trace() -> tuple[TraceEvent, ...]` | 記録したイベントを順に返します。`TRACE_VDP` が有効な間はポート 0x99 と 0x9B に書いた各バイト、`TRACE_MAPPER` が有効な間はスロット 1/2 のマッパーのバンクの変化を、`TRACE_DEBUG` が有効な間は I/O ポートへの各アクセス、VDP の R#0 と R#1 に保持された各書き込み、各未定義命令、各ハング（PC が 1000 回続くループか、フレームの終わりの割り込み禁止の HALT。直前の 64 命令の後に記録する）を、サイクル数、フレーム番号、PC とともに記録します。取り出すとバッファは空になります。バッファは `TRACE_CAPACITY`（65536）件まで持ちます。あふれた後の取り出しは、失われた件数を示す `TRACE_EVENT_LOST` のレコード 1 つで終わります |
|  | `step() -> int` | 今のフレームの 1 命令を実行し、その T ステート数を返します。サイクル数、フレーム内の位置、VDP は `step_frame` と同じように進みます。ブレークポイントとブレーク要求は確かめません |
|  | `request_break() -> int` | 次のフレームの区間に入る前に止めます。シグナルハンドラ（Ctrl-C）から呼べます。常に `STATUS_OK` を返します |
|  | `last_stop() -> StopInfo` | 直前の停止。理由、PC、ウォッチポイントならアドレス、値、アクセスの種類 |

メモリに割り当てられたデバイスのレジスタを読む場合を除き、状態を読む操作はエミュレートしている状態を変えません。`tests/test_core_api.py` がこれを確かめます。どの操作の前後でも、ステートセーブのバイト列は同じになります。

`CoreApi` のメンバーは、どれもコールバックを受け取りません。ブレークは、`step_frame` の結果と `last_stop()` だけで伝わります。コアは host のコードを呼ばず、何も表示せず、`KeyboardInterrupt` も捕まえません。止まったあとは、host の `handle_stop()`（`host/debugger/stop.py`）がウォッチポイントの行を表示し、RPC サーバを一時停止させるか、デバッガを開きます。実行中は、ターミナルの Ctrl-C を host が `request_break()` に変えます。次の `step_frame` は、止まったフレームの続きを実行します。続きの最初の命令はブレークポイントと照合しないので、再開してすぐにまた止まることはありません。

ほかのメンバーは境界を通りません。ソフトウェアを動かすだけなら、移植する必要はありません。

- `run_frame()`：内部のフレームループ

### MachineConfig の文書

マシンの定義は、2 つの層を経てコアに届きます。

1. **手で書く YAML**（`config/machines`、`config/devices`、`config/extensions`）。ハードウェアについてのコメントを残せます。読むのは host だけです（[マシンと拡張の YAML スキーマ](#マシンと拡張の-yaml-スキーマ) を参照）です。
2. **MachineConfig の文書**。`load_machine_config()` が YAML とコマンドラインの指定から作ります。文書は RFC 8785（JCS）で正規化した JSON（UTF-8、キーはソート済み、空白なし、整数のみ）で、`format_version` は `2` です。書き出すのは 2 だけですが、パーサーは形式 1 も読みます。形式 1 には `printer_port` がなく、その場合の形式は `none` です。後の形式で追加するキーには、古い文書のための既定値を決め、ABI の minor を上げます。major が同じコアは、古い形式をすべて読み続けます。解決済みのマシン、スロットごとのカートリッジとそのマッパー（`auto` にはならない）、拡張オーバーレイを持ちます。エンコードと解析は `msx/machine_config.py` が行い、形は `msx/machine_config.schema.json`（JSON Schema draft 2020-12）に書いてあります。スキーマを読むのはテストで、コアは読みません。`tests/vectors/canonical_json.json` には、入力とその正規形と SHA-256、読み込みで拒否する入力、base64 のバイト列を載せてあります。コアを別の言語で実装するときは、このファイルで自分の書き出しと読み込みを確かめられます。

文書の中の ROM は、どれも `{name, size, sha1, pages}` の参照で表します。`name` はディレクトリを含まないファイル名で、表示用のラベルにすぎません。`sha1` は host が読んだバイト列の SHA1 です。イメージは、その SHA1 をキーにして `MachineConfig.roms` で渡します。解析は厳密で、次のものを拒否します。

- 知らないキーや欠けたキー、型や範囲の誤り、浮動小数点数、重複したキー
- 1 と 2 以外の `format_version`
- 閉じた集合にない名前（マッパー、FDC のコントローラ、接続方式、その 2 つの組、フラットな拡張のデバイス、サブスロットのデバイス、I/O のデバイス）
- ROM が必要なのに持たないデバイス、`size_kb` のない `ram_mapper`
- ない ROM イメージや、サイズか SHA1 が参照と違う ROM イメージ

SRAM と CMOS のイメージ、ディスク、RTC の開始時刻、host 側のすべてのパスは、実行時の入力です。文書の外にあるので、文書の同一性には影響しません。この同一性は、コアが文書を解析してからエンコードし直した正規形の SHA-256 で表します。したがって、空白やキーの順序だけが違う文書は、同一性も同じになります。`Machine` はこの値を `machine_config_sha256` として持ちます。

YAML のローダを持たない移植では、文書を直接書けます。または Python の host が作った文書も使用できます。`load_machine_config()` でエンコードし、`config.config.document` と `config.config.roms` を保存します。

### スナップショット

`save_state()` は、マシンの全状態を正規化した JSON（フォーマットバージョン 22）で返します。形は `msx/snapshot.schema.json` に書いてあります。オブジェクトはコンポーネントごとに 1 つあります（`cpu`、`memory`、`slots`、`vdp`、`psg`、`ppi`、`scc`、`fdc`、`rtc`、`mouse`、`printer`）。キーは常にすべてそろい、マシンにない部分の値は `null` になります。バイト列はパディング付きの RFC 4648 base64 の文字列で、整数はどれも ±(2^53 − 1) の範囲に入ります。

- `load_state()` は、空白もキーの順序も問いません。浮動小数点数、NaN や Infinity、重複したキー、ほかのフォーマットバージョンは拒否します。
- 最初にマシンの同一性（マシンの種類、マッパーの種類、スロットの配線、FDC、マウント中のディスク。ディスクはサイズと SHA1 で比べるので、同じ内容なら別のファイルでも同じディスクとみなす）を調べます。続いて `machine_config_sha256` を動作中のマシンのハッシュと比べます。このハッシュは、カートリッジの ROM のバイト列も対象に含みます。つまり、スナップショットは同じ MachineConfig の文書から作ったコアにしか読み込めません。
- どれかの検査に失敗したスナップショットは、何も変えません。
- `save_state()`、`load_state()`、もう一度 `save_state()` の順に呼ぶと、同じバイト列になります。

スナップショットが持たない状態は 2 種類あります。1 つは漢字フォント ROM のアドレスと読み出しカウンタで、これは意図して保存しません（`machine-state` の spec を参照）です。もう 1 つは音声フィルタの状態など、コアの外にある状態です。

### フレームの実行

`Machine.step_frame(frame_input, render_video)` は、コアを 1 フレーム進める呼び出しです。`frame_input` を、PPI と PSG が読む入力線（`InputLines`）に反映するので、入力はフレームの間ずっと変わらず、マウスはそのフレームの移動量を 1 回だけ受け取ります。その後フレームを実行し、`FrameOutput` を返します。`render_video` が決めるのは、コアが映像を作るかどうかだけです。映像を作らないフレームでもスプライトの処理は行うので、VDP のステータス、音声、次のスナップショットは、どちらでも同じになります。ブレークで止まった呼び出しは、`stop_reason` を入れ、`frame_complete` を 0 にして返します。映像も音声も返しません。次の呼び出しはそのフレームの続きを実行します。入力線とボタンはもう一度反映しますが、マウスの移動量は渡しません。移動量は、そのフレームですでに受け取っているからです。

入力は、コアの外にある `host/input_state.py` の `InputState` が組み立てます。キーボード、ゲームパッド、マウス、RPC の入力メソッドはそこに書き込みます。各ポートのキーボード側とゲームパッド側の入力は、合成するまで別々に持つ。フロントエンドは毎フレーム `take_frame()` を `step_frame` に渡し、そのとき組み立て側のマウスの移動量も 0 に戻ります。RPC の入力メソッドは `Machine.apply_input` を呼びます。`apply_input` はフレームを動かさずに入力線とマウスのボタンをすぐ反映するので、一時停止中のマシンでも次の命令から入力が見えます。ヘッドレスの実行（`--count-frame`、`--benchmark`）は空の `InputFrame()` を渡します。

### 保証

- **決定性。** 同じ MachineConfig から 2 つのコアを作り、同じ `InputFrame` の列を与えます。すると、映像、音声、スナップショットのバイト列は同じになります。コアを誰が動かしても、`render_video` にどんな値を渡しても同じです。RTC はエミュレートしたサイクル数で進み、ホストの時計は使いません。
- **1 回の呼び出しで 1 フレーム。** ふつうの実行モードでは、`step_frame` の 1 回の呼び出しでちょうど 1 フレーム進み、1 フレーム分の音声を返します。1 フレームは NTSC で 59,736 T ステート、PAL で 71,364 T ステートになります（1 命令分の誤差の範囲で）。デバッガのブレークで、フレームが途中で終わることがあります。そうなる実行は契約の対象外です。
- **RGB 変換はコアの中。** コアは整数の表だけを使って、すべてのピクセルを RGB に変換します。フロントエンドがパレットや VDP のレジスタを知る必要はありません。
- **フィルタ前の音声。** 音声は、マシンにあるすべての音源（PSG、SCC、Majutsushi DAC、キークリック、OPLL）の出力を、フィルタを通す前に足し合わせて範囲に収めたものです。フィルタ、リサンプリング、バッファリングはフロントエンドが担当します。
- **出力の有効期間。** `FrameOutput.video_pixels` と `.audio` が有効なのは、次の `step_frame` までです。Python では毎回新しく作る、変更できない `bytes` なので、SDL2 フロントエンドは最後のフレームをコピーせずに持っています。ネイティブのコアはバッファを使い回すことができます。その場合、フロントエンドは手元に長く残すフレームをコピーします。
- **メディア。** メディアは、エミュレートしたマシンが最初に書き込んだ時点から、`mark_persisted` が呼ばれるまで dirty になります。すでに入っている値と同じ値の書き込みは数えません。書き込み禁止のメディアは dirty になりません。SRAM の id の集合はコアが構築時に決め、その後は変わりません。ディスクを交換するときは、外す側のイメージが dirty なら先に書き戻し、それから `mount_disk` を呼びます。`host/media.py` の `swap_disk` はこの順で処理するので、書き戻しに失敗しても、ドライブは元の状態のまま残ります。

### 組み込み例

この例は読み込みに Python の host を使い、コアをヘッドレスで動かします。これが、別のフロントエンドに必要な最小限の形です。ループの中身を、自前の表示、音声出力、入力の処理に置き換えて使います。

```python
from pathlib import Path

from host.machine_config import (
    load_device_registry, load_machine_config, load_machine_spec, sram_paths,
)
from host.media import DiskFiles, persist_dirty_media
from msx.input import InputFrame
from msx.machine_loader import create_machine

root = Path(".")                                   # リポジトリのルート
registry = load_device_registry(root / "config")
spec = load_machine_spec("cbios_msx2", root / "config", registry, root)
config = load_machine_config(spec)                 # host：YAML とファイル -> MachineConfig
result = create_machine(config.config)             # コア：値だけを使う
if result.machine is None:                         # STATUS_BAD_CONFIG
    raise SystemExit(result.error_text.decode("utf-8"))
machine = result.machine

for _ in range(120):
    out = machine.step_frame(InputFrame(), render_video=True)
    # out.video_pixels：out.video_width * 212 * 3 バイトの RGB24
    # out.audio：samples_per_frame() 個の符号付き 16 ビット LE モノラルのサンプル

snapshot = machine.save_state()                    # bytes。保存先は自由
if machine.load_state(snapshot) != 0:             # 0 は STATUS_OK
    print(machine.last_error_text().decode("utf-8"))
assert machine.save_state() == snapshot

disk_files = DiskFiles(config.disk_files)          # host：各ドライブのディスクのファイル
persist_dirty_media(machine, sram_paths(config), disk_files)  # SRAM/CMOS/ディスクの変更を書き戻す
```

SDL2 フロントエンド（`frontend/sdl2_frontend.py`）も同じ形で動きます。毎フレーム、入力の組み立て側が作ったフレームを `step_frame` に渡します。コアがフレームを描画したとき（`out.video_width` が 0 でないとき）は `out.video_pixels` をアップロードします。`out.audio` は `BiquadLowPass`（`frontend/audio_filter.py`）に通して SDL のキューに積みます。終了時には `__main__.py` が `persist_dirty_media` を呼びます。自前の host 側を持たないフロントエンドは、一度の実行で得た `config.config.document` と `config.config.roms` を取っておき、それをもとに `MachineConfig` を直接作成できます。

### コアの移植

ネイティブのコアが実装するコア API の C の形は、`include/msx_core.h` にあります。`CoreApi` のメンバーごとに `msx_core_<member>` の関数が 1 つ、境界のレコードごとに構造体が 1 つあり、定数は `msx/boundary.py` と同じ値を持ちます。名前と定数が Python のコードと同じであることは、`tests/test_core_c_header.py` が確かめます。フレーム出力の映像と音声は、次の `step_frame`、`reset`、`load_state` まで有効です。コアが返すそれ以外のバイト列は、同じコアに対する次の呼び出しまで有効です。渡した入力は呼び出しの間だけ読まれます。1 つのコアは、一度に 1 つのスレッドからだけ使います。C の各型は、コアがマスクする値、範囲に収める値、拒否する値のどれも表せる幅を持ちます。マウスの移動量は `int32_t`、フレームのブレークポイントは `int64_t` です。`msx_core_create` は呼び出し側が渡したバッファにメッセージを書くので、コアはグローバルな状態を持ちません。host は音声のレートとフレームの高さを `msx/boundary.py`（`AUDIO_SAMPLE_RATE`、`OUTPUT_HEIGHT`）から取ります。MSX のキー名（`host/key_names.py`）とカートリッジの SRAM のサイズ（`host/machine_config.py`）は、コアのどのモジュールも使わないので、host が持ちます。

このヘッダの ABI バージョンは 1.1 です。`MSX_CORE_ABI_VERSION_MAJOR` と `MSX_CORE_ABI_VERSION_MINOR` がそれぞれの値で、`msx_core_abi_version()` は `(major << 16) | minor` を返します。関数か定数を追加したら minor を上げます。既存の宣言かマクロの値を変更または削除したら major を上げ、minor を 0 に戻します。ネイティブのコアがこのヘッダを実装するまで major は 1 のままなので、それまでは基準ファイルを更新するだけです。コメントだけの変更では何も変わりません。host は、major が異なるコアと、minor が自分のビルド時より小さいコアを拒否します。C や Zig の host は、ヘッダの `MSX_CORE_ABI_COMPATIBLE(v)` でこの判定をします。このマクロは host の側で評価するので、基準ファイルには入りません。ネイティブのコアを読み込む host はまだないので、このマクロを使う host もまだありません。Python のバインディングにも同じ判定が必要です。host は、コアが返した定数の値を知らなくても動作を続けます。`tests/msx_core_abi_baseline.txt` は宣言とマクロの値をバージョンと一緒に記録しています。`tests/test_core_c_header.py` は、基準ファイルがヘッダと一致するまで失敗し、メッセージで変更の種類を示します。

Python のコードは、`allium/core-frontend.allium` にある言語に依存しない契約と、次の点で違います（同ファイルの _Status_ のコメントに一覧がある）です。

- `save_state` と `load_state` は、`msx/state.py` を呼ぶ `Machine` のメソッド
- 状態を読む操作のレコードは、Python のレジスタ名（`AF`、`AF_`）を使い、パレットをタプルで持ちます。
- `read_media` はバイト列だけを返し、書き込み禁止のフラグはマウント中のイメージが持ちます。
- フレームレートはメソッドではなく、`VideoStandard` のデータ（`target_fps`）
- `load_machine_config()` は `LoadedMachineConfig` を返します。これは `MachineConfig`（`config`です。コアが受け取る部分）と、host の SRAM とディスクのファイルパスを持ちます。

移植する側は、次の点も決める必要があります。

- **デバッグの制御。** ブレークの設定、`request_break()`、停止の結果は契約に入っています（DebugControl）。デバッガに対応する移植では、止まったフレームの位置を覚えて次の `step_frame` で続きを実行し、止まった直後の最初の命令ではブレークポイントの照合を飛ばします。最初の移植では、ブレークの設定を省略できます。何も設定しなければ、`step_frame` が止まることはありません。

静的型付けの言語にそのままの対応がない Python のパターンもあります。バスフック、レジスタのプロパティ、PSG エンベロープのアンダーフロー、レンダラの変換表、Callable のフックがそうです。詳しくは [移植性](#移植性) を参照してください。

移植は、次の順で進めることを提案します。

1. **CPU、メモリとスロット、VDP、PSG**。MSX1 の C-BIOS マシン 1 台（`cbios_msx1`）を対象にします。MachineConfig の文書と ROM イメージは Python の host から取ります。両方で、空の入力のまま N フレームをヘッドレスで実行します（`--count-frame` と同じ）です。そのあと `save_state()` のバイト列を比べます。違いがあれば、どのコンポーネントのどのフィールドかがわかります。
2. **スナップショットの形式**。一方の実装で保存したスナップショットを、もう一方で読めるようにします。この段階を終えると、どちらの実装で保存した状態も、もう一方の開始状態に使えます。そのため、プログラムのどの時点からでもテストを始められます。
3. **MSX2**：V9938 とそのコマンドエンジン、RAM マッパー、RTC、拡張スロット（`cbios_msx2`）。
4. **残りの音源とカートリッジ**：SCC、DAC とキークリック、表を持たせた OPLL、SRAM 付きのマッパー。
5. **FDC**（WD2793 と TC8566AF）とメディアの API。

どの段階でも、Python のコアとバイト列を比べます。同じ設定と同じ `InputFrame` の列から、同じ `video_pixels`、音声、スナップショットのバイト列が得られなければなりません。`tests/vectors/golden_state.json` には、このような実行を定めてあります。3 機種の C-BIOS の起動と 2 本のホームブリューのゲームについて、入力の手順と、決まった確認点のフレームを持ちます。確認点ごとに、スナップショットの部分ごと、音声、映像の SHA-256 を記録できます。スナップショットの形はまだ変わるので、ハッシュは移植を始める前に、コアとケースを見直してから記録します。`tools/golden_state.py` は Python のコアをこのファイルと照合し、移植したコアも同じファイルを読めます。必要な ROM と置き場所は [`tools/README_tools_ja.md`](../tools/README_tools_ja.md#golden_statepy) に書いてあります。

## 移植性

実装は純粋な Python 3.10+ で、SDL2 フロントエンドを除いて C 拡張やネイティブのバインディングはありません。コアの処理は、静的型付けのシステム言語（Rust、C++）に移しやすいように設計しています。各判断の理由は、対応するソースファイルに _Portability note_ として書いてあります。この節は、それを要約します。

### 移植性を意識した設計

次のパターンは、静的型付けの言語にそのまま移せます。

- **オペコードの振り分け**（`msx/cpu/z80.py`、`msx/cpu/opcodes_main.py`）：`_DISPATCH` はインポート時に一度だけ作る、256 要素のフラットな callable のリストで、各要素はふつうの関数です。Rust では関数ポインタの配列か、オペコードのバイトに対する `match` になります。
- **レジスタ**（`msx/cpu/registers.py`）：主レジスタは Python の `int` のフィールドで、書き込むたびに `& 0xFF` / `& 0xFFFF` で明示的にマスクします。多倍長の値には頼りません。裏レジスタは、`A_`、`BC_` などの名前の付いた個別のフィールドで、リフレクションは使いません。
- **VRAM と RAM**：サイズ固定の `bytearray` で持ちます（V9938 は `_VRAM_SIZE = 131072`、TMS9918A は 16384、RAM マッパーはセグメントの配列）です。アクセスはすべてスライスとインデックスで直接行います。int のリストや辞書を使った記憶域はありません。
- **I/O バスの振り分け**（`msx/io.py`）：`list[tuple[int, int, Callable]]` を先頭から調べます。辞書の検索もリフレクションも使いません。登録するハンドラは少なく、マシン設定ごとに決まっています。
- **コンポーネントの構造体**：ハードウェアのコンポーネントはすべて `@dataclass` で、フィールドはすべて型付きで明示的に宣言します。`msx/` のどこにも `__getattr__`/`__setattr__` の仕掛けはありません。最も頻繁に通る 2 つの構造体、`Z80` と `Registers` は `@dataclass(slots=True)` にして、インスタンスごとの `__dict__` をなくしています。`Memory` と、V9938 の小さな値の型いくつか（ログのエントリの `_RegChange`/`_PaletteChange`、`_RenderStatus`、`_SatSegment`）も同じです。ほかは素の `@dataclass` のままにしています。頻繁に通らない場所では、フィールド配置の保証がそれほど重要でないからです。
- **エミュレーション上の時刻**（`msx/clock.py`）：マシンは、サイクル数、フレーム数、命令のアドレスを持ちます `CoreClock` の記録を 1 つ持ちます。`build_machine` は、同じ記録を CPU、VDP、PSG、PPI、RTC、Majutsushi の DAC、トレースバッファに渡します。各値を書くのは 1 か所だけです。どのコンポーネントも、クロージャや `Machine` への参照を通して時刻を読みません。C や Zig の移植では、この記録を構造体にして、各コンポーネントがそれを指します。
- **タイミングの定数**：`CYCLES_PER_LINE`、`NTSC_LINES_PER_FRAME` / `PAL_LINES_PER_FRAME`（`msx/vdp/_geometry.py`）、`_HBLANK_START`、`_TR_REASSERT_CYCLES` などは、モジュールレベルの整数定数です。1 フレームの予算は、同じモジュールの `lines_per_frame(standard)` と `cycles_per_frame(standard)` から得ます。移植では `match` / `const fn` として書きます。

### 移植時に対応が必要な Python 固有パターン

次のパターンは、静的型付けの言語にそのままの対応がない Python の機能を使います。各パターンについて、Rust/C++ の移植で代わりに使うものをソースのコメントに書いてあります。

#### バスフックとしての再代入可能な束縛メソッド

`Z80` は `read_byte` / `write_byte` / `read_port` / `write_port` を `Callable` のフィールドとして持ちます。`Machine.__post_init__` が実行時に束縛メソッドを代入してつなぎます。あとでプログラムがウォッチポイントを有効にすると、`Machine.set_watchpoints` が `cpu.read_byte` / `cpu.write_byte` をもう一度差し替えます。ふつうのメモリハンドラと、ウォッチポイントを捕まえる版（`Machine._read_with_watch` / `_write_with_watch`）を切り替えます。

Python では、`Callable` のフィールドは `__call__` を持つどんなオブジェクトでも入れられる場所なので、これができます。Rust と C++ には、構造体のフィールドのメソッドを実行時に差し替える仕組みはありません。移植では、バスを `trait MemoryBus`（または `enum { Normal, Watchpoint }`）で表します。具体的な実装をフラグで一度選ぶので、アクセスごとの振り分けに分岐はありません。

#### レジスタの 8 ビットの半分を計算するプロパティ

`Registers` は `BC`、`DE`、`HL` を 16 ビットの `int` フィールドとして持ちます。`B`/`C`、`D`/`E`、`H`/`L` へのアクセスは、16 ビットの組をシフトしてマスクする `@property` の getter/setter（`Registers.B`/`C` など）を通ります。8 ビットの半分を読み書きするオペコードはどれもディスクリプタの仕組みを経由することになり、頻繁に通る命令の経路に呼び出しが 1 段増えます。

Rust/C++ の移植では、8 ビットの半分をふつうの `u8` フィールド（または明示的なインラインの getter/setter）として持ち、16 ビットの組は必要なときに計算します。これでプロパティ呼び出しのコストがなくなります。

#### PSG エンベロープの多倍長整数

Python の整数は精度に上限がないので、`-1 & 0x20 == 0x20` になります。PSG のエンベロープジェネレータは、`_env_step` が負になったときにこれを使います。負の Python の int でも、bit 5 がアンダーフローのフラグとして働きます（`PSG._env_step`）。

Rust/C++ の符号なしの型はアンダーフローで折り返し、符号を広げないので、同じ式は `0x20` ではなく 0 になります。移植では、符号付きの型（`i32`）か、明示的な `wrapping_sub` / ビットフィールドでフラグを保たなければなりません。

#### レンダラの `bytes.translate()` LUT キャッシュ

V9938 の G4/G6 と G5 の、スキャンラインごとのピクセル展開は、`bytes.translate()` の表を `_G46_LUT_CACHE` / `_G5_LUT_CACHE` にためておきます。どちらも `(tp: bool, border: int)` をキーにした辞書です。`bytes.translate()` は CPython の組み込みで、256 バイトの変換表を C で一度に適用するので、Python でこの変換をする最も速い方法になります。辞書のキャッシュで、スキャンラインごとに同じ表を作り直さずに済みます。

`bytes.translate()` も呼び出しごとの辞書のハッシュも、システム言語では自然な書き方ではありません。Rust/C++ の移植では、固定の `[u8; 256]` の配列を初期化時に計算しておき、直接インデックスで引きます。`(tp, border)` の組み合わせは全部で 32 通りしかありません。

#### フレームタイマーのスピンループ

`FrameTimer.tick()` は、フレームの締め切りまでの最後の 2 ミリ秒を、`time.perf_counter()` を使った待ちループで待ちます。Python には、このループがスピンであることをスケジューラに伝える方法がありません。

Rust の移植では、同じループに `std::hint::spin_loop()` を入れます。スリープせずに CPU のパイプラインにヒントを出せます。SMT のコアで消費電力を減らし、タイミングのぶれを小さくできることがあります。

早送り中、フロントエンドは `tick()` の代わりに `FrameTimer.tick_unpaced()` を呼びます。fps の計測のためにフレームを数え、締め切りも設定し直しますが、待ちはしません。移植でも同じ 2 つの入口を残します。
