# しおりツール – 見開き分割

[한국어](README.md) · [English](README.en.md) · [中文](README.zh-CN.md)

本を開いてスキャンし 1 枚に 2 ページ写った PDF を左右のページに分け、電子書籍リーダーで 1 ページずつめくれるようにします。画像を再圧縮しないので画質はそのまま、ファイルサイズもほとんど増えません。サーバー不要、インストール不要、元のファイルは変更しません。

![変換前後の比較](docs/before_after.png)

## ダウンロード

- **実行ファイル**：[Releases](https://github.com/microhan1/scan-pdf-split/releases) から `scan-pdf-split.exe` をダウンロードしてダブルクリック。インストールは不要です。
- **ソースから実行**：

```bash
pip install -r requirements.txt
python main.py
```

## 使い方

1. PDF ファイルまたはフォルダーをウィンドウにドロップします。
2. プレビューを見ながらオプションを調整します（読む順序 · 分割するページ · のどの自動調整 · 中央の重なり）。ページをめくりながら赤い分割線をドラッグしたり、ページごとに分割 / そのまま / 除外を選んだりできます。
3. **実行** を押すと、元のファイルの隣に `<元の名前>_split.pdf` ができます。

- 縦長の表紙は分割せずそのまま残します。
- **自動調整** は各ページの中央でのどの影や余白を探し、分割線をそこへ動かします。見つからないページは分割線の位置オプションどおりに分けます。
- 回転情報のあるページ、リンクと注釈、しおり（目次）も分割後のページに合わせて移ります。

コマンドラインでも使えます。

```bash
python main.py book.pdf
python main.py scans/ --direction rtl --overlap 1
python main.py book.pdf --no-auto --position 52
```

`python main.py --help` で OS の言語（한국어 · English · 中文 · 日本語）のオプション一覧が表示されます。

## しないこと

- OCR はしません。検索できる PDF は分割後も検索できますが、隣のページの文字も一緒にヒットすることがあります。
- 余白の切り取り、画質補正は別のツールです。このツールはページの分割だけを行います。
- 切り落とした半分は見えなくなるだけで、ファイル内には残ります。内容を隠す目的には使わないでください。
- 傾いたスキャンをまっすぐにしたり、回転情報なしで横向きに保存された画像を回したりはしません。

## シリーズ

- しおりツール：[スキャンPDF補正](https://github.com/microhan1/scan-pdf-cleanup) · [余白カット](https://github.com/microhan1/scan-pdf-crop)
- インストール不要、ブラウザーで使える Web 版：[見開き分割](https://microhan1.github.io/spread-split/)
- [しおりライブラリ（Chaekgalpi Library）](https://github.com/microhan1/chaekgalpi)

## ライセンス

MIT。[LICENSE](LICENSE) を参照してください。
