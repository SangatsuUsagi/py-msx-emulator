# 拡張機能

`--extension1 <id>`/`--extension2 <id>` は、解決済みのベースマシンの上に、
`config/extensions/<id>.yaml` の YAML フラグメントで定義されたデバイスフラグ
メントを、それぞれプライマリスロット 1 またはスロット 2 に重ね合わせます。各
カートリッジスロットは独立に最大 1 つの拡張をホストできます：`--extension1`
は必ずプライマリスロット 1 を専有するため位置引数のカートリッジ/`--slot1`/
`--mapper1` とは併用できません、`--extension2` は必ずプライマリスロット 2 を
専有するため `--slot2`/`--mapper2` とは併用できません。両スロットの拡張（あ
るいは片方が拡張、もう片方がただのカートリッジという組み合わせ）はそれ以外
は独立で自由に組み合わせられます ── 例外は同じ `id` を両スロットに同時指定す
ることで、これは起動時に拒否されます（理由は下記の[「スロットモデル」節](#スロットモデル)
を参照）。同じ id は `py_emulator.yaml` の `extension1:`/`extension2:` キーで
も設定でき（従来の裸の `extension:` キーも `extension2:` のレガシー別名とし
て使えます）、`--extension1 none`/`--extension2 none` はそのスロットに関して
のみ、設定ファイルの指定を 1 回の実行に限り上書きします。FM-PAC/SCC-I/HBI-J1
の実装詳細（メモリマップ、I/O ポート、既知の制限）は
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

マシン構築時に各プライマリスロットが何を保持できるか、そして `--extension1`/
`--extension2` が実際に到達できるスロットはどれかをまとめます。

| スロット | サブスロット拡張 | 拡張手段 | 保持できるもの |
| --- | --- | --- | --- |
| 0 | なし | いいえ | メイン BIOS ROM のみ（オプションでページ 2 にロゴ ROM を追加可能） |
| 1 | あり、0〜3（`--extension1` 経由のみ ── マシン YAML 単体でスロット 1 を拡張状態にするものはない） | `--extension1` | カートリッジ ROM + マッパー（位置引数のカートリッジ/`--slot1`/`--mapper`/`--mapper1`）、**または**、拡張状態にした場合：スロット 2 と同じサブスロットデバイス形状（下記） |
| 2 | あり、0〜3（`--extension2` 経由のみ ── マシン YAML 単体でスロット 2 を拡張状態にするものはない） | `--extension2` | カートリッジ ROM + マッパー（`--slot2`/`--mapper2`）、**または**、拡張状態にした場合：任意個数の ROM サブスロット（各サブスロットは `flat_rom`、もしくはバンク切り替え型マッパー 1 種 ── `ascii8`、`ascii16`、`halnote` のいずれか）、最大 1 つの `ram_mapper` サブスロット、そして拡張全体で最大 1 つの `io_device`（現状は `kanji_rom` のみで、専用のスロット位置を持たない） |
| 3 | あり、0〜3（マシン定義のみ） | いいえ | マシン YAML（`config/machines/*.yaml`）ごとに固定で、`--extension1`/`--extension2` では選択不可：MSX2 自体の SUB ROM、RAM マッパーまたはフラット RAM、そして実機で検証済みの 2 マシンではフロッピーディスクコントローラ（`hb_f1xd`：WD2793、`fs_a1f`：TC8566AF） |

スロット 1・スロット 2 はそれぞれ独立に拡張可能で、スロットごとに拡張は 1 つ
までです（つまり *異なる* 拡張をそれぞれのスロットに配置する組み合わせは自由
に可能 ── 例：スロット 1 に FM-PAC、スロット 2 に HBI-J1、あるいは片方にゲー
ムカートリッジ、もう片方に拡張）。まだサポートされていないのは *同じ* id を
両スロットに同時指定することです：`--extension1`/`--extension2` は `Machine`
のスロットごとのカートリッジ SRAM・セーブステートの仕組みを共有しており、実
機でもこれを行う正当な使用例はほとんど存在しません（理論上あり得るのは
FM-PAC/SCC-I カートリッジを 2 枚挿してより豊かな多重和音を得るケースですが、
これは対象外です）。これは起動時に「同じ ROM/拡張 id」チェックとして拒否され、
下記の KonamiSCC 単一チップ共有チェック（これも引き続き有効）とは独立です。
スロット 3 自身のサブスロット拡張は `config/machines/*.yaml` に宣言されたベー
スマシンのプロパティであり、どちらのカートリッジスロットの拡張とも独立して
影響を受けません（すべての拡張はそれぞれ専用のセカンダリスロットレジスタを
持ち、共存します）。

スロットごとの独立性だけでは解決しない点が 1 つあります：KonamiSCC カート
リッジマッパーと `scc_plus` 拡張はいずれも最終的に実際の SCC 音源チップを構
築し、`Machine` は単一の `scc` フィールドしか持ちません ── そのため、一方の
スロットのマッパーが `KonamiSCC` でもう一方のスロットの拡張が `scc_plus`
（あるいは両スロットのマッパーがともに `KonamiSCC`）という組み合わせは、実機
が同時に 2 つの SCC チップを搭載することがないのと同じ「物理チップは 1 つ」
という理由で拒否されます。

## FM-PAC

`--extension1`/`--extension2 fmpac` ── [FM-PAC（ＦＭパナアミューズメントカートリッジ）](https://generation-msx.nl/software/matsushita-electric-industrial/fm-pana-amusement-cartridge/1072) は実在する
MSX-MUSIC カートリッジで、YM2413（OPLL）FM 音源チップ、64 KB のバンク切り替え
ROM、8 KB の電池バックアップ SRAM を備えます。カートリッジ自体の ROM が
`roms/fmpac/fmpac.rom` に必要です ── このプロジェクトには含まれないため、各自
でダンプしてください。

## SCC-I カートリッジ (SCC+)

`--extension1`/`--extension2 scc_plus` ──
[Konami の SCC（052539）](https://www.msx.org/wiki/Konami_052539)サウンドチッ
プをモデルにした、単体のサウンドカートリッジです。フラグが対象とするプライ
マリスロットに無条件に接続され、64 KB の物理バンク切り替え RAM を 128 KB であ
るかのように扱い
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

`--extension1`/`--extension2 hbi_j1` ──
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

`--extension1`/`--extension2 memory_512k` は実在するカートリッジに対応するも
のではありません ── MSX2 のメインメモリ向けに、フラグが対象とするプライマリ
スロットに配置される 512 KB のバンク切り替え RAM 拡張です。主な用途は、スロッ
ト 3 に RAM マッパーを持たないマシンで MSX-DOS2 を動作させることです。片方の
スロットに MSX-DOS2 カーネルカートリッジ、もう片方にこの拡張を配置すれば、
MSX-DOS2 を動かすのに十分なバンク RAM を備えたマシンになります。ROM ファイル
は不要です ── RAM は実行のたびに空の状態で開始し、セーブファイルへの永続化は
ありません。両スロットで `ram_mapper` を要求する拡張（例えばこれと、同じく
`ram_mapper` を宣言する `msxdos2_512k`）を同時指定することは拒否されます ──
上記の[「スロットモデル」節](#スロットモデル)を参照。

```bash
# スロット 1 に MSX-DOS2 カーネルカートリッジ、スロット 2 にこの拡張の RAM を組み合わせる
python . path/to/msxdos2_kernel.rom --extension2 memory_512k --machine hb_f1xd --fdd1 path/to/disk.dsk
```

## msxdos2_512k: 512 KB RAM + MSX-DOS2 カーネル

`--extension1`/`--extension2 msxdos2_512k` もまた実在するカートリッジではあ
りません ── `memory_512k` の 512 KB RAM 拡張と、MSX-DOS2 カーネル ROM を同一
のスロットフラグメント内に組み合わせたもので、`--extension1`/`--extension2`
フラグ 1 つだけで、もう片方のスロットに別途カーネルカートリッジを用意するこ
となく MSX-DOS2 を動作させられます。

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
python . --extension2 msxdos2_512k --machine hb_f1xd --fdd1 path/to/disk.dsk
```

## msxdos2_512k_kanjirom: + 漢字フォント ROM

`--extension1`/`--extension2 msxdos2_512k_kanjirom` は、`msxdos2_512k` に MSX 標準の JIS 漢字
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
python . --extension2 msxdos2_512k_kanjirom --machine hb_f1xd --fdd1 path/to/disk.dsk
```

## msxdos2_512k_viewfont: + View フォント ROM

`--extension1`/`--extension2 msxdos2_512k_viewfont` は、MSX-View 付属アドオンパッケージに同梱
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
python . --extension2 msxdos2_512k_viewfont --machine hb_f1xd --fdd1 path/to/disk.dsk
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
