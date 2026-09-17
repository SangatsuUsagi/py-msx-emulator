# Tools

エミュレータ本体とは別に動く、ディスクイメージ管理・エージェント向けRPC/MCP層のコマンドラインツール群です。
いずれもエミュレータのプロセス内では動作せず、`python tools/<script>.py` として個別に実行します。

| スクリプト / セクション | 用途 |
| --- | --- |
| [`dskftp.py`](#dskftppy) | `.dsk` イメージ内のファイルを編集する、ftp風の対話シェル |
| [`dskblank.py`](#dskblankpy) | 未フォーマットの空 `.dsk` イメージを作成 |
| [`dskdd.py`](#dskddpy) | `.dsk` イメージと物理USBフロッピードライブ間で生セクタをコピー(Linux専用) |
| [リモート制御（Socket RPC & MCP）](#リモート制御socket-rpc--mcp) | `rpc_client.py` と `mcp_server.py` が使うソケットRPC + MCP制御層の概要 |
| [`rpc_client.py`](#rpc_clientpy) | 手動テスト用、エミュレータのソケットRPCサーバーへの薄いCLIクライアント |
| [`mcp_server.py`](#mcp_serverpy) | ソケットRPC APIをラップするMCPサーバー。Claude Codeから起動中のエミュレータを操作できる |

いずれも素のPython 3.10+で、プロジェクト本体の依存(`requirements.txt` /
`requirements-dev.txt`)以外の追加依存はありません。プロジェクトのvenvで実行し
ます: 例 `venvs/py-msx-emulator/bin/python tools/dskftp.py`。

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

シェル内のコマンド(完全な一覧と使い方は `help` を参照):

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
スタブ)は含みますが、`MSXDOS.SYS` は含みません。そのためDOSとしては起動でき
ず、単純なFAT12ボリュームとして読み書きされるだけです。

## `dskblank.py`

`--fdd1` / `--fdd2` で使う、空の `*.dsk` イメージを作成します。ファイルは
`0xE5`(フロッピーの標準フィルバイト)で埋められており、ブートセクタ・BPB・
FATは一切書き込まれず、フォーマット前の未使用メディアのように見える状態になり
ます。作成後はエミュレータ内でMSX BASICの `CALL FORMAT` を使うか、
`dskftp.py format` でフォーマットしてください。

```bash
python tools/dskblank.py blank.dsk               # 720 KB (2DD、既定)
python tools/dskblank.py blank.dsk --size 360    # 360 KB (1DD)
python tools/dskblank.py blank.dsk --force       # 既存ファイルを上書き
```

## `dskdd.py`

`.dsk` イメージとUSB接続の3モードフロッピードライブ間で生セクタをコピーしま
す。**Linux専用。**
[`extras/msx_2dd_disk_imaging_linux.md`](../extras/msx_2dd_disk_imaging_linux.md)
にある「USB FDD + dd」方式の両方向を実装しています:

```bash
sudo python3 tools/dskdd.py read /dev/sdb msxdisk.dsk            # ディスク -> イメージ
sudo python3 tools/dskdd.py read /dev/sdb msxdisk.dsk --retries 5
sudo python3 tools/dskdd.py write msxdisk.dsk /dev/sdb --verify  # イメージ -> ディスク
```

LinuxではUSB FDDは(`/dev/fd0` ではなく)ブロックデバイスとして `/dev/sdX` に
現れます。実行前に `sudo ufiformat -i` でデバイスノードを調べ、`sudo ufiformat
-i /dev/sdX` で挿入中のメディアを確認してください。

どちらの方向も、次のすべてを満たさない限り実行を拒否します:

- 対象がブロックデバイスである
- マウントされていない
- メディアが入っている
- フロッピーとして妥当なサイズである(4MB以下、`--force` で上書き可能)

`write` はさらに、
誤ったデバイスへの書き込みで既存データが失われるのを防ぐため、デバイスパスを
操作者に再入力させます。`read` は `--overwrite` を指定しない限り既存イメージ
を上書きしません。

| サブコマンド | オプション |
| --- | --- |
| `read <device> <image>` | `--sectors N`(既定: デバイス全体)、`--retries N`(既定: 2)、`--overwrite`、`--force` |
| `write <image> <device>` | `--yes`(確認省略)、`--verify`(書き込み後に読み戻して比較)、`--force` |

終了コード: `0` 成功、`1` エラー、`2` イメージ化はできたが読み取れないセクタ
が残った場合(イメージ内では0埋めされ、標準出力に一覧表示されます)。

## リモート制御（Socket RPC & MCP）

エミュレータは小さなローカル制御インターフェースを公開できます。外部ツール(シェ
ルスクリプト、テストハーネス、AIコーディングエージェントなど)から実行中のイ
ンスタンスを一時停止・検査・操作できます。2つの層があります:

- **Socket RPC** — エミュレータプロセスに組み込まれたUnixドメインソケットの
  JSON-RPCサーバー(`msx/rpc_server.py`)。**既定では無効**で、`--rpc` で有効
  化します。
- **MCPサーバー** — 下記の [`mcp_server.py`](#mcp_serverpy)。Socket RPCを
  [Model Context Protocol](https://modelcontextprotocol.io) ツールとしてラッ
  プするスタンドアロンのstdioサーバーで、Claude Codeのようなクライアントがエ
  ミュレータ機能をネイティブツールとして呼び出せます(スクリーンショットはイ
  ンライン画像として受け取れます)。

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

RPCメソッドは次を網羅します:

- デバッガの一時停止/ステップ/継続
- ブレークポイントとウォッチポイント
- メモリ・VRAMの読み書き、逆アセンブル
- VDPレジスタ
- キーボード/ジョイスティック入力
- スクリーンショット取得
- ステートセーブ
- ディスク入れ替え

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
- 認証はありません。共有ホストでは `chmod 600` でソケットを保護してください
  ——保護しないと、同一ホストの他のユーザーが接続して `memory.write`/
  `cpu.step` を呼び出し、実行中のマシンの状態を自由に書き換えられてしまいま
  す。制御インターフェースであるため、サーバーは明示的なオプトイン
  (`--rpc`)方式で、指定しない限りソケットは作成されません。

## `rpc_client.py`

エミュレータのUnixソケットJSON-RPCサーバー向けの、手動テスト用の薄いCLIクラ
イアントです。MCP層を介さずメソッドを1回呼び出し、レスポンスを表示します。エ
ミュレータを `--rpc` 付きで起動しておく必要があります。メソッドの全リファレ
ンスは [`../docs/socket-rpc-mcp.md`](../docs/socket-rpc-mcp.md) を参照してく
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
クライアントから起動中のエミュレータを直接操作できるようにします。主な操作は
次のとおりです:

- Z80のポーズ/ステップ/再開
- ブレークポイント・ウォッチポイントの設定
- メモリ/VRAMの読み書き
- キーボード・ジョイスティック入力の注入
- スクリーンショットの取得

アーキテクチャとツールの全リストは
[`../docs/socket-rpc-mcp.md`](../docs/socket-rpc-mcp.md) を参照してくださ
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

登録後はClaude Codeがセッションごとに自動でこのプロセスを起動します。ソケッ
トパスは環境変数 `MSX_RPC_SOCKET`(既定 `/tmp/py_msx_emu.sock`、上記のように
登録した場合は `.mcp.json` の `env` ブロックで設定可能)から読み込まれます。
