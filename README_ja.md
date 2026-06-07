# py-msx-emulator

機械可読なコンポーネント仕様書によって駆動される、純粋な Python で書かれた機能的に正確な MSX1 エミュレータです。

![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Tests](https://img.shields.io/badge/tests-498%20passing-brightgreen)

[English README is here](README.md)

---

## ゴール

> **このエミュレータの主な目標は、MSX1 上で [KONAMI の沙羅曼蛇（Salamander）](https://ja.wikipedia.org/wiki/%E6%B2%99%E7%BE%85%E6%9B%BC%E8%9B%87) を動かすことです。**
>
> 互換性に関する注記: このエミュレータは、作者が所有する沙羅曼蛇の物理的な ROM ダンプのみでテストされています。すべての MSX1 ROM が正しく動作することを保証するものではありません。他のタイトルに関するバグ報告は歓迎しますが、サポートはベストエフォートとなります。

---

## 概要

py-msx-emulator は、沙羅曼蛇を動かすために必要なハードウェアの正確な再現を目標とした、機能的な MSX1 エミュレータです。SDL2 ディスプレイ・オーディオライブラリを除き、C 拡張やネイティブバインディングを一切使用せず、純粋な Python 3.10+ のみで書かれています。

**設計方針:**

- **ポータビリティ最優先。** すべてのコンポーネントは純粋な Python です。プラットフォーム固有の依存関係は、ディスプレイ・オーディオフロントエンド用の pysdl2 のみです。
- **仕様駆動開発（Spec-Driven Development）。** 各ハードウェアコンポーネントは、実装コードを書く前に機械可読な仕様書として定義されます。仕様書は `openspec/specs/` 以下に置かれ、テスト設計・実装・変更管理を駆動します。
- **明示的であること。** コンポーネントの配線は `make_machine()` で手動で行います。リフレクションや魔法的な依存性注入は使用しません。

---

## 機能一覧

- **Zilog Z80 CPU** — 完全なレジスタファイル（AF, BC, DE, HL, IX, IY, SP, PC, I, R およびシャドウレジスタ）、252 の文書化済みオペコードすべて、CB/DD/ED/FD プレフィックステーブル、非文書化の IXH/IXL/IYH/IYL レジスタアクセスオペコード、マスカブル割り込み（INT モード 1・モード 2）および非マスカブル割り込み（NMI）、T ステート精度のステッピング
- **TMS9918A VDP** — 16 KB VRAM、8 個のコントロールレジスタ、スクリーンモード 0–3（テキスト 40 桁、グラフィック 1/2、マルチカラー）、サイズ・拡大率付きスプライト描画、5 番目スプライトフラグおよびコインシデンスフラグ、VBlank 割り込み
- **AY-3-8910 PSG** — 16 レジスタ、3 トーンチャンネル、ノイズチャンネル、8 波形エンベロープジェネレータ、準対数振幅テーブル、44100 Hz PCM サンプル出力（1 フレームあたり 735 サンプル）
- **Konami SCC** — 5 チャンネルウェーブテーブルシンセサイザ（4 波形バンク、各 32 サンプル）、チャンネルごとの 12 ビット周波数・4 ビットボリューム、PSG と混合してオーディオ出力
- **i8255 PPI** — スロット選択レジスタ（ポート 0xA8）、11 行 × 8 ビット MSX キーボードマトリクス（ポート 0xA9）、行選択（ポート 0xAA）
- **MSX スロットシステム** — 4 ページ × 4 スロットディスパッチ、スロット 0 に BIOS ROM、スロット 1 にカートリッジ、スロット 2 にオプションの第 2 カートリッジ、スロット 3 に 32 KB RAM
- **カートリッジマッパー** — フラット（バンク切り替えなし）、ASCII8、ASCII16、Konami、KonamiSCC；SHA1 ベースの ROM データベースから自動検出
- **SDL2 フロントエンド** — 768×576 ウィンドウ（256×192 × スケール 3）、TMS9918A ハードウェアパレット、44100 Hz モノラルオーディオ、フルスクリーン切り替え、スクリーンショット、ステートセーブ/ロード、自動フレームスキップ（遅延フレームで VDP ピクセル描画を省略；VBlank 割り込みは毎フレーム発火）
- **物理ジョイスティック** — SDL2 GameController および生ジョイスティック API、ホットプラグ対応、キーボードによるジョイスティックエミュレーション（WASD + ZX/.,）
- **ステートセーブ/ロード** — pickle による完全なハードウェアスナップショット（CPU、RAM、VDP、PSG、SCC、マッパーバンク）、セーブごとに PNG スクリーンショットも保存、素早い復帰のための `saves/latest.*` シンボリックリンク
- **ROM データベース** — SHA1 によるタイトル検索で自動的にゲームタイトルとマッパーを判別
- **デバッグツール** — オプトインの構造化ログ、CPU 命令トレース、I/O ポートトレース、ハング検出器
- **純粋な Python** — C 拡張なし；Python 3.10 と SDL2 が動作する環境ならどこでも実行可能

---

## 仕様駆動アーキテクチャ

このエミュレータのすべてのハードウェアコンポーネントは、コードを書く前に機械可読な仕様書として定義されます。本プロジェクトは [Claude Code](https://claude.ai/code) と [OpenSpec](https://openspec.dev/) を使用して実装しました。

### 仕組み

仕様書は `openspec/specs/<component>/spec.md` に置かれます。各仕様書ファイルは、自然言語による要件と具体的な WHEN/THEN シナリオを組み合わせた構造化された散文形式を使用します。

```markdown
### Requirement: Instruction fetch and execute
`Z80.step() -> int` SHALL fetch the opcode byte at PC, advance PC, decode and execute
the instruction, and return the number of T-states consumed.

#### Scenario: NOP executes in 4 T-states
- **WHEN** opcode 0x00 (NOP) is at PC and `step()` is called
- **THEN** the return value is 4 and PC is incremented by 1

#### Scenario: LD BC, nn loads a 16-bit immediate
- **WHEN** bytes [0x01, 0x34, 0x12] are at PC and `step()` is called
- **THEN** BC is 0x1234 and PC is incremented by 3
```

シナリオはユニットテストに直接対応しており、実装が仕様を満たしていることを容易に検証できます。新機能の追加や既存コンポーネントの変更時には、まず仕様書を更新し、その後実装を行います。

### 仕様のカバレッジ

以下のコンポーネントについて仕様書が定義されています。

`z80-cpu` · `vdp-core` · `vdp-renderer` · `vdp-sprites` · `vdp-interrupt` · `psg` · `psg-synthesis` · `scc-sound-chip` · `ppi` · `memory-bus` · `mega-rom-mapper` · `io-bus` · `keyboard-matrix` · `joystick-input` · `physical-joystick` · `machine` · `frame-timer` · `hang-detector` · `romdb` · `debug-logger` · `cpu-trace-buffer` · `io-trace` · `boot-diagnostic` · `sdl2-frontend` · `state-save-load`

> 注: `openspec/` ディレクトリおよび `tests/` ディレクトリは公開リポジトリには含まれていません。

---

## コンポーネントリファレンス

### CPU — Zilog Z80

| 項目 | 詳細 |
|------|------|
| 実装 | `msx/cpu/z80.py`、`msx/cpu/opcodes_main.py`、`msx/cpu/registers.py` |
| レジスタファイル | AF、BC、DE、HL、IX、IY、SP、PC、I、R；シャドウレジスタ AF′、BC′、DE′、HL′ |
| 命令セット | 文書化済み 252 オペコードすべて；CB、DD、ED、FD プレフィックステーブル |
| 非文書化オペコード | DD/FD プレフィックス：`LD r, IXH/IXL/IYH/IYL`、`LD IXH/IXL/IYH/IYL, r`、IXH/IXL/IYH/IYL を使った算術命令；8 T ステート、正しいフラグ動作 |
| 割り込み | マスカブル INT（モード 1：0x0038 にジャンプ；モード 2：I レジスタベクタ）；NMI（PC をプッシュして 0x0066 にジャンプ） |
| タイミング | `step()` が消費 T ステート数を返す；1 NTSC フレームあたり 59,659 T ステート |
| 既知の制限 | OTIR/INIR 等のブロック I/O 命令はページ境界をまたぐ場合にサイクル精度でない；R レジスタはオペコードフェッチ時のみインクリメント |

### VDP — TMS9918A

| 項目 | 詳細 |
|------|------|
| 実装 | `msx/vdp/vdp.py`、`msx/vdp/renderer.py` |
| VRAM | 16 KB |
| コントロールレジスタ | ポート 0x99 経由の 8 レジスタ（R0–R7） |
| スクリーンモード | モード 0：テキスト 40 桁（SCREEN 0）；モード 1：グラフィック 1（SCREEN 1）；モード 2：グラフィック 2（SCREEN 2）；モード 3：マルチカラー（SCREEN 3） |
| スプライト | 32 スプライト、8×8 または 16×16 サイズ、×1/×2 拡大；1 ライン 4 スプライト制限；5 番目スプライトフラグ；コインシデンスフラグ |
| 出力 | 1 フレームあたり 256×192 の色インデックスバッファ；フロントエンドが TMS9918A パレットを使って RGB24 に変換 |
| 割り込み | VBlank が INT コールバックをトリガ；ステータスレジスタのビット 7 は読み出し時にクリア |
| 既知の制限 | フレーム途中でのレジスタ変更タイミングと非文書化のスプライトオーバーフロー動作はエミュレートされていない |

### PSG — AY-3-8910

| 項目 | 詳細 |
|------|------|
| 実装 | `msx/psg.py` |
| レジスタ | ポート 0xA0（アドレスラッチ）、0xA1（書き込み）、0xA2（読み出し）経由の 16 レジスタ |
| トーンチャンネル | 3 チャンネル（A、B、C）、12 ビット周期レジスタ、矩形波 |
| ノイズチャンネル | 17 ビット LFSR |
| エンベロープ | 8 種類の波形；準対数 16 ステップ振幅テーブル |
| オーディオ出力 | 44100 Hz、符号付き 16 ビットモノラル；`generate_samples(735)` で 1 フレームあたり 735 サンプル |

### SCC — Konami SCC（Sound Creative Chip）

| 項目 | 詳細 |
|------|------|
| 実装 | `msx/scc.py` |
| チャンネル数 | 5 チャンネル |
| 波形 | 4 波形バンク、各 32 バイトの符号付き整数；チャンネル 4 と 5 はバンク 3 を共有 |
| 周波数 | チャンネルごとに 12 ビットレジスタ |
| ボリューム | チャンネルごとに 4 ビットレジスタ |
| 有効化 | KonamiSCC マッパーが 0x9000 への 0x3F 書き込みを検出した際に SCC を有効化；レジスタは 0x9800 に現れる |
| 混合 | SCC サンプルを PSG サンプルにサンプルごとに加算し、[−32768, 32767] にクリップ |

### PPI — Intel i8255

| 項目 | 詳細 |
|------|------|
| 実装 | `msx/ppi.py` |
| ポート 0xA8 | プライマリスロットレジスタ（読み書き） |
| ポート 0xA9 | キーボードマトリクス行読み出し（8 ビットアクティブロー） |
| ポート 0xAA | キーボード行セレクタ（ビット 0–3） |
| 既知の制限 | カセットインターフェース（ポート 0xAA のビット 4–7）は実装されていない |

### メモリバス / スロットシステム

| 項目 | 詳細 |
|------|------|
| 実装 | `msx/memory.py` |
| アドレス空間 | フラット 64 KB（0x0000–0xFFFF）、4 つの 16 KB ページ |
| スロット 0 | BIOS ROM（読み取り専用） |
| スロット 1 | マッパー経由のカートリッジ ROM |
| スロット 2 | `_mapper2` 経由の第 2 カートリッジ ROM；スロット 2 ROM が未装着の場合はオープンバス（読み出しは 0xFF、書き込みは無視） |
| スロット 3 | 0x8000–0xFFFF の 32 KB RAM |

### カートリッジマッパー

| マッパー | 説明 |
|---------|------|
| `FlatMapper` | バンク切り替えなし；ROM を 32 KB カートリッジ領域全体にミラー |
| `Ascii8Mapper` | 0x4000/0x6000/0x8000/0xA000 に 4 つの 8 KB ウィンドウ；コントロールレジスタは 0x6000–0x7FFF |
| `Ascii16Mapper` | 0x4000 と 0x8000 に 2 つの 16 KB ウィンドウ；コントロールレジスタは 0x6000–0x7FFF |
| `KonamiMapper` | 0x6000/0x8000/0xA000 に 3 つの 8 KB ウィンドウ；バンクレジスタはウィンドウベースアドレスへの書き込みで選択 |
| `KonamiSCCMapper` | Konami と同じだが、0x9000 への 0x3F 書き込みで SCC を有効化 |

マッパーは SHA1 ROM データベースから自動検出されます。`--mapper` オプションで上書き可能です。

スロット 2 は `--mapper2` で独立して制御します（デフォルトは自動検出）。`KonamiSCC` はスロット 2 では無効です。ROM データベースがスロット 2 カートリッジに対して `KonamiSCC` を返した場合、警告を標準エラー出力に表示したうえで `Konami` マッパーに自動フォールバックします。

### ROM データベース

| 項目 | 詳細 |
|------|------|
| 実装 | `msx/romdb.py` |
| 検索キー | カートリッジ ROM の SHA1 ハッシュ |
| データ | ROM ごとのゲームタイトルと推奨マッパー種別 |
| データソース | [openMSX ソフトウェアデータベース](https://github.com/openMSX/openMSX/blob/master/share/softwaredb.xml) をベースに作成 |
| フォールバック | PyYAML が未インストールの場合、または ROM がデータベースに未登録の場合、タイトルなしで起動し、マッパーは `--mapper auto` のヒューリスティックにフォールバック |

### I/O バス

| 項目 | 詳細 |
|------|------|
| 実装 | `msx/io.py` |
| 設計 | レンジベースのポート登録；読み書きは登録済みハンドラにディスパッチ |

### キーボード / ジョイスティック入力

| 項目 | 詳細 |
|------|------|
| キーボード | `msx/input.py`；MSX テクニカルハンドブック準拠の 11 行 × 8 ビット、アクティブロー |
| キー行 | 行 6：F1–F3、修飾キー；行 7：F4–F5、Tab、Return；行 8：カーソルキー、スペース |
| 物理ジョイスティック | `msx/joystick.py`；SDL2 GameController API（優先）と生ジョイスティックのフォールバック；ホットプラグ対応 |
| キーボードエミュレーション | WASD = Joy1 方向；Z/X または ,/. = トリガ A/B；矢印キーも対応 |

---

## 必要要件

- **Python 3.10 以降**
- **SDL2 ネイティブライブラリ** — pysdl2 とは別途インストールが必要

| パッケージ | 最低バージョン | 用途 |
|-----------|--------------|------|
| Pillow | 12.0 | スクリーンショットおよびステートセーブの PNG 出力 |
| pysdl2 | 0.9.16 | ディスプレイ・オーディオフロントエンドの SDL2 バインディング |
| PyYAML | 6.0 | ROM データベースのタイトル検索（なくてもエミュレータは動作します） |

開発用依存関係（pytest、ruff、mypy）は `requirements-dev.txt` に記載されています。このプロジェクトは PyPI には公開されておらず、パッケージとしてインストールすることを想定していません。

---

## パフォーマンス

沙羅曼蛇（KonamiSCC マッパー）を `--speed 1.0`（目標：60 fps）で実行した際の実測値です。

| プラットフォーム | ランタイム | 実測 FPS | 備考 |
|----------------|-----------|----------|------|
| Apple MacBook（M5 Pro） | Python 3.12 | 約 60 fps | リアルタイムでプレイ可能 |
| Raspberry Pi 5 | Python 3.12 | 約 16 fps | リアルタイムの約 27%；ゲームはスローモーションで動作 |
| Raspberry Pi 5 | PyPy3 | 約 35〜45 fps | リアルタイムの約 60〜75%；CPython より大幅に高速 |

60 fps を維持できないプラットフォームでは、達成されたフレームレートに比例してゲームがスローモーションで動作します。オーディオサンプルはフレームごとに生成される一方でオーディオデバイスは常に 44,100 Hz で消費するため、60 fps を下回るとオーディオが劣化します（クリックノイズや無音）。PyPy3 はそのまま代替として使えるランタイムであり、処理能力の低いハードウェアでのスループットを大幅に改善します。Raspberry Pi でリアルタイムに近い動作を目指す際に推奨されます。

自動フレームスキップ（`--frame-skip auto`、デフォルト）は、締め切りに間に合わなかったフレームで VDP のピクセル描画を省略しつつ、毎フレームの VBlank 割り込みは発火し続けます。これにより 60 fps に近いが届かないホストでの表示の滑らかさが向上します。たとえば Raspberry Pi 5 + PyPy3（エミュレーション ~35〜45 fps）では、表示フレームレートが生の処理速度より高い ~25〜35 fps に改善します。オーディオ品質はフレームスキップの影響を受けません。60 fps 未満のプラットフォームではアンダーランが継続します。フレームスキップは `--frame-skip none` で無効化できます。

`--speed` はターゲットフレームレートを調整します（例：`--speed 2.0` は処理能力の十分なホストでゲームを 2 倍速で動作させます）。処理能力が不足しているホストでの補正や、低速なハードウェアでのオーディオ品質の改善はできません。

---

## BIOS のセットアップ

このエミュレータには BIOS ROM が同梱されていません。ユーザー自身で用意する必要があります。

**C-BIOS** は無償のオープンソース MSX BIOS 代替品であり、推奨される選択肢です。

1. [https://cbios.sourceforge.net/](https://cbios.sourceforge.net/) から最新リリースをダウンロードします。
2. アーカイブを展開し、以下のファイルをこのリポジトリの `roms/` ディレクトリにコピーします。
   - `cbios_main_msx1.rom`
3. CLI のデフォルト ROM パスは `roms/cbios_main_msx1.rom` です。異なるファイル名の場合は、最初の位置引数として指定してください。

> **法的注記:** 市販のMSXマシンから取り出した著作権で保護された BIOS ダンプは使用しないでください。C-BIOS が無償で合法的に利用できる推奨の代替品です。`roms/` ディレクトリは `.gitignore` によってバージョン管理から除外されています。

---

## インストール

```bash
git clone https://github.com/SangatsuUsagi/py-msx-emulator.git
cd py-msx-emulator

# SDL2 ネイティブライブラリをインストール
# macOS:
brew install sdl2
# Ubuntu / Debian:
sudo apt install libsdl2-2.0-0

python -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
```

> **動作確認済みプラットフォーム:** macOS および Linux（Ubuntu）。Windows での動作は未確認です。

---

## 使い方

### エミュレータの起動

```bash
# MSX BASIC のみ（カートリッジなし）
python . roms/cbios_main_msx1.rom

# ゲームカートリッジあり
python . roms/cbios_main_msx1.rom path/to/game.rom

# 2 倍速でエミュレーション
python . roms/cbios_main_msx1.rom path/to/game.rom --speed 2.0

# デュアルカートリッジ（スロット 1 + スロット 2）
python . roms/cbios_main_msx1.rom path/to/game1.rom --slot2 path/to/game2.rom

# デュアルカートリッジでマッパーを明示指定
python . roms/cbios_main_msx1.rom path/to/game1.rom --mapper KonamiSCC --slot2 path/to/game2.rom --mapper2 Konami

# マッパーを指定
python . roms/cbios_main_msx1.rom path/to/game.rom --mapper KonamiSCC

# 最新のステートセーブから復帰
python . roms/cbios_main_msx1.rom path/to/game.rom --resume

# 特定のステートファイルから復帰
python . roms/cbios_main_msx1.rom path/to/game.rom --resume saves/salamander_20260605_120000.state

# デバッグログを有効化
python . roms/cbios_main_msx1.rom path/to/game.rom --debug --log trace.log
```

### コマンドラインオプション

| オプション | デフォルト | 説明 |
|-----------|----------|------|
| `rom` | `roms/cbios_main_msx1.rom` | MSX BIOS ROM のパス |
| `cartridge` | _（なし）_ | カートリッジ ROM のパス |
| `--speed FLOAT` | `1.0` | エミュレーション速度の倍率 |
| `--mapper TYPE` | `auto` | スロット 1 マッパー：`auto`、`Mirrored`、`Normal`、`ASCII8`、`ASCII16`、`Konami`、`KonamiSCC` |
| `--slot2 ROM2` | _（なし）_ | スロット 2 カートリッジ ROM のパス |
| `--mapper2 TYPE` | `auto` | スロット 2 マッパー：`auto`、`Mirrored`、`Normal`、`ASCII8`、`ASCII16`、`Konami`（スロット 2 では KonamiSCC 非対応） |
| `--resume [FILE]` | _（なし）_ | `saves/latest.state` から復帰（引数なし）、または特定の `.state` ファイルから復帰 |
| `--frame-skip MODE` | `auto` | フレームスキップ：`auto` で遅延フレームの VDP 描画を省略、`none` で無効化 |
| `--debug` | オフ | 構造化診断ログを stderr に出力 |
| `--log FILE` | _（なし）_ | 診断ログをファイルに書き出す（`--debug` が必要） |

### エミュレータ内のキー操作

| キー | 動作 |
|------|------|
| Esc | 終了 |
| F8 | ステートセーブ（`saves/<title>_YYYYMMDD_HHMMSS.state` に保存）* |
| F9 | 最新のステートセーブを読み込む |
| F10 | スクリーンショットを保存（`screenshot_YYYYMMDD_HHMMSS.png`） |
| F11 | フルスクリーン切り替え |
| F1–F5 | MSX キーボードマトリクスにそのまま渡す |

\* `<title>` は ROM データベースから取得したゲームタイトルです。データベースにない場合は `"py-msx-emulator"` が使われます。

**キーボードによるジョイスティックエミュレーション（Joy 1）:**

| キー | 動作 |
|------|------|
| W / ↑ | 上 |
| S / ↓ | 下 |
| A / ← | 左 |
| D / → | 右 |
| Z または , | トリガ A |
| X または . | トリガ B |

### プログラマティック API

```python
from pathlib import Path
from msx.machine import make_machine

# ROM の読み込み
rom = Path("roms/cbios_main_msx1.rom").read_bytes()
cartridge = Path("game.rom").read_bytes()

# マシンの作成と配線（CPU、VDP、PSG、PPI、メモリ、I/O がすべて接続される）
machine = make_machine(rom=rom, cartridge=cartridge)

# CPU 命令を 1 ステップ実行；消費 T ステート数を返す
t_states = machine.cpu.step()

# メモリの直接読み書き
value = machine.memory.read(0x0000)
machine.memory.write(0x8000, 0x42)

# 1 NTSC フレームを実行（59,659 T ステート）
# 49,152 バイト（256×192）の色インデックスバッファを返す
frame_buf = machine.run_frame()

# CPU 状態の確認
print(hex(machine.cpu.registers.PC))
print(hex(machine.cpu.registers.A))
```

---

## テストの実行

テストスイートは 498 個のテストで構成されており、個々のオペコードやハードウェアレジスタを対象としたユニットテスト、複数コンポーネントを組み合わせた統合テスト、仕様書のシナリオから直接導出したシナリオレベルのテストが含まれます。

```bash
# すべてのテストを実行
python -m pytest

# 詳細出力
python -m pytest -v

# キーワードに一致するテストのみ実行
python -m pytest -k "psg"
```

> 注: `tests/` ディレクトリは公開リポジトリには含まれていません。

---

## プロジェクト構成

```
py-msx-emulator/
├── __main__.py            # CLI エントリポイント（python .）
├── frontend/
│   └── sdl2_frontend.py   # SDL2 ウィンドウ、オーディオ、イベントループ
├── msx/                   # コアエミュレータパッケージ
│   ├── cpu/               # Z80 CPU（レジスタ、フラグ、オペコード）
│   ├── vdp/               # TMS9918A VDP（コアとレンダラ）
│   ├── machine.py         # コンポーネント配線とフレームループ
│   ├── memory.py          # スロットベースのメモリバス
│   ├── mapper.py          # カートリッジマッパー（Flat、ASCII8/16、Konami、SCC）
│   ├── psg.py             # AY-3-8910 PSG + オーディオ合成
│   ├── scc.py             # Konami SCC ウェーブテーブルシンセサイザ
│   ├── ppi.py             # i8255 PPI（スロットレジスタ、キーボード）
│   ├── io.py              # I/O バス（ポートディスパッチ）
│   ├── input.py           # キーボードマトリクス + ジョイスティック入力状態
│   ├── joystick.py        # 物理ジョイスティックマネージャ（SDL2）
│   ├── frame_timer.py     # 60 fps ペーシング + FPS 計測
│   ├── romdb.py           # SHA1 ベースの ROM タイトル/マッパーデータベース
│   ├── state.py           # マシン状態のセーブ/ロード（pickle + PNG）
│   └── debug/             # DebugLogger、CPU/I/O トレース、ハング検出器
├── roms/                  # C-BIOS ROM ファイルをここに置く（バージョン管理外）
├── saves/                 # ステートセーブとスクリーンショット（実行時に生成）
├── openspec/
│   └── specs/             # コンポーネント仕様書（公開リポジトリには含まれていません）
├── tests/                 # テストスイート — 486 テスト（公開リポジトリには含まれていません）
├── requirements.txt       # ランタイム依存関係
├── requirements-dev.txt   # 開発用依存関係
└── pyproject.toml         # プロジェクトメタデータとツール設定
```

---

## コントリビューション

### 仕様書優先ルール

新しいハードウェアコンポーネントの追加や重要な動作変更を行う際は、実装コードを書く前に `openspec/specs/<component>/spec.md` に仕様書を追加または更新する必要があります。仕様書のシナリオはテストケースの信頼できる情報源です。対応する仕様書の更新なしに実装を追加するプルリクエストはマージされません。

### コーディング規約

- **純粋な Python のみ** — C 拡張、Cython、SDL2 フロントエンドで既に使われている ctypes 以外の追加 ctypes は使用しない
- **Python 3.10+** — データクラス、`match`/`case`、モダンな型アノテーション構文を積極的に使用する
- **型ヒントの徹底** — プロジェクトは mypy のストリクトモードでチェックされる
- **リント** — `line-length = 99` の ruff を使用；コミット前に `python -m ruff check .` を実行する
- **自明でない場合のみコメントを書く** — コードが既に語っていることを繰り返すコメントは追加しない

### Issue とプルリクエスト

現時点では正式な CONTRIBUTING.md はありません。重要な変更についてはプルリクエストを提出する前に GitHub Issue で議論してください。沙羅曼蛇以外の ROM に関するバグ報告は歓迎します；特定タイトルの互換性修正はベストエフォートで対応します。

---

## 謝辞

- **[openMSX](https://openmsx.org/)** — ROM タイトルおよびマッパーデータベース（`msx/romdb.py`）は [openMSX ソフトウェアデータベース](https://github.com/openMSX/openMSX/blob/master/share/softwaredb.xml) をベースに作成しています。openMSX は GNU GPL v2 でリリースされています。
- **[C-BIOS](https://cbios.sourceforge.net/)** — テストに使用している無償の MSX BIOS 代替品。

---

## ライセンス

MIT — [LICENSE](LICENSE) を参照してください。
