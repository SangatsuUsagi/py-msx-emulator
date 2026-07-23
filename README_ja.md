# py-msx-emulator

機械可読なコンポーネント仕様書によって駆動される、純粋な Python 3.10+ で書かれた機能的に正確な MSX1/MSX2 エミュレータです。

![Python](https://img.shields.io/badge/python-3.10%2B-blue) ![License](https://img.shields.io/badge/license-MIT-green) ![Tests](https://img.shields.io/badge/tests-1485%20passing-brightgreen)

[English README is here](README.md)

---

対象世代のハードウェアで特定のタイトルを正確に動かすことを目標にしています。

- **MSX1:** [沙羅曼蛇（Salamander）— KONAMI](https://ja.wikipedia.org/wiki/%E6%B2%99%E7%BE%85%E6%9B%BC%E8%9B%87) · [グラディウス2（Nemesis 2）— KONAMI](https://ja.wikipedia.org/wiki/%E3%82%B0%E3%83%A9%E3%83%87%E3%82%A3%E3%82%A6%E3%82%B92) · [夢大陸アドベンチャー（Penguin Adventure）— KONAMI](https://ja.wikipedia.org/wiki/%E5%A4%A2%E5%A4%A7%E9%99%B8%E3%82%A2%E3%83%89%E3%83%99%E3%83%B3%E3%83%81%E3%83%A3%E3%83%BC)
- **MSX2:** [ドラゴンスレイヤーIV ドラスレファミリー（Legacy of the Wizard）— Falcom](https://ja.wikipedia.org/wiki/%E3%83%89%E3%83%A9%E3%82%B4%E3%83%B3%E3%82%B9%E3%83%AC%E3%82%A4%E3%83%A4%E3%83%BCIV_%E3%83%89%E3%83%A9%E3%82%B9%E3%83%AC%E3%83%95%E3%82%A1%E3%83%9F%E3%83%AA%E3%83%BC) · [ロマンシア（Romancia）— Falcom](https://ja.wikipedia.org/wiki/%E3%83%AD%E3%83%9E%E3%83%B3%E3%82%B7%E3%82%A2)

テストは作者が所有する物理 ROM ダンプのみで行っているため、上記以外の MSX1/MSX2 ROM が正しく動作する保証はありません。他のタイトルのバグ報告は歓迎しますが、サポートはベストエフォートです。

各ハードウェアコンポーネントは純粋な Python で書かれ、実装前に機械可読な仕様書（`openspec/specs/` 以下）として定義され、その仕様がテストを駆動します。コンポーネントの配線は `build_machine()` で手動で行い、リフレクションや依存性注入の魔法は使いません。プラットフォーム固有の依存は、ディスプレイ・オーディオフロントエンド用の pysdl2 のみです。

---

## 機能一覧

- **Zilog Z80 CPU** — 完全なレジスタファイル（AF、BC、DE、HL、IX、IY、SP、PC、I、R およびシャドウレジスタ）、252 の文書化済みオペコードすべて、CB/DD/ED/FD プレフィックステーブル、非文書化の IXH/IXL/IYH/IYL レジスタアクセスオペコード、マスカブル割り込み（INT モード 1・モード 2）および非マスカブル割り込み（NMI）、設定可能な MSX **M1 ウェイトステート**（オペコードフェッチごとに +1 T ステート、実機 / openMSX 準拠。デフォルト 0 で純正 Zilog Z80）付きの T ステート精度ステッピング
- **TMS9918A VDP** — 16 KB VRAM、8 個のコントロールレジスタ、スクリーンモード 0–3（テキスト 40 桁、グラフィック 1/2、マルチカラー）、サイズ・拡大率付きスプライト描画、5 番目スプライトフラグおよびコインシデンスフラグ、VBlank 割り込み
- **Yamaha V9938 VDP** — 128 KB VRAM、28 個のコントロールレジスタ、プログラマブル 16 色パレット（9 ビット GRB333）、スクリーンモード 0–8（SCREEN 0 〜 SCREEN 8）、ハードウェアコマンドエンジン（HMMV、HMMM、HMMC、LMMV、LMMM、LMCM、LMMC、YMMM、LINE、PSET、POINT、SRCH）、水平ライン割り込み（R#19/R#23、IE1）、バンディングレンダラによるフレーム途中のレジスタ・パレット変更対応
- **AY-3-8910 PSG** — 16 レジスタ、3 トーンチャンネル、ノイズチャンネル、8 波形エンベロープジェネレータ、準対数振幅テーブル、44100 Hz PCM サンプル出力（1 フレームあたり 735 サンプル）；**サブフレーム・ソフトウェア PCM** 再現（ボリュームレジスタ書き込みをサイクル単位でタイムスタンプし、フレーム内のサンプル位置に再生）。トーンジェネレータを出力サンプルごとに積分するため、period-0 の超音波 PCM キャリアはデューティ平均に帯域制限され、full/zero のチョッピング（エイリアシング）に陥らない
- **Konami SCC** — 5 チャンネルウェーブテーブルシンセサイザ（4 波形バンク、各 32 サンプル）、チャンネルごとの 12 ビット周波数・4 ビットボリューム、PSG と混合してオーディオ出力
- **i8255 PPI** — スロット選択レジスタ（ポート 0xA8）、11 行 × 8 ビット MSX キーボードマトリクス（ポート 0xA9）、行選択（ポート 0xAA）
- **MSX1 スロットシステム** — 4 ページ × 4 スロットディスパッチ、スロット 0 に BIOS ROM、スロット 1 にカートリッジ、スロット 2 にオプションの第 2 カートリッジ、スロット 3 に 32 KB RAM
- **MSX2 サブスロットシステム** — プライマリスロット 3 を 4 つのセカンダリスロットに拡張；サブスロット 3-0 にサブ ROM、3-2 に 128 KB RAM マッパー
- **RAM マッパー** — 128 KB メインメモリ（8 セグメント × 16 KB）、ポート 0xFC–0xFF のセグメントレジスタ
- **RTC** — RP5C01 リアルタイムクロック、ポート 0xB4–0xB5
- **カートリッジマッパー** — フラット（バンク切り替えなし）、ASCII8、ASCII16、Konami、KonamiSCC、Majutsushi（DAC）、ASCII8SRAM2/8、ASCII16SRAM2/8、R-Type；SHA1 ベースの ROM データベースから自動検出
- **フロッピーディスクドライブ（WD2793）** — 汎用 FDC 層（ディスクイメージ / ドライブ / コントローラ / 接続スタイルインターフェース）に WD2793 コントローラと Sony/Philips 接続スタイルを実装。Sony HB-F1XD が採用。スロット 3 サブスロット 0 にメモリマップされ、`--fdd1` で `*.dsk` イメージをマウント。Disk BASIC 起動・`CALL FORMAT`・ファイル読み書き（終了時にイメージへ書き戻し）に対応。デバッガ REPL（`fdd1`/`fdd2`）から起動中のディスク入れ替えも可能
- **SDL2 フロントエンド** — 全マシン・全モードで固定 768×636 ウィンドウ（256×212 × スケール 3）。描画フレームは常に 212 ライン（192 ラインの SCREEN モードは上下にボーダー行を付けて中央寄せ）で、R#9 LN によらず 4:3 の CRT アスペクト比を維持。SCREEN 6/7（512 幅）は同じウィンドウ幅にダウンスケールしてピクセルアスペクト比を維持、ハードウェアパレット、44100 Hz モノラルオーディオ（実機 MSX の音声出力段の RC フィルタを模した **アナログ的な出力ローパスフィルタ**（2 極バターワース）付きで、残留するイメージング/エイリアシングを除去）、フルスクリーン切り替え、スクリーンショット、ステートセーブ/ロード、自動フレームスキップ（遅延フレームで VDP ピクセル描画を省略；VBlank 割り込みは毎フレーム発火）
- **物理ジョイスティック** — SDL2 GameController および生ジョイスティック API、ホットプラグ対応、キーボードによるジョイスティックエミュレーション（WASD + ZX/.,）
- **ステートセーブ/ロード** — stdlib JSON による完全なハードウェアスナップショット（CPU、RAM、VDP、PSG、SCC、マッパーバンク）、セーブごとに PNG スクリーンショットも保存、素早い復帰のための `saves/states/latest.*` シンボリックリンク
- **ROM データベース** — SHA1 によるタイトル検索で自動的にゲームタイトルとマッパーを判別
- **インタラクティブデバッガ** — Ctrl+C またはブレークポイント到達でアクセスできる REPL；ブレークポイント/ウォッチポイント、ステップ実行、レジスタ/VRAM ダンプ、逆アセンブル、VDP トレース、マッパートレース、スロットインスペクタ、フロッピーディスク入れ替え（`fdd1`/`fdd2 [FILE|-]`）
- **デバッグツール** — オプトインの構造化ログ、CPU 命令トレース、I/O ポートトレース、ハング検出器

---

## 仕様駆動アーキテクチャ

このエミュレータのすべてのハードウェアコンポーネントは、コードを書く前に機械可読な仕様書として定義されます。本プロジェクトは [Claude Code](https://claude.ai/code) と [OpenSpec](https://openspec.dev/) を使用して実装しました。

### 仕組み

仕様書は `openspec/specs/<component>/spec.md` に置かれます。各仕様書ファイルは、自然言語による要件と具体的な WHEN/THEN シナリオを組み合わせた構造化された散文形式を使用します。

```markdown
### Requirement: Instruction fetch and execute

`Z80.step() -> int` SHALL fetch the opcode byte at PC, advance PC, decode and execute the instruction, and return the number of T-states consumed.

#### Scenario: NOP executes in 4 T-states

- **WHEN** opcode 0x00 (NOP) is at PC and `step()` is called
- **THEN** the return value is 4 and PC is incremented by 1

#### Scenario: LD BC, nn loads a 16-bit immediate

- **WHEN** bytes [0x01, 0x34, 0x12] are at PC and `step()` is called
- **THEN** BC is 0x1234 and PC is incremented by 3
```

シナリオはユニットテストに直接対応しており、実装が仕様を満たしていることを容易に検証できます。新機能の追加や既存コンポーネントの変更時には、まず仕様書を更新し、その後実装を行います。

---

## コンポーネントリファレンス

### CPU — Zilog Z80

| 項目 | 詳細 |
| --- | --- |
| 実装 | `msx/cpu/z80.py`、`msx/cpu/opcodes_main.py`、`msx/cpu/registers.py` |
| 既知の制限 | OTIR/INIR 等のブロック I/O 命令はページ境界をまたぐ場合にサイクル精度でない；R レジスタはオペコードフェッチ時のみインクリメント |

### VDP — TMS9918A（MSX1）

| 項目 | 詳細 |
| --- | --- |
| 実装 | `msx/vdp/vdp.py`、`msx/vdp/renderer.py` |
| 既知の制限 | フレーム途中でのレジスタ変更タイミングと非文書化のスプライトオーバーフロー動作はエミュレートされていない |

### VDP — Yamaha V9938（MSX2）

| 項目 | 詳細 |
| --- | --- |
| 実装 | `msx/vdp/v9938.py`、`msx/vdp/v9938_renderer.py` |
| 既知の制限 | コマンドタイミングは近似値；ビームレースの書き込みやフレーム内の VRAM ダブルバッファは忠実に再現されない |

### PSG — AY-3-8910

実装：`msx/psg.py`

### SCC — Konami SCC（Sound Creative Chip）

| 項目 | 詳細 |
| --- | --- |
| 実装 | `msx/scc.py` |
| 有効化 | KonamiSCC マッパーが 0x9000 への 0x3F 書き込みを検出した際に SCC を有効化；レジスタは 0x9800 に現れる |

### オーディオ出力フィルタ

実装：`msx/audio_filter.py`

### PPI — Intel i8255

| 項目 | 詳細 |
| --- | --- |
| 実装 | `msx/ppi.py` |
| 既知の制限 | カセットインターフェース（ポート 0xAA のビット 4–7）は実装されていない |

### RAM マッパー

実装：`msx/ram_mapper.py`

### RTC — RP5C01

| 項目 | 詳細 |
| --- | --- |
| 実装 | `msx/rtc.py` |
| 既知の制限 | クロック読み出しはホストシステム時刻を反映；アラームおよびタイマー出力は未実装 |

### メモリバス / スロットシステム

| 項目 | 詳細 |
| --- | --- |
| 実装 | `msx/memory.py` |
| アドレス空間 | フラット 64 KB（0x0000–0xFFFF）、4 つの 16 KB ページ |
| スロット 0 ページ 0–1 | BIOS ROM（読み取り専用、0x0000–0x7FFF） |
| スロット 0 ページ 2 | ロゴ ROM（`cbios_logo_msx1.rom`）を 0x8000–0xBFFF にマップ；BIOS と同じディレクトリに存在すれば自動ロード；存在しない場合は 0xFF を返す |
| スロット 1 | マッパー経由のカートリッジ ROM |
| スロット 2 | `_mapper2` 経由の第 2 カートリッジ ROM；未装着の場合はオープンバス（読み出しは 0xFF、書き込みは無視） |
| スロット 3（MSX1） | ページ 2–3（0x8000–0xFFFF）の 32 KB RAM |
| スロット 3（MSX2） | 4 つのセカンダリスロットに拡張；3-0 にサブ ROM、3-2 に 128 KB RAM マッパー |

### カートリッジマッパー

| マッパー | 説明 |
| --- | --- |
| `FlatMapper` | バンク切り替えなし；ROM を 32 KB カートリッジ領域全体にミラー |
| `Ascii8Mapper` | 4 つの 8 KB ウィンドウ；コントロールレジスタは 0x6000–0x7FFF |
| `Ascii16Mapper` | 2 つの 16 KB ウィンドウ；コントロールレジスタは 0x6000–0x7FFF |
| `KonamiMapper` | 3 つの 8 KB ウィンドウ；バンクレジスタはウィンドウベースアドレスへの書き込みで選択 |
| `KonamiSCCMapper` | Konami と同じだが、0x9000 への 0x3F 書き込みで SCC を有効化 |
| `MajutsushiMapper` | ASCII8 派生型；0x9000 への書き込みで DAC 出力 |
| `ASCII8SRAM2`、`ASCII8SRAM8` | ASCII8 + 2 KB または 8 KB バッテリーバックアップ SRAM |
| `ASCII16SRAM2`、`ASCII16SRAM8` | ASCII16 + 2 KB または 8 KB バッテリーバックアップ SRAM |
| `RTypeMapper` | 8 KB ウィンドウ；バンク 0 は ROM 先頭に固定 |

マッパーは SHA1 ROM データベースから自動検出されます。`--mapper` オプションで上書き可能です。

スロット 2 は `--mapper2` で独立して制御します（デフォルトは自動検出）。`KonamiSCC` はスロット 2 では無効です。ROM データベースがスロット 2 カートリッジに対して `KonamiSCC` を返した場合、警告を stderr に表示したうえで `Konami` マッパーに自動フォールバックします。

### ROM データベース

| 項目 | 詳細 |
| --- | --- |
| 実装 | `msx/romdb.py` |
| データソース | [openMSX ソフトウェアデータベース](https://github.com/openMSX/openMSX/blob/master/share/softwaredb.xml)（参照元；すべてのエントリは独自に収集した事実情報） |
| フォールバック | PyYAML が未インストールの場合、または ROM がデータベースに未登録の場合、タイトルなしで起動し、マッパーは `--mapper auto` のヒューリスティックにフォールバック |

### I/O バス

実装：`msx/io.py` — レンジベースのポート登録；読み書きは登録済みハンドラにディスパッチ。

### キーボード / ジョイスティック入力

| 項目 | 詳細 |
| --- | --- |
| キーボード | `msx/input.py`；MSX テクニカルハンドブック準拠の 11 行 × 8 ビット、アクティブロー |
| 物理ジョイスティック | `msx/joystick.py`；SDL2 GameController API（優先）と生ジョイスティックのフォールバック；ホットプラグ対応 |
| キーボードエミュレーション | WASD = Joy1 方向；Z/X または ,/. = トリガ A/B；矢印キーも対応 |

---

## 必要要件

- **Python 3.10 以降**
- **SDL2 ネイティブライブラリ** — pysdl2 とは別途インストールが必要

| パッケージ | 最低バージョン | 用途 |
| --- | --- | --- |
| Pillow | 12.0 | スクリーンショットおよびステートセーブの PNG 出力 |
| pysdl2 | 0.9.16 | ディスプレイ・オーディオフロントエンドの SDL2 バインディング |
| PyYAML | 6.0 | ROM データベースのタイトル検索とマシン YAML 読み込み（なくてもエミュレータは動作します） |

開発用依存関係（pytest、ruff、mypy）は `requirements-dev.txt` に記載されています。このプロジェクトは PyPI には公開されておらず、パッケージとしてインストールすることを想定していません。

---

## パフォーマンス

`--benchmark` CLI オプションで計測しています。ヘッドレス・無制限速度（`FrameTimer` によるペーシングなし）で実行し、`--resume` により保存済みの中盤シーンから開始します（BIOS 起動時間は計測から除外）。CPU エミュレーションとオフスクリーンバッファへの VDP 描画は毎フレーム実行され、インタラクティブ再生時と同じ描画コストを負担しますが、`--benchmark` は SDL ウィンドウの作成・オーディオサンプル生成・テクスチャアップロード/ブリットを一切行わないため、実際のインタラクティブセッションはここに示す生の数値よりもいくらか遅くなります。

各（ランタイム、ゲーム）の組み合わせについて、10 秒間のトライアルを 10 回実行し、最速・最遅を除いた残り 8 回の平均をスコアとしています。

| プラットフォーム | ランタイム | ゲーム | 平均 FPS（`--benchmark`） | 60 fps 目標との比 |
| --- | --- | --- | --- | --- |
| Apple MacBook Pro（M5 Pro） | CPython 3.12.13 | MSX1: 沙羅曼蛇（KonamiSCC） | 221.09 | 約 3.7 倍 |
| Apple MacBook Pro（M5 Pro） | CPython 3.12.13 | MSX2: ドラゴンスレイヤー4（ASCII8） | 429.98 | 約 7.2 倍 |
| Apple MacBook Pro（M5 Pro） | PyPy 7.3.19（Python 3.10.16） | MSX1: 沙羅曼蛇（KonamiSCC） | 1542.85 | 約 25.7 倍 |
| Apple MacBook Pro（M5 Pro） | PyPy 7.3.19（Python 3.10.16） | MSX2: ドラゴンスレイヤー4（ASCII8） | 1676.95 | 約 28.0 倍 |
| Raspberry Pi 5 | CPython 3.12.13 | MSX1: 沙羅曼蛇（KonamiSCC） | 53.10 | 約 0.9 倍（目標未達） |
| Raspberry Pi 5 | CPython 3.12.13 | MSX2: ドラゴンスレイヤー4（ASCII8） | 102.81 | 約 1.7 倍 |
| Raspberry Pi 5 | PyPy 7.3.19（Python 3.10.16） | MSX1: 沙羅曼蛇（KonamiSCC） | 81.31 | 約 1.4 倍 |
| Raspberry Pi 5 | PyPy 7.3.19（Python 3.10.16） | MSX2: ドラゴンスレイヤー4（ASCII8） | 366.00 | 約 6.1 倍 |

60 fps をインタラクティブに維持できないプラットフォームでは、達成されたフレームレートに比例してゲームがスローモーションで動作します。オーディオサンプルはフレームごとに生成される一方でオーディオデバイスは常に 44,100 Hz で消費するため、60 fps を下回るとオーディオが劣化します（クリックノイズや無音）。今回の計測では、Raspberry Pi 5 + CPython で沙羅曼蛇（MSX1、KonamiSCC マッパー — 対象タイトルの中で描画・オーディオ負荷が最も重い）を実行した場合のみ、生の 60 fps 目標を下回りました。PyPy に切り替えることで目標を大きく上回ります。ドラゴンスレイヤー4（MSX2、V9938 レンダラ）は、Raspberry Pi 5 + CPython を含むすべての組み合わせで目標を達成しています。PyPy3 はそのまま代替として使えるランタイムであり、処理能力の低いハードウェアでのスループットを大幅に改善するため、Raspberry Pi のような制約のあるハードウェアでリアルタイム（またはそれ以上）に近づけるために推奨されます。

自動フレームスキップ（`--frame-skip auto`、デフォルト）は、締め切りに間に合わなかったフレームで VDP のピクセル描画を省略しつつ、毎フレームの VBlank 割り込みは発火し続けます。これにより、60 fps 目標に近いが届かないホストでの表示の滑らかさが向上します。オーディオ品質はフレームスキップの影響を受けません。60 fps 未満のプラットフォームではアンダーランが継続します。フレームスキップは `--frame-skip none` で無効化できます。

`--speed` はターゲットフレームレートを調整します（例：`--speed 2.0` は処理能力の十分なホストでゲームを 2 倍速で動作させます）。処理能力が不足しているホストでの補正や低速なハードウェアでのオーディオ品質の改善はできません。

---

## BIOS のセットアップ

このエミュレータには BIOS ROM が同梱されていません。ユーザー自身で用意する必要があります。

**C-BIOS** は無償のオープンソース MSX BIOS 代替品であり、推奨される選択肢です。

1. [https://cbios.sourceforge.net/](https://cbios.sourceforge.net/) から最新リリースをダウンロードします。
2. アーカイブを展開し、必要なファイルをこのリポジトリの `roms/cbios/` ディレクトリにコピーします。

MSX1（`cbios_msx1_jp`、MSX1 カートリッジ検出時のデフォルト）の場合：

- `cbios_main_msx1_jp.rom`
- `cbios_logo_msx1.rom`

MSX2（`cbios_msx2_jp`、カートリッジなし・MSX2 カートリッジ時のデフォルト）の場合：

- `cbios_main_msx2.rom`
- `cbios_logo_msx2.rom`
- `cbios_sub.rom`

マシン ID ごとに必要なファイル名は `config/machines/` 以下の対応する YAML に記載されています。

> **法的注記:** 市販の MSX マシンから取り出した著作権で保護された BIOS ダンプは使用しないでください。C-BIOS が無償で合法的に利用できる推奨の代替品です。`roms/` ディレクトリは `.gitignore` によってバージョン管理から除外されています。

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

動作確認済みプラットフォームは macOS および Linux（Ubuntu）です。Windows は未確認です。試す場合はシステムの SDL2 ライブラリの代わりに `pysdl2-dll` をインストールしてください。

---

## 使い方

### エミュレータの起動

```bash
# MSX BASIC のみ — デフォルトマシン（cbios_msx2_jp、カートリッジなし）
python .

# カートリッジあり — ROM データベースからマシンを自動判定
python . path/to/game.rom

# MSX1（日本）を明示指定
python . path/to/game.rom --machine cbios_msx1_jp

# MSX2（日本）を明示指定
python . path/to/game.rom --machine cbios_msx2_jp

# 2 倍速でエミュレーション
python . path/to/game.rom --speed 2.0

# デュアルカートリッジ（スロット 1 + スロット 2）
python . path/to/game1.rom --slot2 path/to/game2.rom

# Sony HB-F1XD にフロッピーをマウントして起動（Disk BASIC が起動）
python . --machine hb_f1xd --fdd1 path/to/disk.dsk

# CALL FORMAT でフォーマットするための空ディスク（720 KB）を作成
python tools/make_blank_dsk.py blank.dsk

# マッパーを明示指定
python . path/to/game.rom --mapper KonamiSCC

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

# ベンチマーク：10 秒間（デフォルト）ヘッドレスで実行し、平均 FPS を表示
python . path/to/game.rom --benchmark

# 保存済みシーンから 30 秒間ベンチマーク
python . path/to/game.rom --benchmark 30 --resume saves/states/game_20260605_120000.state
```

### コマンドラインオプション

| オプション | デフォルト | 説明 |
| --- | --- | --- |
| `cartridge` | _（なし）_ | カートリッジ ROM のパス |
| `--machine MACHINE_ID` | _（自動）_ | マシン設定 ID（例：`cbios_msx2_jp`）；省略時は ROM データベースから自動判定 |
| `--speed FLOAT` | `1.0` | エミュレーション速度の倍率 |
| `--mapper TYPE` | `auto` | スロット 1 マッパー：`auto`、`Mirrored`、`Normal`、`ASCII8`、`ASCII16`、`Konami`、`KonamiSCC`、`Majutsushi`、`ASCII8SRAM2`、`ASCII8SRAM8`、`ASCII16SRAM2`、`ASCII16SRAM8`、`R-Type` |
| `--slot2 ROM2` | _（なし）_ | スロット 2 カートリッジ ROM のパス |
| `--mapper2 TYPE` | `auto` | スロット 2 マッパー：`auto`、`Mirrored`、`Normal`、`ASCII8`、`ASCII16`、`Konami`、`Majutsushi`（スロット 2 では KonamiSCC 非対応） |
| `--fdd1 DSK` | _（なし）_ | ドライブ A にマウントするフロッピー `*.dsk` イメージ（FDC 搭載機、例：`hb_f1xd`）。書き込みは終了時にファイルへ反映 |
| `--fdd2 DSK` | _（なし）_ | ドライブ B にマウントするフロッピー `*.dsk` イメージ（2 ドライブ機のみ） |
| `--resume [FILE]` | _（なし）_ | `saves/states/latest.state` から復帰（引数なし）、または特定の `.state` ファイルから復帰 |
| `--frame-skip MODE` | `auto` | フレームスキップ：`auto` で遅延フレームの VDP 描画を省略、`none` で無効化 |
| `--debug` | オフ | 構造化診断ログを stderr に出力 |
| `--log FILE` | _（なし）_ | 診断ログをファイルに書き出す（`--debug` が必要） |
| `--vdp-trace` | オフ | VDP レジスタ書き込みトレースを stdout に出力 |
| `--vdp-trace-out FILE` | stdout | VDP トレースを FILE に書き出す |
| `--mapper-trace` | オフ | カートリッジマッパーのバンク切り替えトレースを出力（MAP\_BANK レコード） |
| `--mapper-trace-out FILE` | stdout | マッパートレースを FILE に書き出す |
| `--count-frame N` | _（なし）_ | N フレームをヘッドレスで実行して終了（SDL ウィンドウなし） |
| `--benchmark [SECONDS]` | _（なし）_ | SECONDS 秒間（デフォルト：10）ヘッドレス・無制限速度で実行し、平均 FPS を表示。`--resume` と組み合わせると保存済みシーンからベンチマークできる。`--count-frame` とは併用不可 |
| `--break-point ADDRS` | _（なし）_ | カンマ区切りの 16 進ブレークポイントアドレス（最大 4 個、MSX2 専用） |
| `--watch-point ADDRS` | _（なし）_ | ウォッチポイントアドレス（最大 4 個、MSX2 専用）；各アドレスの後に `,r`（読み取り）、`,w`（書き込み）、または `,rw`（両方）を付加できる（省略時は `rw`）。例：`C000,rw,D000,r` |
| `--rpc` | オフ | 組み込みの Unix ソケット JSON-RPC 制御サーバを有効化（対話実行モード）。[リモート制御](#リモート制御socket-rpc--mcp)を参照 |
| `--rpc-socket PATH` | `/tmp/py_msx_emu.sock` | `--rpc` 用の Unix ソケットパス（`--rpc` なしでは無効） |

### エミュレータ内のキー操作

| キー | 動作 |
| --- | --- |
| Esc | 終了 |
| F8 | ステートセーブ（`saves/states/<title>_YYYYMMDD_HHMMSS.state` に保存）* |
| F9 | 最新のステートセーブを読み込む |
| F10 | スクリーンショットを保存（`saves/screenshots/screenshot_YYYYMMDD_HHMMSS.png`） |
| F11 | フルスクリーン切り替え |
| F1–F5 | MSX キーボードマトリクスにそのまま渡す |

\* `<title>` は ROM データベースから取得したゲームタイトルです。データベースにない場合は `"py-msx-emulator"` が使われます。

**キーボードによるジョイスティックエミュレーション（Joy 1）:**

| キー       | 動作     |
| ---------- | -------- |
| W / ↑      | 上       |
| S / ↓      | 下       |
| A / ←      | 左       |
| D / →      | 右       |
| Z または , | トリガ A |
| X または . | トリガ B |

---

## リモート制御（Socket RPC & MCP）

エミュレータは小さなローカル制御インターフェースを公開でき、外部ツール（シェル
スクリプト、テストハーネス、AI コーディングエージェントなど）から実行中のインス
タンスを一時停止・検査・操作できます。2 つの層があります。

- **Socket RPC** — エミュレータプロセスに組み込まれた Unix ドメインソケットの
  JSON-RPC サーバ（`msx/rpc_server.py`）。**既定では無効**で、`--rpc` で有効化します。
- **MCP サーバ** — Socket RPC を [Model Context Protocol](https://modelcontextprotocol.io)
  ツールとしてラップするスタンドアロンの stdio サーバ（`tools/mcp_server.py`）。
  Claude Code のようなクライアントがエミュレータ機能をネイティブツールとして呼び出せ
  （スクリーンショットはインライン画像として受け取れ）ます。

```
MCP クライアント ──stdio/MCP──▶ tools/mcp_server.py ──Unix ソケット──▶ エミュレータ (--rpc)
```

### RPC サーバの有効化

```bash
# 制御ソケットを有効にして起動
python . path/to/cartridge.rom --rpc

# 任意：ソケットパスを指定（複数インスタンス運用時など）
python . path/to/cartridge.rom --rpc --rpc-socket /tmp/py_msx_alt.sock
```

RPC メソッドは、デバッガの一時停止/ステップ/継続、ブレークポイントとウォッチポイ
ント、メモリ・VRAM の読み書き、逆アセンブル、VDP レジスタ、キーボード/ジョイス
ティック入力、スクリーンショット取得、ステートセーブ、ディスク入れ替えを網羅しま
す。ワイヤプロトコルと全メソッドの一覧は
[`extras/msx_emulator_rpc_spec.md`](extras/msx_emulator_rpc_spec.md) を参照してくだ
さい。

同梱クライアントによる簡単な動作確認:

```bash
python tools/rpc_client.py debugger.status
python tools/rpc_client.py memory.read address=0xC000 length=16
```

### MCP サーバの登録

MCP サーバにはオプションの `mcp` 依存が必要です。

```bash
pip install -e '.[mcp]'      # または: pip install 'mcp[cli]>=1.0'
```

Claude Code に一度だけ登録します（`.mcp.json` に書き込まれます）。

```bash
claude mcp add --transport stdio --scope project msx-emulator \
    -- python tools/mcp_server.py
claude mcp list        # msx-emulator  ●  connected
```

既定以外のソケットを使う場合は、環境変数 `MSX_RPC_SOCKET`（`.mcp.json` の `env`
ブロックで設定可能）で指定します。

### セキュリティ上の注意

- Unix ソケットは、同一ユーザで動作するローカルプロセスからのみ到達可能です。
- `memory.write` と `cpu.step` はマシン状態を変更するため、**一時停止中のみ**実行
  できます。
- 認証はありません。共有ホストでは `chmod 600` でソケットを保護してください。制御
  インターフェースであるため、サーバは明示的なオプトイン（`--rpc`）方式で、指定し
  ない限りソケットは作成されません。

---

## マシン設定

ハードウェア構成（VDP の種類、RAM サイズ、スロット配線、ROM ファイル）は `config/machines/` 以下の YAML ファイルで宣言されます。`--machine` フラグでマシン ID を指定します。省略時は ROM データベースが世代を自動判定します（MSX1 ROM → `cbios_msx1_jp`；MSX2 ROM またはカートリッジなし → `cbios_msx2_jp`）。

### 利用可能なマシン ID

| ID              | 世代 | 地域               | VDP      |
| --------------- | ---- | ------------------ | -------- |
| `cbios_msx1`    | MSX1 | インターナショナル | TMS9918A |
| `cbios_msx1_jp` | MSX1 | 日本               | TMS9918A |
| `cbios_msx1_eu` | MSX1 | ヨーロッパ         | TMS9918A |
| `cbios_msx1_br` | MSX1 | ブラジル           | TMS9918A |
| `cbios_msx2`    | MSX2 | インターナショナル | V9938    |
| `cbios_msx2_jp` | MSX2 | 日本（デフォルト） | V9938    |
| `cbios_msx2_eu` | MSX2 | ヨーロッパ         | V9938    |
| `cbios_msx2_br` | MSX2 | ブラジル           | V9938    |
| `hb_f1xd`       | MSX2 | 日本               | V9938    |

`hb_f1xd`（Sony HB-F1XD）は実機 ROM を使用し、WD2793 フロッピーディスクドライブを備えます。`hb-f1xd_basic-bios2.rom`・`hb-f1xd_msx2sub.rom`・`hb-f1xd_disk.rom` を `roms/hb_f1xd/` に配置し、`--fdd1` でディスクをマウントします。

### マシン YAML の構造

マシンファイルには CPU、スロット配線、内蔵デバイスを宣言します。デバイス定義は `config/devices/` に分離して記述し、`ref:` で参照します。

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
| `generation` | `msx1` または `msx2`；VDP クラスとメモリモデルを決定 |
| `cpu.clock_mhz` | Z80A クロック周波数（NTSC MSX は 3.579545 MHz） |
| `cpu.m1_wait_states` | M1（オペコードフェッチ）ごとの追加 T ステート（任意；デフォルト 0 = 純正 Z80）。MSX 機は `1` |
| `slots.primary.N` | プライマリスロット N：`{type: cartridge}`、`{type: ram, ...}`、またはインライン ROM `content` |
| `slots.primary.N.expanded` | `true` で 4 つのセカンダリスロットに拡張 |
| `builtin_devices` | スロット非経由で直接配線するデバイス：VDP、PSG、PPI、RTC、RAM マッパー |
| `overrides` | デバイスデフォルトへの浅いマージ（例：V9938 の `vram_kb: 128`） |
| `sha1` | `null` はハッシュ検証なしで読み込む |

独自のマシン定義を追加するには、`config/machines/` に新しい YAML ファイルを置き、その `id` を `--machine` に渡してください。デバイス YAML で `implemented: false` と記述されたエントリは、ロード時に警告を表示してスキップされます。

---

## テストの実行

テストスイートは 1485 個のテストで構成されており、個々のオペコードやハードウェアレジスタを対象としたユニットテスト、複数コンポーネントを組み合わせた統合テスト、仕様書のシナリオから直接導出したシナリオレベルのテストが含まれます。

```bash
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
│   ├── diagnostics/       # DebugLogger、CPU/I/O トレース、ハング検出器
│   ├── debugger/          # インタラクティブ REPL（プロンプト、逆アセンブラ）
│   ├── machine.py         # コンポーネント配線とフレームループ
│   ├── machine_loader.py  # YAML ベースのマシン設定ローダ
│   ├── memory.py          # スロットベースのメモリバス
│   ├── mapper.py          # カートリッジマッパー（Flat、ASCII8/16、Konami、SCC...）
│   ├── mapper_tracer.py   # カートリッジバンク切り替えトレーサ
│   ├── ram_mapper.py      # MSX2 RAM マッパー（128 KB、8 セグメント）
│   ├── rtc.py             # RP5C01 リアルタイムクロック
│   ├── psg.py             # AY-3-8910 PSG + オーディオ合成（サブフレーム PCM）
│   ├── audio_filter.py    # アナログ的な出力ローパス（BiquadLowPass）
│   ├── scc.py             # Konami SCC ウェーブテーブルシンセサイザ
│   ├── ppi.py             # i8255 PPI（スロットレジスタ、キーボード）
│   ├── io.py              # I/O バス（ポートディスパッチ）
│   ├── input.py           # キーボードマトリクス + ジョイスティック入力状態
│   ├── joystick.py        # 物理ジョイスティックマネージャ（SDL2）
│   ├── frame_timer.py     # 60 fps ペーシング + FPS 計測
│   ├── romdb.py           # SHA1 ベースの ROM タイトル/マッパーデータベース
│   ├── screenshot.py      # RGB24→PNG 書き出し（スクリーンショット + ステート画像）
│   └── state.py           # マシン状態のセーブ/ロード（JSON + PNG）
├── config/
│   ├── devices/           # デバイス YAML 定義（VDP、PSG、PPI、RTC...）
│   └── machines/          # マシン YAML 定義（cbios_msx1_jp、cbios_msx2_jp...）
├── roms/
│   └── cbios/             # C-BIOS ROM ファイルをここに置く（バージョン管理外）
├── saves/                 # ステートセーブとスクリーンショット（実行時に生成）
├── openspec/
│   └── specs/             # コンポーネント仕様書（公開リポジトリには含まれていません）
├── tests/                 # テストスイート — 1485 テスト
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

現時点では正式な CONTRIBUTING.md はありません。重要な変更についてはプルリクエストを提出する前に GitHub Issue で議論してください。対象タイトル以外の ROM に関するバグ報告は歓迎します；特定タイトルの互換性修正はベストエフォートで対応します。

---

## 謝辞

- **[openMSX](https://openmsx.org/)** — ROM 識別データは openMSX softwaredb.xml（https://github.com/openMSX/openMSX）を参照していますが、すべてのエントリは独自に収集した事実情報です。openMSX は GNU GPL v2 でリリースされています。
- **[C-BIOS](https://cbios.sourceforge.net/)** — テストに使用している無償の MSX BIOS 代替品。

---

## ライセンス

MIT — [LICENSE](LICENSE) を参照してください。
