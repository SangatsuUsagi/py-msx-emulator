# Tools

エミュレータと組み合わせて使うコマンドラインツール群です。ディスクイメージの管理、テープ用 WAV の変換、CPU 命令テストの実行、コア全体のゴールデンテストに使うツールと、エージェント向けの RPC/MCP 層があります。どのツールもエミュレータのプロセス内では動かず、それぞれ `python tools/<script>.py` として個別に実行します。

| スクリプト / セクション                                          | 用途                                                                                        |
| ---------------------------------------------------------------- | ------------------------------------------------------------------------------------------- |
| [`dskftp.py`](#dskftppy)                                         | `.dsk` イメージ内のファイルを編集する、ftp 風の対話シェル                                   |
| [`dskblank.py`](#dskblankpy)                                     | 未フォーマットの空の `.dsk` イメージを作成                                                  |
| [`dskdd.py`](#dskddpy)                                           | `.dsk` イメージと物理 USB フロッピードライブの間で生セクタをコピー（Linux 専用）            |
| [`bas2wav.py`](#bas2wavpy)                                       | ASCII 形式の BASIC プログラムを、実機の MSX が `LOAD"CAS:"` で読み込めるテープ用 WAV に変換 |
| [`wav2cas.py`](#wav2caspy)                                       | MSX のテープの WAV を、エミュレータの `--tape` で使う `.cas` ファイルに変換                 |
| [`run_z80_exercisers.py`](#run_z80_exerciserspy)                 | Z80 の命令テスト（zexdoc、zexall、z80test）を CPU コア単体で実行                            |
| [`run_singlestep_tests.py`](#run_singlestep_testspy)             | SingleStepTests/z80 の 1 命令ごとのテストを CPU コア単体で実行                              |
| [`golden_state.py`](#golden_statepy)                             | コア全体を決まった条件で動かし（ゴールデンケース）、結果のハッシュを記録または照合          |
| [リモート制御（Socket RPC & MCP）](#リモート制御socket-rpc--mcp) | `rpc_client.py` と `mcp_server.py` が使う、ソケット RPC と MCP の制御層の概要               |
| [`rpc_client.py`](#rpc_clientpy)                                 | エミュレータのソケット RPC サーバに接続する、手動テスト用の薄い CLI クライアント            |
| [`mcp_server.py`](#mcp_serverpy)                                 | ソケット RPC API をラップする MCP サーバ。Claude Code から起動中のエミュレータを操作できる  |

どのスクリプトも素の Python 3.10+ で動き、プロジェクト本体の依存（`requirements.txt` / `requirements-dev.txt`）以外は必要ありません。例外は `mcp_server.py` で、オプションの `mcp` 依存も必要です（[`mcp_server.py`](#mcp_serverpy) を参照）。実行にはプロジェクトの venv を使います（例：`venvs/py-msx-emulator/bin/python tools/dskftp.py`）。以下の例で `python` と書いたところは、venv のインタプリタに読み替えてください。`dskdd.py` は標準ライブラリだけを使うので、例ではシステムの `python3` を `sudo` で実行しています。

---

## `dskftp.py`

MSX-DOS（FAT12）の `.dsk` ディスクイメージを扱う、ftp 風の対話シェルです。エミュレータを起動せずに、ディレクトリを見たり、ファイルをアップロード、ダウンロード、削除したりできます。標準的な MSX のフロッピー形式（1DD 360KB/320KB、2DD 720KB/640KB）に対応します。MSX BASIC の `CALL FORMAT` やこのツールの `format` コマンドでフォーマットしたイメージも扱えます。サブディレクトリ（MSX-DOS2）も使えます。MSX-DOS1 のイメージは、サブディレクトリのないイメージとして扱います。

シェルでの変更は、すべてその場でイメージファイルに書き込まれます。別の保存操作はありません。転送は常にバイナリで、CR/LF の変換はしません。

```bash
python tools/dskftp.py game.dsk        # 起動時に接続
python tools/dskftp.py                 # 後から `open` で接続
```

シェル内のコマンドを次の表に示します（全コマンドと使い方は `help` で表示できます）。

| コマンド                                  | 動作                                                                  |
| ----------------------------------------- | --------------------------------------------------------------------- |
| `open <image.dsk>`                        | ディスクイメージに接続                                                |
| `format <image.dsk> [720\|640\|360\|320]` | 空のイメージを作成（容量は KB、デフォルトは 720）して接続             |
| `dir` / `ls [path]`                       | リモートディレクトリを一覧表示（path の末尾はワイルドカードにできる） |
| `cd <dir>`                                | リモートディレクトリを変更                                            |
| `lcd [dir]`                               | ローカルディレクトリを変更（引数なしなら表示）                        |
| `pwd` / `lpwd`                            | リモート / ローカルの作業ディレクトリを表示                           |
| `get <remote> [local]`                    | ファイルをダウンロード                                                |
| `put <local> [remote]`（別名 `send`）     | ファイルをアップロード                                                |
| `mget <pattern>` / `mput <pattern>`       | glob パターンに一致する複数のファイルをダウンロード / アップロード    |
| `delete <file>`                           | リモートのファイルを削除                                              |
| `mkdir <dir>` / `rmdir <dir>`             | リモートディレクトリを作成 / 削除                                     |
| `quit` / `bye`（または Ctrl-D）           | シェルを終了                                                          |

`format` が作るのはデータディスクです。標準的な MSX-DOS のブートセクタローダー（`CALL FORMAT` も書き込む "Boot error / Press any key for retry" のスタブ）は入りますが、`MSXDOS.SYS` は入りません。このディスクから DOS は起動できません。ふつうの FAT12 ボリュームとして読み書きするだけです。

## `dskblank.py`

`--fdd1` / `--fdd2` で使う空の `*.dsk` イメージを作成します。スクリプトはファイルを `0xE5`（フロッピーの標準のフィルバイト）で埋めます。そのため、トラックはフォーマット済みでも、ファイルシステムのないディスクのように見えます。ブートセクタ、BPB、FAT は書き込みません。作成後に、エミュレータ内の MSX BASIC の `CALL FORMAT` か、`dskftp.py format` でフォーマットしてください。

```bash
python tools/dskblank.py blank.dsk               # 720 KB（2DD、デフォルト）
python tools/dskblank.py blank.dsk --size 360    # 360 KB（1DD）
python tools/dskblank.py blank.dsk --force       # 既存のファイルを上書き
```

## `dskdd.py`

`.dsk` イメージと、USB 接続の 3 モードフロッピードライブの間で生セクタをコピーします。**Linux 専用です。** `dd` と同じく、ふつうのブロックデバイスの入出力でディスクを読み書きします。

Linux では、USB FDD は `/dev/fd0` ではなく、ブロックデバイスの `/dev/sdX` として現れます。このスクリプトを実行する前に、次の手順を行ってください。

1. `sudo ufiformat -i` を実行して、デバイスノードを調べます。
2. `sudo ufiformat -i /dev/sdX` を実行して、挿入中のメディアを確認します。

`read` も `write` も、次の条件をすべて満たすときだけ実行します。

- デバイスがブロックデバイスである。
- デバイスがマウントされていない。
- デバイスにメディアが入っている。
- デバイスがフロッピーとして妥当な大きさである（4 MB 以下。`--force` でこの確認を省ける）。

`write` は、さらにデバイスのパスを操作者にもう一度入力させます。誤ったデバイスに書き込むと、そのデバイスのデータがすべて失われるからです。`read` が既存のイメージを上書きするのは、`--overwrite` を指定したときだけです。

```bash
sudo python3 tools/dskdd.py read /dev/sdb msxdisk.dsk            # ディスク -> イメージ
sudo python3 tools/dskdd.py read /dev/sdb msxdisk.dsk --retries 5
sudo python3 tools/dskdd.py write msxdisk.dsk /dev/sdb --verify  # イメージ -> ディスク
```

| サブコマンド             | オプション                                                                                          |
| ------------------------ | --------------------------------------------------------------------------------------------------- |
| `read <device> <image>`  | `--sectors N`（デフォルト：デバイス全体）、`--retries N`（デフォルト：2）、`--overwrite`、`--force` |
| `write <image> <device>` | `--yes`（確認を省く）、`--verify`（書き込み後に読み戻して比較）、`--force`                          |

終了コードは、成功なら `0`、エラーなら `1` です。イメージは作れたものの読み取れないセクタが残ったときは `2` です。そのセクタはイメージ内で 0 で埋め、標準出力に一覧を表示します。

## `bas2wav.py`

ASCII 形式の BASIC プログラム（`.BAS` のテキストファイル）を、MSX のテープの WAV ファイルに変換します。この WAV を再生して実機の MSX に入力すると、プログラムを手で打ち込まずに読み込めます。WAV の中身は MSX の ASCII 形式のテープファイル（1200 ボー）で、`SAVE"CAS:NAME"` が書くものと同じ形式です。

```bash
python tools/bas2wav.py PROGRAM.BAS program.wav
python tools/bas2wav.py PROGRAM.BAS program.wav --invert   # 信号を反転
```

読み込みの手順は次のとおりです。

1. コンピュータのヘッドホン出力を、MSX のカセットケーブルの **EAR** プラグにつなぎます。MIC と REMOTE のプラグはつなぎません。
2. MSX で `LOAD"CAS:"` を入力します（読み込み後にそのまま実行するなら `RUN"CAS:"`）。
3. WAV を再生します。ファイルが見つかると、MSX に `Found:` とファイル名が表示されます。

ファイルが見つからないときやエラーになるときは、まず音量を上げ、次に `--invert` を試してください。

| オプション    | 意味                                                                                                  |
| ------------- | ----------------------------------------------------------------------------------------------------- |
| `--name NAME` | テープ上のファイル名、6 文字まで（デフォルト：元のファイル名を大文字にしたもの）                      |
| `--invert`    | 信号を反転する                                                                                        |
| `--volume V`  | ピークの大きさ、0 より大きく 1 以下（デフォルト：0.8）                                                |
| `--gap S`     | 256 バイトの各ブロックの前に入れる無音の秒数。BASIC がブロックを処理する時間になる（デフォルト：1.0） |

入力の改行は CRLF にそろえます。出力は 48 kHz、16 ビット、モノラルです。このスクリプトで作った WAV は、[`wav2cas.py`](#wav2caspy) で変換するとエミュレータで読み込めます。実機での読み込みはまだ試していません。

## `wav2cas.py`

MSX のテープを録音した WAV ファイルを、エミュレータの `--tape` オプションで使う `.cas` ファイルに変換します。読めるのは MSX の BIOS が使う 1200 ボーの標準形式です。`CSAVE`、`SAVE"CAS:"`、`BSAVE"CAS:"` で保存したテープを録音した WAV と、`bas2wav.py` が作る WAV がこの形式です。

`hb_10` のように MSX BASIC のある機種を使ってください。デフォルトの C-BIOS の機種には BASIC がないので、下の例の `CLOAD` などのコマンドを入力できません。

```bash
python tools/wav2cas.py tape.wav tape.cas
python . --machine hb_10 --tape tape.cas    # その後、MSX で CLOAD、RUN"CAS:"、BLOAD"CAS:",R のどれかを入力
```

変換中は、見つけた各ブロックの長さと先頭の数バイトを表示します。入力に使えるのは 8 ビットと 16 ビットの WAV です。ステレオは平均してモノラルにします。反転した信号も、オプションなしでそのまま読めます。トーンごとの鳴り始めの乱れや、雑音で 1 周期が 2 つに分かれた箇所があっても問題ありません。ただし、音が割れない（クリップしない）音量で録音したほうが確実です。ブロックが 1 つも見つからないときは、ファイルを書かずに終了コード `1` で終わります。

既知の制限：

- 2400 ボーのテープ（`CSAVE"NAME",2`）は試していません。
- 独自のローダーや高速な独自形式を使うテープは読めません。
- テープの形式にはチェックサムがないので、読み誤ったビットは検出できません。

## `run_z80_exercisers.py`

MSX のマシンを組まずに、`Z80` コア単体で Z80 の命令テストを動かします。CP/M の `.com` プログラム（zexdoc、zexall）と、[z80test](https://github.com/raxoft/z80test) の `.tap` イメージに対応します。テストプログラムはこのリポジトリに含めていないので、パスを渡してください。どれかのプログラムが失敗を報告すると終了コードは 1、それ以外は 0 です。

```bash
venvs/pypy-msx-emulator/bin/python tools/run_z80_exercisers.py \
    path/to/zexdoc.com path/to/zexall.com path/to/z80full.tap
```

PyPy で実行してください。zexdoc と zexall はそれぞれ PyPy で約 1.5 分、CPython でははるかに長くかかります。形式ごとの動かし方と期待される結果は、[命令テスト](../docs/technical-implementation_ja.md#命令テスト)を参照してください。

## `run_singlestep_tests.py`

[SingleStepTests/z80](https://github.com/SingleStepTests/z80) のテストを、`Z80` コア単体で動かします。1 ファイルには 1 オペコード分のテストが 1000 件あり、各件には 1 命令を実行する前と後の CPU の状態が入っています。このツールは 1 件ごとに 1 命令を実行し、結果を比べます。比べるのは、全レジスタ、MEMPTR（WZ）、Q、IFF1/IFF2、IM、RAM、ポートへの読み書き、T ステート数です。サイクルごとのバスの状態は比べません。テストファイルはこのリポジトリに含めていないので、パスを渡してください。

```bash
venvs/pypy-msx-emulator/bin/python tools/run_singlestep_tests.py \
    path/to/SingleStepTests/z80/v1/*.json
```

全件は PyPy で実行してください（1604 ファイルで約 3 分）。CPython では、`--limit N` で各ファイルの先頭 N 件だけを実行できます。コアが意図して別の結果を出すテストは、根拠とともに `tools/singlestep_known_divergences.json` に載せています（`--divergences <ファイル>` で別の一覧を使えます）。一覧に載せたテストは別に数え、失敗にはしません。どのテストにも当てはまらなくなった項目は、古くなった項目（stale）として報告します。一覧にない失敗か、古くなった項目が 1 つでもあると終了コードは 1、どちらもなければ 0 です。

テストは実機ではなく、あるエミュレータのコアから作られています。コアを他の言語に移植したときも、同じファイルと同じ既知の食い違いの一覧で確かめられます。

---

## `golden_state.py`

コア全体を決まった条件で動かし（ゴールデンケース）、結果のハッシュを `tests/vectors/golden_state.json` と比べます。

各ケースでは、機種、入力の手順、確認点のフレームを指定します。スロット 1 のカートリッジ、ドライブ A のディスクイメージ、テープイメージも指定できます（どれも省略可）。確認点ごとに、次の 3 種類の SHA-256 を記録できます。

- セーブステートの各セクション（`cpu`、`memory`、`vdp`、`psg` など）
- そのフレームまでの音声
- そのフレームの映像

リポジトリの `golden_state.json` には、すべての確認点の音声と映像のハッシュを記録しています。セーブステートのレイアウトは、カセットやディスクなどの機器に対応するたびに変わります。そのたびにステートのセクションのハッシュも変わり、動作が同じでも一致しなくなります。そのため、ステートのセクションのハッシュを記録するのは、コアの移植を始める前だけです。記録の前に、コアとケースを見直します。

照合では、確認点に記録してあるハッシュだけを比べます。一致しないときは、ケース、フレーム、どのハッシュが違うか（ステートのセクション、`audio`、`video` のどれか）を示します。ハッシュのないケースはその旨を表示するだけで、失敗にはしません。一致しないケースがあると終了コードは 1、それ以外は 0 です。

ROM とメディアはこのリポジトリに含めていません。メディアは `tests/media/` に置きます。このディレクトリは `.gitignore` で除外しています。ROM やメディアがないケースや、メディアの SHA-256 が違うケースは、パスを示してスキップします。テープとディスクのケースには、`hb_10` と `hb_f1xd` の ROM も必要です。このリポジトリでは入手元を示せないので、自分の実機から吸い出してください。ROM がなければ、この 2 ケースはスキップします。各ファイルの入手元は次の表のとおりです。

| パス                           | 入手元                                                                                                                                                   | SHA-256                                                            |
| ------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------ |
| `roms/cbios/`                  | [C-BIOS](https://cbios.sourceforge.net/)。C-BIOS の機種を使うときと同じ                                                                                  | （照合しない）                                                     |
| `roms/hb_10/`、`roms/hb_f1xd/` | 自分の Sony HB-10 と HB-F1XD（ファイル名は `config/machines/hb_10.yaml` と `hb_f1xd.yaml`）                                                              | （照合しない）                                                     |
| `tests/media/noborunoca.rom`   | [NOBORUNOCA v1.0.2](https://github.com/h1romas4/noborunoca/releases/tag/v1.0.2) の `noborunoca.rom`（MIT）                                               | `57a3be2fe5c3190daa3e69cf23cd262cdc8af98c60993ea941d43c815cbb25f6` |
| `tests/media/uno-msx2.rom`     | [commodore-uno v1.0.5](https://github.com/jhonnaker1/commodore-uno/releases/tag/v1.0.5) の `uno-msx2.rom`（MIT）、または v1.0.5 をビルドしたもの（下記） | `74d74d8720e623a7b8aadce8e0b0c9094f8a2162339a8e7c677c7732f673ddb8` |
| `tests/media/FREEBALL.DSK`     | [MSX-10Liners](https://github.com/martin-rizzo/MSX-10Liners) のコミット `0d7d72c` の `05_FREEBALL/FREEBALL.DSK`（CC0）                                   | `359115f0bf475a172d7815d53a23ee447bd2f4c38e0ebc0b93116566642bdbcf` |
| `tests/media/FREEBALL.cas`     | 同じコミットの `05_FREEBALL/FREEBALL.BAS` を変換したもの（下記）                                                                                         | `6fb1112f51e6013538c9686fda7811ab11563c43f76d4084dbfd591a20a6d20d` |

v1.0.5 と v1.0.10 のリリースの `uno-msx2.rom` は表の SHA-256 と一致するので、ダウンロードしたものをそのまま使えます。表の値は、commodore-uno v1.0.5 をビルドした ROM から求めたものです。ほかのバージョンでは ROM が変わることがあります。自分でビルドするときは、v1.0.5 を [SDCC](https://sdcc.sourceforge.net/) と GNU make でビルドします。

```bash
# 1. ソースを取得する
git clone --branch v1.0.5 https://github.com/jhonnaker1/commodore-uno.git
cd commodore-uno/msx2

# 2. ビルドする。ROM は build/uno.rom（16 KB）
make
```

できた `build/uno.rom` を `tests/media/uno-msx2.rom` にコピーし、SHA-256 が表の値と一致することを確かめてください。

ディスクとテープのケースは、同じゲーム（MSX-10Liners の FREEBALL）を使います。ファイルは次の手順でそろえます。

1. ディスクイメージと BASIC のプログラムを `tests/media/` にダウンロードします。
2. プログラムを [`bas2wav.py`](#bas2wavpy) で WAV ファイルに変換します。
3. WAV ファイルを [`wav2cas.py`](#wav2caspy) で `tests/media/FREEBALL.cas` に変換します。

`FREEBALL.BAS` は `tests/media/` に残してください。テストがこのファイルを変換し直して、結果の SHA-256 を確かめるためです。3 つの手順は、次のコマンドで実行できます。

```bash
BASE=https://raw.githubusercontent.com/martin-rizzo/MSX-10Liners/0d7d72ca0fc2184b612e068e93837b27556f4ed7/05_FREEBALL
mkdir -p tests/media
curl -fLo tests/media/FREEBALL.DSK $BASE/FREEBALL.DSK
curl -fLo tests/media/FREEBALL.BAS $BASE/FREEBALL.BAS
venvs/py-msx-emulator/bin/python tools/bas2wav.py tests/media/FREEBALL.BAS /tmp/FREEBALL.wav
venvs/py-msx-emulator/bin/python tools/wav2cas.py /tmp/FREEBALL.wav tests/media/FREEBALL.cas
```

ファイルをそろえたら、ハッシュを照合または記録します。

```bash
venvs/py-msx-emulator/bin/python tools/golden_state.py           # 照合
venvs/py-msx-emulator/bin/python tools/golden_state.py --write   # 音声と映像を記録
venvs/py-msx-emulator/bin/python tools/golden_state.py --write --state   # ステートのセクションも記録
```

音声や映像のハッシュが変わっても、コアのどの部分が変わったかはわかりません。調べるには、変更の前に `golden_state.json` のコピーへすべてのハッシュを記録し、変更の後にそのコピーと照合します。こうすると、一致しないときは、どのステートのセクションが違うかもわかります。

```bash
cp tests/vectors/golden_state.json /tmp/golden_state.json
venvs/py-msx-emulator/bin/python tools/golden_state.py --write --state --vectors /tmp/golden_state.json  # 変更の前
venvs/py-msx-emulator/bin/python tools/golden_state.py --vectors /tmp/golden_state.json          # 変更の後
```

意図して動作を変えたときは、`--write` で記録し直し、新しいファイルをコミットします。

テストスイートでは `tests/test_golden_state.py` が同じ照合をします。`hb_10` と `hb_f1xd` の ROM があると約 25 秒かかります。テープのケースは通常の速度で読み込むので、このケースだけで約 12 秒かかります。

ハッシュはこのコアの動作を記録したもので、実機の動作ではありません。動作が変われば、ハッシュでわかります。コアを移植したときも、同じケースを動かせば、移植先を Python のコアと比べられます。

## リモート制御（Socket RPC & MCP）

エミュレータは、シェルスクリプト、テストハーネス、AI コーディングエージェントなどの外部ツール向けに、小さなローカルの制御インターフェースを公開できます。これらのツールは、実行中のエミュレータを一時停止したり、内部を調べたり、操作したりします。制御は次の 2 つの層からなります。

- **Socket RPC**：エミュレータのプロセスに組み込まれた、Unix ドメインソケットの JSON-RPC サーバ（`host/rpc_server.py`）です。**デフォルトでは無効**で、`--rpc` で有効にします。
- **MCP サーバ**：下記の [`mcp_server.py`](#mcp_serverpy) です。ソケット RPC を [Model Context Protocol](https://modelcontextprotocol.io) のツールとしてラップする、独立した stdio サーバです。そのため、Claude Code のようなクライアントは、エミュレータの機能をネイティブのツールとして呼び出し、スクリーンショットをインライン画像として受け取れます。

```
MCP クライアント ──stdio/MCP──▶ tools/mcp_server.py ──Unix ソケット──▶ エミュレータ (--rpc)
```

### セキュリティ上の注意

- ソケットは Unix ドメインソケットなので、接続できるのはローカルのプロセスだけです。ソケット自体に認証はなく、アクセスはソケットファイルのパーミッションで決まります。サーバは起動のたびにソケットを作り直すので、ファイルに `chmod` しても、次の起動で元に戻ります。共有ホストでは、`--rpc-socket` で自分だけが開けるディレクトリにソケットを置いてください。
- `memory.write` と `cpu.step` はマシンの状態を変えるので、**一時停止中にだけ**実行できます。
- RPC が有効になるのは、`--rpc` を指定したときか、`py_emulator.yaml` で `rpc.enabled` を設定したときだけです。ふつうの実行ではソケットを開きません。

### RPC サーバの有効化

```bash
# 制御ソケットを有効にして起動
python . path/to/game.rom --rpc

# 任意：ソケットのパスを指定（複数のインスタンスを動かすときなど）
python . path/to/game.rom --rpc --rpc-socket /tmp/py_msx_alt.sock
```

毎回有効にするには、`py_emulator.yaml` に設定します。

```yaml
rpc:
  enabled: true
  socket: /tmp/py_msx_emu.sock
```

`--rpc-socket` は `rpc.socket` より優先します。

RPC のメソッドで操作できる対象は次のとおりです。

- デバッガ
- ブレークポイントとウォッチポイント
- メモリ
- VRAM と VDP レジスタ
- キーボードとジョイスティックの入力
- スクリーンショット
- セーブステートの保存と読み込み
- フロッピーディスク
- テープ

ワイヤプロトコルと全メソッドの一覧は、[`../docs/socket-rpc-mcp_ja.md`](../docs/socket-rpc-mcp_ja.md) を参照してください。

MCP 層を介さずに手動で試すときは下記の [`rpc_client.py`](#rpc_clientpy)、MCP クライアントから操作するときは下記の [`mcp_server.py`](#mcp_serverpy) を使います。

## `rpc_client.py`

エミュレータの Unix ソケット JSON-RPC サーバに接続する、手動テスト用の薄い CLI クライアントです。MCP 層を介さずにメソッドを 1 回呼び出し、レスポンスを表示します。エミュレータを `--rpc` 付きで起動しておく必要があります。メソッドの全リファレンスは、[`../docs/socket-rpc-mcp_ja.md`](../docs/socket-rpc-mcp_ja.md) を参照してください。

```bash
python tools/rpc_client.py debugger.status
python tools/rpc_client.py debugger.pause
python tools/rpc_client.py memory.read address=0xC000 length=16
python tools/rpc_client.py debug.set_breakpoint '{"address": "0xC000"}'
python tools/rpc_client.py --socket /tmp/alt.sock cpu.get_registers
```

パラメータは JSON オブジェクトか `key=value` 形式で指定します。クライアントは各値を `int`、`bool`、`str` のうち当てはまる型に変換します。デフォルトのソケットのパスは `/tmp/py_msx_emu.sock` です。

## `mcp_server.py`

エミュレータのソケット RPC API を MCP のツールとしてラップし、Claude Code などの MCP クライアントから起動中のエミュレータを直接操作できるようにします。Z80 の実行制御（一時停止、ステップ実行、再開）、ブレークポイントとウォッチポイントの設定、メモリの読み書き、入力の注入、スクリーンショットの取得ができます。アーキテクチャとツールの全リストは、[`../docs/socket-rpc-mcp_ja.md`](../docs/socket-rpc-mcp_ja.md) を参照してください。

MCP サーバには、オプションの `mcp` 依存が必要です。

```bash
pip install -e '.[mcp]'      # または：pip install 'mcp[cli]>=1.0,<2.0'
```

`python` には、`mcp` パッケージを入れたインタプリタを使ってください。仮想環境に入れた場合は、下の登録コマンドの `python` の代わりに、その環境のインタプリタのフルパスを指定します。

```bash
# RPC サーバを有効にしてエミュレータを起動
python . path/to/game.rom --rpc

# この MCP サーバを Claude Code に登録（初回のみ。.mcp.json に書き込む）
claude mcp add --transport stdio --scope project msx-emulator \
    -- python tools/mcp_server.py
claude mcp list        # 登録を確認
```

登録後は、Claude Code がセッションごとに自動でこのプロセスを起動します。サーバは、ソケットのパスを環境変数 `MSX_RPC_SOCKET` から読みます（デフォルトは `/tmp/py_msx_emu.sock`）。上記のように登録した場合は、この変数を `.mcp.json` の `env` ブロックで設定できます。

サーバはエミュレータからの各レスポンスを最大 60 秒待つので、`cpu_continue_until_pause` の `timeout_seconds` は 60 未満にしてください。詳しくは、[既知の制限](../docs/socket-rpc-mcp_ja.md#既知の制限)を参照してください。
