# py-msx-emulator

純粋な Python 3.10+ で書いた、機能的に正確な MSX1/MSX2 エミュレータ。機械可読なコンポーネント仕様に基づいて実装している。

![Python](https://img.shields.io/badge/python-3.10%2B-blue) ![License](https://img.shields.io/badge/license-MIT-green) ![Tests](https://img.shields.io/badge/tests-3782%20passing-brightgreen)

[English README is here](README.md)

---

ハードウェアコンポーネントはすべて純粋な Python で書いている。各コンポーネントは実装の前に機械可読な仕様として定義し、その仕様がテストを決める（[仕様駆動アーキテクチャ](#仕様駆動アーキテクチャ)を参照）。コンポーネントの配線は `build_machine()` に手で書いてあり、リフレクションや依存性注入は使わない。プラットフォーム固有の依存は、画面と音声のフロントエンドに使う pysdl2 だけ。内部構造は [`docs/technical-implementation_ja.md`](docs/technical-implementation_ja.md) で説明している。

動かすには BIOS ROM が必要で、リポジトリには含まれない。汎用マシンには無償の [C-BIOS](https://cbios.sourceforge.net/) が使える。手順は [QUICK_START_ja.md](QUICK_START_ja.md) を参照。

### 検証済みハードウェアとソフトウェア

作者が検証したのは、作者が所有する以下のハードウェアとソフトウェアだけで、テストにもそれらからダンプした ROM とディスクだけを使っている。ほかの MSX1/MSX2 タイトルが動く保証はない。ほかのタイトルのバグ報告は歓迎するが、対応はベストエフォートになる。

**ハードウェア:**

- [Sony HB-10](https://www.msx.org/wiki/Sony_HB-10)
- [Sony HB-F1XD](https://www.msx.org/wiki/Sony_HB-F1XD)
- [MSX標準日本語カートリッジ HBI-J1](https://www.msx.org/wiki/Sony_HBI-J1)
- [Sony HBP-F1C](https://www.msx.org/wiki/Sony_HBP-F1C)（熱転写カラープリンタ。エミュレートしているプリンタは、取扱説明書をもとにした機能単位の実装。後述の「プリンタ」節を参照）
- [FM-PAC（ＦＭパナアミューズメントカートリッジ）](https://generation-msx.nl/software/matsushita-electric-industrial/fm-pana-amusement-cartridge/1072)

**ソフトウェア:**

- **MSX1:**
  - [沙羅曼蛇（Salamander）、KONAMI](https://generation-msx.nl/software/konami/salamander/941)
  - [グラディウス2（Nemesis 2）、KONAMI](https://generation-msx.nl/software/konami/nemesis-2/932)
  - [夢大陸アドベンチャー（Penguin Adventure）、KONAMI](https://generation-msx.nl/software/konami/penguin-adventure/873)
  - [コナミの新10倍カートリッジ（Game Master 2）、KONAMI](https://generation-msx.nl/software/konami/game-master-2/937)
  - [雀フレンド（Mahjong Friend）、TAITO](https://generation-msx.nl/software/taito/mahjong-friend/504)
- **MSX2:**
  - [ドラゴンスレイヤーIVドラスレファミリー（Dragon Slayer IV - Drasle Family）、Falcom](https://generation-msx.nl/software/falcom/dragon-slayer-iv-drasle-family/979)
  - [ロマンシア（Romancia - Dragon Slayer Jr.）、Falcom](https://generation-msx.nl/software/falcom/romancia-dragon-slayer-jr/881)
- **MSX2（フロッピーディスク）:**
  - [ドラゴンスレイヤー英雄伝説（Dragon Slayer VI - The Legend of Heroes）、Falcom](https://generation-msx.nl/software/falcom/dragon-slayer-vi-the-legend-of-heroes/1387)
  - [はがき書右衛門（Letter Writer: Print Like Handwriting）、Sony](https://generation-msx.nl/software/sony-corporation/letter-writer-print-like-handwriting/1181)

---

## 目次

- [対応ハードウェア](#対応ハードウェア)
- [仕様駆動アーキテクチャ](#仕様駆動アーキテクチャ)
- [パフォーマンス](#パフォーマンス)
- [インストール](#インストール)
- [使い方](#使い方)
- [ツール](#ツール)
- [リモート制御（Socket RPC & MCP）](#リモート制御socket-rpc--mcp)
- [マシン設定](#マシン設定)
- [テストの実行](#テストの実行)
- [プロジェクト構成](#プロジェクト構成)
- [コントリビューション](#コントリビューション)
- [謝辞](#謝辞)
- [ライセンス](#ライセンス)
- [更新履歴](#更新履歴)

---

## 対応ハードウェア

各コンポーネントは純粋な Python で書いている。「実装」はソースファイル、「既知の制限」はまだ再現していない動作を示す。

### CPU：Zilog Z80

レジスタはすべて持つ（AF、BC、DE、HL、IX、IY、SP、PC、I、R と裏レジスタ）。文書化された 252 のオペコードすべてと、CB/DD/ED/FD プレフィックスのテーブル、非公開の IXH/IXL/IYH/IYL アクセス命令を実装している。マスカブル割り込み（INT モード 1 と 2）とノンマスカブル割り込み（NMI）にも対応する。ステップ実行は T ステート単位で正確。MSX の **M1 ウェイト**を設定でき、オペコードフェッチごとに 1 T ステートを足す（実機と openMSX と同じ）。デフォルトは 0 で、純粋な Zilog Z80 として動く。

- 実装：`msx/cpu/z80.py`、`msx/cpu/opcodes_main.py`、`msx/cpu/registers.py`
- 既知の制限：
  - OTIR/INIR などのブロック I/O 命令は、ページ境界をまたぐとサイクル単位では正確でない。
  - 非文書化フラグビット Y（ビット5）と X（ビット3）は資料にある命令ごとの規則に従うが、例外が3つある。内部レジスタ MEMPTR を再現していないため、`BIT n,(HL)` では Y と X が 0 のままになる。繰り返しのブロック命令で PC から決まる Y/X と、`SCF`/`CCF` が直前の命令に依存する動作も再現していない。

### VDP（Video Display Processor）：TMS9918A（MSX1）

16 KB の VRAM と 8 個のコントロールレジスタを持つ。スクリーンモード 0〜3（テキスト 40 桁、グラフィック 1、グラフィック 2、マルチカラー）、サイズと拡大のあるスプライト描画、5 番目のスプライトとスプライト衝突のフラグ、VBlank 割り込みを実装している。

- 実装：`msx/vdp/vdp.py`、`msx/vdp/renderer.py`
- 既知の制限：
  - フレーム途中のレジスタ変更のタイミングは再現しない。ゲームがフレームの途中でスクリーンモード、色、スプライト設定を変えると、新しい状態がフレーム全体に適用される。実機では変更したスキャンラインから先だけに適用される。非公開のスプライトオーバーフロー動作も再現しない。
  - フレームスキップで飛ばしたフレームでは、5 番目のスプライトとスプライト衝突のステータスを更新しない。ターボモードで衝突をポーリングする ROM には、最後に描画したフレームの古いステータスが見える。

### VDP：Yamaha V9938（MSX2）

128 KB の VRAM、28 個のコントロールレジスタ、16 色のプログラマブルパレット（9 ビット GRB333）を持つ。スクリーンモード 0〜8（SCREEN 0〜SCREEN 8）、ハードウェアコマンド（HMMV、HMMM、HMMC、LMMV、LMMM、LMCM、LMMC、YMMM、LINE、PSET、POINT、SRCH）、水平ライン割り込み（R#19/R#23、IE1）を実装している。フレーム途中のレジスタとパレットの変更は、バンド単位のレンダラで処理する。

- 実装：`msx/vdp/v9938.py`、`msx/vdp/v9938_renderer.py`
- 既知の制限：
  - コマンドのタイミングは近似で、サイクル単位では正確でない。ビームに合わせた転送や、1 フレーム内での VRAM のダブルバッファは正しく再現しない。ブロックコマンドには 1 バイトまたは 1 ドットあたりのコストが 1 つずつあり、その値は実機の HB-F1XD で表示とスプライトをオンにして測った。表示やスプライトの VRAM アクセスが少ないときに速くなる分はモデル化していない。
  - GRAPHIC 6/7 のプレーナ VRAM インターリーブはモデル化していない。実機はこれらのモードで、偶数バイトと奇数バイトを 2 個の VRAM IC に分ける。エミュレータはリニアなアドレスを使う。結果が違うのは、プレーナモードで VRAM に書き、クリアせずに非プレーナモードへ切り替え、データを読み戻した場合だけ。
  - R#13 の点滅タイマーは 1 フレームに 1 回進む。このタイマーは、R#12 の色による TEXT2 の文字点滅と、GRAPHIC4〜7 の自動ページ切り替えを制御する。そのため、フレーム途中の R#13 書き込みはフレーム全体に効く。非公開の R#1 bit 2 によるライン単位の点滅タイミングは実装していない。R#9 の EO ビットは、実機の V9938 と同じく GRAPHIC4〜7 の偶数/奇数ページを自動で切り替える。本当のインターレース出力（縦の解像度が 2 倍）に必要な半走査線分のずらしは、描画を単純で速く保つため、意図的に対象外にしている。
  - 上の TMS9918A と同じフレームスキップの制限がある。

### PSG（Programmable Sound Generator）：AY-3-8910

16 個のレジスタ、3 つのトーンチャンネル、ノイズチャンネル、8 種類の波形を持つエンベロープジェネレータを備える。音量テーブルは実チップの測定値に基づく。理想化した幾何カーブではなく、ayumi がオシロスコープで測った AY-3-8910 の DAC カーブを使う。出力は 44100 Hz の PCM で、1 フレームあたり NTSC マシン（60 fps）では 735 サンプル、PAL マシン（50 fps）では 882 サンプル。

**フレーム内のソフトウェア PCM** の再現のため、音量レジスタへの書き込みごとにサイクル時刻を記録し、フレーム内の対応するサンプル位置で再生する。トーンジェネレータは出力サンプルごとに積分するので、周期 0 の超音波 PCM キャリアは帯域制限されたデューティ平均になり、0/最大のチョッピングにエイリアスしない。

- 実装：`msx/psg.py`

### キークリック：PPI ポートC bit 7

PPI ポートC の bit 7（I/O ポート 0xAA）は 1 ビット DAC を駆動する。bit が変わるたびに、出力は 0 か一定のレベルに切り替わる。エミュレータは変化ごとにサイクルを記録し、フレーム内の対応するサンプル位置に置く。Majutsushi カートリッジの DAC も同じ方法で動く。この仕組みで BIOS のキークリック音が鳴り、bit を切り替えて鳴らす 1 ビット PCM も再生される。PSG に対する音量の比は openMSX のマシン設定に合わせた。

- 実装：`msx/ppi.py`、`msx/dac.py`（キークリックと Majutsushi DAC が共有する書き込み駆動型の DAC チャンネル）
- 既知の制限：
  - ミキサーに直流成分を除去する段がない。実機ではクリックは変化のたびに減衰して無音に戻るが、本エミュレータでは bit を 1 のまま保持すると、次に変化するまで一定のオフセットとして残る。

### SCC：Konami SCC（Sound Creative Chip）

5 チャンネルのウェーブテーブル音源。出力は PSG と一緒にミックスする。SCC には 2 つのモードがある。

- **Compatible モード**（デフォルト）：32 サンプルの波形バンクが 4 つ（チャンネル 5 はチャンネル 4 の波形を使う）。チャンネルごとに 12 ビットの周波数と 4 ビットの音量を持つ。プログラムが 0x9000 に 0x3F を書くと KonamiSCC マッパーがこのモードを有効にし、レジスタが 0x9800 に現れる。
- **Plus モード**：5 チャンネルそれぞれに*独立した*波形バンクを持つ。周波数、音量、イネーブルのレジスタは別のオフセットに移る。後述の SCC-I カートリッジがこのモードを使い、`SCC.set_mode()` で選ぶ。

- 実装：`msx/scc.py`
- 既知の制限：
  - デフォーム/テストレジスタ（周波数逓倍の調整ビット、カウンタのリセット、ハードウェアによる波形の自動ローテーション）は実装していない。

### SCC-I カートリッジ（「SCC+」）

ゲーム ROM を持たない音源だけのカートリッジ。`--extension1`/`--extension2 scc_plus` で有効にする。有効化とスロットの仕組みは [`docs/README_extension_ja.md`](docs/README_extension_ja.md#scc-i-カートリッジ-scc) を参照。音源目的でこのカートリッジを使うフロッピー（FDD）の MSX2 タイトル向け。

カートリッジは物理 64 KB のバンク切り替え RAM を持ち、128 KB として見える。RAM は空で、ROM やデータファイルは一切読み込まない。バンクレジスタは bit 3 を無視するので、ブロック N はブロック N+8 と同じになる。これは実機で知られている改造（「[2 つの 64 KB バンクを接続する](http://bifi.msxnet.org/msxnet/tech/soundcartridge.html)」）を再現したもの。この改造により、工場出荷時の RAM 搭載パターン 2 種類のどちらにも 1 つのカートリッジで対応できる。このプロジェクトの対象 2 タイトルは、それぞれどちらか一方のパターンを前提にしている。

モードレジスタで、搭載している SCC チップの Compatible/Plus モードを選ぶ。ウィンドウごとの RAM 書き込み制御もある。

> **注記**：作者は実機の SCC-I（SCC+）カートリッジも対応ソフトウェアも持っていない。この実装は公開されている技術資料に基づくもので、実機では確認していない。

| 項目 | 詳細 |
| --- | --- |
| 実装 | `msx/mapper.py:SCCICart` |
| 有効化 | `--extension1`/`--extension2 scc_plus`（[`docs/README_extension_ja.md`](docs/README_extension_ja.md#scc-i-カートリッジ-scc)参照） |
| メモリマップ | 0x4000-0xBFFF に 4 × 8 KB のバンク切り替え RAM ウィンドウ。0xBFFE/0xBFFF にモードレジスタ |
| SCC レジスタウィンドウ | モードレジスタに応じて `0x9800-0x9FFF`（Compatible モード）または `0xB800-0xBFFF`（Plus モード） |

### FM-PAC：MSX-MUSIC カートリッジ（YM2413/OPLL）

追加で挿せるカートリッジ。`--extension1`/`--extension2 fmpac` で有効にする。有効化と必要な ROM は [`docs/README_extension_ja.md`](docs/README_extension_ja.md#fm-pac) を参照。64 KB のバンク切り替え ROM、openMSX 互換のマジック値でロックを外す 8 KB のバッテリーバックアップ SRAM、YM2413（OPLL）FM 音源チップを持つ。チップは 9 チャンネル 2 オペレータのメロディ合成（内蔵 15 音色とユーザー定義音色）、ADSR エンベロープ、リズムモード（バスドラム、スネア、タム、トップシンバル、ハイハット）を備える。出力は PSG/SCC と一緒にミックスする。

| 項目 | 詳細 |
| --- | --- |
| 実装 | `msx/fmpac.py`（カートリッジデバイス）、`msx/opll.py`（YM2413/OPLL チップ） |
| 有効化 | `--extension1`/`--extension2 fmpac`（[`docs/README_extension_ja.md`](docs/README_extension_ja.md#fm-pac)参照）。ROM は `roms/fmpac/fmpac.rom` |
| メモリマップ | 0x4000-0x7FFF に 64 KB ROM（16 KB × 4 バンク、バンクレジスタ 0x7FF7）。8 KB SRAM（openMSX 準拠の 0x1FFE バイト有効領域、0x5FFE/0x5FFF へのマジック値書き込みでアンロック）。メモリマップされた OPLL レジスタ（0x7FF4/0x7FF5）、イネーブルレジスタ（0x7FF6） |
| I/O ポート | 0x7C/0x7D、イネーブルレジスタの bit 0 でゲート |
| SRAM 永続化 | `saves/sram/fmpac.sram`。起動時にロード、終了時に保存 |
| OPLL 音源合成 | emu2413 v1.5.9 の忠実移植：対数ドメイン合成（log-sin + exp テーブル）、キースケール付きの実機エンベロープレートテーブル、AM/PM LFO、YM2413 音色 ROM、実機のショートノイズ/LFSR タップに基づくリズムモード（レジスタ 0x0E：バスドラム、スネア、タム、トップシンバル、ハイハット） |

- 既知の制限：
  - 出力レートの変換には、emu2413 の窓付き sinc リサンプラではなく、積算平均による間引き（チップ clk/72 → 44100 Hz）を使う。残るイメージングはアナログ風のローパスで取り除く。
  - 音色セットは YM2413 のものだけ（VRC7、YMF281 のバンクはない）。
  - チャンネルマスクとステレオパンはない。

### Sony HBI-J1：漢字ROM + MSX-JEワードプロセッサ

追加で挿せるカートリッジ。`--extension1`/`--extension2 hbi_j1` で有効にする。有効化、必要な ROM、ライセンスは [`docs/README_extension_ja.md`](docs/README_extension_ja.md#sony-hbi-j1) を参照。カートリッジは 2 つの部分からなる。

- JIS 漢字フォント ROM の I/O デバイス。ポート `0xD8-0xDB` にあり、スロット上の位置は持たない。
- サブスロットを 2 つ持つ*拡張*プライマリスロット。Halnote マッパー方式の MSX-JE ワープロ ROM（16 KB のバッテリーバックアップ SRAM 付き）と、フラットな漢字ドライバ兼 BASIC 拡張 ROM が入る。どのスロットになるかは `--extension1`/`--extension2` で決まる。

標準的な MSX2 本体に挿すカートリッジなので、この拡張スロットは本体側のスロット 3 の拡張と共存する。

作者はこのカートリッジの実物を持っており、ROM をダンプした。SHA1 は openMSX の `Sony_HBI-J1.xml` と一致する。この実装は、資料だけでなく実機を対象にしている。

| 項目 | 詳細 |
| --- | --- |
| 実装 | `msx/kanji.py`（漢字ROMデバイス）、`msx/mapper.py:HalnoteMapper`（MSX-JEカートリッジ）、`msx/machine_loader.py`（拡張オーバーレイの配線） |
| 有効化 | `--extension1`/`--extension2 hbi_j1`（[`docs/README_extension_ja.md`](docs/README_extension_ja.md#sony-hbi-j1)参照）。ROM は `roms/hbi_j1/` |
| 漢字ROM I/O | ポート `0xD8-0xDB`：bit 1 でJISレベル（1/2）を選択、bit 0 で列/行書き込みとデータ読み出しを区別。5ビットの読み出しカウンタが読み出しごとに自動インクリメントし、32回読み出すごとに同じ文字の先頭バイトへラップする |
| サブスロット0 | `HalnoteMapper`：1 MB ROM（128 × 8 KBバンク）、`0x0000-0x3FFF` に16 KB SRAM（バンク0レジスタのbit 7）、`0x7000-0x7FFF` をシャドウするJIS2辞書サブマッパー（バンク1レジスタのbit 7） |
| サブスロット1 | `0x4000-0xBFFF` にフラット32 KB漢字ドライバ+BASIC ROM。`0x0000-0x3FFF`/`0xC000-0xFFFF` はオープンバス |
| SRAM 永続化 | `saves/sram/hbi-j1_msx-je.sram`。起動時にロード、終了時に保存 |
| 既知の制限 | アドレスを書き換えずに漢字 ROM を 32 回続けて読んだ後の実機の動作は確認していない。この実装は openMSX の動作（同じ文字のバイト 0 に戻る）を正解として扱う |

### プリンタ：Sony HBP-F1 / HBP-F1C

MSX のパラレルプリンタポートと、そこに接続できる 2 つの機器。受け取ったバイトをそのまま記録するロガー（`--printer-log`）と、受け取った内容をページ画像に描画する Sony HBP-F1/HBP-F1C プリンタのエミュレーション（`--printer-out`）がある。どちらも指定しない場合、ポートは何も接続されていないコネクタ（BUSY）として読めるので、BIOS の印字はプリンタのないマシンと同じ動作になる。

エミュレートしているプリンタは、HBP-F1C の取扱説明書で公開されているコントロールコードを、機能単位で実装したもの。プリンタの完全なエミュレーションではない。作者は HBP-F1C の実機を持っているが、出力を実機の印字とドット単位では比べていない。

| 項目 | 詳細 |
| --- | --- |
| 実装 | `msx/printer.py`（プリンタポート、ロガー）、`msx/printer_hbpf1.py`（HBP-F1/HBP-F1C プリンタ） |
| ポート | `0x91` への書き込みでデータをラッチし、`0x90` の bit 0（STROBE）の立ち下がりでプリンタに渡す。`0x90` の読み出しは `0xFD`（レディ）か `0xFF`（ビジー、またはプリンタなし） |
| 出力 | PNG のみ。改ページごとに、180dpi で 1586 × 1980 ドット（ヘッドの 1 行の幅 × 11 インチのページ）の 1 ページを、8 色パレットの `page-001.png`、`page-002.png` … として書き出す。描画しないコントロールコードは `unsupported.log` に 1 回ごとに 1 行と、コード別の回数を記録する。ソフトが実際に使っている未実装機能がわかる |
| フォント | 実機のプリンタは本体に内蔵したフォントで印字する。この実装はプリンタのフォント ROM を持たず、ANK とグラフィック文字には MSX メイン ROM のフォントを、漢字には漢字 ROM のフォントデータ（`--extension1`/`--extension2 hbi_j1` で接続する HBI-J1 のもの）を使う。字形をプリンタの文字の大きさに拡大して描くので、実機の印字とは見た目が異なる。漢字 ROM がない場合、漢字は空白になる |
| 実装済み | パイカ／エリート／コンデンスの ANK。漢字モード（JIS 漢字、`00 xx` 形式の半角、漢字ピッチ）。CR、LF、FF、逆方向改行、改行幅。左右マージン。横倍と縦倍。白抜き／斜体／太字の飾り文字。下線。7 色のカラー印字（ESC C）。ドットスペース、文字の繰り返し、SOH によるグラフィック文字、プリンタリセット |
| 未実装 | フォント選択（ESC p@/A/B/\`/a/b/Z）。縦書き（ESC t は横書きで印字する。ESC h1、ESC q）。はがき印字モード（STX K）。外字登録（ESC +、SUB 2）。ビットイメージ（ESC S/I/J/V/W/U、ESC D/M）とドットアドレッシング（ESC F）。プロポーショナル、上付き、下付きの字形（パイカで印字する）。強調印字（ESC !）。タブ（HT、VT、ESC ( ) 2）。ページ長（GS…RS）と VFU（US）。CAN。テスト印字（ESC CT）。半角文字漢字コード 0x2921-0x2B7E。カラー/モノクロ切り換えスイッチ。PNG 以外の出力。これらはすべて、説明書どおりの長さのパラメータごと読み飛ばし、ログに記録する |
| 既知の制限 | インクの色は画面表示用の近似値で、重ね打ちしたときの混色（例：黄色 + 紫 = 黒）は確認していないモデルになっている。カラーリボンを入手できず、検証できなかったため。飾り文字と下線のドットパターンはこの実装独自のもので、説明書には載っていない。印字時間は再現しておらず、プリンタは常にレディになる |

### オーディオ出力フィルタ

実機 MSX の音声出力の RC フィルタを模した、アナログ風の出力ローパスフィルタ（2 極バターワース）。ミックス後の PSG/SCC/DAC/OPLL/キークリックの信号から、残ったイメージングとエイリアシングを取り除く。

- 実装：`msx/audio_filter.py`

### PPI（Programmable Peripheral Interface）：Intel i8255

スロット選択レジスタ（ポート 0xA8）、11 行 × 8 ビットの MSX キーボードマトリクス（ポート 0xA9）、行選択（ポート 0xAA）を持つ。書き込み専用の制御ポート 0xAB を読んだときの値は、マシンの PPI チップによって違う。マシンごとの `control_read` 設定で、次のどれかを選ぶ。

- `control`（デフォルト）：最後のモード設定値。CMOS 82C55A と同じ。
- `open_bus`：0xFF。S1985 搭載マシンと同じ。実機 HB-F1XD で確認した。
- `bus_latch`：最後の PPI アクセスでラッチしたバイト。`hb_10` と `hb_10_64k` の S3527 と同じ。実機 HB-10 で確認した。

- 実装：`msx/ppi.py`
- 既知の制限：カセットインターフェース（ポート 0xAA のビット 4〜5）と CAPS LED（ビット 6）は実装していない。ビット 7 はキークリックを駆動する（「キークリック：PPI ポートC bit 7」を参照）。

### メモリバス / スロットシステム

MSX1 は 4 ページ × 4 スロットで振り分ける。スロット 0 に BIOS ROM、スロット 1 にカートリッジ、スロット 2 に追加のカートリッジ、スロット 3 に 32 KB の RAM が入る。MSX2 ではプライマリスロット 3 が 4 つのセカンダリスロットに拡張され、サブスロット 3-0 にサブ ROM、3-2 に RAM マッパーが入る。

`--extension1`/`--extension2` でプライマリスロット 1 とスロット 2 もそれぞれ拡張できる。この拡張はスロット 3 の拡張と共存し、それぞれが自分のセカンダリスロットレジスタを持つ。各スロットに何を入れられるか、どの拡張がスロットを拡張するかは、[`docs/README_extension_ja.md` の「スロットモデル」節](docs/README_extension_ja.md#スロットモデル)を参照。同じ ROM や拡張 id を両方のカートリッジスロットに指定すると、起動時にエラーになる。

| 項目 | 詳細 |
| --- | --- |
| 実装 | `msx/memory.py` |
| アドレス空間 | フラット 64 KB（0x0000–0xFFFF）、4 つの 16 KB ページ |
| スロット 0 ページ 0–1 | BIOS ROM（読み取り専用、0x0000–0x7FFF） |
| スロット 0 ページ 2 | ロゴ ROM（`cbios_logo_msx1.rom`）を 0x8000–0xBFFF にマップ。BIOS と並べてマシン YAML の `pages: [2]` エントリとして宣言する。存在しない場合は 0xFF を返す |
| スロット 0 ページ 3 | 一部の機種（例：`hb_10`）で 16 KB SRAM を 0xC000–0xFFFF にマップする。電源断で消えるワーク RAM で、カートリッジ SRAM のようなバッテリーバックアップや保存はない。BIOS と並べてマシン YAML の `ram: {size_kb: 16, pages: [3]}` エントリとして宣言する |
| スロット 1 | マッパー経由のカートリッジ ROM。未装着の場合はオープンバス（読み出しは 0xFF、書き込みは無視）。`--extension1` によりサブスロットに拡張。詳細は[`docs/README_extension_ja.md`](docs/README_extension_ja.md#スロットモデル)を参照 |
| スロット 2 | `_mapper2` 経由の第 2 カートリッジ ROM。未装着の場合はオープンバス（読み出しは 0xFF、書き込みは無視）。`--extension2` によりサブスロットに拡張。詳細は[`docs/README_extension_ja.md`](docs/README_extension_ja.md#スロットモデル)を参照 |
| スロット 3（MSX1） | 既定はページ 2–3（0x8000–0xFFFF）の 32 KB RAM。サイズは機種ごとに設定（`hb_10_64k` は全ページの 64 KB、`hb_10` は空きでオープンバス） |
| スロット 3（MSX2） | 4 つのセカンダリスロットに拡張。3-0 にサブ ROM、3-2 に RAM マッパー |

### RAM マッパー

メイン RAM を 16 KB 単位のセグメントで扱う。セグメントレジスタはポート 0xFC〜0xFF。標準の MSX2 マシンは 128 KB（8 セグメント）。`hb_f1xd_256k` は 256 KB で、[`docs/README_extension_ja.md`](docs/README_extension_ja.md) の 512 KB の拡張を使うとさらに増える。

- 実装：`msx/ram_mapper.py`

### RTC：RP5C01

ポート 0xB4〜0xB5 のリアルタイムクロック。バッテリーバックアップの CMOS RAM（ブロック 2/3、計 26 ニブル）と、12/24 時間モードの切り替え（レジスタ 10 の bit 0）も持つ。

- 実装：`msx/rtc.py`
- CMOS RAM の保存先：`saves/sram/rtc_<machine_id>.sram`。マシンごとに 1 ファイル。カートリッジの SRAM と違い、実機は 1 台に 1 個の RTC と電池を持つため。起動時に読み込み、終了時に保存する
- 既知の制限：時計はホストのシステム時刻を返す。アラームとタイマー出力はない。

### カートリッジマッパー

フラット（バンク切り替えなし）、ASCII8、ASCII16、Konami、KonamiSCC、Majutsushi（DAC）、ASCII8SRAM2/8、ASCII16SRAM2/8、R-Type、固定配置マッパー（Page2/0x4000/0x8000）、KoeiSRAM32、GameMaster2 に対応する。マッパーは SHA1 ベースの ROM データベースで自動的に選ぶ。上書きするには `--mapper`/`--mapper1`（スロット 1）か `--mapper2`（スロット 2）を使う。どちらも同じタイプ一覧を受け付ける。

| マッパー | 説明 |
| --- | --- |
| `FlatMapper` | バンク切り替えなし。ROM を 32 KB カートリッジ領域全体にミラー |
| `Ascii8Mapper` | 4 つの 8 KB ウィンドウ。コントロールレジスタは 0x6000–0x7FFF |
| `Ascii16Mapper` | 2 つの 16 KB ウィンドウ。コントロールレジスタは 0x6000–0x7FFF |
| `KonamiMapper` | 3 つの 8 KB ウィンドウ。バンクレジスタはウィンドウベースアドレスへの書き込みで選択 |
| `KonamiSCCMapper` | Konami と同じだが、0x9000 への 0x3F 書き込みで SCC を有効化 |
| `MajutsushiMapper` | ASCII8 派生型。0x9000 への書き込みで DAC 出力 |
| `ASCII8SRAM2`、`ASCII8SRAM8` | ASCII8 + 2 KB または 8 KB バッテリーバックアップ SRAM |
| `ASCII16SRAM2`、`ASCII16SRAM8` | ASCII16 + 2 KB または 8 KB バッテリーバックアップ SRAM |
| `RTypeMapper` | 8 KB ウィンドウ。バンク 0 は ROM 先頭に固定 |
| `FixedPageMapper` | バンク切り替えなし。ROM は固定ベースアドレス（データベース上の `Page2`、`0x4000`、`0x8000`）にのみ存在し、カートリッジ領域の残りはオープンバス |
| `KoeiSRAM32Mapper` | ASCII8 + 32 KB バッテリーバックアップ SRAM。SRAM 選択可能ウィンドウに 0x4000 を追加 |
| `GameMaster2Mapper` | Konami 系 128 KB ROM + 8 KB バッテリーバックアップ SRAM。各バンクレジスタで ROM ページまたは 4 KB SRAM 半分（8 KB ウィンドウ内でミラー）を選択、SRAM 書き込みは 0xB000–0xBFFF |

スロット 1 とスロット 2 はそれぞれ独立にマッパーを選ぶ（`--mapper`/`--mapper1` と `--mapper2`、どちらもデフォルトは自動）。SRAM 付きのタイプも含め、両スロットは同じマッパーに対応する。各スロットの SRAM は、それぞれ `saves/sram/<カートリッジの SHA1>.sram` に保存する。

`KonamiSCC` はどちらのスロットでも実際の Konami-SCC チップを作る。実機の Konami-SCC カートリッジは、Konami のスロットコンバータなどを使えばどちらのスロットにも挿せるため。ただし、スロット 1 と 2 はマシンに 1 つだけの `SCC` チップを共有する。次の場合は `MachineLoadError` になる。

- 両方のスロットが `KonamiSCC` になる。
- 一方のスロットが `KonamiSCC` で、もう一方が `--extensionN scc_plus` を選んでいる。

### フロッピーディスクドライブ（WD2793）

汎用の FDC（フロッピーディスクコントローラ）層は、ディスクイメージ、ドライブ、コントローラ、接続方式の 4 つの部分からなる。この層の上に、Sony HB-F1XD と同じ WD2793 コントローラと Sony/Philips 接続方式を実装している。レジスタはスロット 3 のサブスロット 0 にメモリマップされる。`--fdd1` で `*.dsk` イメージをマウントする。Disk BASIC の起動、`CALL FORMAT`、ファイルの読み書き（終了時にファイルへ書き戻す）に対応する。実行中のディスク交換は、F6/Shift+F6（[エミュレータ内のキー操作](#エミュレータ内のキー操作)参照）かデバッガ REPL（`fdd1`/`fdd2`）でできる。

- 実装：`msx/fdc/wd2793.py`、`msx/fdc/interface.py`
- 既知の制限：
  - タイミングのモデルがなく、コマンドは即座に終わる。シーク/ステップの速度、回転に基づく ID の検索、ヘッドロードやシーク後ベリファイの待ちはない。
  - READ TRACK は中身のないスタブ。
  - WRITE TRACK は送られたデータを捨て、バイト数だけでフォーマットする。ギャップバイトやアドレスマークは解釈しない。

### フロッピーディスクドライブ（TC8566AF）

同じ汎用 FDC 層の上にある、2 つ目のコントローラと接続方式の組み合わせ。Panasonic FS-A1F（`--machine fs_a1f`）と同じもの。レジスタは実機の FS-A1F と同じくスロット 3 のサブスロット 2 にメモリマップされる（RAM はサブスロット 0、SUB ROM はサブスロット 1）。レジスタは Main Status Register、Data Register、2 つのコントロールレジスタ。WD2793 と違い、TRACK/SECTOR レジスタを直接読み書きできない。`*.dsk` イメージは同じく `--fdd1` でマウントする。

SPECIFY、SENSE INTERRUPT STATUS、SENSE DEVICE STATUS、RECALIBRATE、SEEK、READ DATA、WRITE DATA、FORMAT を実装している。MSX の DISK ROM の起動とセクタ入出力、`CALL FORMAT` にはこれで足りる。

- 実装：`msx/fdc/tc8566af.py`、`msx/fdc/interface.py`
- 既知の制限：
  - タイミングのモデルはない（WD2793 と同じ方針）。
  - 非 DMA モードだけ。DRQ2/-DACK2/DMATC ピンはモデル化していない。
  - READ DELETED DATA、WRITE DELETED DATA、READ DIAGNOSTIC、READ ID、SCAN は実装していない。Disk BASIC の起動とセクタ入出力には使わない。
  - WD2793 の WRITE TRACK と同じく、FORMAT は送られたディスクリプタのバイトを捨てる。現在のトラックとサイドの全セクタを空にするだけで、ギャップ、同期、ID、CRC のバイトは書かない。
  - 同梱のマシン設定はどれもドライブ 1 台なので、複数ドライブでの動作は確認していない。実行中のディスク交換で転送を中断する処理は、ドライブ 0 だけを正しく追う。対象ドライブは、Control Register 0 の選択状態ではなく、各コマンドのパラメータバイトから決める。どちらも、実際の DISK ROM の通信で見られる 1 ドライブの動作とは一致する。

### ROM データベース

SHA1 でタイトルを引き、ゲームタイトルとマッパーを自動で決める。

| 項目 | 詳細 |
| --- | --- |
| 実装 | `msx/romdb.py` |
| データソース | [openMSX ソフトウェアデータベース](https://github.com/openMSX/openMSX/blob/master/share/softwaredb.xml)（参照元。すべてのエントリは独自に収集した事実情報） |
| フォールバック | ROM がデータベースにない場合は、タイトルなしで起動し、`--mapper auto` の推定でマッパーを選ぶ |

### I/O バス

ポートを範囲で登録する。読み書きは登録したハンドラに渡す。

- 実装：`msx/io.py`

### キーボード / ジョイスティック入力

| 項目 | 詳細 |
| --- | --- |
| キーボード | `msx/input.py`。MSX テクニカルハンドブック準拠の 11 行 × 8 ビット、アクティブロー |
| 物理ジョイスティック | `msx/joystick.py`。SDL2 GameController API（優先）と生ジョイスティックのフォールバック。ホットプラグ対応 |
| キーボードエミュレーション | WASD = Joy1 方向。Z/X または ,/. = トリガ A/B。矢印キーも対応。各機能のキーは `py_emulator.yaml` の `keyboard_joystick.buttons` で上書き可能 |

- 既知の制限：
  - MSX のテンキー（マトリクスの行 9〜10）に割り当てたホストのキーはない。
  - ダイオードのないマトリクスで起きるキーゴーストは再現しない。
  - ジョイスティックの Type A / Type B（ピン 7 の第 2 トリガ）の判別はなく、どのポートも Type B として動く。
  - macOS で JIS キーボードを使うと、起動直後に SDL2 が配列を US と誤認することがある。`cbios_msx2_jp` などの JIS マシンで記号キーがおかしいときは、⌘+Space で入力ソースを一度切り替えて戻すと、たいてい直る。SDL2/macOS 側の問題で、このプロジェクトでは制御できない。

### マウス

ホストのマウスに追従する MSX マウス（任意）。ジョイスティックポートに、そのポートの物理ジョイスティックやキーボードジョイスティックの代わりにつながる。実機のプロトコルを再現している。MSX はポートの pin 8 をトグルする。pin 8 はふだん、常に High の PSG のストローブ出力になっている。トグルのたびに、符号付きの相対移動量を 4 ビットずつ送る。順番は X 上位、X 下位、Y 上位、Y 下位。続けて移動量 0 のサイクルを送るので、MSX BIOS のマウス判定はトラックボールではなくマウスと判断する。左右のボタンはポートのトリガ A/B につながる。

| 項目 | 詳細 |
| --- | --- |
| 実装 | `msx/mouse.py`（プロトコル状態機械）、`msx/psg.py`（レジスタ 14/15 の配線） |
| 有効化 | `--mouse[=1\|2]`（CLI）または `py_emulator.yaml` の `mouse.enabled`/`mouse.port`。CLI フラグは常に設定ファイルより優先 |
| ホスト入力 | SDL の relative-mouse モード（カーソルは非表示になりウィンドウにロック）。移動量はウィンドウの整数 `--scale` 倍率でスケーリング |

- 既知の制限：
  - NYYRIKKI/Prodatron の拡張プロトコル（高解像度、ホイール、追加ボタン）は実装していない。
  - 絶対座標は扱わない。
  - 実機のマウスにある、ボタンを押したまま起動するジョイスティックモードは実装していない。

### SDL2 フロントエンド

ウィンドウは全マシン・全モードで 768×636（256×212 の 3 倍）に固定。描画するフレームは常に 212 ラインで、192 ラインの SCREEN モードは上下にボーダーを足して中央に置くので、R#9 の LN に関係なく 4:3 の CRT の比率が保たれる。SCREEN 6/7（横 512 ドット）は同じウィンドウ幅に縮小してドットの比率を保つ。

ほかに、ハードウェアパレット、アナログ風ローパスを通した 44100 Hz モノラル音声、フルスクリーン切り替え、スクリーンショット、ステートセーブ/ロード、一時停止、早送り、ミュート、リセットがある。ウィンドウタイトルには fps、`[PAUSED]`/`[FF]`/`[MUTE]`、ドライブ A のディスクを表示する。早送り中は音声を再生しない。自動フレームスキップは[パフォーマンス](#パフォーマンス)を参照。

- 実装：`frontend/sdl2_frontend.py`
- 既知の制限：音声デバイスが開いていないとき（ヘッドレスや `--benchmark` の実行など）は PSG/SCC/OPLL の合成が呼ばれないので、その間はエンベロープや LFO の状態が進まない。

### ステートセーブ/ロード

ハードウェアの状態をまるごと標準ライブラリの JSON に保存する。CPU、RAM、VDP、PSG、SCC、FM-PAC/OPLL、マッパーのバンク、FDC のレジスタと処理段階、マウント中のディスクの識別情報を含む。セーブのたびに PNG のスクリーンショットも保存し、すぐ再開できるよう `saves/states/latest.*` のシンボリックリンクを更新する。マウント中の `.dsk` はパス、サイズ、SHA1 で記録する。ロード時にディスクが一致しなければ、そのまま続けずにエラーにする。

- 実装：`msx/state.py`

### インタラクティブデバッガ

Ctrl+C かブレークポイントで開く REPL。ブレークポイントとウォッチポイント、ステップ実行、レジスタと VRAM のダンプ、逆アセンブル、VDP トレース、マッパートレース、スロットの表示、フロッピーの交換（`fdd1`/`fdd2 [FILE|-]`）がある。全コマンドは [`docs/debugger_ja.md`](docs/debugger_ja.md) を参照。

- 実装：`msx/debugger/`

### デバッグツール

必要なときに有効にする構造化ログ、CPU 命令トレース、I/O ポートトレース、ハング検出。

- 実装：`msx/diagnostics/`

---

## 仕様駆動アーキテクチャ

このプロジェクトは [Claude Code](https://claude.ai/code) と [OpenSpec](https://openspec.dev/) を使って実装した。

v2.5.0 からは、振る舞いに注目した 2 つ目の仕様の層（`allium/*.allium`）として [Allium](https://juxt.github.io/allium/) も使っている。この層で、各コンポーネントの実装が仕様からずれていないかを確かめる。

`openspec/` と `allium/` の仕様はまだ公開していない。仕様は `openspec/specs/<spec-id>/spec.md` にコンポーネントごとのディレクトリで置き、ID にはドメインの接頭辞が付く（`cpu-z80`、`vdp-tms9918a`、`audio-psg`、`fdc-wd2793` など）。各ファイルには、自然言語の要件と具体的な WHEN/THEN のシナリオを交互に書く。次は `vdp-tms9918a` の冒頭。

```markdown
### Requirement: VRAM
The VDP SHALL maintain 16 KB of video RAM as a `bytearray` of length 0x4000.
All VRAM addresses SHALL be masked to 14 bits (`& 0x3FFF`).

#### Scenario: VRAM initialises to zero
- **WHEN** a `VDP` instance is created
- **THEN** all 16384 bytes of VRAM are 0x00

#### Scenario: VRAM write and read back
- **WHEN** a byte is written to VRAM address 0x1234 via the data port
- **THEN** reading VRAM at 0x1234 directly returns the same byte
```

シナリオはそれぞれユニットテストに対応する。新しい機能や既存コンポーネントの変更では、先に仕様を変え、そのあと実装する。

---

## パフォーマンス

数値は `--benchmark` オプションで計測した。ヘッドレスで速度制限なし（`FrameTimer` の調整なし）に、コールドブートから指定フレーム数まで動かすので、BIOS の起動時間も含む。CPU のエミュレーションと、オフスクリーンバッファへの VDP 描画は、通常のプレイと同じく毎フレーム行う。ただし `--benchmark` は SDL ウィンドウを開かず、音声サンプルも作らず、テクスチャの転送もしない。実際のプレイは、下の数値より少し遅くなる。

（ランタイム、ゲーム）の組み合わせごとに 10000 フレームの計測を 10 回行い、最速と最遅を除いた 8 回の平均をスコアにした。

| プラットフォーム | ランタイム | ゲーム | 平均 FPS（`--benchmark`） | 60 fps 目標との比 |
| --- | --- | --- | --- | --- |
| Apple MacBook Pro（M5 Pro） | CPython 3.12.13 | MSX1: 沙羅曼蛇（CPU+TMS9918+SCC） | 292.67 | 約 4.9 倍 |
| Apple MacBook Pro（M5 Pro） | CPython 3.12.13 | MSX2: ドラゴンスレイヤー4（CPU+V9938+PSG） | 705.33 | 約 11.8 倍 |
| Apple MacBook Pro（M5 Pro） | CPython 3.12.13 | MSX2: ドラゴンスレイヤー英雄伝説（CPU+V9938+FDC+FMPAC） | 313.11 | 約 5.2 倍 |
| Apple MacBook Pro（M5 Pro） | PyPy 7.3.19（Python 3.10.16） | MSX1: 沙羅曼蛇（CPU+TMS9918+SCC） | 1226.14 | 約 20.4 倍 |
| Apple MacBook Pro（M5 Pro） | PyPy 7.3.19（Python 3.10.16） | MSX2: ドラゴンスレイヤー4（CPU+V9938+PSG） | 1412.82 | 約 23.5 倍 |
| Apple MacBook Pro（M5 Pro） | PyPy 7.3.19（Python 3.10.16） | MSX2: ドラゴンスレイヤー英雄伝説（CPU+V9938+FDC+FMPAC） | 1151.97 | 約 19.2 倍 |
| Raspberry Pi 5 | CPython 3.12.13 | MSX1: 沙羅曼蛇（CPU+TMS9918+SCC） | 76.25 | 約 1.3 倍 |
| Raspberry Pi 5 | CPython 3.12.13 | MSX2: ドラゴンスレイヤー4（CPU+V9938+PSG） | 191.72 | 約 3.2 倍 |
| Raspberry Pi 5 | CPython 3.12.13 | MSX2: ドラゴンスレイヤー英雄伝説（CPU+V9938+FDC+FMPAC） | 84.96 | 約 1.4 倍 |
| Raspberry Pi 5 | PyPy 7.3.19（Python 3.10.16） | MSX1: 沙羅曼蛇（CPU+TMS9918+SCC） | 353.01 | 約 5.9 倍 |
| Raspberry Pi 5 | PyPy 7.3.19（Python 3.10.16） | MSX2: ドラゴンスレイヤー4（CPU+V9938+PSG） | 472.98 | 約 7.9 倍 |
| Raspberry Pi 5 | PyPy 7.3.19（Python 3.10.16） | MSX2: ドラゴンスレイヤー英雄伝説（CPU+V9938+FDC+FMPAC） | 307.53 | 約 5.1 倍 |

計測したすべての組み合わせが 60 fps を上回った。余裕が最も小さいのは Raspberry Pi 5 の CPython で沙羅曼蛇を動かした場合で、約 1.3 倍。沙羅曼蛇（MSX1、KonamiSCC マッパー）は対象タイトルの中で描画と音声の負荷が最も重い。PyPy にすると、同じ組み合わせで約 5.9 倍になる。

Raspberry Pi 5 より遅いハードウェアや、もっと重いタイトルでは、60 fps を下回ることがある。そのときゲームは、出たフレームレートに比例してスローモーションになる。音声も劣化する（クリックや無音）。音声のサンプルはフレームごとに作るのに、音声デバイスは常に 44100 Hz で消費するため。PyPy3 はそのまま置き換えて使え、遅いハードウェアでの処理速度を大きく上げる。Raspberry Pi のような非力な環境で余裕を持たせたいなら PyPy を勧める。

PyPy の数値は、OS のスケジューリングや発熱による速度低下のためか、CPython より実行ごとのばらつきが大きい。目安として見てほしい。

### ベンチマーク推移

v0.1.0 から v2.7.5 までの平均 FPS（`--benchmark`）の推移を、プラットフォームとランタイムごとに示す。どのバージョンも上と同じコールドブートの方法で計測した。

![Apple MacBook Pro（M5 Pro）でのベンチマーク推移](assets/bench-history-m5pro.png)

![Raspberry Pi 5 でのベンチマーク推移](assets/bench-history-rpi5.png)

自動フレームスキップ（`--frame-skip auto`、デフォルト）は、間に合わなかったフレームで VDP のピクセル描画を省く。VBlank 割り込みは毎フレーム起きる。60 fps にあと少し届かないホストで、表示がなめらかになる。音質には効かず、60 fps に届かない環境では音切れが続く。無効にするには `--frame-skip none` を使う。

`--speed` は目標のフレームレートを変える。たとえば `--speed 2.0` は、十分に速いホストでゲームを 2 倍速で動かす。遅いホストの処理不足は補えず、遅いハードウェアでの音質もよくならない。

---

## インストール

必要なもの、インストール手順、BIOS の準備は [QUICK_START_ja.md](QUICK_START_ja.md) を参照。

---

## 使い方

### エミュレータの起動

```bash
# カートリッジあり（ROM データベースがマシンを選ぶ）
python . path/to/game.rom

# MSX1（日本）を明示指定
python . path/to/game.rom --machine cbios_msx1_jp

# MSX2（日本）を明示指定
python . path/to/game.rom --machine cbios_msx2_jp

# 2 倍速でエミュレーション
python . path/to/game.rom --speed 2.0

# ウィンドウ拡大率（デフォルト 3）：小さいディスプレイは 2、大きいディスプレイは 4
python . path/to/game.rom --scale 4

# デュアルカートリッジ（スロット 1 + スロット 2）
python . path/to/game1.rom --slot2 path/to/game2.rom

# 同じ内容を、位置引数の代わりに --slot1 を明示指定
python . --slot1 path/to/game1.rom --slot2 path/to/game2.rom

# Sony HB-F1XD にフロッピーをマウントして起動（Disk BASIC が起動）
python . --machine hb_f1xd --fdd1 path/to/disk.dsk

# CALL FORMAT でフォーマットするための空ディスク（720 KB）を作成
python tools/dskblank.py blank.dsk

# マッパーを明示指定（--mapper2 も同一のタイプ一覧を受け付ける）
python . path/to/game.rom --mapper KonamiSCC

# スロット 1 のゲームと合わせてスロット 2 に FM-PAC（MSX-MUSIC）を追加
python . path/to/game.rom --extension2 fmpac

# スロット 2 に SCC-I（SCC+）カートリッジを接続（カートリッジ ROM 引数なし）。
# フロッピーから起動
python . --machine hb_f1xd --extension2 scc_plus --fdd1 path/to/disk.dsk

# Sony HBI-J1（漢字ROM/MSX-JEカートリッジ、スロット2を拡張）を接続
python . --extension2 hbi_j1

# --extension1/--extension2 は完全に対称：どちらのスロットにも任意の拡張 id を
# 配置できる（片方はゲーム、もう片方は別の拡張、という組み合わせも可能）
python . path/to/game.rom --extension1 fmpac --extension2 hbi_j1

# 他の --extension1/--extension2 id（MSX-DOS2 + RAM 拡張、漢字フォント、
# View フォント）は docs/README_extension_ja.md に記載

# エミュレートした HBP-F1 プリンタで PNG に印字する（漢字には漢字 ROM が必要。
# ここでは HBI-J1 のもの）。ページと unsupported.log は prints/ に出る
python . --machine hb_f1xd --extension2 hbi_j1 --printer-out prints

# ソフトがプリンタポートに送るバイトをそのまま記録する
python . --machine hb_f1xd --printer-log printer.prn

# ホストのマウスで駆動する MSX マウスを Joy2（デフォルトポート）に接続
python . path/to/game.rom --mouse

# MSX マウスを Joy1 に接続
python . path/to/game.rom --mouse=1

# 最新のステートセーブから復帰
python . path/to/game.rom --resume

# 特定のステートファイルから復帰
python . path/to/game.rom --resume saves/states/game_20260605_120000.state

# デバッグログを有効化
python . path/to/game.rom --debug --log trace.log

# 起動時にブレークポイントを設定
python . path/to/game.rom --break-point C000,D000

# 300 フレームをヘッドレスで実行して VDP トレースを取得（SDL ウィンドウなし）
python . path/to/game.rom --count-frame 300 --vdp-trace --vdp-trace-out trace.log

# ベンチマーク：10000 フレーム（デフォルト）ヘッドレスで実行し、平均 FPS を表示
python . path/to/game.rom --benchmark

# 保存済みシーンから 30000 フレームのベンチマーク
python . path/to/game.rom --benchmark 30000 --resume saves/states/game_20260605_120000.state
```

### コマンドラインオプション

| オプション | デフォルト | 説明 |
| --- | --- | --- |
| `cartridge` | _（なし）_ | スロット 1 のカートリッジ ROM のパス。`--slot1 cartridge --mapper1 auto` の省略形。`--slot1`/`--extension1` とは併用できない |
| `--slot1 ROM1`、`--slot ROM1` | _（なし）_ | スロット 1 のカートリッジ ROM のパス。位置引数 `cartridge` の代わりに使う。位置引数とも `--extension1` とも併用できない。`--slot` は `--slot1` の別名（`--mapper` が `--mapper1` の別名なのと同じ） |
| `--machine MACHINE_ID` | _（自動）_ | マシン設定の ID（例：`cbios_msx2_jp`）。省略すると ROM データベースで自動的に選ぶ |
| `--speed FLOAT` | `1.0` | エミュレーション速度の倍率 |
| `--scale N` | `3` | 256×212 ベース解像度に対する整数の拡大率（例：小さいディスプレイなら `2`、大きいディスプレイなら `4`） |
| `--mapper TYPE`、`--mapper1 TYPE` | `auto` | スロット 1 マッパー（`--mapper` は `--mapper1` の別名）：`auto`、`Mirrored`、`Normal`、`ASCII8`、`ASCII16`、`Konami`、`KonamiSCC`、`Majutsushi`、`ASCII8SRAM2`、`ASCII8SRAM8`、`ASCII16SRAM2`、`ASCII16SRAM8`、`R-Type`、`Page2`、`0x4000`、`0x8000`、`KoeiSRAM32`、`GameMaster2` |
| `--slot2 ROM2` | _（なし）_ | スロット 2 カートリッジ ROM のパス。`--extension2` と併用不可 |
| `--mapper2 TYPE` | `auto` | スロット 2 のマッパー。タイプ一覧は `--mapper1` と同じで、KonamiSCC と SRAM 付きのタイプもすべて含む。スロット 2 の SRAM もスロット 1 と同じく保存する。KonamiSCC はマシンに 1 つの SCC チップをスロット 1 と共有するので、スロット 1 も KonamiSCC になる場合や、スロット 1 が `--extension1 scc_plus` を選んでいる場合はエラーになる |
| `--extension1 ID`、`--extension2 ID`、`--extension ID` | _（なし）_ | スロット 1/2 に拡張デバイスを追加する。id の一覧と説明は [`docs/README_extension_ja.md`](docs/README_extension_ja.md) を参照。`--extension1` は位置引数 `cartridge`、`--slot1`、`--mapper1` と、`--extension2` は `--slot2`、`--mapper2` と併用できない。同じ拡張 id（`--slot1`/`--slot2` なら同じ ROM）を両方のスロットに指定すると、起動時にエラーになる。`none` を指定すると、`py_emulator.yaml` がそのスロットに拡張を設定していても拡張なしになり、そのスロットの `--slotN`/`--mapperN` を CLI で使えるようになる。`--extension` は `--extension2` の別名（`--extension1`/`--extension2` ができる前と同じ意味） |
| `--fdd1 DSK [DSK ...]` | _（なし）_ | ドライブ A 用のフロッピー `*.dsk` イメージ（FDC 搭載機、例：`hb_f1xd`）。起動時に先頭のイメージをマウントし、F6 で一覧を順に切り替える。書き込みは終了時にファイルへ反映。位置引数の `cartridge` は `--fdd1` より前に書くか `--slot1` を使うこと（後ろに書くとディスクイメージとして読まれる） |
| `--fdd2 DSK` | _（なし）_ | ドライブ B にマウントするフロッピーの `*.dsk` イメージ（ドライブが 2 台あるマシンだけ） |
| `--resume [FILE]` | _（なし）_ | `saves/states/latest.state`（引数なし）か、指定した `.state` ファイルから再開する |
| `--mouse [1\|2]` | _（なし）_ | MSX マウスをジョイスティックポートにつなぐ（引数なしは Joy2）。`py_emulator.yaml` の `mouse` 設定より優先する。ヘッドレス実行では無視する |
| `--frame-skip MODE` | `auto` | フレームスキップ。`auto` は遅れたフレームの VDP 描画を省く。`none` は無効にする |
| `--debug` | オフ | 構造化診断ログを stderr に出力 |
| `--log FILE` | _（なし）_ | 診断ログをファイルに書き出す（`--debug` が必要） |
| `--vdp-trace` | オフ | VDP レジスタ書き込みトレースを stdout に出力 |
| `--vdp-trace-out FILE` | stdout | VDP トレースを FILE に書き出す |
| `--mapper-trace` | オフ | カートリッジマッパーのバンク切り替えトレースを出力（MAP\_BANK レコード） |
| `--mapper-trace-out FILE` | stdout | マッパートレースを FILE に書き出す |
| `--printer-log FILE` | _（なし）_ | プリンタポート（I/O 0x90/0x91）にプリンタを接続し、受け取ったバイトを無変換で FILE に書き出す。指定しない場合、プリンタは未接続として扱われる |
| `--printer-out DIR` | _（なし）_ | エミュレートした Sony HBP-F1/HBP-F1C プリンタを接続し、受け取った内容を 180dpi のカラー PNG ページ（`page-001.png` など）として DIR に描画する（DIR がなければ作る）。ANK は MSX メイン ROM のフォント、漢字は漢字 ROM（例：`--extension2 hbi_j1`）で描く。漢字 ROM がない場合、漢字は空白になる。実装していないコントロールコードは回数付きで `DIR/unsupported.log` に記録される。詳細は「プリンタ」節を参照。`--printer-log` とは同時に指定できない |
| `--count-frame N` | _（なし）_ | N フレームをヘッドレスで実行して終了（SDL ウィンドウなし） |
| `--benchmark [FRAMES]` | _（なし）_ | FRAMES フレーム（デフォルト 10000）をヘッドレス・速度制限なしで動かし、平均 FPS を表示する。`--resume` と組み合わせると、保存した場面から計測できる。`--count-frame` とは併用できない |
| `--break-point ADDRS` | _（なし）_ | カンマ区切りの 16 進ブレークポイントアドレス（最大 4 個） |
| `--watch-point ADDRS` | _（なし）_ | ウォッチポイントのアドレス（最大 4 個）。各アドレスの後ろに `,r`（読み取り）、`,w`（書き込み）、`,rw`（両方）を付けられる（省略時は `rw`）。例：`C000,rw,D000,r` |
| `--rpc` | オフ | 内蔵の Unix ソケット JSON-RPC 制御サーバを有効にする（ウィンドウを開く通常の実行のみ）。[リモート制御](#リモート制御socket-rpc--mcp)を参照 |
| `--rpc-socket PATH` | `/tmp/py_msx_emu.sock` | `--rpc` の Unix ソケットのパス（`--rpc` がないと効かない） |

### 設定ファイル（`py_emulator.yaml`）

よく使う設定は、リポジトリのルートに `py_emulator.yaml` を置いて書いておける。
毎回フラグを指定しなくてよくなる。同梱の `py_emulator.example.yaml` を
`py_emulator.yaml` にコピーして編集する。

```bash
cp py_emulator.example.yaml py_emulator.yaml
```

値の優先順位は **組み込みのデフォルト < `py_emulator.yaml` < コマンドライン引数**。
指定したフラグが常に勝ち、ファイルは指定しなかった項目だけを補う。ファイルがなけ
れば動作は変わらない。このファイルは git の管理外なので、ローカルの設定はバージ
ョン管理に入らない。

```yaml
machine: cbios_msx2_jp   # デフォルトのマシン ID（未設定なら自動判定）
speed: 1.0               # エミュレーション速度倍率
scale: 3                 # 256x212 ベースに対するウィンドウ整数拡大率
# slot1: roms/slot1.rom  # スロット 1 カートリッジ ROM のパス（未設定なら位置引数のカートリッジを使用）
# slot2: roms/slot2.rom  # スロット 2 カートリッジ ROM のパス（未設定ならスロット 2 なし）
# mapper1/mapper2 は CLI 専用（--mapper / --mapper1 / --mapper2）；ここでは設定不可
# extension1: fmpac        # スロット 1 の拡張を重ねる -- 詳細は docs/README_extension_ja.md
# extension2: hbi_j1       # スロット 2 の拡張を重ねる（'extension' も extension2 のレガシー別名として使える）
# fdd1: [disks/a.dsk, disks/b.dsk]  # ドライブ A のディスク一覧（1 つのパスまたはリスト）；F6 で順に切り替え
frame_skip: true         # true = auto（デフォルト）、false = none（無効化）

rpc:
  enabled: false
  socket: /tmp/py_msx_emu.sock

joystick:
  turbo_hz: 20           # 連射レート（round(60 / turbo_hz) フレーム）
  buttons:               # MSX 機能ごとの SDL GameController ボタン
    up: dpup
    down: dpdown
    left: dpleft
    right: dpright
    trigger_a: a
    trigger_b: b
    turbo_a: y           # Trigger A の連射
    turbo_b: x           # Trigger B の連射

keyboard_joystick:
  buttons: {}             # Joy1 のキー上書き（例：{ trigger_a: j }）
    # up: i               # （各項目はその機能のデフォルトキーを丸ごと置き換える
    # down: k             #  ・部分置き換えや併用は不可。使用可能なキー名の全一覧は
    # left: j             #  py_emulator.example.yaml を参照）
    # right: l
    # trigger_a: n
    # trigger_b: m

mouse:
  enabled: false         # ホストのマウスで駆動する MSX マウスを接続する
  port: 2                # 1（Joy1）または 2（Joy2）；有効時のデフォルトは 2

hotkeys: {}              # フロントエンドのホットキーの上書き（例：{ pause: f6, disk_next: f11 }）
  # disk_next: f6         # 組み込みのデフォルト。各項目はその動作のキーを置き換える
  # disk_eject: shift+f6  # キー：f1〜f12、"shift+" も可。f1〜f5、shift+f1〜f5（MSX のキー）
  # fast_forward_hold: f7 # と f8〜f10（固定のホットキー）は使えない
  # fast_forward_lock: shift+f7
  # pause: f11
  # fullscreen: shift+f11
  # mute: f12
  # reset: shift+f12
```

設定できるのは `machine`、`speed`、`scale`、`slot1`、`slot2`、`extension1`、
`extension2`、`fdd1`、`frame_skip`、`mouse`、`hotkeys` と、RPC、ゲームパッド、キーボード
ジョイスティックの設定。`extension` も `extension2` の古い別名として使える。`mapper1`/
`mapper2` は CLI だけ（上の `--mapper`/`--mapper1`/`--mapper2` を参照）。次の点にも注意。

- ゲームパッドのボタン割り当ては SDL GameController の経路に効き、両ポートで共通。
- `keyboard_joystick.buttons` は Joy1 のキーボードエミュレーションだけに効く。
- コマンドラインの `--mouse` は常に `mouse.enabled`/`mouse.port` より優先する。
- `fdd1` には 1 つのパスかパスのリストを書ける。コマンドラインの `--fdd1` は一覧全体を
  置き換える。
- `hotkeys` でフロントエンドのホットキーを別のファンクションキーに移せる。次の場合は
  起動時にエラーになる：2 つの動作が同じキーを使う、MSX のキー（F1〜F5、Shift+F1〜F5）を
  使う、固定のホットキー（F8〜F10）を使う。

全項目と、使えるボタン名・キー名は `py_emulator.example.yaml` を参照。

### エミュレータ内のキー操作

| キー | 動作 |
| --- | --- |
| Ctrl + Q | 終了 |
| F6 | `--fdd1` の一覧の次のディスクイメージをドライブ A にマウント（最後の次は先頭に戻る）。FDC のないマシンでは何もしない |
| Shift + F6 | ドライブ A のディスクを取り出す、または一覧の現在のイメージを再挿入する |
| F7（押している間） | 早送り：押している間だけ速度制限なしで動かす |
| Shift + F7 | 早送りの固定 ON/OFF |
| F8 | ステートセーブ（`saves/states/<title>_YYYYMMDD_HHMMSS.state` に保存）* |
| F9 | 最新のステートセーブを読み込む |
| F10 | スクリーンショットを保存（`saves/screenshots/screenshot_YYYYMMDD_HHMMSS.png`） |
| F11 | 一時停止/再開（RPC の一時停止とは別。どちらも相手の一時停止は解除しない） |
| Shift + F11 | フルスクリーン切り替え |
| F12 | ミュートの切り替え（音源は動き続ける） |
| Shift + F12 | マシンをリセット |
| Esc、F1〜F5 | MSX のキーボードマトリクスにそのまま渡す。Shift+F1〜F5 は MSX の F6〜F10 |
| Ctrl + F1 | MSX HOME |
| Ctrl + F2 | MSX INS |
| Ctrl + F3 | MSX DEL |
| Ctrl + F4 | MSX STOP |
| Ctrl + F5 | MSX SELECT |
| 左 Alt/Option | MSX GRAPH |
| 右 Alt/Option | MSX CODE/KANA |
| Shift + 0 | MSX の "_"。SHIFT+"\" で入る文字で、"\" は International キーボードにない JIS 専用のキー |

\* `<title>` は ROM データベースのゲームタイトル。データベースにないカートリッジでは `"py-msx-emulator"` になる。

F6、F7、F11、F12 とその Shift 付きは組み込みのデフォルト。別のキーに割り当てるには
`py_emulator.yaml` の `hotkeys` を設定する（[設定ファイル](#設定ファイルpy_emulatoryaml)参照）。
これらのホットキーは MSX のキーボードに渡らない。Shift 付きのホットキーを押している間は、
MSX から見た SHIFT も離した状態になる。

**注記（macOS）:** macOS はこれらのキーの一部を、システム全体のショートカットとして
予約していることがある。`^F1`〜`^F5`（Ctrl+F1〜F5。例：「メニューバーにフォーカスを
移動」= `^F2`）、F11（「デスクトップを表示」）、F12 など。システム設定 → キーボード →
キーボードショートカットと、「F1、F2 などのキーを標準のファンクションキーとして使用」の
設定を確認してほしい。エミュレータにキーが届かないときは、そこでショートカットを無効に
する。F6、F7、F11、F12 は `py_emulator.yaml` の `hotkeys` で別のキーに移すこともできる。

**注記（JIS BIOS 機種）:** HB-F1XD など日本の MSX は日本語の BIOS ROM を使う。この
ROM の文字デコード表は、マシン設定の `keyboard_type` に関係なく常に JIS 版になる。
`keyboard_type` が決めるのは、ホストのキーと MSX キーボードマトリクスのセルの対応だけ。
各セルの文字は BIOS が決める。そのため、これらのマシンを
International キーボードで使うと、キートップと入力される文字が一致しないことがある。
International キーボードにキーがない文字は `"_"` だけで、上の表の Shift+0 で入力できる。
ほかのキーも文字は入力できるが、キートップどおりとは限らない。

**キーボードによるジョイスティックエミュレーション（Joy 1）:** 下の表は組み込みの
デフォルト。各機能のキーは `py_emulator.yaml` の `keyboard_joystick.buttons` で
変えられる（[設定ファイル](#設定ファイルpy_emulatoryaml)を参照）。

| キー | 動作 |
| --- | --- |
| W / ↑ | 上 |
| S / ↓ | 下 |
| A / ← | 左 |
| D / → | 右 |
| Z または , | トリガ A |
| X または . | トリガ B |

---

## ツール

`tools/` にある単独で動くコマンドラインツール。ディスクイメージ用のツール（ftp 風の
対話シェル、空イメージの作成、USB フロッピードライブからの生セクタのコピー）と、
[リモート制御](#リモート制御socket-rpc--mcp)で使う RPC/MCP のクライアントツールがある。

各スクリプトの使い方、オプション、実行例は
[`tools/README_tools.md`](tools/README_tools.md) /
[`tools/README_tools_ja.md`](tools/README_tools_ja.md) を参照。

---

## リモート制御（Socket RPC & MCP）

`--rpc` を付けると、エミュレータはローカルの Unix ソケット JSON-RPC サーバを開く。
`tools/mcp_server.py` はそれを MCP サーバとして包む。外部ツール、テストハーネス、
Claude Code のような AI コーディングエージェントから、実行中のエミュレータを一時停止、
検査、操作できる。設定方法、メソッド一覧、セキュリティ上の注意は
[`tools/README_tools.md`](tools/README_tools.md#remote-control-socket-rpc--mcp)
/ [`tools/README_tools_ja.md`](tools/README_tools_ja.md#リモート制御socket-rpc--mcp) を参照。

---

## マシン設定

ハードウェア構成（VDP の種類、RAM のサイズ、スロットの配線、ROM ファイル）は `config/machines/` の YAML ファイルに書く。`--machine` でマシンの ID を選ぶ。省略すると ROM データベースで世代を決める（MSX1 の ROM なら `cbios_msx1_jp`、MSX2 の ROM かカートリッジなしなら `cbios_msx2_jp`）。

### 利用可能なマシン ID

| ID | 世代 | 地域 | VDP |
| --- | --- | --- | --- |
| `cbios_msx1` | MSX1 | インターナショナル | TMS9918A |
| `cbios_msx1_jp` | MSX1 | 日本 | TMS9918A |
| `cbios_msx1_eu` | MSX1 | ヨーロッパ | TMS9918A |
| `cbios_msx1_br` | MSX1 | ブラジル | TMS9918A |
| `cbios_msx2` | MSX2 | インターナショナル | V9938 |
| `cbios_msx2_jp` | MSX2 | 日本（デフォルト） | V9938 |
| `cbios_msx2_eu` | MSX2 | ヨーロッパ | V9938 |
| `cbios_msx2_br` | MSX2 | ブラジル | V9938 |
| `hb_10` | MSX1 | 日本 | TMS9918A |
| `hb_10_64k` | MSX1 | 日本 | TMS9918A |
| `hb_f1xd` | MSX2 | 日本 | V9938 |
| `hb_f1xd_256k` | MSX2 | 日本 | V9938 |
| `fs_a1f` | MSX2 | 日本 | V9938 |

`hb_10`（Sony HB-10）は実機の ROM を使い、BIOS と同じスロット 0 に 16 KB の RAM を持つ。実機と同じく、スロット 3 は空き。`hb-10_basic-bios1.rom` を `roms/hb_10/` に置く。

HB-10 の VDP は TMS9118NL だが、エミュレータでは機能が同じ TMS9918A として実装している。

`hb_10_64k` は、[Sony HB-10 のカテゴリページ](https://www.msx.org/wiki/Category:Sony_HB-10)にある 64 KB RAM 改造をした HB-10 にあたる。スロット 0 の 16 KB RAM をなくし、スロット 3 の 4 ページすべてに 64 KB のフラットな RAM を置く。ROM は `hb_10` と同じ `roms/hb_10/` のものを使う。改造した実機では確認していない。

`hb_f1xd`（Sony HB-F1XD）は実機の ROM を使い、WD2793 のフロッピーディスクドライブを持つ。`hb-f1xd_basic-bios2.rom`、`hb-f1xd_msx2sub.rom`、`hb-f1xd_disk.rom` を `roms/hb_f1xd/` に置き、`--fdd1` でディスクをマウントする。

`hb_f1xd_256k` は `hb_f1xd` と同じ ROM とフロッピードライブを使い、スロット 3 のフラットな 64 KB RAM を 256 KB の RAM マッパーに置き換えたもの。MSX-DOS2 の拡張 BIOS のマッパー支援ルーチンには 128 KB 以上の RAM マッパーが必要で、フラットな RAM ではサイズを増やしても使えないため。

> **注記**：256 KB の RAM マッパーを持つ HB-F1XD は実在しない。実機の HB-F1XD は常に 64 KB のフラットな RAM で、RAM マッパーではない。`hb_f1xd_256k` は実機についての未確認の主張ではなく、MSX-DOS2 が必要とするメモリ構成を用意するためだけの架空の構成。ほかの部分は実機の HB-F1XD の設定をそのまま使っている。

`fs_a1f`（Panasonic FS-A1F）は実機の ROM を使い、TC8566AF のフロッピーディスクドライブを持つ。`fs-a1f_basic-bios2.rom`、`fs-a1f_msx2sub.rom`、`fs-a1f_disk.rom` を `roms/fs_a1f/` に置き、`--fdd1` でディスクをマウントする。実機ではこれらは 1 つの 128 KB マスク ROM になっている。分け方は `config/machines/fs_a1f.yaml` のコメントを参照。

> **注記**：実機の FS-A1F には「コックピット」アプリケーションと漢字 ROM/フォントデバイスも入っているが、どちらもエミュレートしていない。作者は実機の FS-A1F を持っていない。この実装は公開されている技術資料に基づくもので、実機では確認していない。

### マシン YAML の構造

マシンファイルには CPU、スロットの配線、内蔵デバイスを書く。デバイスの定義は `config/devices/` に分けてあり、マシンファイルからは `ref:` で参照する。

```yaml
schema_version: 1
id: cbios_msx2
name: "Generic MSX2 (C-BIOS, International)"
generation: msx2
video_standard: ntsc
cpu:
  type: z80a
  clock_mhz: 3.579545
  m1_wait_states: 1 # MSX は M1（オペコードフェッチ）ごとに 1 ウェイト挿入；省略で純正 Z80

slots:
  primary:
    0:
      content:
        - rom:
            file: cbios_main_msx2.rom
            size_kb: 32
            pages: [0, 1]
            sha1: null
        - rom:
            file: cbios_logo_msx2.rom
            size_kb: 16
            pages: [2]
            sha1: null
    1: { type: cartridge }
    2: { type: cartridge }
    3:
      expanded: true
      secondary:
        0:
          content:
            - rom:
                file: cbios_sub.rom
                size_kb: 32
                pages: [0, 1]
                sha1: null
        2:
          type: ram
          size_kb: 128
          mapper: standard

builtin_devices:
  - ref: ppi8255
  - ref: vdp_v9938
    overrides: { vram_kb: 128 }
  - ref: psg_ay8910
  - ref: rtc_rp5c01
  - ref: memory_mapper_standard
```

主なフィールド：

| フィールド | 説明 |
| --- | --- |
| `generation` | `msx1` または `msx2`。VDP クラスとメモリモデルを決定 |
| `cpu.clock_mhz` | Z80A クロック周波数（NTSC MSX は 3.579545 MHz）。説明用のみで、ローダーは読まない |
| `cpu.m1_wait_states` | M1（オペコードフェッチ）ごとの追加 T ステート（任意。デフォルト 0 = 純正 Z80）。MSX 機は `1` |
| `slots.primary.N` | プライマリスロット N：`{type: cartridge}`、`{type: ram, ...}`、またはインライン ROM `content` |
| `slots.primary.N.expanded` | `true` で 4 つのセカンダリスロットに拡張 |
| `builtin_devices` | スロット非経由で直接配線するデバイス：VDP、PSG、PPI、RTC、RAM マッパー |
| `overrides` | マシンごとのデバイス設定。ローダーが読むのは `ppi8255` の `keyboard_type` と `control_read` だけ。ほかのキーは受け付けて無視する（例：V9938 の `vram_kb: 128`） |
| `sha1` | `null` はハッシュ検証なしで読み込む |

独自のマシン定義を使うには、`config/machines/` に新しい YAML ファイルを置き、その `id` を `--machine` に渡す。デバイス YAML で `implemented: false` になっている項目は、読み込み時に警告を出して飛ばす。

---

## テストの実行

テストスイートは 3782 個のテストで、主なコンポーネントをすべて扱う。個々のオペコードやハードウェアレジスタのユニットテスト、複数のコンポーネントをつないだ統合テスト、仕様のシナリオをそのまま条件にしたテストがある。

```bash
# 開発用依存関係（pytest、ruff、mypy）をインストール
pip install -r requirements-dev.txt

# すべてのテストを実行
python -m pytest

# 詳細出力
python -m pytest -v

# キーワードに一致するテストのみ実行
python -m pytest -k "psg"
```

---

## プロジェクト構成

```
py-msx-emulator/
├── __main__.py            # CLI エントリポイント（python .）
├── frontend/
│   └── sdl2_frontend.py   # SDL2 ウィンドウ、オーディオ、イベントループ
├── msx/                   # コアエミュレータパッケージ
│   ├── cpu/               # Z80 CPU（レジスタ、フラグ、オペコード）
│   ├── vdp/               # VDP（TMS9918A + V9938 コア、レンダラ、トレーサ）
│   ├── fdc/               # フロッピー（ディスクイメージ、ドライブ、WD2793/TC8566AF、インタフェース）
│   ├── diagnostics/       # DebugLogger、CPU/I/O トレース、ハング検出器
│   ├── debugger/          # インタラクティブ REPL（プロンプト、逆アセンブラ）
│   ├── machine.py         # コンポーネント配線とフレームループ
│   ├── machine_loader.py  # YAML ベースのマシン設定ローダ
│   ├── memory.py          # スロットベースのメモリバス
│   ├── mapper.py          # カートリッジマッパー（Flat、ASCII8/16、Konami、SCC、Halnote...）
│   ├── mapper_tracer.py   # カートリッジバンク切り替えトレーサ
│   ├── kanji.py           # JIS漢字フォントROM I/Oデバイス
│   ├── printer.py         # プリンタポート（0x90/0x91）+ バイトロガー
│   ├── printer_hbpf1.py   # Sony HBP-F1/HBP-F1C プリンタ → PNG ページ
│   ├── ram_mapper.py      # MSX2 RAM マッパー（16 KB セグメント）
│   ├── rtc.py             # RP5C01 リアルタイムクロック
│   ├── psg.py             # AY-3-8910 PSG + オーディオ合成（サブフレーム PCM）
│   ├── audio_filter.py    # アナログ的な出力ローパス（BiquadLowPass）
│   ├── dac.py             # 書き込み駆動型の DAC チャンネル（Majutsushi DAC、キークリック）
│   ├── scc.py             # Konami SCC ウェーブテーブルシンセサイザ
│   ├── fmpac.py           # FM-PAC カートリッジ（バンク ROM、SRAM、OPLL ルーティング）
│   ├── opll.py            # YM2413（OPLL）FM 音源チップ
│   ├── ppi.py             # i8255 PPI（スロットレジスタ、キーボード、キークリック）
│   ├── io.py              # I/O バス（ポートディスパッチ）
│   ├── input.py           # キーボードマトリクス + ジョイスティック入力状態
│   ├── joystick.py        # 物理ジョイスティックマネージャ（SDL2）
│   ├── mouse.py           # MSX マウスプロトコル状態機械（pin-8 クロックのニブル転送）
│   ├── frame_timer.py     # フレームの速度調整（NTSC 60 fps / PAL 50 fps）と FPS 計測
│   ├── app_config.py      # py_emulator.yaml の起動時デフォルト値
│   ├── romdb.py           # SHA1 ベースの ROM タイトル/マッパーデータベース
│   ├── rpc_server.py      # Unix ソケット JSON-RPC 制御サーバ（--rpc）
│   ├── screenshot.py      # RGB24→PNG 書き出し（スクリーンショット + ステート画像）
│   └── state.py           # マシン状態のセーブ/ロード（JSON + PNG）
├── config/
│   ├── devices/           # デバイス YAML 定義（VDP、PSG、PPI、RTC...）
│   └── machines/          # マシン YAML 定義（cbios_msx1_jp、cbios_msx2_jp...）
├── scripts/               # ベンチマーク、ROM データベースの生成、プロファイル用の補助スクリプト
├── tools/                 # ディスクイメージツール（dskftp、dskblank、dskdd）、RPC クライアント、MCP サーバ（詳細は tools/README_tools_ja.md）
├── docs/                  # デバッガガイド、ソケット RPC / MCP リファレンス
├── assets/                # この README が参照するベンチマーク履歴グラフ
├── roms/
│   └── cbios/             # C-BIOS ROM ファイルをここに置く（バージョン管理外）
├── saves/                 # ステートセーブとスクリーンショット（実行時に生成）
├── allium/                # Allium の振る舞い仕様。仕様と実装のずれを確かめる（未公開）
├── openspec/
│   └── specs/             # コンポーネントの仕様（未公開）
├── tests/                 # テストスイート（3782 テスト）
├── py_emulator.example.yaml # 設定ファイルの記述例（注釈付き）
├── QUICK_START_ja.md      # 必要なもの、インストール、BIOS の準備
├── CHANGELOG_ja.md        # 更新履歴
├── requirements.txt       # ランタイム依存関係
├── requirements-dev.txt   # 開発用依存関係
└── pyproject.toml         # プロジェクトメタデータとツール設定
```

---

## コントリビューション

Issue とプルリクエストは歓迎する。仕様（`openspec/`、`allium/`）はまだ公開しておらず、
開発は今のところ AI を使った「Vibe coding」で進めている。そのため、コントリビュータが
仕様を書く必要はない。作者が Issue やプルリクエストの内容を確認したうえで、OpenSpec の
仕様と Allium の contract を修正する。

### コーディング規約

- **純粋な Python のみ**：C 拡張と Cython は使わない。ctypes は SDL2 フロントエンドで使っている分だけ
- **Python 3.10+**：データクラスと新しい型アノテーションの書き方を使う。コアは Rust/C/JS に移しやすく保つ。明示的な `for` ループを使い、セイウチ演算子や複雑な内包表記・ジェネレータは使わない
- **型ヒントを必ず付ける**：mypy の strict モードでチェックする
- **リント**：ruff（`line-length = 99`）。コミット前に `python -m ruff check .` を実行する
- **コメントは自明でないことだけ**：コードを読めばわかることは書かない

### Issue とプルリクエスト

今のところ CONTRIBUTING.md はない。大きな変更のプルリクエストを出す前に、[GitHub Issue](https://github.com/SangatsuUsagi/py-msx-emulator/issues) で相談してほしい。対象タイトル以外の ROM のバグ報告も歓迎する。それらの互換性の修正はベストエフォートになる。

---

## 謝辞

- **[openMSX](https://openmsx.org/)**：ROM の識別データは openMSX の softwaredb.xml（https://github.com/openMSX/openMSX）を参考にしたが、エントリはすべて事実情報として独自に集めた。openMSX のライセンスは GNU GPL v2。
- **[emu2413](https://github.com/digital-sound-antiques/emu2413)**（Mitsutaka Okazaki 氏）：`msx/opll.py` の YM2413（OPLL）は emu2413 v1.5.9 を Python に移植したもの。emu2413 のライセンスは MIT。
- **[C-BIOS](https://cbios.sourceforge.net/)**：無償の MSX BIOS の代替。テストに使っており、利用を勧める。

---

## ライセンス

MIT。[LICENSE](LICENSE) を参照。

---

## 更新履歴

更新履歴の全文は [CHANGELOG_ja.md](CHANGELOG_ja.md) を参照。

