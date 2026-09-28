# クイックスタート

py-msx-emulator の必要要件・インストール・BIOS のセットアップについてまとめたページです。対応ハードウェアの詳細や使い方などは [README_ja.md](README_ja.md) を参照してください。

[English version is here](QUICK_START.md)

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

## BIOS のセットアップ

このエミュレータには BIOS ROM が同梱されていません。ユーザー自身で用意する必要があります。

**C-BIOS** は無償のオープンソース MSX BIOS 代替品であり、推奨される選択肢です。

1. [https://cbios.sourceforge.net/](https://cbios.sourceforge.net/) から最新リリースをダウンロードします。
2. アーカイブを展開し、必要なファイルをこのリポジトリの `roms/cbios/` ディレクトリにコピーします。

MSX1（`cbios_msx1_jp`、MSX1 カートリッジ検出時のデフォルト）の場合：

- `cbios_main_msx1_jp.rom`
- `cbios_logo_msx1.rom`

MSX2（`cbios_msx2_jp`、カートリッジなし・MSX2 カートリッジ時のデフォルト）の場合：

- `cbios_main_msx2_jp.rom`
- `cbios_logo_msx2.rom`
- `cbios_sub.rom`

マシン ID ごとに必要なファイル名は `config/machines/` 以下の対応する YAML に記載されています。

> **法的注記:** BIOS ファイルは著作権で保護されています。該当する MSX 実機を所持し、その実機から自分でダンプしたファイルを必ず使用してください。C-BIOS が無償で合法的に利用できる推奨の代替品です。`roms/` ディレクトリは `.gitignore` によってバージョン管理から除外されています。

---

## 次のステップ

`python .` を実行してください。ウィンドウが開き、C-BIOS の `No cartridge
found` メッセージが表示されれば起動できています（C-BIOS には BASIC インタプ
リタが含まれていないため、単体では BASIC の起動画面にはなりません）。実際に
ソフトウェアが動くかどうかは、合法的に入手した ROM/ディスクイメージや
homebrew ROM で確認してください。そこから先は
[README_ja.md の「使い方」](README_ja.md#使い方) を参照して、カートリッジや
フロッピーディスクなど残りのオプションを試してください。
