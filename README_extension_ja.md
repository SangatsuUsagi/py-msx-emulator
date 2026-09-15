# 拡張機能

`--extension <id>` は、解決済みのベースマシンの上に、`config/extensions/<id>.yaml`
の YAML フラグメントで定義されたデバイスフラグメントをプライマリスロット 2 に
重ね合わせます。1 回の実行で有効にできる拡張は最大 1 つで、必ずプライマリスロッ
ト 2 を専有するため、`--slot2`/`--mapper2`（プライマリスロット 1 を対象とする
カートリッジ ROM 引数や `--mapper` とは自由に併用可能）とは併用できません。同じ
`id` は `py_emulator.yaml` の `extension:` キーでも設定でき、`--extension none`
はその設定ファイルの指定を 1 回の実行に限り上書きします。FM-PAC/SCC-I/HBI-J1 の
実装詳細（メモリマップ、I/O ポート、既知の制限）は
[`README_ja.md`の「対応ハードウェア」節](README_ja.md#対応ハードウェア)を参照
してください。このドキュメントでは、各拡張が何のためのもので、どの ROM ファイル
を必要とするかに絞って説明します。

[English version here](README_extension.md)

- [スロットモデル](#スロットモデル)
- [FM-PAC](#fm-pac)
- [SCC-I カートリッジ (SCC+)](#scc-i-カートリッジ-scc)
- [Sony HBI-J1](#sony-hbi-j1)
- [memory_512k: 512 KB RAM 拡張](#memory_512k-512-kb-ram-拡張)
- [msxdos2_512k: 512 KB RAM + MSX-DOS2 カーネル](#msxdos2_512k-512-kb-ram--msx-dos2-カーネル)
- [msxdos2_512k_kanjirom: + 漢字フォント ROM](#msxdos2_512k_kanjirom--漢字フォント-rom)
- [msxdos2_512k_viewfont: + View フォント ROM](#msxdos2_512k_viewfont--view-フォント-rom)
- [ROM ファイル配置](#rom-ファイル配置)

---

## スロットモデル

マシン構築時に各プライマリスロットが何を保持できるか、そして `--extension` が
実際に到達できるスロットはどれかをまとめます。

| スロット | サブスロット拡張 | `--extension` で拡張可能か | 保持できるもの |
| --- | --- | --- | --- |
| 0 | なし | いいえ | メイン BIOS ROM のみ（オプションでページ 2 にロゴ ROM を追加可能） |
| 1 | なし | いいえ | カートリッジ ROM + マッパーのみ（`--mapper`） |
| 2 | あり、0〜3（`--extension` 経由のみ ── マシン YAML 単体でスロット 2 を拡張状態にするものはない） | **はい ── `--extension` が到達できる唯一のスロット** | カートリッジ ROM + マッパー（`--slot2`/`--mapper2`）、**または**、拡張状態にした場合：任意個数の ROM サブスロット（各サブスロットは `flat_rom`、もしくはバンク切り替え型マッパー 1 種 ── `ascii8`、`ascii16`、`halnote` のいずれか）、最大 1 つの `ram_mapper` サブスロット、そして拡張全体で最大 1 つの `io_device`（現状は `kanji_rom` のみで、専用のスロット位置を持たない） |
| 3 | あり、0〜3（マシン定義のみ） | いいえ | マシン YAML（`config/machines/*.yaml`）ごとに固定で、`--extension` では選択不可：MSX2 自体の SUB ROM、RAM マッパーまたはフラット RAM、そして実機で検証済みの 2 マシンではフロッピーディスクコントローラ（`hb_f1xd`：WD2793、`fs_a1f`：TC8566AF） |

`--extension` で拡張できるのはスロット 2 のみです。スロット 3 自身のサブスロット
拡張は `config/machines/*.yaml` に宣言されたベースマシンのプロパティであり、その
時点で有効な `--extension` とは独立して影響を受けません（2 つの拡張はそれぞれ
専用のセカンダリスロットレジスタを持ち、共存します）。

同じ理由から、拡張同士を組み合わせることもできません。これにより現状、SCC-I と
FM-PAC の同時使用、MSX-DOS2 で HBI-J1 の MSX-JE を使った日本語入力、MSX-DOS2 上
での FM-PAC 対応アプリケーションの実行のいずれもできません。スロット 1 もスロッ
ト 2 と同様に拡張可能にすること（将来の変更として計画中）で、この制限は解消され
る見込みです。

## FM-PAC

`--extension fmpac` ── [FM-PAC](https://www.msx.org/wiki/Panasoft_SW-M004) は実在する
MSX-MUSIC カートリッジで、YM2413（OPLL）FM 音源チップ、64 KB のバンク切り替え
ROM、8 KB の電池バックアップ SRAM を備えます。カートリッジ自体の ROM が
`roms/fmpac/fmpac.rom` に必要です ── このプロジェクトには含まれないため、各自
でダンプしてください。

## SCC-I カートリッジ (SCC+)

`--extension scc_plus` ──
[Konami の SCC（052539）](https://www.msx.org/wiki/Konami_052539)サウンドチッ
プをモデルにした、単体のサウンドカートリッジです。プライマリスロット 2 に無条
件に接続され、64 KB の物理バンク切り替え RAM を 128 KB であるかのように扱い
（空 ── ROM/データファイルは一切ロードされません）、バンクレジスタの bit 3 を
無視することでブロック N がブロック N+8 をミラーするようにしています ──
これは実機の改造として知られる
[「2 つの 64 KB バンクを接続する」](http://bifi.msxnet.org/msxnet/tech/soundcartridge.html)
を再現したもので、1 枚の物理 SCC-I カートリッジが、このプロジェクトの対象タイ
トル 2 本がそれぞれ前提とする 2 種類の工場出荷時 RAM 実装パターンのどちらでも
動作できるようにしています。ROM ファイルは不要です。

> **Note**: 作者は SCCI カートリッジ、および SCCI カートリッジを使用するソフト
> ウェアを所持しておらず、公開情報を元に実装していますが、検証はできていませ
> ん。

## Sony HBI-J1

`--extension hbi_j1` ──
[Sony HBI-J1](https://www.msx.org/wiki/Sony_HBI-J1) は実在する漢字ROM +
MSX-JE ワードプロセッサカートリッジです。以下の 3 つの ROM ダンプが必要です。

```
roms/hbi_j1/
├── hbi-j1_kanjibasic.rom   (32 KB  ── 漢字ドライバ + BASIC 拡張)
├── hbi-j1_kanjifont.rom    (256 KB ── JIS 漢字フォント ROM)
└── hbi-j1_msx-je.rom       (1 MB   ── MSX-JE ワードプロセッサ ROM)
```

これらは商用の Sony 製 ROM であり、**このプロジェクトには含まれていません** ──
このプロジェクトが許諾できるいかなるライセンスの下でも再配布できないもので
す。HBI-J1 カートリッジを実際に所持している場合は、各自で ROM をダンプし、上
記のパスに配置してください。それ以外に正当な入手方法はありません。

## memory_512k: 512 KB RAM 拡張

`--extension memory_512k` は実在するカートリッジに対応するものではありません
── MSX2 のメインメモリ向けに、プライマリスロット 2 に配置される 512 KB のバン
ク切り替え RAM 拡張です。主な用途は、スロット 3 に RAM マッパーを持たないマ
シンで MSX-DOS2 を動作させることです。スロット 1 に MSX-DOS2 カーネルカート
リッジ、スロット 2 にこの拡張を配置すれば、MSX-DOS2 を動かすのに十分なバンク
RAM を備えたマシンになります。ROM ファイルは不要です ── RAM は実行のたびに空
の状態で開始し、セーブファイルへの永続化はありません。

```bash
# スロット 1 に MSX-DOS2 カーネルカートリッジ、スロット 2 にこの拡張の RAM を組み合わせる
python . path/to/msxdos2_kernel.rom --extension memory_512k --machine hb_f1xd --fdd1 path/to/disk.dsk
```

## msxdos2_512k: 512 KB RAM + MSX-DOS2 カーネル

`--extension msxdos2_512k` もまた実在するカートリッジではありません ──
`memory_512k` の 512 KB RAM 拡張と、MSX-DOS2 カーネル ROM を同一のスロット 2
フラグメント内に組み合わせたもので、`--extension` フラグ 1 つだけで、スロット
1 に別途カーネルカートリッジを用意することなく MSX-DOS2 を動作させられます。

この拡張が前提とするカーネル ROM は
[`msxdos2s`](https://github.com/b3rendsh/msxdos2s)（オープンな MSX-DOS2 カー
ネル実装）で、`roms/msxdos2s/msxd22s.rom` に配置します。**使用前に当該プロジェ
クトのライセンスを必ず確認してください** ── 本稿執筆時点では教育目的・非商用
利用のみが許可されており、無制限な再配布や商用利用は認められていません。

> **Note**: MSX-DOS2 の起動には、この拡張の DOS2 カーネル ROM に加えて、
> `MSXDOS2.SYS` と `COMMAND2.COM` を含むフロッピーディスクイメージが必要です。
> `cbios_*` マシンにはフロッピーディスクコントローラが一切ないため MSX-DOS2 は
> 起動しません ── 代わりに `--machine hb_f1xd` または `fs_a1f`（どちらもコン
> トローラを備えています）を使用してください。`MSXDOS2.SYS`/`COMMAND2.COM` は
> このプロジェクトには**含まれません**。これは以下の
> [msxdos2_512k_kanjirom](#msxdos2_512k_kanjirom--漢字フォント-rom) と
> [msxdos2_512k_viewfont](#msxdos2_512k_viewfont--view-フォント-rom) にも同様
> に当てはまります。

```bash
# MSX-DOS2 を直接起動 ── スロット 1 のカートリッジ不要、カーネルは拡張に内蔵済み
python . --extension msxdos2_512k --machine hb_f1xd --fdd1 path/to/disk.dsk
```

## msxdos2_512k_kanjirom: + 漢字フォント ROM

`--extension msxdos2_512k_kanjirom` は、`msxdos2_512k` に MSX 標準の JIS 漢字
フォント ROM I/O デバイス（ポート `0xD8`〜`0xDB`）を追加し、MSX-DOS2 アプリケー
ションが漢字を表示できるようにします。設定上は、HBI-J1 拡張自身のフォントダン
プ `roms/hbi_j1/hbi-j1_kanjifont.rom` を再利用します ── ライセンスに関する注意
は上記[Sony HBI-J1](#sony-hbi-j1)節のものがそのまま当てはまります。
`kanji_rom` I/O デバイスと互換性のある他の JIS 漢字フォント ROM ダンプに差し
替える場合は、`config/extensions/msxdos2_512k_kanjirom.yaml` の
`io_device.rom` ブロックを編集してください。

これは固定ポートの I/O デバイス形状のみに対応するもので、*マッパー方式*（メモ
リマップされた ROM カートリッジ）の漢字フォント（MSX-View 付属アドオンパッケー
ジに同梱されているようなもの）は別のデバイス種別であり、この拡張ではサポート
されません。その形状については下記の
[msxdos2_512k_viewfont](#msxdos2_512k_viewfont--view-フォント-rom)を参照して
ください。

```bash
# msxdos2_512k と同様、漢字表示にも対応
python . --extension msxdos2_512k_kanjirom --machine hb_f1xd --fdd1 path/to/disk.dsk
```

## msxdos2_512k_viewfont: + View フォント ROM

`--extension msxdos2_512k_viewfont` は、MSX-View 付属アドオンパッケージに同梱
されるフォントカートリッジと互換性のある、ASCII8 マップ方式（8 KB バンクの
MEGA ROM コントローラ）のフォント ROM を、`msxdos2_512k` の上（サブスロット 2、
`msxdos2_512k` 自身の RAM マッパーおよび MSX-DOS2 カーネルと同居）に追加しま
す。

設定上は、
[MSXView 漢字カートリッジ互換ROM](https://littlelimit.net/viewfont.htm)
（`k12x8shn.rom`）── しののめフォントと k12x8 フォントを元にこのカートリッジ
形状専用にビルドされた、無償かつ再配布可能なビットマップフォント ROM ──
を使用しています。そのライセンスは「商用・非商用を問わず、改変の有無にかか
わらず、使用・複製・配布を無制限に許可する」としています。このプロジェクトは
コピーを**同梱していません** ── 上記のリンクからダウンロードし、
`roms/kanji/k12x8shn.rom` に配置してください。他の MSX-View 互換フォント ROM
も使用できます ── `config/extensions/msxdos2_512k_viewfont.yaml` のサブスロッ
ト 2 の `rom` ブロックを編集して差し替えてください。

```bash
# msxdos2_512k と同様、View フォント互換の漢字 ROM を使用
python . --extension msxdos2_512k_viewfont --machine hb_f1xd --fdd1 path/to/disk.dsk
```

## ROM ファイル配置

拡張関連の ROM ファイル（このプロジェクトには含まれません ── 入手方法は上記
の各拡張の節を参照してください）：

```
roms/
├── fmpac/
│   └── fmpac.rom
├── hbi_j1/
│   ├── hbi-j1_kanjibasic.rom
│   ├── hbi-j1_kanjifont.rom
│   └── hbi-j1_msx-je.rom
├── kanji/
│   └── k12x8shn.rom
└── msxdos2s/
    └── msxd22s.rom
```
