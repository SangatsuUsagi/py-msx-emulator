# ソケット RPC と MCP サーバ

エミュレータは、ローカルの制御インターフェースを外部ツールに公開できます。外部ツールとは、シェルスクリプト、テストハーネス、Claude Code のような AI コーディングエージェントなどです。外部ツールは、SDL ウィンドウを使わずに、エミュレータの一時停止、Z80/VDP の状態の確認、入力の送信、スクリーンショットの取得ができます。次の 2 つが連携して動きます。

- **ソケット RPC**（`host/rpc_server.py`）：エミュレータ自体に組み込まれた、Unix ドメインソケット上の JSON-RPC サーバ。`--rpc` で有効化します。
- **MCP サーバ**（`tools/mcp_server.py`）：ソケット RPC を MCP ツールとしてラップするスタンドアロンプロセスで、Claude Code から直接呼び出せます。

どちらもデフォルトでは無効であり、通常の実行に新たなランタイム依存を追加しません。ターミナルのデバッガと同じブレークポイント・ウォッチポイント・ステップ実行のモデルを使っています（[`docs/debugger_ja.md`](debugger_ja.md) を参照）。RPC を使うと、このモデルを人間の代わりにプログラムから操作できます。

この文書は両レイヤーのリファレンスであり、コードに追随して更新します。[`docs/msx_emulator_rpc_spec.md`](msx_emulator_rpc_spec.md) は同じ機能の当初の設計記録です。実装の前に書いたもので、ソースの全文を含みます。設計判断の理由はそちらを参照してください。現在の挙動はこの文書を参照してください。

---

## アーキテクチャ

```
MCP client  <-- MCP (stdio) -->  tools/mcp_server.py  <-- Unix socket -->  host/rpc_server.py (in-process)
```

`tools/mcp_server.py` は、エミュレータの状態を自分では持ちません。ツールを呼び出すたびに、ソケットへの短い接続を開き、RPC リクエストを 1 つ送り、接続を閉じます。そのため、呼び出しの合間にエミュレータを再起動しても、MCP サーバは動き続けます。エミュレータがまだソケットで待ち受けていなければ、そのリクエストは失敗します。

エミュレータプロセス内部では、`DebugServer`（`host/rpc_server.py`）がソケットの accept/read ループをバックグラウンドのデーモンスレッドで実行します。リクエストはそのスレッドでパースしますが、実行はしません。各リクエストはキューに積み、ホストループが `DebugServer.drain()` を呼び出したときに、エミュレータのメインスレッドで処理します。ホストループはこれを 1 フレームに 1 回呼び、デバッガで一時停止している間は繰り返し呼びます。そのため、CPU/VDP のホットパスにロックは要りません。RPC ハンドラが CPU のステップや VDP の描画と同時に動くことはないからです。

1 つ例外があります。`cpu.continue_sync` は、呼び出し元のソケットスレッドを次の一時停止イベントまでブロックします。そのためサーバはこれをキューに積まず、そのスレッドで直接処理します（`DebugServer._continue_sync` を参照）。このスレッドはコアを呼びません。エミュレータのスレッドは、待っている呼び出しを起こす前に一時停止の記録を書きます。ソケットスレッドはその記録をもとに応答します。

コアのエミュレータ（`machine`、`cpu`、`vdp`、`memory`）は RPC について一切関知しません。この機能で `socket`・`json`・`threading` を使うのは `host/rpc_server.py` だけです。コアが RPC サーバを呼ぶことはありません。ブレークすると `step_frame` が停止理由を返し、フロントエンドのループがそれを `DebugServer.on_core_stop` に渡します（`host/debugger/stop.py`）。このレイヤー構造が Python 以外への移植先にどう対応するかは、`host/rpc_server.py` 冒頭の移植性に関する注記を参照してください。

---

## RPC を有効にしてエミュレータを起動する

```bash
# デフォルトのソケットパス (/tmp/py_msx_emu.sock)
python . path/to/game.rom --machine cbios_msx2_jp --rpc

# ソケットパスを指定
python . path/to/game.rom --machine cbios_msx2_jp --rpc --rpc-socket /tmp/alt.sock
```

`--rpc` が効くのはインタラクティブな SDL 実行モードだけです（`--count-frame` / `--benchmark` では効きません）。`py_emulator.yaml` でも RPC を有効にできます。`--rpc-socket` は `rpc.socket` より優先します。

```yaml
rpc:
  enabled: true
  socket: /tmp/py_msx_emu.sock # 省略可
```

起動時にエミュレータはバインドしたパスを表示します。

```
rpc     : /tmp/py_msx_emu.sock
```

サーバが同時に受け付けるクライアントは 1 つだけです。ソケットパスに古いファイルが残っているとき（クラッシュしたプロセスの残骸など）は、サーバが次の起動時にそれを消して作り直します。

RPC は Unix ドメインソケットを使うため、macOS か Linux が必要です。エミュレータ自体が対象としているのと同じプラットフォームです。Windows は未検証です（[`QUICK_START_ja.md`](../QUICK_START_ja.md) を参照）。

---

## ワイヤープロトコル

トランスポートは `AF_UNIX` / `SOCK_STREAM` で、改行区切りの JSON でフレーミングします。1 行に UTF-8 の JSON オブジェクトを 1 つ置きます。

接続時、サーバはバナー通知を送信します。

```json
{ "notification": "connected", "version": "1.0", "emulator": "py-msx-emulator" }
```

### リクエストとレスポンス

```json
// リクエスト
{"id": "1", "method": "debugger.status", "params": {}}

// 成功
{"id": "1", "result": {"paused": false, "pc": "0xC010", "reason": "user_request"}}

// エラー
{"id": "1", "error": {"code": 1, "message": "emulator must be paused for this operation"}}
```

`params` は引数のないメソッドでは省略できます。サーバは、リクエストの `id` を必ずそのままレスポンスに入れて返します。

### エラーコード

| コード   | 意味                                                                                  |
| -------- | ------------------------------------------------------------------------------------- |
| `-32700` | パースエラー（不正な JSON）                                                           |
| `-32601` | メソッドが見つかりません                                                              |
| `-32602` | 不正な params                                                                         |
| `1`      | エミュレータが一時停止していることが必要な操作                                        |
| `3`      | エミュレータ内部エラー。dispatch timeout（[既知の制限](#既知の制限)を参照）を含みます |

（`2` は使っていません。プロトコルはこのコードに意味を定めていません。）

### サーバプッシュ通知

エミュレータが一時停止するたびに、サーバは接続中のクライアントへ、リクエストとは無関係にフレームをプッシュします。このフレームには `id` がありません。一時停止のきっかけは、ブレークポイント、ウォッチポイント、ステップの完了、`debugger.pause` の呼び出しです。

```json
{
  "notification": "paused",
  "reason": "breakpoint",
  "pc": "0xC080",
  "registers": { "AF": "0x0100", "BC": "0x0010", "...": "..." }
}
```

プッシュ更新が不要なクライアントは `notification` キーのあるフレームを無視できます。`tools/mcp_server.py` と `tools/rpc_client.py` はどちらも、自分のレスポンスの `id` を待つ間これらを読み飛ばします。

---

## メソッドリファレンス

`reason` を返すのは、`debugger.status`、`debugger.pause`、`cpu.continue_sync` と `paused` 通知です。値は `"user_request"`、`"breakpoint"`、`"watchpoint"`、`"step_complete"` のどれかです。`cpu.continue_sync` は、時間内にエミュレータが一時停止しなかったときに `"timeout"` も返します。

### デバッガ制御

- **`debugger.status`** → `{ paused, pc, reason }`。状態を変更せずに報告します。エミュレータの実行中は、`reason` は直前の一時停止の理由です（最初の一時停止の前は `"user_request"`）。
- **`debugger.pause`** → `{ paused: true, pc, reason: "user_request" }`。ターミナルでの Ctrl+C はこれと同じではありません。Ctrl+C はデバッガの REPL を開き、REPL から再開するまでサーバは要求を処理しません。

### CPU / 実行

- **`cpu.step`** _（一時停止が必要）_ → `{ pc, t_states, mnemonic, registers }`。Z80 命令を 1 つだけ実行します。
- **`cpu.continue`** _（一時停止が必要）_ → `{ running: true }`。再開して即座に返ります（非ブロッキング）。ブレークで止まったフレームは、止まった位置から続きを実行します。
- **`cpu.continue_sync`** _（一時停止が必要）_：params は `{ timeout_ms? }`（デフォルト 5000）。再開し、次の一時停止イベントまたはタイムアウトまでブロックします。`{ paused: true, reason, pc, registers }` または `{ paused: false, reason: "timeout" }` を返します。サーバはこれをソケットスレッドで処理します。そのため、呼び出しがブロックしてもエミュレータループは止まりません。
- **`cpu.get_registers`** → `{ registers }`。どの状態でも動作します。

`registers` オブジェクトは、`AF`、`BC`、`DE`、`HL`、`IX`、`IY`、`SP`、`PC`（`0xXXXX` の 16 進文字列）、シャドウレジスタ `AF'`/`BC'`/`DE'`/`HL'`、`I`/`R`（`0xXX`）、`IFF1`/`IFF2`（真偽値）、`IM`（整数）が入ります。

### ブレークポイント / ウォッチポイント

- **`debug.set_breakpoint`**：params は `{ address }` → `{ id, address, active }`。
- **`debug.remove_breakpoint`**：params は `{ id }` → `{ removed }`。
- **`debug.list_breakpoints`** → `{ breakpoints: [{ id, address, active }] }`。
- **`debug.set_watchpoint`**：params は `{ address, mode? }`（`"r"` / `"w"` / `"rw"`、デフォルト `"rw"`）→ `{ id, address, mode, active }`。
- **`debug.remove_watchpoint`**：params は `{ id }` → `{ removed }`。

アドレスは JSON の整数または 16 進文字列（`"0xC000"` あるいは `"C000"`）のどちらでも指定できます。マシンが持てるのは、最大 4 個のブレークポイントと 4 個のウォッチポイントです（[インタラクティブデバッガ](debugger_ja.md)の `ba`/`wa` の上限と同じ）。5 個目の `debug.set_*` 呼び出しは invalid params のエラー（`-32602`）を返し、設定済みの 4 個はそのまま有効です。

RPC のメソッドは、`--break-point`、`--watch-point` オプションや `ba`/`wa` コマンドと、マシンの同じ設定を共有します。追加と削除は自分のエントリだけを変え、4 個の上限はすべてのエントリを数えます。マシンは 1 つのアドレスに 1 つのエントリしか持ちません。そのため、削除するとデバッガが同じアドレスに設定したエントリも消えます。また、ウォッチ中のアドレスにウォッチポイントを設定すると、そのアドレスにモードが加わります。`debug.list_breakpoints` の `active` は、アドレスが設定されていないとき（たとえばデバッガの `br` で消した後）`false` です。

### メモリ

- **`memory.read`**：params は `{ address, length? }`（デフォルト 16）→ `{ address, data }`。`data` はスペース区切りの 16 進バイト列です。長さは 0〜0x10000 です。それ以外の長さは invalid-params エラー（`-32602`）になります。
- **`memory.write`** _（一時停止が必要）_：params は `{ address, data }`（16 進バイト、例：`"3E 01 C9"`）→ `{ written }`。
- **`memory.read_vram`**：params は `{ address, length? }`（デフォルト 32）→ `{ address, data }`。アドレスは VRAM サイズ（0x4000 か 0x20000）内でラップするので、MSX2 では 0xFFFF より上のアドレスで 64 KB より上の VRAM を読めます。そのとき結果の `address` は 16 進 5 桁（`0x1C000`）です。長さは 0〜VRAM サイズです。それ以外の長さは invalid-params エラー（`-32602`）になります。
- **`memory.disassemble`**：params は `{ address, count? }`（デフォルト 10）→ `{ instructions: [{ address, bytes, mnemonic }] }`。

### VDP

- **`vdp.get_registers`** → `{ type: "V9938"|"TMS9918A"|"unknown", registers }`（`R0`.. を 16 進で）。
- **`vdp.get_status`** → `{ status }`（16 進バイト）。

どちらもどの状態でも動作します。

### 入力の注入

- **`input.press_key`** / **`input.release_key`**：params は `{ row, bit }`（生のキーボードマトリクスセル）。
- **`input.press_key_named`**：params は `{ key, duration_ms? }`（デフォルト 100）。`key` は MSX のキー名（`SPACE`、`RETURN`、`ESC`、`UP`/`DOWN`/`LEFT`/`RIGHT`、`SHIFT`、`CTRL`、`GRAPH`、`CAPS`、`CODE`、`STOP`、`HOME`、`INS`、`DEL`、`BS`、`TAB`、`SELECT`、`F1`-`F5`、`A`-`Z`、`0`-`9`）で、大文字小文字を区別しません。キーを押し、`duration_ms` 後に自動的に離します。
- **`input.joystick`**：params は `{ port? (1|2, デフォルト 1), up?, down?, left?, right?, trigger_a?, trigger_b? }`（すべて真偽値）→ `{ port, state }`。この状態は次の呼び出しまで変わりません。
- **`input.joystick_release`**：params は `{ port? }`。そのポートのすべての方向とトリガーをクリアします。

すべての入力系メソッドは一時停止中・実行中の両方で動作します。入力は、次に PPI やジョイスティックを読んだときに効きます。

### スクリーンショット

- **`screen.capture`**：params は `{ scale? }`（デフォルト 1）→ `{ width, height, format: "png", encoding: "base64", data }`。フレームカウンタを進めずに現在の VDP フレームを描画します。どの状態でも動作します。

### ステート保存 / 読み込み

- **`state.save`**：params は `{ path? }` → `{ path }`。マシン全体のスナップショットを `saves/states/<title>_YYYYMMDD_HHMMSS.state` に保存し、同じ名前の `.png` スクリーンショットも書き込み、`saves/states/latest.state` を更新します。デバッガの `sv` コマンドと同じ形式、同じ保存先です。`path` を渡すと、そのファイル名部分がタイトルになります。出力先には使いません。`path` がなければ、タイトルは `rpc_checkpoint` です。
- **`state.load`**：params は `{ path }`（必須）→ `{ path, loaded: true }`。

### フロッピーディスク

- **`fdd.swap`**：params は `{ drive (1|2), path? }` → `{ drive, path, mounted }`。`path` を指定するとその `.dsk` イメージをマウントし、省略するか null にするとディスクを取り出します。マシンにフロッピーインターフェースがない場合はエラーになります。

### カセットテープ

どのメソッドも一時停止を必要としません。失敗したとき（カセットポートがない、テープがない、ファイルや位置が不正）はコード `-32602` を返し、テープは変えません。

- **`tape.insert`**：params は `{ path }`（必須）→ `{ mounted: true, blocks }`。`.cas` ファイルを変換し、位置 0 でマウントします。対応するのは CAS だけです。WAV ファイルはエラーになります。WAV の録音は、先に [`tools/wav2cas.py`](../tools/README_tools_ja.md#wav2caspy) で変換してください。
- **`tape.eject`** → `{ mounted: false }`。
- **`tape.rewind`** → `tape.status` と同じ結果。位置を 0 に戻します。
- **`tape.seek`**：params は `{ seconds }` か `{ block }` のどちらか 1 つ → `tape.status` と同じ結果。`seconds` は 0 以上の数、`block` は `tape.blocks` のブロック番号です。
- **`tape.status`** → `{ mounted, path, motor, position, length, position_seconds, length_seconds, block }`。`position` と `length` は 3.579545 MHz のクロックで数えた T ステートです。`block` は、位置かそれより前で始まる最後のブロックです。テープがないときは `path` と `block` が null、数値は 0 です。
- **`tape.blocks`** → `{ blocks: [{ number, type, name, start, start_seconds, length }] }`。`type` はファイルヘッダなら `binary`、`basic`、`ascii`、ほかのブロックなら `data` です。`name` はヘッダのファイル名で、データのブロックでは null です。`length` はバイト数です。テープがないときは空の一覧を返します。

モーターを動かすメソッドはありません。モーターを回したり止めたりするのはゲストだけです（例：`BLOAD"CAS:",R`）。

---

## 手動テスト：`tools/rpc_client.py`

MCP を介さずソケットを直接操作するための、シンプルな CLI クライアントです。

```bash
python tools/rpc_client.py debugger.status
python tools/rpc_client.py debugger.pause
python tools/rpc_client.py memory.read address=0xC000 length=16
python tools/rpc_client.py debug.set_breakpoint '{"address": "0xC000"}'
python tools/rpc_client.py --socket /tmp/alt.sock cpu.get_registers
```

params は `key=value` の組か、1 つの JSON オブジェクトで指定します。`key=value` の値は int、bool、文字列のどれかに変換します。レスポンスを待つ間に受け取ったプッシュ通知は stderr に出力します。レスポンスは整形した JSON として stdout に出力します。エラーレスポンスのときは、プロセスが 0 以外のコードで終了します。

---

## MCP サーバ

`tools/mcp_server.py` は `FastMCP` ベースの stdio サーバで、各ツール呼び出しを 1 つの RPC リクエストに変換し、結果をテキスト（スクリーンショットの場合は画像も）として整形します。ソケットパスは環境変数 `MSX_RPC_SOCKET`（デフォルト `/tmp/py_msx_emu.sock`）から読みます。そのため、MCP 登録の `env` ブロックで、プロジェクトごとにデフォルト以外のソケットを指定できます。

### ツール一覧

ほとんどのツールは、[メソッドリファレンス](#メソッドリファレンス)に記載した同名の RPC メソッド（`.` を `_` に置き換えた名前）をラップします。名前の違うツールは 4 つです。`emulator_status` と `emulator_pause` は `debugger.status` と `debugger.pause` を、`cpu_continue_until_pause` は `cpu.continue_sync` を、`input_press_key` は `input.press_key_named` をラップします。生のキーボードマトリクスを行とビットで指定する 2 メソッド（`input.press_key` / `input.release_key`）に対応するツールはありません。「一時停止が必要」は、あらかじめエミュレータが一時停止していることを要求するツールを示します。

- `emulator_status`、`emulator_pause`
- `cpu_get_registers`、`cpu_step`（一時停止が必要）、`cpu_continue`（一時停止が必要、非ブロッキング）、`cpu_continue_until_pause(timeout_seconds=30)`（一時停止が必要、ブロッキング）
- `debug_set_breakpoint(address)`、`debug_remove_breakpoint(breakpoint_id)`、`debug_list_breakpoints`、`debug_set_watchpoint(address, mode="rw")`、`debug_remove_watchpoint(watchpoint_id)`
- `memory_read(address, length=16)`、`memory_write(address, data_hex)`（一時停止が必要）、`memory_disassemble(address, count=10)`、`memory_read_vram(address, length=32)`
- `vdp_get_registers`、`vdp_get_status`
- `input_press_key(key, duration_ms=100)`、`input_joystick(port=1, up=, down=, left=, right=, trigger_a=, trigger_b=)`、`input_joystick_release(port=1)`
- `screen_capture(scale=1)`：テキストに加えて画像を返します
- `state_save(path="")`、`state_load(path)`
- `fdd_swap(drive, path="")`
- `tape_insert(path)`、`tape_eject`、`tape_rewind`、`tape_seek(seconds=None, block=None)`、`tape_status`、`tape_blocks`

アドレス引数は、`0x` プレフィックスの有無を問わず 16 進表記を受け付けます。MCP サーバは、RPC メソッドを呼ぶ前にこの値を正規化します。ソケットに接続できないときは、どのツールもエラーになります。エラーメッセージはソケットパスを示し、エミュレータが `--rpc` 付きで動いているかを尋ねます。

### オプション依存関係のインストール

MCP サーバには `mcp[cli]` パッケージが必要です。通常のインストールにこの依存が入らないよう、オプションの extra として宣言しています。

```bash
pip install -e ".[mcp]"
```

これをインストールしないと、`tools/mcp_server.py` は起動時に `ModuleNotFoundError: No module named 'mcp'` で失敗します。Claude Code にサーバを登録する前に、上記の extra をインストールしてください。

---

## Claude Code からエミュレータを使う

### 1. MCP サーバを登録する（プロジェクトごとに 1 回）

```bash
claude mcp add --transport stdio --scope project msx-emulator \
    -- python tools/mcp_server.py
```

`python` には、`mcp` パッケージを入れた Python を使ってください。仮想環境に入れた場合は、`python` の代わりにその環境のインタプリタをフルパスで指定します。このコマンドは `.mcp.json` にエントリを書き込みます。Claude Code は、このエントリを使うセッションを始めるときに、`tools/mcp_server.py` を自動で起動します。手動で常駐させておく必要はありません。登録は次のコマンドで確認できます。

```bash
claude mcp list
```

デフォルト以外のソケットを指定する場合は、`env` ブロックを追加します。

```bash
claude mcp add --transport stdio --scope project msx-emulator \
    --env MSX_RPC_SOCKET=/tmp/alt.sock \
    -- python tools/mcp_server.py
```

### 2. `--rpc` 付きでエミュレータを起動する

```bash
python . path/to/game.rom --machine cbios_msx2_jp --rpc
```

これを別のターミナルで動かし続けてください。Claude Code の Bash ツールでバックグラウンドで起動してもかまいません。MCP ツールが使えるのは、エミュレータのプロセスがソケットで待ち受けている間だけです。

### 3. Claude Code セッションから操作する

MCP サーバを登録済みであれば、Claude に `msx-emulator` のツールを直接使うよう依頼できます。例えば：

- 「エミュレータを一時停止して Z80 レジスタをダンプし、現在の PC から逆アセンブルして」
- 「0xC080 にブレークポイントを設定して continue し、何が起きたか教えて」
- 「3 倍スケールでスクリーンショットを撮って、画面に何が表示されているか確認して」
- 「SPACE キーを押して、1 秒後の画面を確認して」

典型的なデバッグの流れは次のようになります。

1. `emulator_pause` → PC と理由を確認します。
2. `memory_disassemble` / `cpu_get_registers` → 状態を確認します。
3. `debug_set_breakpoint` + `cpu_continue_until_pause` → 注目したい箇所まで実行します。
4. `screen_capture` → 描画内容を視覚的に確認します。
5. `cpu_continue` → 通常の実行を再開します。

---

## セキュリティに関する注意

- ソケットは Unix ドメインソケットなので、届くのはローカルのプロセスだけです。ソケット自体に認証の仕組みはありません。アクセスはソケットファイルのパーミッションで決まります。サーバは起動のたびにソケットを作り直すので、ファイルへの `chmod` は次の起動までしか効きません。共有ホストでは、`--rpc-socket` で自分だけが入れるディレクトリにソケットを置いてください。
- `memory.write` と `cpu.step` は命令のちょうど境目で状態を変えるので、エミュレータが一時停止しているときだけ動きます。
- RPC は、`--rpc` を付けるか `py_emulator.yaml` で `rpc.enabled` を設定しない限り無効です。通常の実行では、ソケットを開かず、バックグラウンドスレッドも起動しません。

---

## 既知の制限

- **アクティブなクライアントは 1 つのみ。** accept ループはシングルスレッドなので、一度に処理できるクライアントは 1 つだけです。同じソケットに対して 2 つの MCP セッション（または MCP セッションと `tools/rpc_client.py`）を同時に動かすと、後から接続した方は、最初の接続が切れるまで待たされます。
- **エミュレータの自動起動はしません。** MCP サーバはエミュレータを起動しません。先に `--rpc` 付きで別に起動しておいてください。起動していないと、最初のツール呼び出しが、[ツール一覧](#ツール一覧)で説明した接続エラーで失敗します。
- **`cpu.continue_sync` のタイムアウトは呼び出し単位です。** `timeout_ms` が経過する前にエミュレータが一時停止しなければ、無期限には待たず、`{ paused: false, reason: "timeout" }` を返します。エミュレータ自体は実行を続けます。
- **MCP サーバがレスポンスを待つのは最大 60 秒です。** `tools/mcp_server.py` はソケットに 60 秒のタイムアウトを設定しています。そのため、`timeout_seconds` を 60 以上にした `cpu_continue_until_pause` は、エミュレータが応答する前にソケットのタイムアウトで失敗します。`timeout_seconds` は 60 未満にしてください。
- **リクエストはホストループの稼働を前提とします。** `cpu.continue_sync` 以外のメソッドはすべて `DebugServer.drain()` 用のキューに積まれます（ホストは 1 フレームに 1 回、一時停止中は繰り返し呼びます）。エミュレータが止まっていて 30 秒以内にキューを処理しないときは、リクエストが `"dispatch timeout"` の内部エラー（コード `3`）で失敗します。クライアントが固まることはありません。
