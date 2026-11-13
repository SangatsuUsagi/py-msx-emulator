# クイックスタート

py-msx-emulator の動作に必要なもの、インストールの手順、BIOS のセットアップをまとめたページです。エミュレーションの対象ハードウェアの詳細、使い方、そのほかの話題は、[README_ja.md](README_ja.md) を参照してください。

[English version is here](QUICK_START.md)

---

## 必要なもの

- **Python 3.10 以降**
- **SDL2 のネイティブライブラリ**（pysdl2 とは別にインストールします）

| パッケージ | 最低バージョン | 用途                                                       |
| ---------- | -------------- | ---------------------------------------------------------- |
| Pillow     | 12.0           | スクリーンショットとステートセーブの PNG 出力              |
| pysdl2     | 0.9.16         | 表示とオーディオのフロントエンドで使う SDL2 バインディング |
| PyYAML     | 6.0            | `py_emulator.yaml` と機種の YAML ファイルの読み込み        |

開発用の依存（pytest、ruff、mypy）は `requirements-dev.txt` にあります。`tools/mcp_server.py` を使う場合は、オプションの `mcp` extra も必要です（[tools/README_tools_ja.md](tools/README_tools_ja.md#mcp_serverpy) を参照）。このプロジェクトは PyPI では公開していません。

---

## インストール

```bash
git clone https://github.com/SangatsuUsagi/py-msx-emulator.git
cd py-msx-emulator

# SDL2 のネイティブライブラリをインストール
# macOS:
brew install sdl2
# Ubuntu / Debian:
sudo apt install libsdl2-2.0-0 python3-venv

python -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
```

対応プラットフォームは macOS と Linux（Ubuntu）です。Windows では動作を確認していません。Windows で使う場合は、システムの SDL2 ライブラリの代わりに `pysdl2-dll` をインストールしてください。

---

## BIOS のセットアップ

BIOS ROM は同梱していないので、自分で用意してください。

推奨するのは **C-BIOS** です。無償でオープンソースの、MSX BIOS 互換の ROM です。

1. [https://cbios.sourceforge.net/](https://cbios.sourceforge.net/) から最新のリリースをダウンロードします。
2. アーカイブを展開します。
3. 必要なファイルを、このリポジトリの `roms/cbios/` にコピーします。

MSX1（`cbios_msx1_jp`）：ROM データベースで MSX1 用とされているカートリッジを起動するときのデフォルトです。

- `cbios_main_msx1_jp.rom`
- `cbios_logo_msx1.rom`

MSX2（`cbios_msx2_jp`）：それ以外のとき（カートリッジなしで起動するときを含む）のデフォルトです。

- `cbios_main_msx2_jp.rom`
- `cbios_logo_msx2.rom`
- `cbios_sub.rom`

`--machine` オプションも `py_emulator.yaml` の `machine:` も指定していないときだけ、これらのデフォルトが使われます。機種 ID ごとに必要なファイル名は、`config/machines/` にあるその機種の YAML ファイルに書いてあります。

> **法的な注意：** MSX の BIOS ファイルは著作権で保護されています。使ってよいのは、自分が持っている MSX 実機から自分でダンプした、その機種用のファイルだけです。代わりに、無償で合法的に使える C-BIOS を推奨します。`roms/` ディレクトリは `.gitignore` でバージョン管理から除外しています。

---

## 次のステップ

1. `python .` を実行します。
2. ウィンドウが開き、C-BIOS のメッセージ `No cartridge found` が表示されることを確かめます。C-BIOS の機種には BASIC がないので、BASIC のプロンプトは表示されません。
3. エミュレータでソフトウェアが動くことを確かめるには、合法的に入手した ROM やディスクイメージ、または自作ソフト（ホームブリュー）の ROM で実行します。

カートリッジ、フロッピーディスク、その他のコマンドラインオプションは、README の[使い方](README_ja.md#使い方)を参照してください。
