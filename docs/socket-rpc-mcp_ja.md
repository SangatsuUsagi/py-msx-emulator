# ソケット RPC と MCP サーバー

エミュレータはローカルの制御インターフェースを公開でき、外部ツール——中でも Claude Code——が SDL ウィンドウに触れることなく、一時停止・Z80/VDP 状態の確認・入力の注入・スクリーンショットの取得を行えます。次の2つの要素が連携して動作します。

- **ソケット RPC**（`msx/rpc_server.py`）：エミュレータ自体に組み込まれた、Unix ドメインソケット上の JSON-RPC サーバー。`--rpc` で有効化します。
- **MCP サーバー**（`tools/mcp_server.py`）：ソケット RPC を MCP ツールとしてラップするスタンドアロンプロセスで、Claude Code から直接呼び出せます。

どちらもデフォルトでは無効であり、通常の実行に新たなランタイム依存を追加しません。ターミナルのデバッガと同じブレークポイント・ウォッチポイント・ステップ実行のモデルを使っています（[`docs/debugger.md`](debugger_ja.md) を参照）。RPC はそれを人間ではなくプログラムから操作できるようにするだけです。

---

## アーキテクチャ

```
Claude Code  <-- MCP (stdio) -->  tools/mcp_server.py  <-- Unix socket -->  msx/rpc_server.py (in-process)
```

`tools/mcp_server.py` はそれ自体エミュレータの状態を保持しません——各ツール呼び出しはソケットへの短命な接続を開き、1つの RPC リクエストを送信して接続を閉じます。これにより MCP サーバーは呼び出しの合間にエミュレータが再起動されても存続でき、まだ何もリッスンしていない場合はそのリクエストが失敗するだけです。

エミュレータプロセス内部では、`DebugServer`（`msx/rpc_server.py`）がソケットの accept/read ループをバックグラウンドのデーモンスレッドで実行します。リクエストはそのスレッドでパースされますが、実行はされません。各リクエストはキューに積まれ、ホストループが `DebugServer.drain()` を呼び出したとき（1フレームに1回、およびデバッガで一時停止している間は継続的に）にエミュレータのメインスレッドで処理されます。これにより CPU/VDP のホットパスはロックフリーのままとなり、RPC ハンドラが CPU ステップや VDP レンダリングと並行して実行されることはありません。

1つ例外があります。`cpu.continue_sync` は呼び出し元のソケットスレッドを次の一時停止イベントまでブロックするため、キューに積まれずそのスレッド上でインラインに処理されます（`DebugServer._continue_sync` を参照）。

コアのエミュレータ（`machine`、`cpu`、`vdp`、`memory`）は RPC について一切関知しません。`msx/rpc_server.py` はこの機能のために `socket`・`json`・`threading` を使用する唯一の場所です。コアが公開するのは汎用の一時停止フック（`Machine.set_pause_hook`）だけであり、RPC サーバーはそこにインストールされます。このレイヤー構造が Python 以外への移植先にどう対応するかは、`msx/rpc_server.py` 冒頭の移植性に関する注記を参照してください。

---

## RPC を有効にしてエミュレータを起動する

```bash
# デフォルトのソケットパス (/tmp/py_msx_emu.sock)
python . path/to/game.rom --machine cbios_msx2_jp --rpc

# ソケットパスを指定
python . path/to/game.rom --machine cbios_msx2_jp --rpc --rpc-socket /tmp/alt.sock
```

`--rpc` はインタラクティブな SDL 実行モードにのみ適用されます（`--count-frame` / `--benchmark` には適用されません）。起動時にエミュレータはバインドしたパスを表示します。

```
rpc     : /tmp/py_msx_emu.sock
```

サーバーは同時に1つのアクティブなクライアントのみを受け付けます。ソケットパスが（クラッシュしたプロセスの残骸などで）既に古いファイルとして存在している場合は、削除してから次回起動時に再バインドされます。

RPC は Unix ドメインソケットを使うため、macOS か Linux が必要です——エミュレータ自体が対象としているのと同じプラットフォームです。Windows は未検証です（メインの README を参照）。

---

## ワイヤープロトコル

トランスポートは `AF_UNIX` / `SOCK_STREAM` で、改行区切りの JSON——1行に1つの UTF-8 JSON オブジェクト——としてフレーミングされます。

接続時、サーバーはバナー通知を送信します。

```json
{"notification": "connected", "version": "1.0", "emulator": "py-msx-emulator"}
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

`params` は引数のないメソッドでは省略できます。リクエストの `id` は常にレスポンスにそのまま返されます。

### エラーコード

| コード | 意味 |
|------|---------|
| `-32700` | パースエラー（不正な JSON） |
| `-32601` | メソッドが見つからない |
| `-32602` | 不正な params |
| `1` | エミュレータが一時停止していることが必要な操作 |
| `2` | 予約済み：実行中状態が必要な操作（現時点で使用するハンドラなし） |
| `3` | エミュレータ内部エラー |

### サーバープッシュ通知

エミュレータが一時停止状態に遷移するたび（ブレークポイント、ウォッチポイント、ステップ完了、または `debugger.pause` 呼び出し）、サーバーは接続中のクライアントに `id` を持たない未要求のフレームをプッシュします。

```json
{
  "notification": "paused",
  "reason": "breakpoint",
  "pc": "0xC080",
  "registers": { "AF": "0x0100", "BC": "0x0010", "...": "..." }
}
```

プッシュ更新が不要なクライアントは `notification` キーを持つフレームを無視できます。`tools/mcp_server.py` と `tools/rpc_client.py` はどちらも、自分のレスポンスの `id` を待つ間これらを読み飛ばします。

---

## メソッドリファレンス

全体で使われる `reason` の値：`"user_request"`、`"breakpoint"`、`"watchpoint"`、`"step_complete"`。

### デバッガ制御

- **`debugger.status`** → `{ paused, pc, reason }`。状態を変更せずに報告します。
- **`debugger.pause`** → `{ paused: true, pc, reason: "user_request" }`。Ctrl+C を押すのと同等です。

### CPU / 実行

- **`cpu.step`** *（一時停止が必要）* → `{ pc, t_states, mnemonic, registers }`。Z80 命令を1つだけ実行します。
- **`cpu.continue`** *（一時停止が必要）* → `{ running: true }`。再開して即座に返ります（非ブロッキング）。
- **`cpu.continue_sync`** *（一時停止が必要）* — params: `{ timeout_ms? }`（デフォルト 5000）。再開し、次の一時停止イベントまたはタイムアウトまでブロックします。`{ paused: true, reason, pc, registers }` または `{ paused: false, reason: "timeout" }` を返します。エミュレータループを止めずにブロックできるよう、ソケットスレッド上で処理されます。
- **`cpu.get_registers`** → `{ registers }`。どの状態でも動作します。

`registers` オブジェクト：`AF`、`BC`、`DE`、`HL`、`IX`、`IY`、`SP`、`PC`（`0xXXXX` の16進文字列）、シャドウレジスタ `AF'`/`BC'`/`DE'`/`HL'`、`I`/`R`（`0xXX`）、`IFF1`/`IFF2`（真偽値）、`IM`（整数）。

### ブレークポイント / ウォッチポイント

- **`debug.set_breakpoint`** — params: `{ address }` → `{ id, address, active }`。
- **`debug.remove_breakpoint`** — params: `{ id }` → `{ removed }`。
- **`debug.list_breakpoints`** → `{ breakpoints: [{ id, address, active }] }`。
- **`debug.set_watchpoint`** — params: `{ address, mode? }`（`"r"` / `"w"` / `"rw"`、デフォルト `"rw"`）→ `{ id, address, mode, active }`。
- **`debug.remove_watchpoint`** — params: `{ id }` → `{ removed }`。

アドレスは JSON の整数または16進文字列（`"0xC000"` あるいは `"C000"`）のどちらでも指定できます。実機側は最大4個のブレークポイントと4個のウォッチポイントしか保持しません（[インタラクティブデバッガ](debugger_ja.md)の `ba`/`wa` の上限と同じ）。5個目の `debug.set_*` 呼び出しも新しい `id` を返しますが、実際に有効化されるのは（割り当て順で）最初の4個だけです。

### メモリ

- **`memory.read`** — params: `{ address, length? }`（デフォルト 16）→ `{ address, data }`。`data` はスペース区切りの16進バイト列です。
- **`memory.write`** *（一時停止が必要）* — params: `{ address, data }`（16進バイト、例：`"3E 01 C9"`）→ `{ written }`。
- **`memory.read_vram`** — params: `{ address, length? }`（デフォルト 32）→ `{ address, data }`。VRAM サイズ内でラップします。
- **`memory.disassemble`** — params: `{ address, count? }`（デフォルト 10）→ `{ instructions: [{ address, bytes, mnemonic }] }`。

### VDP

- **`vdp.get_registers`** → `{ type: "V9938"|"TMS9918A", registers }`（`R0`.. を16進で）。
- **`vdp.get_status`** → `{ status }`（16進バイト）。

どちらもどの状態でも動作します。

### 入力の注入

- **`input.press_key`** / **`input.release_key`** — params: `{ row, bit }`（生のキーボードマトリクスセル）。
- **`input.press_key_named`** — params: `{ key, duration_ms? }`（デフォルト 100）。`key` は MSX のキー名（`SPACE`、`RETURN`、`ESC`、`UP`/`DOWN`/`LEFT`/`RIGHT`、`SHIFT`、`CTRL`、`GRAPH`、`CAPS`、`CODE`、`STOP`、`HOME`、`INS`、`DEL`、`BS`、`TAB`、`SELECT`、`F1`-`F5`、`A`-`Z`、`0`-`9`）で、大文字小文字を区別しません。キーを押し、`duration_ms` 後に自動的に離すようスケジュールします。
- **`input.joystick`** — params: `{ port? (1|2, デフォルト 1), up?, down?, left?, right?, trigger_a?, trigger_b? }`（すべて真偽値）→ `{ port, state }`。状態は次の呼び出しまで保持されます。
- **`input.joystick_release`** — params: `{ port? }` → そのポートのすべての方向とトリガーをクリアします。

すべての入力系メソッドは一時停止中・実行中の両方で動作し、その効果は次の PPI/ジョイスティック読み取り時に反映されます。

### スクリーンショット

- **`screen.capture`** — params: `{ scale? }`（デフォルト 1）→ `{ width, height, format: "png", encoding: "base64", data }`。フレームカウンタを進めずに現在の VDP フレームを描画します。どの状態でも動作します。

### ステート保存 / 読み込み

- **`state.save`** — params: `{ path? }` → `{ path }`。CPU、RAM、VDP、PSG、マッパーのバンク状態をキャプチャします。常に `saves/states/` 配下に新しいタイムスタンプ付きファイルを書き込みます。任意の `path` はスナップショットのタイトルとしてそのファイル名部分を提供するだけで、出力先としては使われません。
- **`state.load`** — params: `{ path }`（必須）→ `{ path, loaded: true }`。

### フロッピーディスク

- **`fdd.swap`** — params: `{ drive (1|2), path? }`。`path` を指定するとその `.dsk` イメージがマウントされ、省略または null にするとイジェクトされます → `{ drive, path, mounted }`。マシンにフロッピーインターフェースがない場合はエラーになります。

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

params は `key=value` の組（int/bool/文字列に変換されます）または単一の JSON オブジェクトのどちらかで指定します。レスポンス待ち中に受信したプッシュ通知は stderr に出力されます。レスポンスは整形された JSON として stdout に出力され、エラーレスポンスの場合はプロセスが非0で終了します。

---

## MCP サーバー

`tools/mcp_server.py` は `FastMCP` ベースの stdio サーバーで、各ツール呼び出しを1つの RPC リクエストに変換し、結果をテキスト（スクリーンショットの場合は画像も）として整形します。ソケットパスは環境変数 `MSX_RPC_SOCKET`（デフォルト `/tmp/py_msx_emu.sock`）から読み込まれるため、プロジェクトごとに MCP 登録の `env` ブロックで非デフォルトのソケットを指定できます。

### ツール一覧

各ツールは[メソッドリファレンス](#メソッドリファレンス)に記載した同名の RPC メソッドと1対1で対応します（ツール名は `.` の代わりに `_` を使います）。

- `emulator_status`、`emulator_pause`
- `cpu_get_registers`、`cpu_step`（一時停止が必要）、`cpu_continue`（一時停止が必要、非ブロッキング）、`cpu_continue_until_pause(timeout_seconds=30)`（一時停止が必要、ブロッキング）
- `debug_set_breakpoint(address)`、`debug_remove_breakpoint(breakpoint_id)`、`debug_list_breakpoints`、`debug_set_watchpoint(address, mode="rw")`、`debug_remove_watchpoint(watchpoint_id)`
- `memory_read(address, length=16)`、`memory_write(address, data_hex)`（一時停止が必要）、`memory_disassemble(address, count=10)`、`memory_read_vram(address, length=32)`
- `vdp_get_registers`、`vdp_get_status`
- `input_press_key(key, duration_ms=100)`、`input_joystick(port=1, up=, down=, left=, right=, trigger_a=, trigger_b=)`、`input_joystick_release(port=1)`
- `screen_capture(scale=1)` — テキストだけでなく実際の画像を返す
- `state_save(path="")`、`state_load(path)`
- `fdd_swap(drive, path="")`

「一時停止が必要」は、あらかじめエミュレータが一時停止していることを要求するツールを示します。アドレス引数は `0x` プレフィックスの有無を問わず16進表記を受け付け、サーバーが RPC メソッド呼び出し前に正規化します。ソケットに到達できない場合、すべてのツールはソケットパスを明示し、`--rpc` 付きでエミュレータが動いているか確認するよう促すエラーを送出します。

### オプション依存関係のインストール

MCP サーバーには `mcp[cli]` パッケージが必要です。通常のエミュレータインストールを依存関係なしに保つため、オプションの extra として宣言されています。

```bash
pip install -e ".[mcp]"
```

これをインストールしないと、`tools/mcp_server.py` は起動時に `ModuleNotFoundError: No module named 'mcp'` で失敗します。Claude Code にサーバーを登録する前に、上記の extra をインストールしてください。

---

## Claude Code からエミュレータを使う

### 1. MCP サーバーを登録する（プロジェクトごとに1回）

```bash
claude mcp add --transport stdio --scope project msx-emulator \
    -- python tools/mcp_server.py
```

これにより `.mcp.json` にエントリが書き込まれます。Claude Code はそれを使用するセッションの開始時に `tools/mcp_server.py` を自動的に起動します——手動で常駐させておく必要はありません。以下で確認できます。

```bash
claude mcp list
```

デフォルト以外のソケットを指定する場合は、代わりに `env` ブロックを追加します。

```bash
claude mcp add --transport stdio --scope project msx-emulator \
    --env MSX_RPC_SOCKET=/tmp/alt.sock \
    -- python tools/mcp_server.py
```

### 2. `--rpc` 付きでエミュレータを起動する

```bash
python . path/to/game.rom --machine cbios_msx2_jp --rpc
```

これを別のターミナルで動かし続けてください（あるいは Claude Code の Bash ツールでバックグラウンド起動しても構いません）——MCP ツールはエミュレータプロセスがソケットをリッスンしている間だけ機能します。

### 3. Claude Code セッションから操作する

MCP サーバーを登録済みであれば、Claude に `msx-emulator` のツールを直接使うよう依頼できます。例えば：

- 「エミュレータを一時停止して Z80 レジスタをダンプし、現在の PC から逆アセンブルして」
- 「0xC080 にブレークポイントを設定して continue し、何が起きたか教えて」
- 「3倍スケールでスクリーンショットを撮って、画面に何が表示されているか見せて」
- 「SPACE キーを押して、1秒後の画面を確認して」

これらは内部的には上記の[MCP サーバー](#mcp-サーバー)節に挙げたツール呼び出しの1つ以上になり、それぞれが1対1でソケット RPC メソッドに対応します。典型的なデバッグの流れは次のようになります。

1. `emulator_pause` → PC と理由を確認する。
2. `memory_disassemble` / `cpu_get_registers` → 状態を確認する。
3. `debug_set_breakpoint` + `cpu_continue_until_pause` → 注目したい箇所まで実行する。
4. `screen_capture` → 描画内容を視覚的に確認する。
5. `cpu_continue` → 通常の実行を再開する。

---

## セキュリティに関する注意

- ソケットはローカルユーザー専用（Unix ドメインソケットであり、ネットワークには公開されません）で、それ自体に認証機構はありません。アクセス制御はソケットファイルのファイルシステム権限に依存します。
- `memory.write` と `cpu.step`（状態を変更し、タイミングの正確さが要る操作）は、事前にエミュレータが一時停止していることを要求します。
- 同時に接続できるクライアントは1つだけです——実際にどういうことか詳しくは後述の「既知の制限」を参照してください。
- RPC は完全にオプトイン（`--rpc`）です。通常の実行にはソケットもバックグラウンドスレッドもなく、追加の攻撃対象領域はありません。

---

## 既知の制限

- **アクティブなクライアントは1つのみ。** accept ループはシングルスレッドのため一度に処理されるクライアントは1つだけです。同じソケットに対して2つの MCP セッション（または MCP セッションと `tools/rpc_client.py`）を同時に動かすと、後から接続した方は最初の接続が切断されるまでブロックされます。
- **エミュレータの自動起動はしません。** MCP サーバーはエミュレータを代わりに起動しません——先に `--rpc` 付きで別途起動しておく必要があり、そうでなければ最初のツール呼び出しが明確な「接続できない」エラーで失敗します。
- **`cpu.continue_sync` のタイムアウトは呼び出し単位です。** `timeout_ms` が経過する前にエミュレータが一時停止しなければ、無期限にブロックする代わりに `{ paused: false, reason: "timeout" }` を返します。エミュレータ自体は実行を続けます。
