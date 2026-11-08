# Tools

エミュレータ本体とは別に動く、ディスクイメージ管理・テープ用 WAV との相互変換・CPU 命令テストの実行・エージェント向けRPC/MCP層のコマンドラインツール群です。
いずれもエミュレータのプロセス内では動作せず、`python tools/<script>.py` として個別に実行します。

| スクリプト / セクション | 用途 |
| --- | --- |
| [`dskftp.py`](#dskftppy) | `.dsk` イメージ内のファイルを編集する、ftp風の対話シェル |
| [`dskblank.py`](#dskblankpy) | 未フォーマットの空 `.dsk` イメージを作成 |
| [`dskdd.py`](#dskddpy) | `.dsk` イメージと物理USBフロッピードライブ間で生セクタをコピー(Linux専用) |
| [`bas2wav.py`](#bas2wavpy) | ASCII 形式の BASIC プログラムを、実機の MSX が `LOAD"CAS:"` で読み込めるテープ用 WAV に変換 |
| [`wav2cas.py`](#wav2caspy) | MSX のテープの WAV を、エミュレータの `--tape` で使う `.cas` ファイルに変換 |
| [`run_z80_exercisers.py`](#run_z80_exerciserspy) | Z80 の命令テスト(zexdoc、zexall、z80test)を CPU コア単体で実行 |
| [`run_singlestep_tests.py`](#run_singlestep_testspy) | SingleStepTests/z80 の 1 命令ごとのテストを CPU コア単体で実行 |
| [`golden_state.py`](#golden_statepy) | コア全体の決まった実行(ゴールデンケース)を動かし、そのハッシュを記録または照合 |
| [リモート制御（Socket RPC & MCP）](#リモート制御socket-rpc--mcp) | `rpc_client.py` と `mcp_server.py` が使うソケットRPC + MCP制御層の概要 |
| [`rpc_client.py`](#rpc_clientpy) | 手動テスト用、エミュレータのソケットRPCサーバーへの薄いCLIクライアント |
| [`mcp_server.py`](#mcp_serverpy) | ソケットRPC APIをラップするMCPサーバー。Claude Codeから起動中のエミュレータを操作できる |

いずれも素のPython 3.10+で、プロジェクト本体の依存(`requirements.txt` /
`requirements-dev.txt`)以外の追加依存はありません。ただし `mcp_server.py` だけは
オプションの `mcp` 依存も必要です([`mcp_server.py`](#mcp_serverpy) を参照)。実行には
プロジェクトのvenvを使います(例: `venvs/py-msx-emulator/bin/python tools/dskftp.py`)。

---

## `dskftp.py`

MSX-DOS(FAT12)`.dsk` ディスクイメージ向けの、ftp風の対話シェルです。エミュレー
タを起動せずにディレクトリを閲覧し、ファイルのアップロード/ダウンロード/削除が
できます。標準的なMSXフロッピー形式(1DD 360KB/320KB、2DD 720KB/640KB)を扱えます。
MSX BASICの `CALL FORMAT` や本ツール自身の `format` コマンドでフォーマットした
イメージにも対応します。サブディレクトリ(MSX-DOS2)にも対応しており、MSX-DOS1
のイメージはルートディレクトリしか持たないものとしてそのまま扱われます。

書き込みはすべて即座にイメージファイルへ反映されます(別途の保存操作はありませ
ん)。転送は常にバイナリで、CR/LF変換は行いません。

```bash
python tools/dskftp.py game.dsk        # 起動時に接続
python tools/dskftp.py                 # 後から `open` で接続
```

シェル内の主なコマンドを次の表に示します(完全な一覧と使い方は `help` を参照)。

| コマンド | 動作 |
| --- | --- |
| `open <image.dsk>` | ディスクイメージに接続 |
| `format <image.dsk> [720\|640\|360\|320]` | 空イメージを作成(容量KB、既定720)して接続 |
| `dir` / `ls [path]` | リモートディレクトリを一覧(pathの末尾はワイルドカード可) |
| `cd <dir>` | リモートディレクトリを変更 |
| `lcd [dir]` | ローカルディレクトリを変更(引数無しなら表示) |
| `pwd` / `lpwd` | リモート / ローカルの作業ディレクトリを表示 |
| `get <remote> [local]` | ファイルをダウンロード |
| `put <local> [remote]`(別名 `send`) | ファイルをアップロード |
| `mget <pattern>` / `mput <pattern>` | glob パターンで複数ファイルをダウンロード / アップロード |
| `delete <file>` | リモートファイルを削除 |
| `mkdir <dir>` / `rmdir <dir>` | リモートディレクトリを作成 / 削除 |
| `quit` / `bye`(または Ctrl-D) | シェルを終了 |

`format` はデータディスクを作成します。標準的なMSX-DOSブートセクタローダー
(`CALL FORMAT` が書き込むのと同じ、"Boot error / Press any key for retry" の
スタブ)は含みますが、`MSXDOS.SYS` は含みません。DOS は起動できません。読み書き
は、ふつうの FAT12 ボリュームとしてだけできます。

## `dskblank.py`

`--fdd1` / `--fdd2` で使う、空の `*.dsk` イメージを作成します。スクリプトはファイルを
`0xE5`(フロッピーの標準フィルバイト)で埋めます。そのため、トラックはフォーマット
済みで、ファイルシステムのないディスクのように見えます。ブートセクタ、BPB、FAT
は書き込みません。作成後はエミュレータ内でMSX BASICの `CALL FORMAT` を使うか、
`dskftp.py format` でフォーマットしてください。

```bash
python tools/dskblank.py blank.dsk               # 720 KB (2DD、既定)
python tools/dskblank.py blank.dsk --size 360    # 360 KB (1DD)
python tools/dskblank.py blank.dsk --force       # 既存ファイルを上書き
```

## `dskdd.py`

`.dsk` イメージとUSB接続の3モードフロッピードライブ間で生セクタをコピーしま
す。**Linux専用。** `dd` と同じく、ブロックデバイスとしてディスクを読み書きします。

```bash
sudo python3 tools/dskdd.py read /dev/sdb msxdisk.dsk            # ディスク -> イメージ
sudo python3 tools/dskdd.py read /dev/sdb msxdisk.dsk --retries 5
sudo python3 tools/dskdd.py write msxdisk.dsk /dev/sdb --verify  # イメージ -> ディスク
```

LinuxではUSB FDDは(`/dev/fd0` ではなく)ブロックデバイスとして `/dev/sdX` に
現れます。実行前に `sudo ufiformat -i` でデバイスノードを調べ、`sudo ufiformat
-i /dev/sdX` で挿入中のメディアを確認してください。

どちらの方向も、次の条件をすべて満たさない限り実行を拒否します。

- 対象がブロックデバイスである
- マウントされていない
- メディアが入っている
- フロッピーとして妥当なサイズである(4MB以下、`--force` で上書き可能)

`write` は、さらにデバイスパスを操作者にもう一度入力させます。誤ったデバイスに
書き込むと、そのデバイスのデータがすべて失われるからです。`read` は `--overwrite` を指定しない限り既存イメージ
を上書きしません。

| サブコマンド | オプション |
| --- | --- |
| `read <device> <image>` | `--sectors N`(既定: デバイス全体)、`--retries N`(既定: 2)、`--overwrite`、`--force` |
| `write <image> <device>` | `--yes`(確認省略)、`--verify`(書き込み後に読み戻して比較)、`--force` |

終了コード: `0` 成功、`1` エラー、`2` イメージ化はできたが読み取れないセクタ
が残った場合(イメージ内では0埋めされ、標準出力に一覧表示されます)。

## `bas2wav.py`

ASCII 形式の BASIC プログラム(`.BAS` のテキストファイル)を、MSX のテープの
WAV ファイルに変換します。この WAV を実機の MSX に再生して聞かせると、プログラム
を手で打ち込まずに読み込めます。WAV の中身は 1200 ボーの MSX の ASCII テープ
ファイルで、`SAVE"CAS:NAME"` が書くものと同じ形式です。

```bash
python tools/bas2wav.py PROGRAM.BAS program.wav
python tools/bas2wav.py PROGRAM.BAS program.wav --invert   # 信号を反転
```

読み込みの手順:

1. コンピュータのヘッドホン出力を、MSX のカセットケーブルの **EAR** プラグに
   つなぎます。MIC と REMOTE のプラグはつなぎません。
2. MSX で `LOAD"CAS:"` を入力します(読み込み後にそのまま実行するなら
   `RUN"CAS:"`)。
3. WAV を再生します。ファイルが見つかると、MSX に `Found:` とファイル名が表示
   されます。

ファイルが見つからないときやエラーになるときは、まず音量を上げ、次に
`--invert` を試してください。

| オプション | 意味 |
| --- | --- |
| `--name NAME` | テープ上のファイル名、6 文字まで(既定: 元のファイル名を大文字にしたもの) |
| `--invert` | 信号を反転する |
| `--volume V` | ピークの大きさ、0 より大きく 1 以下(既定: 0.8) |
| `--gap S` | 256 バイトの各ブロックの前に入れる無音の秒数。BASIC がブロックを処理する時間になる(既定: 1.0) |

改行は CRLF にそろえます。出力は 48 kHz、16 ビット、モノラルです。この
スクリプトで作った WAV は、[`wav2cas.py`](#wav2caspy) で変換するとエミュレータで
読み込めます。実機での読み込みはまだ試していません。

## `wav2cas.py`

MSX のテープの WAV ファイルを、エミュレータの `--tape` オプションで使う
`.cas` ファイルに変換します。読めるのは 1200 ボーの標準形式で、
`SAVE"CAS:"` が書く形式、`bas2wav.py` が作る形式です。

```bash
python tools/wav2cas.py tape.wav tape.cas
python . --tape tape.cas    # その後、MSX で RUN"CAS:"(または LOAD"CAS:")を入力
```

見つけた各ブロックの長さと先頭のバイトを表示します。8 ビットと 16 ビットの
WAV を読めます。ステレオは平均してモノラルにします。信号が反転していても、
オプションなしでそのまま読めます。ブロックが 1 つも見つからないときは、
ファイルを書かずに終了コード `1` で終わります。

## `run_z80_exercisers.py`

MSX のマシンを組まずに、`Z80` コア単体で Z80 の命令テストを動かします。CP/M の
`.com` プログラム(zexdoc、zexall)と、[z80test](https://github.com/raxoft/z80test)
の `.tap` イメージに対応します。テストプログラムはこのリポジトリに含めていないので、
パスを渡してください。どれかのプログラムが失敗を報告すると終了コードは 1、それ以外は 0 です。

```bash
venvs/pypy-msx-emulator/bin/python tools/run_z80_exercisers.py \
    path/to/zexdoc.com path/to/zexall.com path/to/z80full.tap
```

PyPy で実行してください。zexdoc と zexall はそれぞれ PyPy で約 1.5 分、CPython では
さらに長くかかります。形式ごとの動かし方と期待される結果は
[命令テスト](../docs/technical-implementation_ja.md#命令テスト)を参照してください。

## `run_singlestep_tests.py`

[SingleStepTests/z80](https://github.com/SingleStepTests/z80) のテストを、`Z80`
コア単体で動かします。1 つのファイルが 1 つのオペコードの 1000 件のテストで、各件に
1 命令を実行する前と後の CPU の状態が入っています。このツールは 1 件ごとに 1 命令を
実行し、全レジスタ、MEMPTR(WZ)、Q、IFF1/IFF2、IM、RAM、ポートへの読み書き、
T ステート数を比べます。サイクルごとのバスの状態は比べません。テストファイルは
このリポジトリに含めていないので、パスを渡してください。

```bash
venvs/pypy-msx-emulator/bin/python tools/run_singlestep_tests.py \
    path/to/SingleStepTests/z80/v1/*.json
```

全件は PyPy で実行してください(1604 ファイルで約 3 分)。CPython では `--limit N`
で各ファイルの先頭 N 件だけを流せます。コアが意図して別の結果を出すテストは、
根拠とともに `tools/singlestep_known_divergences.json` に載せています
(`--divergences <ファイル>` で別の一覧を使えます)。これらは別に数え、失敗には
しません。どのテストにも当てはまらなくなった項目は、役目を
終えた項目(stale)として報告します。それ以外の失敗があるか、役目を終えた項目が
あると終了コードは 1、それ以外は 0 です。

テストは実機ではなく、エミュレータのコアから作られています。コアを他の言語に
移植したときも、同じファイルと同じ食い違いの一覧で確かめられます。

## `golden_state.py`

コア全体の決まった実行(ケース)を動かし、結果のハッシュを
`tests/vectors/golden_state.json` と比べます。ケースは、機種、スロット 1 の
カートリッジ(なくてもよい)、入力の手順、確認点のフレームを持ちます。確認点ごとに、
保存状態の部分(`cpu`、`memory`、`vdp`、`psg` など)ごとの SHA-256、そのフレームまでの
音声の SHA-256、そのフレームの映像の SHA-256 を記録できます。一致しないときは、
ケース、フレーム、部分を示します。

リポジトリのファイルには、ケースと確認点のフレームだけがあり、ハッシュはまだ記録していません。
保存状態の形がまだ変わる(カセット、ディスクなどの機器)ので、ハッシュはコアの移植を始める前に、
コアとケースを見直してから記録します。ハッシュのないケースは、照合のときにその旨を表示し、
失敗にはしません。

```bash
venvs/py-msx-emulator/bin/python tools/golden_state.py           # 照合
venvs/py-msx-emulator/bin/python tools/golden_state.py --write   # 記録
```

変更で動作が変わらないことを確かめるには、変更の前にファイルの写しへハッシュを記録し、
変更の後に `--vectors <写し>` でその写しと照合します。ハッシュをリポジトリに記録した後は、
意図して動作を変えたときに `--write` で記録し直し、新しいファイルをコミットします。
テストスイートでは `tests/test_golden_state.py` が同じ照合をします。

ROM はこのリポジトリに含めていません。ROM がないケースや、SHA-256 が違うケースは、
パスを示してスキップします。次のファイルを置いてください。

| パス | 入手元 | SHA-256 |
| --- | --- | --- |
| `roms/cbios/` | [C-BIOS](https://cbios.sourceforge.net/)。C-BIOS の機種を使うときと同じ | (照合しない) |
| `roms/homebrew/noborunoca.rom` | [NOBORUNOCA v1.0.2](https://github.com/h1romas4/noborunoca/releases/tag/v1.0.2) の `noborunoca.rom`(MIT) | `57a3be2fe5c3190daa3e69cf23cd262cdc8af98c60993ea941d43c815cbb25f6` |
| `roms/homebrew/uno-msx2.rom` | [commodore-uno v1.0.5](https://github.com/jhonnaker1/commodore-uno/releases/tag/v1.0.5) の `uno-msx2.rom`(MIT)、または v1.0.5 をビルドしたもの(下記) | `74d74d8720e623a7b8aadce8e0b0c9094f8a2162339a8e7c677c7732f673ddb8` |

表の SHA-256 は、commodore-uno v1.0.5 をビルドした ROM の値です。バージョンが違うと
ROM が変わることがあるので、v1.0.5 を使ってください。v1.0.5 と v1.0.10 のリリースの
`uno-msx2.rom` は、このビルドと同じ SHA-256 です。ROM をビルドするには、
[SDCC](https://sdcc.sourceforge.net/) と GNU make を使います。

```bash
# 1. ソースを取得する
git clone --branch v1.0.5 https://github.com/jhonnaker1/commodore-uno.git
cd commodore-uno/msx2

# 2. ビルドする。ROM は build/uno.rom(16 KB)
make
```

できた `build/uno.rom` を `roms/homebrew/uno-msx2.rom` にコピーし、SHA-256 が表の値と
一致することを確かめてください。

ハッシュが記録しているのは、このコアの動作で、実機の動作ではありません。動作の
変化を検出でき、コアを移植したときも、同じケースを動かして Python のコアと比べられます。

## リモート制御（Socket RPC & MCP）

エミュレータは小さなローカル制御インターフェースを公開できます。外部ツール(シェ
ルスクリプト、テストハーネス、AIコーディングエージェントなど)から実行中のイ
ンスタンスを一時停止・検査・操作できます。制御は次の 2 つの層からなります。

- **Socket RPC**: エミュレータプロセスに組み込まれたUnixドメインソケットの
  JSON-RPCサーバー(`host/rpc_server.py`)。**既定では無効**で、`--rpc` で有効
  化します。
- **MCPサーバー**: 下記の [`mcp_server.py`](#mcp_serverpy)。Socket RPCを
  [Model Context Protocol](https://modelcontextprotocol.io) ツールとしてラッ
  プする、スタンドアロンのstdioサーバーです。Claude Codeのようなクライアント
  は、エミュレータの機能をネイティブのツールとして呼び出せます。スクリーンショ
  ットはインライン画像として受け取れます。

```
MCPクライアント ──stdio/MCP──▶ tools/mcp_server.py ──Unixソケット──▶ エミュレータ (--rpc)
```

### RPCサーバーの有効化

```bash
# 制御ソケットを有効にして起動
python . path/to/game.rom --rpc

# 任意: ソケットパスを指定(複数インスタンス運用時など)
python . path/to/game.rom --rpc --rpc-socket /tmp/py_msx_alt.sock
```

RPCメソッドで次の操作ができます。

- デバッガの一時停止/ステップ/継続
- ブレークポイントとウォッチポイント
- メモリの読み書き、VRAMの読み出し、逆アセンブル
- VDPレジスタ
- キーボード/ジョイスティック入力
- スクリーンショット取得
- ステートのセーブ/ロード
- ディスク入れ替え
- テープの挿入/取り出し/巻き戻し/シーク

ワイヤプロトコルと全メソッドの一覧は
[`../docs/socket-rpc-mcp_ja.md`](../docs/socket-rpc-mcp_ja.md) を参照してく
ださい。

MCP層を介さない手動テストは下記の [`rpc_client.py`](#rpc_clientpy)、MCPクラ
イアントからの操作は下記の [`mcp_server.py`](#mcp_serverpy) を参照してくださ
い。

### セキュリティ上の注意

- Unixソケットは、同一ユーザーで動作するローカルプロセスからのみ到達可能で
  す。
- `memory.write` と `cpu.step` はマシン状態を変更するため、**一時停止中のみ**
  実行できます。
- 認証はありません。共有ホストでは `chmod 600` でソケットを保護してください。
  保護しないと、同一ホストの他のユーザーが接続して `memory.write`/
  `cpu.step` を呼び出し、実行中のマシンの状態を書き換えられます。サーバーは制
  御インターフェースなので、明示的なオプトイン(`--rpc`)方式にしています。指定
  しない限り、ソケットは作られません。

## `rpc_client.py`

エミュレータのUnixソケットJSON-RPCサーバー向けの、手動テスト用の薄いCLIクラ
イアントです。MCP層を介さずメソッドを1回呼び出し、レスポンスを表示します。エ
ミュレータを `--rpc` 付きで起動しておく必要があります。メソッドの全リファレ
ンスは [`../docs/socket-rpc-mcp_ja.md`](../docs/socket-rpc-mcp_ja.md) を参照してく
ださい。

```bash
python tools/rpc_client.py debugger.status
python tools/rpc_client.py debugger.pause
python tools/rpc_client.py memory.read address=0xC000 length=16
python tools/rpc_client.py debug.set_breakpoint '{"address": "0xC000"}'
python tools/rpc_client.py --socket /tmp/alt.sock cpu.get_registers
```

パラメータはJSONオブジェクトまたは `key=value` 形式で指定でき、各値は
`int`/`bool`/`str` に適宜変換されます。既定のソケットパスは
`/tmp/py_msx_emu.sock` です。

## `mcp_server.py`

エミュレータのソケットRPC APIをMCPツールとしてラップし、Claude CodeなどのMCP
クライアントから起動中のエミュレータを直接操作できるようにします。主な操作を
次に挙げます。

- Z80のポーズ/ステップ/再開
- ブレークポイント・ウォッチポイントの設定
- メモリの読み書き、VRAMの読み出し
- キーボード・ジョイスティック入力の注入
- スクリーンショットの取得

アーキテクチャとツールの全リストは
[`../docs/socket-rpc-mcp_ja.md`](../docs/socket-rpc-mcp_ja.md) を参照してくださ
い。

MCPサーバーにはオプションの `mcp` 依存が必要です。

```bash
pip install -e '.[mcp]'      # または: pip install 'mcp[cli]>=1.0,<2.0'
```

```bash
# RPCサーバーを有効にしてエミュレータを起動:
python . path/to/game.rom --rpc

# このMCPサーバーをClaude Codeに登録(初回のみ、.mcp.json に書き込まれます):
claude mcp add --transport stdio --scope project msx-emulator \
    -- python tools/mcp_server.py
claude mcp list        # msx-emulator  ●  connected
```

登録後はClaude Codeがセッションごとに自動でこのプロセスを起動します。サーバー
はソケットパスを環境変数 `MSX_RPC_SOCKET` から読みます(既定は
`/tmp/py_msx_emu.sock`)。上記のように登録した場合は、この変数を `.mcp.json` の
`env` ブロックで設定できます。
